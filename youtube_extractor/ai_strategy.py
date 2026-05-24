"""Send chunked timeline to an LLM and extract backtestable strategy rules.

Two backends:
  - Ollama  (default; matches the existing ai_analyzer.py pattern)
  - Claude  (via the `anthropic` SDK, used when ANTHROPIC_API_KEY is set
             and `backend="claude"` is requested)

The prompt is strict: model must return ONE JSON object per chunk that conforms
to the schema below. Subjective rules are filtered out post-hoc.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from .chunker import Chunk


# ── Schema --------------------------------------------------------------------

CHUNK_SCHEMA_HINT = """
Return ONLY a JSON object with this exact shape:

{
  "rules": [
    {
      "id": "R1",
      "name": "short_name",
      "trigger": "precise condition, no fuzzy verbs",
      "side": "long" | "short" | "both",
      "entry": "exact entry condition",
      "exit": "exact exit condition or 'unspecified'",
      "stop": "stop condition or 'unspecified'",
      "timeframe": "e.g. 5m, 1h, daily, 'unspecified'",
      "confidence": 0.0_to_1.0,
      "evidence_timestamps": ["HH:MM:SS", ...]
    }
  ],
  "concepts": ["liquidity sweep", "fair value gap", ...],
  "examples": [
    {"timestamp": "HH:MM:SS", "what_happened": "..."}
  ],
  "unclear": [
    {"timestamp": "HH:MM:SS", "why": "narrator was vague about ..."}
  ]
}
""".strip()


SUBJECTIVE_VERBS = re.compile(
    r"\b(feels?|looks?|seems?|appears?|might|maybe|probably|kinda|sort of|"
    r"clean|nice|pretty|beautiful|strong-looking|good)\b",
    re.IGNORECASE,
)


@dataclass
class StrategyOutput:
    rules: list[dict[str, Any]] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    examples: list[dict[str, Any]] = field(default_factory=list)
    unclear: list[dict[str, Any]] = field(default_factory=list)

    def merge(self, other: "StrategyOutput") -> None:
        self.rules.extend(other.rules)
        self.examples.extend(other.examples)
        self.unclear.extend(other.unclear)
        for c in other.concepts:
            if c not in self.concepts:
                self.concepts.append(c)


# ── Prompt --------------------------------------------------------------------

def _build_prompt(chunk: Chunk) -> str:
    return f"""You are a quantitative strategy extractor.

You will read a transcript chunk from a trading-strategy YouTube video.
Your job is to extract ONLY backtestable rules — concrete, testable conditions
on price, indicators, time, or volume. Reject anything subjective.

REJECT (do not output) rules that depend on words like:
  feels, looks, seems, appears, clean, nice, strong-looking, beautiful.
If the narrator says something subjective, list it under "unclear" instead.

For each rule include the exact timestamps it was discussed at, so a human
can verify. If something is unspecified (e.g. no stop given), write
"unspecified" — do NOT invent values.

{CHUNK_SCHEMA_HINT}

─────────────────────────────────────────
TRANSCRIPT CHUNK {chunk.index}
─────────────────────────────────────────
{chunk.as_text()}
─────────────────────────────────────────

Output JSON only. No prose.
"""


# ── Backends ------------------------------------------------------------------

def _call_ollama(prompt: str, model: str, host: str) -> str:
    r = requests.post(
        f"{host.rstrip('/')}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
        timeout=600,
    )
    r.raise_for_status()
    return r.json().get("response", "")


def _call_claude(prompt: str, model: str, image_paths: list[str]) -> str:
    try:
        from anthropic import Anthropic  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Claude backend requires the 'anthropic' package.\n"
            "Install with: pip install anthropic>=0.40.0"
        ) from exc

    client = Anthropic()
    content: list[dict[str, Any]] = []
    for p in image_paths[:8]:                # cap to keep request size sane
        path = Path(p)
        if not path.exists():
            continue
        b64 = base64.standard_b64encode(path.read_bytes()).decode()
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        })
    content.append({"type": "text", "text": prompt})

    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )
    return "".join(block.text for block in msg.content if block.type == "text")


# ── Parsing -------------------------------------------------------------------

def _parse_json_strict(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    # Ollama may wrap in ```json fences when format=json fails.
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to find the first { ... } object.
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def _filter_subjective(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = []
    for r in rules:
        blob = " ".join(str(r.get(k, "")) for k in ("trigger", "entry", "exit", "stop"))
        if SUBJECTIVE_VERBS.search(blob):
            continue
        kept.append(r)
    return kept


# ── Public --------------------------------------------------------------------

def extract_strategy(
    chunks: list[Chunk],
    backend: str = "ollama",
    ollama_model: str = "llama3.1",
    ollama_host: str = "http://localhost:11434",
    claude_model: str = "claude-opus-4-7",
    send_images: bool = False,
) -> StrategyOutput:
    """Run extraction over each chunk and merge.

    send_images only applies to the Claude backend (vision-capable).
    """
    out = StrategyOutput()
    for chunk in chunks:
        prompt = _build_prompt(chunk)
        print(f"[strategy] chunk {chunk.index} ({chunk.approx_tokens} tok) → {backend}")
        try:
            if backend == "ollama":
                raw = _call_ollama(prompt, ollama_model, ollama_host)
            elif backend == "claude":
                images = chunk.image_paths() if send_images else []
                raw = _call_claude(prompt, claude_model, images)
            else:
                raise ValueError(f"Unknown backend: {backend}")
            data = _parse_json_strict(raw)
        except Exception as e:
            print(f"[strategy] chunk {chunk.index} failed: {e!s}", file=sys.stderr)
            out.unclear.append({
                "timestamp": chunk.entries[0].timestamp if chunk.entries else "00:00:00",
                "why": f"LLM call failed: {e!s}",
            })
            continue

        rules = _filter_subjective(data.get("rules") or [])
        out.merge(StrategyOutput(
            rules=rules,
            concepts=data.get("concepts") or [],
            examples=data.get("examples") or [],
            unclear=data.get("unclear") or [],
        ))
    return out
