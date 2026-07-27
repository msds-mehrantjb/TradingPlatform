"""Regime backtest metrics."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


def calculate_backtest_metrics(trades: list[dict[str, Any]], decisions: list[dict[str, Any]], starting_capital: float) -> dict[str, Any]:
    net_values = [float(trade.get("netPnl", trade.get("pnl") or 0.0)) for trade in trades]
    gross_values = [float(trade.get("grossPnl", trade.get("pnl") or 0.0)) for trade in trades]
    net_profit = sum(net_values)
    gross_pnl = sum(gross_values)
    total_costs = sum(float(trade.get("totalCosts") or 0.0) for trade in trades)
    wins = [value for value in net_values if value > 0]
    losses = [abs(value) for value in net_values if value < 0]
    gross_wins = sum(wins)
    gross_losses = sum(losses)
    no_trade = sum(1 for decision in decisions if decision.get("signal") in {"Hold", "No-trade"})
    equity_curve = _equity_curve(trades, starting_capital)
    maximum_drawdown, maximum_drawdown_pct = _maximum_drawdown(equity_curve)
    returns = [value / starting_capital for value in net_values] if starting_capital else []
    downside = [value for value in returns if value < 0]
    order_opportunities = sum(1 for decision in decisions if decision.get("orderIntent") is not None)
    filled_opportunities = sum(1 for decision in decisions if _execution_status(decision) in {"filled", "partially_filled"})
    rejected = sum(1 for decision in decisions if decision.get("orderIntent") is not None and _execution_status(decision) not in {"filled", "partially_filled"})
    missed_fills = sum(1 for decision in decisions if decision.get("orderIntent") is not None and _execution_status(decision) in {"missed", "expired", "cancelled", "rejected"})
    turnover = sum(abs(float(trade.get("entryPrice") or 0.0) * int(trade.get("quantity") or 0)) for trade in trades)
    realised_slippage = sum(float(trade.get("entrySlippage") or 0.0) + float(trade.get("exitSlippage") or 0.0) for trade in trades)
    average_slippage = realised_slippage / len(trades) if trades else 0.0
    return {
        "totalPnl": net_profit,
        "grossPnl": gross_pnl,
        "netProfit": net_profit,
        "totalCosts": total_costs,
        "tradeCount": len(trades),
        "decisionCount": len(decisions),
        "winRate": len(wins) / len(trades) if trades else 0,
        "averageWin": _average(wins),
        "averageLoss": _average(losses),
        "expectancy": _average(net_values),
        "returnPercent": (net_profit / starting_capital) * 100 if starting_capital else 0,
        "netReturn": net_profit / starting_capital if starting_capital else 0,
        "maximumDrawdown": maximum_drawdown,
        "maximumDrawdownPercent": maximum_drawdown_pct,
        "profitFactor": None if gross_losses == 0 else gross_wins / gross_losses,
        "noTradePercentage": no_trade / len(decisions) if decisions else 0,
        "sharpeLike": _sharpe_like(returns),
        "downsideDeviation": _downside_deviation(downside),
        "turnover": turnover,
        "turnoverRatio": turnover / starting_capital if starting_capital else 0,
        "averageHoldingBars": _average([float(trade.get("holdingBars") or 0.0) for trade in trades]),
        "averageSlippage": average_slippage,
        "realisedSlippage": realised_slippage,
        "fillRate": filled_opportunities / order_opportunities if order_opportunities else 0,
        "missedFillRate": missed_fills / order_opportunities if order_opportunities else 0,
        "rejectedOpportunities": rejected,
        "orderOpportunityCount": order_opportunities,
        "filledOpportunityCount": filled_opportunities,
        "equityCurve": equity_curve,
        "segments": segment_backtest_performance(trades),
    }


def segment_backtest_performance(trades: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, float]]]:
    dimensions = {
        "confirmedRegime": "regime",
        "strategyFamily": "strategyFamily",
        "strategy": "strategyId",
        "sessionPhase": "sessionPhase",
        "volatilityBucket": "volatilityBucket",
        "transactionCostBucket": "transactionCostBucket",
        "spreadBucket": "spreadBucket",
    }
    segments: dict[str, dict[str, dict[str, float]]] = {name: {} for name in dimensions}
    for output_name, trade_key in dimensions.items():
        buckets: dict[str, dict[str, float]] = defaultdict(lambda: {"tradeCount": 0, "grossPnl": 0.0, "netProfit": 0.0, "totalCosts": 0.0})
        for trade in trades:
            label = str(trade.get(trade_key) or "unknown")
            bucket = buckets[label]
            bucket["tradeCount"] += 1
            bucket["grossPnl"] += float(trade.get("grossPnl") or 0.0)
            bucket["netProfit"] += float(trade.get("netPnl", trade.get("pnl") or 0.0))
            bucket["totalCosts"] += float(trade.get("totalCosts") or 0.0)
        segments[output_name] = {label: dict(values) for label, values in buckets.items()}
    return segments


def _equity_curve(trades: list[dict[str, Any]], starting_capital: float) -> list[dict[str, Any]]:
    equity = float(starting_capital)
    curve = [{"timestamp": None, "equity": equity, "netPnl": 0.0}]
    for trade in sorted(trades, key=lambda item: str(item.get("exitAt") or "")):
        pnl = float(trade.get("netPnl", trade.get("pnl") or 0.0))
        equity += pnl
        curve.append({"timestamp": trade.get("exitAt"), "equity": equity, "netPnl": pnl})
    return curve


def _maximum_drawdown(curve: list[dict[str, Any]]) -> tuple[float, float]:
    peak = -math.inf
    maximum = 0.0
    maximum_pct = 0.0
    for point in curve:
        equity = float(point.get("equity") or 0.0)
        peak = max(peak, equity)
        drawdown = max(0.0, peak - equity)
        maximum = max(maximum, drawdown)
        maximum_pct = max(maximum_pct, drawdown / peak if peak > 0 else 0.0)
    return maximum, maximum_pct


def _sharpe_like(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    stdev = math.sqrt(variance)
    return None if stdev == 0 else mean / stdev * math.sqrt(len(returns))


def _downside_deviation(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    return math.sqrt(sum(value * value for value in returns) / len(returns))


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _execution_status(decision: dict[str, Any]) -> str:
    execution = decision.get("execution") if isinstance(decision.get("execution"), dict) else {}
    return str(execution.get("status") or "")
