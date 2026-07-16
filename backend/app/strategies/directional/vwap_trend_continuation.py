from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from statistics import mean
from typing import Any

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


class VwapTrendContinuationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "vwap_trend_continuation_v1"
    minVwapSlope: float = Field(default=0.00008, ge=0)
    minEmaSlope: float = Field(default=0.00004, ge=0)
    pullbackLookbackCandles: int = Field(default=6, ge=2, le=30)
    volumeLookbackCandles: int = Field(default=20, ge=5, le=60)
    pullbackAtrTolerance: float = Field(default=0.35, ge=0)
    reclaimAtrThreshold: float = Field(default=0.04, ge=0)
    rejectionAtrThreshold: float = Field(default=0.04, ge=0)
    minConfirmationVolumeRatio: float = Field(default=1.05, ge=0)
    maxEntryDistanceAtr: float = Field(default=1.7, gt=0)
    extendedEntryDistanceAtr: float = Field(default=2.4, gt=0)
    minStructureScore: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def distance_bounds_must_be_ordered(self) -> VwapTrendContinuationConfig:
        if self.extendedEntryDistanceAtr < self.maxEntryDistanceAtr:
            raise ValueError("extendedEntryDistanceAtr must be greater than or equal to maxEntryDistanceAtr")
        return self

    @property
    def configurationHash(self) -> str:
        payload = self.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class VwapContinuationEvidence:
    signal: Signal
    latestClose: float
    sessionVwap: float
    atr: float
    distanceAtr: float
    vwapSlope: float
    emaAligned: bool
    emaSlopeAligned: bool
    structureAligned: bool
    pullbackDetected: bool
    confirmationDetected: bool
    volumeRatio: float | None
    pullbackIndex: int | None
    structuralInvalidationPrice: float | None


class VwapTrendContinuationStrategy:
    registryEntry = resolve_strategy("vwap_trend_continuation")

    def __init__(self, config: VwapTrendContinuationConfig | None = None) -> None:
        self.config = config or VwapTrendContinuationConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> StrategySignal:
        required_features = self.required_feature_names()
        if not context.featureSnapshot.dataReady:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Feature snapshot is not ready for VWAP trend continuation.",
            )
        if not required_features_ready(context.featureSnapshot, required_features):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Required feature measurements are unavailable for VWAP trend continuation.",
            )

        candles = _session_candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [], context)
        if len(candles) < max(self.config.volumeLookbackCandles + 1, self.config.pullbackLookbackCandles + 2):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Insufficient completed 1-minute candles for VWAP trend continuation.",
            )

        buy_evidence = self._evidence(context, candles, Signal.BUY)
        sell_evidence = self._evidence(context, candles, Signal.SELL)
        evidence = buy_evidence if buy_evidence.signal == Signal.BUY else sell_evidence if sell_evidence.signal == Signal.SELL else buy_evidence

        if evidence.signal in {Signal.BUY, Signal.SELL}:
            confidence = self._confidence(evidence)
            return strategy_signal(
                context,
                signal=evidence.signal,
                confidence=confidence,
                eligible=confidence >= 0.45,
                setupDetected=True,
                regimeFit=self._regime_fit(evidence),
                reliability=self._reliability(evidence),
                reasonCodes=[
                    "vwap_continuation.completed",
                    f"direction:{evidence.signal.value.lower()}",
                    f"distance_atr:{evidence.distanceAtr:.2f}",
                ],
                explanation=self._explanation(evidence),
                featureNames=required_features,
                structuralInvalidationPrice=evidence.structuralInvalidationPrice,
            )

        reason_codes = self._hold_reason_codes(buy_evidence, sell_evidence)
        return hold_signal(
            context,
            confidence=max(self._hold_confidence(buy_evidence), self._hold_confidence(sell_evidence)),
            setupDetected=buy_evidence.pullbackDetected or sell_evidence.pullbackDetected,
            regimeFit=0.4,
            reliability=max(self._reliability(buy_evidence), self._reliability(sell_evidence)),
            reasonCodes=reason_codes,
            explanation=self._hold_explanation(buy_evidence, sell_evidence),
            featureNames=required_features,
        )

    def required_feature_names(self) -> tuple[str, ...]:
        return (
            "sessionVwap",
            "sessionVwapSlope",
            "distanceFromVwapAtr",
            "spy1mEma9",
            "spy1mEma20",
            "spy1mEma9Slope",
            "spy1mEma20Slope",
            "spy1mAtr14",
            "spy1mRelativeVolume",
            "spy1mHigherHighHigherLow",
            "spy1mLowerHighLowerLow",
            "spy1mRollingHigh20",
            "spy1mRollingLow20",
        )

    def _evidence(
        self,
        context: StrategyEvaluationContext,
        candles: list[dict[str, Any]],
        direction: Signal,
    ) -> VwapContinuationEvidence:
        features = context.featureSnapshot.features
        latest = candles[-1]
        previous = candles[-2]
        latest_close = float(latest["close"])
        session_vwap = _number(features["sessionVwap"].value)
        vwap_slope = _number(features["sessionVwapSlope"].value)
        atr = _number(features["spy1mAtr14"].value)
        ema9 = _number(features["spy1mEma9"].value)
        ema20 = _number(features["spy1mEma20"].value)
        ema9_slope = _number(features["spy1mEma9Slope"].value)
        ema20_slope = _number(features["spy1mEma20Slope"].value)
        distance_atr = _number(features["distanceFromVwapAtr"].value)
        rolling_high = _number(features["spy1mRollingHigh20"].value)
        rolling_low = _number(features["spy1mRollingLow20"].value)
        if None in {session_vwap, vwap_slope, atr, ema9, ema20, ema9_slope, ema20_slope, distance_atr}:
            return _empty_evidence(direction)

        assert session_vwap is not None
        assert vwap_slope is not None
        assert atr is not None
        assert ema9 is not None
        assert ema20 is not None
        assert ema9_slope is not None
        assert ema20_slope is not None
        assert distance_atr is not None

        if direction == Signal.BUY:
            trend_ok = vwap_slope >= self.config.minVwapSlope
            ema_aligned = ema9 > ema20
            ema_slope_aligned = ema9_slope >= self.config.minEmaSlope and ema20_slope >= 0
            structure_aligned = bool(features["spy1mHigherHighHigherLow"].value)
            pullback_index = self._buy_pullback_index(candles, session_vwap, atr)
            confirmation = self._buy_confirmation(latest, previous, session_vwap, atr)
            distance = abs(distance_atr)
            price_position_ok = latest_close >= session_vwap + (atr * self.config.reclaimAtrThreshold)
            signal = Signal.BUY
            invalidation = rolling_low
        else:
            trend_ok = vwap_slope <= -self.config.minVwapSlope
            ema_aligned = ema9 < ema20
            ema_slope_aligned = ema9_slope <= -self.config.minEmaSlope and ema20_slope <= 0
            structure_aligned = bool(features["spy1mLowerHighLowerLow"].value)
            pullback_index = self._sell_pullback_index(candles, session_vwap, atr)
            confirmation = self._sell_confirmation(latest, previous, session_vwap, atr)
            distance = abs(distance_atr)
            price_position_ok = latest_close <= session_vwap - (atr * self.config.rejectionAtrThreshold)
            signal = Signal.SELL
            invalidation = rolling_high

        pullback_detected = pullback_index is not None
        volume_ratio = self._volume_ratio(candles)
        volume_ok = volume_ratio is not None and volume_ratio >= self.config.minConfirmationVolumeRatio
        distance_ok = distance <= self.config.maxEntryDistanceAtr
        excessive = distance > self.config.extendedEntryDistanceAtr
        should_signal = all(
            [
                trend_ok,
                price_position_ok,
                ema_aligned,
                ema_slope_aligned,
                structure_aligned,
                pullback_detected,
                confirmation,
                volume_ok,
                distance_ok,
                not excessive,
            ]
        )

        return VwapContinuationEvidence(
            signal=signal if should_signal else Signal.HOLD,
            latestClose=latest_close,
            sessionVwap=session_vwap,
            atr=atr,
            distanceAtr=distance,
            vwapSlope=vwap_slope,
            emaAligned=ema_aligned,
            emaSlopeAligned=ema_slope_aligned,
            structureAligned=structure_aligned,
            pullbackDetected=pullback_detected,
            confirmationDetected=confirmation,
            volumeRatio=volume_ratio,
            pullbackIndex=pullback_index,
            structuralInvalidationPrice=round(invalidation, 4) if invalidation is not None else None,
        )

    def _buy_pullback_index(self, candles: list[dict[str, Any]], session_vwap: float, atr: float) -> int | None:
        tolerance = atr * self.config.pullbackAtrTolerance
        start = max(0, len(candles) - 1 - self.config.pullbackLookbackCandles)
        for index in range(start, len(candles) - 1):
            candle = candles[index]
            low = float(candle["low"])
            close = float(candle["close"])
            if low <= session_vwap + tolerance and close >= session_vwap - tolerance:
                return index
        return None

    def _sell_pullback_index(self, candles: list[dict[str, Any]], session_vwap: float, atr: float) -> int | None:
        tolerance = atr * self.config.pullbackAtrTolerance
        start = max(0, len(candles) - 1 - self.config.pullbackLookbackCandles)
        for index in range(start, len(candles) - 1):
            candle = candles[index]
            high = float(candle["high"])
            close = float(candle["close"])
            if high >= session_vwap - tolerance and close <= session_vwap + tolerance:
                return index
        return None

    def _buy_confirmation(self, latest: dict[str, Any], previous: dict[str, Any], session_vwap: float, atr: float) -> bool:
        close = float(latest["close"])
        open_price = float(latest["open"])
        previous_high = float(previous["high"])
        return close > open_price and close >= session_vwap + (atr * self.config.reclaimAtrThreshold) and close >= previous_high

    def _sell_confirmation(self, latest: dict[str, Any], previous: dict[str, Any], session_vwap: float, atr: float) -> bool:
        close = float(latest["close"])
        open_price = float(latest["open"])
        previous_low = float(previous["low"])
        return close < open_price and close <= session_vwap - (atr * self.config.rejectionAtrThreshold) and close <= previous_low

    def _volume_ratio(self, candles: list[dict[str, Any]]) -> float | None:
        lookback = candles[-self.config.volumeLookbackCandles - 1 : -1]
        if len(lookback) < self.config.volumeLookbackCandles:
            return None
        baseline = mean(float(candle["volume"]) for candle in lookback)
        if baseline <= 0:
            return None
        return float(candles[-1]["volume"]) / baseline

    def _confidence(self, evidence: VwapContinuationEvidence) -> float:
        slope_score = min(1.0, abs(evidence.vwapSlope) / max(self.config.minVwapSlope * 3, 0.000001))
        distance_score = max(0.0, 1.0 - (evidence.distanceAtr / self.config.maxEntryDistanceAtr))
        volume_score = 0.0 if evidence.volumeRatio is None else min(1.0, evidence.volumeRatio / max(self.config.minConfirmationVolumeRatio * 1.5, 0.01))
        structure_score = 1.0 if evidence.structureAligned else 0.0
        confidence = (0.25 * slope_score) + (0.25 * distance_score) + (0.25 * volume_score) + (0.25 * structure_score)
        return round(max(0.0, min(1.0, confidence)), 4)

    def _hold_confidence(self, evidence: VwapContinuationEvidence) -> float:
        partials = [
            abs(evidence.vwapSlope) >= self.config.minVwapSlope,
            evidence.emaAligned,
            evidence.emaSlopeAligned,
            evidence.structureAligned,
            evidence.pullbackDetected,
            evidence.confirmationDetected,
            evidence.volumeRatio is not None and evidence.volumeRatio >= self.config.minConfirmationVolumeRatio,
            evidence.distanceAtr <= self.config.maxEntryDistanceAtr,
        ]
        return round(min(0.44, max(0.05, sum(1 for item in partials if item) / len(partials) * 0.44)), 4)

    def _regime_fit(self, evidence: VwapContinuationEvidence) -> float:
        slope_fit = min(1.0, abs(evidence.vwapSlope) / max(self.config.minVwapSlope * 2, 0.000001))
        distance_fit = max(0.0, 1.0 - (evidence.distanceAtr / self.config.extendedEntryDistanceAtr))
        return round(max(0.0, min(1.0, (0.6 * slope_fit) + (0.4 * distance_fit))), 4)

    def _reliability(self, evidence: VwapContinuationEvidence) -> float:
        volume_score = 0.5 if evidence.volumeRatio is None else min(1.0, evidence.volumeRatio / max(self.config.minConfirmationVolumeRatio, 0.01))
        setup_score = sum(
            1
            for value in (
                evidence.emaAligned,
                evidence.emaSlopeAligned,
                evidence.structureAligned,
                evidence.pullbackDetected,
                evidence.confirmationDetected,
            )
            if value
        ) / 5
        return round(max(0.0, min(1.0, (0.55 * setup_score) + (0.45 * volume_score))), 4)

    def _explanation(self, evidence: VwapContinuationEvidence) -> str:
        action = "reclaim" if evidence.signal == Signal.BUY else "rejection"
        return (
            f"{evidence.signal.value} VWAP trend continuation: VWAP slope {evidence.vwapSlope:.5f}, "
            f"{action} confirmed after pullback, distance {evidence.distanceAtr:.2f} ATR."
        )

    def _hold_reason_codes(self, buy: VwapContinuationEvidence, sell: VwapContinuationEvidence) -> list[str]:
        if abs(buy.vwapSlope) < self.config.minVwapSlope and abs(sell.vwapSlope) < self.config.minVwapSlope:
            return ["vwap_continuation.flat_vwap"]
        if not buy.pullbackDetected and not sell.pullbackDetected:
            return ["vwap_continuation.no_pullback"]
        if buy.distanceAtr > self.config.maxEntryDistanceAtr or sell.distanceAtr > self.config.maxEntryDistanceAtr:
            return ["vwap_continuation.entry_extended"]
        if not buy.confirmationDetected and not sell.confirmationDetected:
            return ["vwap_continuation.no_confirmation"]
        return ["vwap_continuation.weak_or_conflicting_evidence"]

    def _hold_explanation(self, buy: VwapContinuationEvidence, sell: VwapContinuationEvidence) -> str:
        reason = self._hold_reason_codes(buy, sell)[0].removeprefix("vwap_continuation.").replace("_", " ")
        return (
            f"HOLD because VWAP continuation evidence is incomplete: {reason}; "
            f"buy distance {buy.distanceAtr:.2f} ATR, sell distance {sell.distanceAtr:.2f} ATR."
        )


def _empty_evidence(direction: Signal) -> VwapContinuationEvidence:
    return VwapContinuationEvidence(
        signal=Signal.HOLD,
        latestClose=0.0,
        sessionVwap=0.0,
        atr=0.0,
        distanceAtr=999.0,
        vwapSlope=0.0,
        emaAligned=False,
        emaSlopeAligned=False,
        structureAligned=False,
        pullbackDetected=False,
        confirmationDetected=False,
        volumeRatio=None,
        pullbackIndex=None,
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
