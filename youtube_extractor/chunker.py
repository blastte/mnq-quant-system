"""Split a timeline into LLM-sized chunks while keeping segment boundaries intact."""

from __future__ import annotations

from dataclasses import dataclass

from .structurize import TimelineEntry


# Cheap heuristic: 1 token ≈ 4 chars of English. Good enough for budgeting;
# the model will not crash if we're slightly off.
def _approx_tokens(s: str) -> int:
    return max(1, len(s) // 4)


@dataclass
class Chunk:
    index: int
    entries: list[TimelineEntry]
    approx_tokens: int

    def as_text(self) -> str:
        lines = []
        for e in self.entries:
            img = f"  [image: {e.image}]" if e.image else ""
            lines.append(f"[{e.timestamp}] {e.text}{img}")
        return "\n".join(lines)

    def image_paths(self) -> list[str]:
        return [e.image for e in self.entries if e.image]


def chunk_timeline(
    entries: list[TimelineEntry],
    max_tokens_per_chunk: int = 6000,
    overlap_entries: int = 1,
) -> list[Chunk]:
    """Greedy split. Carry `overlap_entries` from previous chunk for continuity."""
    chunks: list[Chunk] = []
    cur: list[TimelineEntry] = []
    cur_tokens = 0
    idx = 0

    for entry in entries:
        line = f"[{entry.timestamp}] {entry.text}"
        t = _approx_tokens(line)
        if cur and cur_tokens + t > max_tokens_per_chunk:
            chunks.append(Chunk(index=idx, entries=cur, approx_tokens=cur_tokens))
            idx += 1
            cur = cur[-overlap_entries:] if overlap_entries else []
            cur_tokens = sum(_approx_tokens(f"[{e.timestamp}] {e.text}") for e in cur)
        cur.append(entry)
        cur_tokens += t

    if cur:
        chunks.append(Chunk(index=idx, entries=cur, approx_tokens=cur_tokens))
    return chunks
