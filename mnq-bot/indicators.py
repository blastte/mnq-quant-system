"""
Technical indicator functions.
All functions accept and return pandas Series or DataFrames.
No side effects — safe to call multiple times.
"""

import numpy as np
import pandas as pd

import config


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = config.RSI_PERIOD) -> pd.Series:
    """
    Wilder's RSI.
    Returns values in [0, 100]. NaN rows (< period bars) are filled with 50
    so they never trigger a false signal.
    """
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    result   = 100 - (100 / (1 + rs))
    return result.fillna(50)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — useful for dynamic SL sizing."""
    high, low, prev_close = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def pivot_high(series: pd.Series, n: int) -> pd.Series:
    """
    Pivot high matching Pine Script ta.pivothigh(n, n).
    Bar i-n is a pivot high if it is the maximum in [i-2n, i].
    The value is reported at bar i (n bars after the pivot) — same delay as Pine.
    """
    arr    = series.values
    result = np.full(len(arr), np.nan)
    for i in range(2 * n, len(arr)):
        pivot_idx = i - n
        window    = arr[i - 2 * n : i + 1]
        if arr[pivot_idx] == window.max() and arr[pivot_idx] > arr[pivot_idx - 1]:
            result[i] = arr[pivot_idx]
    return pd.Series(result, index=series.index)


def pivot_low(series: pd.Series, n: int) -> pd.Series:
    """Pivot low matching Pine Script ta.pivotlow(n, n)."""
    arr    = series.values
    result = np.full(len(arr), np.nan)
    for i in range(2 * n, len(arr)):
        pivot_idx = i - n
        window    = arr[i - 2 * n : i + 1]
        if arr[pivot_idx] == window.min() and arr[pivot_idx] < arr[pivot_idx - 1]:
            result[i] = arr[pivot_idx]
    return pd.Series(result, index=series.index)


def add_all_indicators(
    df: pd.DataFrame,
    fast_period: int = config.FAST_EMA_PERIOD,
    slow_period: int = config.SLOW_EMA_PERIOD,
    rsi_period:  int = config.RSI_PERIOD,
) -> pd.DataFrame:
    """
    Add indicator columns to df in-place and return it.
    Columns added: ema_fast, ema_slow, rsi, atr
    """
    df["ema_fast"] = ema(df["close"], fast_period)
    df["ema_slow"] = ema(df["close"], slow_period)
    df["rsi"]      = rsi(df["close"], rsi_period)
    df["atr"]      = atr(df)
    return df
