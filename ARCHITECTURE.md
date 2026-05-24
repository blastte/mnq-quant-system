# Architecture

This repository is a **curated excerpt** of a larger automated trading system.
This document explains the whole system end to end, so you can see how the two
shipped components — the [video→strategy compiler](youtube_extractor/) and the
[no-look-ahead backtesting engine](mnq-bot/backtesting/) — fit into it, and what
is deliberately kept private (see [In this repo vs. private](#in-this-repo-vs-private)).

The design goal across the whole system is one idea: **constrain the model with
structure and code, never with hope.** Every place an LLM touches the system, it
is boxed in by a formal grammar, a hard-coded gate, or a verification step — so
its failure modes are caught, not shipped.

---

## System overview

```
   ┌─────────────────────────────────────────────────────────────────┐
   │  IDEA SOURCES                                                     │
   │  YouTube strategy videos   ·   external research   ·   prior runs │
   └───────────────┬───────────────────────────┬──────────────────────┘
                   │                            │
          ┌────────▼─────────┐        ┌─────────▼───────────────────┐
          │ VIDEO → DSL       │        │ MULTI-AGENT R&D LOOP         │
          │ COMPILER          │        │ research → architect → build │
          │ (this repo)       │        │ → risk/leak gates → backtest │
          │ rejects what it   │        │ → critic (keep/reject/promo) │
          │ can't verify      │        └─────────┬───────────────────┘
          └────────┬──────────┘                  │
                   │        strategy artifacts    │
                   └───────────────┬──────────────┘
                                   │
                      ┌────────────▼─────────────┐
                      │ BACKTEST + VALIDATION      │
                      │ no-look-ahead engine       │
                      │ walk-forward · Monte Carlo │
                      │ slippage/commission stress │
                      │ (engine: this repo)        │
                      └────────────┬───────────────┘
                                   │ results
                      ┌────────────▼─────────────┐         feedback
                      │ SELF-MAINTAINING          │◄──────────────────┐
                      │ KNOWLEDGE BASE            │                   │
                      │ structured pages + index  │   agents read it  │
                      │ auto-generated hot-state  │   on later runs   │
                      │ (machinery: /knowledge-base)──────────────────┘
                      └────────────┬───────────────┘
                                   │ promoted strategy
                      ┌────────────▼─────────────┐
                      │ LIVE EXECUTION LAYER       │
                      │ LLM decides → ~10 hard-     │
                      │ coded risk gates it can't   │
                      │ override → prop-firm broker │
                      │ (private)                   │
                      └─────────────────────────────┘
```

---

## 1. Video → strategy compiler  *(in this repo: [`youtube_extractor/`](youtube_extractor/))*

Turns an arbitrary YouTube trading video into executable, backtested code:
transcribe → extract keyframes → LLM pulls the trader's rules → compile to a
**deliberately narrow formal grammar** (operands `open/high/low/close/ema/rsi/
atr/number`, operators `> >= < <= ==`). Anything the grammar can't express —
order blocks, fair-value gaps, liquidity sweeps — is routed to an explicit
`rejected` list with a reason, instead of being hallucinated into a fake rule.
See [the README](README.md#1-youtube--executable-strategy-youtube_extractor) for detail.

## 2. Backtesting + validation  *(engine in this repo: [`mnq-bot/backtesting/`](mnq-bot/backtesting/))*

A strictly no-look-ahead, bar-by-bar simulator: a signal on bar *i*'s close can
only fill on bar *i+1*'s open. Around it sits a validation stack — walk-forward
analysis, multi-tier slippage/commission stress profiles, and Monte Carlo
bootstraps — that a candidate must clear before it's trusted. Composite
strategies are additionally re-audited through a single-position gate that
mirrors what one live account can actually hold (one position at a time), which
is the correct denominator for live P&L (vs. the inflated per-leg sum).

## 3. Multi-agent R&D loop  *(orchestration private)*

New strategies aren't hand-written one at a time — they're produced by a loop of
specialized agents, each with a single job:

- **Researchers** gather external evidence (quantitative edges, domain concepts,
  ML feature-importance on past trades, academic literature).
- **Architect** synthesizes one *testable* edge thesis with explicit invalidation
  criteria and a builder-ready spec.
- **Builder** turns the spec into a runnable strategy artifact.
- **Gates** run *before* compute is spent: a risk enforcer checks prop-firm rule
  compliance, and code/data-integrity reviewers catch look-ahead bias and
  leakage. A guaranteed-to-fail variant is blocked here.
- **Backtester** runs it through the engine and persists the result.
- **Critic** renders a keep / reject / promote verdict and writes the outcome —
  and the *reasoning* — back into the knowledge base.

The loop runs unattended and improves over time because each verdict becomes
input the next iteration reads.

## 4. Self-maintaining knowledge base  *(machinery in this repo: [`knowledge-base/`](knowledge-base/))*

Every run writes a structured page (one per strategy, experiment, concept) under
a fixed schema, and updates a single index the agents read *first* on every
session — so when the system hits a concept it doesn't recognize, it researches
it once, files it, and reuses it instead of repeating the gap. A generator script
(`update_hot.py`, fired on a stop hook) regenerates a "current state" summary
from the structured registry + activity log automatically. This folder ships the
*machinery* (schema + generator), not the proprietary contents.

## 5. Live execution layer  *(private)*

A promoted strategy runs live, where an LLM makes the real-time decision and
**~10 hard-coded risk checks it cannot override** stand between it and the
broker: position-size caps, an R:R floor, a daily-loss gate, stop-distance
bounds, and an anti-hallucination check that rejects any decision citing a price
level not present in its input. The caps live in code, not in the prompt — the
model cannot negotiate its own risk limits. This layer places real-money trades
on funded prop-firm accounts; its broker integration is not in this repo.

---

## In this repo vs. private

| Component | Here? | Why |
|---|---|---|
| Video → DSL compiler | ✅ | Self-contained, novel, no edge to protect |
| No-look-ahead backtest engine + metrics | ✅ | Infrastructure, demonstrates rigor |
| `BaseStrategy` + a generic indicator baseline | ✅ | Shows the contract; carries no edge |
| Knowledge-base schema + auto-generator | ✅ | The *machinery* of the self-maintaining KB |
| This architecture doc | ✅ | So the curated slice reads as part of the whole |
| Live broker / execution code | ❌ | Carries credentials and operational risk |
| Market data (MNQ/MES CSVs) | ❌ | Licensed and large; bring your own |
| Multi-agent orchestration files | ❌ | They name proprietary strategy internals |
| Proprietary strategy logic + research results | ❌ | This is the edge — deliberately not shipped |

The omissions are intentional. The interesting engineering is the *machinery*
above; the specific profitable strategies are replaceable by comparison and stay
private.
