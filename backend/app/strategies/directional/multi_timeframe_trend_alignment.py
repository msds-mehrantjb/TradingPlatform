from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.models import Signal, StrategySignal
from backend.app.strategies.base import (
    StrategyEvaluationContext,
    hold_signal,
    required_features_ready,
    strategy_signal,
    unavailable_signal,
)
from backend.app.strategies.registry import resolve_strategy


TIMEFRAMES: tuple[Literal["1m", "5m", "15m"], ...] = ("1m", "5m", "15m")


class MultiTimeframeTrendAlignmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "multi_timeframe_trend_alignment_v1"
    bullishThreshold: float = Field(default=0.24, ge=0, le=1)
    bearishThreshold: float = Field(default=-0.24, ge=-1, le=0)
    strongBearishThreshold: float = Field(default=-0.55, ge=-1, le=0)
    strongBullishThreshold: float = Field(default=0.55, ge=0, le=1)
    entryUsableScore: float = Field(default=0.18, ge=0, le=1)
    minAlignedTimeframes: int = Field(default=2, ge=1, le=3)
    minSlopeMagnitude: float = Field(default=0.00005, ge=0)
    minMomentumMagnitude: float = Field(default=0.0002, ge=0)
    momentumLookbackCandles: int = Field(default=3, ge=1, le=20)
    emaRelationWeight: float = Field(default=0.28, ge=0)
    emaSlopeWeight: float = Field(default=0.2, ge=0)
    priceVwapWeight: float = Field(default=0.18, ge=0)
    vwapSlopeWeight: float = Field(default=0.12, ge=0)
    structureWeight: float = Field(default=0.12, ge=0)
    momentumWeight: float = Field(default=0.1, ge=0)

    @property
    def configurationHash(self) -> str:
        payload = self.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]

    @property
    def totalWeight(self) -> float:
        return (
            self.emaRelationWeight
            + self.emaSlopeWeight
            + self.priceVwapWeight
            + self.vwapSlopeWeight
            + self.structureWeight
            + self.momentumWeight
        )


@dataclass(frozen=True)
class TimeframeTrendState:
    timeframe: str
    score: float
    emaRelation: int
    emaSlope: int
    priceVwap: int
    vwapSlope: int
    structure: int
    momentum: int
    latestClose: float
    invalidationPrice: float | None


class MultiTimeframeTrendAlignmentStrategy:
    registryEntry = resolve_strategy("multi_timeframe_trend_alignment")

    def __init__(self, config: MultiTimeframeTrendAlignmentConfig | None = None) -> None:
        self.config = config or MultiTimeframeTrendAlignmentConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> StrategySignal:
        required_features = self.required_feature_names()
        if not context.featureSnapshot.dataReady:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Feature snapshot is not ready for multi-timeframe trend alignment.",
            )
        if not required_features_ready(context.featureSnapshot, required_features):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Required feature measurements are unavailable for multi-timeframe trend alignment.",
            )

        states = [self._timeframe_state(context, timeframe) for timeframe in TIMEFRAMES]
        if any(state is None for state in states):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Required timeframe measurements are unavailable for multi-timeframe trend alignment.",
            )
        ready_states = [state for state in states if state is not None]

        bullish_count = sum(1 for state in ready_states if state.score >= self.config.bullishThreshold)
        bearish_count = sum(1 for state in ready_states if state.score <= self.config.bearishThreshold)
        strong_bearish_count = sum(1 for state in ready_states if state.score <= self.config.strongBearishThreshold)
        strong_bullish_count = sum(1 for state in ready_states if state.score >= self.config.strongBullishThreshold)
        shortest = ready_states[0]

        buy_entry_usable = shortest.score >= self.config.entryUsableScore
        sell_entry_usable = shortest.score <= -self.config.entryUsableScore
        trend_strength = sum(abs(state.score) for state in ready_states) / len(ready_states)
        slope_consistency = max(
            sum(1 for state in ready_states if state.emaSlope > 0),
            sum(1 for state in ready_states if state.emaSlope < 0),
        ) / len(ready_states)
        structure_consistency = max(
            sum(1 for state in ready_states if state.structure > 0),
            sum(1 for state in ready_states if state.structure < 0),
        ) / len(ready_states)
        data_quality = self._data_quality(context)

        if (
            bullish_count >= self.config.minAlignedTimeframes
            and strong_bearish_count == 0
            and buy_entry_usable
        ):
            confidence = self._confidence(bullish_count, trend_strength, slope_consistency, structure_consistency, data_quality)
            return strategy_signal(
                context,
                signal=Signal.BUY,
                confidence=confidence,
                eligible=True,
                setupDetected=True,
                regimeFit=self._regime_fit(ready_states, Signal.BUY),
                reliability=self._reliability(slope_consistency, structure_consistency, data_quality),
                reasonCodes=["multi_timeframe.bullish_alignment", f"aligned_timeframes:{bullish_count}"],
                explanation=self._explanation(Signal.BUY, ready_states),
                featureNames=required_features,
                structuralInvalidationPrice=self._buy_invalidation(ready_states),
            )

        if (
            bearish_count >= self.config.minAlignedTimeframes
            and strong_bullish_count == 0
            and sell_entry_usable
        ):
            confidence = self._confidence(bearish_count, trend_strength, slope_consistency, structure_consistency, data_quality)
            return strategy_signal(
                context,
                signal=Signal.SELL,
                confidence=confidence,
                eligible=True,
                setupDetected=True,
                regimeFit=self._regime_fit(ready_states, Signal.SELL),
                reliability=self._reliability(slope_consistency, structure_consistency, data_quality),
                reasonCodes=["multi_timeframe.bearish_alignment", f"aligned_timeframes:{bearish_count}"],
                explanation=self._explanation(Signal.SELL, ready_states),
                featureNames=required_features,
                structuralInvalidationPrice=self._sell_invalidation(ready_states),
            )

        conflict = bullish_count > 0 and bearish_count > 0
        return hold_signal(
            context,
            confidence=self._hold_confidence(ready_states, data_quality),
            setupDetected=bool(bullish_count or bearish_count),
            regimeFit=0.5,
            reliability=self._reliability(slope_consistency, structure_consistency, data_quality),
            reasonCodes=["multi_timeframe.conflict" if conflict else "multi_timeframe.weak_evidence"],
            explanation=self._hold_explanation(ready_states, conflict=conflict),
            featureNames=required_features,
        )

    def required_feature_names(self) -> tuple[str, ...]:
        names: list[str] = ["sessionVwap", "sessionVwapSlope"]
        for timeframe in TIMEFRAMES:
            prefix = f"spy{timeframe}"
            names.extend(
                [
                    f"{prefix}Ema9",
                    f"{prefix}Ema20",
                    f"{prefix}Ema9Slope",
                    f"{prefix}Ema20Slope",
                    f"{prefix}HigherHighHigherLow",
                    f"{prefix}LowerHighLowerLow",
                    f"{prefix}RollingHigh20",
                    f"{prefix}RollingLow20",
                ]
            )
        return tuple(names)

    def _timeframe_state(self, context: StrategyEvaluationContext, timeframe: str) -> TimeframeTrendState | None:
        features = context.featureSnapshot.features
        prefix = f"spy{timeframe}"
        raw_key = f"spy{timeframe}Candles"
        candles = context.featureSnapshot.rawInputs.get(raw_key) or []
        if len(candles) <= self.config.momentumLookbackCandles:
            return None
        latest = candles[-1]
        previous = candles[-1 - self.config.momentumLookbackCandles]
        latest_close = float(latest["close"])
        previous_close = float(previous["close"])
        ema9 = _number(features[f"{prefix}Ema9"].value)
        ema20 = _number(features[f"{prefix}Ema20"].value)
        ema9_slope = _number(features[f"{prefix}Ema9Slope"].value)
        ema20_slope = _number(features[f"{prefix}Ema20Slope"].value)
        session_vwap = _number(features["sessionVwap"].value)
        session_vwap_slope = _number(features["sessionVwapSlope"].value)
        higher_structure = bool(features[f"{prefix}HigherHighHigherLow"].value)
        lower_structure = bool(features[f"{prefix}LowerHighLowerLow"].value)
        rolling_high = _number(features[f"{prefix}RollingHigh20"].value)
        rolling_low = _number(features[f"{prefix}RollingLow20"].value)

        if None in {ema9, ema20, ema9_slope, ema20_slope, session_vwap, session_vwap_slope}:
            return None

        ema_relation = 1 if ema9 > ema20 else -1 if ema9 < ema20 else 0
        average_ema_slope = (ema9_slope + ema20_slope) / 2
        ema_slope = _signed_threshold(average_ema_slope, self.config.minSlopeMagnitude)
        price_vwap = 1 if latest_close > session_vwap else -1 if latest_close < session_vwap else 0
        vwap_slope = _signed_threshold(session_vwap_slope, self.config.minSlopeMagnitude)
        structure = 1 if higher_structure else -1 if lower_structure else 0
        momentum_value = (latest_close - previous_close) / previous_close if previous_close else 0
        momentum = _signed_threshold(momentum_value, self.config.minMomentumMagnitude)

        weighted = (
            ema_relation * self.config.emaRelationWeight
            + ema_slope * self.config.emaSlopeWeight
            + price_vwap * self.config.priceVwapWeight
            + vwap_slope * self.config.vwapSlopeWeight
            + structure * self.config.structureWeight
            + momentum * self.config.momentumWeight
        )
        score = weighted / self.config.totalWeight if self.config.totalWeight else 0
        invalidation = rolling_low if score > 0 else rolling_high if score < 0 else None

        return TimeframeTrendState(
            timeframe=timeframe,
            score=round(score, 4),
            emaRelation=ema_relation,
            emaSlope=ema_slope,
            priceVwap=price_vwap,
            vwapSlope=vwap_slope,
            structure=structure,
            momentum=momentum,
            latestClose=latest_close,
            invalidationPrice=invalidation,
        )

    def _confidence(
        self,
        aligned_count: int,
        trend_strength: float,
        slope_consistency: float,
        structure_consistency: float,
        data_quality: float,
    ) -> float:
        aligned_score = aligned_count / len(TIMEFRAMES)
        confidence = (
            0.3 * aligned_score
            + 0.25 * min(1.0, trend_strength)
            + 0.2 * slope_consistency
            + 0.15 * structure_consistency
            + 0.1 * data_quality
        )
        return round(max(0.0, min(1.0, confidence)), 4)

    def _hold_confidence(self, states: list[TimeframeTrendState], data_quality: float) -> float:
        conflict_penalty = max(abs(state.score) for state in states) if states else 0
        return round(max(0.05, min(0.45, 0.25 + (0.15 * data_quality) - (0.1 * conflict_penalty))), 4)

    def _data_quality(self, context: StrategyEvaluationContext) -> float:
        feature_names = self.required_feature_names()
        ready = sum(1 for name in feature_names if context.featureSnapshot.features.get(name) and context.featureSnapshot.features[name].quality == "READY")
        return ready / len(feature_names)

    def _reliability(self, slope_consistency: float, structure_consistency: float, data_quality: float) -> float:
        return round(max(0.0, min(1.0, (0.4 * slope_consistency) + (0.3 * structure_consistency) + (0.3 * data_quality))), 4)

    def _regime_fit(self, states: list[TimeframeTrendState], signal: Signal) -> float:
        if signal == Signal.BUY:
            aligned = sum(1 for state in states if state.score > 0)
        elif signal == Signal.SELL:
            aligned = sum(1 for state in states if state.score < 0)
        else:
            aligned = 0
        return round(aligned / len(states), 4)

    def _buy_invalidation(self, states: list[TimeframeTrendState]) -> float | None:
        values = [state.invalidationPrice for state in states if state.invalidationPrice is not None and state.score > 0]
        return round(min(values), 4) if values else None

    def _sell_invalidation(self, states: list[TimeframeTrendState]) -> float | None:
        values = [state.invalidationPrice for state in states if state.invalidationPrice is not None and state.score < 0]
        return round(max(values), 4) if values else None

    def _explanation(self, signal: Signal, states: list[TimeframeTrendState]) -> str:
        summary = ", ".join(f"{state.timeframe}={state.score:+.2f}" for state in states)
        return f"{signal.value} from multi-timeframe trend alignment: {summary}."

    def _hold_explanation(self, states: list[TimeframeTrendState], *, conflict: bool) -> str:
        summary = ", ".join(f"{state.timeframe}={state.score:+.2f}" for state in states)
        reason = "timeframes conflict" if conflict else "trend evidence is weak"
        return f"HOLD because {reason}: {summary}."


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _signed_threshold(value: float, threshold: float) -> int:
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0
