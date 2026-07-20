"""Golden-master characterization for the legacy Meta-Strategy behavior.

These fixtures intentionally call the current public V2 components in place:
family-aware deterministic candidate generation, candidate meta-feature
generation, triple-barrier labels, and safe ML inference. They do not read WCA,
Regime, Weighted Voting, or Voting Ensemble private persistence/state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from backend.app.domain.feature_engine import MarketCandle
from backend.app.domain.models import (
    AccountRiskState,
    BaselineTradingSettings,
    ContextSignal,
    DecisionSnapshotV2,
    Direction,
    DynamicPolicyBounds,
    EffectiveTradePolicy,
    EnsembleDecision,
    GateResult,
    GateStatus,
    GlobalGateDecision,
    HardRiskLimits,
    OperatingMode,
    OrderPlan,
    RegimeState,
    Signal,
    StrategySignal,
    TradeCandidate,
)
from backend.app.algorithms.meta_strategy.versions import meta_strategy_version_identifiers
from backend.app.ensemble.family_aware import FamilyAwareDeterministicEnsemble
from backend.app.algorithms.meta_strategy.inference.safe_inference import SafeMLInferenceConfig, apply_safe_ml_inference
from backend.app.algorithms.meta_strategy.labeling.candidate_meta_labeling import MetaLabelExecutionConfig, create_candidate_meta_label
from backend.app.algorithms.meta_strategy.ml_features import MLFeatureSet, build_candidate_meta_features, candidate_meta_feature_schema_hash
from backend.app.strategies.registry import directional_strategy_input_ids, resolve_strategy


META_STRATEGY_CHARACTERIZATION_SCHEMA_VERSION = "meta_strategy_current_behavior_characterization_v1"
NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)
CONFIG_HASH = "meta-strategy-current-behavior-v1"

REQUIRED_CHARACTERIZATION_FIXTURES = {
    "trending_market",
    "range_bound_market",
    "high_volatility_market",
    "low_volatility_market",
    "breakout",
    "failed_breakout",
    "gap_up_session",
    "gap_down_session",
    "event_risk_session",
    "poor_liquidity_session",
    "buy_candidate",
    "sell_candidate",
    "hold_candidate",
    "accepted_ml_candidate",
    "rejected_ml_candidate",
    "missing_feature_fallback",
    "out_of_distribution_fallback",
}


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    signal: Signal
    confidence: float
    reliability: float = 1.0
    regime_fit: float = 1.0
    eligible: bool = True
    data_ready: bool = True
    active: bool = True
    features: dict[str, Any] | None = None


@dataclass(frozen=True)
class ContextSpec:
    context_id: str
    effect: str = "neutral"
    confidence: float = 0.6
    data_ready: bool = True
    features: dict[str, Any] | None = None


@dataclass(frozen=True)
class CaseSpec:
    fixture_id: str
    description: str
    category: str
    strategy_specs: tuple[StrategySpec, ...]
    context_specs: tuple[ContextSpec, ...]
    regime_label: str
    regime_direction: Direction
    volatility: str
    regime_features: dict[str, Any]
    hard_gate_status: GateStatus = GateStatus.PASS
    hard_gate_eligible: bool = True
    feature_values: dict[str, Any] | None = None
    model_probabilities: dict[str, float] | None = None
    ml_config: SafeMLInferenceConfig | None = None
    model_ood_score: float = 0.0
    future_candles: tuple[tuple[float, float, float, float], ...] = (
        (100.0, 100.8, 99.8, 100.4),
        (100.4, 102.3, 100.2, 102.0),
        (102.0, 102.2, 101.6, 101.9),
    )


def build_current_behavior_characterization() -> dict[str, Any]:
    fixtures = [_characterize_case(spec) for spec in _case_specs()]
    payload = {
        "schemaVersion": META_STRATEGY_CHARACTERIZATION_SCHEMA_VERSION,
        "algorithmId": "meta_strategy",
        "versions": meta_strategy_version_identifiers(),
        "sourceBehavior": "legacy_public_v2_components",
        "capturedAtUtc": NOW.isoformat().replace("+00:00", "Z"),
        "sessionDate": SESSION_DATE.isoformat(),
        "dataPolicy": {
            "marketDataFeed": "deterministic_synthetic",
            "containsCredentials": False,
            "containsPrivateAccountInformation": False,
            "requiresLiveFeed": False,
        },
        "featureSchemaHash": candidate_meta_feature_schema_hash(),
        "fixtures": fixtures,
    }
    return payload


def _characterize_case(spec: CaseSpec) -> dict[str, Any]:
    signals = _strategy_signals(spec.strategy_specs)
    contexts = _context_signals(spec.context_specs)
    regime = _regime_state(spec)
    gate = _global_gate(spec)
    deterministic = FamilyAwareDeterministicEnsemble().aggregate(
        strategySignals=signals,
        contextSignals=contexts,
        regimeState=regime,
        safetyDecision=gate,
        decidedAt=NOW,
        sessionDate=SESSION_DATE,
    )
    candidate = _trade_candidate(spec.fixture_id, deterministic)
    order = _order_plan(spec.fixture_id, deterministic)
    snapshot = _snapshot(
        spec=spec,
        signals=signals,
        contexts=contexts,
        regime=regime,
        gate=gate,
        deterministic=deterministic,
        candidate=candidate,
        order=order,
    )
    features = build_candidate_meta_features(snapshot)
    label = create_candidate_meta_label(snapshot, _future_candles(spec, deterministic.signal), _label_config())
    ml_config = spec.ml_config or SafeMLInferenceConfig(mode=OperatingMode.ACTIVE, minSuccessProbability=0.55, minCalibratedProbability=0.55)
    artifact = _model_artifact(features, spec)
    ml_decision = apply_safe_ml_inference(
        deterministic_signal=deterministic.signal,
        feature_set=features,
        model_artifact=artifact,
        config=ml_config,
        hard_gates_passed=gate.eligible and gate.status != GateStatus.FAIL.value,
        candidate_eligible=bool(order and order.eligible),
        session_date=SESSION_DATE,
        predicted_at=NOW,
    )

    return {
        "id": spec.fixture_id,
        "category": spec.category,
        "description": spec.description,
        "directionalStrategyOutputs": _strategy_summary(signals),
        "contextOutputs": _context_summary(contexts),
        "regimeOutput": _regime_summary(regime),
        "familyScores": _family_scores(deterministic),
        "deterministicCandidate": _deterministic_summary(deterministic),
        "candidateGeometry": _candidate_geometry(order),
        "featureVector": _feature_summary(features),
        "featureSchemaHash": features.schemaHash,
        "label": _label_summary(label),
        "modelProbabilities": _probabilities(spec),
        "mlDecision": _ml_decision_summary(ml_decision),
        "riskMultiplier": ml_decision.recommendedRiskCap,
        "finalCandidateStatus": _final_status(ml_decision),
    }


def _case_specs() -> tuple[CaseSpec, ...]:
    active_baseline = SafeMLInferenceConfig(
        mode=OperatingMode.ACTIVE,
        minSuccessProbability=0.55,
        minCalibratedProbability=0.55,
        activeMinRiskCap=0.25,
        activeMaxRiskCap=1.0,
    )
    fallback_on_missing = SafeMLInferenceConfig(
        mode=OperatingMode.ACTIVE,
        minSuccessProbability=0.55,
        minCalibratedProbability=0.55,
        fallbackBehavior="DETERMINISTIC_BASELINE",
        maxFeatureMissingness=0.01,
    )
    fallback_on_ood = SafeMLInferenceConfig(
        mode=OperatingMode.ACTIVE,
        minSuccessProbability=0.55,
        minCalibratedProbability=0.55,
        fallbackBehavior="DETERMINISTIC_BASELINE",
        maxOutOfDistributionScore=0.50,
    )
    return (
        CaseSpec(
            "trending_market",
            "Strong trend context where trend and breakout families support a long candidate.",
            "market_regime",
            (_buy("multi_timeframe_trend_alignment", 0.86), _buy("opening_range_breakout", 0.74)),
            (_context("relative_strength_qqq_iwm", "confirm_long", 0.8), _context("market_breadth_momentum", "confirm_long", 0.7)),
            "strong_trend",
            Direction.LONG,
            "NORMAL",
            {"trendStrengthAdx": 34.0, "atrPercentile": 0.58, "realizedVolatilityPercentile": 0.55, "trendFit": 0.92, "breakoutFit": 0.72, "reversalFit": 0.20, "meanReversionFit": 0.22, "gapSessionFit": 0.30},
            model_probabilities={"BUY": 0.68, "SELL": 0.08, "HOLD": 0.24},
            ml_config=active_baseline,
        ),
        CaseSpec(
            "range_bound_market",
            "Range context where reversal and mean-reversion evidence produce a short candidate.",
            "market_regime",
            (_sell("failed_breakout_reversal", 0.80), _sell("vwap_mean_reversion", 0.72)),
            (_context("market_structure_context", "confirm_short", 0.6),),
            "range",
            Direction.FLAT,
            "NORMAL",
            {"trendStrengthAdx": 15.0, "atrPercentile": 0.42, "realizedVolatilityPercentile": 0.38, "trendFit": 0.24, "breakoutFit": 0.28, "reversalFit": 0.82, "meanReversionFit": 0.86, "gapSessionFit": 0.25},
            model_probabilities={"BUY": 0.12, "SELL": 0.67, "HOLD": 0.21},
            ml_config=active_baseline,
            future_candles=((100.0, 100.2, 99.5, 99.8), (99.8, 99.9, 97.7, 98.0), (98.0, 98.4, 97.9, 98.2)),
        ),
        CaseSpec(
            "high_volatility_market",
            "Volatility expansion with a global gate block despite deterministic long evidence.",
            "market_regime",
            (_buy("volatility_breakout", 0.83), _buy("multi_timeframe_trend_alignment", 0.68)),
            (_context("economic_event_context", "reduce_long", 0.7, {"eventState": "watch", "eventImportance": "medium"}),),
            "event_shock",
            Direction.LONG,
            "EXTREME",
            {"trendStrengthAdx": 26.0, "atrPercentile": 0.95, "realizedVolatilityPercentile": 0.97, "trendFit": 0.60, "breakoutFit": 0.88, "reversalFit": 0.35, "meanReversionFit": 0.20, "gapSessionFit": 0.45},
            hard_gate_status=GateStatus.FAIL,
            hard_gate_eligible=False,
            model_probabilities={"BUY": 0.76, "SELL": 0.06, "HOLD": 0.18},
            ml_config=active_baseline,
        ),
        CaseSpec(
            "low_volatility_market",
            "Low-volatility compression where deterministic score is too weak to create a trade.",
            "market_regime",
            (_buy("volatility_breakout", 0.22), _hold("multi_timeframe_trend_alignment", 0.55)),
            (_context("volume_confirmation", "neutral", 0.4, {"volumeTrend": "flat"}),),
            "low_volatility",
            Direction.FLAT,
            "LOW",
            {"trendStrengthAdx": 12.0, "atrPercentile": 0.12, "realizedVolatilityPercentile": 0.10, "trendFit": 0.30, "breakoutFit": 0.18, "reversalFit": 0.38, "meanReversionFit": 0.42, "gapSessionFit": 0.18},
            model_probabilities={"BUY": 0.41, "SELL": 0.15, "HOLD": 0.44},
            ml_config=active_baseline,
        ),
        CaseSpec(
            "breakout",
            "Breakout case with breakout and trend families independently aligned long.",
            "market_setup",
            (_buy("opening_range_breakout", 0.88), _buy("volatility_breakout", 0.79), _buy("multi_timeframe_trend_alignment", 0.66)),
            (_context("volume_confirmation", "confirm_long", 0.8, {"volumeTrend": "rising"}),),
            "breakout_expansion",
            Direction.LONG,
            "HIGH",
            {"trendStrengthAdx": 24.0, "atrPercentile": 0.74, "realizedVolatilityPercentile": 0.78, "trendFit": 0.70, "breakoutFit": 0.92, "reversalFit": 0.18, "meanReversionFit": 0.20, "gapSessionFit": 0.30},
            model_probabilities={"BUY": 0.70, "SELL": 0.08, "HOLD": 0.22},
            ml_config=active_baseline,
        ),
        CaseSpec(
            "failed_breakout",
            "Failed upside breakout with reversal and mean-reversion families aligned short.",
            "market_setup",
            (_sell("failed_breakout_reversal", 0.86), _sell("bollinger_atr_reversion", 0.70)),
            (_context("market_structure_context", "confirm_short", 0.9, {"breakOfStructure": "bearish_break", "structureQuality": 0.84}),),
            "failed_breakout_reversal",
            Direction.SHORT,
            "NORMAL",
            {"trendStrengthAdx": 18.0, "atrPercentile": 0.55, "realizedVolatilityPercentile": 0.51, "trendFit": 0.26, "breakoutFit": 0.34, "reversalFit": 0.90, "meanReversionFit": 0.70, "gapSessionFit": 0.20},
            model_probabilities={"BUY": 0.10, "SELL": 0.72, "HOLD": 0.18},
            ml_config=active_baseline,
            future_candles=((100.0, 100.2, 99.4, 99.7), (99.7, 99.9, 97.8, 98.1), (98.1, 98.3, 97.9, 98.0)),
        ),
        CaseSpec(
            "gap_up_session",
            "Gap-up continuation candidate with trend confirmation.",
            "market_setup",
            (_buy("gap_continuation_gap_fade", 0.82), _buy("multi_timeframe_trend_alignment", 0.69)),
            (_context("relative_strength_qqq_iwm", "confirm_long", 0.7),),
            "gap_up_continuation",
            Direction.LONG,
            "HIGH",
            {"trendStrengthAdx": 29.0, "atrPercentile": 0.68, "realizedVolatilityPercentile": 0.66, "trendFit": 0.68, "breakoutFit": 0.64, "reversalFit": 0.22, "meanReversionFit": 0.25, "gapSessionFit": 0.90},
            feature_values={"gapPercent": 2.15},
            model_probabilities={"BUY": 0.66, "SELL": 0.10, "HOLD": 0.24},
            ml_config=active_baseline,
        ),
        CaseSpec(
            "gap_down_session",
            "Gap-down continuation candidate with trend confirmation.",
            "market_setup",
            (_sell("gap_continuation_gap_fade", 0.80), _sell("multi_timeframe_trend_alignment", 0.68)),
            (_context("relative_strength_qqq_iwm", "confirm_short", 0.7),),
            "gap_down_continuation",
            Direction.SHORT,
            "HIGH",
            {"trendStrengthAdx": 30.0, "atrPercentile": 0.70, "realizedVolatilityPercentile": 0.68, "trendFit": 0.66, "breakoutFit": 0.62, "reversalFit": 0.24, "meanReversionFit": 0.24, "gapSessionFit": 0.88},
            feature_values={"gapPercent": -2.05},
            model_probabilities={"BUY": 0.08, "SELL": 0.64, "HOLD": 0.28},
            ml_config=active_baseline,
            future_candles=((100.0, 100.2, 99.5, 99.8), (99.8, 99.9, 97.9, 98.2), (98.2, 98.5, 98.0, 98.1)),
        ),
        CaseSpec(
            "event_risk_session",
            "Event-risk session where local/global safety blocks the otherwise valid deterministic setup.",
            "market_safety",
            (_buy("opening_range_breakout", 0.76), _buy("multi_timeframe_trend_alignment", 0.74)),
            (_context("economic_event_context", "veto_long", 1.0, {"eventState": "blackout", "eventImportance": "high"}),),
            "event_blackout",
            Direction.FLAT,
            "EXTREME",
            {"trendStrengthAdx": 22.0, "atrPercentile": 0.93, "realizedVolatilityPercentile": 0.96, "trendFit": 0.44, "breakoutFit": 0.42, "reversalFit": 0.32, "meanReversionFit": 0.20, "gapSessionFit": 0.40},
            hard_gate_status=GateStatus.FAIL,
            hard_gate_eligible=False,
            model_probabilities={"BUY": 0.71, "SELL": 0.07, "HOLD": 0.22},
            ml_config=active_baseline,
        ),
        CaseSpec(
            "poor_liquidity_session",
            "Poor-liquidity session where spread and volume inputs reject candidate application.",
            "market_safety",
            (_buy("opening_range_breakout", 0.72), _buy("multi_timeframe_trend_alignment", 0.70)),
            (_context("volume_confirmation", "reduce_long", 0.8, {"volumeTrend": "thin"}),),
            "poor_liquidity",
            Direction.FLAT,
            "NORMAL",
            {"trendStrengthAdx": 20.0, "atrPercentile": 0.45, "realizedVolatilityPercentile": 0.42, "trendFit": 0.52, "breakoutFit": 0.50, "reversalFit": 0.28, "meanReversionFit": 0.25, "gapSessionFit": 0.18},
            hard_gate_status=GateStatus.FAIL,
            hard_gate_eligible=False,
            feature_values={"spreadDollars": 0.18, "spy1mRelativeVolume": 0.35},
            model_probabilities={"BUY": 0.69, "SELL": 0.09, "HOLD": 0.22},
            ml_config=active_baseline,
        ),
        CaseSpec(
            "buy_candidate",
            "Canonical deterministic buy candidate with complete order geometry.",
            "candidate",
            (_buy("multi_timeframe_trend_alignment", 0.84), _buy("opening_range_breakout", 0.78)),
            (_context("vwap_position_context", "confirm_long", 0.6, {"reclaimRejectionState": "above_rising_vwap", "distanceFromVwapAtr": 0.42}),),
            "weak_trend",
            Direction.LONG,
            "NORMAL",
            {"trendStrengthAdx": 23.0, "atrPercentile": 0.57, "realizedVolatilityPercentile": 0.52, "trendFit": 0.72, "breakoutFit": 0.68, "reversalFit": 0.30, "meanReversionFit": 0.34, "gapSessionFit": 0.25},
            model_probabilities={"BUY": 0.63, "SELL": 0.12, "HOLD": 0.25},
            ml_config=active_baseline,
        ),
        CaseSpec(
            "sell_candidate",
            "Canonical deterministic sell candidate with complete order geometry.",
            "candidate",
            (_sell("failed_breakout_reversal", 0.82), _sell("vwap_mean_reversion", 0.76)),
            (_context("vwap_position_context", "confirm_short", 0.6, {"reclaimRejectionState": "below_falling_vwap", "distanceFromVwapAtr": -0.39}),),
            "range",
            Direction.SHORT,
            "NORMAL",
            {"trendStrengthAdx": 16.0, "atrPercentile": 0.46, "realizedVolatilityPercentile": 0.43, "trendFit": 0.22, "breakoutFit": 0.26, "reversalFit": 0.78, "meanReversionFit": 0.82, "gapSessionFit": 0.20},
            model_probabilities={"BUY": 0.10, "SELL": 0.65, "HOLD": 0.25},
            ml_config=active_baseline,
            future_candles=((100.0, 100.2, 99.4, 99.7), (99.7, 99.8, 97.9, 98.1), (98.1, 98.4, 98.0, 98.2)),
        ),
        CaseSpec(
            "hold_candidate",
            "Diagnostic hold case produced by conflicting directional families.",
            "candidate",
            (_buy("multi_timeframe_trend_alignment", 0.78), _sell("opening_range_breakout", 0.78)),
            (),
            "mixed",
            Direction.FLAT,
            "NORMAL",
            {"trendStrengthAdx": 19.0, "atrPercentile": 0.50, "realizedVolatilityPercentile": 0.49, "trendFit": 0.50, "breakoutFit": 0.50, "reversalFit": 0.45, "meanReversionFit": 0.44, "gapSessionFit": 0.20},
            model_probabilities={"BUY": 0.42, "SELL": 0.40, "HOLD": 0.18},
            ml_config=active_baseline,
        ),
        CaseSpec(
            "accepted_ml_candidate",
            "Active ML accepts the deterministic buy candidate and returns bounded sizing.",
            "ml_policy",
            (_buy("multi_timeframe_trend_alignment", 0.82), _buy("opening_range_breakout", 0.75)),
            (),
            "weak_trend",
            Direction.LONG,
            "NORMAL",
            {"trendStrengthAdx": 24.0, "atrPercentile": 0.54, "realizedVolatilityPercentile": 0.51, "trendFit": 0.70, "breakoutFit": 0.66, "reversalFit": 0.30, "meanReversionFit": 0.30, "gapSessionFit": 0.20},
            model_probabilities={"BUY": 0.78, "SELL": 0.05, "HOLD": 0.17},
            ml_config=active_baseline,
        ),
        CaseSpec(
            "rejected_ml_candidate",
            "Active ML rejects a deterministic buy candidate whose candidate-side probability is below threshold.",
            "ml_policy",
            (_buy("multi_timeframe_trend_alignment", 0.82), _buy("opening_range_breakout", 0.75)),
            (),
            "weak_trend",
            Direction.LONG,
            "NORMAL",
            {"trendStrengthAdx": 24.0, "atrPercentile": 0.54, "realizedVolatilityPercentile": 0.51, "trendFit": 0.70, "breakoutFit": 0.66, "reversalFit": 0.30, "meanReversionFit": 0.30, "gapSessionFit": 0.20},
            model_probabilities={"BUY": 0.31, "SELL": 0.12, "HOLD": 0.57},
            ml_config=active_baseline,
        ),
        CaseSpec(
            "missing_feature_fallback",
            "Missing decision-time features exceed configured missingness tolerance and fall back to deterministic baseline.",
            "ml_fallback",
            (_buy("multi_timeframe_trend_alignment", 0.82), _buy("opening_range_breakout", 0.75)),
            (),
            "unknown",
            Direction.FLAT,
            "NORMAL",
            {},
            feature_values={},
            model_probabilities={"BUY": 0.82, "SELL": 0.04, "HOLD": 0.14},
            ml_config=fallback_on_missing,
        ),
        CaseSpec(
            "out_of_distribution_fallback",
            "Explicit OOD score exceeds configured tolerance and falls back to deterministic baseline.",
            "ml_fallback",
            (_buy("multi_timeframe_trend_alignment", 0.82), _buy("opening_range_breakout", 0.75)),
            (),
            "weak_trend",
            Direction.LONG,
            "NORMAL",
            {"trendStrengthAdx": 24.0, "atrPercentile": 0.54, "realizedVolatilityPercentile": 0.51, "trendFit": 0.70, "breakoutFit": 0.66, "reversalFit": 0.30, "meanReversionFit": 0.30, "gapSessionFit": 0.20},
            model_probabilities={"BUY": 0.82, "SELL": 0.04, "HOLD": 0.14},
            ml_config=fallback_on_ood,
            model_ood_score=0.95,
        ),
    )


def _buy(strategy_id: str, confidence: float) -> StrategySpec:
    return StrategySpec(strategy_id, Signal.BUY, confidence)


def _sell(strategy_id: str, confidence: float) -> StrategySpec:
    return StrategySpec(strategy_id, Signal.SELL, confidence)


def _hold(strategy_id: str, confidence: float) -> StrategySpec:
    return StrategySpec(strategy_id, Signal.HOLD, confidence)


def _context(context_id: str, effect: str, confidence: float, features: dict[str, Any] | None = None) -> ContextSpec:
    return ContextSpec(context_id, effect, confidence, features=features)


def _strategy_signals(specs: tuple[StrategySpec, ...]) -> list[StrategySignal]:
    by_id = {spec.strategy_id: spec for spec in specs}
    return [_strategy_signal(by_id.get(strategy_id) or StrategySpec(strategy_id, Signal.HOLD, 0.0)) for strategy_id in directional_strategy_input_ids()]


def _strategy_signal(spec: StrategySpec) -> StrategySignal:
    entry = resolve_strategy(spec.strategy_id)
    direction = {Signal.BUY: Direction.LONG, Signal.SELL: Direction.SHORT, Signal.HOLD: Direction.FLAT}[spec.signal]
    return StrategySignal(
        strategyId=entry.strategyId,
        strategyName=entry.strategyName,
        strategyVersion=entry.strategyVersion,
        family=entry.family,
        role=entry.role,
        signal=spec.signal,
        direction=direction,
        confidence=spec.confidence,
        active=spec.active,
        eligible=spec.eligible,
        dataReady=spec.data_ready,
        setupDetected=spec.signal != Signal.HOLD,
        regimeFit=spec.regime_fit,
        reliability=spec.reliability,
        structuralInvalidationPrice=99.0 if spec.signal == Signal.BUY else 101.0 if spec.signal == Signal.SELL else None,
        reasonCodes=[f"characterization.{spec.strategy_id}.{spec.signal.value.lower()}"],
        explanation=f"Deterministic characterization output for {entry.strategyName}.",
        features=spec.features or {},
        requiredInputs=list(entry.requiredInputs),
        inputTimestamps={name: NOW for name in entry.requiredInputs},
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def _context_signals(specs: tuple[ContextSpec, ...]) -> list[ContextSignal]:
    return [_context_signal(spec) for spec in specs]


def _context_signal(spec: ContextSpec) -> ContextSignal:
    base_features = {
        "contextEffect": spec.effect,
        "maxConfidenceAdjustment": 0.08,
        "primaryRelativeReturn": 0.003,
        "normalizedRelativeStrengthScore": 0.62,
        "dataCoverage": 0.88,
        "eventState": "none",
        "eventImportance": "low",
        "breakOfStructure": "none",
        "structureQuality": 0.5,
        "volumeTrend": "normal",
        "reclaimRejectionState": "near_vwap",
        "distanceFromVwapAtr": 0.0,
    }
    return ContextSignal(
        contextId=spec.context_id,
        signal=Signal.HOLD,
        direction=Direction.FLAT,
        confidence=spec.confidence,
        dataReady=spec.data_ready,
        explanation=f"Deterministic characterization context for {spec.context_id}.",
        features={**base_features, **(spec.features or {})},
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def _regime_state(spec: CaseSpec) -> RegimeState:
    return RegimeState(
        regimeId="legacy_characterized_regime",
        label=spec.regime_label,
        direction=spec.regime_direction,
        volatility=spec.volatility,
        confidence=0.72 if spec.regime_features else 0.35,
        features=spec.regime_features,
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def _global_gate(spec: CaseSpec) -> GlobalGateDecision:
    gate = GateResult(
        gateId="characterization_global_gate",
        gateName="Characterization Global Gate",
        status=spec.hard_gate_status,
        blocksTrading=not spec.hard_gate_eligible,
        reasonCodes=[f"characterization.global_gate.{spec.hard_gate_status.value.lower()}"],
        explanation="Deterministic characterization global gate.",
        checkedAt=NOW,
        configurationHash=CONFIG_HASH,
    )
    return GlobalGateDecision(
        status=spec.hard_gate_status,
        eligible=spec.hard_gate_eligible,
        dataReady=True,
        gateResults=[gate],
        reasonCodes=gate.reasonCodes,
        explanation=gate.explanation,
        checkedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def _trade_candidate(fixture_id: str, decision: EnsembleDecision) -> TradeCandidate | None:
    if decision.signal == Signal.HOLD.value:
        return None
    side = Signal(decision.signal)
    stop, target = _stop_target(side)
    return TradeCandidate(
        candidateId=f"{fixture_id}-candidate",
        symbol="SPY",
        signal=side,
        direction=Direction.LONG if side == Signal.BUY else Direction.SHORT,
        entryPrice=100.0,
        stopPrice=stop,
        targetPrice=target,
        quantity=10,
        confidence=decision.confidence,
        expectedValue=round(abs(decision.finalScore), 4),
        features={"sourceDecisionId": decision.decisionId},
        reasonCodes=["characterization.deterministic_candidate"],
        explanation="Legacy deterministic candidate geometry captured for Meta-Strategy characterization.",
        generatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def _order_plan(fixture_id: str, decision: EnsembleDecision) -> OrderPlan | None:
    if decision.signal == Signal.HOLD.value:
        return None
    side = Signal(decision.signal)
    stop, target = _stop_target(side)
    return OrderPlan(
        orderPlanId=f"{fixture_id}-order-plan",
        candidateId=f"{fixture_id}-candidate",
        symbol="SPY",
        side=side,
        orderType="STOP_LIMIT",
        quantity=10,
        entryPrice=100.0,
        stopPrice=stop,
        targetPrice=target,
        limitPrice=100.02 if side == Signal.BUY else 99.98,
        maximumHoldingMinutes=30,
        strategyInvalidationPrice=stop,
        timeInForce="DAY",
        eligible=True,
        explanation="Legacy order intent geometry captured for Meta-Strategy characterization.",
        generatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def _stop_target(side: Signal) -> tuple[float, float]:
    if side == Signal.BUY:
        return 99.0, 102.0
    return 101.0, 98.0


def _snapshot(
    *,
    spec: CaseSpec,
    signals: list[StrategySignal],
    contexts: list[ContextSignal],
    regime: RegimeState,
    gate: GlobalGateDecision,
    deterministic: EnsembleDecision,
    candidate: TradeCandidate | None,
    order: OrderPlan | None,
) -> DecisionSnapshotV2:
    return DecisionSnapshotV2(
        snapshotId=f"{spec.fixture_id}-snapshot",
        codeVersion="characterization",
        symbol="SPY",
        marketDataFeed="deterministic_synthetic",
        sessionDate=SESSION_DATE,
        sessionDateNewYork=SESSION_DATE,
        decisionTimestamp=NOW,
        decisionTimestampUtc=NOW,
        operatingMode=OperatingMode.SHADOW,
        dataQuality={"dataReady": True, "eligibleForTraining": True},
        rawMarketReferences={"spy1mCandles": [{"provider": "deterministic_synthetic", "timestamp": NOW.isoformat()}]},
        featureSnapshot=_feature_snapshot(spec),
        strategySignals=signals,
        directionalStrategyOutputs=signals,
        contextSignals=contexts,
        contextOutputs=contexts,
        regimeState=regime,
        ensembleDecision=deterministic,
        globalGateDecision=gate,
        effectiveTradePolicy=_policy(),
        tradeCandidate=candidate,
        orderPlan=order,
        fillResult=None,
        positionState={"quantity": 0},
        finalOutcome=None,
        eligibleForTraining=True,
        explanation="Meta-Strategy current-behavior characterization snapshot.",
        engineVersion="meta_strategy_current_behavior_characterization_v1",
        strategyConfigurationHash=CONFIG_HASH,
        tradingSettingsHash=CONFIG_HASH,
        configurationHash=CONFIG_HASH,
    )


def _feature_snapshot(spec: CaseSpec) -> dict[str, Any]:
    values = {
        "spreadDollars": 0.02,
        "spy1mRelativeVolume": 1.25,
        "spy1mClose": 100.1,
        "estimatedSlippage": 0.02,
    }
    if spec.feature_values is not None:
        values.update(spec.feature_values)
    return {
        "engineVersion": "point_in_time_feature_engine_v1",
        "eligibleForTraining": True,
        "dataReady": True,
        "features": {key: {"value": value, "quality": "OK", "timestamp": NOW.isoformat()} for key, value in values.items()},
        "rawInputs": {"provider": "deterministic_synthetic"},
    }


def _policy() -> EffectiveTradePolicy:
    return EffectiveTradePolicy(
        mode=OperatingMode.SHADOW,
        baselineSettings=BaselineTradingSettings(
            startingCapital=25000,
            orderAllocationPercent=10,
            dailyAllocationPercent=30,
            riskBudgetPercentOfOrder=50,
            maxTradesPerDay=3,
            stopLossPercent=0.35,
            fixedStopDistanceDollars=1,
            takeProfitR=2.0,
            slippagePerShare=0.02,
            positionSizingMode="allocation",
            settingsVersion="characterization-baseline-v1",
            configurationHash=CONFIG_HASH,
        ),
        hardRiskLimits=HardRiskLimits(
            maxDailyLossPercent=2,
            maxOrderNotional=2500,
            maxPositionNotional=12500,
            maxShareQuantity=100,
            minStopDistanceDollars=0.05,
            maxSlippagePerShare=0.05,
            configurationHash=CONFIG_HASH,
        ),
        dynamicBounds=DynamicPolicyBounds(
            minConfidence=0.6,
            minReliability=0.5,
            minRegimeFit=0.5,
            maxSpreadPercent=0.03,
            maxParticipationPercent=0.3,
            minLiquidityShares=10000,
            configurationHash=CONFIG_HASH,
        ),
        accountRiskState=AccountRiskState(
            accountId="paper-account-characterization",
            equity=25000,
            buyingPower=10000,
            openPositionNotional=0,
            realizedPnlToday=0,
            tradesToday=0,
            observedAt=NOW,
            sessionDate=SESSION_DATE,
        ),
        maxQuantity=25,
        maxNotional=2500,
        riskDollars=50,
        explanation="Characterization policy snapshot.",
        effectiveAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def _label_config() -> MetaLabelExecutionConfig:
    return MetaLabelExecutionConfig(
        maxHoldingPeriodMinutes=5,
        spreadDollars=0.02,
        slippagePerShare=0.01,
        feesPerShare=0.005,
        flatFeePerOrder=0.10,
        configurationHash="meta-strategy-characterization-label-v1",
    )


def _future_candles(spec: CaseSpec, side: Signal | str) -> list[MarketCandle]:
    signal = Signal(side)
    if signal == Signal.HOLD:
        rows = ((100.0, 100.1, 99.9, 100.0),)
    else:
        rows = spec.future_candles
    return [
        MarketCandle(
            timestamp=NOW + timedelta(minutes=index + 1),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=100000 + (index * 1000),
            tradeCount=1000 + (index * 10),
            symbol="SPY",
            timeframe="1Min",
        )
        for index, (open_price, high, low, close) in enumerate(rows)
    ]


def _model_artifact(feature_set: MLFeatureSet, spec: CaseSpec) -> dict[str, Any]:
    return {
        "featureSchemaHash": feature_set.schemaHash,
        "championModel": "logistic_regression_champion",
        "models": {
            "logistic_regression_champion": {
                "available": True,
                "kind": "fixed_probability_test_model",
                "featureSchemaHash": feature_set.schemaHash,
                "fixedProbabilities": _probabilities(spec),
                "outOfDistributionScore": spec.model_ood_score,
                "modelHealthScore": 1.0,
                "calibration": {"method": "none"},
            }
        },
    }


def _probabilities(spec: CaseSpec) -> dict[str, float]:
    return spec.model_probabilities or {"BUY": 0.34, "SELL": 0.33, "HOLD": 0.33}


def _strategy_summary(signals: list[StrategySignal]) -> dict[str, dict[str, Any]]:
    return {
        signal.strategyId: {
            "family": signal.family,
            "signal": signal.signal,
            "direction": int(signal.direction),
            "confidence": round(signal.confidence, 4),
            "eligible": signal.eligible,
            "active": signal.active,
            "dataReady": signal.dataReady,
            "regimeFit": round(signal.regimeFit, 4),
            "reliability": round(signal.reliability, 4),
        }
        for signal in signals
    }


def _context_summary(contexts: list[ContextSignal]) -> list[dict[str, Any]]:
    return [
        {
            "contextId": context.contextId,
            "confidence": round(context.confidence, 4),
            "dataReady": context.dataReady,
            "features": {
                key: context.features[key]
                for key in sorted(context.features)
                if key in {"contextEffect", "eventState", "eventImportance", "volumeTrend", "reclaimRejectionState", "distanceFromVwapAtr"}
            },
        }
        for context in contexts
    ]


def _regime_summary(regime: RegimeState) -> dict[str, Any]:
    return {
        "regimeId": regime.regimeId,
        "label": regime.label,
        "direction": int(regime.direction),
        "volatility": regime.volatility,
        "confidence": round(regime.confidence, 4),
        "features": {key: regime.features[key] for key in sorted(regime.features)},
    }


def _family_scores(decision: EnsembleDecision) -> list[dict[str, Any]]:
    return [
        {
            "family": score.family,
            "buyScore": round(score.buyScore, 4),
            "sellScore": round(score.sellScore, 4),
            "holdScore": round(score.holdScore, 4),
            "confidence": round(score.confidence, 4),
            "reliability": round(score.reliability, 4),
        }
        for score in decision.familyScores
    ]


def _deterministic_summary(decision: EnsembleDecision) -> dict[str, Any]:
    return {
        "decisionId": decision.decisionId,
        "signal": decision.signal,
        "direction": int(decision.direction),
        "confidence": round(decision.confidence, 4),
        "rawScore": round(decision.rawScore, 4),
        "finalScore": round(decision.finalScore, 4),
        "buyConfidence": round(decision.buyConfidence, 4),
        "sellConfidence": round(decision.sellConfidence, 4),
        "holdConfidence": round(decision.holdConfidence, 4),
        "eligible": decision.eligible,
        "dataReady": decision.dataReady,
        "supportingFamilies": decision.supportingFamilies,
        "opposingFamilies": decision.opposingFamilies,
        "reasonCodes": decision.reasonCodes,
        "engineVersion": decision.engineVersion,
    }


def _candidate_geometry(order: OrderPlan | None) -> dict[str, Any]:
    if order is None:
        return {"candidateId": None, "entryPrice": None, "stopPrice": None, "targetPrice": None, "quantity": 0, "orderType": "NO_ORDER", "eligible": False}
    return {
        "candidateId": order.candidateId,
        "entryPrice": order.entryPrice,
        "stopPrice": order.stopPrice,
        "targetPrice": order.targetPrice,
        "limitPrice": order.limitPrice,
        "quantity": order.quantity,
        "orderType": order.orderType,
        "eligible": order.eligible,
    }


def _feature_summary(features: MLFeatureSet) -> dict[str, Any]:
    selected_keys = (
        "candidate_side",
        "deterministic_score",
        "signal_margin",
        "regime_category",
        "family_trend_score",
        "family_breakout_score",
        "family_reversal_score",
        "family_mean_reversion_score",
        "family_gap_session_score",
        "spread_dollars",
        "relative_volume",
        "entry_distance",
        "stop_distance",
        "target_distance",
        "reward_risk_ratio",
        "expected_transaction_cost",
        "strongest_family",
        "strongest_family_score",
        "weakest_family",
        "weakest_family_score",
    )
    return {
        "schemaVersion": features.schemaVersion,
        "schemaHash": features.schemaHash,
        "featureCount": len(features.featureValues),
        "missingFeatureCount": sum(1 for value in features.missingIndicators.values() if value),
        "completeFeatureVectorHash": _hash(features.featureValues),
        "selectedValues": {key: features.featureValues.get(key) for key in selected_keys},
    }


def _label_summary(label) -> dict[str, Any]:
    return {
        "labelId": label.labelId,
        "labelVersion": label.labelVersion,
        "candidateSide": label.candidateSide,
        "entryPrice": label.entryPrice,
        "protectiveStopPrice": label.protectiveStopPrice,
        "profitTargetPrice": label.profitTargetPrice,
        "firstBarrierHit": label.firstBarrierHit,
        "strictOutcomeLabel": label.strictOutcomeLabel,
        "costAdjustedTrainingLabel": label.costAdjustedTrainingLabel,
        "eligibleForTraining": label.eligibleForTraining,
        "reasonCodes": label.reasonCodes,
    }


def _ml_decision_summary(ml_decision) -> dict[str, Any]:
    return {
        "mode": ml_decision.mode,
        "effectiveMode": ml_decision.effectiveMode,
        "deterministicSignal": ml_decision.deterministicSignal,
        "finalSignal": ml_decision.finalSignal,
        "candidateAccepted": ml_decision.candidateAccepted,
        "mlWouldAcceptCandidate": ml_decision.mlWouldAcceptCandidate,
        "appliedToOrder": ml_decision.appliedToOrder,
        "successProbability": ml_decision.successProbability,
        "calibratedProbability": ml_decision.calibratedProbability,
        "expectedValueAfterCosts": ml_decision.expectedValueAfterCosts,
        "outOfDistributionScore": ml_decision.outOfDistributionScore,
        "featureMissingness": ml_decision.featureMissingness,
        "modelHealth": ml_decision.modelHealth,
        "recommendedRiskCap": ml_decision.recommendedRiskCap,
        "reasonCodes": ml_decision.reasonCodes,
    }


def _final_status(ml_decision) -> str:
    if ml_decision.finalSignal == Signal.HOLD.value and ml_decision.deterministicSignal == Signal.HOLD.value:
        return "HOLD_DIAGNOSTIC"
    if ml_decision.effectiveMode == OperatingMode.FALLBACK.value and ml_decision.candidateAccepted:
        return "FALLBACK_ACCEPTED"
    if ml_decision.candidateAccepted:
        return "ACCEPTED"
    return "REJECTED"


def _hash(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
