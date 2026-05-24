# Self-maintaining knowledge base

The research loop (see [`../ARCHITECTURE.md`](../ARCHITECTURE.md)) doesn't just
produce strategies — it produces *knowledge*, and writes it down in a structured
store that later runs read from. This folder ships the **machinery** of that
store. The contents (the actual strategy/experiment pages) are proprietary and
not included.

## How it works

- **One page per thing.** Every strategy, experiment, concept, and comparison
  gets a markdown page under a fixed schema (`SCHEMA.md`). Pages carry
  frontmatter (`type`, `status`, `sources`, `tier`) and link to their raw
  artifacts (result JSON, trade lists) rather than duplicating them.
- **An index read first.** A single catalog page is the entry point; an agent
  reads it at the start of every session before touching raw code or JSON, so it
  reuses prior knowledge instead of re-deriving it. When it hits a concept it
  doesn't recognize, it researches it once, files a page, and that gap is closed
  for every future run.
- **Append-only log + auto-generated state.** Activity is logged chronologically;
  `update_hot.py` regenerates a "current state" summary (live baseline, recent
  experiments, recent activity) from the structured registry + log on a stop
  hook — so the dashboard of where things stand is never stale and never
  hand-maintained. A `Next Actions` block is preserved across regenerations.
- **Tiered trust.** Pages are tagged Tier 1 (compiled DSL rules), Tier 2
  (generated Python, requires a code-review gate), or Tier 3 (rejected ideas,
  kept for the record). The schema encodes the promotion/rejection workflow so
  the agents apply it consistently.

## Files

- `SCHEMA.md` — the workflow rules and page templates the agents follow.
- `update_hot.py` — regenerates the current-state summary from the registry +
  log. Designed to run unattended via a stop hook.

The point: the system's accumulated understanding lives in a queryable,
self-updating form, not in a person's head or a pile of loose JSON.
