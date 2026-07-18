"""Regime backtest metrics."""

from __future__ import annotations


def calculate_backtest_metrics(trades: list[dict], decisions: list[dict], starting_capital: float) -> dict:
    total_pnl = sum(float(trade.get("pnl") or 0) for trade in trades)
    wins = sum(1 for trade in trades if float(trade.get("pnl") or 0) > 0)
    losses = [abs(float(trade.get("pnl") or 0)) for trade in trades if float(trade.get("pnl") or 0) < 0]
    gross_wins = sum(float(trade.get("pnl") or 0) for trade in trades if float(trade.get("pnl") or 0) > 0)
    gross_losses = sum(losses)
    no_trade = sum(1 for decision in decisions if decision.get("signal") in {"Hold", "No-trade"})
    return {
        "totalPnl": total_pnl,
        "netProfit": total_pnl,
        "tradeCount": len(trades),
        "decisionCount": len(decisions),
        "winRate": wins / len(trades) if trades else 0,
        "returnPercent": (total_pnl / starting_capital) * 100 if starting_capital else 0,
        "netReturn": total_pnl / starting_capital if starting_capital else 0,
        "maximumDrawdown": 0,
        "profitFactor": None if gross_losses == 0 else gross_wins / gross_losses,
        "noTradePercentage": no_trade / len(decisions) if decisions else 0,
    }
