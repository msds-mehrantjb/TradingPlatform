"""Legacy-compatible WCA backend engine.

Step 2 preserves the current frontend WCA behavior. The engine therefore
accepts the immutable strategy-signal snapshot frozen in Step 0 and applies the
same aggregation, hard-filter, and sizing rules without adding new strategy
logic or ML dependencies.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from backend.app.algorithms.wca import WCA_PACKAGE_VERSION
from backend.app.algorithms.wca.configuration import WCA_CONFIGURATION_VERSION
from backend.app.algorithms.wca.contracts import (
    ProposedOrder,
    WcaAggregationResult,
    WcaDecision,
    WcaEvaluateRequest,
    WcaEvaluateResponse,
    WcaEvaluationStatus,
    WcaGateStatus,
    WcaLegacyHardFilter,
    WcaLegacySizingResult,
    WcaLocalGateResult,
    WcaMarketSnapshot,
    WcaMarketStatus,
    WcaSide,
    WcaSizingInputs,
    WcaSizingResult,
    WcaStrategyEvaluation,
)
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY


WCA_ENGINE_VERSION = "wca_legacy_compatible_v1"


class WcaEngineInputError(ValueError):
    pass


def evaluate_wca_legacy(request: WcaEvaluateRequest) -> WcaEvaluateResponse:
    if not request.strategy_signals:
        raise WcaEngineInputError("WCA Step 2 requires immutable legacy strategySignals for parity evaluation.")
    sizing_inputs = request.sizing_inputs or sizing_inputs_from_market_snapshot(request)
    strategies = tuple(request.strategy_signals)

    buy_score = round4(sum(row.effective_weight * row.confidence for row in strategies if normalized_signal(row.signal) == "buy"))
    sell_score = round4(sum(row.effective_weight * row.confidence for row in strategies if normalized_signal(row.signal) == "sell"))
    active_weight = round4(sum(row.effective_weight for row in strategies if normalized_signal(row.signal) != "hold"))
    active_strategy_count = sum(1 for row in strategies if normalized_signal(row.signal) != "hold")
    buy_weight = round4(sum(row.effective_weight for row in strategies if normalized_signal(row.signal) == "buy"))
    sell_weight = round4(sum(row.effective_weight for row in strategies if normalized_signal(row.signal) == "sell"))
    net_score = round4(buy_score - sell_score)
    normalized_net_score = round4(net_score / active_weight) if active_weight else 0
    buy_agreement = round4(buy_weight / active_weight) if active_weight else 0
    sell_agreement = round4(sell_weight / active_weight) if active_weight else 0
    buy_average_confidence = round4(buy_score / buy_weight) if buy_weight else 0
    sell_average_confidence = round4(sell_score / sell_weight) if sell_weight else 0

    settings = request.decision_settings
    enough_active = active_strategy_count >= settings.minimum_active_strategies
    buy_requirements_met = (
        enough_active
        and buy_agreement >= settings.minimum_directional_agreement
        and buy_average_confidence >= settings.minimum_average_confidence
    )
    sell_requirements_met = (
        enough_active
        and sell_agreement >= settings.minimum_directional_agreement
        and sell_average_confidence >= settings.minimum_average_confidence
    )
    if normalized_net_score >= settings.strong_buy_threshold and buy_requirements_met:
        raw_decision = "Strong Buy"
    elif normalized_net_score >= settings.buy_threshold and buy_requirements_met:
        raw_decision = "Buy"
    elif normalized_net_score <= settings.strong_sell_threshold and sell_requirements_met:
        raw_decision = "Strong Sell"
    elif normalized_net_score <= settings.sell_threshold and sell_requirements_met:
        raw_decision = "Sell"
    else:
        raw_decision = "Hold"

    raw_signal = raw_decision_to_signal(raw_decision)
    hard_filter_blocked = any(row.status == "fail" for row in request.hard_filters)
    effective_decision = "Hold" if hard_filter_blocked else raw_decision
    signal = "Hold" if hard_filter_blocked else raw_signal
    sizing = legacy_position_sizing(sizing_inputs, signal, normalized_net_score)
    proposed_order = proposed_order_from_result(request, signal, sizing)
    canonical_decision = canonical_decision_from_legacy(
        request=request,
        strategies=strategies,
        buy_score=buy_score,
        sell_score=sell_score,
        net_score=net_score,
        active_weight=active_weight,
        normalized_net_score=normalized_net_score,
        active_strategy_count=active_strategy_count,
        buy_agreement=buy_agreement,
        sell_agreement=sell_agreement,
        buy_average_confidence=buy_average_confidence,
        sell_average_confidence=sell_average_confidence,
        effective_decision=effective_decision,
        signal=signal,
        sizing=sizing,
        proposed_order=proposed_order,
    )

    return WcaEvaluateResponse(
        configurationVersion=WCA_CONFIGURATION_VERSION,
        engineVersion=WCA_ENGINE_VERSION,
        baseWeights={row.strategy: row.base_weight for row in strategies},
        effectiveWeights={row.strategy: row.effective_weight for row in strategies},
        strategyEvaluations=strategies,
        buyScore=buy_score,
        sellScore=sell_score,
        netScore=net_score,
        activeWeight=active_weight,
        normalizedNetScore=normalized_net_score,
        activeStrategyCount=active_strategy_count,
        buyWeight=buy_weight,
        sellWeight=sell_weight,
        buyAgreement=buy_agreement,
        sellAgreement=sell_agreement,
        buyAverageConfidence=buy_average_confidence,
        sellAverageConfidence=sell_average_confidence,
        rawDecision=raw_decision,
        rawSignal=raw_signal,
        localGateResult=request.hard_filters,
        effectiveDecision=effective_decision,
        signal=signal,
        sizingResult=sizing,
        proposedOrder=proposed_order,
        reasonCodes=reason_codes_from_filters(request.hard_filters, hard_filter_blocked),
        decision=canonical_decision,
    )


def legacy_position_sizing(inputs: WcaSizingInputs, signal: str, normalized_net_score: float) -> WcaLegacySizingResult:
    signal_strength = abs(normalized_net_score)
    size_multiplier = confidence_size_multiplier(signal_strength)
    account_equity = inputs.account_equity
    price = max(inputs.price, 0.01)
    risk_dollars = account_equity * (inputs.base_risk_percent / 100) * size_multiplier
    stop_distance = max(inputs.atr * inputs.atr_stop_multiplier, price * (inputs.minimum_stop_distance_percent / 100))
    shares_by_risk = risk_dollars / stop_distance if stop_distance > 0 else 0
    shares_by_order = (account_equity * (inputs.order_allocation_percent / 100)) / price
    shares_by_capital = (account_equity * (inputs.max_position_percent / 100)) / price
    max_position_dollars = account_equity * (inputs.max_position_percent / 100)
    daily_buying_power_dollars = account_equity * (inputs.daily_allocation_percent / 100)
    available_buying_power = max(0, min(max_position_dollars, daily_buying_power_dollars) - inputs.current_position_value)
    shares_by_buying_power = available_buying_power / price
    shares_by_liquidity = inputs.latest_volume * (inputs.max_participation_percent / 100)
    shares_by_max = inputs.max_allowed_shares if inputs.max_allowed_shares > 0 else math.inf
    caps = (
        ("risk budget", shares_by_risk),
        ("order limit", shares_by_order),
        ("max position", shares_by_capital),
        ("buying power", shares_by_buying_power),
        ("liquidity participation", shares_by_liquidity),
        ("max shares", shares_by_max),
    )
    limiting_factor, limiting_shares = min(caps, key=lambda item: item[1])
    raw_quantity = min(value for _, value in caps)
    final_quantity = (
        0
        if signal == "Hold" or size_multiplier <= 0 or stop_distance <= 0
        else max(0, math.floor(raw_quantity if math.isfinite(raw_quantity) else 0))
    )
    if signal == "Hold":
        blocked_reason = "final signal is Hold"
    elif size_multiplier <= 0:
        blocked_reason = f"signal strength {signal_strength:.4f} is below 50%"
    elif final_quantity < 1:
        blocked_reason = f"{limiting_factor} allows {limiting_shares:.2f} shares, below 1 share"
    else:
        blocked_reason = ""
    return WcaLegacySizingResult(
        signalStrength=round4(signal_strength),
        sizeMultiplier=size_multiplier,
        riskDollars=round(risk_dollars, 2),
        stopDistance=round4(stop_distance),
        sharesByRisk=round4(shares_by_risk),
        sharesByOrder=round4(shares_by_order),
        sharesByCapital=round4(shares_by_capital),
        sharesByBuyingPower=round4(shares_by_buying_power),
        sharesByLiquidity=round4(shares_by_liquidity),
        finalQuantity=final_quantity,
        availableBuyingPower=round(available_buying_power, 2),
        accountEquity=account_equity,
        maxPositionDollars=round(max_position_dollars, 2),
        currentPositionValue=round(inputs.current_position_value, 2),
        limitingFactor=limiting_factor,
        blockedReason=blocked_reason,
    )


def confidence_size_multiplier(signal_strength: float) -> float:
    if signal_strength >= 0.8:
        return 1
    if signal_strength >= 0.7:
        return 0.75
    if signal_strength >= 0.6:
        return 0.5
    if signal_strength >= 0.5:
        return 0.25
    return 0


def sizing_inputs_from_market_snapshot(request: WcaEvaluateRequest) -> WcaSizingInputs:
    market = request.market_snapshot or {}
    price = float(market.get("close") or 0)
    if price <= 0:
        raise WcaEngineInputError("WCA evaluation requires sizingInputs or marketSnapshot.close.")
    return WcaSizingInputs(
        price=price,
        accountEquity=request.trading_settings.starting_capital,
        baseRiskPercent=request.trading_settings.base_risk_percent,
        orderAllocationPercent=request.trading_settings.order_allocation_percent,
        dailyAllocationPercent=request.trading_settings.daily_allocation_percent,
        maxPositionPercent=request.trading_settings.max_position_percent,
        atr=float(market.get("atr") or 0),
        atrStopMultiplier=request.trading_settings.atr_stop_multiplier,
        minimumStopDistancePercent=request.trading_settings.minimum_stop_distance_percent,
        latestVolume=float(market.get("latestVolume") or market.get("latest_volume") or 0),
        maxParticipationPercent=request.trading_settings.max_participation_percent,
        maxAllowedShares=request.trading_settings.max_allowed_shares,
    )


def proposed_order_from_result(request: WcaEvaluateRequest, signal: str, sizing: WcaLegacySizingResult) -> ProposedOrder | None:
    if signal == "Hold" or sizing.final_quantity <= 0:
        return None
    market = request.market_snapshot or {}
    price = float(market.get("close") or 0) or None
    side = WcaSide.BUY if signal == "Buy" else WcaSide.SELL
    snapshot_id = request.snapshot_id or "adhoc"
    return ProposedOrder(
        decision_id=f"wca-{snapshot_id}",
        order_intent_id=f"wca-intent-{snapshot_id}",
        symbol=request.symbol,
        side=side,
        quantity=sizing.final_quantity,
        limit_price=price,
        reason_codes=("wca.shadow.proposal_only",),
    )


def canonical_decision_from_legacy(
    *,
    request: WcaEvaluateRequest,
    strategies: tuple,
    buy_score: float,
    sell_score: float,
    net_score: float,
    active_weight: float,
    normalized_net_score: float,
    active_strategy_count: int,
    buy_agreement: float,
    sell_agreement: float,
    buy_average_confidence: float,
    sell_average_confidence: float,
    effective_decision: str,
    signal: str,
    sizing: WcaLegacySizingResult,
    proposed_order: ProposedOrder | None,
) -> WcaDecision:
    timestamp = request.timestamp or datetime.now(timezone.utc)
    side = WcaSide.BUY if signal == "Buy" else WcaSide.SELL if signal == "Sell" else WcaSide.HOLD
    strategy_rows = tuple(
        WcaStrategyEvaluation(
            strategy_id=row.key,
            name=row.name,
            status=WcaEvaluationStatus.ACTIVE if normalized_signal(row.signal) != "hold" else WcaEvaluationStatus.NOT_APPLICABLE,
            signal=WcaSide.BUY if normalized_signal(row.signal) == "buy" else WcaSide.SELL if normalized_signal(row.signal) == "sell" else WcaSide.HOLD,
            confidence=row.confidence,
            base_weight=row.base_weight,
            effective_weight=row.effective_weight,
            contribution=round4(row.direction * row.effective_weight * row.confidence),
            reason_codes=(f"wca.strategy.{row.strategy}",),
            explanation=row.reason,
        )
        for row in strategies
    )
    aggregation = WcaAggregationResult(
        signal=side,
        decision_label=effective_decision,
        buy_score=buy_score,
        sell_score=sell_score,
        net_score=net_score,
        active_weight=active_weight,
        normalized_net_score=normalized_net_score,
        active_strategy_count=active_strategy_count,
        buy_agreement=buy_agreement,
        sell_agreement=sell_agreement,
        buy_average_confidence=buy_average_confidence,
        sell_average_confidence=sell_average_confidence,
        strategy_evaluations=strategy_rows,
        reason_codes=("wca.legacy_aggregation",),
    )
    local_gates = tuple(
        WcaLocalGateResult(
            gate_id=filter_row.label.lower().replace(" ", "_"),
            status=WcaGateStatus.FAIL if filter_row.status == "fail" else WcaGateStatus.INFO if filter_row.status == "info" else WcaGateStatus.PASS,
            blocks_entry=filter_row.status == "fail",
            reason_codes=(f"wca.gate.{filter_row.label.lower().replace(' ', '_')}.{filter_row.status}",),
            explanation=filter_row.detail,
        )
        for filter_row in request.hard_filters
    )
    market = request.market_snapshot or {}
    close = max(float(market.get("close") or 1), 0.01)
    market_snapshot = WcaMarketSnapshot(
        symbol=request.symbol,
        data_timestamp=timestamp,
        decision_timestamp=timestamp,
        candles=(
            {
                "timestamp": timestamp,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": float(market.get("latestVolume") or 0),
            },
        ),
        source="wca_legacy_strategy_snapshot",
        reason_codes=("wca.step2.legacy_snapshot",),
    )
    return WcaDecision(
        decision_id=f"wca-{request.snapshot_id or timestamp.isoformat()}",
        configuration_version=WCA_CONFIGURATION_VERSION,
        weight_version=f"{WCA_PACKAGE_VERSION}-legacy-base-weights",
        data_timestamp=timestamp,
        decision_timestamp=timestamp,
        market_snapshot=market_snapshot,
        market_status=WcaMarketStatus(status=WcaEvaluationStatus.ACTIVE),
        aggregation=aggregation,
        local_gates=local_gates,
        sizing=WcaSizingResult(
            final_quantity=sizing.final_quantity,
            risk_dollars=sizing.risk_dollars,
            stop_distance=sizing.stop_distance,
            shares_by_risk=sizing.shares_by_risk,
            shares_by_order=sizing.shares_by_order,
            shares_by_capital=sizing.shares_by_capital,
            shares_by_buying_power=sizing.shares_by_buying_power,
            shares_by_liquidity=sizing.shares_by_liquidity,
            limiting_factor=sizing.limiting_factor,
            blocked_reason=sizing.blocked_reason,
        ),
        proposed_order=proposed_order,
        reason_codes=("wca.step2.legacy_compatible",),
    )


def reason_codes_from_filters(filters: tuple[WcaLegacyHardFilter, ...], blocked: bool) -> tuple[str, ...]:
    codes = ["wca.step2.legacy_compatible"]
    if blocked:
        codes.append("wca.local_gate.blocked")
    codes.extend(f"wca.local_gate.{row.label.lower().replace(' ', '_')}.{row.status}" for row in filters if row.status == "fail")
    return tuple(codes)


def raw_decision_to_signal(label: str) -> str:
    if label in {"Strong Buy", "Buy"}:
        return "Buy"
    if label in {"Strong Sell", "Sell"}:
        return "Sell"
    return "Hold"


def normalized_signal(signal: str) -> str:
    lowered = signal.lower()
    if lowered in {"buy", "sell"}:
        return lowered
    return "hold"


def round4(value: float) -> float:
    return round(value, 4)


def base_weight_map() -> dict[str, float]:
    return {row.slug: row.base_weight for row in WCA_STRATEGY_REGISTRY}
