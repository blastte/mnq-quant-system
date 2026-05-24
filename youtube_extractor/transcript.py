"""Transcript extraction with three fallback layers.

1. youtube-transcript-api  — fast, captions only
2. yt-dlp auto-subtitles   — works when transcript-api blocks
3. local Whisper on audio  — final fallback, requires `openai-whisper`
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .utils import clean_text, extract_video_id


@dataclass
class TranscriptSegment:
    start: float        # seconds
    duration: float
    text: str

    @property
    def end(self) -> float:
        return self.start + self.duration


# ── Layer 1: youtube-transcript-api ──────────────────────────────────────────

def _fetch_via_api(video_id: str, languages: list[str]) -> list[TranscriptSegment]:
    """Compatible with both youtube-transcript-api 0.x and 1.x APIs."""
    from youtube_transcript_api import YouTubeTranscriptApi

    # 1.x: instance-based .fetch() returning FetchedTranscript with .snippets
    if hasattr(YouTubeTranscriptApi, "fetch"):
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=languages)
        snippets = getattr(fetched, "snippets", fetched)
        return [
            TranscriptSegment(
                start=float(getattr(s, "start", s.get("start", 0.0) if isinstance(s, dict) else 0.0)),
                duration=float(getattr(s, "duration", s.get("duration", 0.0) if isinstance(s, dict) else 0.0)),
                text=getattr(s, "text", s.get("text", "") if isinstance(s, dict) else ""),
            )
            for s in snippets
        ]

    # 0.x: classmethod .get_transcript() returning list of dicts
    raw = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
    return [
        TranscriptSegment(
            start=float(item["start"]),
            duration=float(item.get("duration", 0.0)),
            text=item["text"],
        )
        for item in raw
    ]


# ── Layer 2: yt-dlp auto-subs ────────────────────────────────────────────────

def _fetch_via_ytdlp(url: str, languages: list[str], workdir: Path) -> list[TranscriptSegment]:
    lang_arg = ",".join(languages)
    out_tpl = str(workdir / "%(id)s.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--skip-download",
        "--write-auto-subs",
        "--write-subs",
        "--sub-langs", lang_arg,
        "--sub-format", "json3",
        "--convert-subs", "json3",
        "-o", out_tpl,
        url,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=None)

    json3 = next(workdir.glob("*.json3"), None)
    if json3 is None:
        raise FileNotFoundError("yt-dlp produced no subtitle file")

    data = json.loads(json3.read_text(encoding="utf-8"))
    segments: list[TranscriptSegment] = []
    for ev in data.get("events", []):
        if "segs" not in ev:
            continue
        text = "".join(s.get("utf8", "") for s in ev["segs"]).strip()
        if not text:
            continue
        segments.append(TranscriptSegment(
            start=ev["tStartMs"] / 1000.0,
            duration=ev.get("dDurationMs", 0) / 1000.0,
            text=text,
        ))
    return segments


# ── Layer 3: Whisper on local audio ──────────────────────────────────────────

def _ensure_ffmpeg_on_path() -> None:
    """Whisper shells out to `ffmpeg` via subprocess. If the system ffmpeg
    isn't on PATH, fall back to the binary bundled with imageio-ffmpeg by
    prepending its directory to PATH for this process."""
    import os
    import shutil

    if shutil.which("ffmpeg"):
        return                  # already discoverable

    try:
        import imageio_ffmpeg   # type: ignore
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return

    ffmpeg_dir = str(Path(ffmpeg_exe).parent)
    # Whisper looks for the binary named "ffmpeg" not "ffmpeg-win-x86_64-...".
    # On Windows, copy/symlink the imageio binary to a sibling "ffmpeg.exe"
    # so Whisper's subprocess call works.
    target = Path(ffmpeg_dir) / "ffmpeg.exe"
    if not target.exists():
        try:
            import shutil as _sh
            _sh.copy2(ffmpeg_exe, target)
        except Exception:
            pass
    if ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")


def _fetch_via_whisper(audio_path: Path, model_name: str = "base") -> list[TranscriptSegment]:
    try:
        import whisper  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Whisper transcription requires the 'openai-whisper' package.\n"
            "Install with: pip install openai-whisper"
        ) from exc

    _ensure_ffmpeg_on_path()

    model = whisper.load_model(model_name)
    result = model.transcribe(str(audio_path), verbose=False)
    return [
        TranscriptSegment(
            start=float(seg["start"]),
            duration=float(seg["end"]) - float(seg["start"]),
            text=seg["text"],
        )
        for seg in result.get("segments", [])
    ]


# ── Public entrypoint ────────────────────────────────────────────────────────

def get_transcript(
    url: str,
    languages: list[str] | None = None,
    audio_path: Path | None = None,
    drop_fillers: bool = True,
    whisper_model: str = "base",
    force_whisper: bool = False,
) -> list[TranscriptSegment]:
    """Try caption layers, then Whisper if audio is provided.

    If `force_whisper` is True, skip Layers 1 and 2 and go straight to Whisper.
    Useful for jargon-heavy content (trading, medical, legal) where YouTube's
    auto-captions garble specialised vocabulary.
    """
    languages = languages or ["en", "en-US", "en-GB"]
    video_id = extract_video_id(url)

    if force_whisper:
        if audio_path is None or not audio_path.exists():
            raise RuntimeError(
                "force_whisper=True but no audio file is available. "
                "Run with extract_video=True so audio gets downloaded."
            )
        print(f"[transcript] FORCED Whisper transcription ({whisper_model})…")
        segs = _fetch_via_whisper(audio_path, whisper_model)
    else:
        try:
            segs = _fetch_via_api(video_id, languages)
        except Exception as e1:
            print(f"[transcript] API layer failed: {e1!s}; falling back to yt-dlp")
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    segs = _fetch_via_ytdlp(url, languages, Path(tmp))
            except Exception as e2:
                print(f"[transcript] yt-dlp layer failed: {e2!s}")
                if audio_path is None or not audio_path.exists():
                    raise RuntimeError(
                        "All caption layers failed and no local audio was provided "
                        "for Whisper fallback."
                    ) from e2
                print(f"[transcript] running Whisper ({whisper_model})…")
                segs = _fetch_via_whisper(audio_path, whisper_model)

    for s in segs:
        s.text = clean_text(s.text, drop_fillers=drop_fillers)
    return [s for s in segs if s.text]
