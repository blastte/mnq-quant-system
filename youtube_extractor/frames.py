"""Download a YouTube video with yt-dlp and extract frames with OpenCV.

Two strategies, combined:
  - Fixed interval (every N seconds)
  - Scene-change detection (HSV histogram delta above a threshold)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2  # type: ignore
import numpy as np

from .utils import fmt_timestamp


@dataclass
class Frame:
    timestamp: float        # seconds
    path: Path
    reason: str             # "interval" | "scene"


# ── Download ─────────────────────────────────────────────────────────────────

def _ffmpeg_path() -> str | None:
    """Resolve a usable ffmpeg binary. Prefers system ffmpeg; falls back to
    the imageio-ffmpeg-bundled binary."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _video_id_from_url(url: str) -> str:
    """Extract the 11-char YouTube video ID from a URL/raw-id string."""
    import re
    m = re.search(r"(?:v=|/)([a-zA-Z0-9_-]{11})", url)
    return m.group(1) if m else url[:11]


def _is_video_file(p: Path) -> bool:
    """Heuristic: format-suffixed files like '<id>.f134.mp4' are video-only
    streams from yt-dlp's split download. Files without a format suffix are
    typically the merged/usable file."""
    # Any file with a single dot (no .fNNN. suffix) is a candidate
    parts = p.stem.split(".")
    return len(parts) == 1


def download_video(url: str, out_dir: Path, max_height: int = 720) -> tuple[Path, Path | None]:
    """Download video + audio with yt-dlp. Returns (video_path, audio_path or None).

    yt-dlp downloads split video+audio streams (`<id>.f134.mp4`, `<id>.f251.webm`)
    and merges them into `<id>.mp4` if ffmpeg is available. We pick the un-suffixed
    output as the canonical video, and fall back to the largest format-suffixed
    file if the merge didn't happen. Audio comes from the bestaudio download.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    video_id = _video_id_from_url(url)
    video_tpl = str(out_dir / "%(id)s.%(ext)s")

    ff = _ffmpeg_path()
    ff_args = ["--ffmpeg-location", str(Path(ff).parent)] if ff else []

    fmt = f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]"
    subprocess.run([
        sys.executable, "-m", "yt_dlp", "-f", fmt, "--merge-output-format", "mp4",
        "-o", video_tpl, *ff_args, url,
    ], check=True, stdout=subprocess.PIPE, stderr=None)

    # Prefer the un-suffixed output (the merged file or single-format fallback).
    # If that doesn't exist, take the largest format-suffixed file we got.
    candidates = sorted(
        [p for p in out_dir.iterdir()
         if p.is_file() and p.suffix in {".mp4", ".webm", ".mkv"}],
        key=lambda p: (not _is_video_file(p), -p.stat().st_size),
    )
    if not candidates:
        raise FileNotFoundError("yt-dlp did not produce a video file")
    video = candidates[0]

    # Extract bestaudio as a separate m4a so Whisper has a clean audio file.
    # If ffmpeg is missing, the conversion is skipped — we still have the
    # raw downloaded audio (.webm / .m4a) which Whisper can read via its
    # bundled ffmpeg.
    subprocess.run([
        sys.executable, "-m", "yt_dlp", "-f", "bestaudio", "-x", "--audio-format", "m4a",
        "-o", str(out_dir / f"{video_id}_audio.%(ext)s"),
        *ff_args, url,
    ], check=False, stdout=subprocess.PIPE, stderr=None)

    # Find any audio file — preferring the freshly-extracted m4a, then any
    # audio-only webm/m4a in the directory.
    audio_candidates = (
        list(out_dir.glob(f"{video_id}_audio.*")) +
        [p for p in out_dir.iterdir()
         if p.is_file() and p.suffix in {".m4a", ".webm"} and p != video]
    )
    audio = audio_candidates[0] if audio_candidates else None
    return video, audio


# ── Frame extraction ─────────────────────────────────────────────────────────

def _hist(frame_bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
    return cv2.normalize(h, h).flatten()


def extract_frames(
    video_path: Path,
    out_dir: Path,
    interval_sec: float = 30.0,
    scene_threshold: float = 0.55,
    min_gap_sec: float = 5.0,
) -> list[Frame]:
    """Extract frames at fixed interval + on scene changes.

    scene_threshold: HSV-histogram correlation distance (0=identical, 1=very different).
    min_gap_sec: don't save another scene-change frame within this window.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps

    sample_every = max(1, int(round(fps)))   # one read per second of video
    saved: list[Frame] = []
    last_hist: np.ndarray | None = None
    last_save_t = -1e9
    next_interval_t = 0.0
    idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % sample_every != 0:
            idx += 1
            continue

        t = idx / fps
        reason: str | None = None

        cur_hist = _hist(frame)
        if t >= next_interval_t:
            reason = "interval"
            next_interval_t = t + interval_sec
        else:
            if last_hist is not None:
                # 1 - correlation gives a distance in [0, 2]
                dist = 1.0 - cv2.compareHist(last_hist, cur_hist, cv2.HISTCMP_CORREL)
                if dist > scene_threshold and (t - last_save_t) >= min_gap_sec:
                    reason = "scene"

        if reason is not None:
            fname = f"frame_{int(round(t)):06d}_{reason}.jpg"
            fpath = out_dir / fname
            cv2.imwrite(str(fpath), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            saved.append(Frame(timestamp=t, path=fpath, reason=reason))
            last_save_t = t
            last_hist = cur_hist

        idx += 1

    cap.release()
    print(f"[frames] {len(saved)} frames saved (duration {fmt_timestamp(duration)})")
    return saved


def cleanup(workdir: Path) -> None:
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
