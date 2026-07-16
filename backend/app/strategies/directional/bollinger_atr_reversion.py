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


class BollingerAtrReversionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "bollinger_atr_reversion_v1"
    extensionLookbackCandles: int = Field(default=5, ge=2, le=20)
    momentumLookbackCandles: int = Field(default=4, ge=2, le=10)
    minBandExtensionAtr: float = Field(default=0.18, ge=0)
    minEquilibriumDistanceAtr: float = Field(default=1.05, gt=0)
    reentryBufferAtr: float = Field(default=0.02, ge=0)
    maxAdxForReversion: float = Field(default=28.0, ge=0, le=100)
    cautionAdx: float = Field(default=22.0, ge=0, le=100)
    maxBandWidthPercentile: float = Field(default=0.72, ge=0, le=1)
    trendExpansionBandWidthPercentile: float = Field(default=0.86, ge=0, le=1)
    bandWalkMinOutsideCloses: int = Field(default=3, ge=2, le=10)
    minWickToRangeRatio: float = Field(default=0.22, ge=0, le=1)
    minDecelerationRatio: float = Field(default=0.25, ge=0, le=1)
    maxContinuationVolumeRatio: float = Field(default=1.9, gt=0)

    @model_validator(mode="after")
    def thresholds_must_be_ordered(self) -> BollingerAtrReversionConfig:
        if self.trendExpansionBandWidthPercentile < self.maxBandWidthPercentile:
            raise ValueError("trendExpansionBandWidthPercentile must be greater than or equal to maxBandWidthPercentile")
        return self

    @property
    def configurationHash(self) -> str:
        payload = self.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class BollingerAtrReversionEvidence:
    signal: Signal
    candidateSignal: Signal
    bandExtensionAtr: float
    equilibriumDistanceAtr: float
    bandWidthPercentile: float | None
    adx: float | None
    reenteredBand: bool
    outsideCloseCount: int
    rejectionDetected: bool
    decelerationDetected: bool
    regimeSuitable: bool
    sustainedTrendExpansion: bool
    volumeBehaviorOk: bool
    volumeRatio: float | None
    structuralInvalidationPrice: float | None
    targetReferencePrice: float | None


class BollingerAtrReversionStrategy:
    registryEntry = resolve_strategy("bollinger_atr_reversion")

    def __init__(self, config: BollingerAtrReversionConfig | None = None) -> None:
        self.config = config or BollingerAtrReversionConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> StrategySignal:
        required_features = self.required_feature_names()
        if not context.featureSnapshot.dataReady:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Feature snapshot is not ready for Bollinger/ATR reversion.",
            )
        if not required_features_ready(context.featureSnapshot, required_features):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Required feature measurements are unavailable for Bollinger/ATR reversion.",
            )

        candles = _session_candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [], context)
        minimum = max(self.config.extensionLookbackCandles + 1, self.config.momentumLookbackCandles + 2)
        if len(candles) < minimum:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Insufficient completed 1-minute candles for Bollinger/ATR reversion.",
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
                    "bollinger_atr_reversion.confirmed",
                    f"direction:{evidence.signal.value.lower()}",
                    f"extension_atr:{evidence.bandExtensionAtr:.2f}",
                ],
                explanation=self._explanation(evidence),
                featureNames=required_features,
                structuralInvalidationPrice=evidence.structuralInvalidationPrice,
            )

        return hold_signal(
            context,
            confidence=self._hold_confidence(evidence),
            setupDetected=evidence.candidateSignal != Signal.HOLD and evidence.bandExtensionAtr >= self.config.minBandExtensionAtr,
            regimeFit=self._regime_fit(evidence),
            reliability=self._reliability(evidence),
            reasonCodes=self._hold_reason_codes(evidence),
            explanation=self._hold_explanation(evidence),
            featureNames=required_features,
            structuralInvalidationPrice=evidence.structuralInvalidationPrice,
        )

    def required_feature_names(self) -> tuple[str, ...]:
        return (
            "spy1mBollingerBands",
            "spy1mBollingerWidthPercentile",
            "spy1mAtr14",
            "spy1mAdx14",
            "distanceFromEma20Atr",
            "spy1mRelativeVolume",
            "spy1mRollingHigh20",
            "spy1mRollingLow20",
        )

    def _evidence(self, context: StrategyEvaluationContext, candles: list[dict[str, Any]]) -> BollingerAtrReversionEvidence:
        features = context.featureSnapshot.features
        bands = _bands(features["spy1mBollingerBands"].value)
        atr = _number(features["spy1mAtr14"].value)
        adx = _number(features["spy1mAdx14"].value)
        width_percentile = _number(features["spy1mBollingerWidthPercentile"].value)
        equilibrium_distance_atr = _number(features["distanceFromEma20Atr"].value)
        relative_volume = _number(features["spy1mRelativeVolume"].value)
        rolling_high = _number(features["spy1mRollingHigh20"].value)
        rolling_low = _number(features["spy1mRollingLow20"].value)
        if bands is None or None in {atr, adx, width_percentile, equilibrium_distance_atr, relative_volume}:
            return _empty_evidence()

        assert atr is not None
        assert adx is not None
        assert width_percentile is not None
        assert equilibrium_distance_atr is not None
        assert relative_volume is not None

        latest = candles[-1]
        recent = candles[-self.config.extensionLookbackCandles - 1 :]
        upper = bands["upper"]
        middle = bands["middle"]
        lower = bands["lower"]
        buffer_dollars = atr * self.config.reentryBufferAtr

        lower_extension = max((lower - float(candle["low"]) for candle in recent), default=0.0)
        upper_extension = max((float(candle["high"]) - upper for candle in recent), default=0.0)
        latest_close = float(latest["close"])

        if lower_extension >= upper_extension and lower_extension > 0:
            candidate = Signal.BUY
            band_extension_atr = lower_extension / atr if atr else 0.0
            reentered = latest_close >= lower + buffer_dollars
            outside_closes = sum(1 for candle in recent if float(candle["close"]) < lower)
            rejection = _lower_rejection(latest, self.config.minWickToRangeRatio)
            deceleration = _downside_deceleration(candles, self.config.momentumLookbackCandles, self.config.minDecelerationRatio)
            invalidation = rolling_low if rolling_low is not None else min(float(candle["low"]) for candle in recent)
        elif upper_extension > 0:
            candidate = Signal.SELL
            band_extension_atr = upper_extension / atr if atr else 0.0
            reentered = latest_close <= upper - buffer_dollars
            outside_closes = sum(1 for candle in recent if float(candle["close"]) > upper)
            rejection = _upper_rejection(latest, self.config.minWickToRangeRatio)
            deceleration = _upside_deceleration(candles, self.config.momentumLookbackCandles, self.config.minDecelerationRatio)
            invalidation = rolling_high if rolling_high is not None else max(float(candle["high"]) for candle in recent)
        else:
            candidate = Signal.HOLD
            band_extension_atr = 0.0
            reentered = False
            outside_closes = 0
            rejection = False
            deceleration = False
            invalidation = None

        sustained_trend = (
            candidate != Signal.HOLD
            and (
                adx > self.config.maxAdxForReversion
                or (
                    width_percentile >= self.config.trendExpansionBandWidthPercentile
                    and outside_closes >= self.config.bandWalkMinOutsideCloses
                )
            )
        )
        regime_suitable = (
            candidate != Signal.HOLD
            and adx <= self.config.maxAdxForReversion
            and width_percentile <= self.config.maxBandWidthPercentile
        )
        volume_ok = relative_volume <= self.config.maxContinuationVolumeRatio
        enough_equilibrium_distance = abs(equilibrium_distance_atr) >= self.config.minEquilibriumDistanceAtr
        correct_side_distance = (
            candidate == Signal.BUY
            and equilibrium_distance_atr <= -self.config.minEquilibriumDistanceAtr
        ) or (
            candidate == Signal.SELL
            and equilibrium_distance_atr >= self.config.minEquilibriumDistanceAtr
        )
        should_signal = all(
            [
                candidate in {Signal.BUY, Signal.SELL},
                band_extension_atr >= self.config.minBandExtensionAtr,
                enough_equilibrium_distance,
                correct_side_distance,
                reentered,
                rejection or deceleration,
                regime_suitable,
                not sustained_trend,
                volume_ok,
            ]
        )

        return BollingerAtrReversionEvidence(
            signal=candidate if should_signal else Signal.HOLD,
            candidateSignal=candidate,
            bandExtensionAtr=band_extension_atr,
            equilibriumDistanceAtr=equilibrium_distance_atr,
            bandWidthPercentile=width_percentile,
            adx=adx,
            reenteredBand=reentered,
            outsideCloseCount=outside_closes,
            rejectionDetected=rejection,
            decelerationDetected=deceleration,
            regimeSuitable=regime_suitable,
            sustainedTrendExpansion=sustained_trend,
            volumeBehaviorOk=volume_ok,
            volumeRatio=relative_volume,
            structuralInvalidationPrice=round(invalidation, 4) if invalidation is not None else None,
            targetReferencePrice=round(middle, 4),
        )

    def _confidence(self, evidence: BollingerAtrReversionEvidence) -> float:
        extension_score = min(1.0, evidence.bandExtensionAtr / max(self.config.minBandExtensionAtr * 3, 0.01))
        distance_score = min(1.0, abs(evidence.equilibriumDistanceAtr) / max(self.config.minEquilibriumDistanceAtr * 2, 0.01))
        momentum_score = 1.0 if evidence.rejectionDetected and evidence.decelerationDetected else 0.7 if evidence.rejectionDetected or evidence.decelerationDetected else 0.0
        regime_score = self._regime_fit(evidence)
        confidence = (0.28 * extension_score) + (0.27 * distance_score) + (0.25 * momentum_score) + (0.2 * regime_score)
        return round(max(0.0, min(1.0, confidence)), 4)

    def _hold_confidence(self, evidence: BollingerAtrReversionEvidence) -> float:
        partials = [
            evidence.bandExtensionAtr >= self.config.minBandExtensionAtr,
            abs(evidence.equilibriumDistanceAtr) >= self.config.minEquilibriumDistanceAtr,
            evidence.reenteredBand,
            evidence.rejectionDetected or evidence.decelerationDetected,
            evidence.regimeSuitable,
            evidence.volumeBehaviorOk,
            not evidence.sustainedTrendExpansion,
        ]
        return round(min(0.44, max(0.05, sum(1 for item in partials if item) / len(partials) * 0.44)), 4)

    def _regime_fit(self, evidence: BollingerAtrReversionEvidence) -> float:
        if evidence.adx is None or evidence.bandWidthPercentile is None:
            return 0.0
        adx_score = max(0.0, 1.0 - max(0.0, evidence.adx - self.config.cautionAdx) / max(self.config.maxAdxForReversion - self.config.cautionAdx, 0.01))
        width_score = max(0.0, 1.0 - evidence.bandWidthPercentile / max(self.config.maxBandWidthPercentile, 0.01))
        return round(max(0.0, min(1.0, (0.6 * adx_score) + (0.4 * width_score))), 4)

    def _reliability(self, evidence: BollingerAtrReversionEvidence) -> float:
        checks = [
            evidence.candidateSignal != Signal.HOLD,
            evidence.reenteredBand,
            evidence.rejectionDetected,
            evidence.decelerationDetected,
            evidence.regimeSuitable,
            evidence.volumeBehaviorOk,
            not evidence.sustainedTrendExpansion,
        ]
        return round(max(0.0, min(1.0, sum(1 for item in checks if item) / len(checks))), 4)

    def _explanation(self, evidence: BollingerAtrReversionEvidence) -> str:
        target = f"{evidence.targetReferencePrice:.2f}" if evidence.targetReferencePrice is not None else "middle band"
        return (
            f"{evidence.signal.value} Bollinger/ATR reversion: band extension {evidence.bandExtensionAtr:.2f} ATR, "
            f"distance from equilibrium {evidence.equilibriumDistanceAtr:.2f} ATR, re-entry confirmed toward {target}."
        )

    def _hold_reason_codes(self, evidence: BollingerAtrReversionEvidence) -> list[str]:
        if evidence.sustainedTrendExpansion:
            return ["bollinger_atr_reversion.sustained_trend_expansion"]
        if evidence.bandExtensionAtr < self.config.minBandExtensionAtr:
            return ["bollinger_atr_reversion.no_band_extension"]
        if abs(evidence.equilibriumDistanceAtr) < self.config.minEquilibriumDistanceAtr:
            return ["bollinger_atr_reversion.atr_distance_insufficient"]
        if not evidence.reenteredBand:
            return ["bollinger_atr_reversion.no_band_reentry"]
        if not evidence.rejectionDetected and not evidence.decelerationDetected:
            return ["bollinger_atr_reversion.no_momentum_deceleration"]
        if not evidence.regimeSuitable:
            return ["bollinger_atr_reversion.regime_unsuitable"]
        if not evidence.volumeBehaviorOk:
            return ["bollinger_atr_reversion.continuation_volume_behavior"]
        return ["bollinger_atr_reversion.weak_or_conflicting_evidence"]

    def _hold_explanation(self, evidence: BollingerAtrReversionEvidence) -> str:
        reason = self._hold_reason_codes(evidence)[0].removeprefix("bollinger_atr_reversion.").replace("_", " ")
        return (
            f"HOLD because Bollinger/ATR reversion evidence is incomplete: {reason}; "
            f"extension {evidence.bandExtensionAtr:.2f} ATR, equilibrium distance {evidence.equilibriumDistanceAtr:.2f} ATR."
        )


def _empty_evidence() -> BollingerAtrReversionEvidence:
    return BollingerAtrReversionEvidence(
        signal=Signal.HOLD,
        candidateSignal=Signal.HOLD,
        bandExtensionAtr=0.0,
        equilibriumDistanceAtr=0.0,
        bandWidthPercentile=None,
        adx=None,
        reenteredBand=False,
        outsideCloseCount=0,
        rejectionDetected=False,
        decelerationDetected=False,
        regimeSuitable=False,
        sustainedTrendExpansion=False,
        volumeBehaviorOk=False,
        volumeRatio=None,
        structuralInvalidationPrice=None,
        targetReferencePrice=None,
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


def _bands(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        upper = float(value["upper"])
        middle = float(value["middle"])
        lower = float(value["lower"])
    except (KeyError, TypeError, ValueError):
        return None
    if not lower < middle < upper:
        return None
    return {"upper": upper, "middle": middle, "lower": lower}


def _lower_rejection(candle: dict[str, Any], min_wick_ratio: float) -> bool:
    open_price = float(candle["open"])
    close = float(candle["close"])
    high = float(candle["high"])
    low = float(candle["low"])
    candle_range = high - low
    if candle_range <= 0:
        return False
    lower_wick = min(open_price, close) - low
    return close >= open_price and lower_wick / candle_range >= min_wick_ratio


def _upper_rejection(candle: dict[str, Any], min_wick_ratio: float) -> bool:
    open_price = float(candle["open"])
    close = float(candle["close"])
    high = float(candle["high"])
    low = float(candle["low"])
    candle_range = high - low
    if candle_range <= 0:
        return False
    upper_wick = high - max(open_price, close)
    return close <= open_price and upper_wick / candle_range >= min_wick_ratio


def _downside_deceleration(candles: list[dict[str, Any]], lookback: int, min_ratio: float) -> bool:
    closes = [float(candle["close"]) for candle in candles[-lookback - 1 :]]
    if len(closes) < lookback + 1:
        return False
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    negative_moves = [abs(change) for change in changes[:-1] if change < 0]
    if not negative_moves:
        return changes[-1] > 0
    baseline = mean(negative_moves)
    return changes[-1] > 0 or (changes[-1] < 0 and abs(changes[-1]) <= baseline * (1 - min_ratio))


def _upside_deceleration(candles: list[dict[str, Any]], lookback: int, min_ratio: float) -> bool:
    closes = [float(candle["close"]) for candle in candles[-lookback - 1 :]]
    if len(closes) < lookback + 1:
        return False
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    positive_moves = [change for change in changes[:-1] if change > 0]
    if not positive_moves:
        return changes[-1] < 0
    baseline = mean(positive_moves)
    return changes[-1] < 0 or (changes[-1] > 0 and changes[-1] <= baseline * (1 - min_ratio))


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
