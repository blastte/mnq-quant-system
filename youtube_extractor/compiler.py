"""Compile free-text strategy rules into a strict, backtestable DSL.

INPUT  → strategy.json from ai_strategy.extract_strategy()
OUTPUT → compiled.json with rules in the form:

  {
    "id": "R1",
    "side": "long" | "short" | "both",
    "timeframe": "5m",
    "conditions": [
      {"left": "<operand>", "op": "<>=", "right": "<operand or number>"},
      ...
    ],
    "stop":   {"type": "points|atr|rr",  "value": float, "mult": float?},
    "target": {"type": "points|atr|rr",  "value": float}
  }

Allowed operands: close, open, high, low, ema_fast, ema_slow, rsi, atr.
Allowed operators: >, >=, <, <=, ==.
Anything that doesn't fit the grammar lands in "unclear[]".
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests


ALLOWED_OPERANDS = {"close", "open", "high", "low", "ema_fast", "ema_slow", "rsi", "atr"}
ALLOWED_OPS = {">", ">=", "<", "<=", "=="}
ALLOWED_SIDES = {"long", "short", "both"}
ALLOWED_STOP_TYPES = {"points", "atr", "rr"}


COMPILER_PROMPT = """You convert free-text trading rules into a strict JSON DSL.

ALLOWED OPERANDS (left/right):
  close, open, high, low, ema_fast, ema_slow, rsi, atr, or a literal number

ALLOWED OPERATORS:
  >, >=, <, <=, ==

OUTPUT SCHEMA (return ONLY this JSON, no prose):
{
  "rules": [
    {
      "id": "R1",
      "side": "long" | "short" | "both",
      "timeframe": "5m" | "15m" | "1h" | "daily" | "unspecified",
      "conditions": [
        {"left": "<operand>", "op": "<op>", "right": "<operand-or-number>"}
      ],
      "stop":   {"type": "points|atr|rr", "value": <number>, "mult": <number?>},
      "target": {"type": "points|atr|rr", "value": <number>}
    }
  ],
  "rejected": [
    {"id": "R?", "reason": "why it could not be compiled"}
  ]
}

REJECT (move to "rejected[]") any rule that:
  - mentions concepts not expressible in the operand list
    (e.g. "fair value gap", "liquidity sweep", "order block", chart patterns)
  - depends on subjective words like clean/nice/looks/seems/feels
  - has no clear stop/target
  - references a timeframe other than 1m/2m/3m/5m/15m/30m/1h/4h/daily

Do NOT invent values. If the source rule says "stop: unspecified", reject it.

INPUT RULES:
{rules_json}
"""


# ── Validation ───────────────────────────────────────────────────────────────

@dataclass
class Condition:
    left: str
    op: str
    right: str | float

    def validate(self) -> str | None:
        if self.left not in ALLOWED_OPERANDS:
            return f"left operand '{self.left}' not allowed"
        if self.op not in ALLOWED_OPS:
            return f"op '{self.op}' not allowed"
        if isinstance(self.right, str) and self.right not in ALLOWED_OPERANDS:
            try:
                float(self.right)
            except (TypeError, ValueError):
                return f"right operand '{self.right}' not allowed"
        return None


@dataclass
class StopSpec:
    type: str
    value: float
    mult: float | None = None

    def validate(self) -> str | None:
        if self.type not in ALLOWED_STOP_TYPES:
            return f"stop type '{self.type}' not allowed"
        if self.value is None or self.value <= 0:
            return "stop value must be positive"
        return None


@dataclass
class CompiledRule:
    id: str
    side: str
    timeframe: str
    conditions: list[Condition]
    stop: StopSpec
    target: StopSpec
    source_rule_id: str | None = None

    def validate(self) -> str | None:
        if self.side not in ALLOWED_SIDES:
            return f"side '{self.side}' not allowed"
        if not self.conditions:
            return "no conditions"
        for c in self.conditions:
            err = c.validate()
            if err:
                return err
        for spec in (self.stop, self.target):
            err = spec.validate()
            if err:
                return err
        return None


@dataclass
class CompiledStrategy:
    rules: list[CompiledRule] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)


# ── Parsing helpers ──────────────────────────────────────────────────────────

def _parse_json_strict(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def _coerce(d: dict) -> CompiledStrategy:
    out = CompiledStrategy()
    for r in d.get("rules") or []:
        try:
            conds = [
                Condition(
                    left=str(c["left"]).lower(),
                    op=str(c["op"]),
                    right=c["right"] if isinstance(c["right"], (int, float)) else str(c["right"]).lower(),
                )
                for c in r.get("conditions", [])
            ]
            stop = StopSpec(**{k: r["stop"].get(k) for k in ("type", "value", "mult")})
            tgt = StopSpec(**{k: r["target"].get(k) for k in ("type", "value", "mult")})
            rule = CompiledRule(
                id=str(r.get("id") or f"R{len(out.rules)+1}"),
                side=str(r.get("side", "both")).lower(),
                timeframe=str(r.get("timeframe", "unspecified")).lower(),
                conditions=conds,
                stop=stop,
                target=tgt,
            )
        except Exception as e:
            out.rejected.append({"id": str(r.get("id", "?")), "reason": f"coerce failed: {e}"})
            continue
        err = rule.validate()
        if err:
            out.rejected.append({"id": rule.id, "reason": err})
            continue
        out.rules.append(rule)
    for rj in d.get("rejected") or []:
        out.rejected.append({"id": str(rj.get("id", "?")), "reason": str(rj.get("reason", ""))})
    return out


# ── LLM call ─────────────────────────────────────────────────────────────────

def _call_ollama(prompt: str, model: str, host: str) -> str:
    r = requests.post(
        f"{host.rstrip('/')}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
        timeout=600,
    )
    r.raise_for_status()
    return r.json().get("response", "")


# ── Public ───────────────────────────────────────────────────────────────────

def compile_rules(
    strategy_json: dict,
    *,
    model: str = "llama3.1",
    host: str = "http://localhost:11434",
) -> CompiledStrategy:
    raw_rules = strategy_json.get("rules") or []
    if not raw_rules:
        return CompiledStrategy()

    prompt = COMPILER_PROMPT.replace(
        "{rules_json}",
        json.dumps(raw_rules, indent=2, ensure_ascii=False),
    )
    raw = _call_ollama(prompt, model, host)
    try:
        parsed = _parse_json_strict(raw)
    except Exception as e:
        return CompiledStrategy(rejected=[{"id": "?", "reason": f"compiler json failed: {e}"}])
    compiled = _coerce(parsed)
    if not compiled.rules and not compiled.rejected:
        print("[compiler] WARNING: LLM returned empty output — re-run to retry", file=sys.stderr)
    elif not compiled.rules:
        print(f"[compiler] WARNING: 0 rules compiled; {len(compiled.rejected)} rejected", file=sys.stderr)
    return compiled


def write_compiled(comp: CompiledStrategy, path: Path) -> None:
    path.write_text(
        json.dumps({
            "rules": [
                {**asdict(r), "conditions": [asdict(c) for c in r.conditions],
                 "stop": asdict(r.stop), "target": asdict(r.target)}
                for r in comp.rules
            ],
            "rejected": comp.rejected,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_compiled(path: Path) -> CompiledStrategy:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = CompiledStrategy(rejected=data.get("rejected", []))
    for r in data.get("rules", []):
        rule = CompiledRule(
            id=r["id"],
            side=r["side"],
            timeframe=r["timeframe"],
            conditions=[Condition(**c) for c in r["conditions"]],
            stop=StopSpec(**r["stop"]),
            target=StopSpec(**{k: r["target"].get(k) for k in ("type", "value", "mult")}),
        )
        out.rules.append(rule)
    return out
