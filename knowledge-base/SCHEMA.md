---
type: meta
updated: 2026-05-05
---

# Wiki Schema

Workflow rules for Claude maintaining this wiki. Read this at the start of any session that touches the wiki.

---

## Session startup

1. Read `wiki/index.md` for a full catalog of pages.
2. Before answering strategy or experiment questions, read the relevant wiki pages first; don't re-derive from raw JSON/code unless drilling down is needed.
3. After any backtest, extraction, or analysis, update the wiki per the workflows below.

## Directory layout

```
wiki/
  README.md          overview and navigation
  SCHEMA.md          this file — workflow rules
  index.md           content catalog (update on every ingest)
  log.md             append-only chronological log
  lessons.md         cross-cutting synthesis
  strategies/        one page per strategy module (+ youtube/ + user/ subdirectories)
  experiments/       one page per archived backtest run
  videos/            one page per YouTube source
  concepts/          ICT/SMC/indicator vocabulary
  instruments/       instrument-specific notes
  sessions/          session-time observations
  comparisons/       cross-strategy benchmark tables (strategy-agent audit)
  research/          ingested external strategies awaiting decision (strategy-agent find-similar)
```

## Page templates

### Strategy page
```markdown
---
type: strategy
status: active | experimental | retired | unfamiliar
sources: [mnq-bot/strategies/<file>.py]
related: []
updated: YYYY-MM-DD
---

# <Name>

## Idea
(1-2 paragraph description of the setup logic)

## Entry / Exit / Risk

## Experiments
- [[experiments/<id>]] — one-line outcome

## Verdict so far
```

### Experiment page
```markdown
---
type: experiment
strategy: [[strategies/<name>]]
compared_to: <baseline version>
config: mnq-bot/experiments/<ts>_<name>_config.json
results: mnq-bot/experiments/<ts>_<name>_results.json
metrics: { combined_pf: X, delta_pf: X, max_dd_mnq: X, tpw: X }
verdict: KEEP | REJECT | PROMOTED
updated: YYYY-MM-DD
---

# <Name>

## Hypothesis
## Setup (param delta, baseline version)
## Result
## Reading
- What worked / what didn't
- What to try next
```

### Video page
```markdown
---
type: video
video_id: <id>
url: https://youtube.com/watch?v=<id>
raw: yt_out/<id>/
compiled: yt_out/<id>/compiled.json
strategy_page: [[strategies/youtube/<id>]]
updated: YYYY-MM-DD
---

# <Video Title>

## Summary
## Extracted rules
## Rejected rules
## Backtest verdict
```

### Concept page
```markdown
---
type: concept
referenced_by: []
updated: YYYY-MM-DD
---

# <Concept>

## What it is
## How we use it in this repo
## Open questions
```

### Comparison page
```markdown
---
type: comparison
strategies: [[strategies/<a>]], [[strategies/<b>]]
csv: mnq-bot/data/mnq_5m.csv
metrics: {}
updated: YYYY-MM-DD
---

# Strategy Comparison — <Label>

| Strategy | Trades | Win% | PF | Max DD | TPW | Last Run | Verdict |
|----------|--------|------|----|--------|-----|----------|---------|
| <name>   |        |      |    |        |     |          |         |

## Notes
```

### Research page
```markdown
---
type: research
source_urls: []
concepts: []
status: candidate | building | blocked | declined
updated: YYYY-MM-DD
---

# <Research Title>

## Summary
(What was found and why it's interesting)

## Sources
- [Title](URL) — one-line relevance note

## Within DSL
(Rules that could be expressed in the existing DSL grammar)

## Outside DSL
(Concepts that cannot — FVG, OB, sweep, etc.)

## Academic context
(arXiv/SSRN citations if applicable — from scientific-literature-researcher)

## Decision
(What we decided to do: ingest, build, block, decline — and why)
```

---

## Workflows

### Ingest a YouTube video

After `youtube_extractor` or `to_backtest` finishes:
1. Read `yt_out/<id>/strategy.json` and `compiled.json`.
2. Determine the video title from `timeline.json` or the filename.
3. Create `wiki/videos/<id>.md` and `wiki/strategies/youtube/<id>.md`.
4. For every concept the video references, create or update the relevant `wiki/concepts/<name>.md` and add this video to its `referenced_by`.
5. Update `wiki/index.md` (add entries under Videos and Strategies/YouTube).
6. Append to `wiki/log.md`:
   `## [YYYY-MM-DD] ingest:video | <Title> | <id>`

### Ingest a backtest experiment

After an experiment JSON pair appears in `mnq-bot/experiments/`:
1. Read both `_config.json` and `_results.json`.
2. Create `wiki/experiments/<ts>-<name>.md` with metrics + reading.
3. Open the parent strategy's wiki page and add a line to its **Experiments** list.
4. If `ai_analysis.txt` exists, paste the critique into the experiment page under an **AI Critique** section. (The file is overwritten each run; the wiki is the archive.)
5. Update `wiki/index.md` and append to `wiki/log.md`:
   `## [YYYY-MM-DD] ingest:experiment | <name> | verdict:<VERDICT>`

### Query

1. Read `index.md` to locate relevant pages.
2. Read relevant pages; drill into raw sources only if necessary.
3. If the answer is worth keeping (analysis, comparison, new synthesis), file it as a new wiki page and add it to `index.md`.
4. Append to `log.md`: `## [YYYY-MM-DD] query | <question summary>`

### Lint

Periodically run a health check:
- Orphan pages: any page with no inbound links from `index.md` or other pages
- Stale verdicts: experiment conclusions that newer runs have superseded
- Missing concept pages: concepts referenced in strategy/experiment pages that have no `concepts/` page
- Contradictions: experiments that disagree about a param's effect
- Gaps: strategies with `status: unfamiliar` that have been actively used
- Unreviewed Tier 2: strategy pages with `tier: 2` that have no `code-reviewer` approval noted
- Null verdicts: registry experiments with `verdict: null` older than 7 days

Append: `## [YYYY-MM-DD] lint | <summary of findings>`

### Ingest external research

After `strategy-agent find-similar` or a Tier 3 build rejection:
1. Identify the source URLs and extracted concept list.
2. Create `wiki/research/<slug>.md` using the Research page template.
3. For every new concept referenced, create or update the relevant `wiki/concepts/<name>.md`.
4. Update `wiki/index.md` — add entry under a `## Research` section (create section if absent).
5. Append to `wiki/log.md`:
   `## [YYYY-MM-DD] ingest:research | <slug> | status:<status>`

### Record a comparison

After `strategy-agent audit` or an explicit cross-strategy benchmark:
1. Read the most recent `_results.json` for each strategy in the comparison set.
2. Create or update `wiki/comparisons/<slug>.md` using the Comparison page template.
3. Update `wiki/index.md` — add/update entry under a `## Comparisons` section.
4. Append to `wiki/log.md`:
   `## [YYYY-MM-DD] comparison | <slug> | strategies: <list>`

### Promotion checklist

Before marking an experiment PROMOTED (and before updating `registry.json["promotions"]`):
1. Verify numeric gate: `combined_pf >= current_baseline.combined_pf + 0.03` AND `max_dd_mnq <= current_baseline.max_dd_mnq * 1.10`.
2. Verify sample size: ≥ 30 trades spanning ≥ 90 calendar days.
3. Verify regime robustness: PF ≥ 1.0 in both first and second half of the data period.
4. For Tier 2 strategies: confirm `code-reviewer` APPROVED and `# UNVERIFIED` banner exists.
5. Update the experiment page `verdict: PROMOTED`.
6. Update `registry.json["promotions"]` and `registry.json["current_baseline"]` manually.
7. Append to `wiki/log.md`:
   `## [YYYY-MM-DD] promoted | <experiment-id> | <strategy> | combined_pf: <X.XXX>`

---

## Frontmatter conventions

- `status` on strategy pages: `active` (regularly backtested), `experimental` (under investigation), `retired` (dropped), `unfamiliar` (in codebase but never studied here)
- `tier` on agent-built strategy pages: `1` (DSL compiled.json), `2` (LLM-generated Python in strategies/user/), `3` (rejected — wiki/research/ only)
- `related` links: always use `[[wiki-link]]` style pointing to wiki pages, not file paths
- `updated`: ISO date of last meaningful edit (not just reformatting)
- Raw source paths: relative to repo root, no leading slash

## Link style

- Internal wiki links: `[[page-name]]` (Obsidian-style, no .md extension)
- External file references: plain relative path, e.g. `mnq-bot/experiments/foo.json`
- YouTube URLs: full https URL in frontmatter; short `[[videos/<id>]]` in body links
