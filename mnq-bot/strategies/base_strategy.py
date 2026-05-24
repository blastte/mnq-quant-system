"""
Abstract base class for all trading strategies.

To create a new strategy:
  1. Subclass BaseStrategy
  2. Implement the `name` property
  3. Implement `generate_signals(df)` — add a 'signal' column:
       1  = LONG entry
      -1  = SHORT entry
       0  = no trade (HOLD)
  4. Drop it in the strategies/ folder and import it in __init__.py

The strategy must NOT know about order execution, risk limits, or the broker.
It only answers: "given this price data, should I be long, short, or flat?"
"""

from abc import ABC, abstractmethod

import pandas as pd


class BaseStrategy(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name, e.g. 'EMA(9/21) + RSI(14)'."""

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add a 'signal' column to a copy of df and return it.

        The DataFrame passed in has at minimum: open, high, low, close, volume.
        The returned DataFrame must include those columns plus 'signal'
        and any indicator columns the strategy adds (ema_fast, rsi, etc.).

        Values in 'signal':
            1  → LONG
           -1  → SHORT
            0  → HOLD / flat
        """

    # ── Convenience methods (don't override unless needed) ────────────────────

    def latest_signal(self, df: pd.DataFrame) -> int:
        """
        Returns the signal for the most recent complete bar.
        Used by bot.py to get the current directional bias.
        """
        result = self.generate_signals(df)
        return int(result["signal"].iloc[-1])

    def latest_row(self, df: pd.DataFrame) -> pd.Series:
        """Returns the last row with all indicator columns populated."""
        return self.generate_signals(df).iloc[-1]
