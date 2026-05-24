"""
Bar-by-bar backtesting engine.

Simulation rules (no look-ahead bias):
  • Signal evaluated at bar i close.
  • Entry fill at bar i+1 open.
  • SL/TP checked each bar using that bar's high/low range.
  • If SL and TP both in the same bar's range, SL wins (conservative).
  • Max hold: BACKTEST_MAX_HOLD_BARS → force-closed at bar close.
  • Only one open trade at a time.

Dynamic stops (strategy-defined):
  • If the strategy's signal DataFrame includes 'stop_price' and
    'target_price' columns, those are used per trade.
  • Otherwise falls back to config STOP_LOSS_POINTS / TAKE_PROFIT_POINTS.

Position management (optional, matches Pine Script):
  • use_be=True    → move stop to breakeven when price reaches +1R
  • speed_kill_bars > 0 → close if no +0.5R progress in N bars
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

import config
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    direction:    str
    entry_time:   str
    entry_price:  float
    stop_price:   float
    target_price: float
    contracts:    int   = 1   # default 1× for backtests; live override via controller → risk.calculate_levels(contracts=N)
    exit_time:    Optional[str]   = None
    exit_price:   Optional[float] = None
    exit_reason:  Optional[str]   = None   # TARGET | STOP | TIMEOUT | SPEED_KILL | SESSION_END
    pnl:          float           = 0.0
    bars_held:    int             = 0
    # Strategy-recorded FVG zone (for FVG-based strategies) — used by the chart
    # to highlight the exact zone the strategy locked onto. NaN if not used.
    fvg_low:      float           = float("nan")
    fvg_high:     float           = float("nan")
    fvg_time:     Optional[str]   = None     # UTC ISO of FVG formation bar
    sweep_time:   Optional[str]   = None     # UTC ISO of sweep bar
    tf:           Optional[str]   = None     # timeframe the trade fired on ("1m"/"3m"/etc)
    # ── Partial-fill / BE-move support (iFVG masterclass mechanic) ────────────
    # When a strategy provides `internal_target_price`, the engine takes
    # `partial_size_frac` off at that level and moves the stop to entry.
    # Final trade.pnl = partial_pnl + remainder_pnl
    internal_target_price: float       = float("nan")
    partial_size_frac:     float       = 0.5     # fraction of position scaled at internal
    partial_pnl:           float       = 0.0
    partial_close_price:   Optional[float] = None
    partial_close_time:    Optional[str]   = None
    # position management state (not serialised to JSON as sentinel)
    _be_moved:         bool  = field(default=False, repr=False)
    _initial_stop:     float = field(default=0.0,   repr=False)
    _partial_closed:   bool  = field(default=False, repr=False)
    _remaining_frac:   float = field(default=1.0,   repr=False)


@dataclass
class BacktestResult:
    strategy_name: str
    trades:        list[Trade] = field(default_factory=list)
    equity_curve:  list[float] = field(default_factory=list)
    metrics:       dict        = field(default_factory=dict)


class BacktestEngine:

    def __init__(
        self,
        # Fixed fallback stops (used when strategy doesn't provide dynamic ones)
        stop_points:     float = config.STOP_LOSS_POINTS,
        target_points:   float = config.TAKE_PROFIT_POINTS,
        point_value:     float = config.POINT_VALUE,
        max_hold_bars:   int   = config.BACKTEST_MAX_HOLD_BARS,
        initial_capital: float = config.BACKTEST_INITIAL_CAPITAL,
        # Position management
        use_be:          bool  = config.DEFAULT_USE_BE,
        speed_kill_bars: int   = config.DEFAULT_SPEED_KILL_BARS,
        use_speed_kill:  bool  = config.DEFAULT_USE_SPEED_KILL,
        # BE-move trigger as a multiple of R (entry → initial_stop distance).
        # Default 1.0 preserves the historical "+1R" behavior bit-identically.
        be_trigger_r:    float = 1.0,
    ):
        self.stop_points     = stop_points
        self.target_points   = target_points
        self.point_value     = point_value
        self.max_hold_bars   = max_hold_bars
        self.initial_capital = initial_capital
        self.use_be          = use_be
        self.speed_kill_bars = speed_kill_bars if use_speed_kill else 0
        self.be_trigger_r    = be_trigger_r

    def run(self, df: pd.DataFrame, strategy: BaseStrategy) -> BacktestResult:
        df_sig = strategy.generate_signals(df)

        # Check if strategy provides per-trade stop/target
        has_dynamic = (
            "stop_price"   in df_sig.columns and
            "target_price" in df_sig.columns
        )
        # Optional explicit entry_price column (long-term contract).
        # When provided + non-NaN at signal bar, the engine fills at that price
        # instead of closes[i]. Lets strategies pin entry to their intended
        # reference (FVG cap, SD level, opening range edge, etc.) so that
        # strategy-computed stops/targets remain coherent regardless of how
        # the bar closes.
        has_entry_col = "entry_price" in df_sig.columns
        entry_col = df_sig["entry_price"].values if has_entry_col else None

        result        = BacktestResult(strategy_name=strategy.name)
        equity        = self.initial_capital
        in_trade      = False
        trade: Optional[Trade] = None
        bars_in_trade = 0
        # Count of signals dropped due to entry being on the wrong side of
        # stop/target. Surfaced via result.metrics["malformed_signals_dropped"].
        malformed_dropped = 0

        signals    = df_sig["signal"].values
        opens      = df_sig["open"].values
        highs      = df_sig["high"].values
        lows       = df_sig["low"].values
        closes     = df_sig["close"].values
        times      = [str(t) for t in df_sig.index]
        stop_col   = df_sig["stop_price"].values   if has_dynamic else None
        tgt_col    = df_sig["target_price"].values if has_dynamic else None
        # Optional FVG / sweep tracking — only populated by strategies that record them
        fvg_lo_col   = df_sig["fvg_low"].values    if "fvg_low"    in df_sig.columns else None
        fvg_hi_col   = df_sig["fvg_high"].values   if "fvg_high"   in df_sig.columns else None
        sweep_t_col  = df_sig["sweep_time"].values if "sweep_time" in df_sig.columns else None
        # Internal-target column (iFVG masterclass: scale 50% at first internal H/L,
        # move stop to BE). Only acts if column present + value not-NaN.
        int_tgt_col  = (df_sig["internal_target_price"].values
                        if "internal_target_price" in df_sig.columns else None)

        result.equity_curve.append(equity)

        for i in range(len(df_sig)):

            # ── Manage open trade ─────────────────────────────────────────────
            if in_trade and trade is not None:
                bars_in_trade += 1
                h, l = highs[i], lows[i]

                # BE move: if price reaches +be_trigger_r * R, slide stop to entry
                if self.use_be and not trade._be_moved:
                    r_mult = self.be_trigger_r
                    one_r = (trade.entry_price + r_mult * (trade.entry_price - trade._initial_stop)
                             if trade.direction == "LONG"
                             else trade.entry_price - r_mult * (trade._initial_stop - trade.entry_price))
                    if (trade.direction == "LONG"  and h >= one_r) or \
                       (trade.direction == "SHORT" and l <= one_r):
                        trade.stop_price = trade.entry_price
                        trade._be_moved  = True
                        logger.debug("BE triggered @ bar %d  new_stop=%.2f", i, trade.stop_price)

                # ── Internal-target partial-close + BE move (iFVG masterclass) ──
                # When price reaches the first internal H/L between entry and
                # final target, take `partial_size_frac` off and move stop to
                # entry. This models the trader's "scale 50% then BE" rule.
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
                        # Move stop to BE on the remainder
                        trade.stop_price          = trade.entry_price
                        trade._be_moved           = True
                        equity = round(equity + partial_pnl, 2)
                        result.equity_curve.append(equity)

                # Speed kill: no +0.5R progress within N bars
                closed = False
                if self.speed_kill_bars > 0 and bars_in_trade >= self.speed_kill_bars:
                    half_r = (trade.entry_price + 0.5 * (trade.entry_price - trade._initial_stop)
                              if trade.direction == "LONG"
                              else trade.entry_price - 0.5 * (trade._initial_stop - trade.entry_price))
                    start  = max(0, i - self.speed_kill_bars + 1)
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
                        trade, equity = self._close(trade, times[i], trade.stop_price,   "STOP",    equity, bars_in_trade)
                        closed = True
                    elif tp_hit:
                        trade, equity = self._close(trade, times[i], trade.target_price, "TARGET",  equity, bars_in_trade)
                        closed = True
                    elif bars_in_trade >= self.max_hold_bars:
                        trade, equity = self._close(trade, times[i], closes[i],           "TIMEOUT", equity, bars_in_trade)
                        closed = True

                if closed:
                    result.trades.append(trade)
                    result.equity_curve.append(equity)
                    in_trade      = False
                    trade         = None
                    bars_in_trade = 0

            # ── New entry (only if flat and not last bar) ─────────────────────
            if not in_trade and i < len(df_sig) - 1:
                sig = signals[i]
                if sig == 0:
                    continue

                # Fill price priority:
                #   1. strategy-provided entry_price column (if non-NaN)
                #   2. closes[i]  (default — signal bar close)
                # Using next-bar open introduces gap-slippage that makes LONG
                # entries appear worse (open > close) or better (open < close)
                # than reality.
                if has_entry_col and not np.isnan(entry_col[i]):
                    entry_price = float(entry_col[i])
                else:
                    entry_price = closes[i]
                direction   = "LONG" if sig == 1 else "SHORT"

                # Use strategy-provided stop/target if available, else fixed
                dyn_stop = stop_col[i]   if has_dynamic else np.nan
                dyn_tgt  = tgt_col[i]    if has_dynamic else np.nan

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

                # ── INTEGRITY GUARD: skip malformed signals ─────────────────
                # Strategy-provided stops/targets are computed against the
                # strategy's intended entry reference (FVG cap, SD level, OR
                # edge, etc.). If closes[i] has already moved past those
                # levels, the trade is malformed: a "stop hit" would pay
                # positive (phantom win) or a "target hit" would pay negative.
                # Reject these signals rather than fabricating wins.
                if direction == "LONG":
                    if entry_price <= stop or entry_price >= target:
                        malformed_dropped += 1
                        continue
                else:  # SHORT
                    if entry_price >= stop or entry_price <= target:
                        malformed_dropped += 1
                        continue

                # Optional FVG / sweep metadata
                fvg_lo = float(fvg_lo_col[i])  if fvg_lo_col  is not None else float("nan")
                fvg_hi = float(fvg_hi_col[i])  if fvg_hi_col  is not None else float("nan")
                sweep_t = (str(sweep_t_col[i]) if sweep_t_col is not None
                           and sweep_t_col[i] is not None
                           and str(sweep_t_col[i]) != "None"
                           else None)
                fvg_t  = times[i] if (not np.isnan(fvg_lo) and not np.isnan(fvg_hi)) else None
                int_tgt = float(int_tgt_col[i]) if int_tgt_col is not None else float("nan")

                trade = Trade(
                    direction     = direction,
                    entry_time    = times[i + 1],
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
                in_trade      = True
                bars_in_trade = 0

        # Force-close any trade still open at end of data
        if in_trade and trade is not None:
            trade, equity = self._close(trade, times[-1], closes[-1], "TIMEOUT", equity, bars_in_trade)
            result.trades.append(trade)
            result.equity_curve.append(equity)

        from backtesting.metrics import calculate_metrics
        result.metrics = calculate_metrics(result.trades, self.initial_capital)
        result.metrics["malformed_signals_dropped"] = malformed_dropped

        if malformed_dropped > 0:
            logger.warning(
                "Backtest dropped %d malformed signals (stop/target on wrong "
                "side of close). Strategy may be computing stops/targets "
                "against an entry reference that doesn't match closes[i]. "
                "Consider providing an explicit entry_price column.",
                malformed_dropped,
            )

        logger.info(
            "Backtest done: %d trades | P&L $%.2f | win %.1f%%",
            result.metrics.get("total_trades", 0),
            result.metrics.get("total_pnl_usd", 0),
            result.metrics.get("win_rate_pct", 0),
        )
        return result

    def _close(
        self,
        trade:      Trade,
        exit_time:  str,
        exit_price: float,
        reason:     str,
        equity:     float,
        bars_held:  int,
    ) -> tuple[Trade, float]:
        # Size the closing fill by remaining position fraction (1.0 if no partial,
        # 1 - partial_size_frac if a partial close already happened).
        size_factor = trade._remaining_frac
        if trade.direction == "LONG":
            remainder_pnl = (exit_price - trade.entry_price) * \
                            self.point_value * trade.contracts * size_factor
        else:
            remainder_pnl = (trade.entry_price - exit_price) * \
                            self.point_value * trade.contracts * size_factor

        # Combined trade P&L = partial scale-out + remainder exit
        total_pnl = trade.partial_pnl + remainder_pnl

        trade.exit_time   = exit_time
        trade.exit_price  = round(exit_price, 4)
        trade.exit_reason = reason
        trade.pnl         = round(total_pnl, 2)
        trade.bars_held   = bars_held
        return trade, round(equity + remainder_pnl, 2)
