"""Authoritative Meta-Strategy execution pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Callable, Literal, Protocol

from backend.app.algorithms.meta_strategy.broker_adapter import MetaStrategyBrokerAdapter, NoopMetaStrategyBrokerAdapter
from backend.app.algorithms.meta_strategy.candidate_generator import GeneratedDeterministicCandidate, generate_deterministic_candidate
from backend.app.algorithms.meta_strategy.candidate_geometry import CandidateGeometryResult, calculate_candidate_geometry
from backend.app.algorithms.meta_strategy.configuration import MetaStrategyBaselineSettings, meta_strategy_baseline_settings
from backend.app.algorithms.meta_strategy.contracts import MetaOrderIntent, MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.dynamic_profile import (
    MetaStrategyDynamicProfile,
    MetaStrategyDynamicProfileContext,
    resolve_meta_strategy_dynamic_profile,
)
from backend.app.algorithms.meta_strategy.feature_builder import MetaStrategyFeatureSet, build_meta_strategy_features
from backend.app.algorithms.meta_strategy.global_risk_adapter import MetaStrategyGlobalRiskAdapter, ReadOnlyMetaStrategyGlobalRiskAdapter
from backend.app.algorithms.meta_strategy.inference import MetaStrategyInferenceConfig, MetaStrategyInferenceResult, apply_meta_strategy_inference
from backend.app.algorithms.meta_strategy.local_gates import (
    MetaStrategyLocalGateContext,
    MetaStrategyLocalGateEvaluation,
    evaluate_meta_strategy_local_gates,
)
from backend.app.algorithms.meta_strategy.market_snapshot import MetaStrategyMarketSnapshotRequest, build_meta_strategy_market_snapshot
from backend.app.algorithms.meta_strategy.order_intent import build_meta_strategy_order_intent
from backend.app.algorithms.meta_strategy.order_validation import (
    MetaStrategyOrderValidationContext,
    MetaStrategyOrderValidationResult,
    validate_meta_strategy_order,
)
from backend.app.algorithms.meta_strategy.sizing import (
    MetaStrategySizingContext,
    MetaStrategySizingResult,
    calculate_meta_strategy_position_size,
)
from backend.app.algorithms.meta_strategy.reconciliation import MetaStrategyReconciliationRecord, reconcile_meta_strategy_broker_fill


MetaStrategyPipelineMode = Literal["EVALUATION", "SHADOW", "PAPER", "BACKTEST", "DAILY_REPLAY", "DIAGNOSTICS", "LIVE"]

META_STRATEGY_EXECUTION_PIPELINE_STAGES: tuple[str, ...] = (
    "market_snapshot",
    "strategies",
    "context_and_regime",
    "safety",
    "family_aggregation",
    "deterministic_candidate",
    "candidate_geometry",
    "feature_builder",
    "artifact_validation",
    "model_inference",
    "ml_decision_policy",
    "local_gates",
    "dynamic_profile",
    "sizing",
    "order_intent",
    "global_risk",
    "final_validation",
    "broker_adapter",
    "persistence",
    "reconciliation",
)


class MetaStrategyPersistenceAdapter(Protocol):
    def persist(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class MetaStrategyExecutionPipelineConfig:
    inference_config: MetaStrategyInferenceConfig = field(default_factory=lambda: MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="NO_TRADE"))
    baseline_settings: MetaStrategyBaselineSettings = field(default_factory=meta_strategy_baseline_settings)
    live_trading_enabled: bool = False
    default_account_equity: float = 100_000.0
    default_buying_power: float = 100_000.0
    default_remaining_algorithm_risk: float = 1_000.0
    default_global_available_risk: float = 1_000.0
    default_global_quantity_cap: int = 10_000
    configuration_hash: str = "meta_strategy_execution_pipeline_v1"


@dataclass(frozen=True)
class MetaStrategyExecutionPipelineRequest:
    mode: MetaStrategyPipelineMode
    snapshot_request: MetaStrategyMarketSnapshotRequest
    model_artifact: dict[str, Any] | None = None
    account_equity: float | None = None
    available_buying_power: float | None = None
    remaining_algorithm_risk: float | None = None
    global_available_risk: float | None = None
    global_quantity_cap: int | None = None
    realized_daily_pnl: float = 0.0
    daily_trade_count: int = 0
    last_trade_at: datetime | None = None
    paper_trading_permission: bool = True
    live_trading_permission: bool = False
    event_blackout: bool = False
    session_allowed: bool = True
    broker_quantity: int = 0
    duplicate_order_intent_ids: tuple[str, ...] = ()
    existing_position_symbols: tuple[str, ...] = ()
    max_quote_age_seconds: int = 60


@dataclass(frozen=True)
class MetaStrategyExecutionPipelineResult:
    mode: MetaStrategyPipelineMode
    stage_sequence: tuple[str, ...]
    stage_results: dict[str, Any]
    snapshot: MetaStrategyMarketSnapshot
    deterministic_candidate: GeneratedDeterministicCandidate
    geometry: CandidateGeometryResult
    features: MetaStrategyFeatureSet
    inference: MetaStrategyInferenceResult
    local_gates: MetaStrategyLocalGateEvaluation
    dynamic_profile: MetaStrategyDynamicProfile
    sizing: MetaStrategySizingResult
    order_intent: MetaOrderIntent | None
    global_risk: dict[str, Any]
    order_validation: MetaStrategyOrderValidationResult
    broker_result: dict[str, Any]
    persistence_result: dict[str, Any]
    reconciliation: MetaStrategyReconciliationRecord | None
    final_valid: bool
    reason_codes: tuple[str, ...]


@dataclass
class _PipelineState:
    request: MetaStrategyExecutionPipelineRequest
    config: MetaStrategyExecutionPipelineConfig
    broker: MetaStrategyBrokerAdapter
    persistence: MetaStrategyPersistenceAdapter
    global_risk_adapter: MetaStrategyGlobalRiskAdapter
    stage_results: dict[str, Any] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)
    snapshot: MetaStrategyMarketSnapshot | None = None
    deterministic_candidate: GeneratedDeterministicCandidate | None = None
    geometry: CandidateGeometryResult | None = None
    features: MetaStrategyFeatureSet | None = None
    inference: MetaStrategyInferenceResult | None = None
    local_gates: MetaStrategyLocalGateEvaluation | None = None
    dynamic_profile: MetaStrategyDynamicProfile | None = None
    sizing: MetaStrategySizingResult | None = None
    order_intent: MetaOrderIntent | None = None
    global_risk: dict[str, Any] | None = None
    order_validation: MetaStrategyOrderValidationResult | None = None
    broker_result: dict[str, Any] | None = None
    persistence_result: dict[str, Any] | None = None
    reconciliation: MetaStrategyReconciliationRecord | None = None
    final_valid: bool = False


class InMemoryMetaStrategyPersistenceAdapter:
    def persist(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "PERSISTED",
            "recordId": f"meta_strategy.pipeline.{payload.get('decisionId', 'unknown')}",
            "stageCount": len(payload.get("stageSequence") or ()),
            "reasonCodes": ("meta_strategy.pipeline.persisted",),
        }


def run_meta_strategy_execution_pipeline(
    request: MetaStrategyExecutionPipelineRequest,
    *,
    config: MetaStrategyExecutionPipelineConfig | None = None,
    broker_adapter: MetaStrategyBrokerAdapter | None = None,
    persistence_adapter: MetaStrategyPersistenceAdapter | None = None,
    global_risk_adapter: MetaStrategyGlobalRiskAdapter | None = None,
) -> MetaStrategyExecutionPipelineResult:
    state = _PipelineState(
        request=request,
        config=config or MetaStrategyExecutionPipelineConfig(),
        broker=broker_adapter or NoopMetaStrategyBrokerAdapter(),
        persistence=persistence_adapter or InMemoryMetaStrategyPersistenceAdapter(),
        global_risk_adapter=global_risk_adapter or ReadOnlyMetaStrategyGlobalRiskAdapter(),
    )
    for stage in META_STRATEGY_EXECUTION_PIPELINE_STAGES:
        _STAGE_HANDLERS[stage](state)
    return _build_result(state)


def pipeline_modes_using_authoritative_sequence() -> dict[str, tuple[str, ...]]:
    return {
        mode: META_STRATEGY_EXECUTION_PIPELINE_STAGES
        for mode in ("EVALUATION", "SHADOW", "PAPER", "BACKTEST", "DAILY_REPLAY", "DIAGNOSTICS", "LIVE")
    }


def _stage_market_snapshot(state: _PipelineState) -> None:
    state.snapshot = build_meta_strategy_market_snapshot(state.request.snapshot_request)
    _record(state, "market_snapshot", {"snapshotId": state.snapshot.snapshot_id, "symbol": state.snapshot.symbol})


def _stage_passive(name: str, payload: dict[str, Any] | None = None) -> Callable[[_PipelineState], None]:
    def handler(state: _PipelineState) -> None:
        _record(state, name, payload or {"status": "captured_by_deterministic_candidate_stage"})

    return handler


def _stage_deterministic_candidate(state: _PipelineState) -> None:
    state.deterministic_candidate = generate_deterministic_candidate(_require(state.snapshot, "snapshot"))
    state.reason_codes.extend(state.deterministic_candidate.reason_codes)
    _record(
        state,
        "deterministic_candidate",
        {
            "direction": state.deterministic_candidate.direction,
            "confidence": state.deterministic_candidate.deterministic_confidence,
            "edge": state.deterministic_candidate.edge,
        },
    )


def _stage_candidate_geometry(state: _PipelineState) -> None:
    state.geometry = calculate_candidate_geometry(
        _require(state.snapshot, "snapshot"),
        _require(state.deterministic_candidate, "deterministic_candidate").deterministic_candidate,
    )
    state.reason_codes.extend(state.geometry.reason_codes)
    _record(state, "candidate_geometry", {"side": state.geometry.geometry.side, "quantity": state.geometry.geometry.quantity})


def _stage_feature_builder(state: _PipelineState) -> None:
    candidate = _require(state.deterministic_candidate, "deterministic_candidate")
    snapshot = _require(state.snapshot, "snapshot")
    geometry = _require(state.geometry, "geometry")
    state.features = build_meta_strategy_features(
        {
            "algorithmId": "meta_strategy",
            "id": snapshot.decision_id,
            "decisionId": snapshot.decision_id,
            "snapshotId": snapshot.snapshot_id,
            "symbol": snapshot.symbol,
            "timestamp": snapshot.timestamp.isoformat(),
            "deterministicCandidate": {
                "direction": candidate.direction,
                "confidence": candidate.deterministic_confidence,
                "edge": candidate.edge,
                "supportingFamilies": candidate.supporting_families,
                "opposingFamilies": candidate.opposing_families,
            },
            "familyScores": candidate.evidence.get("familyAggregation", {}).get("familyScores", ()),
            "selectedValues": {
                "candidate_direction": candidate.direction,
                "candidate_edge": candidate.edge,
                "candidate_confidence": candidate.deterministic_confidence,
                "expected_net_reward_risk": geometry.expected_net_reward_risk or 0.0,
                "spread_bps": snapshot.spread_bps or 0.0,
                "liquidity": (snapshot.liquidity or {}).get("dollarVolume", snapshot.volume),
                "target_distance": geometry.target_distance,
                "stop_distance": geometry.stop_distance,
                "expected_transaction_cost": geometry.estimated_cost,
            },
        }
    )
    _record(state, "feature_builder", {"schemaHash": state.features.schemaHash, "missingness": state.features.missingnessRatio})


def _stage_artifact_validation(state: _PipelineState) -> None:
    artifact = state.request.model_artifact or {}
    expected = _require(state.features, "features").schemaHash
    compatible = bool(artifact) and str(artifact.get("featureSchemaHash") or "") == expected
    _record(state, "artifact_validation", {"compatible": compatible, "expectedFeatureSchemaHash": expected})


def _stage_model_inference(state: _PipelineState) -> None:
    mode_config = _inference_config_for_mode(state)
    state.inference = apply_meta_strategy_inference(
        deterministic_signal=_require(state.deterministic_candidate, "deterministic_candidate").direction,
        feature_set=_require(state.features, "features"),
        model_artifact=state.request.model_artifact,
        config=mode_config,
        hard_gates_passed=True,
        candidate_eligible=_require(state.deterministic_candidate, "deterministic_candidate").deterministic_candidate.eligible,
        deterministic_risk_multiplier=1.0,
        session_date=state.request.snapshot_request.decision_timestamp.date(),
        predicted_at=state.request.snapshot_request.decision_timestamp,
    )
    state.reason_codes.extend(state.inference.reasonCodes)
    _record(state, "model_inference", {"finalSignal": state.inference.finalSignal, "decisionAction": state.inference.decisionAction})


def _stage_ml_decision_policy(state: _PipelineState) -> None:
    inference = _require(state.inference, "inference")
    _record(state, "ml_decision_policy", {"decisionAction": inference.decisionAction, "appliedToOrder": inference.appliedToOrder})


def _stage_local_gates(state: _PipelineState) -> None:
    request = state.request
    snapshot = _require(state.snapshot, "snapshot")
    candidate = _require(state.deterministic_candidate, "deterministic_candidate")
    inference = _require(state.inference, "inference")
    geometry = _require(state.geometry, "geometry")
    state.local_gates = evaluate_meta_strategy_local_gates(
        MetaStrategyLocalGateContext(
            timestamp=snapshot.timestamp,
            proposed_quantity=1 if inference.finalSignal in {"BUY", "SELL"} else 0,
            active_strategy_count=candidate.evidence.get("familyAggregation", {}).get("activeStrategyCount", 0),
            independent_family_count=candidate.evidence.get("familyAggregation", {}).get("activeFamilyCount", 0),
            deterministic_score=candidate.deterministic_confidence,
            deterministic_edge=candidate.edge,
            calibrated_success_probability=inference.calibratedProbability or inference.probabilityOfSuccess or 0.0,
            uncertainty=inference.uncertainty or 1.0,
            missingness=inference.featureMissingness,
            ood_score=inference.outOfDistributionScore or 0.0,
            model_health_score=float((inference.modelHealth or {}).get("score", 0.0)),
            reward_risk_after_costs=geometry.expected_net_reward_risk or 0.0,
            spread_bps=snapshot.spread_bps or 0.0,
            liquidity=float((snapshot.liquidity or {}).get("dollarVolume") or snapshot.volume),
            realized_daily_pnl=request.realized_daily_pnl,
            daily_trade_count=request.daily_trade_count,
            last_trade_at=request.last_trade_at,
            event_blackout=request.event_blackout,
            session_phase=snapshot.session_phase,
            execution_mode="LIVE" if request.mode == "LIVE" else "PAPER",
            paper_trading_permission=request.paper_trading_permission,
            live_trading_permission=request.live_trading_permission and state.config.live_trading_enabled,
        )
    )
    state.reason_codes.extend(state.local_gates.reason_codes)
    _record(state, "local_gates", {"passed": state.local_gates.passed, "approvedQuantity": state.local_gates.approved_quantity})


def _stage_dynamic_profile(state: _PipelineState) -> None:
    snapshot = _require(state.snapshot, "snapshot")
    inference = _require(state.inference, "inference")
    state.dynamic_profile = resolve_meta_strategy_dynamic_profile(
        state.config.baseline_settings,
        MetaStrategyDynamicProfileContext(
            timestamp=snapshot.timestamp,
            volatility_level=_volatility_level(snapshot),
            liquidity_level=_liquidity_level(snapshot),
            spread_bps=snapshot.spread_bps or 0.0,
            event_blackout=state.request.event_blackout,
            session_allowed=state.request.session_allowed,
            model_health_score=float((inference.modelHealth or {}).get("score", 0.0)),
            missingness=inference.featureMissingness,
            ood_score=inference.outOfDistributionScore or 0.0,
        ),
    )
    state.reason_codes.extend(state.dynamic_profile.reason_codes)
    _record(state, "dynamic_profile", {"profileId": state.dynamic_profile.profile_id})


def _stage_sizing(state: _PipelineState) -> None:
    request = state.request
    config = state.config
    snapshot = _require(state.snapshot, "snapshot")
    inference = _require(state.inference, "inference")
    geometry = _require(state.geometry, "geometry")
    profile = _require(state.dynamic_profile, "dynamic_profile")
    local_gates = _require(state.local_gates, "local_gates")
    entry = geometry.entry_reference or snapshot.last_price
    stop_distance = geometry.stop_distance if geometry.stop_distance > 0 else max(0.0, abs(entry - (geometry.geometry.stop_price or entry)))
    state.sizing = calculate_meta_strategy_position_size(
        MetaStrategySizingContext(
            side=inference.finalSignal if inference.finalSignal in {"BUY", "SELL"} else "HOLD",
            candidate_accepted=inference.candidateAccepted,
            local_gates_passed=local_gates.passed,
            baseline_settings=config.baseline_settings,
            effective_settings=profile.effective_settings,
            model_risk_multiplier=inference.recommendedRiskMultiplier,
            account_equity=request.account_equity or config.default_account_equity,
            available_buying_power=request.available_buying_power or config.default_buying_power,
            entry_price=entry,
            stop_distance=stop_distance,
            market_liquidity=float((snapshot.liquidity or {}).get("shareVolume") or snapshot.volume),
            remaining_algorithm_risk=request.remaining_algorithm_risk or config.default_remaining_algorithm_risk,
            global_available_risk=request.global_available_risk or config.default_global_available_risk,
            global_quantity_cap=request.global_quantity_cap if request.global_quantity_cap is not None else config.default_global_quantity_cap,
        )
    )
    state.reason_codes.extend(state.sizing.reason_codes)
    _record(state, "sizing", {"quantity": state.sizing.quantity, "limitingCap": state.sizing.limiting_cap})


def _stage_order_intent(state: _PipelineState) -> None:
    sizing = _require(state.sizing, "sizing")
    inference = _require(state.inference, "inference")
    geometry = _require(state.geometry, "geometry")
    snapshot = _require(state.snapshot, "snapshot")
    result = build_meta_strategy_order_intent(
        snapshot=snapshot,
        side=inference.finalSignal,
        quantity=sizing.quantity,
        stop_price=geometry.geometry.stop_price,
    )
    state.order_intent = result.intent
    state.reason_codes.extend(result.reason_codes)
    _record(
        state,
        "order_intent",
        {
            "status": result.status,
            "quantity": getattr(result.intent, "quantity", 0),
            "reasonCodes": result.reason_codes,
        },
    )


def _stage_final_validation(state: _PipelineState) -> None:
    snapshot = _require(state.snapshot, "snapshot")
    sizing = _require(state.sizing, "sizing")
    geometry = _require(state.geometry, "geometry")
    inference = _require(state.inference, "inference")
    global_risk = state.global_risk or {}
    approved_quantity = int(global_risk.get("approvedQuantity") or 0)
    entry = geometry.entry_reference or snapshot.last_price
    stop_distance = geometry.stop_distance if geometry.stop_distance > 0 else abs(entry - (geometry.geometry.stop_price or entry))
    state.order_validation = validate_meta_strategy_order(
        MetaStrategyOrderValidationContext(
            order_intent=state.order_intent,
            snapshot=snapshot,
            model_action=inference.decisionAction,
            deterministic_direction=_require(state.deterministic_candidate, "deterministic_candidate").direction,
            final_direction=inference.finalSignal,
            sizing_quantity=sizing.quantity,
            global_approved_quantity=approved_quantity,
            entry_price=entry,
            stop_price=geometry.geometry.stop_price,
            target_price=geometry.geometry.target_price,
            reward_risk=geometry.geometry.risk_reward,
            available_buying_power=state.request.available_buying_power or state.config.default_buying_power,
            reserved_risk_dollars=approved_quantity * stop_distance,
            maximum_reserved_risk_dollars=state.request.global_available_risk or state.config.default_global_available_risk,
            session_allowed=state.request.session_allowed and not (state.request.mode == "LIVE" and not state.config.live_trading_enabled),
            max_quote_age_seconds=state.request.max_quote_age_seconds,
            max_spread_bps=state.dynamic_profile.effective_settings.spread_limit_bps if state.dynamic_profile else 15.0,
            minimum_liquidity=state.dynamic_profile.effective_settings.liquidity_requirement if state.dynamic_profile else 0.0,
            duplicate_intent_ids=state.request.duplicate_order_intent_ids,
            existing_position_symbols=state.request.existing_position_symbols,
        )
    )
    if state.request.mode == "LIVE" and not state.config.live_trading_enabled:
        state.reason_codes.append("meta_strategy.pipeline.live_trading_not_enabled")
    state.reason_codes.extend(state.order_validation.reason_codes)
    state.final_valid = state.order_validation.valid
    if not state.order_validation.valid:
        state.order_intent = None
        state.reason_codes.append("meta_strategy.pipeline.invalid_order_blocked_before_broker")
    _record(state, "final_validation", state.order_validation.persisted_payload)


def _stage_global_risk(state: _PipelineState) -> None:
    sizing = _require(state.sizing, "sizing")
    global_result = state.global_risk_adapter.apply(state.order_intent, requested_quantity=sizing.quantity)
    approved = int(global_result.get("approvedQuantity") or 0)
    if state.order_intent is not None and approved < int(state.order_intent.quantity):
        state.order_intent = _order_with_quantity(state.order_intent, approved) if approved > 0 else None
        state.reason_codes.append("meta_strategy.pipeline.global_risk_reduced_quantity")
    state.global_risk = global_result
    _record(state, "global_risk", global_result)


def _stage_broker_adapter(state: _PipelineState) -> None:
    state.broker_result = state.broker.submit(state.order_intent, mode=state.request.mode)
    state.reason_codes.extend(tuple(state.broker_result.get("reasonCodes") or ()))
    _record(state, "broker_adapter", state.broker_result)


def _stage_persistence(state: _PipelineState) -> None:
    snapshot = _require(state.snapshot, "snapshot")
    state.persistence_result = state.persistence.persist(
        {
            "algorithmId": "meta_strategy",
            "decisionId": snapshot.decision_id,
            "mode": state.request.mode,
            "stageSequence": META_STRATEGY_EXECUTION_PIPELINE_STAGES,
            "stageResults": state.stage_results,
            "reasonCodes": tuple(dict.fromkeys(state.reason_codes)),
        }
    )
    state.reason_codes.extend(tuple(state.persistence_result.get("reasonCodes") or ()))
    _record(state, "persistence", state.persistence_result)


def _stage_reconciliation(state: _PipelineState) -> None:
    if state.order_intent is None:
        state.reconciliation = None
        _record(state, "reconciliation", {"status": "NO_POSITION"})
        return
    filled = int(state.broker_result.get("filledQuantity") or state.request.broker_quantity or 0) if state.broker_result else state.request.broker_quantity
    geometry = _require(state.geometry, "geometry")
    state.reconciliation = reconcile_meta_strategy_broker_fill(
        planned_quantity=int(state.order_intent.quantity),
        filled_quantity=filled,
        position_id=f"meta_strategy.position.{state.order_intent.order_intent_id}",
        symbol=state.order_intent.symbol,
        side=state.order_intent.side,
        average_fill_price=geometry.entry_reference or _require(state.snapshot, "snapshot").last_price,
        filled_at=_require(state.snapshot, "snapshot").timestamp,
        protective_stop=geometry.geometry.stop_price or 0.0,
        profit_target=geometry.geometry.target_price or 0.0,
        maximum_holding_minutes=geometry.maximum_holding_minutes or 1,
    )
    state.reason_codes.extend(state.reconciliation.reason_codes)
    _record(state, "reconciliation", state.reconciliation.as_pipeline_result())


def _build_result(state: _PipelineState) -> MetaStrategyExecutionPipelineResult:
    return MetaStrategyExecutionPipelineResult(
        mode=state.request.mode,
        stage_sequence=META_STRATEGY_EXECUTION_PIPELINE_STAGES,
        stage_results=dict(state.stage_results),
        snapshot=_require(state.snapshot, "snapshot"),
        deterministic_candidate=_require(state.deterministic_candidate, "deterministic_candidate"),
        geometry=_require(state.geometry, "geometry"),
        features=_require(state.features, "features"),
        inference=_require(state.inference, "inference"),
        local_gates=_require(state.local_gates, "local_gates"),
        dynamic_profile=_require(state.dynamic_profile, "dynamic_profile"),
        sizing=_require(state.sizing, "sizing"),
        order_intent=state.order_intent,
        global_risk=state.global_risk or {},
        order_validation=_require(state.order_validation, "order_validation"),
        broker_result=state.broker_result or {},
        persistence_result=state.persistence_result or {},
        reconciliation=state.reconciliation,
        final_valid=state.final_valid,
        reason_codes=tuple(dict.fromkeys(state.reason_codes)),
    )


def _record(state: _PipelineState, stage: str, payload: dict[str, Any]) -> None:
    state.stage_results[stage] = payload


def _require(value: Any, name: str) -> Any:
    if value is None:
        raise RuntimeError(f"Meta-Strategy pipeline missing required stage output: {name}")
    return value


def _inference_config_for_mode(state: _PipelineState) -> MetaStrategyInferenceConfig:
    if state.request.mode == "SHADOW":
        return MetaStrategyInferenceConfig(**{**state.config.inference_config.__dict__, "mode": "SHADOW"})
    if state.request.mode in {"EVALUATION", "BACKTEST", "DAILY_REPLAY", "DIAGNOSTICS"}:
        return MetaStrategyInferenceConfig(**{**state.config.inference_config.__dict__, "mode": "FILTER"})
    if state.request.mode == "LIVE" and not state.config.live_trading_enabled:
        return MetaStrategyInferenceConfig(**{**state.config.inference_config.__dict__, "mode": "DISABLED"})
    return state.config.inference_config


def _volatility_level(snapshot: MetaStrategyMarketSnapshot) -> Literal["LOW", "NORMAL", "HIGH", "EXTREME"]:
    atr_percent = float((snapshot.atr or {}).get("1m") or 0.0) / max(snapshot.last_price, 0.000001)
    if atr_percent >= 0.05:
        return "EXTREME"
    if atr_percent >= 0.02:
        return "HIGH"
    if atr_percent <= 0.002:
        return "LOW"
    return "NORMAL"


def _liquidity_level(snapshot: MetaStrategyMarketSnapshot) -> Literal["POOR", "NORMAL", "GOOD"]:
    volume = float(snapshot.volume or 0.0)
    if volume < 10_000:
        return "POOR"
    if volume > 100_000:
        return "GOOD"
    return "NORMAL"


def _order_with_quantity(order: MetaOrderIntent, quantity: int) -> MetaOrderIntent:
    return MetaOrderIntent(**{**order.model_dump(mode="python"), "quantity": float(quantity)})


_STAGE_HANDLERS: dict[str, Callable[[_PipelineState], None]] = {
    "market_snapshot": _stage_market_snapshot,
    "strategies": _stage_passive("strategies"),
    "context_and_regime": _stage_passive("context_and_regime"),
    "safety": _stage_passive("safety"),
    "family_aggregation": _stage_passive("family_aggregation"),
    "deterministic_candidate": _stage_deterministic_candidate,
    "candidate_geometry": _stage_candidate_geometry,
    "feature_builder": _stage_feature_builder,
    "artifact_validation": _stage_artifact_validation,
    "model_inference": _stage_model_inference,
    "ml_decision_policy": _stage_ml_decision_policy,
    "local_gates": _stage_local_gates,
    "dynamic_profile": _stage_dynamic_profile,
    "sizing": _stage_sizing,
    "order_intent": _stage_order_intent,
    "global_risk": _stage_global_risk,
    "final_validation": _stage_final_validation,
    "broker_adapter": _stage_broker_adapter,
    "persistence": _stage_persistence,
    "reconciliation": _stage_reconciliation,
}


__all__ = [
    "InMemoryMetaStrategyPersistenceAdapter",
    "META_STRATEGY_EXECUTION_PIPELINE_STAGES",
    "MetaStrategyBrokerAdapter",
    "MetaStrategyExecutionPipelineConfig",
    "MetaStrategyExecutionPipelineRequest",
    "MetaStrategyExecutionPipelineResult",
    "MetaStrategyGlobalRiskAdapter",
    "MetaStrategyPersistenceAdapter",
    "MetaStrategyPipelineMode",
    "NoopMetaStrategyBrokerAdapter",
    "ReadOnlyMetaStrategyGlobalRiskAdapter",
    "pipeline_modes_using_authoritative_sequence",
    "run_meta_strategy_execution_pipeline",
]
