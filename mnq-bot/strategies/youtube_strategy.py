"""Strategy driven by rules extracted from a YouTube video.

Reads `compiled.json` produced by `youtube_extractor.compiler.compile_rules`
and emits BaseStrategy-compatible signals + per-trade stop_price / target_price.

The DSL is intentionally narrow: only operands the engine can actually evaluate
(close/open/high/low/ema_fast/ema_slow/rsi/atr) and operators >, >=, <, <=, ==.
Everything else is rejected at compile time, so this runtime is small.

Multiple rules: ANY rule firing on a bar produces a signal. If both LONG and
SHORT rules fire on the same bar, the bar is skipped (conflicting signals →
no trade, conservative).
"""

from __future__ import annotations

import json
import operator
from pathlib import Path

import numpy as np
import pandas as pd

import config
import indicators
from .base_strategy import BaseStrategy


_OPS = {
    ">":  operator.gt,
    ">=": operator.ge,
    "<":  operator.lt,
    "<=": operator.le,
    "==": operator.eq,
}


def _resolve(operand, df: pd.DataFrame):
    """Return a Series for an operand name, or a scalar for a number."""
    if isinstance(operand, (int, float)):
        return float(operand)
    if isinstance(operand, str):
        try:
            return float(operand)
        except ValueError:
            pass
        if operand in df.columns:
            return df[operand]
    raise ValueError(f"Unknown operand: {operand!r}")


def _eval_conditions(df: pd.DataFrame, conditions: list[dict]) -> pd.Series:
    if not conditions:
        return pd.Series(False, index=df.index)
    mask = pd.Series(True, index=df.index)
    for c in conditions:
        left  = _resolve(c["left"],  df)
        right = _resolve(c["right"], df)
        op    = _OPS[c["op"]]
        mask &= op(left, right)
    return mask.fillna(False)


def _stop_target_for_bar(
    direction: str,
    entry_price: float,
    stop_spec: dict,
    target_spec: dict,
    atr_value: float,
) -> tuple[float, float]:
    """Translate stop/target specs into absolute price levels."""
    sign = 1.0 if direction == "LONG" else -1.0

    # ── stop distance in points ───────────────────────────────────────────
    s_type = stop_spec["type"]
    if s_type == "points":
        stop_dist = float(stop_spec["value"])
    elif s_type == "atr":
        mult = stop_spec.get("mult") or stop_spec.get("value") or 1.0
        stop_dist = float(mult) * float(atr_value)
    elif s_type == "rr":
        # An "rr" stop without a base distance is meaningless; fall back to
        # config STOP_LOSS_POINTS to keep the engine happy.
        stop_dist = float(config.STOP_LOSS_POINTS)
    else:
        stop_dist = float(config.STOP_LOSS_POINTS)

    # ── target distance in points ─────────────────────────────────────────
    t_type = target_spec["type"]
    if t_type == "points":
        tgt_dist = float(target_spec["value"])
    elif t_type == "atr":
        mult = target_spec.get("mult") or target_spec.get("value") or 1.0
        tgt_dist = float(mult) * float(atr_value)
    elif t_type == "rr":
        tgt_dist = float(target_spec["value"]) * stop_dist
    else:
        tgt_dist = float(config.TAKE_PROFIT_POINTS)

    stop   = round(entry_price - sign * stop_dist, 2)
    target = round(entry_price + sign * tgt_dist,  2)
    return stop, target


class YouTubeStrategy(BaseStrategy):

    def __init__(self, rules_path: str | Path):
        self.rules_path = Path(rules_path)
        if not self.rules_path.exists():
            raise FileNotFoundError(f"Rules file not found: {self.rules_path}")

        data = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self._rules: list[dict] = data.get("rules", [])
        self._rejected = data.get("rejected", [])
        if not self._rules:
            raise ValueError(
                f"No compiled rules in {self.rules_path}. "
                f"Rejected: {len(self._rejected)} — "
                f"the source video probably referenced concepts the engine cannot evaluate."
            )

    @property
    def name(self) -> str:
        return f"YouTube[{self.rules_path.stem}|{len(self._rules)}r]"

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = indicators.add_all_indicators(df)

        long_mask  = pd.Series(False, index=df.index)
        short_mask = pd.Series(False, index=df.index)

        # Per-bar stop/target using the FIRST rule that matches each side.
        # This keeps semantics simple when multiple rules fire together.
        long_stop_spec:  list[dict | None] = [None] * len(df)
        long_tgt_spec:   list[dict | None] = [None] * len(df)
        short_stop_spec: list[dict | None] = [None] * len(df)
        short_tgt_spec:  list[dict | None] = [None] * len(df)

        for rule in self._rules:
            mask = _eval_conditions(df, rule["conditions"])
            side = rule["side"]
            if side in ("long", "both"):
                fresh = mask & ~long_mask
                long_mask |= mask
                for i, hit in enumerate(fresh.values):
                    if hit:
                        long_stop_spec[i] = rule["stop"]
                        long_tgt_spec[i]  = rule["target"]
            if side in ("short", "both"):
                fresh = mask & ~short_mask
                short_mask |= mask
                for i, hit in enumerate(fresh.values):
                    if hit:
                        short_stop_spec[i] = rule["stop"]
                        short_tgt_spec[i]  = rule["target"]

        # Conflict resolution: if both fire, skip (signal=0).
        conflict = long_mask & short_mask
        long_mask  &= ~conflict
        short_mask &= ~conflict

        df["signal"] = 0
        df.loc[long_mask,  "signal"] =  1
        df.loc[short_mask, "signal"] = -1

        # Compute absolute stop/target prices using the next bar's open
        # (matches how engine.py fills entries).
        opens = df["open"].values
        atr_v = df["atr"].ffill().fillna(0.0).values
        stop_arr = np.full(len(df), np.nan)
        tgt_arr  = np.full(len(df), np.nan)

        for i in range(len(df) - 1):
            sig = df["signal"].iat[i]
            if sig == 0:
                continue
            entry = opens[i + 1]
            if sig == 1:
                spec_s = long_stop_spec[i]  or {"type": "points", "value": config.STOP_LOSS_POINTS}
                spec_t = long_tgt_spec[i]   or {"type": "points", "value": config.TAKE_PROFIT_POINTS}
                stop, tgt = _stop_target_for_bar("LONG", entry, spec_s, spec_t, atr_v[i])
            else:
                spec_s = short_stop_spec[i] or {"type": "points", "value": config.STOP_LOSS_POINTS}
                spec_t = short_tgt_spec[i]  or {"type": "points", "value": config.TAKE_PROFIT_POINTS}
                stop, tgt = _stop_target_for_bar("SHORT", entry, spec_s, spec_t, atr_v[i])
            stop_arr[i] = stop
            tgt_arr[i]  = tgt

        df["stop_price"]   = stop_arr
        df["target_price"] = tgt_arr
        return df

    def summary(self) -> str:
        lines = [f"YouTubeStrategy from {self.rules_path}"]
        lines.append(f"  compiled rules : {len(self._rules)}")
        lines.append(f"  rejected rules : {len(self._rejected)}")
        for r in self._rules:
            conds = ", ".join(f"{c['left']} {c['op']} {c['right']}" for c in r["conditions"])
            lines.append(f"    [{r['id']}] {r['side']:<5}  "
                         f"{r['timeframe']:<8}  ({conds})")
        if self._rejected:
            lines.append("  rejected:")
            for rj in self._rejected[:5]:
                lines.append(f"    {rj.get('id', '?')}: {rj.get('reason', '')}")
        return "\n".join(lines)
