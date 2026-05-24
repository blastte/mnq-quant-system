"""
Market data loader for MNQ futures.

Two sources:
  1. CSV file  — for backtesting (use load_csv)
  2. yfinance  — for live/paper trading (use fetch_live)

Expected CSV columns (case-insensitive):
  datetime, open, high, low, close, volume
"""

import logging
from pathlib import Path

import pandas as pd
import yfinance as yf

import config

logger = logging.getLogger(__name__)

_REQUIRED_COLS = {"open", "high", "low", "close"}
_INTERVAL_PERIOD = {
    "1m":  "7d",
    "5m":  "60d",
    "15m": "60d",
    "30m": "60d",
    "1h":  "730d",
    "1d":  "5y",
}


# ── CSV loader ────────────────────────────────────────────────────────────────

def load_csv(path: str = config.BACKTEST_DATA_PATH) -> pd.DataFrame:
    """
    Load OHLCV data from a CSV file for backtesting.

    The CSV must have a datetime column and open/high/low/close columns.
    Volume is optional. Rows are sorted oldest-first.

    Example CSV format:
        datetime,open,high,low,close,volume
        2024-01-02 09:35:00,16800.25,16812.50,16795.00,16805.75,1234
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"CSV not found: {path}\n"
            f"Put your MNQ 5-min data there, or run fetch_live() first."
        )

    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]

    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {missing}")

    # Parse the datetime index
    dt_col = _find_dt_col(df)
    df[dt_col] = pd.to_datetime(df[dt_col], utc=True, errors="coerce")
    df = df.set_index(dt_col).sort_index()
    df.index.name = "datetime"

    # Keep only OHLCV
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep].copy()
    df = df.dropna(subset=["open", "high", "low", "close"])

    logger.info("Loaded %d bars from %s", len(df), path)
    return df


def _find_dt_col(df: pd.DataFrame) -> str:
    for candidate in ("datetime", "date", "time", "timestamp", "Date", "Datetime"):
        if candidate in df.columns:
            return candidate
    # Try the first column
    return df.columns[0]


# ── Live data (yfinance) ──────────────────────────────────────────────────────

def fetch_live(
    symbol: str    = config.YFINANCE_SYMBOL,
    interval: str  = config.SIGNAL_TIMEFRAME,
    lookback: int  = 150,
) -> pd.DataFrame:
    """
    Fetch recent OHLCV bars from Yahoo Finance.
    Used by the live bot; not intended for full historical backtests.
    """
    period = _INTERVAL_PERIOD.get(interval, "30d")
    logger.debug("Fetching %s %s %s (period=%s)", symbol, interval, lookback, period)

    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, auto_adjust=True)

    if df.empty:
        raise ValueError(f"No data returned for {symbol} @ {interval}")

    df = df.rename(columns=str.lower)
    df = df[["open", "high", "low", "close", "volume"]].copy()
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "datetime"
    df = df.tail(lookback)

    logger.debug("Fetched %d bars, last close=%.2f", len(df), df["close"].iloc[-1])
    return df


def fetch_ohlcv(interval: str = config.SIGNAL_TIMEFRAME,
                source: str = "yfinance",
                broker_symbol: str = "MNQ",
                lookback: int = 150,
                include_partial: bool = False) -> pd.DataFrame:
    """Fetch live OHLCV bars.

    source="yfinance" — Yahoo Finance (free, ~10-15 min delayed for futures).
                        Used for dry-run mode.
    source="projectx" — TopStep ProjectX broker (real-time, requires auth).
                        Used for paper/eval/funded modes.
    include_partial    — when True (projectx only), include the in-progress bar
                         at the right edge. Use ONLY for the chart endpoint.
                         The bot must never see partial bars.
    """
    if source == "projectx":
        return fetch_broker_bars(symbol=broker_symbol, interval=interval,
                                 lookback=lookback, include_partial=include_partial)
    return fetch_live(interval=interval, lookback=lookback)


def latest_price(symbol: str = config.YFINANCE_SYMBOL,
                 source: str = "yfinance",
                 broker_symbol: str = "MNQ") -> float:
    """Get latest price.

    source="yfinance" — uses `symbol` (e.g. MNQ=F, ES=F)
    source="projectx" — uses `broker_symbol` (e.g. MNQ, MES) via broker API
    """
    if source == "projectx":
        from ui import projectx
        contract = projectx.get_contract(broker_symbol)
        return projectx.get_latest_price(contract["id"])
    df = fetch_live(symbol=symbol, interval="1m", lookback=2)
    return float(df["close"].iloc[-1])


def fetch_broker_bars(symbol: str = "MNQ",
                      interval: str = config.SIGNAL_TIMEFRAME,
                      lookback: int = 150,
                      include_partial: bool = False) -> pd.DataFrame:
    """Fetch real-time OHLCV bars from TopStep ProjectX broker API.

    Returns same DataFrame shape as fetch_live() so callers are interchangeable:
      UTC DatetimeIndex named 'datetime', cols [open, high, low, close, volume].
    include_partial: see fetch_ohlcv. Default False.
    """
    from ui import projectx
    contract = projectx.get_contract(symbol)
    raw = projectx.get_bars(contract["id"], tf=interval, n=lookback,
                            include_partial=include_partial)
    if not raw:
        raise ValueError(f"No broker bars returned for {symbol} @ {interval}")
    # Convert list[{t,o,h,l,c,v}] → DataFrame matching yfinance shape.
    df = pd.DataFrame(raw)
    df["datetime"] = pd.to_datetime(df["t"], unit="s", utc=True)
    df = df.set_index("datetime").sort_index()
    df = df.rename(columns={"o": "open", "h": "high", "l": "low",
                             "c": "close", "v": "volume"})
    df = df[["open", "high", "low", "close", "volume"]]
    logger.debug("Broker fetched %d bars (%s %s), last close=%.2f",
                 len(df), symbol, interval, df["close"].iloc[-1])
    return df


# ── Market-hours guard ────────────────────────────────────────────────────────

def market_is_open() -> bool:
    """
    MNQ trades ~23 h/day Sun–Fri on CME Globex.
    Returns False only during the daily 60-min maintenance window (5–6 PM ET)
    and all day Saturday.
    """
    from datetime import datetime, timedelta
    # Rough ET offset (ignores DST; good enough for a gate check)
    now_et = datetime.utcnow() - timedelta(hours=4)
    if now_et.weekday() == 5:   # Saturday
        return False
    if now_et.hour == 17:       # 5–6 PM ET maintenance
        return False
    return True


# ── Data export helper ────────────────────────────────────────────────────────

def save_live_to_csv(
    symbol: str   = config.YFINANCE_SYMBOL,
    interval: str = "5m",
    path: str     = config.BACKTEST_DATA_PATH,
) -> None:
    """Download historical data and save as CSV for offline backtesting."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df = fetch_live(symbol=symbol, interval=interval, lookback=5000)
    df.to_csv(path)
    logger.info("Saved %d bars to %s", len(df), path)
    print(f"Saved {len(df)} bars -> {path}")
