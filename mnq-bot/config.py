# ── Secrets loader ────────────────────────────────────────────────────────────
# Sensitive values come from environment variables (or a local `.env` file
# placed next to this file). Never commit `.env` — see `.env.example`.
import os
from pathlib import Path

# Optional: auto-load a sibling .env file if python-dotenv is installed.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass


def _required(var: str) -> str:
    v = os.getenv(var, "").strip()
    if not v:
        raise RuntimeError(
            f"Missing required env var: {var}. "
            f"Set it in your shell or in mnq-bot/.env (see .env.example)."
        )
    return v


def _optional(var: str, default: str = "") -> str:
    return os.getenv(var, default).strip()


# ── Broker / data credentials (all env-driven; never hard-coded) ───────────────
TV_USERNAME           = _optional("TV_USERNAME")
TV_PASSWORD           = _optional("TV_PASSWORD")

TRADOVATE_USERNAME    = _optional("TRADOVATE_USERNAME")
TRADOVATE_PASSWORD    = _optional("TRADOVATE_PASSWORD")
TRADOVATE_APP_ID      = _optional("TRADOVATE_APP_ID", "Sample App")
TRADOVATE_APP_VERSION = _optional("TRADOVATE_APP_VERSION", "1.0")
TRADOVATE_CID         = int(_optional("TRADOVATE_CID", "0"))
TRADOVATE_SECRET      = _optional("TRADOVATE_SECRET")

# TopStep / ProjectX broker API (userName + apiKey -> JWT). All env-driven.
PROJECTX_USERNAME         = _optional("PROJECT_X_USERNAME") or _optional("PROJECTX_USERNAME")
PROJECTX_API_KEY          = _optional("PROJECT_X_API_KEY")  or _optional("PROJECTX_API_KEY")
PROJECTX_GATEWAY          = _optional("PROJECTX_GATEWAY", "https://api.topstepx.com")
PROJECTX_PAPER_ACCOUNT_ID = _optional("PROJECTX_PAPER_ACCOUNT_ID")

# ── Live / Demo switch ─────────────────────────────────────────────────────────
# Set True ONLY when trading real money.
LIVE_TRADING          = _optional("LIVE_TRADING", "false").lower() in ("1", "true", "yes")

_DEMO_REST_URL        = "https://demo.tradovateapi.com/v1"
_LIVE_REST_URL        = "https://live.tradovateapi.com/v1"
TRADOVATE_REST_URL    = _LIVE_REST_URL if LIVE_TRADING else _DEMO_REST_URL
TRADOVATE_MD_URL      = "https://md.tradovateapi.com/v1"
TRADOVATE_WS_URL      = "wss://md.tradovateapi.com/v1/websocket"

# ── Symbol + MNQ contract specs ────────────────────────────────────────────────
SYMBOL                = "MNQ1!"     # TradingView display symbol
YFINANCE_SYMBOL       = "MNQ=F"     # Yahoo Finance continuous contract
TRADOVATE_CONTRACT    = "MNQM5"     # front-month (update each roll)
TICK_SIZE             = 0.25        # index points per tick
TICK_VALUE            = 0.50        # USD per tick
POINT_VALUE           = 2.00        # USD per full index point

# ── EMA + RSI baseline strategy ────────────────────────────────────────────────
FAST_EMA_PERIOD       = 9
SLOW_EMA_PERIOD       = 21
RSI_PERIOD            = 14
SIGNAL_TIMEFRAME      = "1m"
RSI_LONG_LOW          = 50          # LONG requires RSI >= this
RSI_LONG_HIGH         = 70          # LONG requires RSI <= this
RSI_SHORT_LOW         = 30          # SHORT requires RSI >= this
RSI_SHORT_HIGH        = 50          # SHORT requires RSI <= this

# ── Risk management ────────────────────────────────────────────────────────────
MAX_CONTRACTS          = 20         # absolute per-trade ceiling for MNQ
STOP_LOSS_POINTS       = 10         # points below/above entry
TAKE_PROFIT_POINTS     = 20         # points above/below entry
STOP_LOSS_TICKS        = int(STOP_LOSS_POINTS   / TICK_SIZE)
TAKE_PROFIT_TICKS      = int(TAKE_PROFIT_POINTS / TICK_SIZE)
MAX_DAILY_LOSS_USD     = 700        # daily-loss kill-switch threshold
MAX_TRADES_PER_DAY     = 6
MAX_CONSECUTIVE_LOSSES = 3          # pause after N back-to-back losses

# ── Default exit management (engine defaults for BE move / speed kill) ──────────
DEFAULT_USE_BE          = True      # move stop to breakeven at +1R
DEFAULT_USE_SPEED_KILL  = True      # exit if no +0.5R progress within N bars
DEFAULT_SPEED_KILL_BARS = 8

# ── Backtesting ────────────────────────────────────────────────────────────────
BACKTEST_DATA_PATH       = "data/mnq_5m.csv"
BACKTEST_MAX_HOLD_BARS   = 24
BACKTEST_INITIAL_CAPITAL = 10_000.0
BACKTEST_RESULTS_PATH    = "data/backtest_results.json"

# ── AI analyzer (local Ollama) ─────────────────────────────────────────────────
OLLAMA_URL            = _optional("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL          = _optional("OLLAMA_MODEL", "llama3.1")
OLLAMA_TIMEOUT        = int(_optional("OLLAMA_TIMEOUT", "120"))

# ── Bot behavior ───────────────────────────────────────────────────────────────
DRY_RUN               = _optional("DRY_RUN", "false").lower() in ("1", "true", "yes")
LOG_LEVEL             = "INFO"
DB_PATH               = "trades.db"
