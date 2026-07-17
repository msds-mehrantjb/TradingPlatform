from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
SetupState = Literal["IDLE", "PERMISSION_ACTIVE", "CONFIRMATION_ACTIVE", "WAITING_FOR_TRIGGER", "TRIGGERED", "SIGNAL_EMITTED", "INVALIDATED", "COOLDOWN"]


class TimeframeTrendParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    momentumLookback: int = Field(default=3, ge=1, le=30)
    minimumNormalizedMomentum: float = Field(default=0.20, ge=0, le=1)
    momentumReturnToNeutral: float = Field(default=0.08, ge=0, le=1)
    slopeLookback: int = Field(default=3, ge=1, le=30)
    minimumNormalizedSlope: float = Field(default=0.20, ge=0, le=1)
    slopeReturnToNeutral: float = Field(default=0.08, ge=0, le=1)

    @model_validator(mode="after")
    def validate_threshold_relationships(self) -> "TimeframeTrendParameters":
        if self.minimumNormalizedMomentum <= self.momentumReturnToNeutral:
            raise ValueError("minimumNormalizedMomentum must be greater than momentumReturnToNeutral")
        if self.minimumNormalizedSlope <= self.slopeReturnToNeutral:
            raise ValueError("minimumNormalizedSlope must be greater than slopeReturnToNeutral")
        return self


class MultiTimeframeTrendAlignmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "multi_timeframe_trend_alignment_v2"
    neutralThreshold: float = Field(default=0.0, ge=-1, le=1)
    bullishThreshold: float = Field(default=0.24, ge=0, le=1)
    bearishThreshold: float = Field(default=-0.24, ge=-1, le=0)
    strongBearishThreshold: float = Field(default=-0.55, ge=-1, le=0)
    strongBullishThreshold: float = Field(default=0.55, ge=0, le=1)
    entryUsableScore: float = Field(default=0.18, ge=0, le=1)
    minAlignedTimeframes: int = Field(default=2, ge=1, le=3)
    emaRelationWeight: float = Field(default=0.40, ge=0)
    emaSlopeWeight: float = Field(default=0.2857142857, ge=0)
    priceVwapWeight: float = Field(default=0.0, ge=0)
    vwapSlopeWeight: float = Field(default=0.0, ge=0)
    structureWeight: float = Field(default=0.1714285714, ge=0)
    momentumWeight: float = Field(default=0.1428571429, ge=0)
    fifteenMinutePermissionThreshold: float = Field(default=-0.05, ge=-1, le=1)
    fiveMinuteConfirmationThreshold: float = Field(default=0.24, ge=0, le=1)
    oneMinuteTriggerThreshold: float = Field(default=0.18, ge=0, le=1)
    triggerLookbackCandles: int = Field(default=3, ge=1, le=10)
    maxTriggerDistanceFromVwapAtr: float = Field(default=50.0, ge=0.1)
    maxEma20DistanceAtr: float = Field(default=12.00, ge=0.1)
    maxVwapDistanceAtr: float = Field(default=30.00, ge=0.1)
    maxTriggerRangeAtr: float = Field(default=1.25, ge=0.1)
    minOpposingLevelDistanceAtr: float = Field(default=0.25, ge=0)
    maxConsecutiveDirectionalCandles: int = Field(default=5, ge=1)
    minTriggerCloseLocation: float = Field(default=0.60, ge=0, le=1)
    minContinuationVolumeRatio: float = Field(default=0.80, ge=0)
    maxExhaustionVolumeRatio: float = Field(default=2.50, ge=0.1)
    minInitialStopDistanceAtr: float = Field(default=0.20, ge=0)
    maxInitialStopDistanceAtr: float = Field(default=2.50, ge=0.1)
    spreadBufferAtr: float = Field(default=0.05, ge=0)
    maxPermissionAgeSeconds: int = Field(default=1800, ge=1)
    maxConfirmationAgeSeconds: int = Field(default=600, ge=1)
    maxTriggerAgeSeconds: int = Field(default=120, ge=1)
    weakAdxThreshold: float = Field(default=12.0, ge=0)
    veryLowAdxThreshold: float = Field(default=12.0, ge=0)
    moderateAdxThreshold: float = Field(default=18.0, ge=0)
    highAdxThreshold: float = Field(default=30.0, ge=0)
    extremeAdxThreshold: float = Field(default=45.0, ge=0)
    normalizedEmaSpreadMaximumAtr: float = Field(default=0.60, ge=0.01)
    normalizedEma9SlopeMaximumAtr: float = Field(default=0.05, ge=0.001)
    normalizedEma20SlopeMaximumAtr: float = Field(default=0.03, ge=0.001)
    normalizedMomentumMaximumAtr: float = Field(default=1.00, ge=0.01)
    normalizedVwapDistanceMaximumAtr: float = Field(default=3.00, ge=0.01)
    sessionVwapSlopeMagnitude: float = Field(default=0.00005, ge=0)
    hysteresisEntryThreshold: float = Field(default=0.20, ge=0, le=1)
    hysteresisNeutralThreshold: float = Field(default=0.08, ge=0, le=1)
    timeframeParameters: dict[Literal["1m", "5m", "15m"], TimeframeTrendParameters] = Field(
        default_factory=lambda: {
            "1m": TimeframeTrendParameters(
                momentumLookback=3,
                minimumNormalizedMomentum=0.20,
                momentumReturnToNeutral=0.08,
                slopeLookback=3,
                minimumNormalizedSlope=0.20,
                slopeReturnToNeutral=0.08,
            ),
            "5m": TimeframeTrendParameters(
                momentumLookback=2,
                minimumNormalizedMomentum=0.18,
                momentumReturnToNeutral=0.07,
                slopeLookback=2,
                minimumNormalizedSlope=0.16,
                slopeReturnToNeutral=0.06,
            ),
            "15m": TimeframeTrendParameters(
                momentumLookback=2,
                minimumNormalizedMomentum=0.15,
                momentumReturnToNeutral=0.06,
                slopeLookback=2,
                minimumNormalizedSlope=0.12,
                slopeReturnToNeutral=0.05,
            ),
        }
    )

    @model_validator(mode="after")
    def validate_relationships(self) -> "MultiTimeframeTrendAlignmentConfig":
        if self.strongBullishThreshold < self.bullishThreshold:
            raise ValueError("strongBullishThreshold must be greater than or equal to bullishThreshold")
        if self.strongBearishThreshold > self.bearishThreshold:
            raise ValueError("strongBearishThreshold must be less than or equal to bearishThreshold")
        if self.bullishThreshold <= self.neutralThreshold:
            raise ValueError("bullishThreshold must be greater than neutralThreshold")
        if self.bearishThreshold >= self.neutralThreshold:
            raise ValueError("bearishThreshold must be less than neutralThreshold")
        if self.totalWeight <= 0:
            raise ValueError("total component weight must be greater than zero")
        if abs(self.totalWeight - 1.0) > 1e-6:
            raise ValueError("normalized component weights must sum to 1")
        if set(self.timeframeParameters) != set(TIMEFRAMES):
            raise ValueError("timeframeParameters must explicitly configure 1m, 5m and 15m")
        if self.fifteenMinutePermissionThreshold >= self.bullishThreshold:
            raise ValueError("15m permission threshold must be below bullishThreshold to allow explicit neutral permission")
        if -self.fifteenMinutePermissionThreshold <= self.bearishThreshold:
            raise ValueError("15m short permission threshold must be above bearishThreshold to allow explicit neutral permission")
        if self.fiveMinuteConfirmationThreshold <= self.neutralThreshold:
            raise ValueError("5m confirmation threshold must be greater than neutralThreshold")
        if self.oneMinuteTriggerThreshold <= self.neutralThreshold:
            raise ValueError("1m trigger threshold must be greater than neutralThreshold")
        if self.hysteresisEntryThreshold <= self.hysteresisNeutralThreshold:
            raise ValueError("hysteresisEntryThreshold must be greater than hysteresisNeutralThreshold")
        if self.maxInitialStopDistanceAtr < self.minInitialStopDistanceAtr:
            raise ValueError("maxInitialStopDistanceAtr must be greater than or equal to minInitialStopDistanceAtr")
        if self.maxExhaustionVolumeRatio <= self.minContinuationVolumeRatio:
            raise ValueError("maxExhaustionVolumeRatio must be greater than minContinuationVolumeRatio")
        if not (self.veryLowAdxThreshold <= self.moderateAdxThreshold <= self.highAdxThreshold <= self.extremeAdxThreshold):
            raise ValueError("ADX thresholds must be ordered from very low to extreme")
        return self

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
            + self.structureWeight
            + self.momentumWeight
        )


@dataclass(frozen=True)
class SessionVwapContext:
    sessionVwap: float
    vwapSlope: int
    pricePosition: int
    distanceFromVwapAtr: float | None
    vwapDistanceScore: float | None


@dataclass(frozen=True)
class TimeframeTrendState:
    timeframe: str
    score: float
    emaRelation: int
    emaSlope: int
    emaSpreadScore: float
    ema9Slope: int
    ema9SlopeScore: float
    ema20SlopeScore: float
    slopeAgreement: str
    structure: int
    momentum: int
    momentumScore: float
    latestClose: float
    invalidationPrice: float | None
    barStartTimestamp: str | None
    barEndTimestamp: str | None
    barAgeSeconds: float | None
    longTrigger: "TriggerEvidence"
    shortTrigger: "TriggerEvidence"
    distanceFromVwapAtr: float | None
    adx14: float | None
    atr14: float | None
    adxRegime: str
    adxQuality: float
    lateEntryRisk: bool
    rollingHigh: float | None
    rollingLow: float | None
    longEntryLocation: dict[str, Any]
    shortEntryLocation: dict[str, Any]


@dataclass(frozen=True)
class TriggerEvidence:
    active: bool
    triggerType: str
    triggerLevel: float | None
    invalidationLevel: float | None
    triggerTimestamp: str | None
    reason: str


class MultiTimeframeTrendAlignmentStrategy:
    registryEntry = resolve_strategy("multi_timeframe_trend_alignment")

    def __init__(self, config: MultiTimeframeTrendAlignmentConfig | None = None) -> None:
        self.config = config or MultiTimeframeTrendAlignmentConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> StrategySignal:
        required_features = self.required_feature_names()
        quality_blockers = self._timeframe_quality_blockers(context)
        if quality_blockers:
            return hold_signal(
                context,
                confidence=0.0,
                setupDetected=False,
                regimeFit=0.0,
                reliability=0.0,
                reasonCodes=["multi_timeframe.timeframe_quality_unavailable", *quality_blockers],
                explanation="Multi-timeframe trend alignment is unavailable because one or more timeframe inputs failed bar-quality validation.",
                featureNames=required_features,
            )
        if not context.featureSnapshot.strategyRequiredFeaturesReady:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Strategy-required SPY trend features are unavailable for multi-timeframe trend alignment.",
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
        state_by_timeframe = {state.timeframe: state for state in ready_states}
        one_minute = state_by_timeframe["1m"]
        five_minute = state_by_timeframe["5m"]
        fifteen_minute = state_by_timeframe["15m"]
        session_vwap_context = self._session_vwap_context(context)
        if session_vwap_context is None:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Session VWAP context is unavailable for multi-timeframe trend alignment.",
            )

        long_blockers = self._long_blockers(context, one_minute, five_minute, fifteen_minute, session_vwap_context)
        short_blockers = self._short_blockers(context, one_minute, five_minute, fifteen_minute, session_vwap_context)
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
        regime_evidence = self._trend_regime_evidence(context, ready_states, session_vwap_context)

        if not long_blockers:
            invalidation_levels = self._invalidation_levels("long", one_minute, five_minute, fifteen_minute)
            confidence = self._directional_confidence(Signal.BUY, one_minute, five_minute, fifteen_minute, session_vwap_context, data_quality)
            result = strategy_signal(
                context,
                signal=Signal.BUY,
                confidence=confidence,
                eligible=True,
                setupDetected=True,
                regimeFit=regime_evidence["regimeSuitability"],
                reliability=self._reliability(ready_states, slope_consistency, structure_consistency, data_quality),
                reasonCodes=[
                    "multi_timeframe.hierarchy_buy",
                    "multi_timeframe.bullish_alignment",
                    f"aligned_timeframes:{bullish_count}",
                    "hierarchy:1m_trigger_5m_confirmation_15m_permission",
                ],
                explanation=self._explanation(Signal.BUY, ready_states),
                featureNames=required_features,
                structuralInvalidationPrice=invalidation_levels["entryInvalidation"]["level"],
            )
            return self._with_bar_evidence(result, context, ready_states, session_vwap_context, regime_evidence)

        if not short_blockers:
            invalidation_levels = self._invalidation_levels("short", one_minute, five_minute, fifteen_minute)
            confidence = self._directional_confidence(Signal.SELL, one_minute, five_minute, fifteen_minute, session_vwap_context, data_quality)
            result = strategy_signal(
                context,
                signal=Signal.SELL,
                confidence=confidence,
                eligible=True,
                setupDetected=True,
                regimeFit=regime_evidence["regimeSuitability"],
                reliability=self._reliability(ready_states, slope_consistency, structure_consistency, data_quality),
                reasonCodes=[
                    "multi_timeframe.hierarchy_sell",
                    "multi_timeframe.bearish_alignment",
                    f"aligned_timeframes:{bearish_count}",
                    "hierarchy:1m_trigger_5m_confirmation_15m_permission",
                ],
                explanation=self._explanation(Signal.SELL, ready_states),
                featureNames=required_features,
                structuralInvalidationPrice=invalidation_levels["entryInvalidation"]["level"],
            )
            return self._with_bar_evidence(result, context, ready_states, session_vwap_context, regime_evidence)

        conflict = bullish_count > 0 and bearish_count > 0
        hierarchy_blockers = _unique([*long_blockers, *short_blockers])
        result = hold_signal(
            context,
            confidence=self._hold_confidence(ready_states, data_quality),
            setupDetected=bool(bullish_count or bearish_count),
            regimeFit=regime_evidence["regimeSuitability"],
            reliability=self._reliability(ready_states, slope_consistency, structure_consistency, data_quality),
            reasonCodes=[
                "multi_timeframe.hierarchy_blocked",
                "multi_timeframe.conflict" if conflict else "multi_timeframe.weak_evidence",
                f"aligned_timeframes_buy:{bullish_count}",
                f"aligned_timeframes_sell:{bearish_count}",
                *hierarchy_blockers,
            ],
            explanation=self._hold_explanation(ready_states, conflict=conflict),
            featureNames=required_features,
        )
        return self._with_bar_evidence(result, context, ready_states, session_vwap_context, regime_evidence)

    def required_feature_names(self) -> tuple[str, ...]:
        names: list[str] = ["sessionVwap", "sessionVwapSlope", "distanceFromVwapAtr"]
        for timeframe in TIMEFRAMES:
            prefix = f"spy{timeframe}"
            names.extend(
                [
                    f"{prefix}Ema9",
                    f"{prefix}Ema20",
                    f"{prefix}Atr14",
                    f"{prefix}Adx14",
                    f"{prefix}HigherHighHigherLow",
                    f"{prefix}LowerHighLowerLow",
                    f"{prefix}RollingHigh20",
                    f"{prefix}RollingLow20",
                ]
            )
        return tuple(names)

    def _timeframe_quality_blockers(self, context: StrategyEvaluationContext) -> list[str]:
        quality = context.featureSnapshot.rawInputs.get("timeframeQuality")
        if not isinstance(quality, dict):
            return ["timeframe_quality_missing"]
        blockers: list[str] = []
        for timeframe in TIMEFRAMES:
            item = quality.get(timeframe)
            if not isinstance(item, dict):
                blockers.append(f"{timeframe}:quality_missing")
                continue
            if (
                item.get("is_complete") is not True
                or item.get("is_fresh") is not True
                or item.get("is_boundary_aligned") is not True
                or item.get("is_ordered") is not True
                or item.get("has_required_history") is not True
                or item.get("has_gaps") is True
                or item.get("has_duplicates") is True
                or bool(item.get("reason_codes"))
            ):
                reasons = item.get("reason_codes") if isinstance(item.get("reason_codes"), list) else []
                blockers.extend(f"{timeframe}:{reason}" for reason in reasons)
                if not reasons:
                    blockers.append(f"{timeframe}:quality_failed")
        return blockers

    def _timeframe_state(self, context: StrategyEvaluationContext, timeframe: str) -> TimeframeTrendState | None:
        features = context.featureSnapshot.features
        parameters = self._timeframe_parameters(timeframe)
        prefix = f"spy{timeframe}"
        raw_key = f"spy{timeframe}Candles"
        window_key = f"spy{timeframe}BarWindows"
        candles = context.featureSnapshot.rawInputs.get(raw_key) or []
        windows = context.featureSnapshot.rawInputs.get(window_key) or []
        required_candle_count = max(parameters.momentumLookback, parameters.slopeLookback) + 21
        if len(candles) < required_candle_count:
            return None
        latest = candles[-1]
        latest_window = windows[-1] if isinstance(windows, list) and windows else {}
        previous = candles[-1 - parameters.momentumLookback]
        latest_close = float(latest["close"])
        previous_close = float(previous["close"])
        ema9 = _number(features[f"{prefix}Ema9"].value)
        ema20 = _number(features[f"{prefix}Ema20"].value)
        atr14 = _number(features[f"{prefix}Atr14"].value)
        adx14 = _number(features[f"{prefix}Adx14"].value)
        session_vwap = _number(features["sessionVwap"].value)
        distance_from_vwap_atr = _number(features["distanceFromVwapAtr"].value)
        higher_structure = bool(features[f"{prefix}HigherHighHigherLow"].value)
        lower_structure = bool(features[f"{prefix}LowerHighLowerLow"].value)
        rolling_high = _number(features[f"{prefix}RollingHigh20"].value)
        rolling_low = _number(features[f"{prefix}RollingLow20"].value)

        if None in {ema9, ema20, atr14, session_vwap} or atr14 <= 0:
            return None

        closes = [float(candle["close"]) for candle in candles]
        ema9_series = _ema_series(closes, 9)
        ema20_series = _ema_series(closes, 20)
        current_ema9 = _last_number(ema9_series)
        previous_ema9 = ema9_series[-1 - parameters.slopeLookback] if len(ema9_series) > parameters.slopeLookback else None
        current_ema20 = _last_number(ema20_series)
        previous_ema20 = ema20_series[-1 - parameters.slopeLookback] if len(ema20_series) > parameters.slopeLookback else None
        if current_ema9 is None or previous_ema9 is None or current_ema20 is None or previous_ema20 is None:
            return None

        ema_spread_score = _normalize_by_atr(ema9 - ema20, atr14, self.config.normalizedEmaSpreadMaximumAtr)
        previous_ema_spread_score = self._previous_ema_spread_score(closes, parameters.slopeLookback, atr14)
        ema9_slope_per_bar = (current_ema9 - previous_ema9) / parameters.slopeLookback
        ema20_slope_per_bar = (current_ema20 - previous_ema20) / parameters.slopeLookback
        ema9_slope_score = _normalize_by_atr(ema9_slope_per_bar, atr14, self.config.normalizedEma9SlopeMaximumAtr)
        ema20_slope_score = _normalize_by_atr(ema20_slope_per_bar, atr14, self.config.normalizedEma20SlopeMaximumAtr)
        ema_relation = self._hysteresis_state(ema_spread_score, previous_ema_spread_score)
        ema9_slope = self._hysteresis_state(
            ema9_slope_score,
            None,
            entry_threshold=parameters.minimumNormalizedSlope,
            neutral_threshold=parameters.slopeReturnToNeutral,
        )
        ema_slope = self._hysteresis_state(
            ema20_slope_score,
            None,
            entry_threshold=parameters.minimumNormalizedSlope,
            neutral_threshold=parameters.slopeReturnToNeutral,
        )
        structure = 1 if higher_structure else -1 if lower_structure else 0
        momentum_score = _normalize_by_atr(latest_close - previous_close, atr14, self.config.normalizedMomentumMaximumAtr)
        momentum = self._hysteresis_state(
            momentum_score,
            None,
            entry_threshold=parameters.minimumNormalizedMomentum,
            neutral_threshold=parameters.momentumReturnToNeutral,
        )
        adx_regime, adx_quality, late_entry_risk = self._adx_suitability(adx14)
        long_entry_location = self._entry_location_gate("long", candles, ema20, session_vwap, atr14, rolling_high, rolling_low) if timeframe == "1m" else _entry_location_not_applicable()
        short_entry_location = self._entry_location_gate("short", candles, ema20, session_vwap, atr14, rolling_high, rolling_low) if timeframe == "1m" else _entry_location_not_applicable()
        bar_end = latest_window.get("barEndTimestamp") if isinstance(latest_window, dict) else None

        weighted = (
            ema_spread_score * self.config.emaRelationWeight
            + ema20_slope_score * self.config.emaSlopeWeight
            + structure * self.config.structureWeight
            + momentum_score * self.config.momentumWeight
        )
        score = weighted / self.config.totalWeight if self.config.totalWeight else 0
        invalidation = rolling_low if score > 0 else rolling_high if score < 0 else None

        return TimeframeTrendState(
            timeframe=timeframe,
            score=round(score, 4),
            emaRelation=ema_relation,
            emaSlope=ema_slope,
            emaSpreadScore=round(ema_spread_score, 4),
            ema9Slope=ema9_slope,
            ema9SlopeScore=round(ema9_slope_score, 4),
            ema20SlopeScore=round(ema20_slope_score, 4),
            slopeAgreement=_slope_agreement(ema_slope, ema9_slope),
            structure=structure,
            momentum=momentum,
            momentumScore=round(momentum_score, 4),
            latestClose=latest_close,
            invalidationPrice=invalidation,
            barStartTimestamp=latest_window.get("barStartTimestamp") if isinstance(latest_window, dict) else None,
            barEndTimestamp=latest_window.get("barEndTimestamp") if isinstance(latest_window, dict) else None,
            barAgeSeconds=_age_seconds(context.featureSnapshot.evaluationTimestamp, bar_end),
            longTrigger=self._one_minute_trigger("long", candles, ema9, ema20, session_vwap, rolling_low, rolling_high) if timeframe == "1m" else _inactive_trigger("Only the 1m trigger timeframe can emit entries."),
            shortTrigger=self._one_minute_trigger("short", candles, ema9, ema20, session_vwap, rolling_low, rolling_high) if timeframe == "1m" else _inactive_trigger("Only the 1m trigger timeframe can emit entries."),
            distanceFromVwapAtr=distance_from_vwap_atr,
            adx14=adx14,
            atr14=atr14,
            adxRegime=adx_regime,
            adxQuality=adx_quality,
            lateEntryRisk=late_entry_risk,
            rollingHigh=rolling_high,
            rollingLow=rolling_low,
            longEntryLocation=long_entry_location,
            shortEntryLocation=short_entry_location,
        )

    def _one_minute_trigger(
        self,
        direction: Literal["long", "short"],
        candles: list[dict[str, Any]],
        ema9: float,
        ema20: float,
        session_vwap: float,
        rolling_low: float | None,
        rolling_high: float | None,
    ) -> TriggerEvidence:
        if len(candles) < max(6, self.config.triggerLookbackCandles + 2):
            return _inactive_trigger("Insufficient 1m candles for explicit trigger.")
        latest = candles[-1]
        previous = candles[-2]
        trigger_timestamp = str(latest.get("timestamp") or "")
        current_close = float(latest["close"])
        previous_close = float(previous["close"])
        previous_high = float(previous["high"])
        previous_low = float(previous["low"])

        if direction == "long":
            if previous_close <= ema9 and current_close > ema9 and ema9 > ema20 and current_close > previous_high:
                return TriggerEvidence(True, "ema_reclaim", round(max(ema9, previous_high), 4), round(min(float(previous["low"]), float(latest["low"])), 4), trigger_timestamp, "Previous close was below EMA9; current close reclaimed EMA9 and broke the previous candle high.")
            pullback = self._pullback_anchor_trigger("long", candles, ema9, ema20, session_vwap, rolling_low, rolling_high)
            if pullback.active:
                return pullback
            return self._micro_structure_trigger("long", candles)

        if previous_close >= ema9 and current_close < ema9 and ema9 < ema20 and current_close < previous_low:
            return TriggerEvidence(True, "ema_reclaim", round(min(ema9, previous_low), 4), round(max(float(previous["high"]), float(latest["high"])), 4), trigger_timestamp, "Previous close was above EMA9; current close lost EMA9 and broke the previous candle low.")
        pullback = self._pullback_anchor_trigger("short", candles, ema9, ema20, session_vwap, rolling_low, rolling_high)
        if pullback.active:
            return pullback
        return self._micro_structure_trigger("short", candles)

    def _pullback_anchor_trigger(
        self,
        direction: Literal["long", "short"],
        candles: list[dict[str, Any]],
        ema9: float,
        ema20: float,
        session_vwap: float,
        rolling_low: float | None,
        rolling_high: float | None,
    ) -> TriggerEvidence:
        latest = candles[-1]
        previous = candles[-2]
        recent = candles[-5:-1]
        trigger_timestamp = str(latest.get("timestamp") or "")
        anchors = (("ema9", ema9), ("ema20", ema20), ("vwap", session_vwap))
        if direction == "long":
            pullback_low = min(float(candle["low"]) for candle in recent)
            if rolling_low is not None and pullback_low <= rolling_low:
                return _inactive_trigger("Pullback invalidated the long trend structure.")
            trigger_level = max(float(previous["high"]), max(float(candle["high"]) for candle in recent[-3:]))
            for anchor_name, anchor in anchors:
                touched = pullback_low <= anchor <= max(float(candle["high"]) for candle in recent)
                if touched and float(latest["close"]) > anchor and float(latest["close"]) > trigger_level:
                    return TriggerEvidence(True, f"pullback_continuation_{anchor_name}", round(trigger_level, 4), round(pullback_low, 4), trigger_timestamp, f"Controlled pullback touched {anchor_name}, held structure, reclaimed anchor, and broke pullback high.")
            return _inactive_trigger("No long pullback-continuation trigger.")

        pullback_high = max(float(candle["high"]) for candle in recent)
        if rolling_high is not None and pullback_high >= rolling_high:
            return _inactive_trigger("Pullback invalidated the short trend structure.")
        trigger_level = min(float(previous["low"]), min(float(candle["low"]) for candle in recent[-3:]))
        for anchor_name, anchor in anchors:
            touched = min(float(candle["low"]) for candle in recent) <= anchor <= pullback_high
            if touched and float(latest["close"]) < anchor and float(latest["close"]) < trigger_level:
                return TriggerEvidence(True, f"pullback_continuation_{anchor_name}", round(trigger_level, 4), round(pullback_high, 4), trigger_timestamp, f"Controlled pullback touched {anchor_name}, held structure, lost anchor, and broke pullback low.")
        return _inactive_trigger("No short pullback-continuation trigger.")

    def _micro_structure_trigger(self, direction: Literal["long", "short"], candles: list[dict[str, Any]]) -> TriggerEvidence:
        latest = candles[-1]
        recent = candles[-5:-1]
        trigger_timestamp = str(latest.get("timestamp") or "")
        if direction == "long":
            higher_low_confirmed = float(recent[-1]["low"]) > float(recent[-2]["low"])
            pullback_swing_high = max(float(candle["high"]) for candle in recent[-3:])
            if higher_low_confirmed and float(latest["close"]) > pullback_swing_high:
                return TriggerEvidence(True, "micro_structure_break", round(pullback_swing_high, 4), round(min(float(candle["low"]) for candle in recent[-3:]), 4), trigger_timestamp, "One-minute higher low confirmed and price broke the pullback swing high.")
            return _inactive_trigger("No long micro-structure break.")

        lower_high_confirmed = float(recent[-1]["high"]) < float(recent[-2]["high"])
        pullback_swing_low = min(float(candle["low"]) for candle in recent[-3:])
        if lower_high_confirmed and float(latest["close"]) < pullback_swing_low:
            return TriggerEvidence(True, "micro_structure_break", round(pullback_swing_low, 4), round(max(float(candle["high"]) for candle in recent[-3:]), 4), trigger_timestamp, "One-minute lower high confirmed and price broke the pullback swing low.")
        return _inactive_trigger("No short micro-structure break.")

    def _long_blockers(
        self,
        context: StrategyEvaluationContext,
        one_minute: TimeframeTrendState,
        five_minute: TimeframeTrendState,
        fifteen_minute: TimeframeTrendState,
        session_vwap_context: SessionVwapContext,
    ) -> list[str]:
        blockers: list[str] = []
        setup = self._setup_evidence(context, "long", one_minute, five_minute, fifteen_minute, session_vwap_context)
        if not self._permits_long(fifteen_minute):
            blockers.append("15m_permission_long_failed")
        if not self._recent(fifteen_minute, self.config.maxPermissionAgeSeconds):
            blockers.append("15m_permission_stale")
        if not self._confirms_long(five_minute):
            blockers.append("5m_confirmation_long_failed")
        if not self._recent(five_minute, self.config.maxConfirmationAgeSeconds):
            blockers.append("5m_confirmation_stale")
        if not setup["trigger"]:
            blockers.append("1m_trigger_long_missing")
        if not self._recent(one_minute, self.config.maxTriggerAgeSeconds):
            blockers.append("1m_trigger_stale")
        if setup["setupState"] == "SIGNAL_EMITTED":
            blockers.append("1m_trigger_long_consumed")
        if setup["setupState"] == "COOLDOWN":
            blockers.append("1m_trigger_long_cooldown")
        if one_minute.longEntryLocation.get("allowed") is False:
            blockers.append("1m_entry_location_long_failed")
            blockers.extend(f"entry_location_long:{reason}" for reason in one_minute.longEntryLocation.get("reasonCodes", []))
        return blockers

    def _short_blockers(
        self,
        context: StrategyEvaluationContext,
        one_minute: TimeframeTrendState,
        five_minute: TimeframeTrendState,
        fifteen_minute: TimeframeTrendState,
        session_vwap_context: SessionVwapContext,
    ) -> list[str]:
        blockers: list[str] = []
        setup = self._setup_evidence(context, "short", one_minute, five_minute, fifteen_minute, session_vwap_context)
        if not self._permits_short(fifteen_minute):
            blockers.append("15m_permission_short_failed")
        if not self._recent(fifteen_minute, self.config.maxPermissionAgeSeconds):
            blockers.append("15m_permission_stale")
        if not self._confirms_short(five_minute):
            blockers.append("5m_confirmation_short_failed")
        if not self._recent(five_minute, self.config.maxConfirmationAgeSeconds):
            blockers.append("5m_confirmation_stale")
        if not setup["trigger"]:
            blockers.append("1m_trigger_short_missing")
        if not self._recent(one_minute, self.config.maxTriggerAgeSeconds):
            blockers.append("1m_trigger_stale")
        if setup["setupState"] == "SIGNAL_EMITTED":
            blockers.append("1m_trigger_short_consumed")
        if setup["setupState"] == "COOLDOWN":
            blockers.append("1m_trigger_short_cooldown")
        if one_minute.shortEntryLocation.get("allowed") is False:
            blockers.append("1m_entry_location_short_failed")
            blockers.extend(f"entry_location_short:{reason}" for reason in one_minute.shortEntryLocation.get("reasonCodes", []))
        return blockers

    def _permits_long(self, state: TimeframeTrendState) -> bool:
        return (
            state.score >= self.config.fifteenMinutePermissionThreshold
            and state.emaSlope >= 0
            and state.structure >= 0
            and state.score > self.config.strongBearishThreshold
        )

    def _permits_short(self, state: TimeframeTrendState) -> bool:
        return (
            state.score <= -self.config.fifteenMinutePermissionThreshold
            and state.emaSlope <= 0
            and state.structure <= 0
            and state.score < self.config.strongBullishThreshold
        )

    def _confirms_long(self, state: TimeframeTrendState) -> bool:
        return (
            state.score >= self.config.fiveMinuteConfirmationThreshold
            and state.emaRelation > 0
            and state.emaSlope > 0
            and state.structure >= 0
            and state.momentum >= 0
        )

    def _confirms_short(self, state: TimeframeTrendState) -> bool:
        return (
            state.score <= -self.config.fiveMinuteConfirmationThreshold
            and state.emaRelation < 0
            and state.emaSlope < 0
            and state.structure <= 0
            and state.momentum <= 0
        )

    def _fresh_long_trigger(self, state: TimeframeTrendState, session_vwap_context: SessionVwapContext) -> bool:
        return (
            state.score >= self.config.oneMinuteTriggerThreshold
            and state.longTrigger.active
            and session_vwap_context.pricePosition > 0
            and state.emaSlope >= 0
            and state.ema9Slope >= 0
            and state.momentum > 0
            and state.structure >= 0
            and state.longEntryLocation.get("allowed") is True
        )

    def _fresh_short_trigger(self, state: TimeframeTrendState, session_vwap_context: SessionVwapContext) -> bool:
        return (
            state.score <= -self.config.oneMinuteTriggerThreshold
            and state.shortTrigger.active
            and session_vwap_context.pricePosition < 0
            and state.emaSlope <= 0
            and state.ema9Slope <= 0
            and state.momentum < 0
            and state.structure <= 0
            and state.shortEntryLocation.get("allowed") is True
        )

    def _recent(self, state: TimeframeTrendState, max_age_seconds: int) -> bool:
        return state.barAgeSeconds is not None and state.barAgeSeconds <= max_age_seconds

    def _excessively_extended(self, state: TimeframeTrendState) -> bool:
        return (
            "vwap_overextension" in state.longEntryLocation.get("reasonCodes", [])
            or "vwap_overextension" in state.shortEntryLocation.get("reasonCodes", [])
            or "ema20_overextension" in state.longEntryLocation.get("reasonCodes", [])
            or "ema20_overextension" in state.shortEntryLocation.get("reasonCodes", [])
        )

    def _entry_location_gate(
        self,
        direction: Literal["long", "short"],
        candles: list[dict[str, Any]],
        ema20: float,
        session_vwap: float,
        atr14: float,
        rolling_high: float | None,
        rolling_low: float | None,
    ) -> dict[str, Any]:
        if atr14 <= 0 or len(candles) < 21:
            return {"allowed": False, "reasonCodes": ["entry_location_insufficient_data"]}
        latest = candles[-1]
        close = float(latest["close"])
        high = float(latest["high"])
        low = float(latest["low"])
        open_price = float(latest["open"])
        current_range = max(0.0, high - low)
        range_atr = current_range / atr14
        ema_distance_atr = abs(close - ema20) / atr14
        vwap_distance_atr = abs(close - session_vwap) / atr14
        close_location = ((close - low) / current_range) if direction == "long" and current_range > 0 else ((high - close) / current_range) if current_range > 0 else 0.5
        opposing_level = rolling_high if direction == "long" else rolling_low
        opposing_distance_atr = None
        if opposing_level is not None:
            raw_distance = (opposing_level - close) if direction == "long" else (close - opposing_level)
            opposing_distance_atr = max(0.0, raw_distance / atr14)
        consecutive_directional = _consecutive_directional_candles(candles, direction)
        prior_volumes = [float(candle.get("volume", 0.0)) for candle in candles[-21:-1]]
        average_volume = sum(prior_volumes) / len(prior_volumes) if prior_volumes else 0.0
        volume_ratio = (float(latest.get("volume", 0.0)) / average_volume) if average_volume > 0 else None
        volume_state = _volume_state(volume_ratio, close_location, self.config.minContinuationVolumeRatio, self.config.maxExhaustionVolumeRatio)
        reason_codes: list[str] = []
        if ema_distance_atr > self.config.maxEma20DistanceAtr:
            reason_codes.append("ema20_overextension")
        if vwap_distance_atr > self.config.maxVwapDistanceAtr:
            reason_codes.append("vwap_overextension")
        if range_atr > self.config.maxTriggerRangeAtr:
            reason_codes.append("trigger_range_too_large")
        if opposing_distance_atr is not None and opposing_distance_atr <= self.config.minOpposingLevelDistanceAtr:
            reason_codes.append("opposing_level_too_close")
        if consecutive_directional > self.config.maxConsecutiveDirectionalCandles:
            reason_codes.append("late_after_consecutive_directional_candles")
        if close_location < self.config.minTriggerCloseLocation:
            reason_codes.append("weak_trigger_close_location")
        if volume_state == "exhaustion":
            reason_codes.append("volume_exhaustion")
        return {
            "allowed": not reason_codes,
            "direction": direction,
            "reasonCodes": reason_codes,
            "ema20DistanceAtr": round(ema_distance_atr, 4),
            "vwapDistanceAtr": round(vwap_distance_atr, 4),
            "triggerRangeAtr": round(range_atr, 4),
            "opposingStructuralLevel": opposing_level,
            "opposingLevelDistanceAtr": round(opposing_distance_atr, 4) if opposing_distance_atr is not None else None,
            "consecutiveDirectionalCandles": consecutive_directional,
            "triggerCloseLocation": round(close_location, 4),
            "volumeRatio": round(volume_ratio, 4) if volume_ratio is not None else None,
            "volumeState": volume_state,
            "closeAboveOpen": close > open_price,
        }

    def _hysteresis_state(
        self,
        current_score: float,
        previous_score: float | None,
        *,
        entry_threshold: float | None = None,
        neutral_threshold: float | None = None,
    ) -> int:
        entry = self.config.hysteresisEntryThreshold if entry_threshold is None else entry_threshold
        neutral = self.config.hysteresisNeutralThreshold if neutral_threshold is None else neutral_threshold
        if current_score >= entry:
            return 1
        if current_score <= -entry:
            return -1
        if previous_score is not None and previous_score >= entry and current_score > neutral:
            return 1
        if previous_score is not None and previous_score <= -entry and current_score < -neutral:
            return -1
        return 0

    def _timeframe_parameters(self, timeframe: str) -> TimeframeTrendParameters:
        return self.config.timeframeParameters.get(timeframe, TimeframeTrendParameters())

    def _previous_ema_spread_score(self, closes: list[float], lookback: int, atr14: float) -> float | None:
        ema9_series = _ema_series(closes, 9)
        ema20_series = _ema_series(closes, 20)
        if len(ema9_series) <= lookback or len(ema20_series) <= lookback:
            return None
        previous_ema9 = ema9_series[-1 - lookback]
        previous_ema20 = ema20_series[-1 - lookback]
        if previous_ema9 is None or previous_ema20 is None:
            return None
        return _normalize_by_atr(previous_ema9 - previous_ema20, atr14, self.config.normalizedEmaSpreadMaximumAtr)

    def _setup_evidence(
        self,
        context: StrategyEvaluationContext,
        direction: Literal["long", "short"],
        one_minute: TimeframeTrendState,
        five_minute: TimeframeTrendState,
        fifteen_minute: TimeframeTrendState,
        session_vwap_context: SessionVwapContext,
    ) -> dict[str, Any]:
        permission = self._permits_long(fifteen_minute) if direction == "long" else self._permits_short(fifteen_minute)
        confirmation = self._confirms_long(five_minute) if direction == "long" else self._confirms_short(five_minute)
        trigger = one_minute.longTrigger if direction == "long" else one_minute.shortTrigger
        trigger_active = self._fresh_long_trigger(one_minute, session_vwap_context) if direction == "long" else self._fresh_short_trigger(one_minute, session_vwap_context)
        setup_id = self._setup_id(context, direction, fifteen_minute, five_minute, trigger) if trigger.active else None
        consumed = set(context.featureSnapshot.rawInputs.get("consumedTrendTriggerIds") or [])
        cooldown_until = context.featureSnapshot.rawInputs.get("trendTriggerCooldownUntil")
        cooldown_active = _cooldown_active(context.featureSnapshot.evaluationTimestamp, cooldown_until)
        entry_location = one_minute.longEntryLocation if direction == "long" else one_minute.shortEntryLocation
        invalidated = entry_location.get("allowed") is False
        if cooldown_active:
            setup_state: SetupState = "COOLDOWN"
        elif not permission:
            setup_state = "IDLE"
        elif not confirmation:
            setup_state = "PERMISSION_ACTIVE"
        elif invalidated:
            setup_state = "INVALIDATED"
        elif not trigger_active:
            setup_state = "WAITING_FOR_TRIGGER"
        elif setup_id in consumed:
            setup_state = "SIGNAL_EMITTED"
        else:
            setup_state = "TRIGGERED"
        return {
            "direction": direction,
            "permission": permission,
            "confirmation": confirmation,
            "trigger": trigger_active,
            "setupState": setup_state,
            "setupId": setup_id,
            "triggerType": trigger.triggerType,
            "triggerLevel": trigger.triggerLevel,
            "triggerInvalidationLevel": trigger.invalidationLevel,
            "triggerTimestamp": trigger.triggerTimestamp,
            "triggerReason": trigger.reason,
            "stateMachine": ["IDLE", "PERMISSION_ACTIVE", "CONFIRMATION_ACTIVE", "WAITING_FOR_TRIGGER", setup_state],
        }

    def _setup_id(
        self,
        context: StrategyEvaluationContext,
        direction: Literal["long", "short"],
        fifteen_minute: TimeframeTrendState,
        five_minute: TimeframeTrendState,
        trigger: TriggerEvidence,
    ) -> str | None:
        if not trigger.triggerTimestamp or not trigger.triggerType or trigger.triggerLevel is None:
            return None
        symbol = self._symbol(context)
        return "|".join(
            [
                symbol,
                direction,
                str(fifteen_minute.barEndTimestamp or ""),
                str(five_minute.barEndTimestamp or ""),
                str(trigger.triggerTimestamp),
                trigger.triggerType,
                f"{trigger.triggerLevel:.4f}",
            ]
        )

    def _decision_trace(
        self,
        result: StrategySignal,
        context: StrategyEvaluationContext,
        direction: Literal["long", "short"],
        one_minute: TimeframeTrendState,
        five_minute: TimeframeTrendState,
        fifteen_minute: TimeframeTrendState,
        session_vwap_context: SessionVwapContext,
        invalidation_levels: dict[str, Any],
        confidence_model: dict[str, Any],
        penalties: dict[str, float],
    ) -> dict[str, Any]:
        setup = self._setup_evidence(context, direction, one_minute, five_minute, fifteen_minute, session_vwap_context)
        trigger = one_minute.longTrigger if direction == "long" else one_minute.shortTrigger
        entry_location = one_minute.longEntryLocation if direction == "long" else one_minute.shortEntryLocation
        return {
            "permission15m": self._permission_trace(direction, fifteen_minute),
            "confirmation5m": self._confirmation_trace(direction, five_minute),
            "trigger1m": self._trigger_trace(direction, one_minute, trigger, entry_location, setup),
            "final": {
                "signal": result.signal,
                "confidence": result.confidence,
                "oppositionPenalty": penalties["oppositionPenalty"],
                "overextensionPenalty": penalties["overextensionPenalty"],
                "totalPenalty": penalties["totalPenalty"],
                "invalidationLevels": invalidation_levels,
                "setupId": setup["setupId"],
                "setupState": setup["setupState"],
                "regimeFit": result.regimeFit,
                "reliability": result.reliability,
                "confidenceModel": confidence_model,
            },
        }

    def _trace_direction(
        self,
        result: StrategySignal,
        one_minute: TimeframeTrendState,
        five_minute: TimeframeTrendState,
        fifteen_minute: TimeframeTrendState,
    ) -> Literal["long", "short"]:
        if result.signal in {Signal.BUY.value, Signal.BUY}:
            return "long"
        if result.signal in {Signal.SELL.value, Signal.SELL}:
            return "short"
        long_setup_activity = sum(
            [
                1 if one_minute.longTrigger.active else 0,
                1 if self._confirms_long(five_minute) else 0,
                1 if self._permits_long(fifteen_minute) else 0,
            ]
        )
        short_setup_activity = sum(
            [
                1 if one_minute.shortTrigger.active else 0,
                1 if self._confirms_short(five_minute) else 0,
                1 if self._permits_short(fifteen_minute) else 0,
            ]
        )
        if long_setup_activity != short_setup_activity:
            return "long" if long_setup_activity > short_setup_activity else "short"
        long_support = sum(max(0.0, state.score) for state in (one_minute, five_minute, fifteen_minute))
        short_support = sum(max(0.0, -state.score) for state in (one_minute, five_minute, fifteen_minute))
        return "long" if long_support >= short_support else "short"

    def _permission_trace(self, direction: Literal["long", "short"], fifteen_minute: TimeframeTrendState) -> dict[str, Any]:
        status = self._permits_long(fifteen_minute) if direction == "long" else self._permits_short(fifteen_minute)
        blockers: list[str] = []
        if not status:
            blockers.append("15m_permission_failed")
        if direction == "long" and fifteen_minute.emaSlope < 0:
            blockers.append("15m_ema20_slope_bearish")
        if direction == "short" and fifteen_minute.emaSlope > 0:
            blockers.append("15m_ema20_slope_bullish")
        if direction == "long" and fifteen_minute.structure < 0:
            blockers.append("15m_structure_bearish")
        if direction == "short" and fifteen_minute.structure > 0:
            blockers.append("15m_structure_bullish")
        return {
            "status": "PASS" if status else "BLOCKED",
            "score": fifteen_minute.score,
            "adx": fifteen_minute.adx14,
            "adxRegime": fifteen_minute.adxRegime,
            "emaRelationship": fifteen_minute.emaRelation,
            "ema20Slope": fifteen_minute.emaSlope,
            "ema9Slope": fifteen_minute.ema9Slope,
            "ema20SlopeScore": fifteen_minute.ema20SlopeScore,
            "ema9SlopeScore": fifteen_minute.ema9SlopeScore,
            "slopeAgreement": fifteen_minute.slopeAgreement,
            "structure": fifteen_minute.structure,
            "barStartTimestamp": fifteen_minute.barStartTimestamp,
            "barEndTimestamp": fifteen_minute.barEndTimestamp,
            "blockers": blockers,
        }

    def _confirmation_trace(self, direction: Literal["long", "short"], five_minute: TimeframeTrendState) -> dict[str, Any]:
        status = self._confirms_long(five_minute) if direction == "long" else self._confirms_short(five_minute)
        blockers: list[str] = []
        if not status:
            blockers.append("5m_confirmation_failed")
        if direction == "long" and five_minute.emaRelation <= 0:
            blockers.append("5m_ema_relationship_not_bullish")
        if direction == "short" and five_minute.emaRelation >= 0:
            blockers.append("5m_ema_relationship_not_bearish")
        if direction == "long" and five_minute.structure < 0:
            blockers.append("5m_structure_bearish")
        if direction == "short" and five_minute.structure > 0:
            blockers.append("5m_structure_bullish")
        return {
            "status": "PASS" if status else "BLOCKED",
            "score": five_minute.score,
            "adx": five_minute.adx14,
            "adxRegime": five_minute.adxRegime,
            "structure": five_minute.structure,
            "ageSeconds": five_minute.barAgeSeconds,
            "barStartTimestamp": five_minute.barStartTimestamp,
            "barEndTimestamp": five_minute.barEndTimestamp,
            "blockers": blockers,
        }

    def _trigger_trace(
        self,
        direction: Literal["long", "short"],
        one_minute: TimeframeTrendState,
        trigger: TriggerEvidence,
        entry_location: dict[str, Any],
        setup: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "triggerType": trigger.triggerType,
            "triggerLevel": trigger.triggerLevel,
            "triggerTimestamp": trigger.triggerTimestamp,
            "triggerCandle": self._trigger_candle(one_minute, direction),
            "pullbackDepth": self._pullback_depth(one_minute, direction),
            "entryLocationQuality": entry_location,
            "consumed": setup["setupState"] == "SIGNAL_EMITTED",
            "status": "PASS" if setup["trigger"] else "BLOCKED",
            "blockers": entry_location.get("reasonCodes", []),
        }

    def _trigger_candle(self, one_minute: TimeframeTrendState, direction: Literal["long", "short"]) -> dict[str, Any]:
        location = one_minute.longEntryLocation if direction == "long" else one_minute.shortEntryLocation
        return {
            "close": one_minute.latestClose,
            "rangeAtr": location.get("triggerRangeAtr"),
            "closeLocation": location.get("triggerCloseLocation"),
            "volumeRatio": location.get("volumeRatio"),
            "volumeState": location.get("volumeState"),
            "barStartTimestamp": one_minute.barStartTimestamp,
            "barEndTimestamp": one_minute.barEndTimestamp,
        }

    def _pullback_depth(self, one_minute: TimeframeTrendState, direction: Literal["long", "short"]) -> dict[str, Any]:
        trigger = one_minute.longTrigger if direction == "long" else one_minute.shortTrigger
        if trigger.triggerLevel is None or trigger.invalidationLevel is None or one_minute.atr14 is None or one_minute.atr14 <= 0:
            return {"atr": None, "points": None}
        points = abs(trigger.triggerLevel - trigger.invalidationLevel)
        return {"points": round(points, 4), "atr": round(points / one_minute.atr14, 4)}

    def _symbol(self, context: StrategyEvaluationContext) -> str:
        candles = context.featureSnapshot.rawInputs.get("spy1mCandles") or []
        if candles and isinstance(candles[-1], dict) and candles[-1].get("symbol"):
            return str(candles[-1]["symbol"]).upper()
        return "SPY"

    def _session_vwap_context(self, context: StrategyEvaluationContext) -> SessionVwapContext | None:
        features = context.featureSnapshot.features
        candles = context.featureSnapshot.rawInputs.get("spy1mCandles") or []
        if not candles:
            return None
        session_vwap = _number(features["sessionVwap"].value)
        session_vwap_slope = _number(features["sessionVwapSlope"].value)
        distance_from_vwap_atr = _number(features["distanceFromVwapAtr"].value)
        latest_close = _number(candles[-1].get("close")) if isinstance(candles[-1], dict) else None
        if session_vwap is None or session_vwap_slope is None or latest_close is None:
            return None
        vwap_distance_score = _normalize_existing_atr_ratio(distance_from_vwap_atr, self.config.normalizedVwapDistanceMaximumAtr)
        return SessionVwapContext(
            sessionVwap=session_vwap,
            pricePosition=self._hysteresis_state(vwap_distance_score, None) if vwap_distance_score is not None else 0,
            vwapSlope=_signed_threshold(session_vwap_slope, self.config.sessionVwapSlopeMagnitude),
            distanceFromVwapAtr=distance_from_vwap_atr,
            vwapDistanceScore=round(vwap_distance_score, 4) if vwap_distance_score is not None else None,
        )

    def _with_bar_evidence(
        self,
        result: StrategySignal,
        context: StrategyEvaluationContext,
        states: list[TimeframeTrendState],
        session_vwap_context: SessionVwapContext,
        regime_evidence: dict[str, Any],
    ) -> StrategySignal:
        evidence = {
            state.timeframe: {
                "parameters": self._timeframe_parameters(state.timeframe).model_dump(mode="json"),
                "barStartTimestamp": state.barStartTimestamp,
                "barEndTimestamp": state.barEndTimestamp,
                "barAgeSeconds": state.barAgeSeconds,
                "score": state.score,
                "latestClose": state.latestClose,
                "emaRelation": state.emaRelation,
                "emaSlope": state.emaSlope,
                "emaSpreadScore": state.emaSpreadScore,
                "ema9Slope": state.ema9Slope,
                "ema9SlopeScore": state.ema9SlopeScore,
                "ema20SlopeScore": state.ema20SlopeScore,
                "slopeAgreement": state.slopeAgreement,
                "structure": state.structure,
                "momentum": state.momentum,
                "momentumScore": state.momentumScore,
                "adx14": state.adx14,
                "atr14": state.atr14,
                "adxRegime": state.adxRegime,
                "adxQuality": state.adxQuality,
                "lateEntryRisk": state.lateEntryRisk,
                "rollingHigh": state.rollingHigh,
                "rollingLow": state.rollingLow,
                "longEntryLocation": state.longEntryLocation,
                "shortEntryLocation": state.shortEntryLocation,
                "longTrigger": {
                    "active": state.longTrigger.active,
                    "triggerType": state.longTrigger.triggerType,
                    "triggerLevel": state.longTrigger.triggerLevel,
                    "invalidationLevel": state.longTrigger.invalidationLevel,
                    "triggerTimestamp": state.longTrigger.triggerTimestamp,
                    "reason": state.longTrigger.reason,
                },
                "shortTrigger": {
                    "active": state.shortTrigger.active,
                    "triggerType": state.shortTrigger.triggerType,
                    "triggerLevel": state.shortTrigger.triggerLevel,
                    "invalidationLevel": state.shortTrigger.invalidationLevel,
                    "triggerTimestamp": state.shortTrigger.triggerTimestamp,
                    "reason": state.shortTrigger.reason,
                },
            }
            for state in states
        }
        state_by_timeframe = {state.timeframe: state for state in states}
        role_evidence = {}
        if {"1m", "5m", "15m"}.issubset(state_by_timeframe):
            one_minute = state_by_timeframe["1m"]
            five_minute = state_by_timeframe["5m"]
            fifteen_minute = state_by_timeframe["15m"]
            role_evidence = {
                "longPermission": self._permits_long(fifteen_minute),
                "longConfirmation": self._confirms_long(five_minute),
                "longTrigger": self._fresh_long_trigger(one_minute, session_vwap_context),
                "longSetup": self._setup_evidence(context, "long", one_minute, five_minute, fifteen_minute, session_vwap_context),
                "shortPermission": self._permits_short(fifteen_minute),
                "shortConfirmation": self._confirms_short(five_minute),
                "shortTrigger": self._fresh_short_trigger(one_minute, session_vwap_context),
                "shortSetup": self._setup_evidence(context, "short", one_minute, five_minute, fifteen_minute, session_vwap_context),
            }
        confidence_model = {}
        decision_trace = {}
        invalidation_levels: dict[str, Any] = {}
        if {"1m", "5m", "15m"}.issubset(state_by_timeframe):
            one_minute = state_by_timeframe["1m"]
            five_minute = state_by_timeframe["5m"]
            fifteen_minute = state_by_timeframe["15m"]
            trace_direction = self._trace_direction(result, one_minute, five_minute, fifteen_minute)
            trace_signal = Signal.BUY if trace_direction == "long" else Signal.SELL
            trace_numeric_direction = 1 if trace_signal == Signal.BUY else -1
            invalidation_levels = self._invalidation_levels(trace_direction, one_minute, five_minute, fifteen_minute)
            penalties = self._penalty_breakdown(trace_signal, one_minute, five_minute, fifteen_minute, session_vwap_context)
            trace_confidence_model = {
                "direction": trace_numeric_direction,
                "selectedTraceDirection": trace_direction,
                "oppositionPenalty": penalties["oppositionPenalty"],
                "overextensionPenalty": penalties["overextensionPenalty"],
                "totalPenalty": penalties["totalPenalty"],
            }
            if result.signal in {Signal.BUY.value, Signal.SELL.value, Signal.BUY, Signal.SELL}:
                signal = Signal(result.signal)
                direction = 1 if signal == Signal.BUY else -1
                trigger = one_minute.longTrigger if signal == Signal.BUY else one_minute.shortTrigger
                penalties = self._penalty_breakdown(signal, one_minute, five_minute, fifteen_minute, session_vwap_context)
                confidence_model = {
                    "direction": direction,
                    "permissionDirectionalQuality15m": _positive_support(fifteen_minute.score, direction),
                    "permissionAdxQuality15m": fifteen_minute.adxQuality,
                    "permissionQuality15m": self._permission_quality(fifteen_minute, direction),
                    "confirmationDirectionalQuality5m": _positive_support(five_minute.score, direction),
                    "confirmationAdxQuality5m": five_minute.adxQuality,
                    "confirmationQuality5m": self._confirmation_quality(five_minute, direction),
                    "triggerQuality1m": self._trigger_quality(trigger, one_minute, direction),
                    "sessionVwapContextQuality": self._session_vwap_quality(session_vwap_context, direction),
                    "trendStructureQuality": self._trend_structure_quality((one_minute, five_minute, fifteen_minute), direction),
                    "entryLocationQuality": self._entry_location_quality(one_minute),
                    "dataQuality": self._data_quality(context),
                    "lateEntryRisk": any(state.lateEntryRisk for state in (one_minute, five_minute, fifteen_minute)),
                    "oppositionPenalty": penalties["oppositionPenalty"],
                    "overextensionPenalty": penalties["overextensionPenalty"],
                    "totalPenalty": penalties["totalPenalty"],
                }
                trace_confidence_model = confidence_model
            decision_trace = self._decision_trace(
                result,
                context,
                trace_direction,
                one_minute,
                five_minute,
                fifteen_minute,
                session_vwap_context,
                invalidation_levels,
                trace_confidence_model,
                penalties,
            )
        return result.model_copy(
            update={
                "features": {
                    **result.features,
                    "multiTimeframeBarEvidence": {
                        "evaluationTimestamp": context.featureSnapshot.evaluationTimestamp.isoformat().replace("+00:00", "Z"),
                        "finalizationLagSeconds": context.featureSnapshot.rawInputs.get("finalizationLagSeconds"),
                        "hierarchy": "15m_permission_5m_confirmation_1m_trigger_session_vwap_context",
                        "sessionVwapContext": {
                            "sessionVwap": session_vwap_context.sessionVwap,
                            "pricePosition": session_vwap_context.pricePosition,
                            "vwapSlope": session_vwap_context.vwapSlope,
                            "distanceFromVwapAtr": session_vwap_context.distanceFromVwapAtr,
                            "vwapDistanceScore": session_vwap_context.vwapDistanceScore,
                            "hysteresisEntryThreshold": self.config.hysteresisEntryThreshold,
                            "hysteresisNeutralThreshold": self.config.hysteresisNeutralThreshold,
                            "countedOnce": True,
                        },
                        "regime": regime_evidence,
                        "confidenceModel": confidence_model,
                        "invalidationLevels": invalidation_levels if result.signal in {Signal.BUY.value, Signal.SELL.value, Signal.BUY, Signal.SELL} else {},
                        "decisionTrace": decision_trace,
                        "roles": role_evidence,
                        "timeframes": evidence,
                    },
                }
            }
        )

    def _directional_confidence(
        self,
        signal: Signal,
        one_minute: TimeframeTrendState,
        five_minute: TimeframeTrendState,
        fifteen_minute: TimeframeTrendState,
        session_vwap_context: SessionVwapContext,
        data_quality: float,
    ) -> float:
        direction = 1 if signal == Signal.BUY else -1
        trigger = one_minute.longTrigger if signal == Signal.BUY else one_minute.shortTrigger
        permission_quality = self._permission_quality(fifteen_minute, direction)
        confirmation_quality = self._confirmation_quality(five_minute, direction)
        trigger_quality = self._trigger_quality(trigger, one_minute, direction)
        session_vwap_quality = self._session_vwap_quality(session_vwap_context, direction)
        trend_structure_quality = self._trend_structure_quality((one_minute, five_minute, fifteen_minute), direction)
        entry_location_quality = self._entry_location_quality(one_minute)
        raw_confidence = (
            0.25 * permission_quality
            + 0.25 * confirmation_quality
            + 0.25 * trigger_quality
            + 0.10 * session_vwap_quality
            + 0.05 * trend_structure_quality
            + 0.05 * entry_location_quality
            + 0.05 * data_quality
        )
        penalty = self._confidence_penalty(signal, one_minute, five_minute, fifteen_minute, session_vwap_context)
        return round(max(0.0, min(1.0, raw_confidence - penalty)), 4)

    def _permission_quality(self, fifteen_minute: TimeframeTrendState, direction: int) -> float:
        directional_quality = _positive_support(fifteen_minute.score, direction)
        return max(0.0, min(1.0, directional_quality * (0.50 + (0.50 * fifteen_minute.adxQuality))))

    def _confirmation_quality(self, five_minute: TimeframeTrendState, direction: int) -> float:
        directional_quality = _positive_support(five_minute.score, direction)
        return max(0.0, min(1.0, directional_quality * (0.45 + (0.55 * five_minute.adxQuality))))

    def _adx_suitability(self, adx14: float | None) -> tuple[str, float, bool]:
        if adx14 is None:
            return "missing", 0.50, False
        if adx14 < self.config.veryLowAdxThreshold:
            return "very_low", 0.35, False
        if adx14 < self.config.moderateAdxThreshold:
            return "low", _linear_scale(adx14, self.config.veryLowAdxThreshold, self.config.moderateAdxThreshold, 0.35, 0.70), False
        if adx14 < self.config.highAdxThreshold:
            return "moderate", _linear_scale(adx14, self.config.moderateAdxThreshold, self.config.highAdxThreshold, 0.70, 0.95), False
        if adx14 < self.config.extremeAdxThreshold:
            return "high", 1.00, False
        return "extreme", 0.75, True

    def _trigger_quality(self, trigger: TriggerEvidence, one_minute: TimeframeTrendState, direction: int) -> float:
        if not trigger.active:
            return 0.0
        type_quality = {
            "ema_reclaim": 0.90,
            "micro_structure_break": 0.84,
        }.get(trigger.triggerType, 0.88 if trigger.triggerType.startswith("pullback_continuation") else 0.75)
        directional_score = _positive_support(one_minute.score, direction)
        momentum_bonus = 0.05 if one_minute.momentum == direction else 0.0
        acceleration_quality = self._slope_acceleration_quality(one_minute, direction)
        return max(0.0, min(1.0, (0.55 * type_quality) + (0.25 * directional_score) + (0.15 * acceleration_quality) + momentum_bonus))

    def _slope_acceleration_quality(self, state: TimeframeTrendState, direction: int) -> float:
        if state.emaSlope == direction and state.ema9Slope == direction:
            return 1.0
        if state.emaSlope == direction and state.ema9Slope == 0:
            return 0.70
        if state.emaSlope == direction and state.ema9Slope == -direction:
            return 0.45
        if state.emaSlope == -direction and state.ema9Slope == direction:
            return 0.25
        if state.emaSlope == -direction and state.ema9Slope == -direction:
            return 0.0
        return 0.50

    def _trend_structure_quality(self, states: tuple[TimeframeTrendState, ...], direction: int) -> float:
        values = []
        for state in states:
            directional_score = _positive_support(state.score, direction)
            structure = 1.0 if state.structure == direction else 0.5 if state.structure == 0 else 0.0
            slope = self._slope_acceleration_quality(state, direction)
            values.append((0.5 * directional_score) + (0.25 * structure) + (0.25 * slope))
        return sum(values) / len(values) if values else 0.0

    def _entry_location_quality(self, one_minute: TimeframeTrendState) -> float:
        if one_minute.distanceFromVwapAtr is None:
            return 0.5
        distance = abs(one_minute.distanceFromVwapAtr)
        return max(0.0, min(1.0, 1.0 - (distance / self.config.maxTriggerDistanceFromVwapAtr)))

    def _session_vwap_quality(self, session_vwap_context: SessionVwapContext, direction: int) -> float:
        price_quality = 1.0 if session_vwap_context.pricePosition == direction else 0.5 if session_vwap_context.pricePosition == 0 else 0.0
        slope_quality = 1.0 if session_vwap_context.vwapSlope == direction else 0.5 if session_vwap_context.vwapSlope == 0 else 0.0
        return (0.65 * price_quality) + (0.35 * slope_quality)

    def _confidence_penalty(
        self,
        signal: Signal,
        one_minute: TimeframeTrendState,
        five_minute: TimeframeTrendState,
        fifteen_minute: TimeframeTrendState,
        session_vwap_context: SessionVwapContext,
    ) -> float:
        return self._penalty_breakdown(signal, one_minute, five_minute, fifteen_minute, session_vwap_context)["totalPenalty"]

    def _penalty_breakdown(
        self,
        signal: Signal,
        one_minute: TimeframeTrendState,
        five_minute: TimeframeTrendState,
        fifteen_minute: TimeframeTrendState,
        session_vwap_context: SessionVwapContext,
    ) -> dict[str, float]:
        direction = 1 if signal == Signal.BUY else -1
        opposition_penalty = sum(
            weight * max(0.0, -(state.score * direction))
            for state, weight in ((fifteen_minute, 0.14), (five_minute, 0.10), (one_minute, 0.06))
        )
        vwap_context_penalty = 0.0
        if session_vwap_context.pricePosition == -direction:
            vwap_context_penalty += 0.08
        if session_vwap_context.vwapSlope == -direction:
            vwap_context_penalty += 0.03
        extension_penalty = 0.0
        if one_minute.distanceFromVwapAtr is not None:
            extension_ratio = abs(one_minute.distanceFromVwapAtr) / self.config.maxTriggerDistanceFromVwapAtr
            extension_penalty = max(0.0, min(0.12, (extension_ratio - 0.65) * 0.20))
        weak_adx_penalty = sum(
            0.03 for state in (fifteen_minute, five_minute, one_minute)
            if state.adx14 is not None and state.adx14 < self.config.weakAdxThreshold
        )
        late_entry_penalty = sum(0.04 for state in (fifteen_minute, five_minute, one_minute) if state.lateEntryRisk)
        acceleration_penalty = 0.0
        if one_minute.emaSlope == direction and one_minute.ema9Slope == -direction:
            acceleration_penalty += 0.05
        if one_minute.emaSlope == -direction and one_minute.ema9Slope == direction:
            acceleration_penalty += 0.08
        nearby_level_penalty = self._nearby_opposing_level_penalty(signal, one_minute)
        stale_penalty = 0.0
        if not self._recent(five_minute, self.config.maxConfirmationAgeSeconds * 0.5):
            stale_penalty += 0.04
        if not self._recent(one_minute, self.config.maxTriggerAgeSeconds * 0.5):
            stale_penalty += 0.04
        total = min(0.45, opposition_penalty + vwap_context_penalty + extension_penalty + weak_adx_penalty + late_entry_penalty + acceleration_penalty + nearby_level_penalty + stale_penalty)
        return {
            "oppositionPenalty": round(opposition_penalty, 4),
            "vwapContextPenalty": round(vwap_context_penalty, 4),
            "overextensionPenalty": round(extension_penalty, 4),
            "weakAdxPenalty": round(weak_adx_penalty, 4),
            "lateEntryPenalty": round(late_entry_penalty, 4),
            "accelerationPenalty": round(acceleration_penalty, 4),
            "nearbyLevelPenalty": round(nearby_level_penalty, 4),
            "stalePenalty": round(stale_penalty, 4),
            "totalPenalty": round(total, 4),
        }

    def _nearby_opposing_level_penalty(self, signal: Signal, one_minute: TimeframeTrendState) -> float:
        if signal == Signal.BUY and one_minute.rollingHigh is not None and one_minute.latestClose > 0:
            distance = max(0.0, (one_minute.rollingHigh - one_minute.latestClose) / one_minute.latestClose)
            return 0.05 if distance <= 0.0015 else 0.0
        if signal == Signal.SELL and one_minute.rollingLow is not None and one_minute.latestClose > 0:
            distance = max(0.0, (one_minute.latestClose - one_minute.rollingLow) / one_minute.latestClose)
            return 0.05 if distance <= 0.0015 else 0.0
        return 0.0

    def _invalidation_levels(
        self,
        direction: Literal["long", "short"],
        one_minute: TimeframeTrendState,
        five_minute: TimeframeTrendState,
        fifteen_minute: TimeframeTrendState,
    ) -> dict[str, Any]:
        trigger = one_minute.longTrigger if direction == "long" else one_minute.shortTrigger
        raw_entry_level = trigger.invalidationLevel if trigger.invalidationLevel is not None else one_minute.invalidationPrice
        entry = self._entry_invalidation(direction, one_minute, raw_entry_level, trigger.triggerType)
        confirmation_level = five_minute.rollingLow if direction == "long" else five_minute.rollingHigh
        permission_level = fifteen_minute.rollingLow if direction == "long" else fifteen_minute.rollingHigh
        return {
            "initialStopReference": "entry_invalidation",
            "direction": direction,
            "entryInvalidation": entry,
            "confirmationInvalidation": {
                "level": round(confirmation_level, 4) if confirmation_level is not None else None,
                "source": "5m_structure_confirmation",
                "timeframe": "5m",
            },
            "permissionInvalidation": {
                "level": round(permission_level, 4) if permission_level is not None else None,
                "source": "15m_directional_permission",
                "timeframe": "15m",
            },
            "policy": {
                "minimumAtrDistance": self.config.minInitialStopDistanceAtr,
                "maximumAtrDistance": self.config.maxInitialStopDistanceAtr,
                "spreadBufferAtr": self.config.spreadBufferAtr,
                "positionSizingLimitsApply": True,
            },
        }

    def _entry_invalidation(
        self,
        direction: Literal["long", "short"],
        one_minute: TimeframeTrendState,
        raw_level: float | None,
        trigger_type: str,
    ) -> dict[str, Any]:
        if raw_level is None or one_minute.atr14 is None or one_minute.atr14 <= 0:
            return {
                "level": None,
                "rawLevel": raw_level,
                "source": "1m_trigger_structure",
                "timeframe": "1m",
                "triggerType": trigger_type,
                "reasonCodes": ["entry_invalidation_unavailable"],
            }
        latest = one_minute.latestClose
        atr = one_minute.atr14
        minimum_distance = self.config.minInitialStopDistanceAtr * atr
        maximum_distance = self.config.maxInitialStopDistanceAtr * atr
        buffer = self.config.spreadBufferAtr * atr
        reasons: list[str] = []
        if direction == "long":
            raw_distance = max(0.0, latest - raw_level)
            distance = min(max(raw_distance, minimum_distance), maximum_distance)
            level = latest - distance - buffer
        else:
            raw_distance = max(0.0, raw_level - latest)
            distance = min(max(raw_distance, minimum_distance), maximum_distance)
            level = latest + distance + buffer
        if raw_distance < minimum_distance:
            reasons.append("raised_to_minimum_atr_distance")
        if raw_distance > maximum_distance:
            reasons.append("capped_to_maximum_atr_distance")
        if buffer > 0:
            reasons.append("spread_buffer_applied")
        return {
            "level": round(level, 4),
            "rawLevel": round(raw_level, 4),
            "source": "1m_trigger_or_pullback_swing",
            "timeframe": "1m",
            "triggerType": trigger_type,
            "distanceAtr": round(abs(latest - level) / atr, 4),
            "rawDistanceAtr": round(raw_distance / atr, 4),
            "reasonCodes": reasons,
        }

    def _hold_confidence(self, states: list[TimeframeTrendState], data_quality: float) -> float:
        conflict_penalty = max(abs(state.score) for state in states) if states else 0
        return round(max(0.05, min(0.45, 0.25 + (0.15 * data_quality) - (0.1 * conflict_penalty))), 4)

    def _data_quality(self, context: StrategyEvaluationContext) -> float:
        feature_names = self.required_feature_names()
        ready = sum(1 for name in feature_names if context.featureSnapshot.features.get(name) and context.featureSnapshot.features[name].quality == "READY")
        return ready / len(feature_names)

    def _reliability(self, states: list[TimeframeTrendState], slope_consistency: float, structure_consistency: float, data_quality: float) -> float:
        adx_quality = sum(state.adxQuality for state in states) / len(states) if states else 0.5
        late_entry_penalty = 0.08 if any(state.lateEntryRisk for state in states) else 0.0
        return round(max(0.0, min(1.0, (0.30 * slope_consistency) + (0.25 * structure_consistency) + (0.25 * adx_quality) + (0.20 * data_quality) - late_entry_penalty)), 4)

    def _trend_regime_evidence(
        self,
        context: StrategyEvaluationContext,
        states: list[TimeframeTrendState],
        session_vwap_context: SessionVwapContext,
    ) -> dict[str, Any]:
        raw_inputs = context.featureSnapshot.rawInputs
        candles_by_timeframe = {
            "1m": raw_inputs.get("spy1mCandles") or [],
            "5m": raw_inputs.get("spy5mCandles") or [],
            "15m": raw_inputs.get("spy15mCandles") or [],
        }
        adx_quality = _average([state.adxQuality for state in states])
        directional_efficiency = _average([
            _directional_efficiency(candles_by_timeframe["5m"]),
            _directional_efficiency(candles_by_timeframe["15m"]),
        ])
        choppiness = _average([
            _choppiness_score(candles_by_timeframe["5m"]),
            _choppiness_score(candles_by_timeframe["15m"]),
        ])
        atr_percentile = _average([
            _atr_percentile(candles_by_timeframe["1m"]),
            _atr_percentile(candles_by_timeframe["5m"]),
            _atr_percentile(candles_by_timeframe["15m"]),
        ])
        vwap_crossing_frequency = _vwap_crossing_frequency(candles_by_timeframe["1m"], session_vwap_context.sessionVwap)
        vwap_crossing_score = 1.0 - min(1.0, vwap_crossing_frequency * 4.0)
        ema_separation_stability = _average([
            _ema_separation_stability(candles_by_timeframe["1m"]),
            _ema_separation_stability(candles_by_timeframe["5m"]),
            _ema_separation_stability(candles_by_timeframe["15m"]),
        ])
        structure_consistency = max(
            sum(1 for state in states if state.structure > 0),
            sum(1 for state in states if state.structure < 0),
        ) / len(states) if states else 0.0
        trend_duration = _average([
            _trend_duration_score(candles_by_timeframe["1m"]),
            _trend_duration_score(candles_by_timeframe["5m"]),
            _trend_duration_score(candles_by_timeframe["15m"]),
        ])
        range_suitability = 1.0 - choppiness
        volatility_suitability = 1.0 - max(0.0, atr_percentile - 0.70) / 0.30
        suitability = max(
            0.0,
            min(
                1.0,
                (0.18 * adx_quality)
                + (0.18 * directional_efficiency)
                + (0.14 * range_suitability)
                + (0.10 * volatility_suitability)
                + (0.12 * vwap_crossing_score)
                + (0.12 * ema_separation_stability)
                + (0.08 * structure_consistency)
                + (0.08 * trend_duration),
            ),
        )
        regime = "TRANSITION"
        if atr_percentile >= 0.90 and (choppiness >= 0.55 or abs(session_vwap_context.vwapDistanceScore or 0.0) >= 0.80):
            regime = "HIGH_VOLATILITY_DISLOCATION"
            suitability = min(suitability, 0.35)
        elif suitability >= 0.72 and adx_quality >= 0.65 and directional_efficiency >= 0.45 and vwap_crossing_score >= 0.45:
            regime = "TRENDING"
        elif choppiness >= 0.62 or vwap_crossing_score <= 0.25:
            regime = "RANGE"
            suitability = min(suitability, 0.45)
        elif suitability >= 0.52:
            regime = "WEAK_TREND"
        return {
            "regime": regime,
            "regimeSuitability": round(suitability, 4),
            "metrics": {
                "adxQuality": round(adx_quality, 4),
                "directionalEfficiencyRatio": round(directional_efficiency, 4),
                "choppiness": round(choppiness, 4),
                "atrPercentile": round(atr_percentile, 4),
                "vwapCrossingFrequency": round(vwap_crossing_frequency, 4),
                "vwapCrossingScore": round(vwap_crossing_score, 4),
                "emaSeparationStability": round(ema_separation_stability, 4),
                "structureConsistency": round(structure_consistency, 4),
                "trendDuration": round(trend_duration, 4),
            },
        }

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


def _average(values: list[float | None]) -> float:
    ready = [value for value in values if value is not None]
    return sum(ready) / len(ready) if ready else 0.5


def _directional_efficiency(candles: list[dict[str, Any]]) -> float | None:
    closes = _close_values(candles)
    if len(closes) < 3:
        return None
    path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
    if path <= 0:
        return 0.0
    return _clip(abs(closes[-1] - closes[0]) / path, 0.0, 1.0)


def _choppiness_score(candles: list[dict[str, Any]]) -> float | None:
    if len(candles) < 3:
        return None
    highs = [float(candle["high"]) for candle in candles]
    lows = [float(candle["low"]) for candle in candles]
    total_range = max(highs) - min(lows)
    if total_range <= 0:
        return 1.0
    path_range = sum(max(0.0, float(candle["high"]) - float(candle["low"])) for candle in candles)
    return _clip((path_range / total_range - 1.0) / 4.0, 0.0, 1.0)


def _atr_percentile(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(candles) < period * 2:
        return None
    ranges = [max(0.0, float(candle["high"]) - float(candle["low"])) for candle in candles]
    rolling = [sum(ranges[index - period : index]) / period for index in range(period, len(ranges) + 1)]
    current = rolling[-1]
    if max(rolling) - min(rolling) <= 1e-12:
        return 0.5
    return sum(1 for value in rolling if value <= current) / len(rolling)


def _vwap_crossing_frequency(candles: list[dict[str, Any]], session_vwap: float) -> float:
    closes = _close_values(candles)
    if len(closes) < 2:
        return 0.0
    signs = [_sign(close - session_vwap) for close in closes]
    crossings = sum(1 for left, right in zip(signs, signs[1:]) if left != 0 and right != 0 and left != right)
    return crossings / (len(signs) - 1)


def _ema_separation_stability(candles: list[dict[str, Any]]) -> float | None:
    closes = _close_values(candles)
    if len(closes) < 25:
        return None
    ema9 = _ema_series(closes, 9)
    ema20 = _ema_series(closes, 20)
    signs = [_sign(fast - slow) for fast, slow in zip(ema9[-20:], ema20[-20:]) if fast is not None and slow is not None]
    directional = [value for value in signs if value != 0]
    if not directional:
        return 0.0
    dominant = max(directional.count(1), directional.count(-1))
    return dominant / len(directional)


def _trend_duration_score(candles: list[dict[str, Any]]) -> float | None:
    closes = _close_values(candles)
    if len(closes) < 25:
        return None
    ema9 = _ema_series(closes, 9)
    ema20 = _ema_series(closes, 20)
    signs = [_sign(fast - slow) for fast, slow in zip(ema9, ema20) if fast is not None and slow is not None]
    if not signs or signs[-1] == 0:
        return 0.0
    current = signs[-1]
    duration = 0
    for sign in reversed(signs):
        if sign != current:
            break
        duration += 1
    return _clip(duration / 20.0, 0.0, 1.0)


def _close_values(candles: list[dict[str, Any]]) -> list[float]:
    return [float(candle["close"]) for candle in candles if "close" in candle]


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _last_number(values: list[float | None]) -> float | None:
    for value in reversed(values):
        if value is not None:
            return value
    return None


def _ema_series(values: list[float], period: int) -> list[float | None]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    result: list[float | None] = []
    ema_value: float | None = None
    for index, value in enumerate(values):
        if index + 1 < period:
            result.append(None)
            continue
        if ema_value is None:
            ema_value = sum(values[:period]) / period
        else:
            ema_value = (value * alpha) + (ema_value * (1 - alpha))
        result.append(ema_value)
    return result


def _signed_threshold(value: float, threshold: float) -> int:
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


def _normalize_by_atr(value: float, atr: float, maximum_atr_units: float) -> float:
    if atr <= 0 or maximum_atr_units <= 0:
        return 0.0
    return _clip((value / atr) / maximum_atr_units, -1.0, 1.0)


def _normalize_existing_atr_ratio(value: float | None, maximum_atr_units: float) -> float | None:
    if value is None or maximum_atr_units <= 0:
        return None
    return _clip(value / maximum_atr_units, -1.0, 1.0)


def _clip(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _linear_scale(value: float, left: float, right: float, output_left: float, output_right: float) -> float:
    if right <= left:
        return output_right
    ratio = _clip((value - left) / (right - left), 0.0, 1.0)
    return output_left + ((output_right - output_left) * ratio)


def _slope_agreement(ema20_slope: int, ema9_slope: int) -> str:
    if ema20_slope > 0 and ema9_slope > 0:
        return "strong_bullish_agreement"
    if ema20_slope > 0 and ema9_slope < 0:
        return "bullish_trend_under_pullback"
    if ema20_slope < 0 and ema9_slope > 0:
        return "possible_countertrend_bounce"
    if ema20_slope < 0 and ema9_slope < 0:
        return "bearish_agreement"
    if ema20_slope > 0:
        return "bullish_trend_neutral_acceleration"
    if ema20_slope < 0:
        return "bearish_trend_neutral_acceleration"
    if ema9_slope > 0:
        return "neutral_trend_bullish_acceleration"
    if ema9_slope < 0:
        return "neutral_trend_bearish_acceleration"
    return "neutral_slope"


def _entry_location_not_applicable() -> dict[str, Any]:
    return {"allowed": True, "reasonCodes": [], "notApplicable": True}


def _consecutive_directional_candles(candles: list[dict[str, Any]], direction: Literal["long", "short"]) -> int:
    count = 0
    for candle in reversed(candles):
        open_price = float(candle.get("open", 0.0))
        close = float(candle.get("close", 0.0))
        directional = close > open_price if direction == "long" else close < open_price
        if not directional:
            break
        count += 1
    return count


def _volume_state(volume_ratio: float | None, close_location: float, min_continuation_ratio: float, max_exhaustion_ratio: float) -> str:
    if volume_ratio is None:
        return "unknown"
    if volume_ratio > max_exhaustion_ratio and close_location < 0.75:
        return "exhaustion"
    if volume_ratio >= min_continuation_ratio:
        return "continuation"
    return "weak"


def _inactive_trigger(reason: str) -> TriggerEvidence:
    return TriggerEvidence(False, "none", None, None, None, reason)


def _positive_support(score: float, direction: int) -> float:
    return max(0.0, min(1.0, score * direction))


def _age_seconds(evaluation_timestamp: Any, bar_end_timestamp: str | None) -> float | None:
    if not bar_end_timestamp:
        return None
    try:
        bar_end = datetime_from_iso(bar_end_timestamp)
    except ValueError:
        return None
    return max(0.0, (evaluation_timestamp - bar_end).total_seconds())


def datetime_from_iso(value: str):
    from datetime import UTC, datetime

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _cooldown_active(evaluation_timestamp: Any, cooldown_until: Any) -> bool:
    if not cooldown_until:
        return False
    try:
        return datetime_from_iso(str(cooldown_until)) > evaluation_timestamp
    except ValueError:
        return False


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
