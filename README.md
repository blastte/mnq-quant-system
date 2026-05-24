# MNQ Quant System

An automated futures-trading research system for MNQ (Micro Nasdaq-100), built
solo from scratch. This repository is a **curated, secrets-free excerpt** of the
full system — it contains the parts that are interesting to *read*: the
video-to-strategy compiler and the backtesting engine. The live broker
integration, proprietary market data, and prompt internals are intentionally
excluded (see [What's not here](#whats-not-here)).

**For the whole system — the multi-agent research loop, the self-maintaining
knowledge base, and the live execution layer — see
[`ARCHITECTURE.md`](ARCHITECTURE.md).** The knowledge-base machinery ships in
[`knowledge-base/`](knowledge-base/).

The two pieces worth your time:

1. **A compiler that turns an arbitrary YouTube trading video into executable,
   backtested code** — and refuses to hallucinate.
2. **A strictly no-look-ahead backtesting engine** that the compiled strategies
   run against.

---

## 1. YouTube → executable strategy (`youtube_extractor/`)

A trader explains a setup on video. This pipeline turns that into something a
backtester can actually run:

```
video URL
  └─ transcript.py    download + clean the transcript
  └─ frames.py        extract keyframes (scene-change detection)
  └─ structurize.py   align text + frames onto one timeline
  └─ chunker.py       batch into LLM-sized windows
  └─ ai_strategy.py   LLM extracts free-text rules  → strategy.json
  └─ compiler.py      compile to a strict DSL        → compiled.json
  └─ to_backtest.py   run it through the engine
```

### The interesting part: the compiler rejects what it can't verify

The compiler (`compiler.py`) targets a **deliberately narrow grammar**:

- **Operands:** `open, high, low, close, ema_fast, ema_slow, rsi, atr, <number>`
- **Operators:** `> >= < <= ==`

Anything that can't be expressed in that grammar — order blocks, fair-value
gaps, liquidity sweeps, chart patterns — is **not** forced into a fake rule. It
is routed to an explicit `rejected` list with a reason:

```json
{
  "rules":    [{"id": "R1", "conditions": [{"left": "close", "op": ">", "right": "ema_fast"}]}],
  "rejected": [{"id": "R?", "reason": "order block not expressible in grammar"}]
}
```

**Why build a rejection layer at all?** Because the failure mode of forcing an
LLM into a rigid schema is that it *invents plausible-but-false numbers* — a
price threshold that was never in the source, which then silently produces zero
trades (or worse, fake ones) on real data. The system's core job is to **know
what it doesn't know and discard it**, rather than launder a hallucination into
a strategy. That principle — constrain the model with structure, not hope —
runs through the whole system.

---

## 2. The backtesting engine (`mnq-bot/backtesting/`)

`engine.py` is a bar-by-bar simulator with no look-ahead by construction:

- A signal generated on bar *i*'s **close** can only fill on bar *i+1*'s
  **open**. There is no path by which a strategy sees the future.
- Malformed signals (stop/target on the wrong side of the entry) are dropped
  and counted, not silently filled.
- Supports strategy-provided dynamic stops/targets, break-even moves at
  configurable R-multiples, partial fills, and time-based exits.

`metrics.py` computes profit factor, expectancy, max drawdown, win rate, exit-
reason breakdown, and hourly P&L. `multi_engine.py` runs many strategies over
the same data for portfolio-level aggregation.

Strategies subclass `BaseStrategy` (`mnq-bot/strategies/base_strategy.py`) and
implement `generate_signals(df) -> df`. A readable example ships here:
`ema_rsi_strategy.py` (a pure-indicator baseline). The tuned, proprietary
strategies that carry the actual edge are intentionally excluded — this repo is
about the machinery, not the setups.

---

## On rigor (and a bug I caught in my own work)

Every strategy clears the no-look-ahead engine, walk-forward validation,
multi-tier slippage/commission stress tests, and Monte Carlo bootstraps before
I trust it.

The honest part: I found and fixed a methodology bug in my **own** audit that
was inflating results by ~25%. A composite strategy has multiple legs; an early
audit ran each leg standalone and concatenated the trade lists, which counted
overlapping legs as separate trades — trades a single live account can't
actually take, because it can only hold one position at a time. Correcting it
(running the composite through the engine's single-position gate) dropped the
trade count from **717 → ~517** and the profit factor from **~3.13 → 2.34**
(5-minute bars; ~2.62 on 1-minute). I report the smaller, true number. The
distinction between "does each leg have edge?" and "what will one account
actually make?" is the whole game in prop-firm trading.

---

## What's not here

This is a research/showcase excerpt, deliberately scoped:

- **No live broker code.** The autonomous live execution layer (an LLM placing
  real-money trades on funded prop-firm accounts inside hard-coded risk limits)
  is not in this repo — it carries credentials and operational risk.
- **No market data.** MNQ/MES CSVs are licensed and large; supply your own (see
  `mnq-bot/data_reader.py` for the expected format).
- **No secrets.** All configuration is environment-driven; copy
  `mnq-bot/.env.example` to `.env` and fill in your own values.
- **No multi-agent orchestration files.** The system also runs an agentic loop
  that researches unfamiliar concepts, drafts and backtests new strategy
  variants, and critiques the results into a knowledge base it reads on later
  runs — but that orchestration lives outside this code excerpt.

---

## Running it

```bash
pip install -r youtube_extractor/requirements.txt   # pipeline deps
# numpy / pandas required for the engine

# extract a strategy from a video (needs a local Ollama instance for the LLM)
python -m youtube_extractor "<youtube-url>"

# backtest a compiled strategy (supply your own MNQ CSV)
python -m youtube_extractor.to_backtest "<url>" --run --ltf path/to/mnq_5m.csv
```

## Stack

Python · numpy · pandas · Ollama (local LLM) / Anthropic API · ffmpeg ·
yt-dlp · Whisper (transcript fallback).
