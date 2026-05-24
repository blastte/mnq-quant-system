"""Merge transcript segments + frames into one structured timeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

from .frames import Frame
from .transcript import TranscriptSegment
from .utils import fmt_timestamp


@dataclass
class TimelineEntry:
    timestamp: str          # HH:MM:SS at the start of this entry
    start_sec: float
    end_sec: float
    text: str
    image: str | None       # relative path to the nearest frame, if any
    image_reason: str | None


def _nearest_frame(t: float, frames: list[Frame], max_offset: float = 15.0) -> Frame | None:
    if not frames:
        return None
    best = min(frames, key=lambda f: abs(f.timestamp - t))
    return best if abs(best.timestamp - t) <= max_offset else None


def build_timeline(
    transcript: list[TranscriptSegment],
    frames: list[Frame],
    bucket_sec: float = 30.0,
    image_root: Path | None = None,
) -> list[TimelineEntry]:
    """Group transcript into fixed-size buckets, attach the nearest frame to each."""
    if not transcript:
        return []

    end = max(s.end for s in transcript)
    entries: list[TimelineEntry] = []
    t = 0.0
    while t < end:
        bucket_end = t + bucket_sec
        chunk = [s for s in transcript if s.start < bucket_end and s.end > t]
        if not chunk:
            t = bucket_end
            continue
        text = " ".join(s.text for s in chunk).strip()
        midpoint = (t + bucket_end) / 2
        frame = _nearest_frame(midpoint, frames)
        img_rel = None
        if frame and image_root is not None:
            try:
                img_rel = str(frame.path.relative_to(image_root))
            except ValueError:
                img_rel = str(frame.path)
        elif frame:
            img_rel = str(frame.path)

        entries.append(TimelineEntry(
            timestamp=fmt_timestamp(t),
            start_sec=t,
            end_sec=bucket_end,
            text=text,
            image=img_rel,
            image_reason=frame.reason if frame else None,
        ))
        t = bucket_end
    return entries


def write_timeline(entries: list[TimelineEntry], path: Path) -> None:
    path.write_text(
        json.dumps([asdict(e) for e in entries], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
