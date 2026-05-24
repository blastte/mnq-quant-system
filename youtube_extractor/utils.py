"""Shared helpers: URL parsing, timestamp formatting, filler-word cleanup."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse


def ensure_utf8_stdout() -> None:
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

_FILLERS = {
    "um", "uh", "like", "you know", "basically", "literally", "kinda",
    "sorta", "right", "ok so", "okay so",
}


def extract_video_id(url: str) -> str:
    """Return the 11-char YouTube ID from any common URL form."""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if "youtu.be" in host:
        return parsed.path.lstrip("/").split("/")[0]
    if "youtube.com" in host:
        if parsed.path == "/watch":
            v = parse_qs(parsed.query).get("v", [None])[0]
            if v:
                return v
        # /shorts/<id>, /embed/<id>, /v/<id>
        m = re.match(r"^/(?:shorts|embed|v)/([A-Za-z0-9_-]{11})", parsed.path)
        if m:
            return m.group(1)
    raise ValueError(f"Could not extract video id from: {url}")


def fmt_timestamp(seconds: float) -> str:
    """Seconds → HH:MM:SS."""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def parse_timestamp(ts: str) -> float:
    """HH:MM:SS or MM:SS → seconds."""
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"Bad timestamp: {ts}")


def clean_text(text: str, drop_fillers: bool = True) -> str:
    """Collapse whitespace, optionally drop filler words/phrases."""
    text = re.sub(r"\s+", " ", text).strip()
    if not drop_fillers:
        return text
    pattern = r"\b(?:" + "|".join(re.escape(f) for f in _FILLERS) + r")\b"
    text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" ,.")
