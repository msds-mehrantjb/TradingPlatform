from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import Field

from backend.app.backtesting import (
    CurrentBaselineDecision,
    EventDrivenReplayEngine,
    ReplayComponents,
    ReplayEngineConfig,
    ReplayResult,
    V1ShadowDecision,
    build_deterministic_v2_activation_report,
    build_dynamic_policy_activation_report,
    build_dynamic_policy_shadow_report,
    build_historical_shadow_comparison,
    build_ml_filter_rollout_report,
    build_ml_risk_modifier_experiment_report,
    build_paper_shadow_report,
)
from backend.app.backtesting.event_replay import ReplayDecisionSnapshot
from backend.app.domain.feature_engine import (
    MarketCandle,
    OpeningRangeLevels,
    PointInTimeFeatureEngine,
    PointInTimeFeatureRequest,
    PointInTimeFeatureSnapshot,
    PremarketLevels,
    PriorDayOHLC,
)
from backend.app.domain.models import (
    ContextSignal,
    Direction,
    DomainModel,
    EnsembleDecision,
    GateResult,
    GateStatus,
    GlobalGateDecision,
    OperatingMode,
    OrderPlan,
    RegimeState,
    Signal,
    StrategySignal,
)
from backend.app.ensemble import FamilyAwareDeterministicEnsemble
from backend.app.gates import GLOBAL_GATE_ENGINE_VERSION, GlobalGateEngine, GlobalGateInput
from backend.app.ml.features import MLFeatureSet
from backend.app.ml.inference import SafeMLInferenceConfig, apply_safe_ml_inference
from backend.app.strategies import StrategyEvaluationContext, resolve_strategy
from backend.app.strategies.context import (
    EconomicEventContext,
    MarketBreadthMomentumContext,
    MarketStructureContext,
    RelativeStrengthQqqIwmContext,
    VolumeConfirmationContext,
    VwapPositionContext,
)
from backend.app.strategies.directional import (
    BollingerAtrReversionStrategy,
    FailedBreakoutReversalStrategy,
    FirstPullbackAfterOpenStrategy,
    GapContinuationFadeStrategy,
    LiquiditySweepReversalStrategy,
    MultiTimeframeTrendAlignmentStrategy,
    OpeningRangeBreakoutStrategy,
    VolatilityBreakoutStrategy,
    VwapMeanReversionStrategy,
    VwapTrendContinuationStrategy,
)
from backend.app.strategies.regime import AdxAtrRegimeClassifier
from backend.app.trading_policy import DynamicPolicyInputs, DynamicTradingPolicyEngine


API_V2_VERSION = "api_v2"
PAPER_DECISION_ENDPOINT_VERSION = "paper_decision_evaluate_v1"

router = APIRouter(prefix="/api/v2", tags=["api-v2"])
BACKTEST_RESULTS: dict[str, dict[str, Any]] = {}
DECISION_SNAPSHOTS: dict[str, dict[str, Any]] = {}


class ApiV2Envelope(DomainModel):
    apiVersion: str = API_V2_VERSION
    endpointVersion: str
    configurationHash: str
    payload: dict[str, Any]
    explanation: str


class StrategyEvaluationRequest(DomainModel):
    featureSnapshot: PointInTimeFeatureSnapshot
    strategyIds: list[str] | None = None
    configurationHash: str = Field(default="api_v2_strategy_evaluation", min_length=1)


class EnsembleEvaluationRequest(DomainModel):
    strategySignals: list[StrategySignal]
    contextSignals: list[ContextSignal] = Field(default_factory=list)
    regimeState: RegimeState | None = None
    safetyDecision: GlobalGateDecision | None = None
    decidedAt: datetime | None = None
    sessionDate: date | None = None


class MetaModelPredictionRequest(DomainModel):
    deterministicSignal: Signal
    featureSet: MLFeatureSet
    modelArtifact: dict[str, Any] | None = None
    config: SafeMLInferenceConfig = Field(default_factory=SafeMLInferenceConfig)
    hardGatesPassed: bool = True
    candidateEligible: bool = True
    sessionDate: date | None = None
    predictedAt: datetime | None = None


class OrderValidationRequest(DomainModel):
    orderPlan: OrderPlan
    gateDecision: GlobalGateDecision | None = None


class ReplayDecisionEvaluateRequest(DomainModel):
    symbol: str = Field(default="SPY", min_length=1)
    sessionDate: date
    evaluationTimestamp: datetime
    spy1mCandles: list[MarketCandle]
    spy5mCandles: list[MarketCandle]
    spy15mCandles: list[MarketCandle]
    qqqCandles: list[MarketCandle] = Field(default_factory=list)
    iwmCandles: list[MarketCandle] = Field(default_factory=list)
    priorDayOHLC: PriorDayOHLC | None = None
    premarket: PremarketLevels | None = None
    openingRange: OpeningRangeLevels | None = None
    breadthComponents: dict[str, list[MarketCandle]] = Field(default_factory=dict)
    economicEventState: dict[str, Any] = Field(default_factory=dict)


class BacktestRunRequest(DomainModel):
    symbol: str = Field(default="SPY", min_length=1)
    sessionDate: date
    spy1mCandles: list[MarketCandle]
    spy5mCandles: list[MarketCandle] = Field(default_factory=list)
    spy15mCandles: list[MarketCandle] = Field(default_factory=list)
    qqqCandles: list[MarketCandle] = Field(default_factory=list)
    iwmCandles: list[MarketCandle] = Field(default_factory=list)
    priorDayOHLC: PriorDayOHLC | None = None
    premarket: PremarketLevels | None = None
    openingRange: OpeningRangeLevels | None = None
    breadthComponents: dict[str, list[MarketCandle]] = Field(default_factory=dict)
    economicEventState: dict[str, Any] = Field(default_factory=dict)


class HistoricalShadowComparisonRequest(BacktestRunRequest):
    v1Decisions: list[V1ShadowDecision]
    minimumCleanV2SnapshotsForMl: int = Field(default=500, ge=0)


class PaperShadowEvaluateRequest(ReplayDecisionEvaluateRequest):
    baselineDecision: CurrentBaselineDecision | None = None


class DeterministicV2ActivationEvaluateRequest(ReplayDecisionEvaluateRequest):
    rollbackMode: Literal["NONE", "V1", "DISABLE_AUTOMATIC_ENTRIES"] = "NONE"


class MLFilterRolloutEvaluateRequest(ReplayDecisionEvaluateRequest):
    stage: Literal["SHADOW", "FILTER_ACTIVE"] = "SHADOW"
    shadowComparisonPassed: bool = False
    modelArtifact: dict[str, Any] | None = None
    fallbackBehavior: Literal["DETERMINISTIC_BASELINE", "NO_TRADE"] = "DETERMINISTIC_BASELINE"


class DynamicPolicyShadowEvaluateRequest(ReplayDecisionEvaluateRequest):
    dynamicPolicyShadowEnabled: bool = True


class DynamicPolicyActivationEvaluateRequest(ReplayDecisionEvaluateRequest):
    requestedStages: list[
        Literal[
            "RISK_REDUCTION",
            "STOP_AND_QUANTITY",
            "STRATEGY_FAMILY_ENTRY",
            "TARGET_AND_TIME_STOP",
            "TRAILING_BEHAVIOR",
        ]
    ] = Field(default_factory=lambda: ["RISK_REDUCTION"])
    stageComparisons: list[dict[str, Any]]
    rollback: dict[str, Any] = Field(default_factory=dict)


class MLRiskModifierExperimentEvaluateRequest(DynamicPolicyActivationEvaluateRequest):
    mlRiskModifierConfig: dict[str, Any] = Field(default_factory=dict)


@router.post("/features/evaluate")
def evaluate_features(request: PointInTimeFeatureRequest) -> ApiV2Envelope:
    snapshot = PointInTimeFeatureEngine().compute(request)
    return envelope(
        endpoint_version="features_evaluate_v1",
        payload={"featureSnapshot": snapshot.model_dump(mode="json")},
        configuration_hash=snapshot.engineVersion,
        explanation="Point-in-time features were evaluated by the backend feature engine.",
    )


@router.post("/strategies/evaluate")
def evaluate_strategies(request: StrategyEvaluationRequest) -> ApiV2Envelope:
    strategies = build_directional_strategies(request.strategyIds)
    outputs = [
        strategy.evaluate(
            StrategyEvaluationContext(
                registryEntry=strategy.registryEntry,
                featureSnapshot=request.featureSnapshot,
                configurationHash=request.configurationHash,
            )
        )
        for strategy in strategies
    ]
    return envelope(
        endpoint_version="strategies_evaluate_v1",
        payload={"strategyOutputs": [signal.model_dump(mode="json") for signal in outputs]},
        configuration_hash=hash_payload([signal.configurationHash for signal in outputs]),
        explanation="Directional strategy outputs were evaluated by backend V2 strategy modules.",
    )


@router.post("/ensemble/evaluate")
def evaluate_ensemble(request: EnsembleEvaluationRequest) -> ApiV2Envelope:
    decided_at = request.decidedAt or datetime.now(UTC)
    session_date = request.sessionDate or decided_at.date()
    decision = FamilyAwareDeterministicEnsemble().aggregate(
        strategySignals=request.strategySignals,
        contextSignals=request.contextSignals,
        regimeState=request.regimeState,
        safetyDecision=request.safetyDecision or pass_gate_decision(decided_at, session_date),
        decidedAt=decided_at,
        sessionDate=session_date,
    )
    return envelope(
        endpoint_version="ensemble_evaluate_v1",
        payload={"ensembleDecision": decision.model_dump(mode="json")},
        configuration_hash=decision.configurationHash,
        explanation="Family-aware deterministic ensemble was evaluated by the backend.",
    )


@router.post("/meta-model/predict")
def predict_meta_model(request: MetaModelPredictionRequest) -> ApiV2Envelope:
    result = apply_safe_ml_inference(
        deterministic_signal=request.deterministicSignal,
        feature_set=request.featureSet,
        model_artifact=request.modelArtifact,
        config=request.config,
        hard_gates_passed=request.hardGatesPassed,
        candidate_eligible=request.candidateEligible,
        session_date=request.sessionDate,
        predicted_at=request.predictedAt,
    )
    return envelope(
        endpoint_version="meta_model_predict_v1",
        payload={"mlResult": result.model_dump(mode="json")},
        configuration_hash=result.configurationHash,
        explanation="Safe V2 ML inference was evaluated as a candidate filter; it did not submit orders.",
    )


@router.post("/gates/evaluate")
def evaluate_gates(request: GlobalGateInput) -> ApiV2Envelope:
    result = GlobalGateEngine().evaluate(request)
    return envelope(
        endpoint_version="gates_evaluate_v1",
        payload={"gateDecision": result.model_dump(mode="json"), "canonicalGateDecision": result.to_global_gate_decision().model_dump(mode="json")},
        configuration_hash=result.configurationHash,
        explanation="Global hard gates were evaluated by the backend gate engine.",
    )


@router.post("/trading-policy/evaluate")
def evaluate_trading_policy(request: DynamicPolicyInputs) -> ApiV2Envelope:
    decision = DynamicTradingPolicyEngine().evaluate(request)
    return envelope(
        endpoint_version="trading_policy_evaluate_v1",
        payload={"tradingPolicy": decision.model_dump(mode="json")},
        configuration_hash=decision.configurationHash,
        explanation="Dynamic trading policy was evaluated by the backend policy engine.",
    )


@router.post("/orders/validate")
def validate_order(request: OrderValidationRequest) -> ApiV2Envelope:
    gate_eligible = True if request.gateDecision is None else request.gateDecision.eligible
    eligible = bool(request.orderPlan.eligible and gate_eligible and request.orderPlan.quantity > 0)
    validation_errors = list(request.orderPlan.validationErrors)
    if not gate_eligible:
        validation_errors.append("order_validation.gate_ineligible")
    if request.orderPlan.quantity <= 0:
        validation_errors.append("order_validation.quantity_zero")
    return envelope(
        endpoint_version="orders_validate_v1",
        payload={
            "orderPlan": request.orderPlan.model_dump(mode="json"),
            "eligible": eligible,
            "validationErrors": sorted(set(validation_errors)),
            "submissionSeparated": True,
        },
        configuration_hash=request.orderPlan.configurationHash,
        explanation="Order plan was validated only; order submission is a separate explicit action.",
    )


@router.post("/backtests/run")
def run_backtest(request: BacktestRunRequest) -> ApiV2Envelope:
    result = build_replay_engine().replay_session(
        symbol=request.symbol,
        sessionDate=request.sessionDate,
        spy1mCandles=request.spy1mCandles,
        spy5mCandles=request.spy5mCandles or request.spy1mCandles,
        spy15mCandles=request.spy15mCandles or request.spy1mCandles,
        qqqCandles=request.qqqCandles or request.spy1mCandles,
        iwmCandles=request.iwmCandles or request.spy1mCandles,
        priorDayOHLC=request.priorDayOHLC,
        premarket=request.premarket,
        openingRange=request.openingRange,
        breadthComponents=request.breadthComponents,
        economicEventState=request.economicEventState,
    )
    backtest_id = f"bt-{uuid4().hex[:16]}"
    payload = {"backtestId": backtest_id, "result": result.model_dump(mode="json")}
    BACKTEST_RESULTS[backtest_id] = payload
    for snapshot in result.snapshots:
        DECISION_SNAPSHOTS[snapshot.snapshotId] = snapshot.model_dump(mode="json")
    return envelope(
        endpoint_version="backtests_run_v1",
        payload=payload,
        configuration_hash=hash_payload(payload),
        explanation="Backtest replay ran through the same V2 backend decision components used by paper evaluation.",
    )


@router.post("/paper-shadow/evaluate")
def evaluate_paper_shadow(request: PaperShadowEvaluateRequest) -> ApiV2Envelope:
    snapshot = build_replay_engine().decide_at(
        symbol=request.symbol,
        sessionDate=request.sessionDate,
        evaluationTimestamp=request.evaluationTimestamp,
        spy1mCandles=request.spy1mCandles,
        spy5mCandles=request.spy5mCandles,
        spy15mCandles=request.spy15mCandles,
        qqqCandles=request.qqqCandles or request.spy1mCandles,
        iwmCandles=request.iwmCandles or request.spy1mCandles,
        priorDayOHLC=request.priorDayOHLC,
        premarket=request.premarket,
        openingRange=request.openingRange,
        breadthComponents=request.breadthComponents,
        economicEventState=request.economicEventState,
    )
    baseline = request.baselineDecision or derive_current_baseline_decision(request)
    report = build_paper_shadow_report(v2Snapshot=snapshot, baselineDecision=baseline)
    DECISION_SNAPSHOTS[f"paper_shadow:{snapshot.snapshotId}"] = report.v2DecisionSnapshot
    return envelope(
        endpoint_version="paper_shadow_evaluate_v1",
        payload={"report": report.model_dump(mode="json")},
        configuration_hash=report.mode.configurationHash,
        explanation="V2 deterministic paper-shadow path was evaluated and recorded without automatic paper submission.",
    )


@router.post("/activation/deterministic/evaluate")
def evaluate_deterministic_v2_activation(request: DeterministicV2ActivationEvaluateRequest) -> ApiV2Envelope:
    snapshot = build_replay_engine(
        ml_config=SafeMLInferenceConfig(mode=OperatingMode.SHADOW, fallbackBehavior="DETERMINISTIC_BASELINE")
    ).decide_at(
        symbol=request.symbol,
        sessionDate=request.sessionDate,
        evaluationTimestamp=request.evaluationTimestamp,
        spy1mCandles=request.spy1mCandles,
        spy5mCandles=request.spy5mCandles,
        spy15mCandles=request.spy15mCandles,
        qqqCandles=request.qqqCandles or request.spy1mCandles,
        iwmCandles=request.iwmCandles or request.spy1mCandles,
        priorDayOHLC=request.priorDayOHLC,
        premarket=request.premarket,
        openingRange=request.openingRange,
        breadthComponents=request.breadthComponents,
        economicEventState=request.economicEventState,
    )
    report = build_deterministic_v2_activation_report(snapshot=snapshot, rollbackMode=request.rollbackMode)
    DECISION_SNAPSHOTS[f"deterministic_v2_active:{snapshot.snapshotId}"] = report.deterministicDecisionSnapshot
    return envelope(
        endpoint_version="deterministic_v2_activation_evaluate_v1",
        payload={"report": report.model_dump(mode="json")},
        configuration_hash=report.activationConfig.configurationHash,
        explanation=(
            "Deterministic V2 static baseline was evaluated for paper-entry eligibility. "
            "ML and dynamic policy were recorded as shadow-only and rollback mode was applied before submission."
        ),
    )


@router.post("/ml-filter/rollout/evaluate")
def evaluate_ml_filter_rollout(request: MLFilterRolloutEvaluateRequest) -> ApiV2Envelope:
    deterministic_snapshot = build_replay_engine(
        ml_config=SafeMLInferenceConfig(mode=OperatingMode.SHADOW, fallbackBehavior="DETERMINISTIC_BASELINE"),
        ml_model_artifact=request.modelArtifact,
    ).decide_at(
        symbol=request.symbol,
        sessionDate=request.sessionDate,
        evaluationTimestamp=request.evaluationTimestamp,
        spy1mCandles=request.spy1mCandles,
        spy5mCandles=request.spy5mCandles,
        spy15mCandles=request.spy15mCandles,
        qqqCandles=request.qqqCandles or request.spy1mCandles,
        iwmCandles=request.iwmCandles or request.spy1mCandles,
        priorDayOHLC=request.priorDayOHLC,
        premarket=request.premarket,
        openingRange=request.openingRange,
        breadthComponents=request.breadthComponents,
        economicEventState=request.economicEventState,
    )
    filter_mode = OperatingMode.SHADOW if request.stage == "SHADOW" else OperatingMode.FILTER
    filtered_snapshot = build_replay_engine(
        ml_config=SafeMLInferenceConfig(mode=filter_mode, fallbackBehavior=request.fallbackBehavior),
        ml_model_artifact=request.modelArtifact,
    ).decide_at(
        symbol=request.symbol,
        sessionDate=request.sessionDate,
        evaluationTimestamp=request.evaluationTimestamp,
        spy1mCandles=request.spy1mCandles,
        spy5mCandles=request.spy5mCandles,
        spy15mCandles=request.spy15mCandles,
        qqqCandles=request.qqqCandles or request.spy1mCandles,
        iwmCandles=request.iwmCandles or request.spy1mCandles,
        priorDayOHLC=request.priorDayOHLC,
        premarket=request.premarket,
        openingRange=request.openingRange,
        breadthComponents=request.breadthComponents,
        economicEventState=request.economicEventState,
    )
    report = build_ml_filter_rollout_report(
        snapshot=filtered_snapshot,
        deterministicBaselineSnapshot=deterministic_snapshot,
        stage=request.stage,
        shadowComparisonPassed=request.shadowComparisonPassed,
        fallbackBehavior=request.fallbackBehavior,
    )
    DECISION_SNAPSHOTS[f"ml_filter:{request.stage.lower()}:{filtered_snapshot.snapshotId}"] = report.mlFilteredDecisionSnapshot
    return envelope(
        endpoint_version="ml_filter_rollout_evaluate_v1",
        payload={"report": report.model_dump(mode="json")},
        configuration_hash=report.rolloutConfig.configurationHash,
        explanation=(
            "Meta-Model V2 filter rollout was evaluated for paper trading. "
            "Shadow mode records only; filter-active mode can only accept or reject deterministic candidates and keeps static sizing."
        ),
    )


@router.post("/dynamic-policy/shadow/evaluate")
def evaluate_dynamic_policy_shadow(request: DynamicPolicyShadowEvaluateRequest) -> ApiV2Envelope:
    if not request.dynamicPolicyShadowEnabled:
        raise HTTPException(status_code=400, detail="dynamic policy shadow must be enabled for this endpoint")
    snapshot = build_replay_engine(
        ml_config=SafeMLInferenceConfig(mode=OperatingMode.SHADOW, fallbackBehavior="DETERMINISTIC_BASELINE")
    ).decide_at(
        symbol=request.symbol,
        sessionDate=request.sessionDate,
        evaluationTimestamp=request.evaluationTimestamp,
        spy1mCandles=request.spy1mCandles,
        spy5mCandles=request.spy5mCandles,
        spy15mCandles=request.spy15mCandles,
        qqqCandles=request.qqqCandles or request.spy1mCandles,
        iwmCandles=request.iwmCandles or request.spy1mCandles,
        priorDayOHLC=request.priorDayOHLC,
        premarket=request.premarket,
        openingRange=request.openingRange,
        breadthComponents=request.breadthComponents,
        economicEventState=request.economicEventState,
    )
    report = build_dynamic_policy_shadow_report(snapshot=snapshot)
    DECISION_SNAPSHOTS[f"dynamic_policy_shadow:{snapshot.snapshotId}"] = report.deterministicDecisionSnapshot
    return envelope(
        endpoint_version="dynamic_policy_shadow_evaluate_v1",
        payload={"report": report.model_dump(mode="json")},
        configuration_hash=report.shadowConfig.configurationHash,
        explanation=(
            "Deterministic dynamic policy was calculated in shadow mode beside the static paper path. "
            "Static settings remain the execution source; the dynamic policy did not submit orders."
        ),
    )


@router.post("/dynamic-policy/activation/evaluate")
def evaluate_dynamic_policy_activation(request: DynamicPolicyActivationEvaluateRequest) -> ApiV2Envelope:
    snapshot = build_replay_engine(
        ml_config=SafeMLInferenceConfig(mode=OperatingMode.FILTER, fallbackBehavior="DETERMINISTIC_BASELINE")
    ).decide_at(
        symbol=request.symbol,
        sessionDate=request.sessionDate,
        evaluationTimestamp=request.evaluationTimestamp,
        spy1mCandles=request.spy1mCandles,
        spy5mCandles=request.spy5mCandles,
        spy15mCandles=request.spy15mCandles,
        qqqCandles=request.qqqCandles or request.spy1mCandles,
        iwmCandles=request.iwmCandles or request.spy1mCandles,
        priorDayOHLC=request.priorDayOHLC,
        premarket=request.premarket,
        openingRange=request.openingRange,
        breadthComponents=request.breadthComponents,
        economicEventState=request.economicEventState,
    )
    report = build_dynamic_policy_activation_report(
        snapshot=snapshot,
        stageComparisons=request.stageComparisons,
        requestedStages=request.requestedStages,
        rollback=request.rollback,
    )
    DECISION_SNAPSHOTS[f"dynamic_policy_active:{snapshot.snapshotId}"] = report.deterministicDecisionSnapshot
    return envelope(
        endpoint_version="dynamic_policy_activation_evaluate_v1",
        payload={"report": report.model_dump(mode="json")},
        configuration_hash=report.activationConfig.configurationHash,
        explanation=(
            "Deterministic dynamic policy activation was evaluated with staged capability gates, rollback controls, "
            "authoritative global risk/broker reconciliation, and ML limited to trade filtering."
        ),
    )


@router.post("/ml-risk-modifier/experiment/evaluate")
def evaluate_ml_risk_modifier_experiment(request: MLRiskModifierExperimentEvaluateRequest) -> ApiV2Envelope:
    snapshot = build_replay_engine(
        ml_config=SafeMLInferenceConfig(mode=OperatingMode.FILTER, fallbackBehavior="DETERMINISTIC_BASELINE")
    ).decide_at(
        symbol=request.symbol,
        sessionDate=request.sessionDate,
        evaluationTimestamp=request.evaluationTimestamp,
        spy1mCandles=request.spy1mCandles,
        spy5mCandles=request.spy5mCandles,
        spy15mCandles=request.spy15mCandles,
        qqqCandles=request.qqqCandles or request.spy1mCandles,
        iwmCandles=request.iwmCandles or request.spy1mCandles,
        priorDayOHLC=request.priorDayOHLC,
        premarket=request.premarket,
        openingRange=request.openingRange,
        breadthComponents=request.breadthComponents,
        economicEventState=request.economicEventState,
    )
    report = build_ml_risk_modifier_experiment_report(
        snapshot=snapshot,
        stageComparisons=request.stageComparisons,
        requestedStages=request.requestedStages,
        dynamicPolicyRollback=request.rollback,
        config=request.mlRiskModifierConfig,
    )
    return envelope(
        endpoint_version="ml_risk_modifier_experiment_evaluate_v1",
        payload={"report": report.model_dump(mode="json")},
        configuration_hash=report.config.configurationHash,
        explanation=(
            "Bounded ML risk modification was evaluated as a separate experiment. "
            "The deterministic dynamic policy remains fallback and the feature is disabled by default until validated."
        ),
    )


@router.post("/backtests/shadow-comparison")
def run_historical_shadow_comparison(request: HistoricalShadowComparisonRequest) -> ApiV2Envelope:
    replay = build_replay_engine().replay_session(
        symbol=request.symbol,
        sessionDate=request.sessionDate,
        spy1mCandles=request.spy1mCandles,
        spy5mCandles=request.spy5mCandles or request.spy1mCandles,
        spy15mCandles=request.spy15mCandles or request.spy1mCandles,
        qqqCandles=request.qqqCandles or request.spy1mCandles,
        iwmCandles=request.iwmCandles or request.spy1mCandles,
        priorDayOHLC=request.priorDayOHLC,
        premarket=request.premarket,
        openingRange=request.openingRange,
        breadthComponents=request.breadthComponents,
        economicEventState=request.economicEventState,
    )
    report = build_historical_shadow_comparison(
        v1Decisions=request.v1Decisions,
        v2Replay=replay,
        minimumCleanV2SnapshotsForMl=request.minimumCleanV2SnapshotsForMl,
    )
    comparison_id = f"shadow-{uuid4().hex[:16]}"
    payload = {"shadowComparisonId": comparison_id, "report": report.model_dump(mode="json")}
    BACKTEST_RESULTS[comparison_id] = payload
    for snapshot in report.v2ShadowSnapshots:
        DECISION_SNAPSHOTS[f"{report.storage.v2Namespace}:{snapshot['snapshotId']}"] = snapshot
    return envelope(
        endpoint_version="historical_shadow_comparison_v1",
        payload=payload,
        configuration_hash=report.featureFlags.configurationHash,
        explanation="V1 and V2 were replayed side by side. V2 decisions were recorded in shadow mode only and did not affect paper orders.",
    )


@router.get("/backtests/{backtest_id}")
def get_backtest(backtest_id: str) -> ApiV2Envelope:
    payload = BACKTEST_RESULTS.get(backtest_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return envelope(
        endpoint_version="backtests_get_v1",
        payload=payload,
        configuration_hash=hash_payload(payload),
        explanation="Stored V2 backtest result retrieved by id.",
    )


@router.get("/models/status")
def get_models_status() -> ApiV2Envelope:
    payload = {
        "metaModel": {
            "mode": "OFF",
            "status": "not_loaded",
            "modelVersion": "none",
            "configurationHash": "safe_ml_inference_config_v1",
            "reasonCodes": ["ml.model_unavailable", "ml.off_by_default"],
        },
        "familyWeighting": {
            "enabled": False,
            "configurationHash": "ml_family_weighting_disabled",
        },
    }
    return envelope(
        endpoint_version="models_status_v1",
        payload=payload,
        configuration_hash=hash_payload(payload),
        explanation="Current V2 model status returned without applying ML to orders.",
    )


@router.get("/decision-snapshots/{snapshot_id}")
def get_decision_snapshot(snapshot_id: str) -> ApiV2Envelope:
    payload = DECISION_SNAPSHOTS.get(snapshot_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="decision snapshot not found")
    return envelope(
        endpoint_version="decision_snapshots_get_v1",
        payload={"snapshot": payload},
        configuration_hash=str(payload.get("effectivePolicy", {}).get("configurationHash") or hash_payload(payload)),
        explanation="Stored V2 decision snapshot retrieved by id.",
    )


@router.post("/paper-decisions/evaluate")
def evaluate_paper_decision(request: ReplayDecisionEvaluateRequest) -> ApiV2Envelope:
    snapshot = build_replay_engine().decide_at(
        symbol=request.symbol,
        sessionDate=request.sessionDate,
        evaluationTimestamp=request.evaluationTimestamp,
        spy1mCandles=request.spy1mCandles,
        spy5mCandles=request.spy5mCandles,
        spy15mCandles=request.spy15mCandles,
        qqqCandles=request.qqqCandles or request.spy1mCandles,
        iwmCandles=request.iwmCandles or request.spy1mCandles,
        priorDayOHLC=request.priorDayOHLC,
        premarket=request.premarket,
        openingRange=request.openingRange,
        breadthComponents=request.breadthComponents,
        economicEventState=request.economicEventState,
    )
    DECISION_SNAPSHOTS[snapshot.snapshotId] = snapshot.model_dump(mode="json")
    return envelope(
        endpoint_version=PAPER_DECISION_ENDPOINT_VERSION,
        payload=paper_decision_payload(snapshot),
        configuration_hash=str(snapshot.effectivePolicy.get("configurationHash") or hash_payload(snapshot.model_dump(mode="json"))),
        explanation="Paper decision evaluation ran the complete V2 decision sequence atomically. Submission is a separate explicit action.",
    )


def build_replay_engine(
    ml_config: SafeMLInferenceConfig | None = None,
    ml_model_artifact: dict[str, Any] | None = None,
) -> EventDrivenReplayEngine:
    return EventDrivenReplayEngine(
        ReplayComponents(
            directionalStrategies=tuple(build_directional_strategies(None)),
            contextModules=(
                RelativeStrengthQqqIwmContext(),
                MarketBreadthMomentumContext(),
                EconomicEventContext(),
                MarketStructureContext(),
                VolumeConfirmationContext(),
                VwapPositionContext(),
            ),
            regimeModule=AdxAtrRegimeClassifier(),
            mlConfig=ml_config or SafeMLInferenceConfig(),
            mlModelArtifact=ml_model_artifact,
        ),
        ReplayEngineConfig(minWarmupCandles=1, configurationHash="api_v2_replay_config"),
    )


def build_directional_strategies(strategy_ids: list[str] | None) -> list[Any]:
    factories = {
        "multi_timeframe_trend_alignment": MultiTimeframeTrendAlignmentStrategy,
        "first_pullback_after_open": FirstPullbackAfterOpenStrategy,
        "vwap_trend_continuation": VwapTrendContinuationStrategy,
        "opening_range_breakout": OpeningRangeBreakoutStrategy,
        "volatility_breakout": VolatilityBreakoutStrategy,
        "failed_breakout_reversal": FailedBreakoutReversalStrategy,
        "liquidity_sweep_reversal": LiquiditySweepReversalStrategy,
        "vwap_mean_reversion": VwapMeanReversionStrategy,
        "bollinger_atr_reversion": BollingerAtrReversionStrategy,
        "gap_continuation_gap_fade": GapContinuationFadeStrategy,
    }
    ids = strategy_ids or list(factories)
    return [factories[resolve_strategy(strategy_id).strategyId]() for strategy_id in ids]


def pass_gate_decision(checked_at: datetime, session_date: date) -> GlobalGateDecision:
    gate = GateResult(
        gateId="api_v2_assumed_pass",
        gateName="API V2 Safety",
        status=GateStatus.PASS,
        blocksTrading=False,
        reasonCodes=["api_v2.no_safety_decision_supplied"],
        explanation="No safety decision was supplied; endpoint used a pass-through decision for standalone ensemble evaluation.",
        checkedAt=checked_at,
        configurationHash="api_v2_pass_gate",
    )
    return GlobalGateDecision(
        status=GateStatus.PASS,
        eligible=True,
        dataReady=True,
        gateResults=[gate],
        reasonCodes=["api_v2.no_safety_decision_supplied"],
        explanation="Standalone ensemble endpoint used caller-provided signals and a pass-through safety decision.",
        checkedAt=checked_at,
        sessionDate=session_date,
        configurationHash="api_v2_pass_gate",
    )


def paper_decision_payload(snapshot: ReplayDecisionSnapshot) -> dict[str, Any]:
    return {
        "snapshotId": snapshot.snapshotId,
        "strategyOutputs": snapshot.strategyOutputs,
        "contextOutputs": snapshot.contextOutputs,
        "regime": snapshot.regimeState,
        "familyEnsemble": snapshot.ensembleDecision,
        "gateResults": snapshot.gateDecision,
        "mlResult": snapshot.mlInference,
        "effectivePolicy": snapshot.effectivePolicy,
        "orderPlan": snapshot.orderPlan,
        "eligibility": {
            "ensembleEligible": bool(snapshot.ensembleDecision.get("eligible")),
            "gatesEligible": bool(snapshot.gateDecision.get("eligible")),
            "orderEligible": bool(snapshot.orderPlan and snapshot.orderPlan.get("eligible")),
            "submissionSeparated": True,
        },
        "explanation": "Complete V2 paper decision evaluated without submitting an order.",
        "versions": {
            "apiVersion": API_V2_VERSION,
            "endpointVersion": PAPER_DECISION_ENDPOINT_VERSION,
            "gateVersion": GLOBAL_GATE_ENGINE_VERSION,
            "engineVersion": snapshot.ensembleDecision.get("engineVersion"),
        },
    }


def derive_current_baseline_decision(request: PaperShadowEvaluateRequest) -> CurrentBaselineDecision:
    from backend.app.ensemble import v1 as ensemble_v1

    rows = [candle.model_dump(mode="json") for candle in request.spy1mCandles]
    prior_close = request.priorDayOHLC.close if request.priorDayOHLC else (request.spy1mCandles[0].open if request.spy1mCandles else 0.0)
    raw_summary = ensemble_v1.vote_summary(rows, float(prior_close), timeframe="1Min") if rows else {"signal": "Hold"}
    signal = normalize_baseline_signal(str(raw_summary.get("signal") or "Hold"))
    return CurrentBaselineDecision(
        decisionTimestampUtc=request.evaluationTimestamp,
        signal=signal,
        wouldTrade=signal != Signal.HOLD,
        orderQuantity=None,
        expectedNotional=None,
        rawDecision=raw_summary,
        explanation="Current baseline decision derived by backend V1 vote summary for paper-shadow comparison.",
    )


def normalize_baseline_signal(value: str) -> Signal:
    normalized = value.strip().upper()
    if normalized == "BUY":
        return Signal.BUY
    if normalized == "SELL":
        return Signal.SELL
    return Signal.HOLD


def envelope(*, endpoint_version: str, payload: dict[str, Any], configuration_hash: str, explanation: str) -> ApiV2Envelope:
    return ApiV2Envelope(
        endpointVersion=endpoint_version,
        configurationHash=configuration_hash or hash_payload(payload),
        payload=payload,
        explanation=explanation,
    )


def hash_payload(payload: Any) -> str:
    serialized = json.dumps(jsonable(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value
