"""
MultiPositionBacktestEngine — drop-in replacement for BacktestEngine that
supports MULTIPLE concurrent open positions.

Use this for composite strategies (Full Live Composite, etc.) where the
underlying sub-strategies fire independently and shouldn't be blocked by
each other's open positions.

Differences from BacktestEngine:
  - Tracks a list of open Trade objects, not a single trade
  - Each bar: iterate through all open trades, check SL/TP/BE/timeout per-trade
  - New entries are always allowed (the strategy itself decides via its
    `traded_today` etc. logic; the engine doesn't impose a "one trade at
    a time" rule)
  - Equity aggregates across all closed + partial trades

Single-position backtests should still use BacktestEngine for clean parity
with production semantics.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

import config
from strategies.base_strategy import BaseStrategy
from backtesting.engine import Trade, BacktestResult

logger = logging.getLogger(__name__)


class MultiPositionBacktestEngine:
    """Multi-position version of BacktestEngine. Same constructor signature."""

    def __init__(
        self,
        stop_points:     float = config.STOP_LOSS_POINTS,
        target_points:   float = config.TAKE_PROFIT_POINTS,
        point_value:     float = config.POINT_VALUE,
        max_hold_bars:   int   = config.BACKTEST_MAX_HOLD_BARS,
        initial_capital: float = config.BACKTEST_INITIAL_CAPITAL,
        use_be:          bool  = config.DEFAULT_USE_BE,
        speed_kill_bars: int   = config.DEFAULT_SPEED_KILL_BARS,
        use_speed_kill:  bool  = config.DEFAULT_USE_SPEED_KILL,
    ):
        self.stop_points     = stop_points
        self.target_points   = target_points
        self.point_value     = point_value
        self.max_hold_bars   = max_hold_bars
        self.initial_capital = initial_capital
        self.use_be          = use_be
        self.speed_kill_bars = speed_kill_bars if use_speed_kill else 0

    def _close(self, trade: Trade, exit_time: str, exit_price: float,
               reason: str, equity: float, bars_held: int) -> tuple[Trade, float]:
        """Close a trade, compute PnL on the remaining size, update equity."""
        if trade.direction == "LONG":
            pnl_remainder = (exit_price - trade.entry_price) * \
                            self.point_value * trade.contracts * trade._remaining_frac
        else:
            pnl_remainder = (trade.entry_price - exit_price) * \
                            self.point_value * trade.contracts * trade._remaining_frac
        trade.pnl         = round(trade.partial_pnl + pnl_remainder, 2)
        trade.exit_time   = exit_time
        trade.exit_price  = round(float(exit_price), 2)
        trade.exit_reason = reason
        trade.bars_held   = bars_held
        equity            = round(equity + pnl_remainder, 2)
        return trade, equity

    def run(self, df: pd.DataFrame, strategy: BaseStrategy) -> BacktestResult:
        df_sig = strategy.generate_signals(df)
        has_dynamic = (
            "stop_price"   in df_sig.columns and
            "target_price" in df_sig.columns
        )
        has_entry_col = "entry_price" in df_sig.columns
        entry_col = df_sig["entry_price"].values if has_entry_col else None

        result        = BacktestResult(strategy_name=strategy.name)
        equity        = self.initial_capital
        open_trades: list[tuple[Trade, int]] = []   # list of (trade, bars_in_trade)
        malformed_dropped = 0

        signals = df_sig["signal"].values
        opens   = df_sig["open"].values
        highs   = df_sig["high"].values
        lows    = df_sig["low"].values
        closes  = df_sig["close"].values
        times   = [str(t) for t in df_sig.index]
        stop_col = df_sig["stop_price"].values   if has_dynamic else None
        tgt_col  = df_sig["target_price"].values if has_dynamic else None
        fvg_lo_col   = df_sig["fvg_low"].values    if "fvg_low"    in df_sig.columns else None
        fvg_hi_col   = df_sig["fvg_high"].values   if "fvg_high"   in df_sig.columns else None
        sweep_t_col  = df_sig["sweep_time"].values if "sweep_time" in df_sig.columns else None
        int_tgt_col  = (df_sig["internal_target_price"].values
                        if "internal_target_price" in df_sig.columns else None)

        result.equity_curve.append(equity)

        for i in range(len(df_sig)):
            h, l = highs[i], lows[i]

            # ── Manage all open trades ───────────────────────────────────────
            still_open: list[tuple[Trade, int]] = []
            for trade, bars_in_trade in open_trades:
                bars_in_trade += 1

                # BE move
                if self.use_be and not trade._be_moved:
                    one_r = (trade.entry_price + (trade.entry_price - trade._initial_stop)
                             if trade.direction == "LONG"
                             else trade.entry_price - (trade._initial_stop - trade.entry_price))
                    if (trade.direction == "LONG"  and h >= one_r) or \
                       (trade.direction == "SHORT" and l <= one_r):
                        trade.stop_price = trade.entry_price
                        trade._be_moved  = True

                # Internal-target partial close (iFVG masterclass)
                if (not trade._partial_closed
                    and not np.isnan(trade.internal_target_price)
                    and trade.partial_size_frac > 0):
                    int_p = trade.internal_target_price
                    int_hit = (trade.direction == "LONG"  and h >= int_p) or \
                              (trade.direction == "SHORT" and l <= int_p)
                    if int_hit:
                        scale = trade.partial_size_frac
                        if trade.direction == "LONG":
                            partial_pnl = (int_p - trade.entry_price) * \
                                          self.point_value * trade.contracts * scale
                        else:
                            partial_pnl = (trade.entry_price - int_p) * \
                                          self.point_value * trade.contracts * scale
                        trade.partial_pnl         = round(partial_pnl, 2)
                        trade.partial_close_price = round(int_p, 2)
                        trade.partial_close_time  = times[i]
                        trade._partial_closed     = True
                        trade._remaining_frac     = 1.0 - scale
                        trade.stop_price          = trade.entry_price
                        trade._be_moved           = True
                        equity = round(equity + partial_pnl, 2)
                        result.equity_curve.append(equity)

                # Speed kill
                closed = False
                if self.speed_kill_bars > 0 and bars_in_trade >= self.speed_kill_bars:
                    half_r = (trade.entry_price + 0.5 * (trade.entry_price - trade._initial_stop)
                              if trade.direction == "LONG"
                              else trade.entry_price - 0.5 * (trade._initial_stop - trade.entry_price))
                    start = max(0, i - self.speed_kill_bars + 1)
                    if trade.direction == "LONG"  and np.max(highs[start:i+1]) < half_r:
                        trade, equity = self._close(trade, times[i], closes[i], "SPEED_KILL", equity, bars_in_trade)
                        closed = True
                    elif trade.direction == "SHORT" and np.min(lows[start:i+1]) > half_r:
                        trade, equity = self._close(trade, times[i], closes[i], "SPEED_KILL", equity, bars_in_trade)
                        closed = True

                if not closed:
                    sl_hit = (trade.direction == "LONG"  and l <= trade.stop_price) or \
                             (trade.direction == "SHORT" and h >= trade.stop_price)
                    tp_hit = (trade.direction == "LONG"  and h >= trade.target_price) or \
                             (trade.direction == "SHORT" and l <= trade.target_price)
                    if sl_hit:
                        trade, equity = self._close(trade, times[i], trade.stop_price, "STOP", equity, bars_in_trade)
                        closed = True
                    elif tp_hit:
                        trade, equity = self._close(trade, times[i], trade.target_price, "TARGET", equity, bars_in_trade)
                        closed = True
                    elif bars_in_trade >= self.max_hold_bars:
                        trade, equity = self._close(trade, times[i], closes[i], "TIMEOUT", equity, bars_in_trade)
                        closed = True

                if closed:
                    result.trades.append(trade)
                    result.equity_curve.append(equity)
                else:
                    still_open.append((trade, bars_in_trade))

            open_trades = still_open

            # ── New entry (allowed even if other trades open) ────────────────
            if i < len(df_sig) - 1:
                sig = signals[i]
                if sig == 0:
                    continue

                if has_entry_col and not np.isnan(entry_col[i]):
                    entry_price = float(entry_col[i])
                else:
                    entry_price = closes[i]
                direction = "LONG" if sig == 1 else "SHORT"

                dyn_stop = stop_col[i] if has_dynamic else np.nan
                dyn_tgt  = tgt_col[i]  if has_dynamic else np.nan

                if not np.isnan(dyn_stop) and not np.isnan(dyn_tgt):
                    stop   = dyn_stop
                    target = dyn_tgt
                else:
                    if direction == "LONG":
                        stop   = round(entry_price - self.stop_points,  2)
                        target = round(entry_price + self.target_points, 2)
                    else:
                        stop   = round(entry_price + self.stop_points,  2)
                        target = round(entry_price - self.target_points, 2)

                # Integrity guard
                if direction == "LONG":
                    if entry_price <= stop or entry_price >= target:
                        malformed_dropped += 1
                        continue
                else:
                    if entry_price >= stop or entry_price <= target:
                        malformed_dropped += 1
                        continue

                fvg_lo = float(fvg_lo_col[i]) if fvg_lo_col is not None else float("nan")
                fvg_hi = float(fvg_hi_col[i]) if fvg_hi_col is not None else float("nan")
                sweep_t = (str(sweep_t_col[i]) if sweep_t_col is not None
                           and sweep_t_col[i] is not None
                           and str(sweep_t_col[i]) != "None"
                           else None)
                fvg_t  = times[i] if (not np.isnan(fvg_lo) and not np.isnan(fvg_hi)) else None
                int_tgt = float(int_tgt_col[i]) if int_tgt_col is not None else float("nan")

                trade = Trade(
                    direction     = direction,
                    entry_time    = times[i + 1] if i + 1 < len(times) else times[i],
                    entry_price   = entry_price,
                    stop_price    = stop,
                    target_price  = target,
                    fvg_low       = fvg_lo,
                    fvg_high      = fvg_hi,
                    fvg_time      = fvg_t,
                    sweep_time    = sweep_t,
                    internal_target_price = int_tgt,
                    _initial_stop = stop,
                )
                open_trades.append((trade, 0))

        # Force-close any trades still open at end of data
        for trade, bars_in_trade in open_trades:
            trade, equity = self._close(trade, times[-1], closes[-1], "TIMEOUT", equity, bars_in_trade)
            result.trades.append(trade)
            result.equity_curve.append(equity)

        from backtesting.metrics import calculate_metrics
        result.metrics = calculate_metrics(result.trades, self.initial_capital)
        result.metrics["malformed_signals_dropped"] = malformed_dropped
        if malformed_dropped > 0:
            logger.warning("MultiPositionEngine dropped %d malformed signals", malformed_dropped)
        return result
