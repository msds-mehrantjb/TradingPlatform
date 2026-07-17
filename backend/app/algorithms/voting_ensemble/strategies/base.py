from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from backend.app.domain.feature_engine import FeatureQuality, PointInTimeFeatureSnapshot
from backend.app.domain.models import Direction, Signal, StrategyRole, StrategySignal
from backend.app.algorithms.voting_ensemble.strategies.registry import StrategyCollection, StrategyRegistryEntry


FORBIDDEN_DIRECTION_PROXY_INPUTS = frozenset(
    {
        "session.directionBias",
        "event.directionBias",
        "session_direction_bias",
        "event_direction_bias",
    }
)


@dataclass(frozen=True)
class StrategyEvaluationContext:
    registryEntry: StrategyRegistryEntry
    featureSnapshot: PointInTimeFeatureSnapshot
    configurationHash: str

    @property
    def evaluatedAt(self) -> datetime:
        return self.featureSnapshot.evaluationTimestamp

    @property
    def sessionDate(self) -> date:
        return self.featureSnapshot.sessionDate


class DirectionalStrategy(Protocol):
    registryEntry: StrategyRegistryEntry

    def evaluate(self, context: StrategyEvaluationContext) -> StrategySignal:
        ...


def assert_directional_strategy_entry(entry: StrategyRegistryEntry) -> None:
    if entry.collection != StrategyCollection.DIRECTIONAL.value or entry.role != StrategyRole.DIRECTIONAL.value:
        raise ValueError(f"{entry.strategyName} is not a directional strategy")


def direction_for_signal(signal: Signal) -> Direction:
    if signal == Signal.BUY:
        return Direction.LONG
    if signal == Signal.SELL:
        return Direction.SHORT
    return Direction.FLAT


def required_features_ready(snapshot: PointInTimeFeatureSnapshot, required_feature_names: list[str] | tuple[str, ...]) -> bool:
    return not missing_required_features(snapshot, required_feature_names)


def missing_required_features(snapshot: PointInTimeFeatureSnapshot, required_feature_names: list[str] | tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for name in required_feature_names:
        feature = snapshot.features.get(name)
        if not feature or feature.quality != FeatureQuality.READY.value:
            missing.append(name)
    return missing


def feature_payload(snapshot: PointInTimeFeatureSnapshot, feature_names: list[str] | tuple[str, ...]) -> dict[str, Any]:
    return {
        name: snapshot.features[name].model_dump(mode="json")
        for name in feature_names
        if name in snapshot.features
    }


def input_timestamps(snapshot: PointInTimeFeatureSnapshot, feature_names: list[str] | tuple[str, ...]) -> dict[str, datetime]:
    timestamps: dict[str, datetime] = {}
    for name in feature_names:
        feature = snapshot.features.get(name)
        if feature and feature.sourceTimestamp:
            timestamps[name] = feature.sourceTimestamp
    return timestamps


def validate_no_direction_proxy_inputs(feature_names: list[str] | tuple[str, ...]) -> None:
    forbidden = sorted(set(feature_names).intersection(FORBIDDEN_DIRECTION_PROXY_INPUTS))
    if forbidden:
        raise ValueError(f"directional strategies cannot use proxy direction inputs: {', '.join(forbidden)}")


def unavailable_signal(
    context: StrategyEvaluationContext,
    *,
    requiredFeatureNames: list[str] | tuple[str, ...],
    explanation: str | None = None,
) -> StrategySignal:
    validate_no_direction_proxy_inputs(requiredFeatureNames)
    assert_directional_strategy_entry(context.registryEntry)
    missing = missing_required_features(context.featureSnapshot, requiredFeatureNames)
    reason_codes = ["required_data_unavailable", *[f"missing_or_unready:{name}" for name in missing]]
    return _strategy_signal(
        context,
        signal=Signal.HOLD,
        confidence=0.0,
        active=context.registryEntry.enabled,
        eligible=False,
        dataReady=False,
        setupDetected=False,
        regimeFit=0.0,
        reliability=0.0,
        structuralInvalidationPrice=None,
        reasonCodes=reason_codes,
        explanation=explanation or f"Required data unavailable for {context.registryEntry.strategyName}.",
        featureNames=requiredFeatureNames,
    )


def hold_signal(
    context: StrategyEvaluationContext,
    *,
    confidence: float,
    setupDetected: bool,
    regimeFit: float,
    reliability: float,
    reasonCodes: list[str],
    explanation: str,
    featureNames: list[str] | tuple[str, ...],
    structuralInvalidationPrice: float | None = None,
) -> StrategySignal:
    return strategy_signal(
        context,
        signal=Signal.HOLD,
        confidence=confidence,
        eligible=False,
        setupDetected=setupDetected,
        regimeFit=regimeFit,
        reliability=reliability,
        reasonCodes=reasonCodes,
        explanation=explanation,
        featureNames=featureNames,
        structuralInvalidationPrice=structuralInvalidationPrice,
    )


def strategy_signal(
    context: StrategyEvaluationContext,
    *,
    signal: Signal,
    confidence: float,
    eligible: bool,
    setupDetected: bool,
    regimeFit: float,
    reliability: float,
    reasonCodes: list[str],
    explanation: str,
    featureNames: list[str] | tuple[str, ...],
    structuralInvalidationPrice: float | None = None,
) -> StrategySignal:
    validate_no_direction_proxy_inputs(featureNames)
    assert_directional_strategy_entry(context.registryEntry)
    missing = missing_required_features(context.featureSnapshot, featureNames)
    if missing:
        return unavailable_signal(context, requiredFeatureNames=featureNames)
    return _strategy_signal(
        context,
        signal=signal,
        confidence=confidence,
        active=context.registryEntry.enabled,
        eligible=bool(eligible and signal != Signal.HOLD and context.registryEntry.enabled),
        dataReady=True,
        setupDetected=setupDetected,
        regimeFit=regimeFit,
        reliability=reliability,
        structuralInvalidationPrice=structuralInvalidationPrice,
        reasonCodes=reasonCodes,
        explanation=explanation,
        featureNames=featureNames,
    )


def _strategy_signal(
    context: StrategyEvaluationContext,
    *,
    signal: Signal,
    confidence: float,
    active: bool,
    eligible: bool,
    dataReady: bool,
    setupDetected: bool,
    regimeFit: float,
    reliability: float,
    structuralInvalidationPrice: float | None,
    reasonCodes: list[str],
    explanation: str,
    featureNames: list[str] | tuple[str, ...],
) -> StrategySignal:
    return StrategySignal(
        strategyId=context.registryEntry.strategyId,
        strategyName=context.registryEntry.strategyName,
        strategyVersion=context.registryEntry.strategyVersion,
        family=context.registryEntry.family,
        role=context.registryEntry.role,
        signal=signal,
        direction=direction_for_signal(signal),
        confidence=confidence,
        active=active,
        eligible=eligible,
        dataReady=dataReady,
        setupDetected=setupDetected,
        regimeFit=regimeFit,
        reliability=reliability,
        structuralInvalidationPrice=structuralInvalidationPrice,
        reasonCodes=reasonCodes,
        explanation=explanation,
        features=feature_payload(context.featureSnapshot, featureNames),
        requiredInputs=list(context.registryEntry.requiredInputs),
        inputTimestamps=input_timestamps(context.featureSnapshot, featureNames),
        evaluatedAt=context.evaluatedAt,
        sessionDate=context.sessionDate,
        configurationHash=context.configurationHash,
    )
