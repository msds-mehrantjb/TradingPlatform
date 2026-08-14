"""Authoritative Meta-Strategy execution pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from time import perf_counter
from typing import Any, Callable, Literal, Protocol

from backend.app.algorithms.meta_strategy.broker_adapter import MetaStrategyBrokerAdapter, NoopMetaStrategyBrokerAdapter
from backend.app.algorithms.meta_strategy.candidate_generator import (
    CandidateComponentEvaluation,
    GeneratedDeterministicCandidate,
    evaluate_candidate_components,
    generate_deterministic_candidate,
)
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
from backend.app.algorithms.meta_strategy.inference.artifact_health import artifact_schema_compatible
from backend.app.algorithms.meta_strategy.local_gates import (
    MetaStrategyLocalGateConfig,
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
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettings, build_meta_strategy_settings
from backend.app.algorithms.meta_strategy.strategies.base import SnapshotEvaluationResult
from backend.app.algorithms.meta_strategy.strategy_registry import (
    CONTEXT_STRATEGIES,
    DIRECTIONAL_STRATEGIES,
    REGIME_STRATEGIES,
    SAFETY_STRATEGIES,
    MetaStrategyRegistryEntry,
)


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

META_STRATEGY_STRATEGIES_STAGE_VERSION = "meta_strategy_strategies_stage_v1"
META_STRATEGY_CONTEXT_REGIME_STAGE_VERSION = "meta_strategy_context_regime_stage_v1"
META_STRATEGY_SAFETY_STAGE_VERSION = "meta_strategy_safety_stage_v1"
META_STRATEGY_FAMILY_AGGREGATION_STAGE_VERSION = "meta_strategy_family_aggregation_stage_v1"


class MetaStrategyPersistenceAdapter(Protocol):
    def persist(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class MetaStrategyExecutionPipelineConfig:
    settings: MetaStrategySettings = field(default_factory=lambda: build_meta_strategy_settings(status="ACTIVE"))
    inference_config: MetaStrategyInferenceConfig = field(default_factory=lambda: MetaStrategyInferenceConfig(mode="SHADOW", fallbackBehavior="NO_TRADE"))
    baseline_settings: MetaStrategyBaselineSettings = field(default_factory=meta_strategy_baseline_settings)
    live_trading_enabled: bool = False
    default_account_equity: float = 100_000.0
    default_buying_power: float = 100_000.0
    default_remaining_algorithm_risk: float = 1_000.0
    default_global_available_risk: float = 1_000.0
    default_global_quantity_cap: int = 10_000
    configuration_hash: str = "meta_strategy_execution_pipeline_v1"
    submit_to_broker: bool = True


@dataclass(frozen=True)
class MetaStrategyExecutionPipelineRequest:
    mode: MetaStrategyPipelineMode
    snapshot_request: MetaStrategyMarketSnapshotRequest
    model_artifact: dict[str, Any] | None = None
    settings_version: str | None = None
    active_settings_version: str | None = None
    inventory_snapshot: Mapping[str, Any] | None = None
    reserved_risk_ledger: tuple[Mapping[str, Any], ...] = ()
    account_snapshot: Mapping[str, Any] | None = None
    global_risk_snapshot: Mapping[str, Any] | None = None
    event_state: Mapping[str, Any] | None = None
    operational_health: Mapping[str, Any] | None = None
    operational_controls: Mapping[str, Any] | None = None
    runtime_health: Mapping[str, Any] | None = None
    market_clock_state: Mapping[str, Any] | None = None
    paper_control_state: Mapping[str, Any] | None = None
    state_source_versions: Mapping[str, Any] | None = None
    state_source_timestamps: Mapping[str, Any] | None = None
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
    settings_version: str
    effective_settings_hash: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class MetaStrategyStageContractResult:
    status: str
    eligible: bool
    input_version: str
    output_version: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    reason_codes: tuple[str, ...]
    evidence: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "eligible": self.eligible,
            "inputVersion": self.input_version,
            "outputVersion": self.output_version,
            "startedAt": self.started_at.isoformat(),
            "completedAt": self.completed_at.isoformat(),
            "durationMs": self.duration_ms,
            "reasonCodes": self.reason_codes,
            "evidence": _plain_pipeline_value(self.evidence),
        }


@dataclass(frozen=True)
class MetaStrategyStrategyStageOutput:
    strategy_id: str
    strategy_version: str
    family_id: str
    signal: Literal["BUY", "SELL", "HOLD"]
    confidence: float
    eligible: bool
    data_quality: str
    evidence: dict[str, Any]
    vetoes: tuple[str, ...]
    reason_codes: tuple[str, ...]
    evaluated_at: datetime

    def as_payload(self) -> dict[str, Any]:
        return {
            "strategyId": self.strategy_id,
            "strategyVersion": self.strategy_version,
            "familyId": self.family_id,
            "signal": self.signal,
            "confidence": self.confidence,
            "eligible": self.eligible,
            "dataQuality": self.data_quality,
            "evidence": _plain_pipeline_value(self.evidence),
            "vetoes": self.vetoes,
            "reasonCodes": self.reason_codes,
            "evaluatedAt": self.evaluated_at.isoformat(),
        }


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
    candidate_components: CandidateComponentEvaluation | None = None
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


@dataclass(frozen=True)
class _PipelineLocalRiskState:
    account_equity: float | None
    buying_power: float | None
    allocated_capital: float | None
    realized_daily_pnl: float
    unrealized_pnl: float
    reserved_risk: float
    remaining_local_risk: float | None
    global_available_risk: float | None
    global_quantity_cap: int | None
    daily_trade_count: int
    last_trade_at: datetime | None
    existing_position_symbols: tuple[str, ...]
    existing_symbol_exposure: float
    missing_reason_codes: tuple[str, ...]


class InMemoryMetaStrategyPersistenceAdapter:
    def persist(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "PERSISTED",
            "recordId": f"meta_strategy.pipeline.{payload.get('decisionId', 'unknown')}",
            "stageCount": len(payload.get("stageSequence") or ()),
            "settingsVersion": payload.get("settingsVersion"),
            "effectiveSettingsHash": payload.get("effectiveSettingsHash"),
            "reasonCodes": ("meta_strategy.pipeline.persisted",),
        }


def run_meta_strategy_execution_pipeline(
    request: MetaStrategyExecutionPipelineRequest,
    *,
    config: MetaStrategyExecutionPipelineConfig | None = None,
    broker_adapter: MetaStrategyBrokerAdapter | None = None,
    persistence_adapter: MetaStrategyPersistenceAdapter | None = None,
    global_risk_adapter: MetaStrategyGlobalRiskAdapter | None = None,
    config_settings: MetaStrategySettings | None = None,
) -> MetaStrategyExecutionPipelineResult:
    active_config = config or MetaStrategyExecutionPipelineConfig()
    if config_settings is not None:
        active_config = replace(
            active_config,
            settings=config_settings,
            baseline_settings=config_settings.to_baseline_settings(),
        )
    state = _PipelineState(
        request=request,
        config=active_config,
        broker=broker_adapter or NoopMetaStrategyBrokerAdapter(),
        persistence=persistence_adapter or InMemoryMetaStrategyPersistenceAdapter(),
        global_risk_adapter=global_risk_adapter or ReadOnlyMetaStrategyGlobalRiskAdapter(),
    )
    for stage in META_STRATEGY_EXECUTION_PIPELINE_STAGES:
        started = perf_counter()
        _STAGE_HANDLERS[stage](state)
        state.stage_results.setdefault(stage, {}).setdefault("durationMs", int((perf_counter() - started) * 1000))
    return _build_result(state)


def pipeline_modes_using_authoritative_sequence() -> dict[str, tuple[str, ...]]:
    return {
        mode: META_STRATEGY_EXECUTION_PIPELINE_STAGES
        for mode in ("EVALUATION", "SHADOW", "PAPER", "BACKTEST", "DAILY_REPLAY", "DIAGNOSTICS", "LIVE")
    }


def _stage_market_snapshot(state: _PipelineState) -> None:
    snapshot = build_meta_strategy_market_snapshot(state.request.snapshot_request)
    state.snapshot = snapshot.model_copy(
        update={
            "settings_version": state.config.settings.settings_version,
            "effective_settings_hash": state.config.settings.effective_settings_hash,
        }
    )
    _record(
        state,
        "market_snapshot",
        {
            "snapshotId": state.snapshot.snapshot_id,
            "symbol": state.snapshot.symbol,
            "decisionTimestamp": state.snapshot.timestamp.isoformat(),
            "authoritativeState": _authoritative_state_evidence(state.request),
            "sourceVersions": dict(state.request.state_source_versions or {}),
            "sourceTimestamps": dict(state.request.state_source_timestamps or {}),
        },
    )


def _stage_strategies(state: _PipelineState) -> None:
    components = _candidate_components(state)
    snapshot = _require(state.snapshot, "snapshot")
    outputs = tuple(
        _strategy_stage_output(output, entry, snapshot)
        for output, entry in zip(components.directional_outputs, DIRECTIONAL_STRATEGIES, strict=True)
    )
    active_outputs = tuple(
        item
        for item, entry in zip(outputs, DIRECTIONAL_STRATEGIES, strict=True)
        if entry.enabled
    )
    eligible = bool(active_outputs) and any(item.eligible and item.data_quality == "OK" for item in active_outputs)
    reason_codes = _stage_reason_codes(
        *(item.reason_codes for item in outputs),
        extra=() if eligible else ("meta_strategy.strategies.no_eligible_directional_strategy",),
    )
    _record_stage_contract(
        state,
        "strategies",
        status="PASS" if eligible else "BLOCKED",
        eligible=eligible,
        input_version=_stage_input_version(snapshot),
        output_version=META_STRATEGY_STRATEGIES_STAGE_VERSION,
        reason_codes=reason_codes,
        evidence={
            "strategyOutputs": tuple(item.as_payload() for item in outputs),
            "activeStrategyCount": sum(1 for item in active_outputs if item.eligible and item.signal in {"BUY", "SELL"}),
            "configuredStrategyCount": len(outputs),
            "activeConfiguredStrategyCount": len(active_outputs),
            "catalogStrategyIds": tuple(entry.strategy_id for entry in DIRECTIONAL_STRATEGIES),
            "genericSessionDirectionFallbackUsed": False,
        },
    )


def _stage_context_and_regime(state: _PipelineState) -> None:
    components = _candidate_components(state)
    snapshot = _require(state.snapshot, "snapshot")
    context_outputs = tuple(
        _strategy_stage_output(output, entry, snapshot)
        for output, entry in zip(components.context_outputs, CONTEXT_STRATEGIES, strict=True)
    )
    regime_outputs = tuple(
        _strategy_stage_output(output, entry, snapshot)
        for output, entry in zip(components.regime_outputs, REGIME_STRATEGIES, strict=True)
    )
    regime_evidence = tuple((output.evidence or {}) for output in components.regime_outputs)
    trend_regime = _first_evidence_value(regime_evidence, "regimeLabel", default="UNKNOWN")
    volatility_regime = _first_evidence_value(regime_evidence, "volatility", default=_volatility_level(snapshot))
    event_state = str((snapshot.economic_event_state or {}).get("state") or "none").upper()
    restricted = _restricted_families_from_regime(components.regime_outputs, components.safety_blocks)
    allowed = tuple(family for family in ("TREND", "BREAKOUT", "REVERSAL", "MEAN_REVERSION", "GAP_SESSION", "EVENT_DRIVEN") if family not in restricted)
    eligible = all(item.eligible for item in context_outputs) and all(item.eligible for item in regime_outputs)
    reason_codes = _stage_reason_codes(
        *(item.reason_codes for item in (*context_outputs, *regime_outputs)),
        extra=() if eligible else ("meta_strategy.context_regime.ineligible",),
    )
    _record_stage_contract(
        state,
        "context_and_regime",
        status="PASS" if eligible else "BLOCKED",
        eligible=eligible,
        input_version=_stage_input_version(snapshot),
        output_version=META_STRATEGY_CONTEXT_REGIME_STAGE_VERSION,
        reason_codes=reason_codes,
        evidence={
            "sessionPhase": snapshot.session_phase,
            "trendRegime": trend_regime,
            "volatilityRegime": volatility_regime,
            "liquidityRegime": _liquidity_level(snapshot),
            "eventRegime": event_state,
            "dataQualityRegime": "OK" if eligible else "DEGRADED",
            "allowedStrategyFamilies": allowed,
            "restrictedStrategyFamilies": restricted,
            "contextOutputs": tuple(item.as_payload() for item in context_outputs),
            "regimeOutputs": tuple(item.as_payload() for item in regime_outputs),
        },
    )


def _stage_safety(state: _PipelineState) -> None:
    components = _candidate_components(state)
    snapshot = _require(state.snapshot, "snapshot")
    outputs = tuple(
        _strategy_stage_output(output, entry, snapshot)
        for output, entry in zip(components.safety_outputs, SAFETY_STRATEGIES, strict=True)
    )
    hard_vetoes = list(_explicit_safety_vetoes(state))
    hard_vetoes.extend(code for output in components.safety_blockers for code in output.reason_codes)
    eligible = not hard_vetoes
    reason_codes = _stage_reason_codes(
        *(item.reason_codes for item in outputs),
        extra=tuple(hard_vetoes) if hard_vetoes else ("meta_strategy.safety.pass",),
    )
    _record_stage_contract(
        state,
        "safety",
        status="PASS" if eligible else "BLOCKED",
        eligible=eligible,
        input_version=_stage_input_version(snapshot),
        output_version=META_STRATEGY_SAFETY_STAGE_VERSION,
        reason_codes=reason_codes,
        evidence={
            "hardVetoes": tuple(dict.fromkeys(hard_vetoes)),
            "safetyOutputs": tuple(item.as_payload() for item in outputs),
            "checks": {
                "dataCompleteness": _required_market_data_complete(state),
                "dataFreshness": _quote_fresh(state),
                "marketSessionPermission": state.request.session_allowed,
                "eventBlackout": state.request.event_blackout,
                "spreadBps": snapshot.spread_bps,
                "liquidity": float((snapshot.liquidity or {}).get("dollarVolume") or snapshot.volume),
                "operationalHealth": dict(state.request.operational_health or {}),
                "dailyLossState": state.request.realized_daily_pnl,
                "emergencyControls": dict(state.request.operational_controls or {}) | dict(state.request.runtime_health or {}),
                "unsupportedSymbol": not _symbol_supported(state),
                "unsupportedTimeframe": not _timeframe_supported(state),
                "conflictingPositionState": _has_conflicting_position_state(state),
            },
        },
    )


def _stage_family_aggregation(state: _PipelineState) -> None:
    components = _candidate_components(state)
    snapshot = _require(state.snapshot, "snapshot")
    aggregation = components.aggregation
    supporting, opposing = _family_alignment_from_aggregation(aggregation.signal, aggregation.family_scores)
    winning_score, opposing_score = _winning_scores_from_aggregation(aggregation.signal, aggregation)
    correlation_penalties = {
        item.strategy_id: item.caps_applied
        for item in aggregation.contribution_audit
        if item.caps_applied
    }
    reason_codes = tuple(dict.fromkeys(aggregation.reason_codes))
    _record_stage_contract(
        state,
        "family_aggregation",
        status="PASS" if aggregation.eligible else "BLOCKED",
        eligible=aggregation.eligible,
        input_version=_stage_input_version(snapshot),
        output_version=META_STRATEGY_FAMILY_AGGREGATION_STAGE_VERSION,
        reason_codes=reason_codes,
        evidence={
            "familyScores": {
                score.family: {
                    "buyScore": score.buy_score,
                    "sellScore": score.sell_score,
                    "holdScore": score.hold_score,
                    "activeStrategyCount": score.active_strategy_count,
                    "capped": score.capped,
                }
                for score in aggregation.family_scores
            },
            "activeStrategyCount": aggregation.active_strategy_count,
            "activeFamilyCount": aggregation.active_family_count,
            "supportingFamilies": supporting,
            "opposingFamilies": opposing,
            "correlationPenalties": correlation_penalties,
            "winningScore": winning_score,
            "opposingScore": opposing_score,
            "edge": round(max(0.0, winning_score - opposing_score), 6),
            "eligible": aggregation.eligible,
            "reasonCodes": reason_codes,
            "contributionAudit": {
                item.strategy_id: {
                    "family": item.family,
                    "signal": item.signal,
                    "confidence": item.confidence,
                    "rawContribution": item.raw_contribution,
                    "cappedContribution": item.capped_contribution,
                    "canonicalInfluenceId": item.canonical_influence_id,
                    "correlationKey": item.correlation_key,
                    "counted": item.counted,
                    "capsApplied": item.caps_applied,
                    "reasonCodes": item.reason_codes,
                }
                for item in aggregation.contribution_audit
            },
        },
    )


def _stage_deterministic_candidate(state: _PipelineState) -> None:
    state.deterministic_candidate = generate_deterministic_candidate(
        _require(state.snapshot, "snapshot"),
        settings=state.config.settings,
        components=_candidate_components(state),
    )
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
                "signal": candidate.direction,
                "confidence": candidate.deterministic_confidence,
                "finalScore": candidate.deterministic_confidence,
                "buyConfidence": candidate.winning_score if candidate.direction == "BUY" else candidate.opposing_score,
                "sellConfidence": candidate.winning_score if candidate.direction == "SELL" else candidate.opposing_score,
                "edge": candidate.edge,
                "supportingFamilies": candidate.supporting_families,
                "opposingFamilies": candidate.opposing_families,
            },
            "familyScores": _family_feature_scores(candidate),
            "directionalStrategyOutputs": _directional_feature_outputs(candidate),
            "contextOutputs": _context_feature_outputs(candidate),
            "regimeOutput": _regime_feature_output(candidate),
            "selectedValues": {
                "candidate_direction": candidate.direction,
                "candidate_edge": candidate.edge,
                "candidate_confidence": candidate.deterministic_confidence,
                "expected_net_reward_risk": _number_or_default(geometry.expected_net_reward_risk, 0.0),
                "reward_risk_ratio": geometry.geometry.risk_reward,
                "spread_dollars": (snapshot.spread or {}).get("dollars"),
                "spread_bps": _number_or_default(snapshot.spread_bps, 0.0),
                "liquidity": (snapshot.liquidity or {}).get("dollarVolume", snapshot.volume),
                "relative_volume": (snapshot.relative_volume or {}).get("1m"),
                "estimated_slippage": (geometry.evidence or {}).get("estimatedSlippage", 0.0),
                "target_distance": geometry.target_distance,
                "stop_distance": geometry.stop_distance,
                "expected_transaction_cost": geometry.estimated_cost,
                "current_meta_strategy_virtual_exposure": (snapshot.features or {}).get("metaStrategyVirtualExposure", 0.0),
            },
        }
    )
    _record(state, "feature_builder", {"schemaHash": state.features.schemaHash, "missingness": state.features.missingnessRatio})


def _stage_artifact_validation(state: _PipelineState) -> None:
    artifact = state.request.model_artifact or {}
    expected = _require(state.features, "features").schemaHash
    mode_config = _inference_config_for_mode(state)
    compatible = artifact_schema_compatible(artifact, expected)
    promoted = _artifact_promoted_for_application(artifact)
    model_available = bool(artifact and (artifact.get("models") or {}))
    model_application_allowed = bool(
        compatible
        and promoted
        and model_available
        and mode_config.mode in {"FILTER", "RISK_REDUCTION"}
    )
    shadow_diagnostics_only = bool(artifact and not model_application_allowed and mode_config.mode == "SHADOW")
    deterministic_only = not model_application_allowed and mode_config.mode in {"OFF", "DISABLED"}
    reason_codes = _artifact_validation_reasons(
        artifact=artifact,
        compatible=compatible,
        promoted=promoted,
        model_available=model_available,
        shadow_diagnostics_only=shadow_diagnostics_only,
        deterministic_only=deterministic_only,
    )
    _record(
        state,
        "artifact_validation",
        {
            "status": "PASS" if model_application_allowed else "SHADOW_ONLY" if shadow_diagnostics_only else "DETERMINISTIC_ONLY" if deterministic_only else "FAIL_CLOSED",
            "compatible": compatible,
            "promoted": promoted,
            "modelAvailable": model_available,
            "modelApplicationAllowed": model_application_allowed,
            "shadowDiagnosticsOnly": shadow_diagnostics_only,
            "deterministicOnly": deterministic_only,
            "expectedFeatureSchemaHash": expected,
            "artifactFeatureSchemaHash": str(artifact.get("featureSchemaHash") or ""),
            "artifactId": str(artifact.get("artifactId") or artifact.get("artifact_id") or ""),
            "configuredInferenceMode": mode_config.mode,
            "fallbackBehavior": mode_config.fallbackBehavior,
            "reasonCodes": reason_codes,
        },
    )


def _stage_model_inference(state: _PipelineState) -> None:
    mode_config = _inference_config_for_mode(state)
    hard_stages_passed = _required_entry_stages_passed(state)
    artifact_payload, effective_mode_config = _artifact_for_model_inference(state, mode_config)
    state.inference = apply_meta_strategy_inference(
        deterministic_signal=_require(state.deterministic_candidate, "deterministic_candidate").direction,
        feature_set=_require(state.features, "features"),
        model_artifact=artifact_payload,
        config=effective_mode_config,
        hard_gates_passed=hard_stages_passed,
        candidate_eligible=_require(state.deterministic_candidate, "deterministic_candidate").deterministic_candidate.eligible,
        deterministic_risk_multiplier=1.0,
        session_date=state.request.snapshot_request.decision_timestamp.date(),
        predicted_at=state.request.snapshot_request.decision_timestamp,
    )
    artifact_reasons = tuple((state.stage_results.get("artifact_validation") or {}).get("reasonCodes") or ())
    if artifact_reasons:
        state.reason_codes.extend(artifact_reasons)
    state.reason_codes.extend(state.inference.reasonCodes)
    _record(
        state,
        "model_inference",
        {
            "finalSignal": state.inference.finalSignal,
            "decisionAction": state.inference.decisionAction,
            "hardGatesPassed": state.inference.hardGatesPassed,
            "modelAppliedToOrder": state.inference.appliedToOrder,
            "effectiveMode": state.inference.effectiveMode,
            "artifactValidationStatus": (state.stage_results.get("artifact_validation") or {}).get("status"),
            "artifactReasonCodes": artifact_reasons,
        },
    )


def _stage_ml_decision_policy(state: _PipelineState) -> None:
    inference = _require(state.inference, "inference")
    _record(state, "ml_decision_policy", {"decisionAction": inference.decisionAction, "appliedToOrder": inference.appliedToOrder})


def _stage_local_gates(state: _PipelineState) -> None:
    request = state.request
    snapshot = _require(state.snapshot, "snapshot")
    candidate = _require(state.deterministic_candidate, "deterministic_candidate")
    inference = _require(state.inference, "inference")
    geometry = _require(state.geometry, "geometry")
    risk_state = _pipeline_local_risk_state(state)
    state.local_gates = evaluate_meta_strategy_local_gates(
        MetaStrategyLocalGateContext(
            timestamp=snapshot.timestamp,
            proposed_quantity=1 if inference.finalSignal in {"BUY", "SELL"} else 0,
            active_strategy_count=candidate.evidence.get("familyAggregation", {}).get("activeStrategyCount", 0),
            independent_family_count=candidate.evidence.get("familyAggregation", {}).get("activeFamilyCount", 0),
            deterministic_score=candidate.deterministic_confidence,
            deterministic_edge=candidate.edge,
            calibrated_success_probability=_number_or_default(
                inference.calibratedProbability,
                _number_or_default(inference.probabilityOfSuccess, 0.0),
            ),
            uncertainty=_number_or_default(inference.uncertainty, 1.0),
            missingness=inference.featureMissingness,
            ood_score=_number_or_default(inference.outOfDistributionScore, 0.0),
            model_health_score=float((inference.modelHealth or {}).get("score", 0.0)),
            reward_risk_after_costs=_number_or_default(geometry.expected_net_reward_risk, 0.0),
            spread_bps=_number_or_default(snapshot.spread_bps, 0.0),
            liquidity=float((snapshot.liquidity or {}).get("dollarVolume") or snapshot.volume),
            realized_daily_pnl=risk_state.realized_daily_pnl,
            daily_trade_count=risk_state.daily_trade_count,
            last_trade_at=risk_state.last_trade_at,
            event_blackout=request.event_blackout,
            session_phase=snapshot.session_phase,
            execution_mode="LIVE" if request.mode == "LIVE" else "PAPER",
            paper_trading_permission=request.paper_trading_permission,
            live_trading_permission=request.live_trading_permission and state.config.live_trading_enabled,
        ),
        config=MetaStrategyLocalGateConfig(
            minimum_active_strategies=state.config.settings.candidate_aggregation.minimum_active_strategies,
            minimum_independent_families=state.config.settings.candidate_aggregation.minimum_independent_families,
            minimum_deterministic_score=state.config.settings.entry_exit_management.entry_threshold,
            minimum_deterministic_edge=state.config.settings.candidate_aggregation.minimum_conflict_edge,
            minimum_calibrated_success_probability=state.config.settings.ml_inference.model_probability_threshold,
            minimum_reward_risk_after_costs=state.config.settings.local_risk.minimum_reward_to_risk,
            maximum_spread_bps=state.config.settings.local_risk.spread_limit_bps,
            minimum_liquidity=state.config.settings.local_risk.liquidity_requirement,
            maximum_daily_loss=state.config.settings.local_risk.maximum_daily_loss,
            maximum_daily_trades=state.config.settings.local_risk.trade_count_limit,
            minimum_model_health=0.0 if state.config.settings.ml_inference.mode == "DISABLED" else 0.70,
            allowed_session_phases=state.config.settings.sessions.allowed_sessions,
            paper_trading_allowed=state.config.settings.paper_execution.enabled and state.config.settings.paper_execution.execution_mode == "PAPER",
            live_trading_allowed=False,
        ),
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
            spread_bps=_number_or_default(snapshot.spread_bps, 0.0),
            event_blackout=state.request.event_blackout,
            session_allowed=state.request.session_allowed,
            model_health_score=float((inference.modelHealth or {}).get("score", 0.0)),
            missingness=inference.featureMissingness,
            ood_score=_number_or_default(inference.outOfDistributionScore, 0.0),
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
    risk_state = _pipeline_local_risk_state(state)
    missing_reasons = risk_state.missing_reason_codes if request.mode == "PAPER" else ()
    state.sizing = calculate_meta_strategy_position_size(
        MetaStrategySizingContext(
            side=inference.finalSignal if inference.finalSignal in {"BUY", "SELL"} else "HOLD",
            candidate_accepted=inference.candidateAccepted and _required_entry_stages_passed(state),
            local_gates_passed=local_gates.passed,
            baseline_settings=config.baseline_settings,
            effective_settings=profile.effective_settings,
            model_risk_multiplier=inference.recommendedRiskMultiplier,
            account_equity=risk_state.account_equity if risk_state.account_equity is not None else _pipeline_required_number(None, config.default_account_equity, request.mode),
            available_buying_power=risk_state.buying_power if risk_state.buying_power is not None else _pipeline_required_number(None, config.default_buying_power, request.mode),
            entry_price=entry,
            stop_distance=stop_distance,
            market_liquidity=float((snapshot.liquidity or {}).get("shareVolume") or snapshot.volume),
            remaining_algorithm_risk=risk_state.remaining_local_risk if risk_state.remaining_local_risk is not None else _pipeline_required_number(None, config.default_remaining_algorithm_risk, request.mode),
            global_available_risk=risk_state.global_available_risk,
            global_quantity_cap=risk_state.global_quantity_cap,
            existing_symbol_exposure=risk_state.existing_symbol_exposure,
            realized_daily_pnl=risk_state.realized_daily_pnl,
            unrealized_pnl=risk_state.unrealized_pnl,
            reserved_risk=risk_state.reserved_risk,
            maximum_daily_loss=config.settings.local_risk.maximum_daily_loss,
            maximum_open_risk=config.settings.local_risk.maximum_open_risk,
        )
    )
    if missing_reasons:
        state.reason_codes.extend(missing_reasons)
    state.reason_codes.extend(state.sizing.reason_codes)
    _record(
        state,
        "sizing",
        {
            "quantity": state.sizing.quantity,
            "limitingCap": state.sizing.limiting_cap,
            "authoritativeInputsAvailable": not missing_reasons,
            "missingReasonCodes": missing_reasons,
        },
    )


def _stage_order_intent(state: _PipelineState) -> None:
    sizing = _require(state.sizing, "sizing")
    inference = _require(state.inference, "inference")
    geometry = _require(state.geometry, "geometry")
    snapshot = _require(state.snapshot, "snapshot")
    if not _required_entry_stages_passed(state):
        blocked = _required_entry_stage_block_reasons(state)
        state.order_intent = None
        state.reason_codes.extend(blocked)
        _record(
            state,
            "order_intent",
            {
                "status": "NO_ORDER",
                "quantity": 0,
                "reasonCodes": (*blocked, "meta_strategy.order_intent.blocked_by_required_stage"),
                "blockedByRequiredStage": True,
            },
        )
        return
    result = build_meta_strategy_order_intent(
        snapshot=snapshot,
        side=inference.finalSignal,
        quantity=sizing.quantity,
        stop_price=geometry.geometry.stop_price,
        limit_price=_configured_limit_price(state, inference.finalSignal, geometry),
        time_in_force=state.config.settings.order_construction.time_in_force,
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
    approved_quantity_raw = global_risk.get("approvedQuantity")
    approved_quantity = int(approved_quantity_raw) if approved_quantity_raw is not None else 0
    risk_state = _pipeline_local_risk_state(state)
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
            available_buying_power=risk_state.buying_power if risk_state.buying_power is not None else _pipeline_required_number(None, state.config.default_buying_power, state.request.mode),
            reserved_risk_dollars=approved_quantity * stop_distance,
            maximum_reserved_risk_dollars=risk_state.global_available_risk if risk_state.global_available_risk is not None else risk_state.remaining_local_risk if risk_state.remaining_local_risk is not None else _pipeline_required_number(None, state.config.default_global_available_risk, state.request.mode),
            session_allowed=state.request.session_allowed and not (state.request.mode == "LIVE" and not state.config.live_trading_enabled),
            max_quote_age_seconds=state.request.max_quote_age_seconds,
            max_spread_bps=state.dynamic_profile.effective_settings.spread_limit_bps if state.dynamic_profile else 15.0,
            minimum_liquidity=state.dynamic_profile.effective_settings.liquidity_requirement if state.dynamic_profile else 0.0,
            duplicate_intent_ids=_duplicate_order_intent_ids_from_inventory(state.request.inventory_snapshot or {}),
            existing_position_symbols=risk_state.existing_position_symbols if state.config.settings.position_management.one_position_per_symbol else (),
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
    approved_raw = global_result.get("approvedQuantity")
    approved = int(approved_raw) if approved_raw is not None else 0
    if state.order_intent is not None and approved < int(state.order_intent.quantity):
        state.order_intent = _order_with_quantity(state.order_intent, approved) if approved > 0 else None
        state.reason_codes.append("meta_strategy.pipeline.global_risk_reduced_quantity")
    state.global_risk = global_result
    _record(state, "global_risk", global_result)


def _stage_broker_adapter(state: _PipelineState) -> None:
    if not state.config.submit_to_broker:
        state.broker_result = {
            "status": "SKIPPED",
            "submitted": False,
            "filledQuantity": 0,
            "reasonCodes": ("meta_strategy.pipeline.broker_skipped_in_decision_worker",),
        }
        state.reason_codes.extend(tuple(state.broker_result["reasonCodes"]))
        _record(state, "broker_adapter", state.broker_result)
        return
    state.broker_result = state.broker.submit(state.order_intent, mode=state.request.mode)
    state.reason_codes.extend(tuple(state.broker_result.get("reasonCodes") or ()))
    _record(state, "broker_adapter", state.broker_result)


def _stage_persistence(state: _PipelineState) -> None:
    snapshot = _require(state.snapshot, "snapshot")
    state.persistence_result = state.persistence.persist(
        {
            "algorithmId": "meta_strategy",
            "decisionId": snapshot.decision_id,
            "settingsVersion": state.config.settings.settings_version,
            "effectiveSettingsHash": state.config.settings.effective_settings_hash,
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
    if state.broker_result and state.broker_result.get("filledQuantity") is not None:
        filled = int(state.broker_result["filledQuantity"])
    else:
        filled = state.request.broker_quantity
    geometry = _require(state.geometry, "geometry")
    state.reconciliation = reconcile_meta_strategy_broker_fill(
        planned_quantity=int(state.order_intent.quantity),
        filled_quantity=filled,
        position_id=f"meta_strategy.position.{state.order_intent.order_intent_id}",
        symbol=state.order_intent.symbol,
        side=state.order_intent.side,
        average_fill_price=geometry.entry_reference or _require(state.snapshot, "snapshot").last_price,
        filled_at=_require(state.snapshot, "snapshot").timestamp,
        protective_stop=geometry.geometry.stop_price if geometry.geometry.stop_price is not None else 0.0,
        profit_target=geometry.geometry.target_price if geometry.geometry.target_price is not None else 0.0,
        maximum_holding_minutes=geometry.maximum_holding_minutes if geometry.maximum_holding_minutes is not None else 1,
    )
    state.reason_codes.extend(state.reconciliation.reason_codes)
    _record(state, "reconciliation", state.reconciliation.as_pipeline_result())


def _candidate_components(state: _PipelineState) -> CandidateComponentEvaluation:
    if state.candidate_components is None:
        state.candidate_components = evaluate_candidate_components(
            _require(state.snapshot, "snapshot"),
            settings=state.config.settings,
        )
    return state.candidate_components


def _strategy_stage_output(
    output: SnapshotEvaluationResult,
    entry: MetaStrategyRegistryEntry,
    snapshot: MetaStrategyMarketSnapshot,
) -> MetaStrategyStrategyStageOutput:
    evidence = dict(output.evidence or {})
    required_status = dict(output.required_input_status or {})
    missing_inputs = tuple(key for key, ready in required_status.items() if not ready)
    data_quality = "OK"
    if missing_inputs or evidence.get("dataQualityBlocked") or any("missing_data" in code for code in output.reason_codes):
        data_quality = "BLOCKED"
    elif not output.eligible:
        data_quality = "DEGRADED"
    signal = str(output.signal).upper()
    if signal not in {"BUY", "SELL", "HOLD"}:
        signal = "HOLD"
    vetoes = tuple(
        dict.fromkeys(
            (
                *(output.reason_codes if not output.eligible or data_quality == "BLOCKED" else ()),
                "meta_strategy.strategy.blocks_new_entries" if evidence.get("blocksNewEntries") else "",
            )
        )
    )
    return MetaStrategyStrategyStageOutput(
        strategy_id=output.strategy_id,
        strategy_version=output.strategy_version or entry.strategy_version,
        family_id=str(output.family if output.family != "UNKNOWN" else entry.family),
        signal=signal,  # type: ignore[arg-type]
        confidence=round(max(0.0, min(1.0, float(output.confidence))), 6),
        eligible=bool(output.eligible),
        data_quality=data_quality,
        evidence={
            **evidence,
            "requiredInputs": entry.required_inputs,
            "requiredInputStatus": required_status,
            "minimumWarmup": entry.minimum_warmup,
            "role": str(entry.role),
            "mode": entry.mode,
            "correlationGroup": entry.correlation_group,
        },
        vetoes=tuple(code for code in vetoes if code),
        reason_codes=tuple(output.reason_codes),
        evaluated_at=snapshot.timestamp,
    )


def _record_stage_contract(
    state: _PipelineState,
    stage: str,
    *,
    status: str,
    eligible: bool,
    input_version: str,
    output_version: str,
    reason_codes: tuple[str, ...],
    evidence: dict[str, Any],
) -> None:
    snapshot = _require(state.snapshot, "snapshot")
    result = MetaStrategyStageContractResult(
        status=status,
        eligible=eligible,
        input_version=input_version,
        output_version=output_version,
        started_at=snapshot.timestamp,
        completed_at=snapshot.timestamp,
        duration_ms=0,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        evidence=evidence,
    )
    if not eligible:
        state.reason_codes.extend(result.reason_codes)
    _record(state, stage, result.as_payload())


def _stage_input_version(snapshot: MetaStrategyMarketSnapshot) -> str:
    return ":".join(
        (
            snapshot.algorithm_version,
            snapshot.configuration_version,
            snapshot.strategy_catalog_version,
            snapshot.settings_version,
            snapshot.snapshot_id,
        )
    )


def _stage_reason_codes(*groups: tuple[str, ...], extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    return tuple(dict.fromkeys(code for group in groups for code in group if code) | dict.fromkeys(code for code in extra if code))


def _first_evidence_value(groups: tuple[dict[str, Any], ...], key: str, *, default: Any) -> Any:
    for evidence in groups:
        if evidence.get(key) is not None:
            return evidence[key]
    return default


def _restricted_families_from_regime(outputs: tuple[SnapshotEvaluationResult, ...], safety_blocks: bool) -> tuple[str, ...]:
    restricted: set[str] = set()
    for output in outputs:
        fit = (output.evidence or {}).get("strategyFit") or {}
        if not isinstance(fit, Mapping):
            continue
        restricted.update(str(family) for family, value in fit.items() if _number_or_default(_coerce_float(value), 0.0) <= 0.0)
    if safety_blocks:
        restricted.update(("TREND", "BREAKOUT", "REVERSAL", "MEAN_REVERSION", "GAP_SESSION", "EVENT_DRIVEN"))
    return tuple(sorted(restricted))


def _explicit_safety_vetoes(state: _PipelineState) -> tuple[str, ...]:
    request = state.request
    snapshot = _require(state.snapshot, "snapshot")
    vetoes: list[str] = []
    if not _required_market_data_complete(state):
        vetoes.append("meta_strategy.safety.data_completeness_failed")
    if not _quote_fresh(state):
        vetoes.append("meta_strategy.safety.data_freshness_failed")
    if not request.session_allowed:
        vetoes.append("meta_strategy.safety.market_session_not_allowed")
    if request.event_blackout:
        vetoes.append("meta_strategy.safety.event_blackout")
    spread = snapshot.spread_bps
    if spread is None or float(spread) > state.config.settings.local_risk.spread_limit_bps:
        vetoes.append("meta_strategy.safety.spread_unacceptable")
    liquidity = float((snapshot.liquidity or {}).get("dollarVolume") or snapshot.volume)
    if liquidity < state.config.settings.local_risk.liquidity_requirement:
        vetoes.append("meta_strategy.safety.liquidity_unacceptable")
    if not _operationally_healthy(state):
        vetoes.append("meta_strategy.safety.operational_health_failed")
    if _pipeline_local_risk_state(state).realized_daily_pnl <= -float(state.config.settings.local_risk.maximum_daily_loss):
        vetoes.append("meta_strategy.safety.daily_loss_limit_reached")
    if _emergency_or_entry_pause_active(state):
        vetoes.append("meta_strategy.safety.emergency_or_entry_pause_active")
    if not _symbol_supported(state):
        vetoes.append("meta_strategy.safety.unsupported_symbol")
    if not _timeframe_supported(state):
        vetoes.append("meta_strategy.safety.unsupported_timeframe")
    if _has_conflicting_position_state(state):
        vetoes.append("meta_strategy.safety.conflicting_position_state")
    return tuple(dict.fromkeys(vetoes))


def _required_market_data_complete(state: _PipelineState) -> bool:
    request = state.request.snapshot_request
    return bool(request.quotes and request.one_minute_candles and request.five_minute_candles and request.fifteen_minute_candles)


def _quote_fresh(state: _PipelineState) -> bool:
    snapshot = _require(state.snapshot, "snapshot")
    quote = snapshot.quote or {}
    timestamp = quote.get("timestamp") if isinstance(quote, Mapping) else None
    if timestamp is None:
        return False
    try:
        quote_time = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    if quote_time.tzinfo is None or quote_time.utcoffset() is None:
        return False
    return (snapshot.timestamp - quote_time).total_seconds() <= state.request.max_quote_age_seconds


def _operationally_healthy(state: _PipelineState) -> bool:
    health = dict(state.request.operational_health or {})
    runtime = dict(state.request.runtime_health or {})
    if not health and not runtime:
        return True
    blocked_values = {
        "ready": False,
        "tradingAllowed": False,
        "paperOrdersBlocked": True,
        "blocked": True,
        "degraded": True,
    }
    for key, blocked in blocked_values.items():
        if health.get(key) is blocked or runtime.get(key) is blocked:
            return False
    status = str(health.get("status") or runtime.get("status") or "OK").upper()
    return status not in {"FAIL", "FAILED", "BLOCKED", "UNHEALTHY", "DOWN"}


def _emergency_or_entry_pause_active(state: _PipelineState) -> bool:
    controls = {**dict(state.request.operational_controls or {}), **dict(state.request.runtime_health or {})}
    for key in ("emergencyStop", "emergency_stop", "exitOnly", "exit_only", "pauseNewEntries", "pause_new_entries", "paperOrdersBlocked"):
        if controls.get(key) is True:
            return True
    return False


def _symbol_supported(state: _PipelineState) -> bool:
    symbol = _require(state.snapshot, "snapshot").symbol.upper()
    explicit = (getattr(state.config.settings, "model_extra", None) or {}).get("allowed_symbols")
    if explicit:
        return symbol in {str(item).upper() for item in explicit}
    return bool(symbol)


def _timeframe_supported(state: _PipelineState) -> bool:
    candles = _require(state.snapshot, "snapshot").candles
    return bool(candles.get("1m") and candles.get("5m") and candles.get("15m"))


def _has_conflicting_position_state(state: _PipelineState) -> bool:
    snapshot = _require(state.snapshot, "snapshot")
    existing = {symbol.upper() for symbol in _pipeline_local_risk_state(state).existing_position_symbols}
    return snapshot.symbol.upper() in existing and state.config.settings.position_management.one_position_per_symbol


def _family_alignment_from_aggregation(signal: str, family_scores: tuple[Any, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if signal == "BUY":
        supporting = tuple(score.family for score in family_scores if score.buy_score > score.sell_score and score.buy_score > 0.0)
        opposing = tuple(score.family for score in family_scores if score.sell_score > 0.0)
    elif signal == "SELL":
        supporting = tuple(score.family for score in family_scores if score.sell_score > score.buy_score and score.sell_score > 0.0)
        opposing = tuple(score.family for score in family_scores if score.buy_score > 0.0)
    else:
        supporting = ()
        opposing = tuple(score.family for score in family_scores if score.buy_score > 0.0 or score.sell_score > 0.0)
    return supporting, opposing


def _winning_scores_from_aggregation(signal: str, aggregation: Any) -> tuple[float, float]:
    if signal == "BUY":
        return float(aggregation.buy_score), float(aggregation.sell_score)
    if signal == "SELL":
        return float(aggregation.sell_score), float(aggregation.buy_score)
    return max(float(aggregation.buy_score), float(aggregation.sell_score), float(aggregation.hold_score)), max(min(float(aggregation.buy_score), float(aggregation.sell_score)), 0.0)


def _required_entry_stages_passed(state: _PipelineState) -> bool:
    return all(bool((state.stage_results.get(stage) or {}).get("eligible")) for stage in ("strategies", "context_and_regime", "safety", "family_aggregation"))


def _required_entry_stage_block_reasons(state: _PipelineState) -> tuple[str, ...]:
    reasons: list[str] = []
    for stage in ("strategies", "context_and_regime", "safety", "family_aggregation"):
        payload = state.stage_results.get(stage) or {}
        if payload.get("eligible"):
            continue
        reasons.extend(str(code) for code in payload.get("reasonCodes") or ())
        reasons.append(f"meta_strategy.pipeline.required_stage_blocked.{stage}")
    return tuple(dict.fromkeys(reasons))


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pipeline_local_risk_state(state: _PipelineState) -> _PipelineLocalRiskState:
    request = state.request
    snapshot = _require(state.snapshot, "snapshot")
    account = dict(request.account_snapshot or {})
    inventory = dict(request.inventory_snapshot or {})
    global_risk = dict(request.global_risk_snapshot or {})
    paper_mode = request.mode == "PAPER"

    account_equity = _snapshot_float(account, "accountEquity", "account_equity", "equity")
    buying_power = _snapshot_float(account, "buyingPower", "buying_power", "cashAvailable", "cash_available")
    allocated_capital = _snapshot_float(account, "allocatedCapital", "allocated_capital")
    if allocated_capital is None:
        allocated_capital = _snapshot_float(inventory, "allocatedCapital", "allocated_capital")
    realized_daily_pnl = _snapshot_float(inventory, "realizedDailyPnl", "realisedDailyPnl", "dailyRealizedPnl", "dailyRealisedPnl", "daily_realized_pnl", "daily_realised_pnl", "realizedPnl", "realisedPnl", "realized_pnl", "realised_pnl")
    cumulative_realized_pnl = _snapshot_float(inventory, "realizedPnl", "realisedPnl", "realized_pnl", "realised_pnl")
    unrealized_pnl = _snapshot_float(inventory, "unrealizedPnl", "unrealisedPnl", "unrealized_pnl", "unrealised_pnl")
    fees_and_slippage = _snapshot_float(account, "feesAndSlippage", "fees_and_slippage")
    if fees_and_slippage is None:
        fees_and_slippage = _snapshot_float(inventory, "feesAndSlippage", "fees_and_slippage")
    reserved_risk = _snapshot_float(inventory, "reservedRiskDollars", "reserved_risk_dollars", "reservedRisk", "reserved_risk")
    daily_trade_count = _snapshot_int(inventory, "dailyTradeCount", "daily_trade_count")

    if account_equity is None and allocated_capital is not None:
        account_equity = _account_equity_from_parts(allocated_capital, cumulative_realized_pnl if cumulative_realized_pnl is not None else realized_daily_pnl, unrealized_pnl, fees_and_slippage)
    if not paper_mode:
        if account_equity is None:
            account_equity = _number_or_default(request.account_equity, state.config.default_account_equity)
        if buying_power is None:
            buying_power = _number_or_default(request.available_buying_power, state.config.default_buying_power)
        if daily_trade_count is None:
            daily_trade_count = int(request.daily_trade_count)
        if realized_daily_pnl is None:
            realized_daily_pnl = float(request.realized_daily_pnl)
        if reserved_risk is None:
            reserved_risk = 0.0

    realized = float(realized_daily_pnl or 0.0)
    unrealized = float(unrealized_pnl or 0.0)
    reserved = float(reserved_risk or 0.0)
    remaining_local_risk = _remaining_local_risk_from_inventory(state, account_equity, realized, reserved)
    if not paper_mode and request.remaining_algorithm_risk is not None:
        remaining_local_risk = min(float(request.remaining_algorithm_risk), remaining_local_risk if remaining_local_risk is not None else float(request.remaining_algorithm_risk))

    global_available_risk = _global_available_risk_from_snapshot(global_risk)
    global_quantity_cap = _global_quantity_cap_from_snapshot(global_risk)
    if not paper_mode:
        if global_available_risk is None:
            global_available_risk = _number_or_default(request.global_available_risk, state.config.default_global_available_risk)
        if global_quantity_cap is None:
            global_quantity_cap = _pipeline_required_int(request.global_quantity_cap, state.config.default_global_quantity_cap, request.mode)

    existing_symbols = _existing_position_symbols_from_inventory(inventory)
    if not existing_symbols and not paper_mode:
        existing_symbols = tuple(str(symbol).upper() for symbol in request.existing_position_symbols)
    missing = []
    if paper_mode:
        if account_equity is None:
            missing.append("meta_strategy.sizing.account_equity_unavailable")
        if buying_power is None:
            missing.append("meta_strategy.sizing.buying_power_unavailable")
        if remaining_local_risk is None:
            missing.append("meta_strategy.sizing.algorithm_risk_unavailable")
    return _PipelineLocalRiskState(
        account_equity=account_equity,
        buying_power=buying_power,
        allocated_capital=allocated_capital,
        realized_daily_pnl=realized,
        unrealized_pnl=unrealized,
        reserved_risk=reserved,
        remaining_local_risk=remaining_local_risk,
        global_available_risk=global_available_risk,
        global_quantity_cap=global_quantity_cap,
        daily_trade_count=int(daily_trade_count or 0),
        last_trade_at=_last_trade_at_from_inventory(inventory) or (None if paper_mode else request.last_trade_at),
        existing_position_symbols=existing_symbols,
        existing_symbol_exposure=_symbol_exposure_from_inventory(inventory, snapshot.symbol),
        missing_reason_codes=tuple(dict.fromkeys(missing)),
    )


def _account_equity_from_parts(allocated_capital: float | None, realized_pnl: float | None, unrealized_pnl: float | None, fees_and_slippage: float | None) -> float | None:
    if allocated_capital is None:
        return None
    return round(max(0.0, float(allocated_capital) + float(realized_pnl or 0.0) + float(unrealized_pnl or 0.0) - float(fees_and_slippage or 0.0)), 10)


def _remaining_local_risk_from_inventory(state: _PipelineState, account_equity: float | None, realized_daily_pnl: float, reserved_risk: float) -> float | None:
    if account_equity is None:
        return None
    settings = state.config.settings.local_risk
    configured_trade_risk = max(0.0, float(account_equity) * float(settings.risk_percentage))
    daily_loss_remaining = max(0.0, float(settings.maximum_daily_loss) + float(realized_daily_pnl))
    open_risk_remaining = max(0.0, float(settings.maximum_open_risk) - float(reserved_risk))
    return round(max(0.0, min(configured_trade_risk, daily_loss_remaining, open_risk_remaining)), 10)


def _global_available_risk_from_snapshot(snapshot: Mapping[str, Any]) -> float | None:
    if not snapshot:
        return None
    if bool(snapshot.get("reject") or snapshot.get("rejected") or snapshot.get("tradingHalt") or snapshot.get("trading_halt")):
        return 0.0
    return _snapshot_float(snapshot, "availableRiskDollars", "available_risk_dollars", "globalAvailableRisk", "global_available_risk")


def _global_quantity_cap_from_snapshot(snapshot: Mapping[str, Any]) -> int | None:
    if not snapshot:
        return None
    if bool(snapshot.get("reject") or snapshot.get("rejected") or snapshot.get("tradingHalt") or snapshot.get("trading_halt")):
        return 0
    return _snapshot_int(snapshot, "maxQuantity", "max_quantity", "globalQuantityCap", "global_quantity_cap", "approvedQuantity", "approved_quantity")


def _symbol_exposure_from_inventory(inventory: Mapping[str, Any], symbol: str) -> float:
    normalized = str(symbol).upper()
    for key in ("symbolExposure", "symbol_exposure"):
        exposure = inventory.get(key)
        if isinstance(exposure, Mapping):
            value = _snapshot_float(exposure, normalized, normalized.lower(), symbol)
            if value is not None:
                return abs(float(value))
    total = 0.0
    for position in _inventory_positions(inventory):
        if str(position.get("symbol") or "").upper() != normalized:
            continue
        quantity = _snapshot_float(position, "quantity", "qty") or 0.0
        price = _snapshot_float(position, "marketPrice", "market_price", "averagePrice", "average_price", "price") or 0.0
        total += abs(float(quantity) * float(price))
    return round(total, 10)


def _duplicate_order_intent_ids_from_inventory(inventory: Mapping[str, Any]) -> tuple[str, ...]:
    rows = inventory.get("pendingOrderIntents") or inventory.get("orderIntents") or inventory.get("order_intents") or ()
    if not isinstance(rows, tuple | list):
        return ()
    return tuple(
        str(row.get("orderIntentId") or row.get("order_intent_id"))
        for row in rows
        if isinstance(row, Mapping) and (row.get("orderIntentId") or row.get("order_intent_id"))
    )


def _existing_position_symbols_from_inventory(inventory: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted({str(position.get("symbol") or "").upper() for position in _inventory_positions(inventory) if position.get("symbol")}))


def _inventory_positions(inventory: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    rows = inventory.get("positions") or inventory.get("currentVirtualPositions") or inventory.get("openPositions") or inventory.get("open_positions") or ()
    if not isinstance(rows, tuple | list):
        return ()
    return tuple(row for row in rows if isinstance(row, Mapping))


def _last_trade_at_from_inventory(inventory: Mapping[str, Any]) -> datetime | None:
    value = inventory.get("lastTradeAt") or inventory.get("last_trade_at")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _snapshot_float(payload: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _snapshot_int(payload: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


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
        settings_version=state.config.settings.settings_version,
        effective_settings_hash=state.config.settings.effective_settings_hash,
        reason_codes=tuple(dict.fromkeys(state.reason_codes)),
    )


def _authoritative_state_evidence(request: MetaStrategyExecutionPipelineRequest) -> dict[str, Any]:
    inventory = dict(request.inventory_snapshot or {})
    account = dict(request.account_snapshot or {})
    global_risk = dict(request.global_risk_snapshot or {})
    event_state = dict(request.event_state or {})
    operational = dict(request.operational_health or {})
    market_clock = dict(request.market_clock_state or {})
    return _plain_pipeline_value(
        {
            "settings": {
                "eventSettingsVersion": request.settings_version,
                "activeSettingsVersion": request.active_settings_version,
            },
            "inventory": {
                "snapshotId": inventory.get("snapshotId"),
                "pointInTimeCutoff": inventory.get("pointInTimeCutoff"),
                "rebuiltFromLedger": inventory.get("rebuiltFromLedger"),
                "reservedRiskDollars": inventory.get("reservedRiskDollars"),
                "remainingRiskDollars": inventory.get("remainingRiskDollars"),
                "reservedRiskLedgerCount": len(request.reserved_risk_ledger),
                "dailyTradeCount": inventory.get("dailyTradeCount"),
                "lastTradeAt": inventory.get("lastTradeAt"),
                "existingPositionSymbols": tuple(
                    str(position.get("symbol") or "").upper()
                    for position in inventory.get("positions", ())
                    if isinstance(position, Mapping) and position.get("symbol")
                ),
                "openOrderCount": len(inventory.get("submittedAndOpenOrders", ()) or ()),
            },
            "account": {
                "source": account.get("source"),
                "capturedAt": account.get("capturedAt"),
                "authoritativeReadOnly": account.get("authoritativeReadOnly"),
                "accountEquity": account.get("accountEquity"),
                "buyingPower": account.get("buyingPower"),
            },
            "globalRisk": {
                "source": global_risk.get("source"),
                "capturedAt": global_risk.get("capturedAt"),
                "authoritativeReadOnly": global_risk.get("authoritativeReadOnly"),
                "availableRiskDollars": global_risk.get("availableRiskDollars"),
                "maxQuantity": global_risk.get("maxQuantity"),
                "reject": global_risk.get("reject"),
            },
            "operational": {
                "health": operational,
                "controls": dict(request.operational_controls or {}),
                "runtime": dict(request.runtime_health or {}),
                "paperControl": dict(request.paper_control_state or {}),
                "marketClock": market_clock,
            },
            "eventState": event_state,
            "marketData": {
                "quoteCount": len(request.snapshot_request.quotes),
                "oneMinuteCount": len(request.snapshot_request.one_minute_candles),
                "fiveMinuteCount": len(request.snapshot_request.five_minute_candles),
                "fifteenMinuteCount": len(request.snapshot_request.fifteen_minute_candles),
                "relativeStrengthCounts": {
                    "qqq": len(request.snapshot_request.qqq_candles),
                    "iwm": len(request.snapshot_request.iwm_candles),
                },
                "breadthComponentCount": len(request.snapshot_request.breadth_components),
            },
        }
    )


def _plain_pipeline_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _plain_pipeline_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_plain_pipeline_value(item) for item in value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _record(state: _PipelineState, stage: str, payload: dict[str, Any]) -> None:
    state.stage_results[stage] = {
        **payload,
        "settingsVersion": state.config.settings.settings_version,
        "effectiveSettingsHash": state.config.settings.effective_settings_hash,
    }


def _require(value: Any, name: str) -> Any:
    if value is None:
        raise RuntimeError(f"Meta-Strategy pipeline missing required stage output: {name}")
    return value


def _inference_config_for_mode(state: _PipelineState) -> MetaStrategyInferenceConfig:
    configured = _settings_inference_config(state)
    if state.request.mode == "SHADOW":
        return MetaStrategyInferenceConfig(**{**configured.__dict__, "mode": "SHADOW"})
    if state.request.mode == "LIVE" and not state.config.live_trading_enabled:
        return MetaStrategyInferenceConfig(**{**configured.__dict__, "mode": "DISABLED"})
    return configured


def _settings_inference_config(state: _PipelineState) -> MetaStrategyInferenceConfig:
    mode_map = {
        "DISABLED": "DISABLED",
        "SHADOW": "SHADOW",
        "FILTER": "FILTER",
        "ACTIVE": "FILTER",
    }
    settings = state.config.settings.ml_inference
    return MetaStrategyInferenceConfig(
        **{
            **state.config.inference_config.__dict__,
            "mode": mode_map[settings.mode],
            "minSuccessProbability": settings.model_probability_threshold,
            "minCalibratedProbability": settings.model_probability_threshold,
            "fallbackBehavior": settings.fallback_behavior,
        }
    )


def _directional_feature_outputs(candidate: GeneratedDeterministicCandidate) -> dict[str, dict[str, Any]]:
    outputs = candidate.evidence.get("directionalOutputs") or {}
    audit = (candidate.evidence.get("familyAggregation") or {}).get("contributionAudit") or {}
    if not isinstance(outputs, dict):
        return {}
    enriched: dict[str, dict[str, Any]] = {}
    for strategy_id, output in outputs.items():
        if not isinstance(output, dict):
            continue
        contribution = audit.get(strategy_id) if isinstance(audit, dict) else {}
        evidence = output.get("evidence") if isinstance(output.get("evidence"), dict) else {}
        enriched[strategy_id] = {
            "signal": output.get("signal"),
            "confidence": output.get("confidence"),
            "eligible": output.get("eligible"),
            "family": output.get("family"),
            "direction": output.get("signal"),
            "active": bool(output.get("eligible")) and str(output.get("signal")) != "HOLD",
            "dataReady": not bool(evidence.get("dataQualityBlocked")),
            "regimeFit": evidence.get("regimeFit") or evidence.get("regimeCompatibility"),
            "reliability": evidence.get("reliability") or evidence.get("evidenceQuality"),
            "strategyFamily": output.get("family"),
            "evidenceQuality": evidence.get("evidenceQuality") or contribution.get("confidence") if isinstance(contribution, dict) else evidence.get("evidenceQuality"),
            "correlationAdjustedContribution": contribution.get("cappedContribution") if isinstance(contribution, dict) else None,
        }
    return enriched


def _family_feature_scores(candidate: GeneratedDeterministicCandidate) -> tuple[dict[str, Any], ...]:
    scores = (candidate.evidence.get("familyAggregation") or {}).get("familyScores") or {}
    if isinstance(scores, dict):
        return tuple({"family": family, **payload} for family, payload in scores.items() if isinstance(payload, dict))
    if isinstance(scores, (tuple, list)):
        return tuple(item for item in scores if isinstance(item, dict))
    return ()


def _context_feature_outputs(candidate: GeneratedDeterministicCandidate) -> tuple[dict[str, Any], ...]:
    outputs = candidate.evidence.get("contextOutputs") or {}
    if not isinstance(outputs, dict):
        return ()
    return tuple(
        {
            "contextId": strategy_id,
            "confidence": payload.get("confidence"),
            "eligible": payload.get("eligible"),
            "features": _scalar_feature_subset(payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}),
        }
        for strategy_id, payload in outputs.items()
        if isinstance(payload, dict)
    )


def _regime_feature_output(candidate: GeneratedDeterministicCandidate) -> dict[str, Any]:
    outputs = candidate.evidence.get("regimeOutputs") or {}
    if not isinstance(outputs, dict) or not outputs:
        return {}
    first_id, first_payload = next(iter(outputs.items()))
    if not isinstance(first_payload, dict):
        return {}
    return {
        "label": first_payload.get("signal") or first_id,
        "features": _scalar_feature_subset(first_payload.get("evidence") if isinstance(first_payload.get("evidence"), dict) else {}),
    }


def _scalar_feature_subset(values: dict[str, Any]) -> dict[str, Any]:
    forbidden_fragments = ("future", "outcome", "label", "fill", "pnl", "result", "payload")
    return {
        str(key): value
        for key, value in values.items()
        if not isinstance(value, (dict, list, tuple))
        and not any(fragment in str(key).replace("-", "_").lower() for fragment in forbidden_fragments)
    }


def _configured_limit_price(state: _PipelineState, side: str, geometry: CandidateGeometryResult) -> float | None:
    order_settings = state.config.settings.order_construction
    entry_settings = state.config.settings.entry_construction
    style = str(order_settings.order_type or entry_settings.entry_order_style).upper()
    if style == "MARKET":
        return None
    entry = geometry.entry_reference
    if entry is None or side not in {"BUY", "SELL"}:
        return None
    offset_bps = order_settings.limit_offset_bps if order_settings.limit_offset_bps is not None else entry_settings.marketable_limit_offset_bps
    offset = float(entry) * float(offset_bps) / 10_000.0
    if style == "MARKETABLE_LIMIT":
        return round(float(entry) + offset if side == "BUY" else float(entry) - offset, 6)
    return round(float(entry), 6)


def _artifact_for_model_inference(
    state: _PipelineState,
    mode_config: MetaStrategyInferenceConfig,
) -> tuple[dict[str, Any] | None, MetaStrategyInferenceConfig]:
    validation = state.stage_results.get("artifact_validation") or {}
    artifact = dict(state.request.model_artifact or {})
    if validation.get("modelApplicationAllowed") is True:
        return artifact, mode_config
    if artifact and validation.get("shadowDiagnosticsOnly") is True:
        return artifact, MetaStrategyInferenceConfig(**{**mode_config.__dict__, "mode": "SHADOW"})
    if artifact and validation.get("compatible") is False:
        return artifact, mode_config
    if artifact and validation.get("promoted") is not True and mode_config.mode in {"FILTER", "RISK_REDUCTION"}:
        if mode_config.fallbackBehavior == "DETERMINISTIC_BASELINE":
            return None, mode_config
        return None, MetaStrategyInferenceConfig(**{**mode_config.__dict__, "fallbackBehavior": "NO_TRADE"})
    return state.request.model_artifact, mode_config


def _artifact_promoted_for_application(artifact: Mapping[str, Any]) -> bool:
    if not artifact:
        return False
    for key in ("promoted", "paperPromoted", "promotedForPaper", "trusted"):
        if artifact.get(key) is True:
            return True
    status = str(artifact.get("status") or artifact.get("lifecycleStatus") or artifact.get("promotionStatus") or "").upper()
    return status in {"PROMOTED", "ACTIVE", "TRUSTED", "PAPER_PROMOTED"}


def _artifact_validation_reasons(
    *,
    artifact: Mapping[str, Any],
    compatible: bool,
    promoted: bool,
    model_available: bool,
    shadow_diagnostics_only: bool,
    deterministic_only: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not artifact:
        reasons.append("meta_strategy.artifact_validation.model_unavailable")
    if artifact and not compatible:
        reasons.append("meta_strategy.artifact_validation.feature_schema_mismatch")
    if artifact and compatible:
        reasons.append("meta_strategy.artifact_validation.feature_schema_compatible")
    if artifact and not promoted:
        reasons.append("meta_strategy.artifact_validation.unpromoted_artifact_shadow_only")
    if artifact and promoted:
        reasons.append("meta_strategy.artifact_validation.promoted")
    if artifact and not model_available:
        reasons.append("meta_strategy.artifact_validation.champion_model_unavailable")
    if shadow_diagnostics_only:
        reasons.append("meta_strategy.artifact_validation.shadow_diagnostics_only")
    if deterministic_only:
        reasons.append("meta_strategy.artifact_validation.deterministic_only")
    if artifact and compatible and promoted and model_available:
        reasons.append("meta_strategy.artifact_validation.model_application_allowed")
    return tuple(dict.fromkeys(reasons))


def _number_or_default(value: float | None, default: float) -> float:
    return float(value) if value is not None else float(default)


def _pipeline_required_number(value: float | None, fixture_default: float, mode: str) -> float:
    if value is not None:
        return float(value)
    return 0.0 if mode == "PAPER" else float(fixture_default)


def _pipeline_required_int(value: int | None, fixture_default: int, mode: str) -> int:
    if value is not None:
        return int(value)
    return 0 if mode == "PAPER" else int(fixture_default)


def _paper_sizing_missing_reasons(request: MetaStrategyExecutionPipelineRequest) -> tuple[str, ...]:
    reasons: list[str] = []
    if request.account_equity is None:
        reasons.append("meta_strategy.sizing.account_equity_unavailable")
    if request.available_buying_power is None:
        reasons.append("meta_strategy.sizing.buying_power_unavailable")
    if request.remaining_algorithm_risk is None:
        reasons.append("meta_strategy.sizing.algorithm_risk_unavailable")
    if request.global_available_risk is None:
        reasons.append("meta_strategy.sizing.global_risk_unavailable")
    if request.global_quantity_cap is None:
        reasons.append("meta_strategy.sizing.global_quantity_cap_unavailable")
    return tuple(reasons)


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
    "strategies": _stage_strategies,
    "context_and_regime": _stage_context_and_regime,
    "safety": _stage_safety,
    "family_aggregation": _stage_family_aggregation,
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
