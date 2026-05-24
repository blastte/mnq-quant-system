"""
EMA Crossover + RSI Filter strategy for MNQ futures.

Entry rules
───────────
LONG  (signal = 1):
  • EMA 9  >  EMA 21           (fast above slow = uptrend)
  • RSI between RSI_LONG_LOW and RSI_LONG_HIGH (50–70 default)
    — above 50 = bullish momentum, below 70 = not overbought
  • close  >  EMA 21           (price confirms trend, not just EMAs)

SHORT (signal = -1):
  • EMA 9  <  EMA 21           (fast below slow = downtrend)
  • RSI between RSI_SHORT_LOW and RSI_SHORT_HIGH (30–50 default)
    — below 50 = bearish momentum, above 30 = not oversold
  • close  <  EMA 21           (price confirms trend)

HOLD  (signal = 0):  all other cases

To experiment with parameters, change the values in config.py or
pass them directly to EmaRsiStrategy() at construction time.
"""

import pandas as pd

import config
import indicators
from .base_strategy import BaseStrategy


class EmaRsiStrategy(BaseStrategy):

    def __init__(
        self,
        fast_period:   int   = config.FAST_EMA_PERIOD,
        slow_period:   int   = config.SLOW_EMA_PERIOD,
        rsi_period:    int   = config.RSI_PERIOD,
        rsi_long_low:  float = config.RSI_LONG_LOW,
        rsi_long_high: float = config.RSI_LONG_HIGH,
        rsi_short_low: float = config.RSI_SHORT_LOW,
        rsi_short_high:float = config.RSI_SHORT_HIGH,
    ):
        self.fast_period    = fast_period
        self.slow_period    = slow_period
        self.rsi_period     = rsi_period
        self.rsi_long_low   = rsi_long_low
        self.rsi_long_high  = rsi_long_high
        self.rsi_short_low  = rsi_short_low
        self.rsi_short_high = rsi_short_high

    @property
    def name(self) -> str:
        return (
            f"EMA({self.fast_period}/{self.slow_period}) + "
            f"RSI({self.rsi_period}) "
            f"[L:{self.rsi_long_low}-{self.rsi_long_high} "
            f"S:{self.rsi_short_low}-{self.rsi_short_high}]"
        )

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        # Add indicator columns
        df = indicators.add_all_indicators(
            df,
            fast_period=self.fast_period,
            slow_period=self.slow_period,
            rsi_period=self.rsi_period,
        )

        close    = df["close"]
        ema_fast = df["ema_fast"]
        ema_slow = df["ema_slow"]
        rsi_val  = df["rsi"]

        # ── LONG conditions ───────────────────────────────────────────────────
        long_cond = (
            (ema_fast > ema_slow) &
            (rsi_val  >= self.rsi_long_low) &
            (rsi_val  <= self.rsi_long_high) &
            (close    > ema_slow)
        )

        # ── SHORT conditions ──────────────────────────────────────────────────
        short_cond = (
            (ema_fast < ema_slow) &
            (rsi_val  >= self.rsi_short_low) &
            (rsi_val  <= self.rsi_short_high) &
            (close    < ema_slow)
        )

        df["signal"] = 0
        df.loc[long_cond,  "signal"] =  1
        df.loc[short_cond, "signal"] = -1

        return df
