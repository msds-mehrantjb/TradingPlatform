"""Side-effect-free Weighted Voting decision kernel."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any, Callable

from backend.app.algorithms.weighted_voting.aggregation import aggregate_weighted_signals
from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.decision_gates import WeightedFiveMinuteAlignment, WeightedVotingLocalGateInputs, evaluate_local_decision_gates
from backend.app.algorithms.weighted_voting.dynamic_settings import DynamicSettingsResolver
from backend.app.algorithms.weighted_voting.market_condition import classify_market_condition
from backend.app.algorithms.weighted_voting.models import (
    WeightedDecision,
    WeightedEffectiveSettings,
    WeightedMarketCondition,
    WeightedMarketSnapshot,
    WeightedSide,
    WeightedStrategyOutcome,
    WeightedVotingSignal,
)
from backend.app.algorithms.weighted_voting.order_proposal import WeightedVotingOrderProposal, build_weighted_voting_order_proposal
from backend.app.algorithms.weighted_voting.position_sizing import WeightedVotingSizingContext, WeightedVotingSizingResult, calculate_weighted_voting_position_size
from backend.app.algorithms.weighted_voting.runtime_context import WeightedVotingRuntimeContext
from backend.app.algorithms.weighted_voting.signal_engine import evaluate_signals
from backend.app.algorithms.weighted_voting.strategies.common import average_true_range, average_volume


WEIGHTED_VOTING_DECISION_KERNEL_VERSION = "weighted_voting_decision_kernel_v1"
MISSING_RUNTIME_COST_RETURN = 1_000_000.0
SignalEvaluator = Callable[[WeightedMarketSnapshot, WeightedVotingConfig | None, Any, WeightedMarketCondition | None], list[WeightedVotingSignal] | tuple[WeightedVotingSignal, ...]]


@dataclass(frozen=True)
class WeightedVotingDecisionResult:
    kernel_version: str
    context_version: str
    context_manifest_hash: str
    market_snapshot: WeightedMarketSnapshot
    inventory_snapshot_version: int
    market_condition: WeightedMarketCondition
    effective_settings: WeightedEffectiveSettings
    active_weight_version: str
    signals: tuple[WeightedVotingSignal, ...]
    decision: WeightedDecision
    gate_result: Any
    sizing_result: WeightedVotingSizingResult
    order_proposal: WeightedVotingOrderProposal
    observability_record: dict[str, Any]
    reason_codes: tuple[str, ...]
    deterministic_result_hash: str

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


class WeightedVotingDecisionKernel:
    @staticmethod
    def evaluate(
        context: WeightedVotingRuntimeContext,
        *,
        config: WeightedVotingConfig | None = None,
        settings_resolver: DynamicSettingsResolver | None = None,
        historical_outcomes: tuple[WeightedStrategyOutcome, ...] = (),
        signal_evaluator: SignalEvaluator = evaluate_signals,
    ) -> WeightedVotingDecisionResult:
        active_config = config or WeightedVotingConfig()
        snapshot = context.finalised_one_minute_market_snapshot
        _validate_completed_bars(snapshot)
        inventory = context.inventory_snapshot
        condition = classify_market_condition(snapshot, config=active_config, previous_condition=context.previous_market_condition)
        effective = settings_resolver.resolve(condition, timestamp=snapshot.data_timestamp) if settings_resolver is not None else context.effective_settings
        decision_config = _config_with_effective_settings(active_config, effective)
        context_failure_reasons = tuple(
            dict.fromkeys(
                (
                    *context.context_failure_reason_codes(stale_after_seconds=effective.stale_data_threshold_seconds),
                    *_missing_authoritative_runtime_input_reason_codes(context),
                )
            )
        )
        if settings_resolver is None and effective.expiration_timestamp is not None and effective.expiration_timestamp <= snapshot.data_timestamp:
            context_failure_reasons = tuple(
                dict.fromkeys(
                    (
                        *context_failure_reasons,
                        "weighted_voting.context.settings_expired",
                        "weighted_voting.dynamic_settings.expired_without_resolver",
                    )
                )
            )
        active_weight_state = context.active_weight_state
        signals = tuple(_evaluate_strategy_signals(signal_evaluator, snapshot, decision_config, active_weight_state, condition))
        decision = aggregate_weighted_signals(list(signals), config=decision_config, decision_timestamp=snapshot.data_timestamp, historical_outcomes=historical_outcomes)
        decision = decision.model_copy(
            update={
                "weight_version": active_weight_state.weight_version,
                "settings_version": effective.settings_version,
                "data_manifest_hash": snapshot.data_manifest_hash,
            }
        )
        if context_failure_reasons:
            decision = decision.model_copy(
                update={
                    "signal": WeightedSide.HOLD,
                    "proposed_side": WeightedSide.HOLD,
                    "raw_winner": WeightedSide.HOLD,
                    "eligible": False,
                    "data_ready": False,
                    "reason_codes": tuple(dict.fromkeys((*decision.reason_codes, *context_failure_reasons, "weighted_voting.decision_kernel.fail_closed_hold"))),
                    "explanation": "Weighted Voting held because the typed runtime context failed closed before local entry approval.",
                }
            )
        alignment = _five_minute_alignment(context, decision.proposed_side)
        expected_value_after_costs = _expected_value_after_costs(signals, decision, snapshot, context)
        gate_result = evaluate_local_decision_gates(
            WeightedVotingLocalGateInputs(
                decision=decision,
                signals=signals,
                market_snapshot=snapshot,
                five_minute_alignment=alignment if decision.signal in (WeightedSide.BUY.value, WeightedSide.SELL.value) else WeightedFiveMinuteAlignment.UNAVAILABLE,
                expected_value_after_costs=expected_value_after_costs,
                spread_cost=_spread_cost(snapshot),
                slippage_cost=_normalized_slippage_cost(snapshot, context),
                fee_cost=_normalized_fee_cost(snapshot, context),
                atr_percent=_atr_percent(snapshot),
                entry_quality=decision.vote_scores.winner_score,
                session_allowed=context.exchange_session_state.session_allowed is True,
                weighted_daily_loss_percent=inventory.daily_loss_percent,
                weighted_daily_trade_count=context.algorithm_daily_trade_count,
                capital_available=_required_positive_float(context.remaining_algorithm_capital_partition),
                current_position=context.current_position,
                remaining_weighted_capital_partition=context.remaining_algorithm_capital_partition,
                data_timestamp=snapshot.data_timestamp,
            ),
            config=decision_config,
        )
        sizing = calculate_weighted_voting_position_size(
            WeightedVotingSizingContext(
                decision=decision,
                effective_settings=effective,
                market_snapshot=snapshot,
                account_equity=_required_positive_float(inventory.equity),
                available_buying_power=_required_non_negative_float(inventory.buying_power),
                remaining_weighted_daily_risk=_required_positive_float(context.remaining_algorithm_daily_risk),
                remaining_weighted_capital_partition=_required_positive_float(context.remaining_algorithm_capital_partition),
                global_available_risk=_required_positive_float(context.global_risk_state.global_available_risk),
                global_max_shares=_required_positive_int(context.global_risk_state.global_max_shares),
                structural_invalidation_price=_structural_invalidation(signals, decision.proposed_side),
                atr=average_true_range(snapshot.one_minute_candles, 14),
                slippage_per_share=_required_non_negative_float(context.estimated_slippage),
                current_one_minute_volume=snapshot.one_minute_candles[-1].volume,
                average_one_minute_volume=average_volume(snapshot.one_minute_candles, 20),
                local_gate_result=gate_result,
            )
        )
        decision = decision.model_copy(update={"proposed_quantity": sizing.quantity, "gate_results": gate_result.gate_results})
        final_blocking_reasons = _final_trade_blocking_reason_codes(
            context=context,
            effective=effective,
            expected_value_after_costs=expected_value_after_costs,
            context_failure_reasons=context_failure_reasons,
            gate_result=gate_result,
            sizing=sizing,
            decision=decision,
        )
        if final_blocking_reasons:
            decision = _hold_decision(decision, final_blocking_reasons)
            sizing = _zero_sizing_from(sizing, final_blocking_reasons)
        trigger_price = _proposal_entry_price(snapshot, decision.proposed_side)
        stop_price = _proposal_stop_price(trigger_price, sizing.stop_distance, decision.proposed_side)
        target_price = _proposal_target_price(trigger_price, sizing.stop_distance, effective.target_r, decision.proposed_side)
        order_proposal = build_weighted_voting_order_proposal(
            decision=decision,
            sizing=sizing,
            effective_settings=effective,
            market_snapshot=snapshot,
            signals=signals,
            trigger_price=trigger_price,
            limit_price=trigger_price,
            stop_price=stop_price,
            target_price=target_price,
            created_at=snapshot.data_timestamp,
        )
        reasons = tuple(dict.fromkeys((*decision.reason_codes, *gate_result.reason_codes, *sizing.reason_codes)))
        observability = _observability_record(
            context=context,
            condition=condition,
            effective_settings=effective,
            signals=signals,
            decision=decision,
            gate_result=gate_result,
            sizing=sizing,
            order_proposal=order_proposal,
            reason_codes=reasons,
        )
        result_hash = _hash_payload(
            {
                "kernelVersion": WEIGHTED_VOTING_DECISION_KERNEL_VERSION,
                "contextManifestHash": context.manifest_hash,
                "decision": decision,
                "signals": signals,
                "gateResult": gate_result,
                "sizing": sizing,
                "orderProposal": order_proposal.as_dict(),
                "observabilityHash": observability["snapshotHash"],
            }
        )
        return WeightedVotingDecisionResult(
            kernel_version=WEIGHTED_VOTING_DECISION_KERNEL_VERSION,
            context_version=context.context_version,
            context_manifest_hash=context.manifest_hash,
            market_snapshot=snapshot,
            inventory_snapshot_version=inventory.snapshot_version,
            market_condition=condition,
            effective_settings=effective,
            active_weight_version=active_weight_state.weight_version,
            signals=signals,
            decision=decision,
            gate_result=gate_result,
            sizing_result=sizing,
            order_proposal=order_proposal,
            observability_record=observability,
            reason_codes=reasons,
            deterministic_result_hash=result_hash,
        )


def decision_kernel_status() -> dict[str, Any]:
    return {
        "version": WEIGHTED_VOTING_DECISION_KERNEL_VERSION,
        "algorithmId": "weighted_voting",
        "sideEffectFree": True,
        "authoritativeInput": "WeightedVotingRuntimeContext",
        "authoritativeResult": "WeightedVotingDecisionResult",
        "forbiddenActions": (
            "write_persistence",
            "submit_orders",
            "call_http_api",
            "read_global_mutable_variables",
            "create_default_inventory",
            "create_default_global_risk_response",
            "use_wall_clock_time",
            "use_incomplete_candles",
        ),
        "sequence": (
            "validate_completed_bar_and_data_readiness",
            "load_inventory_snapshot_from_context",
            "classify_market_condition",
            "resolve_dynamic_settings",
            "load_active_versioned_strategy_weights",
            "evaluate_eligible_strategy_signals",
            "apply_weight_family_and_correlation_controls",
            "aggregate_buy_sell_hold_scores",
            "calculate_five_minute_alignment",
            "estimate_transaction_costs_and_expected_value",
            "run_local_algorithm_gates",
            "calculate_position_size_from_algorithm_risk_and_capital",
            "enforce_final_trade_eligibility_after_cost_latency_gate_and_position_limits",
            "create_order_proposal_or_hold",
            "produce_immutable_observability_record",
        ),
        "reasonCodes": ("weighted_voting.decision_kernel.ready",),
    }


def _config_with_effective_settings(config: WeightedVotingConfig, effective: WeightedEffectiveSettings) -> WeightedVotingConfig:
    return replace(
        config,
        strategy_enablement=dict(effective.strategy_eligibility),
        strategy_baseline_weights=dict(effective.baseline_strategy_weights or config.strategy_baseline_weights),
        strategy_minimum_weights=dict(effective.minimum_strategy_weights or config.strategy_minimum_weights),
        strategy_maximum_weights=dict(effective.maximum_strategy_weights or config.strategy_maximum_weights),
        strategy_states=dict(effective.strategy_states or config.strategy_states),
        strategy_time_stop_minutes=dict(effective.strategy_time_stops_minutes or config.strategy_time_stop_minutes),
        family_exposure_caps=dict(effective.family_exposure_caps or config.family_exposure_caps),
        minimum_score=effective.minimum_score,
        minimum_edge=effective.minimum_edge,
        minimum_active_strategies=effective.minimum_active_strategies,
        minimum_directional_strategies=effective.minimum_directional_strategies,
        maximum_disagreement_score=effective.maximum_opposing_score,
        correlation_penalty=effective.correlation_penalty,
        local_max_spread_percent=effective.maximum_spread_percent,
        local_minimum_liquidity_volume=effective.minimum_liquidity_volume,
        maximum_weighted_daily_loss_percent=effective.maximum_daily_loss_percent,
        maximum_weighted_daily_trades=effective.maximum_trades,
        maximum_family_weight=effective.family_exposure_cap,
        stale_after_seconds=effective.stale_data_threshold_seconds,
        data_freshness_limit_seconds=effective.stale_data_threshold_seconds,
        quote_freshness_limit_seconds=effective.quote_freshness_threshold_seconds,
        entry_slippage_per_share=effective.slippage_allowance_per_share,
        fee_per_share=effective.fee_per_share,
        risk_per_trade_baseline_percent=effective.base_risk_per_trade_percent,
        order_allocation_percent=effective.order_allocation_percent,
        daily_allocation_percent=effective.daily_allocation_percent,
        maximum_position_percent=effective.maximum_position_percent,
        maximum_shares=effective.maximum_shares,
        maximum_participation_rate=effective.maximum_participation_rate,
        atr_stop_multiplier=effective.atr_stop_multiplier,
        minimum_stop_distance_percent=effective.minimum_stop_distance_percent,
        maximum_stop_distance_percent=effective.maximum_stop_distance_percent,
        target_r=effective.target_r,
        entry_buffer_percent=effective.entry_buffer_percent,
        break_even_trigger_r=effective.break_even_trigger_r,
        trailing_stop_atr_multiplier=effective.trailing_stop_atr_multiplier,
        trailing_stop_policy=effective.trailing_stop_policy,
        time_stop_minutes=effective.time_stop_minutes,
        session_cutoff_minutes=effective.session_cutoff_minutes,
        end_of_day_liquidation_time=effective.end_of_day_liquidation_time,
        entry_cutoff_time=effective.entry_cutoff_time,
        event_risk_action=effective.event_risk_action,
        volatility_multiplier=effective.volatility_multiplier,
        liquidity_multiplier=effective.liquidity_multiplier,
        session_multiplier=effective.session_multiplier,
    )


def _validate_completed_bars(snapshot: WeightedMarketSnapshot) -> None:
    if not snapshot.one_minute_candles:
        raise ValueError("Weighted Voting decision kernel requires completed one-minute candles")
    latest = snapshot.one_minute_candles[-1]
    if latest.timestamp > snapshot.data_timestamp:
        raise ValueError("Weighted Voting decision kernel refuses incomplete one-minute candles")


def _evaluate_strategy_signals(
    signal_evaluator: SignalEvaluator,
    snapshot: WeightedMarketSnapshot,
    config: WeightedVotingConfig,
    active_weight_state: Any,
    condition: WeightedMarketCondition,
) -> list[WeightedVotingSignal] | tuple[WeightedVotingSignal, ...]:
    try:
        return signal_evaluator(snapshot, config, active_weight_state, condition)
    except TypeError:
        try:
            return signal_evaluator(snapshot, config)
        except TypeError:
            return signal_evaluator(snapshot)


def _five_minute_alignment(context: WeightedVotingRuntimeContext, side: WeightedSide | str) -> WeightedFiveMinuteAlignment:
    side_value = _side_value(side)
    if side_value not in (WeightedSide.BUY.value, WeightedSide.SELL.value):
        return WeightedFiveMinuteAlignment.UNAVAILABLE
    candles = context.five_minute_candles
    if candles:
        first = candles[-1].open
        last = candles[-1].close
    else:
        one_minute = context.finalised_one_minute_market_snapshot.one_minute_candles[-5:]
        if len(one_minute) < 5:
            return WeightedFiveMinuteAlignment.UNAVAILABLE
        first = one_minute[0].open
        last = one_minute[-1].close
    move = last - first
    if abs(move) < last * 0.0002:
        return WeightedFiveMinuteAlignment.NEUTRAL
    if side_value == WeightedSide.BUY.value and move > 0:
        return WeightedFiveMinuteAlignment.POSITIVE
    if side_value == WeightedSide.SELL.value and move < 0:
        return WeightedFiveMinuteAlignment.POSITIVE
    return WeightedFiveMinuteAlignment.NEGATIVE


def _expected_value_after_costs(
    signals: tuple[WeightedVotingSignal, ...],
    decision: WeightedDecision,
    snapshot: WeightedMarketSnapshot,
    context: WeightedVotingRuntimeContext,
) -> float:
    directional = [signal.expected_return_after_costs for signal in signals if signal.signal == decision.proposed_side]
    gross = max(directional) if directional else 0.0
    latest = snapshot.one_minute_candles[-1]
    if latest.close <= 0 or snapshot.spread is None or context.estimated_slippage is None or context.estimated_fees is None:
        return -MISSING_RUNTIME_COST_RETURN
    cost = (snapshot.spread + context.estimated_slippage + context.estimated_fees) / latest.close
    return gross - cost


def _normalized_slippage_cost(snapshot: WeightedMarketSnapshot, context: WeightedVotingRuntimeContext) -> float:
    latest = snapshot.one_minute_candles[-1]
    if latest.close <= 0 or context.estimated_slippage is None:
        return MISSING_RUNTIME_COST_RETURN
    return context.estimated_slippage / latest.close


def _normalized_fee_cost(snapshot: WeightedMarketSnapshot, context: WeightedVotingRuntimeContext) -> float:
    latest = snapshot.one_minute_candles[-1]
    if latest.close <= 0 or context.estimated_fees is None:
        return MISSING_RUNTIME_COST_RETURN
    return context.estimated_fees / latest.close


def _spread_cost(snapshot: WeightedMarketSnapshot) -> float:
    if snapshot.bid is None or snapshot.ask is None:
        return MISSING_RUNTIME_COST_RETURN
    return max(0.0, snapshot.ask - snapshot.bid)


def _atr_percent(snapshot: WeightedMarketSnapshot) -> float | None:
    atr = average_true_range(snapshot.one_minute_candles, 14)
    latest = snapshot.one_minute_candles[-1]
    return atr / latest.close if atr is not None and latest.close > 0 else None


def _structural_invalidation(signals: tuple[WeightedVotingSignal, ...], side: WeightedSide | str) -> float | None:
    side_value = _side_value(side)
    levels = [signal.invalidation_level for signal in signals if signal.signal == side_value and signal.invalidation_level is not None]
    if not levels:
        return None
    return max(levels) if side_value == WeightedSide.BUY.value else min(levels)


def _missing_authoritative_runtime_input_reason_codes(context: WeightedVotingRuntimeContext) -> tuple[str, ...]:
    reasons: list[str] = []
    snapshot = context.finalised_one_minute_market_snapshot
    if snapshot.bid is None or snapshot.ask is None or snapshot.spread is None:
        reasons.append("weighted_voting.decision_kernel.missing_actual_quote_blocks_trade")
    if context.estimated_slippage is None:
        reasons.append("weighted_voting.decision_kernel.missing_actual_slippage_estimate_blocks_trade")
    if context.estimated_fees is None:
        reasons.append("weighted_voting.decision_kernel.missing_actual_fee_estimate_blocks_trade")
    if context.inventory_available is not True:
        reasons.append("weighted_voting.decision_kernel.weighted_inventory_ledger_unavailable_blocks_trade")
    if context.inventory_snapshot.equity <= 0:
        reasons.append("weighted_voting.decision_kernel.local_inventory_equity_unavailable_blocks_trade")
    if context.inventory_snapshot.buying_power < 0:
        reasons.append("weighted_voting.decision_kernel.local_inventory_buying_power_unavailable_blocks_trade")
    if context.remaining_algorithm_daily_risk is None or context.remaining_algorithm_daily_risk <= 0:
        reasons.append("weighted_voting.decision_kernel.remaining_daily_risk_unavailable_blocks_trade")
    if context.remaining_algorithm_capital_partition is None or context.remaining_algorithm_capital_partition <= 0:
        reasons.append("weighted_voting.decision_kernel.algorithm_capital_allocation_unavailable_blocks_trade")
    if context.global_risk_state.service_available is not True:
        reasons.append("weighted_voting.decision_kernel.central_global_risk_unavailable_blocks_trade")
    if context.global_risk_state.global_available_risk is None or context.global_risk_state.global_available_risk <= 0:
        reasons.append("weighted_voting.decision_kernel.central_global_risk_capacity_unavailable_blocks_trade")
    if context.global_risk_state.global_max_shares is None or context.global_risk_state.global_max_shares <= 0:
        reasons.append("weighted_voting.decision_kernel.central_global_share_capacity_unavailable_blocks_trade")
    if context.exchange_session_state.session_allowed is not True or context.exchange_session_state.is_exchange_open is not True:
        reasons.append("weighted_voting.decision_kernel.exchange_session_state_blocks_trade")
    if _enum_value(context.five_minute_alignment) == WeightedFiveMinuteAlignment.UNAVAILABLE.value:
        reasons.append("weighted_voting.decision_kernel.five_minute_confirmation_unavailable_blocks_trade")
    return tuple(dict.fromkeys(reasons))


def _final_trade_blocking_reason_codes(
    *,
    context: WeightedVotingRuntimeContext,
    effective: WeightedEffectiveSettings,
    expected_value_after_costs: float,
    context_failure_reasons: tuple[str, ...],
    gate_result: Any,
    sizing: WeightedVotingSizingResult,
    decision: WeightedDecision,
) -> tuple[str, ...]:
    reasons: list[str] = []
    side = _side_value(decision.proposed_side)
    if side not in (WeightedSide.BUY.value, WeightedSide.SELL.value) or not decision.eligible:
        reasons.append("weighted_voting.decision_kernel.no_directional_trade")
    if context_failure_reasons:
        reasons.append("weighted_voting.decision_kernel.latency_or_runtime_context_blocks_trade")
    if expected_value_after_costs <= 0:
        reasons.append("weighted_voting.decision_kernel.expected_edge_does_not_survive_costs")
    if gate_result.permission_granted is not True:
        reasons.append("weighted_voting.decision_kernel.local_gates_block_trade")
    if context.global_risk_state.service_available is not True:
        reasons.append("weighted_voting.decision_kernel.global_gate_unavailable_blocks_trade")
    if context.global_risk_state.global_available_risk is None or context.global_risk_state.global_available_risk <= 0:
        reasons.append("weighted_voting.decision_kernel.global_risk_capacity_blocks_trade")
    if context.global_risk_state.global_max_shares is None or context.global_risk_state.global_max_shares <= 0:
        reasons.append("weighted_voting.decision_kernel.global_share_capacity_blocks_trade")
    response = context.global_risk_state.gate_response
    if response is not None and (
        response.action not in {"ALLOW", "REDUCE_QUANTITY"}
        or response.maximumAllowedQuantity <= 0
        or response.maximumAdditionalRiskDollars <= 0
    ):
        reasons.append("weighted_voting.decision_kernel.global_gate_response_blocks_trade")
    if sizing.quantity <= 0:
        reasons.append("weighted_voting.decision_kernel.position_sizing_blocks_trade")
    if _cap_quantity(sizing, "maximum_position") <= 0 or effective.maximum_position_percent <= 0:
        reasons.append("weighted_voting.decision_kernel.position_limit_blocks_trade")
    if sizing.buying_power_quantity <= 0 or sizing.capital_partition_quantity <= 0 or sizing.algorithm_maximum_quantity <= 0:
        reasons.append("weighted_voting.decision_kernel.capital_or_algorithm_position_limit_blocks_trade")
    return tuple(dict.fromkeys(reasons))


def _required_positive_float(value: float | int | None) -> float:
    if value is None or float(value) <= 0:
        return 0.0
    return float(value)


def _required_non_negative_float(value: float | int | None) -> float:
    if value is None or float(value) < 0:
        return 0.0
    return float(value)


def _required_positive_int(value: int | None) -> int:
    if value is None or int(value) <= 0:
        return 0
    return int(value)


def _hold_decision(decision: WeightedDecision, reason_codes: tuple[str, ...]) -> WeightedDecision:
    return decision.model_copy(
        update={
            "signal": WeightedSide.HOLD.value,
            "proposed_side": WeightedSide.HOLD.value,
            "raw_winner": WeightedSide.HOLD.value,
            "eligible": False,
            "proposed_quantity": 0,
            "reason_codes": tuple(
                dict.fromkeys(
                    (
                        *decision.reason_codes,
                        *reason_codes,
                        "weighted_voting.decision_kernel.final_trade_eligibility_failed",
                    )
                )
            ),
            "explanation": "Weighted Voting held because the final trade eligibility barrier rejected the trade after costs, latency, gates, and position limits.",
        }
    )


def _zero_sizing_from(sizing: WeightedVotingSizingResult, reason_codes: tuple[str, ...]) -> WeightedVotingSizingResult:
    return replace(
        sizing,
        quantity=0,
        requested_quantity=0,
        approved_local_quantity=0,
        reason_codes=tuple(
            dict.fromkeys(
                (
                    *sizing.reason_codes,
                    *reason_codes,
                    "weighted_voting.sizing.final_trade_eligibility_zeroed",
                )
            )
        ),
        explanation="Final Weighted Voting quantity is zero because the final trade eligibility barrier failed.",
    )


def _cap_quantity(sizing: WeightedVotingSizingResult, cap_id: str) -> int:
    for cap in sizing.caps:
        if cap.cap_id == cap_id:
            return cap.quantity
    return 0


def _proposal_entry_price(snapshot: WeightedMarketSnapshot, side: WeightedSide | str) -> float | None:
    side_value = _side_value(side)
    if side_value == WeightedSide.BUY.value:
        return snapshot.ask
    if side_value == WeightedSide.SELL.value:
        return snapshot.bid
    return None


def _proposal_stop_price(entry_price: float | None, stop_distance: float, side: WeightedSide | str) -> float | None:
    side_value = _side_value(side)
    if entry_price is None or stop_distance <= 0:
        return None
    if side_value == WeightedSide.BUY.value:
        return max(0.01, round(entry_price - stop_distance, 4))
    if side_value == WeightedSide.SELL.value:
        return round(entry_price + stop_distance, 4)
    return None


def _proposal_target_price(entry_price: float | None, stop_distance: float, target_r: float, side: WeightedSide | str) -> float | None:
    side_value = _side_value(side)
    if entry_price is None or stop_distance <= 0 or target_r <= 0:
        return None
    target_distance = stop_distance * target_r
    if side_value == WeightedSide.BUY.value:
        return round(entry_price + target_distance, 4)
    if side_value == WeightedSide.SELL.value:
        return max(0.01, round(entry_price - target_distance, 4))
    return None


def _observability_record(
    *,
    context: WeightedVotingRuntimeContext,
    condition: WeightedMarketCondition,
    effective_settings: WeightedEffectiveSettings,
    signals: tuple[WeightedVotingSignal, ...],
    decision: WeightedDecision,
    gate_result: Any,
    sizing: WeightedVotingSizingResult,
    order_proposal: WeightedVotingOrderProposal,
    reason_codes: tuple[str, ...],
) -> dict[str, Any]:
    record = {
        "observabilityVersion": "weighted_voting_decision_kernel_observability_v1",
        "immutable": True,
        "algorithmId": "weighted_voting",
        "kernelVersion": WEIGHTED_VOTING_DECISION_KERNEL_VERSION,
        "contextVersion": context.context_version,
        "contextManifestHash": context.manifest_hash,
        "decisionId": decision.decision_id,
        "recordedAt": context.finalised_one_minute_market_snapshot.data_timestamp.isoformat(),
        "dataTimestamp": context.finalised_one_minute_market_snapshot.data_timestamp.isoformat(),
        "marketSnapshotHash": context.finalised_one_minute_market_snapshot.data_manifest_hash,
        "sourceAttribution": {key: _json_ready(value) for key, value in context.source_attribution.items()},
        "inventorySnapshotVersion": context.inventory_snapshot.snapshot_version,
        "marketCondition": condition.model_dump(mode="json"),
        "effectiveSettings": effective_settings.model_dump(mode="json"),
        "activeWeightState": context.active_weight_state.model_dump(mode="json"),
        "signals": [signal.model_dump(mode="json") for signal in signals],
        "decision": decision.model_dump(mode="json"),
        "localGateResult": _json_ready(gate_result),
        "sizingResult": _json_ready(sizing),
        "orderProposal": order_proposal.as_dict(),
        "reasonCodes": reason_codes,
    }
    record["snapshotHash"] = _hash_payload(record)
    return record


def _side_value(side: WeightedSide | str) -> str:
    return str(getattr(side, "value", side))


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _json_ready(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in sorted(value.items())}
    if hasattr(value, "__dataclass_fields__"):
        return _json_ready(asdict(value))
    return value


__all__ = [
    "WEIGHTED_VOTING_DECISION_KERNEL_VERSION",
    "WeightedVotingDecisionKernel",
    "WeightedVotingDecisionResult",
    "decision_kernel_status",
]
