"""
Performance metrics for backtest results.

All dollar values are USD. Percentages are 0–100 (not 0–1).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backtesting.engine import Trade


def calculate_metrics(
    trades: list[Trade],
    initial_capital: float = 10_000.0,
) -> dict:
    """
    Compute a full set of performance statistics from a list of Trade objects.

    Returns a dict suitable for JSON serialization and AI analysis.
    """
    if not trades:
        return {"total_trades": 0, "note": "No trades generated."}

    wins   = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    pnls   = [t.pnl for t in trades]

    total_pnl     = sum(pnls)
    gross_profit  = sum(t.pnl for t in wins)
    gross_loss    = abs(sum(t.pnl for t in losses))
    profit_factor = round(gross_profit / gross_loss, 3) if gross_loss else float("inf")

    win_rate      = round(len(wins) / len(trades) * 100, 2)
    avg_win       = round(gross_profit / len(wins),   2) if wins   else 0.0
    avg_loss      = round(gross_loss   / len(losses), 2) if losses else 0.0
    expectancy    = round(
        (win_rate / 100) * avg_win - (1 - win_rate / 100) * avg_loss, 2
    )

    # Equity curve and max drawdown
    equity        = initial_capital
    equity_curve  = [equity]
    peak          = equity
    max_dd_usd    = 0.0
    max_dd_pct    = 0.0

    for pnl in pnls:
        equity += pnl
        equity_curve.append(round(equity, 2))
        peak = max(peak, equity)
        dd_usd = peak - equity
        dd_pct = dd_usd / peak * 100 if peak else 0
        max_dd_usd = max(max_dd_usd, dd_usd)
        max_dd_pct = max(max_dd_pct, dd_pct)

    # Consecutive win / loss streaks
    max_consec_wins   = _max_streak(trades, "win")
    max_consec_losses = _max_streak(trades, "loss")

    # Average bars held
    avg_bars_held = round(
        statistics.mean(t.bars_held for t in trades), 1
    ) if trades else 0

    # Exit reason breakdown
    exit_reasons = defaultdict(int)
    for t in trades:
        exit_reasons[t.exit_reason or "UNKNOWN"] += 1

    # Trade direction breakdown
    long_trades  = [t for t in trades if t.direction == "LONG"]
    short_trades = [t for t in trades if t.direction == "SHORT"]

    long_pnl  = round(sum(t.pnl for t in long_trades),  2)
    short_pnl = round(sum(t.pnl for t in short_trades), 2)

    # Hourly P&L (to find best/worst hours)
    hourly = defaultdict(float)
    for t in trades:
        try:
            hour = str(t.entry_time)[11:13]   # "HH" from ISO timestamp
            hourly[hour] = round(hourly[hour] + t.pnl, 2)
        except Exception:
            pass

    best_hour  = max(hourly, key=hourly.get) if hourly else "N/A"
    worst_hour = min(hourly, key=hourly.get) if hourly else "N/A"

    return {
        # Summary
        "total_trades":        len(trades),
        "wins":                len(wins),
        "losses":              len(losses),
        "win_rate_pct":        win_rate,
        # P&L
        "total_pnl_usd":       round(total_pnl, 2),
        "gross_profit_usd":    round(gross_profit, 2),
        "gross_loss_usd":      round(gross_loss, 2),
        "profit_factor":       profit_factor,
        "expectancy_usd":      expectancy,
        # Per trade
        "avg_win_usd":         avg_win,
        "avg_loss_usd":        avg_loss,
        "avg_bars_held":       avg_bars_held,
        # Risk
        "max_drawdown_usd":    round(max_dd_usd, 2),
        "max_drawdown_pct":    round(max_dd_pct, 2),
        "final_equity_usd":    round(equity, 2),
        "return_pct":          round((equity - initial_capital) / initial_capital * 100, 2),
        # Streaks
        "max_consec_wins":     max_consec_wins,
        "max_consec_losses":   max_consec_losses,
        # Breakdowns
        "exit_reasons":        dict(exit_reasons),
        "long_pnl_usd":        long_pnl,
        "short_pnl_usd":       short_pnl,
        "best_hour_et":        best_hour,
        "worst_hour_et":       worst_hour,
        "hourly_pnl":          dict(sorted(hourly.items())),
        # Full equity curve (for charting)
        "equity_curve":        equity_curve,
    }


def _max_streak(trades: list[Trade], kind: str) -> int:
    """Count the longest run of wins or losses."""
    max_run = cur_run = 0
    for t in trades:
        is_target = (kind == "win" and t.pnl > 0) or (kind == "loss" and t.pnl <= 0)
        if is_target:
            cur_run += 1
            max_run = max(max_run, cur_run)
        else:
            cur_run = 0
    return max_run


def print_summary(metrics: dict) -> None:
    """Print a clean text summary to stdout."""
    sep = "=" * 48
    print("\n" + sep)
    print("  BACKTEST RESULTS")
    print(sep)
    print(f"  Trades          : {metrics.get('total_trades', 0)}")
    print(f"  Win Rate        : {metrics.get('win_rate_pct', 0):.1f}%")
    print(f"  Total P&L       : ${metrics.get('total_pnl_usd', 0):+.2f}")
    print(f"  Profit Factor   : {metrics.get('profit_factor', 0):.2f}")
    print(f"  Expectancy/trade: ${metrics.get('expectancy_usd', 0):+.2f}")
    print(f"  Max Drawdown    : ${metrics.get('max_drawdown_usd', 0):.2f}  ({metrics.get('max_drawdown_pct', 0):.1f}%)")
    print(f"  Return          : {metrics.get('return_pct', 0):+.2f}%")
    print(f"  Final Equity    : ${metrics.get('final_equity_usd', 0):,.2f}")
    print(f"  Long P&L        : ${metrics.get('long_pnl_usd', 0):+.2f}")
    print(f"  Short P&L       : ${metrics.get('short_pnl_usd', 0):+.2f}")
    print(f"  Worst Hour (ET) : {metrics.get('worst_hour_et', 'N/A')}:00")
    print(f"  Best  Hour (ET) : {metrics.get('best_hour_et', 'N/A')}:00")
    exits = metrics.get("exit_reasons", {})
    print(f"  Exits  TARGET   : {exits.get('TARGET', 0)}")
    print(f"  Exits  STOP     : {exits.get('STOP', 0)}")
    print(f"  Exits  TIMEOUT  : {exits.get('TIMEOUT', 0)}")
    print(sep + "\n")
