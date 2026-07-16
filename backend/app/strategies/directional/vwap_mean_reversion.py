from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from statistics import mean, pstdev
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


class VwapMeanReversionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "vwap_mean_reversion_v1"
    zScoreLookbackCandles: int = Field(default=24, ge=10, le=80)
    momentumLookbackCandles: int = Field(default=4, ge=2, le=10)
    minDistanceFromVwapAtr: float = Field(default=1.2, gt=0)
    minAbsDeviationZScore: float = Field(default=1.25, gt=0)
    maxAdxForEntry: float = Field(default=27.0, ge=0, le=100)
    cautionAdx: float = Field(default=22.0, ge=0, le=100)
    maxAbsVwapSlope: float = Field(default=0.00035, ge=0)
    strongTrendVwapSlope: float = Field(default=0.0008, ge=0)
    minWickToRangeRatio: float = Field(default=0.28, ge=0, le=1)
    minBodyDecelerationRatio: float = Field(default=0.3, ge=0, le=1)
    maxContinuationVolumeRatio: float = Field(default=1.8, gt=0)
    minVolumeBehaviorScore: float = Field(default=0.45, ge=0, le=1)
    targetAtrFractionTowardVwap: float = Field(default=0.55, gt=0, le=1)

    @model_validator(mode="after")
    def trend_thresholds_must_be_ordered(self) -> VwapMeanReversionConfig:
        if self.strongTrendVwapSlope < self.maxAbsVwapSlope:
            raise ValueError("strongTrendVwapSlope must be greater than or equal to maxAbsVwapSlope")
        return self

    @property
    def configurationHash(self) -> str:
        payload = self.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class VwapMeanReversionEvidence:
    signal: Signal
    distanceAtr: float
    deviationZScore: float | None
    adx: float | None
    vwapSlope: float | None
    overextended: bool
    regimeAcceptsReversion: bool
    strongTrendSuppressed: bool
    rejectionDetected: bool
    decelerationDetected: bool
    volumeBehaviorOk: bool
    volumeBehaviorScore: float
    latestVolumeRatio: float | None
    targetReferencePrice: float | None
    structuralInvalidationPrice: float | None


class VwapMeanReversionStrategy:
    registryEntry = resolve_strategy("vwap_mean_reversion")

    def __init__(self, config: VwapMeanReversionConfig | None = None) -> None:
        self.config = config or VwapMeanReversionConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> StrategySignal:
        required_features = self.required_feature_names()
        if not context.featureSnapshot.dataReady:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Feature snapshot is not ready for VWAP mean reversion.",
            )
        if not required_features_ready(context.featureSnapshot, required_features):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Required feature measurements are unavailable for VWAP mean reversion.",
            )

        candles = _session_candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [], context)
        minimum = max(self.config.zScoreLookbackCandles + 1, self.config.momentumLookbackCandles + 2)
        if len(candles) < minimum:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Insufficient completed 1-minute candles for VWAP mean reversion.",
            )

        evidence = self._evidence(context, candles)
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
                    "vwap_mean_reversion.confirmed",
                    f"direction:{evidence.signal.value.lower()}",
                    f"distance_atr:{abs(evidence.distanceAtr):.2f}",
                    f"z_score:{evidence.deviationZScore or 0:.2f}",
                ],
                explanation=self._explanation(evidence),
                featureNames=required_features,
                structuralInvalidationPrice=evidence.structuralInvalidationPrice,
            )

        return hold_signal(
            context,
            confidence=self._hold_confidence(evidence),
            setupDetected=evidence.overextended,
            regimeFit=self._regime_fit(evidence),
            reliability=self._reliability(evidence),
            reasonCodes=self._hold_reason_codes(evidence),
            explanation=self._hold_explanation(evidence),
            featureNames=required_features,
            structuralInvalidationPrice=evidence.structuralInvalidationPrice,
        )

    def required_feature_names(self) -> tuple[str, ...]:
        return (
            "sessionVwap",
            "sessionVwapSlope",
            "distanceFromVwapAtr",
            "spy1mAtr14",
            "spy1mAdx14",
            "spy1mRelativeVolume",
            "spy1mRollingHigh20",
            "spy1mRollingLow20",
        )

    def _evidence(self, context: StrategyEvaluationContext, candles: list[dict[str, Any]]) -> VwapMeanReversionEvidence:
        features = context.featureSnapshot.features
        latest = candles[-1]
        latest_close = float(latest["close"])
        session_vwap = _number(features["sessionVwap"].value)
        vwap_slope = _number(features["sessionVwapSlope"].value)
        distance_atr = _number(features["distanceFromVwapAtr"].value)
        atr = _number(features["spy1mAtr14"].value)
        adx = _number(features["spy1mAdx14"].value)
        relative_volume = _number(features["spy1mRelativeVolume"].value)
        rolling_high = _number(features["spy1mRollingHigh20"].value)
        rolling_low = _number(features["spy1mRollingLow20"].value)
        if None in {session_vwap, vwap_slope, distance_atr, atr, adx, relative_volume}:
            return _empty_evidence()

        assert session_vwap is not None
        assert vwap_slope is not None
        assert distance_atr is not None
        assert atr is not None
        assert adx is not None
        assert relative_volume is not None

        deviation_z_score = _vwap_deviation_z_score(candles, self.config.zScoreLookbackCandles)
        if distance_atr <= -self.config.minDistanceFromVwapAtr:
            candidate_signal = Signal.BUY
            z_score_ok = deviation_z_score is not None and deviation_z_score <= -self.config.minAbsDeviationZScore
            rejection = _lower_rejection(latest, self.config.minWickToRangeRatio)
            deceleration = _downside_deceleration(candles, self.config.momentumLookbackCandles, self.config.minBodyDecelerationRatio)
            invalidation = rolling_low if rolling_low is not None else float(latest["low"])
        elif distance_atr >= self.config.minDistanceFromVwapAtr:
            candidate_signal = Signal.SELL
            z_score_ok = deviation_z_score is not None and deviation_z_score >= self.config.minAbsDeviationZScore
            rejection = _upper_rejection(latest, self.config.minWickToRangeRatio)
            deceleration = _upside_deceleration(candles, self.config.momentumLookbackCandles, self.config.minBodyDecelerationRatio)
            invalidation = rolling_high if rolling_high is not None else float(latest["high"])
        else:
            candidate_signal = Signal.HOLD
            z_score_ok = False
            rejection = False
            deceleration = False
            invalidation = None

        slope_continuation = (
            candidate_signal == Signal.BUY
            and vwap_slope <= -self.config.strongTrendVwapSlope
        ) or (
            candidate_signal == Signal.SELL
            and vwap_slope >= self.config.strongTrendVwapSlope
        )
        high_adx_continuation = adx > self.config.maxAdxForEntry
        strong_trend_suppressed = bool(candidate_signal != Signal.HOLD and (high_adx_continuation or slope_continuation))
        regime_accepts = (
            candidate_signal != Signal.HOLD
            and adx <= self.config.maxAdxForEntry
            and abs(vwap_slope) <= self.config.maxAbsVwapSlope
        )
        volume_score = _volume_behavior_score(candles, relative_volume, self.config.maxContinuationVolumeRatio)
        volume_ok = volume_score >= self.config.minVolumeBehaviorScore
        overextended = candidate_signal != Signal.HOLD and z_score_ok
        losing_momentum = rejection or deceleration
        should_signal = all(
            [
                candidate_signal in {Signal.BUY, Signal.SELL},
                overextended,
                regime_accepts,
                not strong_trend_suppressed,
                losing_momentum,
                volume_ok,
            ]
        )
        target = _partial_target(latest_close, session_vwap, atr, self.config.targetAtrFractionTowardVwap)

        return VwapMeanReversionEvidence(
            signal=candidate_signal if should_signal else Signal.HOLD,
            distanceAtr=distance_atr,
            deviationZScore=deviation_z_score,
            adx=adx,
            vwapSlope=vwap_slope,
            overextended=overextended,
            regimeAcceptsReversion=regime_accepts,
            strongTrendSuppressed=strong_trend_suppressed,
            rejectionDetected=rejection,
            decelerationDetected=deceleration,
            volumeBehaviorOk=volume_ok,
            volumeBehaviorScore=volume_score,
            latestVolumeRatio=relative_volume,
            targetReferencePrice=target,
            structuralInvalidationPrice=round(invalidation, 4) if invalidation is not None else None,
        )

    def _confidence(self, evidence: VwapMeanReversionEvidence) -> float:
        distance_score = min(1.0, abs(evidence.distanceAtr) / max(self.config.minDistanceFromVwapAtr * 2.0, 0.01))
        z_score = 0.0 if evidence.deviationZScore is None else min(1.0, abs(evidence.deviationZScore) / max(self.config.minAbsDeviationZScore * 2.0, 0.01))
        regime_score = self._regime_fit(evidence)
        momentum_score = 1.0 if evidence.rejectionDetected and evidence.decelerationDetected else 0.72 if evidence.rejectionDetected or evidence.decelerationDetected else 0.0
        confidence = (0.28 * distance_score) + (0.27 * z_score) + (0.25 * regime_score) + (0.2 * momentum_score)
        return round(max(0.0, min(1.0, confidence)), 4)

    def _hold_confidence(self, evidence: VwapMeanReversionEvidence) -> float:
        partials = [
            evidence.overextended,
            evidence.regimeAcceptsReversion,
            evidence.rejectionDetected or evidence.decelerationDetected,
            evidence.volumeBehaviorOk,
            not evidence.strongTrendSuppressed,
        ]
        return round(min(0.44, max(0.05, sum(1 for item in partials if item) / len(partials) * 0.44)), 4)

    def _regime_fit(self, evidence: VwapMeanReversionEvidence) -> float:
        if evidence.adx is None or evidence.vwapSlope is None:
            return 0.0
        adx_score = max(0.0, 1.0 - max(0.0, evidence.adx - self.config.cautionAdx) / max(self.config.maxAdxForEntry - self.config.cautionAdx, 0.01))
        slope_score = max(0.0, 1.0 - abs(evidence.vwapSlope) / max(self.config.maxAbsVwapSlope, 0.000001))
        return round(max(0.0, min(1.0, (0.6 * adx_score) + (0.4 * slope_score))), 4)

    def _reliability(self, evidence: VwapMeanReversionEvidence) -> float:
        checks = [
            evidence.overextended,
            evidence.regimeAcceptsReversion,
            evidence.rejectionDetected,
            evidence.decelerationDetected,
            evidence.volumeBehaviorOk,
            not evidence.strongTrendSuppressed,
        ]
        return round(max(0.0, min(1.0, sum(1 for item in checks if item) / len(checks))), 4)

    def _explanation(self, evidence: VwapMeanReversionEvidence) -> str:
        target = f"{evidence.targetReferencePrice:.2f}" if evidence.targetReferencePrice is not None else "VWAP"
        return (
            f"{evidence.signal.value} VWAP mean reversion: distance {evidence.distanceAtr:.2f} ATR, "
            f"z-score {evidence.deviationZScore or 0:.2f}, weak-trend regime accepted, target reference {target}."
        )

    def _hold_reason_codes(self, evidence: VwapMeanReversionEvidence) -> list[str]:
        if evidence.strongTrendSuppressed:
            return ["vwap_mean_reversion.strong_trend_suppressed"]
        if abs(evidence.distanceAtr) < self.config.minDistanceFromVwapAtr:
            return ["vwap_mean_reversion.distance_insufficient"]
        if evidence.deviationZScore is None or abs(evidence.deviationZScore) < self.config.minAbsDeviationZScore:
            return ["vwap_mean_reversion.z_score_insufficient"]
        if not evidence.regimeAcceptsReversion:
            return ["vwap_mean_reversion.regime_not_range_or_weak_trend"]
        if not evidence.rejectionDetected and not evidence.decelerationDetected:
            return ["vwap_mean_reversion.no_momentum_loss"]
        if not evidence.volumeBehaviorOk:
            return ["vwap_mean_reversion.continuation_volume_behavior"]
        return ["vwap_mean_reversion.weak_or_conflicting_evidence"]

    def _hold_explanation(self, evidence: VwapMeanReversionEvidence) -> str:
        reason = self._hold_reason_codes(evidence)[0].removeprefix("vwap_mean_reversion.").replace("_", " ")
        return (
            f"HOLD because VWAP mean-reversion evidence is incomplete: {reason}; "
            f"distance {evidence.distanceAtr:.2f} ATR, z-score {evidence.deviationZScore or 0:.2f}."
        )


def _empty_evidence() -> VwapMeanReversionEvidence:
    return VwapMeanReversionEvidence(
        signal=Signal.HOLD,
        distanceAtr=0.0,
        deviationZScore=None,
        adx=None,
        vwapSlope=None,
        overextended=False,
        regimeAcceptsReversion=False,
        strongTrendSuppressed=False,
        rejectionDetected=False,
        decelerationDetected=False,
        volumeBehaviorOk=False,
        volumeBehaviorScore=0.0,
        latestVolumeRatio=None,
        targetReferencePrice=None,
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


def _vwap_deviation_z_score(candles: list[dict[str, Any]], lookback: int) -> float | None:
    if len(candles) < lookback + 1:
        return None
    vwap_series = _running_vwap_series(candles)
    deviations = [float(candle["close"]) - vwap for candle, vwap in zip(candles, vwap_series, strict=True)]
    sample = deviations[-lookback - 1 : -1]
    if len(sample) < lookback:
        return None
    deviation_std = pstdev(sample)
    if deviation_std <= 0:
        return None
    return (deviations[-1] - mean(sample)) / deviation_std


def _running_vwap_series(candles: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    volume_sum = 0.0
    price_volume_sum = 0.0
    for candle in candles:
        volume = float(candle["volume"])
        typical = (float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3
        volume_sum += volume
        price_volume_sum += typical * volume
        values.append(price_volume_sum / volume_sum if volume_sum else typical)
    return values


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


def _volume_behavior_score(candles: list[dict[str, Any]], relative_volume: float, max_continuation_volume_ratio: float) -> float:
    recent = candles[-4:-1]
    prior_mean = mean(float(candle["volume"]) for candle in recent) if recent else 0.0
    latest_volume = float(candles[-1]["volume"])
    local_ratio = latest_volume / prior_mean if prior_mean else relative_volume
    controlled_relative_volume = max(0.0, 1.0 - max(0.0, relative_volume - 1.0) / max(max_continuation_volume_ratio - 1.0, 0.01))
    local_exhaustion = 1.0 if latest_volume <= prior_mean else max(0.0, 1.0 - max(0.0, local_ratio - 1.0) / max(max_continuation_volume_ratio - 1.0, 0.01))
    return round(max(0.0, min(1.0, (0.55 * controlled_relative_volume) + (0.45 * local_exhaustion))), 4)


def _partial_target(latest_close: float, session_vwap: float, atr: float, fraction: float) -> float:
    distance = session_vwap - latest_close
    step = min(abs(distance), atr * fraction)
    return round(latest_close + (step if distance > 0 else -step), 4)


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
