#!/usr/bin/env python3
"""Regenerate the knowledge base's "current state" page from structured data.

Excerpt note: in the full system this maintains the KB's hot-state page from a
strategy registry + activity log on a Claude Code stop hook. The paths below
point into the private tree and are shown to illustrate the mechanism.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
REGISTRY = ROOT / "mnq-bot" / "experiments" / "registry.json"
LOG = ROOT / "wiki" / "log.md"
HOT = ROOT / "wiki" / "hot.md"

NEXT_MARKER = "<!-- NEXT_ACTIONS -->"
NEXT_END = "<!-- /NEXT_ACTIONS -->"

DEFAULT_NEXT = (
    "- Run baseline backtest for the current candidate strategy\n"
    "- Re-run any failed video extractions\n"
    "- Review pending promotions"
)


def read_preserved_next_actions():
    if not HOT.exists():
        return DEFAULT_NEXT
    text = HOT.read_text(encoding="utf-8")
    m = re.search(rf"{re.escape(NEXT_MARKER)}(.*?){re.escape(NEXT_END)}", text, re.DOTALL)
    return m.group(1).strip() if m else DEFAULT_NEXT


def last_log_entries(n=5):
    if not LOG.exists():
        return []
    lines = LOG.read_text(encoding="utf-8").splitlines()
    return [l for l in lines if l.startswith("## [")][-n:]


def main():
    if not REGISTRY.exists():
        print("registry.json not found — skipping hot.md update", file=sys.stderr)
        return

    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    cb = reg.get("current_baseline", {})
    exps = reg.get("experiments", [])
    promos = reg.get("promotions", [])
    next_actions = read_preserved_next_actions()
    log_tail = last_log_entries(5)
    today = datetime.now().strftime("%Y-%m-%d")

    lines = [
        "---",
        "type: hot",
        f"updated: {today}",
        "---",
        "",
        "# Hot — Current State",
        "",
        f"*Auto-generated {today} from registry.json + log.md. Edit only the Next Actions block below.*",
        "",
        "---",
        "",
        "## Live System",
        "",
        f"**Baseline**: {cb.get('version', '?')}  (locked {cb.get('locked_at', '?')})",
        "",
        "| Combined PF | Leg A PF | Leg B PF | Max DD | TPW |",
        "|---|---|---|---|---|",
        f"| {cb.get('combined_pf','?')} "
        f"| {cb.get('leg_a_pf','?')} "
        f"| {cb.get('leg_b_pf','?')} "
        f"| ${cb.get('max_dd_mnq','?')} "
        f"| {cb.get('trades_per_week','?')} |",
        "",
    ]

    if promos:
        last = promos[-1]
        lines += [
            f"**Last promotion**: `{last.get('version','?')}` "
            f"({last.get('promoted_at','?')}) — {last.get('change','')}",
            "",
        ]

    lines += ["---", "", "## Recent Experiments", ""]
    if exps:
        lines += ["| Experiment | Verdict | Δ PF | Note |", "|---|---|---|---|"]
        for e in exps[-5:]:
            name = e.get("name", "?")
            verdict = e.get("verdict", "?")
            delta = e.get("delta_pf", 0)
            promoted = e.get("promoted_to", "")
            promo_str = f" → {promoted}" if promoted else ""
            freq = " ⚠" if e.get("freq_warning") else ""
            note = (e.get("note") or "")[:55]
            lines.append(f"| {name} | {verdict}{promo_str}{freq} | {delta:+.3f} | {note} |")
    lines.append("")

    lines += ["---", "", "## Recent Wiki Activity", ""]
    lines += log_tail if log_tail else ["*(no entries yet)*"]
    lines.append("")

    lines += [
        "---",
        "",
        "## Next Actions",
        "",
        "*(Edit this block. It survives regeneration.)*",
        "",
        NEXT_MARKER,
        next_actions,
        NEXT_END,
        "",
    ]

    HOT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wiki/hot.md updated ({today})")


if __name__ == "__main__":
    main()
