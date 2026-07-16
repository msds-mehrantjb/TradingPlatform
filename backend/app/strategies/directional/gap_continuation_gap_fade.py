from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from statistics import mean
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


GapSetupState = Literal["none", "gap_continuation", "gap_fade"]
GapDirection = Literal["UP", "DOWN", "NONE"]


class GapContinuationFadeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "gap_continuation_gap_fade_v1"
    minGapPercent: float = Field(default=0.35, gt=0)
    minGapAtrMultiple: float = Field(default=0.55, gt=0)
    openingStructureCandles: int = Field(default=5, ge=2, le=20)
    minInitialRelativeVolume: float = Field(default=1.05, ge=0)
    continuationStartMinute: float = Field(default=3.0, ge=0)
    continuationEndMinute: float = Field(default=60.0, ge=0)
    fadeStartMinute: float = Field(default=5.0, ge=0)
    fadeEndMinute: float = Field(default=75.0, ge=0)
    minContinuationProgressAtr: float = Field(default=0.35, ge=0)
    minFadeProgressOfGap: float = Field(default=0.35, ge=0, le=1)
    maxFadeAdx: float = Field(default=30.0, ge=0, le=100)
    maxEventRiskForActiveTrade: float = Field(default=0.8, ge=0, le=1)

    @model_validator(mode="after")
    def windows_must_be_ordered(self) -> GapContinuationFadeConfig:
        if self.continuationEndMinute < self.continuationStartMinute:
            raise ValueError("continuationEndMinute must be greater than or equal to continuationStartMinute")
        if self.fadeEndMinute < self.fadeStartMinute:
            raise ValueError("fadeEndMinute must be greater than or equal to fadeStartMinute")
        return self

    @property
    def configurationHash(self) -> str:
        payload = self.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class GapContinuationFadeEvidence:
    signal: Signal
    setupState: GapSetupState
    gapDirection: GapDirection
    gapPercent: float
    gapAtrMultiple: float
    openingPosition: str
    initialVolumeRatio: float | None
    timeSinceOpenMinutes: float | None
    continuationDetected: bool
    fadeDetected: bool
    continuationScore: float
    fadeScore: float
    openingStructureAligned: bool
    marketContextScore: float
    eventRiskScore: float
    structuralInvalidationPrice: float | None


class GapContinuationFadeStrategy:
    registryEntry = resolve_strategy("gap_continuation_gap_fade")

    def __init__(self, config: GapContinuationFadeConfig | None = None) -> None:
        self.config = config or GapContinuationFadeConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> StrategySignal:
        required_features = self.required_feature_names()
        if not context.featureSnapshot.dataReady:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Feature snapshot is not ready for gap continuation / gap fade.",
            )
        if not required_features_ready(context.featureSnapshot, required_features):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Required feature measurements are unavailable for gap continuation / gap fade.",
            )

        candles = _session_candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [], context)
        if len(candles) < self.config.openingStructureCandles + 1:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Insufficient completed regular-session candles for gap continuation / gap fade.",
            )

        evidence = self._evidence(context, candles)
        if evidence.signal in {Signal.BUY, Signal.SELL}:
            return strategy_signal(
                context,
                signal=evidence.signal,
                confidence=self._confidence(evidence),
                eligible=True,
                setupDetected=True,
                regimeFit=self._regime_fit(evidence),
                reliability=self._reliability(evidence),
                reasonCodes=[
                    f"gap.{evidence.setupState}",
                    f"direction:{evidence.signal.value.lower()}",
                    f"gap_direction:{evidence.gapDirection.lower()}",
                    f"gap_atr:{evidence.gapAtrMultiple:.2f}",
                ],
                explanation=self._explanation(evidence),
                featureNames=required_features,
                structuralInvalidationPrice=evidence.structuralInvalidationPrice,
            )

        return hold_signal(
            context,
            confidence=self._hold_confidence(evidence),
            setupDetected=evidence.gapDirection != "NONE",
            regimeFit=self._regime_fit(evidence),
            reliability=self._reliability(evidence),
            reasonCodes=self._hold_reason_codes(evidence),
            explanation=self._hold_explanation(evidence),
            featureNames=required_features,
            structuralInvalidationPrice=evidence.structuralInvalidationPrice,
        )

    def required_feature_names(self) -> tuple[str, ...]:
        return (
            "gapPercent",
            "spy1mAtr14",
            "spy1mAdx14",
            "spy1mRelativeVolume",
            "premarketHigh",
            "premarketLow",
            "timeSinceMarketOpenMinutes",
            "breadthProxyAverageReturn",
            "relativeStrengthQqq",
            "relativeStrengthIwm",
            "economicEventState",
        )

    def _evidence(self, context: StrategyEvaluationContext, candles: list[dict[str, Any]]) -> GapContinuationFadeEvidence:
        features = context.featureSnapshot.features
        prior_day = context.featureSnapshot.rawInputs.get("priorDayOHLC") or {}
        prior_close = _number(prior_day.get("close"))
        atr = _number(features["spy1mAtr14"].value)
        adx = _number(features["spy1mAdx14"].value)
        gap_percent = _number(features["gapPercent"].value)
        latest_relative_volume = _number(features["spy1mRelativeVolume"].value)
        premarket_high = _number(features["premarketHigh"].value)
        premarket_low = _number(features["premarketLow"].value)
        time_since_open = _number(features["timeSinceMarketOpenMinutes"].value)
        if None in {prior_close, atr, adx, gap_percent, latest_relative_volume, premarket_high, premarket_low, time_since_open}:
            return _empty_evidence()

        assert prior_close is not None
        assert atr is not None
        assert adx is not None
        assert gap_percent is not None
        assert latest_relative_volume is not None
        assert premarket_high is not None
        assert premarket_low is not None
        assert time_since_open is not None

        regular_open = float(candles[0]["open"])
        latest = candles[-1]
        latest_close = float(latest["close"])
        gap_dollars = regular_open - prior_close
        gap_direction: GapDirection = "UP" if gap_dollars > 0 else "DOWN" if gap_dollars < 0 else "NONE"
        gap_atr = abs(gap_dollars) / atr if atr else 0.0
        meaningful_gap = (
            gap_direction != "NONE"
            and abs(gap_percent) >= self.config.minGapPercent
            and gap_atr >= self.config.minGapAtrMultiple
        )
        initial_volume_ratio = _initial_volume_ratio(candles, self.config.openingStructureCandles)
        volume_ok = (
            initial_volume_ratio is not None
            and initial_volume_ratio >= self.config.minInitialRelativeVolume
            and latest_relative_volume >= self.config.minInitialRelativeVolume * 0.85
        )
        opening_position = _opening_position(regular_open, premarket_high, premarket_low)
        opening_structure = _opening_structure(candles, self.config.openingStructureCandles, gap_direction)
        continuation_window = self.config.continuationStartMinute <= time_since_open <= self.config.continuationEndMinute
        fade_window = self.config.fadeStartMinute <= time_since_open <= self.config.fadeEndMinute
        event_risk = _event_risk_score(features["economicEventState"].value)
        market_context = _market_context_score(features, gap_direction)

        if gap_direction == "UP":
            continuation_progress = (latest_close - regular_open) / atr
            fade_progress = (regular_open - latest_close) / max(abs(gap_dollars), 0.01)
            continuation_detected = (
                meaningful_gap
                and continuation_window
                and volume_ok
                and opening_structure
                and latest_close > regular_open
                and latest_close >= premarket_high
                and continuation_progress >= self.config.minContinuationProgressAtr
                and event_risk <= self.config.maxEventRiskForActiveTrade
            )
            fade_detected = (
                meaningful_gap
                and fade_window
                and latest_close < regular_open
                and fade_progress >= self.config.minFadeProgressOfGap
                and latest_close < premarket_high
                and adx <= self.config.maxFadeAdx
                and event_risk <= self.config.maxEventRiskForActiveTrade
            )
            continuation_signal = Signal.BUY
            fade_signal = Signal.SELL
            continuation_invalidation = min(float(candle["low"]) for candle in candles[: self.config.openingStructureCandles])
            fade_invalidation = max(float(candle["high"]) for candle in candles[: self.config.openingStructureCandles])
        elif gap_direction == "DOWN":
            continuation_progress = (regular_open - latest_close) / atr
            fade_progress = (latest_close - regular_open) / max(abs(gap_dollars), 0.01)
            continuation_detected = (
                meaningful_gap
                and continuation_window
                and volume_ok
                and opening_structure
                and latest_close < regular_open
                and latest_close <= premarket_low
                and continuation_progress >= self.config.minContinuationProgressAtr
                and event_risk <= self.config.maxEventRiskForActiveTrade
            )
            fade_detected = (
                meaningful_gap
                and fade_window
                and latest_close > regular_open
                and fade_progress >= self.config.minFadeProgressOfGap
                and latest_close > premarket_low
                and adx <= self.config.maxFadeAdx
                and event_risk <= self.config.maxEventRiskForActiveTrade
            )
            continuation_signal = Signal.SELL
            fade_signal = Signal.BUY
            continuation_invalidation = max(float(candle["high"]) for candle in candles[: self.config.openingStructureCandles])
            fade_invalidation = min(float(candle["low"]) for candle in candles[: self.config.openingStructureCandles])
        else:
            continuation_progress = 0.0
            fade_progress = 0.0
            continuation_detected = False
            fade_detected = False
            continuation_signal = Signal.HOLD
            fade_signal = Signal.HOLD
            continuation_invalidation = None
            fade_invalidation = None

        continuation_score = _score(
            gap_atr / max(self.config.minGapAtrMultiple * 2, 0.01),
            max(0.0, continuation_progress) / max(self.config.minContinuationProgressAtr * 2, 0.01),
            1.0 if volume_ok else 0.0,
            1.0 if opening_structure else 0.0,
            market_context,
            1.0 - event_risk,
        )
        fade_score = _score(
            gap_atr / max(self.config.minGapAtrMultiple * 2, 0.01),
            max(0.0, fade_progress) / max(self.config.minFadeProgressOfGap * 2, 0.01),
            max(0.0, 1.0 - adx / max(self.config.maxFadeAdx, 0.01)),
            market_context,
            1.0 - event_risk,
        )

        # Choose one setup state deterministically. Continuation and fade cannot be active at the same time.
        if continuation_detected and (not fade_detected or continuation_score >= fade_score):
            setup_state: GapSetupState = "gap_continuation"
            signal = continuation_signal
            invalidation = continuation_invalidation
            fade_detected = False
        elif fade_detected:
            setup_state = "gap_fade"
            signal = fade_signal
            invalidation = fade_invalidation
            continuation_detected = False
        else:
            setup_state = "none"
            signal = Signal.HOLD
            invalidation = continuation_invalidation if continuation_progress > fade_progress else fade_invalidation

        return GapContinuationFadeEvidence(
            signal=signal,
            setupState=setup_state,
            gapDirection=gap_direction,
            gapPercent=gap_percent,
            gapAtrMultiple=gap_atr,
            openingPosition=opening_position,
            initialVolumeRatio=initial_volume_ratio,
            timeSinceOpenMinutes=time_since_open,
            continuationDetected=continuation_detected,
            fadeDetected=fade_detected,
            continuationScore=continuation_score,
            fadeScore=fade_score,
            openingStructureAligned=opening_structure,
            marketContextScore=market_context,
            eventRiskScore=event_risk,
            structuralInvalidationPrice=round(invalidation, 4) if invalidation is not None else None,
        )

    def _confidence(self, evidence: GapContinuationFadeEvidence) -> float:
        setup_score = evidence.continuationScore if evidence.setupState == "gap_continuation" else evidence.fadeScore
        confidence = 0.65 * setup_score + 0.2 * min(1.0, evidence.gapAtrMultiple / max(self.config.minGapAtrMultiple * 2, 0.01)) + 0.15 * (1.0 - evidence.eventRiskScore)
        return round(max(0.0, min(1.0, confidence)), 4)

    def _hold_confidence(self, evidence: GapContinuationFadeEvidence) -> float:
        partials = [
            evidence.gapDirection != "NONE",
            abs(evidence.gapPercent) >= self.config.minGapPercent,
            evidence.gapAtrMultiple >= self.config.minGapAtrMultiple,
            evidence.openingStructureAligned,
            evidence.initialVolumeRatio is not None and evidence.initialVolumeRatio >= self.config.minInitialRelativeVolume,
            evidence.eventRiskScore <= self.config.maxEventRiskForActiveTrade,
        ]
        return round(min(0.44, max(0.05, sum(1 for item in partials if item) / len(partials) * 0.44)), 4)

    def _regime_fit(self, evidence: GapContinuationFadeEvidence) -> float:
        if evidence.gapDirection == "NONE":
            return 0.0
        gap_fit = min(1.0, evidence.gapAtrMultiple / max(self.config.minGapAtrMultiple * 2, 0.01))
        context_fit = evidence.marketContextScore
        event_fit = 1.0 - evidence.eventRiskScore
        return round(max(0.0, min(1.0, (0.45 * gap_fit) + (0.35 * context_fit) + (0.2 * event_fit))), 4)

    def _reliability(self, evidence: GapContinuationFadeEvidence) -> float:
        checks = [
            evidence.gapDirection != "NONE",
            evidence.gapAtrMultiple >= self.config.minGapAtrMultiple,
            evidence.openingPosition != "inside_premarket_range",
            evidence.openingStructureAligned or evidence.fadeDetected,
            evidence.initialVolumeRatio is not None and evidence.initialVolumeRatio >= self.config.minInitialRelativeVolume,
            evidence.eventRiskScore <= self.config.maxEventRiskForActiveTrade,
            not (evidence.continuationDetected and evidence.fadeDetected),
        ]
        return round(max(0.0, min(1.0, sum(1 for item in checks if item) / len(checks))), 4)

    def _explanation(self, evidence: GapContinuationFadeEvidence) -> str:
        return (
            f"{evidence.signal.value} {evidence.setupState.replace('_', ' ')}: {evidence.gapDirection.lower()} gap "
            f"{evidence.gapPercent:.2f}% / {evidence.gapAtrMultiple:.2f} ATR, opening position {evidence.openingPosition}."
        )

    def _hold_reason_codes(self, evidence: GapContinuationFadeEvidence) -> list[str]:
        if evidence.gapDirection == "NONE" or abs(evidence.gapPercent) < self.config.minGapPercent or evidence.gapAtrMultiple < self.config.minGapAtrMultiple:
            return ["gap.no_meaningful_gap"]
        if evidence.timeSinceOpenMinutes is not None and evidence.timeSinceOpenMinutes > max(self.config.continuationEndMinute, self.config.fadeEndMinute):
            return ["gap.outside_activation_window"]
        if evidence.eventRiskScore > self.config.maxEventRiskForActiveTrade:
            return ["gap.event_context_too_risky"]
        if evidence.initialVolumeRatio is None or evidence.initialVolumeRatio < self.config.minInitialRelativeVolume:
            return ["gap.initial_volume_insufficient"]
        if not evidence.openingStructureAligned and evidence.continuationScore >= evidence.fadeScore:
            return ["gap.opening_structure_not_aligned"]
        return ["gap.no_clear_continuation_or_fade"]

    def _hold_explanation(self, evidence: GapContinuationFadeEvidence) -> str:
        reason = self._hold_reason_codes(evidence)[0].removeprefix("gap.").replace("_", " ")
        return (
            f"HOLD because gap continuation/fade evidence is incomplete: {reason}; "
            f"gap {evidence.gapPercent:.2f}% / {evidence.gapAtrMultiple:.2f} ATR."
        )


def _empty_evidence() -> GapContinuationFadeEvidence:
    return GapContinuationFadeEvidence(
        signal=Signal.HOLD,
        setupState="none",
        gapDirection="NONE",
        gapPercent=0.0,
        gapAtrMultiple=0.0,
        openingPosition="unknown",
        initialVolumeRatio=None,
        timeSinceOpenMinutes=None,
        continuationDetected=False,
        fadeDetected=False,
        continuationScore=0.0,
        fadeScore=0.0,
        openingStructureAligned=False,
        marketContextScore=0.0,
        eventRiskScore=0.0,
        structuralInvalidationPrice=None,
    )


def _session_candles(raw_candles: list[dict[str, Any]], context: StrategyEvaluationContext) -> list[dict[str, Any]]:
    completed = []
    for candle in raw_candles:
        timestamp = _timestamp(candle)
        if timestamp > context.evaluatedAt:
            continue
        if _new_york_datetime(timestamp).date() != context.sessionDate:
            continue
        completed.append(candle)
    return sorted(completed, key=lambda candle: _timestamp(candle))


def _initial_volume_ratio(candles: list[dict[str, Any]], opening_count: int) -> float | None:
    if len(candles) < opening_count + 2:
        return None
    opening = candles[:opening_count]
    later = candles[opening_count:]
    later_sample = later[: max(opening_count, min(len(later), opening_count * 2))]
    baseline = mean(float(candle["volume"]) for candle in later_sample) if later_sample else 0.0
    if baseline <= 0:
        return None
    return mean(float(candle["volume"]) for candle in opening) / baseline


def _opening_position(open_price: float, premarket_high: float, premarket_low: float) -> str:
    if open_price > premarket_high:
        return "above_premarket_high"
    if open_price < premarket_low:
        return "below_premarket_low"
    return "inside_premarket_range"


def _opening_structure(candles: list[dict[str, Any]], opening_count: int, gap_direction: GapDirection) -> bool:
    sample = candles[:opening_count]
    if len(sample) < opening_count:
        return False
    first_open = float(sample[0]["open"])
    last_close = float(sample[-1]["close"])
    highs = [float(candle["high"]) for candle in sample]
    lows = [float(candle["low"]) for candle in sample]
    if gap_direction == "UP":
        return last_close >= first_open and highs[-1] >= highs[0] and lows[-1] >= min(lows[:2])
    if gap_direction == "DOWN":
        return last_close <= first_open and lows[-1] <= lows[0] and highs[-1] <= max(highs[:2])
    return False


def _event_risk_score(value: Any) -> float:
    if not isinstance(value, dict):
        return 0.0
    for key in ("riskScore", "risk", "impactScore"):
        number = _number(value.get(key))
        if number is not None:
            return max(0.0, min(1.0, number))
    impact = str(value.get("impact") or value.get("severity") or "").lower()
    if impact in {"high", "red"}:
        return 0.8
    if impact in {"medium", "yellow"}:
        return 0.45
    return 0.0


def _market_context_score(features: dict[str, Any], gap_direction: GapDirection) -> float:
    breadth = _number(features["breadthProxyAverageReturn"].value) or 0.0
    qqq = _number(features["relativeStrengthQqq"].value) or 1.0
    iwm = _number(features["relativeStrengthIwm"].value) or 1.0
    if gap_direction == "UP":
        breadth_score = 1.0 if breadth >= 0 else 0.35
        relative_score = 1.0 if qqq >= 1.0 and iwm >= 1.0 else 0.55
    elif gap_direction == "DOWN":
        breadth_score = 1.0 if breadth <= 0 else 0.35
        relative_score = 1.0 if qqq <= 1.0 and iwm <= 1.0 else 0.55
    else:
        return 0.0
    return round(max(0.0, min(1.0, (0.6 * breadth_score) + (0.4 * relative_score))), 4)


def _score(*values: float) -> float:
    if not values:
        return 0.0
    capped = [max(0.0, min(1.0, value)) for value in values]
    return round(mean(capped), 4)


def _timestamp(candle: dict[str, Any]) -> datetime:
    value = candle["timestamp"]
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _nth_sunday(year: int, month: int, nth: int) -> int:
    first = datetime(year, month, 1)
    first_sunday = 1 + ((6 - first.weekday()) % 7)
    return first_sunday + ((nth - 1) * 7)


def _new_york_datetime(value: datetime) -> datetime:
    utc_value = value.astimezone(UTC)
    year = utc_value.year
    dst_start_utc = datetime(year, 3, _nth_sunday(year, 3, 2), 7, 0, tzinfo=UTC)
    dst_end_utc = datetime(year, 11, _nth_sunday(year, 11, 1), 6, 0, tzinfo=UTC)
    offset_hours = -4 if dst_start_utc <= utc_value < dst_end_utc else -5
    return utc_value.astimezone(timezone(timedelta(hours=offset_hours), "America/New_York"))
