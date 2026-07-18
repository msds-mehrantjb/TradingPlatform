"""Position sizing for Weighted Voting order proposals."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor

from backend.app.algorithms.weighted_voting.decision_gates import WeightedVotingGatePipelineResult, all_gates_pass
from backend.app.algorithms.weighted_voting.models import (
    WeightedDecision,
    WeightedEffectiveSettings,
    WeightedGateResult,
    WeightedMarketSnapshot,
    WeightedSide,
)
from backend.app.algorithms.weighted_voting.risk_budget import WeightedVotingRiskBudget


WEIGHTED_VOTING_POSITION_SIZING_VERSION = "weighted_voting_position_sizing_v2"


@dataclass(frozen=True)
class WeightedVotingPositionSize:
    quantity: int
    blocked_reason: str = ""


@dataclass(frozen=True)
class WeightedVotingSizingContext:
    decision: WeightedDecision
    effective_settings: WeightedEffectiveSettings
    market_snapshot: WeightedMarketSnapshot
    account_equity: float
    available_buying_power: float
    remaining_weighted_daily_risk: float
    remaining_weighted_capital_partition: float
    global_available_risk: float
    global_max_shares: int
    structural_invalidation_price: float | None
    atr: float | None
    slippage_per_share: float
    current_one_minute_volume: float
    average_one_minute_volume: float
    use_average_volume_for_participation: bool = True
    market_quality_multiplier: float = 1.0
    voting_quality_multiplier: float = 1.0
    volatility_multiplier: float = 1.0
    daily_performance_multiplier: float = 1.0
    drawdown_multiplier: float = 1.0
    risk_budget: WeightedVotingRiskBudget | None = None
    local_gate_result: WeightedVotingGatePipelineResult | None = None
    gate_results: tuple[WeightedGateResult, ...] = ()


@dataclass(frozen=True)
class WeightedVotingSizingCap:
    cap_id: str
    quantity: int
    reason_codes: tuple[str, ...]
    explanation: str


@dataclass(frozen=True)
class WeightedVotingSizingResult:
    quantity: int
    limiting_cap: str
    caps: tuple[WeightedVotingSizingCap, ...]
    effective_risk_dollars: float
    stop_distance: float
    structural_stop_distance: float | None
    atr_stop_distance: float | None
    minimum_price_stop_distance: float
    spread_safety_buffer: float
    actual_bid: float | None
    actual_ask: float | None
    actual_spread: float | None
    slippage_per_share: float
    current_one_minute_volume: float
    average_one_minute_volume: float
    reason_codes: tuple[str, ...]
    explanation: str
    requested_quantity: int = 0
    approved_local_quantity: int = 0
    risk_dollars: float = 0.0
    size_multiplier: float = 1.0
    limiting_factor: str = ""
    risk_based_quantity: int = 0
    capital_partition_quantity: int = 0
    buying_power_quantity: int = 0
    liquidity_quantity: int = 0
    volume_participation_quantity: int = 0
    algorithm_maximum_quantity: int = 0
    global_maximum_quantity: int = 0


def size_by_risk(budget: WeightedVotingRiskBudget, stop_distance: float) -> WeightedVotingPositionSize:
    if stop_distance <= 0:
        return WeightedVotingPositionSize(quantity=0, blocked_reason="stop distance must be positive")
    return WeightedVotingPositionSize(quantity=max(0, floor(budget.risk_dollars / stop_distance)))


def calculate_weighted_voting_position_size(context: WeightedVotingSizingContext) -> WeightedVotingSizingResult:
    reason_codes: list[str] = []
    side = context.decision.proposed_side
    entry_price = _entry_price(context.market_snapshot, side)
    actual_spread = _actual_spread(context.market_snapshot)
    if not context.decision.eligible or side not in (WeightedSide.BUY.value, WeightedSide.SELL.value):
        reason_codes.append("weighted_voting.sizing.failed_decision")
        return _zero_result(context, "decision", actual_spread, reason_codes, "Failed or non-directional decisions always produce zero quantity.")
    if context.decision.vote_scores.winner_score < context.effective_settings.minimum_score:
        reason_codes.append("weighted_voting.sizing.minimum_score_not_met")
        return _zero_result(context, "minimum_score", actual_spread, reason_codes, "Winner score is below effective minimum score.")
    if context.decision.vote_scores.winner_edge < context.effective_settings.minimum_edge:
        reason_codes.append("weighted_voting.sizing.minimum_edge_not_met")
        return _zero_result(context, "minimum_edge", actual_spread, reason_codes, "Winner edge is below effective minimum edge.")
    if _mandatory_gate_failed(context):
        reason_codes.append("weighted_voting.sizing.local_gate_failed")
        return _zero_result(context, "local_gates", actual_spread, reason_codes, "A mandatory local gate failed.")
    if entry_price is None or actual_spread is None:
        reason_codes.append("weighted_voting.sizing.missing_actual_quote")
        return _zero_result(context, "actual_quote", actual_spread, reason_codes, "Actual bid and ask are required; spread is never manufactured from slippage.")

    stop_components = _stop_components(context, entry_price, actual_spread)
    stop_distance = max(value for value in stop_components if value is not None)
    if stop_distance <= 0:
        reason_codes.append("weighted_voting.sizing.invalid_stop_distance")
        return _zero_result(context, "stop_distance", actual_spread, reason_codes, "Stop distance must be positive.")
    size_multiplier = _size_multiplier(context)
    effective_risk_dollars = _effective_risk_dollars(context, size_multiplier)
    risk_based_quantity = floor(effective_risk_dollars / stop_distance)
    capital_partition_quantity = floor(_remaining_capital_partition(context) / entry_price)
    buying_power_quantity = floor(context.available_buying_power / entry_price)
    liquidity_quantity = _liquidity_quantity(context)
    volume_participation_quantity = _participation_quantity(context)
    algorithm_maximum_quantity = context.effective_settings.maximum_shares if context.effective_settings.maximum_shares > 0 else 2_147_483_647
    global_maximum_quantity = min(context.global_max_shares, floor(context.global_available_risk / stop_distance) if stop_distance > 0 else 0)
    caps = (
        _cap("risk", risk_based_quantity, "weighted_voting.sizing.cap.risk", "Shares capped by effective risk dollars and stop distance."),
        _cap("order_allocation", floor((context.account_equity * (context.effective_settings.order_allocation_percent / 100.0)) / entry_price), "weighted_voting.sizing.cap.order_allocation", "Shares capped by order allocation."),
        _cap("maximum_position", floor((context.account_equity * (context.effective_settings.maximum_position_percent / 100.0)) / entry_price), "weighted_voting.sizing.cap.maximum_position", "Shares capped by maximum position size."),
        _cap("available_buying_power", buying_power_quantity, "weighted_voting.sizing.cap.available_buying_power", "Shares capped by available buying power."),
        _cap("liquidity_participation", volume_participation_quantity, "weighted_voting.sizing.cap.liquidity_participation", "Shares capped by liquidity participation policy."),
        _cap("maximum_shares", algorithm_maximum_quantity, "weighted_voting.sizing.cap.maximum_shares", "Shares capped by effective maximum shares."),
        _cap("global_gates", global_maximum_quantity, "weighted_voting.sizing.cap.global_gates", "Shares capped by global gate allowance."),
    )
    valid_caps = tuple(cap for cap in caps if cap.quantity >= 0)
    requested_caps = {
        "risk_based_quantity": risk_based_quantity,
        "capital_partition_quantity": capital_partition_quantity,
        "buying_power_quantity": buying_power_quantity,
        "liquidity_quantity": liquidity_quantity,
        "volume_participation_quantity": volume_participation_quantity,
        "algorithm_maximum_quantity": algorithm_maximum_quantity,
        "global_maximum_quantity": global_maximum_quantity,
    }
    limiting_name, limiting_quantity = min(requested_caps.items(), key=lambda item: item[1])
    limiting = _legacy_limiting_cap(limiting_name, valid_caps)
    requested_quantity = max(0, min(requested_caps[name] for name in requested_caps if name != "global_maximum_quantity"))
    approved_local_quantity = requested_quantity
    final_quantity = max(0, min(requested_quantity, global_maximum_quantity))
    result_reasons = tuple(dict.fromkeys(reason_codes + ["weighted_voting.sizing.calculated", f"weighted_voting.sizing.limiting_factor.{limiting_name}", *limiting.reason_codes]))
    return WeightedVotingSizingResult(
        quantity=final_quantity,
        limiting_cap=limiting.cap_id,
        caps=valid_caps,
        effective_risk_dollars=round(effective_risk_dollars, 10),
        stop_distance=round(stop_distance, 10),
        structural_stop_distance=round(stop_components[0], 10) if stop_components[0] is not None else None,
        atr_stop_distance=round(stop_components[1], 10) if stop_components[1] is not None else None,
        minimum_price_stop_distance=round(stop_components[2] or 0.0, 10),
        spread_safety_buffer=round(stop_components[3] or 0.0, 10),
        actual_bid=context.market_snapshot.bid,
        actual_ask=context.market_snapshot.ask,
        actual_spread=round(actual_spread, 10),
        slippage_per_share=round(context.slippage_per_share, 10),
        current_one_minute_volume=context.current_one_minute_volume,
        average_one_minute_volume=context.average_one_minute_volume,
        reason_codes=result_reasons,
        explanation="Final Weighted Voting quantity is the floor of the smallest visible sizing cap.",
        requested_quantity=requested_quantity,
        approved_local_quantity=approved_local_quantity,
        risk_dollars=round(effective_risk_dollars, 10),
        size_multiplier=round(size_multiplier, 10),
        limiting_factor=limiting_name,
        risk_based_quantity=max(0, risk_based_quantity),
        capital_partition_quantity=max(0, capital_partition_quantity),
        buying_power_quantity=max(0, buying_power_quantity),
        liquidity_quantity=max(0, liquidity_quantity),
        volume_participation_quantity=max(0, volume_participation_quantity),
        algorithm_maximum_quantity=max(0, algorithm_maximum_quantity),
        global_maximum_quantity=max(0, global_maximum_quantity),
    )


def _effective_risk_dollars(context: WeightedVotingSizingContext, size_multiplier: float) -> float:
    raw = (
        context.account_equity
        * (context.effective_settings.default_settings.base_risk_per_trade_percent / 100.0)
        * size_multiplier
        * context.market_quality_multiplier
        * context.voting_quality_multiplier
        * context.volatility_multiplier
        * context.daily_performance_multiplier
        * context.drawdown_multiplier
    )
    weighted_risk_budget = context.risk_budget.risk_dollars if context.risk_budget is not None else context.remaining_weighted_daily_risk
    weighted_limit = context.account_equity * (context.effective_settings.base_risk_per_trade_percent / 100.0)
    return max(
        0.0,
        min(
            raw,
            weighted_limit,
            weighted_risk_budget,
        ),
    )


def _remaining_capital_partition(context: WeightedVotingSizingContext) -> float:
    if context.risk_budget is not None:
        return context.risk_budget.remaining_capital_partition
    return context.remaining_weighted_capital_partition


def _size_multiplier(context: WeightedVotingSizingContext) -> float:
    scores = context.decision.vote_scores
    score_factor = max(0.0, min(1.0, scores.winner_score))
    edge_factor = max(0.0, min(1.0, scores.winner_edge / max(context.effective_settings.minimum_edge, 0.000001)))
    coverage = getattr(scores, "effective_weight_coverage", getattr(scores, "active_weight", 1.0))
    coverage_factor = max(0.0, min(1.0, coverage))
    return max(0.0, min(1.0, score_factor * edge_factor * coverage_factor))


def _stop_components(context: WeightedVotingSizingContext, entry_price: float, actual_spread: float) -> tuple[float | None, float | None, float, float]:
    structural = None
    if context.structural_invalidation_price is not None:
        structural = abs(entry_price - context.structural_invalidation_price)
    atr_stop = context.atr * context.effective_settings.atr_stop_multiplier if context.atr is not None else None
    minimum_price = entry_price * context.effective_settings.minimum_stop_distance_percent
    spread_buffer = actual_spread + context.slippage_per_share
    return structural, atr_stop, minimum_price, spread_buffer


def _entry_price(snapshot: WeightedMarketSnapshot, side: str) -> float | None:
    if snapshot.bid is None or snapshot.ask is None:
        return None
    if side == WeightedSide.BUY.value:
        return snapshot.ask
    if side == WeightedSide.SELL.value:
        return snapshot.bid
    return None


def _actual_spread(snapshot: WeightedMarketSnapshot) -> float | None:
    if snapshot.bid is None or snapshot.ask is None:
        return None
    return max(0.0, snapshot.ask - snapshot.bid)


def _participation_quantity(context: WeightedVotingSizingContext) -> int:
    volume = context.average_one_minute_volume if context.use_average_volume_for_participation else context.current_one_minute_volume
    return max(0, floor(volume * context.effective_settings.maximum_participation_rate))


def _liquidity_quantity(context: WeightedVotingSizingContext) -> int:
    return max(0, floor(max(context.current_one_minute_volume, context.average_one_minute_volume)))


def _legacy_limiting_cap(limiting_name: str, caps: tuple[WeightedVotingSizingCap, ...]) -> WeightedVotingSizingCap:
    legacy_map = {
        "risk_based_quantity": "risk",
        "capital_partition_quantity": "order_allocation",
        "buying_power_quantity": "available_buying_power",
        "liquidity_quantity": "liquidity_participation",
        "volume_participation_quantity": "liquidity_participation",
        "algorithm_maximum_quantity": "maximum_shares",
        "global_maximum_quantity": "global_gates",
    }
    legacy_id = legacy_map[limiting_name]
    return next(cap for cap in caps if cap.cap_id == legacy_id)


def _mandatory_gate_failed(context: WeightedVotingSizingContext) -> bool:
    if context.local_gate_result is not None:
        return not context.local_gate_result.permission_granted
    if context.gate_results:
        return not all_gates_pass(context.gate_results)
    if context.decision.gate_results:
        return not all_gates_pass(context.decision.gate_results)
    return False


def _cap(cap_id: str, quantity: int, reason_code: str, explanation: str) -> WeightedVotingSizingCap:
    return WeightedVotingSizingCap(cap_id=cap_id, quantity=max(0, quantity), reason_codes=(reason_code,), explanation=explanation)


def _zero_result(
    context: WeightedVotingSizingContext,
    limiting_cap: str,
    actual_spread: float | None,
    reason_codes: list[str],
    explanation: str,
) -> WeightedVotingSizingResult:
    cap = _cap(limiting_cap, 0, reason_codes[-1], explanation)
    return WeightedVotingSizingResult(
        quantity=0,
        limiting_cap=limiting_cap,
        caps=(cap,),
        effective_risk_dollars=0.0,
        stop_distance=0.0,
        structural_stop_distance=None,
        atr_stop_distance=None,
        minimum_price_stop_distance=0.0,
        spread_safety_buffer=0.0,
        actual_bid=context.market_snapshot.bid,
        actual_ask=context.market_snapshot.ask,
        actual_spread=actual_spread,
        slippage_per_share=context.slippage_per_share,
        current_one_minute_volume=context.current_one_minute_volume,
        average_one_minute_volume=context.average_one_minute_volume,
        reason_codes=tuple(reason_codes),
        explanation=explanation,
        requested_quantity=0,
        approved_local_quantity=0,
        risk_dollars=0.0,
        size_multiplier=0.0,
        limiting_factor=limiting_cap,
    )
