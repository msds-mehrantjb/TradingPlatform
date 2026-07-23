from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any
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
    build_historical_shadow_comparison,
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
    OrderPlan,
    RegimeState,
    Signal,
    StrategySignal,
)
from backend.app.ensemble import FamilyAwareDeterministicEnsemble
from backend.app.gates import GLOBAL_GATE_ENGINE_VERSION, GlobalGateEngine, GlobalGateInput
from backend.app.algorithms.meta_strategy.inference.safe_inference import SafeMLInferenceConfig
from backend.app.algorithms.meta_strategy.strategy_registry import (
    CONTEXT_STRATEGIES as META_STRATEGY_CONTEXT_STRATEGIES,
    DIRECTIONAL_STRATEGIES as META_STRATEGY_DIRECTIONAL_STRATEGIES,
    REGIME_STRATEGIES as META_STRATEGY_REGIME_STRATEGIES,
    SAFETY_STRATEGIES as META_STRATEGY_SAFETY_STRATEGIES,
    MetaStrategyRegistryEntry,
)
from backend.app.algorithms.meta_strategy.versions import META_STRATEGY_ALGORITHM_VERSION
from backend.app.algorithms.regime.strategy_registry import REGIME_STRATEGY_ALIASES, REGIME_STRATEGY_DEFINITIONS
from backend.app.algorithms.voting_ensemble.strategies.registry import (
    STRATEGY_ALIAS_MAP as VOTING_ENSEMBLE_ALIAS_MAP,
    StrategyRegistryEntry as VotingEnsembleStrategyRegistryEntry,
    VOTING_ENSEMBLE_AGGREGATOR_STRATEGIES,
    VOTING_ENSEMBLE_CONTEXT_STRATEGIES,
    VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES,
    VOTING_ENSEMBLE_REGIME_STRATEGIES,
    VOTING_ENSEMBLE_SAFETY_STRATEGIES,
    resolve_strategy as resolve_voting_ensemble_strategy,
)
from backend.app.algorithms.wca.engine import WCA_ENGINE_VERSION
from backend.app.algorithms.wca.strategy_registry import WCA_HARD_FILTER_REGISTRY, WCA_MODIFIER_REGISTRY, WCA_STRATEGY_REGISTRY
from backend.app.algorithms.weighted_voting.catalog import WEIGHTED_VOTING_STRATEGY_CATALOG
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_SERVICE_VERSION
from backend.app.strategies import StrategyEvaluationContext, resolve_strategy
from backend.app.strategies.context import (
    MarketBreadthMomentumContext,
    RelativeStrengthQqqIwmContext,
)
from backend.app.strategies.directional import (
    BollingerAtrReversionStrategy,
    FailedBreakoutReversalStrategy,
    FirstPullbackAfterOpenStrategy,
    LiquiditySweepReversalStrategy,
    MultiTimeframeTrendAlignmentStrategy,
)
from backend.app.strategies.regime import AdxAtrRegimeClassifier
from backend.app.trading_policy import DynamicPolicyInputs, DynamicTradingPolicyEngine


API_V2_VERSION = "api_v2"
PAPER_DECISION_ENDPOINT_VERSION = "paper_decision_evaluate_v1"
APPROVED_VOTING_ENSEMBLE_DIRECTIONAL_IDS = (
    "multi_timeframe_trend_alignment",
    "first_pullback_after_open",
    "failed_breakout_reversal",
    "liquidity_sweep_reversal",
    "bollinger_atr_reversion",
)

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


@router.get("/algorithms/voting-ensemble/inventory")
def voting_ensemble_inventory() -> dict[str, Any]:
    return {
        "algorithmId": "voting_ensemble",
        "engineVersion": "voting_ensemble_v2",
        "modules": {
            "directional": [_voting_ensemble_module_payload(entry) for entry in VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES],
            "context": [_voting_ensemble_module_payload(entry) for entry in VOTING_ENSEMBLE_CONTEXT_STRATEGIES],
            "regime": [_voting_ensemble_module_payload(entry) for entry in VOTING_ENSEMBLE_REGIME_STRATEGIES],
            "safety": [_voting_ensemble_module_payload(entry) for entry in VOTING_ENSEMBLE_SAFETY_STRATEGIES],
            "aggregator": [_voting_ensemble_module_payload(entry) for entry in VOTING_ENSEMBLE_AGGREGATOR_STRATEGIES],
        },
    }


@router.get("/algorithms/meta-strategy/inventory")
def meta_strategy_inventory() -> dict[str, Any]:
    return {
        "algorithmId": "meta_strategy",
        "engineVersion": META_STRATEGY_ALGORITHM_VERSION,
        "modules": {
            "directional": [_meta_strategy_module_payload(entry) for entry in META_STRATEGY_DIRECTIONAL_STRATEGIES],
            "context": [_meta_strategy_module_payload(entry) for entry in META_STRATEGY_CONTEXT_STRATEGIES],
            "regime": [_meta_strategy_module_payload(entry) for entry in META_STRATEGY_REGIME_STRATEGIES],
            "safety": [_meta_strategy_module_payload(entry) for entry in META_STRATEGY_SAFETY_STRATEGIES],
            "aggregator": [],
        },
    }


@router.get("/algorithms/regime/inventory")
def regime_inventory() -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "engineVersion": "regime_strategy_catalog_v3_backend",
        "modules": {
            "directional": [_regime_module_payload(entry, "directional") for entry in REGIME_STRATEGY_DEFINITIONS if entry.role == "directional"],
            "context": [_regime_module_payload(entry, "context") for entry in REGIME_STRATEGY_DEFINITIONS if entry.role == "confirmation"],
            "regime": [_regime_module_payload(entry, "regime") for entry in REGIME_STRATEGY_DEFINITIONS if entry.role == "regime_context"],
            "safety": [_regime_module_payload(entry, "safety") for entry in REGIME_STRATEGY_DEFINITIONS if entry.role == "safety_gate"],
            "aggregator": [],
        },
    }


@router.get("/algorithms/wca/inventory")
def wca_inventory() -> dict[str, Any]:
    regime_modifier_ids = {"adx_trend_strength", "atr_volatility_regime"}
    return {
        "algorithmId": "wca",
        "engineVersion": WCA_ENGINE_VERSION,
        "modules": {
            "directional": [_wca_module_payload(entry, "directional", entry.slug) for entry in WCA_STRATEGY_REGISTRY],
            "context": [_wca_module_payload(entry, "context", entry.slug) for entry in WCA_MODIFIER_REGISTRY if entry.slug not in regime_modifier_ids],
            "regime": [_wca_module_payload(entry, "regime", entry.slug) for entry in WCA_MODIFIER_REGISTRY if entry.slug in regime_modifier_ids],
            "safety": [_wca_module_payload(entry, "safety", entry.slug) for entry in WCA_HARD_FILTER_REGISTRY],
            "aggregator": [],
        },
    }


@router.get("/algorithms/weighted-voting/inventory")
def weighted_voting_inventory() -> dict[str, Any]:
    return {
        "algorithmId": "weighted_voting",
        "engineVersion": WEIGHTED_VOTING_SERVICE_VERSION,
        "modules": {
            "directional": [_weighted_voting_module_payload(entry) for entry in WEIGHTED_VOTING_STRATEGY_CATALOG],
            "context": [],
            "regime": [],
            "safety": [],
            "aggregator": [],
        },
    }


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
        qqqCandles=request.qqqCandles,
        iwmCandles=request.iwmCandles,
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
        qqqCandles=request.qqqCandles,
        iwmCandles=request.iwmCandles,
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


@router.post("/backtests/shadow-comparison")
def run_historical_shadow_comparison(request: HistoricalShadowComparisonRequest) -> ApiV2Envelope:
    replay = build_replay_engine().replay_session(
        symbol=request.symbol,
        sessionDate=request.sessionDate,
        spy1mCandles=request.spy1mCandles,
        spy5mCandles=request.spy5mCandles or request.spy1mCandles,
        spy15mCandles=request.spy15mCandles or request.spy1mCandles,
        qqqCandles=request.qqqCandles,
        iwmCandles=request.iwmCandles,
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
        qqqCandles=request.qqqCandles,
        iwmCandles=request.iwmCandles,
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
        "failed_breakout_reversal": FailedBreakoutReversalStrategy,
        "liquidity_sweep_reversal": LiquiditySweepReversalStrategy,
        "bollinger_atr_reversion": BollingerAtrReversionStrategy,
    }
    ids = strategy_ids or list(APPROVED_VOTING_ENSEMBLE_DIRECTIONAL_IDS)
    strategies: list[Any] = []
    for strategy_id in ids:
        canonical_id = resolve_strategy(strategy_id).strategyId
        if canonical_id not in factories:
            raise HTTPException(status_code=400, detail=f"Strategy is not in the production Voting Ensemble inventory: {strategy_id}")
        strategies.append(factories[canonical_id]())
    return strategies


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


def _voting_ensemble_module_payload(entry: VotingEnsembleStrategyRegistryEntry) -> dict[str, Any]:
    return {
        "id": entry.strategyId,
        "name": entry.strategyName,
        "version": entry.strategyVersion,
        "family": entry.family,
        "role": entry.role,
        "collection": _enum_value(entry.collection).lower(),
        "status": entry.status,
        "enabled": entry.enabled,
        "requiredInputs": list(entry.requiredInputs),
        "evidence": list(entry.evidence),
        "aliases": _voting_ensemble_alias_metadata(entry.strategyId),
    }


def _voting_ensemble_alias_metadata(target_id: str) -> list[dict[str, Any]]:
    aliases: list[dict[str, Any]] = []
    for alias, canonical_id in VOTING_ENSEMBLE_ALIAS_MAP.items():
        entry = resolve_voting_ensemble_strategy(canonical_id)
        if entry.strategyId != target_id or alias in {entry.strategyId, entry.strategyName}:
            continue
        aliases.append(_alias_metadata(alias, entry.strategyId))
    return aliases


def _meta_strategy_module_payload(entry: MetaStrategyRegistryEntry) -> dict[str, Any]:
    return {
        "id": entry.strategy_id,
        "name": entry.strategy_name,
        "version": entry.strategy_version,
        "family": _enum_value(entry.family),
        "role": _enum_value(entry.role),
        "collection": _enum_value(entry.role).lower(),
        "status": "active" if entry.enabled else "shadow",
        "enabled": entry.enabled,
        "requiredInputs": list(entry.required_inputs),
        "minimumWarmup": entry.minimum_warmup,
        "aliases": [_alias_metadata(alias, entry.strategy_id) for alias in entry.aliases if alias not in {entry.strategy_id, entry.strategy_name}],
    }


def _regime_module_payload(entry: Any, collection: str) -> dict[str, Any]:
    return {
        "id": entry.strategy_id,
        "name": entry.name,
        "version": "regime_strategy_catalog_v3_backend",
        "family": entry.family,
        "role": entry.role,
        "collection": collection,
        "status": "active",
        "enabled": True,
        "requiredInputs": [],
        "minimumWarmup": entry.minimum_bars,
        "aliases": [_alias_metadata(alias, entry.strategy_id) for alias, canonical_id in REGIME_STRATEGY_ALIASES.items() if canonical_id == entry.strategy_id],
    }


def _wca_module_payload(entry: Any, collection: str, module_id: str) -> dict[str, Any]:
    return {
        "id": module_id,
        "name": entry.name,
        "version": WCA_ENGINE_VERSION,
        "family": entry.family,
        "role": _enum_value(entry.role),
        "collection": collection,
        "status": "active",
        "enabled": True,
        "requiredInputs": [],
        "aliases": [_alias_metadata(entry.strategy_id, entry.slug)] if collection == "directional" else [],
    }


def _weighted_voting_module_payload(entry: Any) -> dict[str, Any]:
    return {
        "id": entry.strategy_id,
        "name": entry.name,
        "version": entry.version,
        "family": _enum_value(entry.family),
        "role": "DIRECTIONAL",
        "collection": "directional",
        "status": "active" if entry.enabled else "shadow",
        "enabled": entry.enabled,
        "requiredInputs": list(entry.required_data),
        "minimumWarmup": entry.minimum_warmup,
        "aliases": [],
    }


def _alias_metadata(alias: str, canonical_id: str) -> dict[str, Any]:
    return {
        "name": alias,
        "status": "deprecated_alias",
        "aliasFor": canonical_id,
    }


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


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
