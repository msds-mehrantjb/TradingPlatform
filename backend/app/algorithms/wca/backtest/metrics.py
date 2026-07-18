"""WCA backtest diagnostics and reporting metrics."""

from __future__ import annotations

from math import sqrt
from statistics import mean, pstdev
from typing import Any, Iterable

from backend.app.algorithms.wca.contracts import BacktestResult, BacktestTrade, WcaCandle, WcaDecision, WcaSide


def build_wca_backtest_diagnostics(
    result: BacktestResult,
    candles: tuple[WcaCandle, ...],
    equity_curve: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Build aggregate, breakdown, and rejected-signal diagnostics.

    Diagnostics are reporting-only. They may inspect future candles after the
    run has completed for counterfactual rejected-signal analysis, but they do
    not feed back into decisions, weights, settings, or fills.
    """

    decision_by_id = {decision.decision_id: decision for decision in result.decisions}
    trades = result.trades
    aggregate = _aggregate_metrics(result, trades, equity_curve)
    breakdowns = _breakdowns(trades, decision_by_id, result.decisions)
    counterfactuals = _counterfactuals(result.decisions, candles)
    return {
        "schemaVersion": "wca_backtest_diagnostics_v1",
        "aggregate": aggregate,
        "breakdowns": breakdowns,
        "counterfactuals": counterfactuals,
        "lineage": {
            "tradeDecisionLinks": tuple({"tradeId": trade.trade_id, "decisionId": trade.decision_id, "linked": trade.decision_id in decision_by_id} for trade in trades),
            "decisionsIncludeStrategyContributions": all(bool(decision.aggregation.strategy_contributions) or decision.aggregation.active_strategy_count == 0 for decision in result.decisions),
            "decisionsIncludeSettings": all(decision.effective_settings is not None for decision in result.decisions),
            "rejectedSignalsCountedAsExecutedTrades": False,
        },
    }


def _aggregate_metrics(result: BacktestResult, trades: tuple[BacktestTrade, ...], equity_curve: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    pnls = [trade.pnl for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    costs = _total_estimated_costs(result)
    gross_before_costs = result.total_pnl + costs
    closed_dd = _drawdown([point["closedEquity"] for point in equity_curve], result.run_configuration.starting_equity)
    mtm_dd = _drawdown([point["markToMarketEquity"] for point in equity_curve], result.run_configuration.starting_equity)
    returns = _trade_returns(trades, result.run_configuration.starting_equity)
    downside = [min(0.0, value) for value in returns]
    holding_minutes = [_holding_minutes(trade) for trade in trades if trade.exit_at is not None]
    losses_in_a_row = _max_consecutive_losses(pnls)
    exposure_minutes = sum(holding_minutes)
    run_minutes = max(1.0, (result.run_configuration.end - result.run_configuration.start).total_seconds() / 60.0)
    turnover = sum(trade.quantity * trade.entry_price for trade in trades)
    return {
        "netProfit": round(result.total_pnl, 10),
        "grossProfit": round(gross_profit, 10),
        "grossLoss": round(gross_loss, 10),
        "grossProfitBeforeEstimatedCosts": round(max(0.0, gross_before_costs), 10),
        "netAfterCosts": round(result.total_pnl, 10),
        "profitFactor": round(gross_profit / abs(gross_loss), 10) if gross_loss else None,
        "expectancy": round(mean(pnls), 10) if pnls else 0.0,
        "averageR": _average_r(result, trades),
        "winRate": round(len(wins) / len(trades), 10) if trades else 0.0,
        "lossRate": round(len(losses) / len(trades), 10) if trades else 0.0,
        "maximumClosedEquityDrawdown": round(closed_dd, 10),
        "maximumMarkToMarketDrawdown": round(mtm_dd, 10),
        "sharpeLike": _sharpe_like(returns),
        "downsideDeviation": round(sqrt(mean([value * value for value in downside])), 10) if downside else 0.0,
        "turnover": round(turnover, 10),
        "totalEstimatedCosts": round(costs, 10),
        "exposureTimePercent": round(exposure_minutes / run_minutes, 10),
        "capitalUtilization": round((turnover / max(result.run_configuration.starting_equity, 1.0)), 10),
        "averageHoldingTimeMinutes": round(mean(holding_minutes), 10) if holding_minutes else 0.0,
        "maximumConsecutiveLosses": losses_in_a_row,
        "dailyLossGateActivations": sum(1 for decision in result.decisions for gate in decision.local_gates if gate.gate_id == "wca_daily_loss_allocation" and gate.status == "FAIL"),
        "globalGateRejections": sum(1 for decision in result.decisions if decision.global_gate_result is not None and decision.global_gate_result.status == "FAIL"),
        "executedTrades": len(trades),
        "decisionCount": len(result.decisions),
        "drawdownIncludesOpenPositions": True,
    }


def _breakdowns(
    trades: tuple[BacktestTrade, ...],
    decision_by_id: dict[str, WcaDecision],
    decisions: tuple[WcaDecision, ...],
) -> dict[str, Any]:
    executed = [(trade, decision_by_id[trade.decision_id]) for trade in trades if trade.decision_id in decision_by_id]
    return {
        "byStrategy": _strategy_breakdown(executed),
        "byStrategyFamily": _family_breakdown(executed),
        "bySide": _trade_group(executed, lambda trade, _: _side_value(trade.side)),
        "byMarketTrendStatus": _trade_group(executed, lambda _, decision: _value(decision.market_status.trend)),
        "byVolatilityStatus": _trade_group(executed, lambda _, decision: _value(decision.market_status.volatility)),
        "byLiquidityStatus": _trade_group(executed, lambda _, decision: _value(decision.market_status.liquidity)),
        "bySessionPhase": _trade_group(executed, lambda _, decision: _value(decision.market_status.session)),
        "byConfidenceBand": _trade_group(executed, lambda _, decision: _band(_winner_confidence(decision), (0.4, 0.55, 0.7, 0.85))),
        "byScoreBand": _trade_group(executed, lambda _, decision: _band(abs(decision.aggregation.normalized_net_score), (0.35, 0.5, 0.65, 0.8))),
        "byScoreEdgeBand": _trade_group(executed, lambda _, decision: _band(decision.aggregation.winner_edge, (0.05, 0.1, 0.2, 0.35))),
        "byAgreementBand": _trade_group(executed, lambda _, decision: _band(_winner_agreement(decision), (0.5, 0.6, 0.75, 0.9))),
        "byDynamicProfile": _trade_group(executed, lambda _, decision: decision.effective_settings.profile_id if decision.effective_settings else "missing_settings"),
        "byActiveOverlay": _overlay_breakdown(executed),
        "byExitReason": _plain_trade_group(trades, lambda trade: trade.exit_reason or "open"),
        "byEntryRejectionReason": _entry_rejection_breakdown(decisions),
    }


def _strategy_breakdown(executed: list[tuple[BacktestTrade, WcaDecision]]) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    for trade, decision in executed:
        total_weight = sum(abs(row.score_contribution) for row in decision.aggregation.strategy_contributions) or 1.0
        for contribution in decision.aggregation.strategy_contributions:
            weight = abs(contribution.score_contribution) / total_weight
            row = rows.setdefault(contribution.strategy_id, {"tradeCount": 0, "signalCount": 0, "allocatedNetProfit": 0.0, "family": contribution.family})
            row["tradeCount"] += 1
            row["signalCount"] += 1
            row["allocatedNetProfit"] += trade.pnl * weight
    return _rounded_rows(rows)


def _family_breakdown(executed: list[tuple[BacktestTrade, WcaDecision]]) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    for trade, decision in executed:
        total_weight = sum(row.directional_weight for row in decision.aggregation.family_contributions) or 1.0
        for contribution in decision.aggregation.family_contributions:
            weight = contribution.directional_weight / total_weight
            row = rows.setdefault(contribution.family, {"tradeCount": 0, "allocatedNetProfit": 0.0})
            row["tradeCount"] += 1
            row["allocatedNetProfit"] += trade.pnl * weight
    return _rounded_rows(rows)


def _trade_group(executed: list[tuple[BacktestTrade, WcaDecision]], key_fn) -> dict[str, Any]:
    grouped: dict[str, list[float]] = {}
    for trade, decision in executed:
        grouped.setdefault(str(key_fn(trade, decision)), []).append(trade.pnl)
    return {key: _pnl_summary(values) for key, values in sorted(grouped.items())}


def _plain_trade_group(trades: Iterable[BacktestTrade], key_fn) -> dict[str, Any]:
    grouped: dict[str, list[float]] = {}
    for trade in trades:
        grouped.setdefault(str(key_fn(trade)), []).append(trade.pnl)
    return {key: _pnl_summary(values) for key, values in sorted(grouped.items())}


def _overlay_breakdown(executed: list[tuple[BacktestTrade, WcaDecision]]) -> dict[str, Any]:
    rows: dict[str, list[float]] = {}
    for trade, decision in executed:
        overlays = decision.effective_settings.active_overlays if decision.effective_settings else ("missing_settings",)
        for overlay in overlays:
            rows.setdefault(overlay, []).append(trade.pnl)
    return {key: _pnl_summary(values) for key, values in sorted(rows.items())}


def _entry_rejection_breakdown(decisions: tuple[WcaDecision, ...]) -> dict[str, Any]:
    rows: dict[str, int] = {}
    for decision in decisions:
        for reason in _local_rejection_reasons(decision) + _global_rejection_reasons(decision):
            rows[reason] = rows.get(reason, 0) + 1
    return dict(sorted(rows.items()))


def _counterfactuals(decisions: tuple[WcaDecision, ...], candles: tuple[WcaCandle, ...], horizon: int = 5) -> dict[str, Any]:
    candle_by_timestamp = {candle.timestamp: index for index, candle in enumerate(candles)}
    local = []
    global_rejected = []
    for decision in decisions:
        pre_gate = _side_value(decision.aggregation.pre_gate_decision)
        post_gate = _side_value(decision.aggregation.post_local_gate_decision)
        if pre_gate in {WcaSide.BUY.value, WcaSide.SELL.value} and post_gate == WcaSide.HOLD.value:
            local.append(_counterfactual_row(decision, pre_gate, _local_rejection_reasons(decision), candle_by_timestamp, candles, horizon))
        if decision.global_gate_result is not None and decision.global_gate_result.status == "FAIL" and decision.proposed_order is not None:
            side = _side_value(decision.proposed_order.side)
            global_rejected.append(_counterfactual_row(decision, side, _global_rejection_reasons(decision), candle_by_timestamp, candles, horizon))
    return {
        "locallyRejectedSignals": tuple(row for row in local if row is not None),
        "globallyRejectedOrders": tuple(row for row in global_rejected if row is not None),
        "rejectedSignalsAreNotExecutedTrades": True,
    }


def _counterfactual_row(
    decision: WcaDecision,
    side: str,
    reasons: tuple[str, ...],
    candle_by_timestamp: dict[Any, int],
    candles: tuple[WcaCandle, ...],
    horizon: int,
) -> dict[str, Any] | None:
    index = candle_by_timestamp.get(decision.data_timestamp)
    if index is None or index + 1 >= len(candles):
        return None
    entry = candles[index + 1].open
    exit_index = min(len(candles) - 1, index + horizon)
    exit_price = candles[exit_index].close
    movement = exit_price - entry if side == WcaSide.BUY.value else entry - exit_price
    return {
        "decisionId": decision.decision_id,
        "side": side,
        "reasonCodes": reasons,
        "entryTimestamp": candles[index + 1].timestamp.isoformat(),
        "exitTimestamp": candles[exit_index].timestamp.isoformat(),
        "observationBars": max(0, exit_index - index),
        "movementPerShare": round(movement, 10),
        "wouldHaveBeenPositive": movement > 0,
    }


def _local_rejection_reasons(decision: WcaDecision) -> tuple[str, ...]:
    if _side_value(decision.aggregation.pre_gate_decision) == WcaSide.HOLD.value or _side_value(decision.aggregation.post_local_gate_decision) != WcaSide.HOLD.value:
        return ()
    return tuple(code for gate in decision.local_gates if gate.status == "FAIL" and gate.blocks_entry for code in gate.reason_codes) or ("wca.local_gate.rejected",)


def _global_rejection_reasons(decision: WcaDecision) -> tuple[str, ...]:
    gate = decision.global_gate_result
    if gate is None or gate.status != "FAIL":
        return ()
    return gate.reason_codes or ("wca.global_gate.rejected",)


def _pnl_summary(values: list[float]) -> dict[str, Any]:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    return {
        "count": len(values),
        "netProfit": round(sum(values), 10),
        "grossProfit": round(sum(wins), 10),
        "grossLoss": round(sum(losses), 10),
        "winRate": round(len(wins) / len(values), 10) if values else 0.0,
    }


def _rounded_rows(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, row in sorted(rows.items()):
        output[key] = {name: round(value, 10) if isinstance(value, float) else value for name, value in row.items()}
    return output


def _total_estimated_costs(result: BacktestResult) -> float:
    config = result.run_configuration
    total = 0.0
    for trade in result.trades:
        if trade.exit_price is None:
            continue
        slippage_and_fees = trade.quantity * (config.slippage_per_share + config.fee_per_share) * 2
        impact = trade.quantity * (trade.entry_price + trade.exit_price) * (config.market_impact_bps / 10000.0)
        spread = trade.quantity * trade.entry_price * (config.spread_bps / 10000.0)
        total += slippage_and_fees + impact + spread
    return total


def _average_r(result: BacktestResult, trades: tuple[BacktestTrade, ...]) -> float:
    values = []
    for trade in trades:
        decision = next((row for row in result.decisions if row.decision_id == trade.decision_id), None)
        risk = (decision.sizing.stop_risk_dollars if decision else 0) or 0
        if risk > 0:
            values.append(trade.pnl / risk)
    return round(mean(values), 10) if values else 0.0


def _trade_returns(trades: tuple[BacktestTrade, ...], equity: float) -> list[float]:
    return [trade.pnl / max(equity, 1.0) for trade in trades]


def _drawdown(values: list[float], starting_equity: float) -> float:
    peak = starting_equity
    drawdown = 0.0
    for value in values or [starting_equity]:
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
    return drawdown


def _sharpe_like(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    deviation = pstdev(returns)
    return round(mean(returns) / deviation, 10) if deviation > 0 else 0.0


def _holding_minutes(trade: BacktestTrade) -> float:
    if trade.exit_at is None:
        return 0.0
    return max(0.0, (trade.exit_at - trade.entry_at).total_seconds() / 60.0)


def _max_consecutive_losses(pnls: list[float]) -> int:
    best = 0
    current = 0
    for pnl in pnls:
        if pnl < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _winner_confidence(decision: WcaDecision) -> float:
    side = _side_value(decision.aggregation.post_local_gate_decision)
    if side == WcaSide.BUY.value:
        return decision.aggregation.buy_average_confidence
    if side == WcaSide.SELL.value:
        return decision.aggregation.sell_average_confidence
    return max(decision.aggregation.buy_average_confidence, decision.aggregation.sell_average_confidence)


def _winner_agreement(decision: WcaDecision) -> float:
    side = _side_value(decision.aggregation.post_local_gate_decision)
    if side == WcaSide.BUY.value:
        return decision.aggregation.buy_agreement
    if side == WcaSide.SELL.value:
        return decision.aggregation.sell_agreement
    return max(decision.aggregation.buy_agreement, decision.aggregation.sell_agreement)


def _band(value: float, thresholds: tuple[float, ...]) -> str:
    previous = 0.0
    for threshold in thresholds:
        if value < threshold:
            return f"{previous:.2f}-{threshold:.2f}"
        previous = threshold
    return f"{thresholds[-1]:.2f}+"


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _side_value(side: WcaSide | str) -> str:
    return side.value if isinstance(side, WcaSide) else str(side)


__all__ = ["BacktestResult", "build_wca_backtest_diagnostics"]
