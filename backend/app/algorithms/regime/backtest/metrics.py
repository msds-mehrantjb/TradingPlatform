"""Regime backtest metrics."""

from __future__ import annotations


def calculate_backtest_metrics(trades: list[dict], decisions: list[dict], starting_capital: float) -> dict:
    total_pnl = sum(float(trade.get("pnl") or 0) for trade in trades)
    wins = sum(1 for trade in trades if float(trade.get("pnl") or 0) > 0)
    return {
        "totalPnl": total_pnl,
        "tradeCount": len(trades),
        "decisionCount": len(decisions),
        "winRate": wins / len(trades) if trades else 0,
        "returnPercent": (total_pnl / starting_capital) * 100 if starting_capital else 0,
    }

