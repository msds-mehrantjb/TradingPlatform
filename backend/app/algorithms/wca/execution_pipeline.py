"""Shared WCA decision and paper-execution pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import floor
from typing import Any, Literal

from backend.app.algorithms.wca.aggregation import aggregate_wca
from backend.app.algorithms.wca.confidence import ConfidenceCalibrationConfig, calibrate_evaluations, conservative_fallback_calibration_table
from backend.app.algorithms.wca.configuration import WcaConfiguration, WcaConfigurationUnavailable
from backend.app.algorithms.wca.contracts import (
    GlobalGateResult,
    WcaBaselineSettings,
    WcaConfidenceCalibrationTable,
    WcaDecision,
    WcaDynamicProfile,
    WcaGateStatus,
    WcaLatencyTimestamps,
    WcaMarketSnapshot,
    WcaMarketStatus,
    WcaSide,
    WcaStrategyEvaluation,
    WcaWeightSnapshot,
)
from backend.app.algorithms.wca.cost_model import WCA_COST_MODEL_ADAPTER_VERSION, WcaCostModelInput, estimate_wca_round_trip_cost
from backend.app.algorithms.wca.dynamic_profile import WcaDynamicProfileConfig, resolve_dynamic_profile
from backend.app.algorithms.wca.exits import WcaBacktestOpenPosition, WcaExitEvaluation, evaluate_wca_exit
from backend.app.algorithms.wca.global_risk import WCA_GLOBAL_RISK_ADAPTER_VERSION, WcaGlobalRiskAdapter, WcaGlobalRiskClient, build_wca_global_risk_proposal
from backend.app.algorithms.wca.latency import WCA_LATENCY_OBSERVABILITY_VERSION, build_decision_latency_snapshot, utc_now
from backend.app.algorithms.wca.local_gates import WcaLocalGateContext, apply_local_gates_to_decision, evaluate_wca_hard_filters, evaluate_wca_local_gates
from backend.app.algorithms.wca.market_status import resolve_market_status
from backend.app.algorithms.wca.order_validation import WcaOrderValidationContext, apply_wca_final_order_validation
from backend.app.algorithms.wca.sizing import WcaManualSizingOverride, WcaSizingContext, size_wca_order
from backend.app.algorithms.wca.strategies.indicators import atr
from backend.app.algorithms.wca.modifiers import evaluate_all_modifiers
from backend.app.algorithms.wca.strategies.primary_voters import WCA_PRIMARY_VOTERS
from backend.app.algorithms.wca.strategy_registry import WcaStrategy
from backend.app.algorithms.wca.weights import baseline_weight_snapshot


WCA_EXECUTION_PIPELINE_VERSION = "wca_execution_pipeline_v1"
WCA_PRODUCTION_PIPELINE_VERSION = "wca_production_pipeline_v1"
WCA_EXECUTION_PIPELINE_MODULES = (
    "command_validation",
    "configuration_revision",
    "data_freshness",
    "strategy_registry",
    "contextual_modifiers",
    "confidence_calibration",
    "weight_engine",
    "reliability_health_family_correlation_controls",
    "market_status",
    "dynamic_profile",
    "aggregation",
    "hard_filters",
    "local_gates",
    "expected_edge_after_costs",
    "sizing",
    "global_risk_approval",
    "order_proposal",
    "order_validation",
    "exits",
    "decision_persistence",
    "order_intent_creation",
)
WCA_PIPELINE_MODULE_VERSIONS = {
    "production_pipeline": WCA_PRODUCTION_PIPELINE_VERSION,
    "execution_pipeline": WCA_EXECUTION_PIPELINE_VERSION,
    "confidence_calibration": "wca_confidence_calibration_beta_binomial_v1",
    "contextual_modifiers": "wca_modifiers_v1",
    "hard_filters": "wca_hard_filters_v1",
    "global_risk_approval": "wca_shared_global_risk_adapter_v1",
    "cost_model": WCA_COST_MODEL_ADAPTER_VERSION,
    "latency_observability": WCA_LATENCY_OBSERVABILITY_VERSION,
}

WcaPipelineRuntimeMode = Literal["manual_paper", "automatic_paper", "shadow", "historical_replay", "backtest", "test_simulation"]


@dataclass(frozen=True)
class WcaExecutionPipelineInput:
    run_id: str
    decision_id: str
    order_intent_id: str
    snapshot: WcaMarketSnapshot
    configuration_version: str
    runtime_mode: WcaPipelineRuntimeMode = "manual_paper"
    synthetic_quote_allowed: bool = False
    account_id: str = "paper"
    configuration: WcaConfiguration | None = None
    baseline: WcaBaselineSettings | None = None
    weight_snapshot: WcaWeightSnapshot | None = None
    calibration_tables: tuple[WcaConfidenceCalibrationTable, ...] = ()
    previous_market_status: WcaMarketStatus | None = None
    previous_dynamic_profile: WcaDynamicProfile | None = None
    risk_improvement_confirmations: int = 0
    trades_today: int = 0
    open_position: WcaBacktestOpenPosition | None = None
    realized_daily_loss: float = 0.0
    account_equity: float = 100_000.0
    available_buying_power: float = 100_000.0
    allocated_daily_loss_budget: float | None = None
    remaining_allocated_risk_budget: float | None = None
    global_gate_quantity_cap: int | None = 2_147_483_647
    approved_risk_budget: float | None = None
    total_account_exposure_snapshot: dict[str, Any] = field(default_factory=dict)
    current_wca_attributed_exposure: float = 0.0
    expected_holding_period_seconds: int = 3600
    broker_round_lot: int = 1
    idempotency_key_seen: bool = False
    allow_position_increase: bool = False
    estimated_cost_per_share: float = 0.01
    estimated_expectancy_after_costs: float = 0.01
    latency_timestamps: WcaLatencyTimestamps | None = None
    manual_sizing_override: WcaManualSizingOverride | None = None
    emergency_exit: bool = False
    session_exit_minutes: int = 15 * 60 + 59


@dataclass(frozen=True)
class WcaExecutionPipelineResult:
    decision: WcaDecision
    market_status: WcaMarketStatus
    dynamic_profile: WcaDynamicProfile
    exit_evaluation: WcaExitEvaluation | None
    risk_improvement_confirmations: int
    called_production_modules: tuple[str, ...] = WCA_EXECUTION_PIPELINE_MODULES


def run_wca_execution_pipeline(
    pipeline_input: WcaExecutionPipelineInput,
    *,
    voters: tuple[WcaStrategy, ...] = WCA_PRIMARY_VOTERS,
    calibration_config: ConfidenceCalibrationConfig = ConfidenceCalibrationConfig(),
    global_risk_client: WcaGlobalRiskClient | None = None,
) -> WcaExecutionPipelineResult:
    """Build one WCA decision through the single authoritative WCA production pipeline."""

    configuration = pipeline_input.configuration
    if configuration is not None:
        baseline = configuration.to_baseline_settings()
        configuration_version = configuration.configuration_version
        configuration_hash = configuration.content_hash
    elif pipeline_input.baseline is not None:
        baseline = pipeline_input.baseline
        configuration_version = pipeline_input.configuration_version
        configuration_hash = "legacy_api_compatibility_boundary"
    else:
        raise WcaConfigurationUnavailable("wca.configuration.missing_active_revision: WCA runtime cannot use unversioned defaults")
    snapshot = pipeline_input.snapshot.model_copy(
        update={
            "configuration_version": configuration_version,
            "configuration_hash": configuration_hash,
        }
    )
    _validate_command(pipeline_input, snapshot)
    weight_snapshot = pipeline_input.weight_snapshot or baseline_weight_snapshot(
        cutoff=snapshot.decision_timestamp,
        weight_version=f"{configuration_version}.baseline_weights",
    )
    hard_filters = evaluate_wca_hard_filters(
        snapshot=snapshot,
        context=_gate_context(pipeline_input, baseline),
        settings=configuration.hard_filter_settings if configuration is not None else None,
    )
    market_status = resolve_market_status(
        snapshot,
        previous_status=pipeline_input.previous_market_status,
        confirmation_count=pipeline_input.risk_improvement_confirmations,
    )
    next_confirmations = (
        pipeline_input.risk_improvement_confirmations + 1
        if "wca.market.hysteresis.improvement_held" in market_status.reason_codes
        else 0
    )
    dynamic_profile = resolve_dynamic_profile(
        baseline=baseline,
        market_status=market_status,
        calculation_timestamp=snapshot.decision_timestamp,
        previous_profile=pipeline_input.previous_dynamic_profile,
        current_drawdown_percent=_drawdown_percent(pipeline_input.realized_daily_loss, pipeline_input.account_equity),
        config=_dynamic_profile_config(configuration),
    )
    effective_settings = dynamic_profile.effective_settings
    exit_evaluation = None
    evaluations = tuple(
        evaluation.model_copy(
            update={
                "configuration_version": configuration_version,
                "configuration_hash": configuration_hash,
            }
        )
        for evaluation in (voter.evaluate(snapshot) for voter in voters)
    )
    strategy_completion = _observability_timestamp(pipeline_input, snapshot)
    modifiers = evaluate_all_modifiers(snapshot, configuration.modifier_settings if configuration is not None else None)
    evaluations = _apply_modifier_effects(evaluations, modifiers)
    calibration_tables = pipeline_input.calibration_tables or _conservative_fallback_tables(
        evaluations,
        decision_timestamp=snapshot.decision_timestamp,
        config=calibration_config,
    )
    calibrated = calibrate_evaluations(
        evaluations,
        tables=calibration_tables,
        config=calibration_config,
        decision_timestamp=snapshot.decision_timestamp,
        regime=_regime_key(market_status),
    )
    evaluations = _apply_weights(calibrated, weight_snapshot)
    provisional = aggregate_wca(evaluations, effective_settings=effective_settings)
    local_only_gates = evaluate_wca_local_gates(
        aggregation=provisional,
        effective_settings=effective_settings,
        context=_gate_context(pipeline_input, baseline),
    )
    local_gates = (*hard_filters, *local_only_gates)
    post_gate = apply_local_gates_to_decision(provisional.pre_gate_decision, local_gates)
    aggregation = aggregate_wca(
        evaluations,
        effective_settings=effective_settings,
        local_gates=local_gates,
    ).model_copy(
        update={
            "post_local_gate_decision": post_gate,
            "signal": post_gate,
            "decision_label": _decision_label(post_gate),
        }
    )
    aggregation_completion = _observability_timestamp(pipeline_input, snapshot)
    exit_evaluation = (
        evaluate_wca_exit(
            position=pipeline_input.open_position,
            candle=snapshot.candles[-1],
            opposite_signal=aggregation.signal,
            emergency_exit=pipeline_input.emergency_exit,
            session_exit_minutes=pipeline_input.session_exit_minutes,
        )
        if pipeline_input.open_position is not None
        else None
    )
    side = aggregation.post_local_gate_decision
    if exit_evaluation is not None and exit_evaluation.should_exit:
        side = WcaSide.SELL if pipeline_input.open_position and _side_value(pipeline_input.open_position.side) == WcaSide.BUY.value else WcaSide.BUY
        aggregation = aggregation.model_copy(
            update={
                "post_local_gate_decision": side,
                "signal": side,
                "decision_label": _decision_label(side),
                "reason_codes": (*aggregation.reason_codes, *exit_evaluation.reason_codes, "wca.pipeline.exit_evaluated_before_entry"),
            }
        )
    average_volume = max(1.0, _average_volume(snapshot.candles))
    gross_edge_per_share = _gross_edge_per_share(aggregation.winner_edge, snapshot)
    cost_estimate = estimate_wca_round_trip_cost(
        WcaCostModelInput(
            snapshot=snapshot,
            effective_settings=effective_settings,
            side=side,
            gross_edge_per_share=gross_edge_per_share,
            average_one_minute_volume=average_volume,
        )
    )
    aggregation = aggregation.model_copy(
        update={
            "estimated_expectancy_after_costs": cost_estimate.conservative_net_edge_per_share,
            "reason_codes": (*aggregation.reason_codes, *cost_estimate.reason_codes),
        }
    )
    quote = snapshot.quote
    bid = quote.bid if quote is not None else 0.0
    ask = quote.ask if quote is not None else 0.0
    sized = size_wca_order(
        WcaSizingContext(
            decision_id=pipeline_input.decision_id,
            order_intent_id=pipeline_input.order_intent_id,
            symbol=snapshot.symbol,
            side=side,
            price=snapshot.candles[-1].close,
            atr=max(_atr(snapshot.candles), 0.01),
            bid=bid,
            ask=ask,
            account_equity=max(1.0, pipeline_input.account_equity),
            available_buying_power=max(0.0, pipeline_input.available_buying_power),
            average_one_minute_volume=average_volume,
            confidence_size_multiplier=max(abs(aggregation.normalized_net_score), 0.01),
            edge_size_multiplier=max(aggregation.winner_edge, 0.01),
            global_gate_quantity_cap=None,
            approved_risk_budget=_budget(None, pipeline_input.account_equity, baseline.base_risk_percent),
            current_position_quantity=pipeline_input.open_position.quantity if pipeline_input.open_position else 0,
            current_position_side=pipeline_input.open_position.side if pipeline_input.open_position else None,
            allow_position_increase=pipeline_input.allow_position_increase,
            estimated_cost_per_share=cost_estimate.conservative_round_trip_cost_per_share,
        ),
        effective_settings,
        manual_override=pipeline_input.manual_sizing_override,
    )
    draft_decision = WcaDecision(
        decision_id=pipeline_input.decision_id,
        configuration_version=configuration_version,
        configuration_hash=configuration_hash,
        weight_version=weight_snapshot.weight_version,
        data_timestamp=snapshot.data_timestamp,
        decision_timestamp=snapshot.decision_timestamp,
        market_snapshot=snapshot,
        market_status=market_status,
        effective_settings=effective_settings,
        runtime_mode=pipeline_input.runtime_mode,
        called_module_versions=_module_versions(evaluations, modifiers, configuration_version, weight_snapshot.weight_version),
        modifier_evaluations=modifiers,
        hard_filter_results=hard_filters,
        aggregation=aggregation,
        local_gates=local_gates,
        sizing=sized.sizing,
        cost_estimate=cost_estimate,
        latency=build_decision_latency_snapshot(
            snapshot=snapshot,
            timestamps=pipeline_input.latency_timestamps,
            strategy_completion=strategy_completion,
            aggregation_completion=aggregation_completion,
        ),
        proposed_order=sized.proposed_order.model_copy(
            update={
                "configuration_version": configuration_version,
                "configuration_hash": configuration_hash,
            }
        )
        if sized.proposed_order is not None
        else None,
        reason_codes=(WCA_PRODUCTION_PIPELINE_VERSION, WCA_EXECUTION_PIPELINE_VERSION, *cost_estimate.reason_codes),
    )
    decision = _apply_global_risk_approval(draft_decision, pipeline_input, global_risk_client=global_risk_client)
    decision = decision.model_copy(
        update={
            "latency": build_decision_latency_snapshot(
                snapshot=snapshot,
                timestamps=decision.latency.timestamps if decision.latency is not None else pipeline_input.latency_timestamps,
                strategy_completion=strategy_completion,
                aggregation_completion=aggregation_completion,
                global_risk_response=_observability_timestamp(pipeline_input, snapshot),
            )
        }
    )
    decision = _apply_broker_rounding(decision, pipeline_input)
    exit_order = exit_evaluation is not None and exit_evaluation.should_exit
    decision = apply_wca_final_order_validation(
        decision,
        WcaOrderValidationContext(
            evaluation_timestamp=snapshot.decision_timestamp,
            paper_only_mode=True,
            account_id=pipeline_input.account_id,
            current_position_quantity=pipeline_input.open_position.quantity if pipeline_input.open_position else 0,
            current_position_side=pipeline_input.open_position.side if pipeline_input.open_position else None,
            allow_position_increase=pipeline_input.allow_position_increase,
            position_owned_by_wca=True,
            quote_freshness_seconds=None if pipeline_input.synthetic_quote_allowed else 15,
            available_buying_power=pipeline_input.available_buying_power,
            account_equity=pipeline_input.account_equity,
            max_position_value=max(0.0, pipeline_input.account_equity * (effective_settings.final_max_position_percent / 100.0)),
            realized_daily_loss=pipeline_input.realized_daily_loss,
            max_daily_loss=_budget(pipeline_input.allocated_daily_loss_budget, pipeline_input.account_equity, effective_settings.final_max_daily_loss_percent),
            trades_today=pipeline_input.trades_today,
            max_daily_trades=effective_settings.final_max_daily_trades,
            aggregate_global_risk_used=_global_risk_used_after_order(pipeline_input.total_account_exposure_snapshot, decision.sizing.stop_risk_dollars),
            aggregate_global_risk_limit=_global_float(pipeline_input.total_account_exposure_snapshot, "maximum_open_risk_dollars"),
            max_spread_percent=effective_settings.final_max_spread_percent,
            average_one_minute_volume=average_volume,
            max_participation_percent=effective_settings.final_max_participation_percent,
            expected_net_edge=cost_estimate.conservative_net_edge_per_share,
            minimum_net_edge=effective_settings.final_minimum_net_edge_per_share,
            idempotency_required=True,
            idempotency_key_seen=pipeline_input.idempotency_key_seen,
            new_entry_permitted=decision.global_gate_result.entry_permitted if decision.global_gate_result is not None else True,
            risk_reducing_exit_permitted=decision.global_gate_result.risk_reducing_exit_permitted if decision.global_gate_result is not None else True,
            is_risk_reducing_exit=exit_order,
            cross_algorithm_position_mutation=False,
        ),
    )
    decision = _with_reproducible_hash(decision)
    return WcaExecutionPipelineResult(
        decision=decision,
        market_status=market_status,
        dynamic_profile=dynamic_profile,
        exit_evaluation=exit_evaluation,
        risk_improvement_confirmations=next_confirmations,
    )


def run_wca_paper_pipeline_adapter(pipeline_input: WcaExecutionPipelineInput, **kwargs) -> WcaExecutionPipelineResult:
    return run_wca_execution_pipeline(replace(pipeline_input, runtime_mode="manual_paper", synthetic_quote_allowed=False), **kwargs)


def run_wca_replay_pipeline_adapter(pipeline_input: WcaExecutionPipelineInput, **kwargs) -> WcaExecutionPipelineResult:
    return run_wca_execution_pipeline(replace(pipeline_input, runtime_mode="historical_replay", synthetic_quote_allowed=False), **kwargs)


def run_wca_backtest_pipeline_adapter(pipeline_input: WcaExecutionPipelineInput, **kwargs) -> WcaExecutionPipelineResult:
    return run_wca_execution_pipeline(replace(pipeline_input, runtime_mode="backtest", synthetic_quote_allowed=True), **kwargs)


def _apply_weights(evaluations: tuple[WcaStrategyEvaluation, ...], weight_snapshot: WcaWeightSnapshot) -> tuple[WcaStrategyEvaluation, ...]:
    weighted = []
    for evaluation in evaluations:
        weight = weight_snapshot.weights.get(evaluation.strategy_id, evaluation.effective_weight)
        direction = 1 if evaluation.signal == WcaSide.BUY.value else -1 if evaluation.signal == WcaSide.SELL.value else 0
        weighted.append(
            evaluation.model_copy(
                update={
                    "effective_weight": weight,
                    "contribution": round(direction * weight * evaluation.calibrated_confidence, 10),
                }
            )
        )
    return tuple(weighted)


def _conservative_fallback_tables(
    evaluations: tuple[WcaStrategyEvaluation, ...],
    *,
    decision_timestamp,
    config: ConfidenceCalibrationConfig,
) -> tuple[WcaConfidenceCalibrationTable, ...]:
    return tuple(
        conservative_fallback_calibration_table(
            strategy_id=evaluation.strategy_id,
            strategy_version=evaluation.strategy_version,
            as_of=decision_timestamp,
            config=config,
        )
        for evaluation in evaluations
    )


def _apply_modifier_effects(evaluations: tuple[WcaStrategyEvaluation, ...], modifiers: tuple) -> tuple[WcaStrategyEvaluation, ...]:
    confidence_multiplier = 1.0
    weight_multiplier = 1.0
    reason_codes: list[str] = []
    for modifier in modifiers:
        if getattr(modifier, "status", None) != "ACTIVE":
            continue
        confidence_multiplier *= getattr(modifier, "confidence_multiplier", 1.0)
        weight_multiplier *= getattr(modifier, "weight_multiplier", 1.0)
        reason_codes.extend(getattr(modifier, "reason_codes", ()))
    adjusted = []
    for evaluation in evaluations:
        adjusted_confidence = round(max(0.0, min(1.0, evaluation.confidence * confidence_multiplier)), 4)
        weight = max(0.0, evaluation.effective_weight * weight_multiplier)
        direction = 1 if evaluation.signal == WcaSide.BUY.value else -1 if evaluation.signal == WcaSide.SELL.value else 0
        adjusted.append(
            evaluation.model_copy(
                update={
                    "confidence": adjusted_confidence,
                    "calibrated_confidence": adjusted_confidence,
                    "effective_weight": weight,
                    "contribution": round(direction * weight * adjusted_confidence, 10),
                    "reason_codes": (*evaluation.reason_codes, *tuple(dict.fromkeys(reason_codes))),
                }
            )
        )
    return tuple(adjusted)


def _gate_context(pipeline_input: WcaExecutionPipelineInput, baseline: WcaBaselineSettings) -> WcaLocalGateContext:
    return WcaLocalGateContext(
        evaluation_timestamp=pipeline_input.snapshot.decision_timestamp,
        trades_today=pipeline_input.trades_today,
        has_open_wca_position=pipeline_input.open_position is not None,
        realized_daily_loss=pipeline_input.realized_daily_loss,
        allocated_daily_loss_budget=_budget(
            pipeline_input.allocated_daily_loss_budget,
            pipeline_input.account_equity,
            baseline.max_daily_loss_percent,
        ),
        remaining_allocated_risk_budget=_budget(
            pipeline_input.remaining_allocated_risk_budget,
            pipeline_input.account_equity,
            baseline.base_risk_percent,
        ),
        is_risk_reducing_exit=pipeline_input.open_position is not None and pipeline_input.emergency_exit,
    )


def _validate_command(pipeline_input: WcaExecutionPipelineInput, snapshot: WcaMarketSnapshot) -> None:
    if pipeline_input.configuration_version and pipeline_input.configuration is not None and pipeline_input.configuration_version != pipeline_input.configuration.configuration_version:
        raise ValueError("pipeline configuration_version must match the loaded WCA configuration revision")
    if not snapshot.candles:
        raise ValueError("WCA pipeline requires at least one completed one-minute bar")
    latest = snapshot.candles[-1]
    if latest.timestamp != snapshot.data_timestamp:
        raise ValueError("WCA pipeline snapshot timestamp must match the latest completed bar")
    if snapshot.decision_timestamp < snapshot.data_timestamp:
        raise ValueError("WCA pipeline cannot decide before the completed bar timestamp")
    if snapshot.quote is None and pipeline_input.runtime_mode in ("manual_paper", "automatic_paper") and not pipeline_input.synthetic_quote_allowed:
        return
    if snapshot.quote is None and not pipeline_input.synthetic_quote_allowed:
        return


def _apply_global_risk_approval(decision: WcaDecision, pipeline_input: WcaExecutionPipelineInput, *, global_risk_client: WcaGlobalRiskClient | None = None) -> WcaDecision:
    proposed = decision.proposed_order
    proposed_quantity = proposed.quantity if proposed is not None else 0
    if proposed is None or proposed_quantity <= 0:
        gate = GlobalGateResult(
            status=WcaGateStatus.PASS,
            proposed_quantity=0,
            allowed_quantity=0,
            requested_risk=0.0,
            approved_risk=0.0,
            entry_permitted=True,
            risk_reducing_exit_permitted=True,
            reason_codes=(WCA_GLOBAL_RISK_ADAPTER_VERSION, "wca.global_risk.no_order", "wca.global_risk.approved"),
            explanation="No WCA entry proposal was sent to shared global risk.",
        )
        return decision.model_copy(update={"global_gate_result": gate, "reason_codes": (*decision.reason_codes, *gate.reason_codes)})

    idempotency_key = proposed.idempotency_key or _pipeline_idempotency_key(pipeline_input.account_id, decision, proposed.order_intent_id)
    exposure_snapshot = dict(pipeline_input.total_account_exposure_snapshot)
    if pipeline_input.global_gate_quantity_cap is not None:
        exposure_snapshot.setdefault("global_gate_quantity_cap", pipeline_input.global_gate_quantity_cap)
    if pipeline_input.approved_risk_budget is not None:
        exposure_snapshot.setdefault("approved_risk_budget", pipeline_input.approved_risk_budget)
    requested_risk = proposed_quantity * decision.sizing.stop_distance
    proposal = build_wca_global_risk_proposal(
        account_id=pipeline_input.account_id,
        symbol=proposed.symbol,
        side=proposed.side,
        requested_quantity=proposed_quantity,
        requested_risk=requested_risk,
        stop_distance=decision.sizing.stop_distance,
        expected_holding_period_seconds=pipeline_input.expected_holding_period_seconds,
        current_wca_attributed_exposure=pipeline_input.current_wca_attributed_exposure,
        total_account_exposure_snapshot=exposure_snapshot,
        configuration_version=decision.configuration_version,
        configuration_hash=decision.configuration_hash,
        decision_id=decision.decision_id,
        idempotency_key=idempotency_key,
        risk_reducing_exit=pipeline_input.open_position is not None and pipeline_input.emergency_exit,
    )
    risk_decision = (global_risk_client or WcaGlobalRiskAdapter()).evaluate_wca_proposal(proposal)
    allowed_quantity = min(proposed_quantity, risk_decision.approved_quantity)
    gate = GlobalGateResult(
        status=WcaGateStatus.PASS if risk_decision.entry_permitted and allowed_quantity == proposed_quantity else WcaGateStatus.FAIL,
        proposed_quantity=proposed_quantity,
        allowed_quantity=allowed_quantity,
        requested_risk=requested_risk,
        approved_risk=risk_decision.approved_risk,
        entry_permitted=risk_decision.entry_permitted,
        risk_reducing_exit_permitted=risk_decision.risk_reducing_exit_permitted,
        idempotency_key=risk_decision.idempotency_key,
        reason_codes=_global_risk_reason_codes(risk_decision.reason_codes, allowed_quantity, proposed_quantity),
        explanation=risk_decision.explanation,
    )
    sizing = decision.sizing.model_copy(
        update={
            "final_quantity": allowed_quantity,
            "stop_risk_dollars": allowed_quantity * decision.sizing.stop_distance,
            "shares_by_global_gate": allowed_quantity,
            "approved_risk_budget": risk_decision.approved_risk,
            "reason_codes": (*decision.sizing.reason_codes, *gate.reason_codes),
        }
    )
    order = (
        proposed.model_copy(
            update={
                "quantity": allowed_quantity,
                "account_id": pipeline_input.account_id,
                "idempotency_key": risk_decision.idempotency_key,
                "reason_codes": (*proposed.reason_codes, *gate.reason_codes),
            }
        )
        if allowed_quantity > 0 and risk_decision.entry_permitted
        else None
    )
    return decision.model_copy(
        update={
            "sizing": sizing,
            "proposed_order": order,
            "global_gate_result": gate,
            "reason_codes": (*decision.reason_codes, *gate.reason_codes),
        }
    )


def _apply_broker_rounding(decision: WcaDecision, pipeline_input: WcaExecutionPipelineInput) -> WcaDecision:
    lot = max(1, int(pipeline_input.broker_round_lot))
    proposed = decision.proposed_order
    if proposed is None or lot <= 1:
        return decision
    rounded_quantity = floor(proposed.quantity / lot) * lot
    if rounded_quantity == proposed.quantity:
        return decision
    reasons = ("wca.broker_rounding.quantity_reduced",)
    sizing = decision.sizing.model_copy(
        update={
            "final_quantity": rounded_quantity,
            "stop_risk_dollars": rounded_quantity * decision.sizing.stop_distance,
            "reason_codes": (*decision.sizing.reason_codes, *reasons),
        }
    )
    order = proposed.model_copy(update={"quantity": rounded_quantity, "reason_codes": (*proposed.reason_codes, *reasons)}) if rounded_quantity > 0 else None
    return decision.model_copy(update={"sizing": sizing, "proposed_order": order, "reason_codes": (*decision.reason_codes, *reasons)})


def _global_risk_reason_codes(reason_codes: tuple[str, ...], allowed_quantity: int, proposed_quantity: int) -> tuple[str, ...]:
    compatibility = "wca.global_risk.approved" if allowed_quantity == proposed_quantity else "wca.global_risk.reduced_or_rejected"
    return tuple(dict.fromkeys((*reason_codes, compatibility)))


def _module_versions(evaluations: tuple[WcaStrategyEvaluation, ...], modifiers: tuple, configuration_version: str, weight_version: str) -> dict[str, str]:
    versions = dict(WCA_PIPELINE_MODULE_VERSIONS)
    versions["global_risk_adapter"] = WCA_GLOBAL_RISK_ADAPTER_VERSION
    versions["configuration"] = configuration_version
    versions["weights"] = weight_version
    for evaluation in evaluations:
        versions[f"strategy.{evaluation.strategy_id}"] = evaluation.strategy_version
        versions[f"calibration.{evaluation.strategy_id}"] = evaluation.calibration_version
    for modifier in modifiers:
        versions[f"modifier.{modifier.modifier_id}"] = "wca_modifier_v1"
    return versions


def _with_reproducible_hash(decision: WcaDecision) -> WcaDecision:
    unhashed = decision.model_copy(update={"decision_hash": "", "runtime_mode": "pre_execution"})
    return decision.model_copy(update={"decision_hash": unhashed.deterministic_hash()})


def _atr(candles: tuple) -> float:
    return atr(candles, min(14, max(1, len(candles) - 1))) or max((candles[-1].high - candles[-1].low), 0.01)


def _average_volume(candles: tuple) -> float:
    selected = candles[-20:]
    return sum(candle.volume for candle in selected) / len(selected) if selected else 1.0


def _gross_edge_per_share(winner_edge: float, snapshot: WcaMarketSnapshot) -> float:
    return max(0.0, winner_edge) * max(_atr(snapshot.candles), 0.01)


def _dynamic_profile_config(configuration: WcaConfiguration | None) -> WcaDynamicProfileConfig:
    if configuration is None:
        return WcaDynamicProfileConfig()
    settings = configuration.dynamic_profile
    return WcaDynamicProfileConfig(
        enabled=settings.enabled,
        minimum_profile_hold_seconds=settings.minimum_profile_hold_seconds,
        profile_ttl_seconds=settings.overlay_ttl_seconds,
        risk_expanding_overlays_enabled=settings.risk_expanding_overlays_enabled,
        maximum_defensive_risk_multiplier=settings.maximum_defensive_risk_multiplier,
        maximum_defensive_quantity_multiplier=settings.maximum_defensive_quantity_multiplier,
    )


def _observability_timestamp(pipeline_input: WcaExecutionPipelineInput, snapshot: WcaMarketSnapshot):
    return utc_now() if pipeline_input.latency_timestamps is not None else snapshot.decision_timestamp


def _budget(value: float | None, account_equity: float, percent: float) -> float:
    return value if value is not None else max(0.0, account_equity * (percent / 100.0))


def _pipeline_idempotency_key(account_id: str, decision: WcaDecision, order_intent_id: str) -> str:
    return ":".join(("wca", account_id, decision.market_snapshot.symbol.upper(), decision.decision_id, order_intent_id, decision.configuration_version))


def _global_float(snapshot: dict[str, Any], key: str) -> float | None:
    value = snapshot.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _global_risk_used_after_order(snapshot: dict[str, Any], approved_order_risk: float) -> float | None:
    current = _global_float(snapshot, "current_open_risk_dollars")
    reserved = _global_float(snapshot, "reserved_open_risk_dollars") or 0.0
    if current is None and "maximum_open_risk_dollars" not in snapshot:
        return None
    return max(0.0, current or 0.0) + max(0.0, reserved) + max(0.0, approved_order_risk)


def _drawdown_percent(realized_daily_loss: float, account_equity: float) -> float:
    return max(0.0, realized_daily_loss / max(1.0, account_equity) * 100.0)


def _regime_key(status: WcaMarketStatus) -> str:
    return ":".join((_side_value(status.trend), _side_value(status.volatility), _side_value(status.session)))


def _decision_label(side: WcaSide | str) -> str:
    value = side.value if isinstance(side, WcaSide) else str(side)
    return "Buy" if value == WcaSide.BUY.value else "Sell" if value == WcaSide.SELL.value else "Hold"


def _side_value(side: WcaSide | str) -> str:
    return side.value if isinstance(side, WcaSide) else str(side)


__all__ = [
    "WCA_EXECUTION_PIPELINE_MODULES",
    "WCA_EXECUTION_PIPELINE_VERSION",
    "WCA_PRODUCTION_PIPELINE_VERSION",
    "WcaExecutionPipelineInput",
    "WcaExecutionPipelineResult",
    "run_wca_backtest_pipeline_adapter",
    "run_wca_execution_pipeline",
    "run_wca_paper_pipeline_adapter",
    "run_wca_replay_pipeline_adapter",
]
