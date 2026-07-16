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


class VolatilityBreakoutConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "volatility_breakout_v1"
    rollingLevelLookbackCandles: int = Field(default=20, ge=5, le=120)
    compressionLookbackCandles: int = Field(default=20, ge=5, le=120)
    maxBollingerWidthPercentile: float = Field(default=0.45, ge=0, le=1)
    maxPriorRangeAtrMultiple: float = Field(default=2.8, ge=0)
    minTrueRangeExpansionRatio: float = Field(default=1.35, ge=0)
    minLatestTrueRangeAtrMultiple: float = Field(default=1.1, ge=0)
    minRealizedVolatilityPercentile: float = Field(default=0.55, ge=0, le=1)
    minVolumeExpansionRatio: float = Field(default=1.5, ge=0)
    minBreakoutBufferDollars: float = Field(default=0.01, ge=0)
    atrBufferMultiplier: float = Field(default=0.12, ge=0)
    spreadBufferMultiplier: float = Field(default=1.0, ge=0)
    maxSpreadBasisPoints: float = Field(default=12.0, gt=0)
    minLatestVolume: float = Field(default=50_000, ge=0)
    minTradeCount: int = Field(default=100, ge=0)
    minBodyToRangeRatio: float = Field(default=0.45, ge=0, le=1)
    minCloseLocationRatio: float = Field(default=0.68, ge=0, le=1)

    @model_validator(mode="after")
    def lookbacks_must_be_compatible(self) -> VolatilityBreakoutConfig:
        if self.compressionLookbackCandles > self.rollingLevelLookbackCandles * 4:
            raise ValueError("compressionLookbackCandles is unexpectedly large versus rollingLevelLookbackCandles")
        return self

    @property
    def configurationHash(self) -> str:
        payload = self.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class VolatilityBreakoutEvidence:
    signal: Signal
    setupId: str | None
    levelBreak: bool
    volatilityExpansion: bool
    compressionDetected: bool
    volumeExpansion: bool
    candleQuality: bool
    liquidityOk: bool
    bufferDollars: float
    level: float | None
    closeDistance: float
    trueRangeExpansionRatio: float | None
    volumeRatio: float | None
    bollingerWidthPercentile: float | None
    realizedVolatilityPercentile: float | None
    spreadBasisPoints: float | None
    breakoutTimestamp: datetime | None
    structuralInvalidationPrice: float | None


class VolatilityBreakoutStrategy:
    registryEntry = resolve_strategy("volatility_breakout")

    def __init__(self, config: VolatilityBreakoutConfig | None = None) -> None:
        self.config = config or VolatilityBreakoutConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> StrategySignal:
        required_features = self.required_feature_names()
        if not context.featureSnapshot.dataReady:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Feature snapshot is not ready for volatility breakout.",
            )
        if not required_features_ready(context.featureSnapshot, required_features):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Required feature measurements are unavailable for volatility breakout.",
            )

        candles = _session_candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [], context)
        min_candles = max(self.config.rollingLevelLookbackCandles + 1, self.config.compressionLookbackCandles + 2)
        if len(candles) < min_candles:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Insufficient completed 1-minute candles for volatility breakout.",
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
                    "volatility_breakout.confirmed",
                    f"direction:{evidence.signal.value.lower()}",
                    f"setup_id:{evidence.setupId}",
                ],
                explanation=self._explanation(evidence),
                featureNames=required_features,
                structuralInvalidationPrice=evidence.structuralInvalidationPrice,
            )

        return hold_signal(
            context,
            confidence=self._hold_confidence(evidence),
            setupDetected=evidence.compressionDetected or evidence.volatilityExpansion or evidence.levelBreak,
            regimeFit=0.4,
            reliability=self._reliability(evidence),
            reasonCodes=self._hold_reason_codes(evidence),
            explanation=self._hold_explanation(evidence),
            featureNames=required_features,
        )

    def required_feature_names(self) -> tuple[str, ...]:
        return (
            "spy1mAtr14",
            "spy1mBollingerWidthPercentile",
            "spy1mRealizedVolatilityPercentile",
            "spy1mRelativeVolume",
            "spy1mRollingHigh20",
            "spy1mRollingLow20",
            "spreadDollars",
            "spreadBasisPoints",
        )

    def _evidence(self, context: StrategyEvaluationContext, candles: list[dict[str, Any]]) -> VolatilityBreakoutEvidence:
        features = context.featureSnapshot.features
        latest = candles[-1]
        prior = candles[-self.config.rollingLevelLookbackCandles - 1 : -1]
        latest_close = float(latest["close"])
        latest_high = float(latest["high"])
        latest_low = float(latest["low"])
        atr = _number(features["spy1mAtr14"].value)
        spread = _number(features["spreadDollars"].value)
        spread_bps = _number(features["spreadBasisPoints"].value)
        bollinger_percentile = _number(features["spy1mBollingerWidthPercentile"].value)
        realized_vol_percentile = _number(features["spy1mRealizedVolatilityPercentile"].value)
        relative_volume = _number(features["spy1mRelativeVolume"].value)
        if None in {atr, spread, spread_bps, bollinger_percentile, realized_vol_percentile, relative_volume}:
            return _empty_evidence()

        assert atr is not None
        assert spread is not None
        assert spread_bps is not None
        assert bollinger_percentile is not None
        assert realized_vol_percentile is not None
        assert relative_volume is not None

        rolling_high = max(float(candle["high"]) for candle in prior)
        rolling_low = min(float(candle["low"]) for candle in prior)
        buffer_dollars = max(
            self.config.minBreakoutBufferDollars,
            atr * self.config.atrBufferMultiplier,
            spread * self.config.spreadBufferMultiplier,
        )
        if latest_close >= rolling_high + buffer_dollars:
            direction = Signal.BUY
            level = rolling_high
            close_distance = latest_close - rolling_high
            structural_invalidation = rolling_high
        elif latest_close <= rolling_low - buffer_dollars:
            direction = Signal.SELL
            level = rolling_low
            close_distance = rolling_low - latest_close
            structural_invalidation = rolling_low
        else:
            direction = Signal.HOLD
            level = None
            close_distance = 0.0
            structural_invalidation = None

        true_range_ratio = _true_range_expansion_ratio(candles, self.config.compressionLookbackCandles)
        true_ranges = _true_ranges(candles)
        latest_true_range_atr = true_ranges[-1] / atr if atr else 0.0
        prior_range_atr = _prior_range_atr_multiple(candles, self.config.compressionLookbackCandles, atr)
        compression_detected = (
            bollinger_percentile <= self.config.maxBollingerWidthPercentile
            or (prior_range_atr is not None and prior_range_atr <= self.config.maxPriorRangeAtrMultiple)
        )
        volatility_expansion = (
            latest_true_range_atr >= self.config.minLatestTrueRangeAtrMultiple
            and (
                (true_range_ratio is not None and true_range_ratio >= self.config.minTrueRangeExpansionRatio)
                or realized_vol_percentile >= self.config.minRealizedVolatilityPercentile
            )
        )
        volume_ratio = _volume_ratio(candles, self.config.compressionLookbackCandles)
        volume_expansion = (
            relative_volume >= self.config.minVolumeExpansionRatio
            and volume_ratio is not None
            and volume_ratio >= self.config.minVolumeExpansionRatio
        )
        candle_quality = _directional_candle_quality(
            latest,
            direction,
            min_body_to_range=self.config.minBodyToRangeRatio,
            min_close_location=self.config.minCloseLocationRatio,
        )
        liquidity_ok = (
            spread_bps <= self.config.maxSpreadBasisPoints
            and float(latest["volume"]) >= self.config.minLatestVolume
            and int(latest.get("tradeCount") or 0) >= self.config.minTradeCount
        )
        should_signal = all(
            [
                direction in {Signal.BUY, Signal.SELL},
                volatility_expansion,
                compression_detected,
                volume_expansion,
                candle_quality,
                liquidity_ok,
            ]
        )
        timestamp = _timestamp(latest)
        setup_id = self._setup_id(context, direction, level, timestamp) if direction in {Signal.BUY, Signal.SELL} else None

        return VolatilityBreakoutEvidence(
            signal=direction if should_signal else Signal.HOLD,
            setupId=setup_id,
            levelBreak=direction in {Signal.BUY, Signal.SELL},
            volatilityExpansion=volatility_expansion,
            compressionDetected=compression_detected,
            volumeExpansion=volume_expansion,
            candleQuality=candle_quality,
            liquidityOk=liquidity_ok,
            bufferDollars=buffer_dollars,
            level=level,
            closeDistance=close_distance,
            trueRangeExpansionRatio=true_range_ratio,
            volumeRatio=volume_ratio,
            bollingerWidthPercentile=bollinger_percentile,
            realizedVolatilityPercentile=realized_vol_percentile,
            spreadBasisPoints=spread_bps,
            breakoutTimestamp=timestamp if setup_id else None,
            structuralInvalidationPrice=round(structural_invalidation, 4) if structural_invalidation is not None else None,
        )

    def _setup_id(
        self,
        context: StrategyEvaluationContext,
        direction: Signal,
        level: float | None,
        timestamp: datetime,
    ) -> str:
        payload = {
            "strategy": self.registryEntry.strategyId,
            "sessionDate": context.sessionDate.isoformat(),
            "direction": direction.value,
            "level": round(level or 0.0, 4),
            "breakoutTimestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "lookback": self.config.rollingLevelLookbackCandles,
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    def _confidence(self, evidence: VolatilityBreakoutEvidence) -> float:
        expansion_score = 0.0 if evidence.trueRangeExpansionRatio is None else min(1.0, evidence.trueRangeExpansionRatio / max(self.config.minTrueRangeExpansionRatio * 1.8, 0.01))
        realized_vol_score = 0.0 if evidence.realizedVolatilityPercentile is None else evidence.realizedVolatilityPercentile
        volume_score = 0.0 if evidence.volumeRatio is None else min(1.0, evidence.volumeRatio / max(self.config.minVolumeExpansionRatio * 1.5, 0.01))
        level_score = min(1.0, evidence.closeDistance / max(evidence.bufferDollars * 3, 0.01))
        quality_score = 1.0 if evidence.candleQuality else 0.0
        confidence = (
            0.25 * max(expansion_score, realized_vol_score)
            + 0.25 * volume_score
            + 0.25 * level_score
            + 0.25 * quality_score
        )
        return round(max(0.0, min(1.0, confidence)), 4)

    def _hold_confidence(self, evidence: VolatilityBreakoutEvidence) -> float:
        partials = [
            evidence.levelBreak,
            evidence.volatilityExpansion,
            evidence.compressionDetected,
            evidence.volumeExpansion,
            evidence.candleQuality,
            evidence.liquidityOk,
        ]
        return round(min(0.44, max(0.05, sum(1 for item in partials if item) / len(partials) * 0.44)), 4)

    def _regime_fit(self, evidence: VolatilityBreakoutEvidence) -> float:
        expansion_fit = 0.0 if evidence.trueRangeExpansionRatio is None else min(1.0, evidence.trueRangeExpansionRatio / max(self.config.minTrueRangeExpansionRatio * 1.5, 0.01))
        volume_fit = 0.0 if evidence.volumeRatio is None else min(1.0, evidence.volumeRatio / max(self.config.minVolumeExpansionRatio * 1.3, 0.01))
        return round(max(0.0, min(1.0, (0.55 * expansion_fit) + (0.45 * volume_fit))), 4)

    def _reliability(self, evidence: VolatilityBreakoutEvidence) -> float:
        checks = [
            evidence.levelBreak,
            evidence.volatilityExpansion,
            evidence.compressionDetected,
            evidence.volumeExpansion,
            evidence.candleQuality,
            evidence.liquidityOk,
        ]
        return round(max(0.0, min(1.0, sum(1 for item in checks if item) / len(checks))), 4)

    def _explanation(self, evidence: VolatilityBreakoutEvidence) -> str:
        return (
            f"{evidence.signal.value} volatility breakout: rolling level break {evidence.closeDistance:.2f}, "
            f"range expansion {evidence.trueRangeExpansionRatio or 0:.2f}, volume ratio {evidence.volumeRatio or 0:.2f}."
        )

    def _hold_reason_codes(self, evidence: VolatilityBreakoutEvidence) -> list[str]:
        if not evidence.levelBreak:
            return ["volatility_breakout.no_level_break"]
        if not evidence.volatilityExpansion:
            return ["volatility_breakout.no_volatility_expansion"]
        if not evidence.compressionDetected:
            return ["volatility_breakout.no_prior_compression"]
        if not evidence.volumeExpansion:
            return ["volatility_breakout.no_volume_expansion"]
        if not evidence.candleQuality:
            return ["volatility_breakout.weak_directional_candle"]
        if not evidence.liquidityOk:
            return ["volatility_breakout.liquidity_or_spread_failed"]
        return ["volatility_breakout.weak_or_conflicting_evidence"]

    def _hold_explanation(self, evidence: VolatilityBreakoutEvidence) -> str:
        reason = self._hold_reason_codes(evidence)[0].removeprefix("volatility_breakout.").replace("_", " ")
        return f"HOLD because volatility breakout evidence is incomplete: {reason}."


def _empty_evidence() -> VolatilityBreakoutEvidence:
    return VolatilityBreakoutEvidence(
        signal=Signal.HOLD,
        setupId=None,
        levelBreak=False,
        volatilityExpansion=False,
        compressionDetected=False,
        volumeExpansion=False,
        candleQuality=False,
        liquidityOk=False,
        bufferDollars=0.0,
        level=None,
        closeDistance=0.0,
        trueRangeExpansionRatio=None,
        volumeRatio=None,
        bollingerWidthPercentile=None,
        realizedVolatilityPercentile=None,
        spreadBasisPoints=None,
        breakoutTimestamp=None,
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


def _true_range_expansion_ratio(candles: list[dict[str, Any]], lookback: int) -> float | None:
    if len(candles) < lookback + 2:
        return None
    true_ranges = _true_ranges(candles)
    latest = true_ranges[-1]
    baseline = mean(true_ranges[-lookback - 1 : -1])
    return latest / baseline if baseline else None


def _prior_range_atr_multiple(candles: list[dict[str, Any]], lookback: int, atr: float) -> float | None:
    if len(candles) < lookback + 1 or atr <= 0:
        return None
    prior = candles[-lookback - 1 : -1]
    prior_range = max(float(candle["high"]) for candle in prior) - min(float(candle["low"]) for candle in prior)
    return prior_range / atr


def _volume_ratio(candles: list[dict[str, Any]], lookback: int) -> float | None:
    if len(candles) < lookback + 1:
        return None
    baseline = mean(float(candle["volume"]) for candle in candles[-lookback - 1 : -1])
    return float(candles[-1]["volume"]) / baseline if baseline else None


def _true_ranges(candles: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for index, candle in enumerate(candles):
        high = float(candle["high"])
        low = float(candle["low"])
        if index == 0:
            values.append(high - low)
            continue
        previous_close = float(candles[index - 1]["close"])
        values.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return values


def _directional_candle_quality(
    candle: dict[str, Any],
    direction: Signal,
    *,
    min_body_to_range: float,
    min_close_location: float,
) -> bool:
    if direction == Signal.HOLD:
        return False
    open_price = float(candle["open"])
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])
    candle_range = high - low
    if candle_range <= 0:
        return False
    body_ratio = abs(close - open_price) / candle_range
    if direction == Signal.BUY:
        close_location = (close - low) / candle_range
        directional_body = close > open_price
    else:
        close_location = (high - close) / candle_range
        directional_body = close < open_price
    return directional_body and body_ratio >= min_body_to_range and close_location >= min_close_location


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
