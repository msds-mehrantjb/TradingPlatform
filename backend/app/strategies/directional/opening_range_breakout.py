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


OpeningRangeDefinition = Literal[5, 15]


class OpeningRangeBreakoutConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "opening_range_breakout_v1"
    openingRangeMinutes: OpeningRangeDefinition = 15
    breakoutWindowEndMinute: int = Field(default=180, ge=5, le=390)
    minCloseBufferDollars: float = Field(default=0.01, ge=0)
    spreadBufferMultiplier: float = Field(default=1.0, ge=0)
    atrBufferMultiplier: float = Field(default=0.08, ge=0)
    minRelativeVolume: float = Field(default=1.15, ge=0)
    compressionLookbackCandles: int = Field(default=20, ge=5, le=60)
    maxCompressionRatio: float = Field(default=1.4, ge=0)
    requireRangeCompression: bool = False
    requireRetestConfirmation: bool = False
    retestLookbackCandles: int = Field(default=5, ge=1, le=30)
    retestAtrTolerance: float = Field(default=0.25, ge=0)

    @model_validator(mode="after")
    def breakout_window_must_follow_range(self) -> OpeningRangeBreakoutConfig:
        if self.breakoutWindowEndMinute <= self.openingRangeMinutes:
            raise ValueError("breakoutWindowEndMinute must be after openingRangeMinutes")
        return self

    @property
    def configurationHash(self) -> str:
        payload = self.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class OpeningRange:
    high: float
    low: float
    startTimestamp: datetime
    endTimestamp: datetime

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass(frozen=True)
class BreakoutEvidence:
    signal: Signal
    setupId: str | None
    range: OpeningRange | None
    closeBeyondRange: bool
    bufferDollars: float
    closeDistance: float
    relativeVolume: float | None
    rangeCompressionRatio: float | None
    retestConfirmed: bool
    breakoutTimestamp: datetime | None
    structuralInvalidationPrice: float | None


class OpeningRangeBreakoutStrategy:
    registryEntry = resolve_strategy("opening_range_breakout")

    def __init__(self, config: OpeningRangeBreakoutConfig | None = None) -> None:
        self.config = config or OpeningRangeBreakoutConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> StrategySignal:
        required_features = self.required_feature_names()
        if not context.featureSnapshot.dataReady:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Feature snapshot is not ready for opening range breakout.",
            )
        if not required_features_ready(context.featureSnapshot, required_features):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Required feature measurements are unavailable for opening range breakout.",
            )

        candles = _session_candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [], context)
        if not candles:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="No completed session candles are available for opening range breakout.",
            )

        current_minute = _minutes_after_open(_timestamp(candles[-1]))
        if current_minute < self.config.openingRangeMinutes:
            return self._hold(
                context,
                reasonCodes=["opening_range.range_incomplete"],
                explanation="HOLD because the configured opening range is not complete.",
                featureNames=required_features,
                setupDetected=False,
                confidence=0.05,
            )

        opening_range = self._opening_range(candles)
        if opening_range is None:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Unable to construct the configured opening range from completed candles.",
            )

        first_event = self._first_breakout_event(context, candles, opening_range)
        if first_event.signal in {Signal.BUY, Signal.SELL}:
            if first_event.breakoutTimestamp == _timestamp(candles[-1]):
                confidence = self._confidence(first_event)
                return strategy_signal(
                    context,
                    signal=first_event.signal,
                    confidence=confidence,
                    eligible=True,
                    setupDetected=True,
                    regimeFit=self._regime_fit(first_event),
                    reliability=self._reliability(first_event),
                    reasonCodes=[
                        "opening_range.breakout_confirmed",
                        f"direction:{first_event.signal.value.lower()}",
                        f"setup_id:{first_event.setupId}",
                    ],
                    explanation=self._explanation(first_event),
                    featureNames=required_features,
                    structuralInvalidationPrice=first_event.structuralInvalidationPrice,
                )

            return self._hold(
                context,
                reasonCodes=["opening_range.already_completed", f"setup_id:{first_event.setupId}"],
                explanation="HOLD because this opening range breakout event already completed earlier in the session.",
                featureNames=required_features,
                setupDetected=False,
                confidence=0.2,
                structuralInvalidationPrice=first_event.structuralInvalidationPrice,
            )

        return self._hold(
            context,
            reasonCodes=self._hold_reason_codes(first_event),
            explanation=self._hold_explanation(first_event),
            featureNames=required_features,
            setupDetected=False,
            confidence=self._hold_confidence(first_event),
        )

    def required_feature_names(self) -> tuple[str, ...]:
        return (
            "spy1mAtr14",
            "spy1mRelativeVolume",
            "spreadDollars",
            "timeSinceMarketOpenMinutes",
        )

    def _opening_range(self, candles: list[dict[str, Any]]) -> OpeningRange | None:
        range_candles = [
            candle
            for candle in candles
            if 0 <= _minutes_after_open(_timestamp(candle)) < self.config.openingRangeMinutes
        ]
        if len(range_candles) < self.config.openingRangeMinutes:
            return None
        return OpeningRange(
            high=max(float(candle["high"]) for candle in range_candles),
            low=min(float(candle["low"]) for candle in range_candles),
            startTimestamp=_timestamp(range_candles[0]),
            endTimestamp=_timestamp(range_candles[-1]),
        )

    def _first_breakout_event(
        self,
        context: StrategyEvaluationContext,
        candles: list[dict[str, Any]],
        opening_range: OpeningRange,
    ) -> BreakoutEvidence:
        for index, candle in enumerate(candles):
            minute = _minutes_after_open(_timestamp(candle))
            if minute < self.config.openingRangeMinutes:
                continue
            if minute > self.config.breakoutWindowEndMinute:
                break
            evidence = self._breakout_evidence(context, candles, opening_range, index)
            if evidence.signal in {Signal.BUY, Signal.SELL}:
                return evidence

        return self._breakout_evidence(context, candles, opening_range, len(candles) - 1)

    def _breakout_evidence(
        self,
        context: StrategyEvaluationContext,
        candles: list[dict[str, Any]],
        opening_range: OpeningRange,
        index: int,
    ) -> BreakoutEvidence:
        features = context.featureSnapshot.features
        candle = candles[index]
        close = float(candle["close"])
        timestamp = _timestamp(candle)
        atr = _number(features["spy1mAtr14"].value) or 0.0
        spread = _number(features["spreadDollars"].value) or 0.0
        relative_volume = self._relative_volume(candles, index)
        buffer_dollars = max(
            self.config.minCloseBufferDollars,
            spread * self.config.spreadBufferMultiplier,
            atr * self.config.atrBufferMultiplier,
        )

        if close > opening_range.high:
            direction = Signal.BUY
            close_distance = close - opening_range.high
            close_beyond = close >= opening_range.high + buffer_dollars
            structural_invalidation = opening_range.high
        elif close < opening_range.low:
            direction = Signal.SELL
            close_distance = opening_range.low - close
            close_beyond = close <= opening_range.low - buffer_dollars
            structural_invalidation = opening_range.low
        else:
            direction = Signal.HOLD
            close_distance = 0.0
            close_beyond = False
            structural_invalidation = None

        compression_ratio = self._range_compression_ratio(candles, opening_range)
        compression_ok = (
            True
            if not self.config.requireRangeCompression
            else compression_ratio is not None and compression_ratio <= self.config.maxCompressionRatio
        )
        volume_ok = relative_volume is not None and relative_volume >= self.config.minRelativeVolume
        retest_confirmed = self._retest_confirmed(candles, opening_range, index, direction, atr)
        retest_ok = True if not self.config.requireRetestConfirmation else retest_confirmed
        should_signal = all([direction != Signal.HOLD, close_beyond, volume_ok, compression_ok, retest_ok])
        setup_id = (
            self._setup_id(context, opening_range, direction, timestamp)
            if direction in {Signal.BUY, Signal.SELL} and close_beyond
            else None
        )

        return BreakoutEvidence(
            signal=direction if should_signal else Signal.HOLD,
            setupId=setup_id,
            range=opening_range,
            closeBeyondRange=close_beyond,
            bufferDollars=buffer_dollars,
            closeDistance=close_distance,
            relativeVolume=relative_volume,
            rangeCompressionRatio=compression_ratio,
            retestConfirmed=retest_confirmed,
            breakoutTimestamp=timestamp if setup_id else None,
            structuralInvalidationPrice=round(structural_invalidation, 4) if structural_invalidation is not None else None,
        )

    def _relative_volume(self, candles: list[dict[str, Any]], index: int) -> float | None:
        start = max(0, index - 20)
        baseline_candles = candles[start:index]
        if len(baseline_candles) < 5:
            return None
        baseline = mean(float(candle["volume"]) for candle in baseline_candles)
        return float(candles[index]["volume"]) / baseline if baseline else None

    def _range_compression_ratio(self, candles: list[dict[str, Any]], opening_range: OpeningRange) -> float | None:
        range_candles = [
            candle
            for candle in candles
            if 0 <= _minutes_after_open(_timestamp(candle)) < self.config.openingRangeMinutes
        ]
        lookback = candles[max(0, len(range_candles) - self.config.compressionLookbackCandles) : len(range_candles)]
        if not lookback or opening_range.width <= 0:
            return None
        average_width = mean(float(candle["high"]) - float(candle["low"]) for candle in lookback)
        return opening_range.width / average_width if average_width else None

    def _retest_confirmed(
        self,
        candles: list[dict[str, Any]],
        opening_range: OpeningRange,
        index: int,
        direction: Signal,
        atr: float,
    ) -> bool:
        if direction == Signal.HOLD:
            return False
        tolerance = atr * self.config.retestAtrTolerance
        start = max(0, index - self.config.retestLookbackCandles)
        prior = candles[start:index]
        if direction == Signal.BUY:
            return any(float(candle["low"]) <= opening_range.high + tolerance for candle in prior)
        return any(float(candle["high"]) >= opening_range.low - tolerance for candle in prior)

    def _setup_id(
        self,
        context: StrategyEvaluationContext,
        opening_range: OpeningRange,
        direction: Signal,
        timestamp: datetime,
    ) -> str:
        payload = {
            "strategy": self.registryEntry.strategyId,
            "sessionDate": context.sessionDate.isoformat(),
            "definition": self.config.openingRangeMinutes,
            "direction": direction.value,
            "rangeHigh": round(opening_range.high, 4),
            "rangeLow": round(opening_range.low, 4),
            "breakoutTimestamp": timestamp.isoformat().replace("+00:00", "Z"),
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    def _confidence(self, evidence: BreakoutEvidence) -> float:
        volume_score = 0.0 if evidence.relativeVolume is None else min(1.0, evidence.relativeVolume / max(self.config.minRelativeVolume * 1.6, 0.01))
        buffer_score = min(1.0, evidence.closeDistance / max(evidence.bufferDollars * 3, 0.01))
        compression_score = 0.7
        if evidence.rangeCompressionRatio is not None:
            compression_score = max(0.0, min(1.0, 1.3 - evidence.rangeCompressionRatio / max(self.config.maxCompressionRatio, 0.01)))
        retest_score = 1.0 if evidence.retestConfirmed else 0.65
        confidence = (0.35 * volume_score) + (0.3 * buffer_score) + (0.2 * compression_score) + (0.15 * retest_score)
        return round(max(0.0, min(1.0, confidence)), 4)

    def _hold_confidence(self, evidence: BreakoutEvidence) -> float:
        partials = [
            evidence.closeBeyondRange,
            evidence.relativeVolume is not None and evidence.relativeVolume >= self.config.minRelativeVolume,
            evidence.closeDistance > 0,
            evidence.range is not None,
        ]
        return round(min(0.44, max(0.05, sum(1 for item in partials if item) / len(partials) * 0.44)), 4)

    def _regime_fit(self, evidence: BreakoutEvidence) -> float:
        volume_fit = 0.5 if evidence.relativeVolume is None else min(1.0, evidence.relativeVolume / max(self.config.minRelativeVolume * 1.3, 0.01))
        distance_fit = min(1.0, evidence.closeDistance / max(evidence.bufferDollars * 2, 0.01))
        return round(max(0.0, min(1.0, (0.55 * volume_fit) + (0.45 * distance_fit))), 4)

    def _reliability(self, evidence: BreakoutEvidence) -> float:
        volume_score = 0.5 if evidence.relativeVolume is None else min(1.0, evidence.relativeVolume / max(self.config.minRelativeVolume, 0.01))
        close_score = 1.0 if evidence.closeBeyondRange else 0.0
        compression_score = 0.8 if evidence.rangeCompressionRatio is not None else 0.5
        return round(max(0.0, min(1.0, (0.4 * volume_score) + (0.4 * close_score) + (0.2 * compression_score))), 4)

    def _explanation(self, evidence: BreakoutEvidence) -> str:
        assert evidence.range is not None
        return (
            f"{evidence.signal.value} opening range breakout: close cleared "
            f"{evidence.closeDistance:.2f} against a {evidence.bufferDollars:.2f} buffer; "
            f"setup {evidence.setupId}."
        )

    def _hold_reason_codes(self, evidence: BreakoutEvidence) -> list[str]:
        if evidence.range is None:
            return ["opening_range.range_unavailable"]
        if evidence.closeDistance > 0 and not evidence.closeBeyondRange:
            return ["opening_range.close_inside_buffer"]
        if evidence.relativeVolume is not None and evidence.relativeVolume < self.config.minRelativeVolume:
            return ["opening_range.volume_too_low"]
        return ["opening_range.no_confirmed_breakout"]

    def _hold_explanation(self, evidence: BreakoutEvidence) -> str:
        reason = self._hold_reason_codes(evidence)[0].removeprefix("opening_range.").replace("_", " ")
        return f"HOLD because opening range breakout evidence is incomplete: {reason}."

    def _hold(
        self,
        context: StrategyEvaluationContext,
        *,
        reasonCodes: list[str],
        explanation: str,
        featureNames: tuple[str, ...],
        setupDetected: bool,
        confidence: float,
        structuralInvalidationPrice: float | None = None,
    ) -> StrategySignal:
        return hold_signal(
            context,
            confidence=confidence,
            setupDetected=setupDetected,
            regimeFit=0.4,
            reliability=0.4,
            reasonCodes=reasonCodes,
            explanation=explanation,
            featureNames=featureNames,
            structuralInvalidationPrice=structuralInvalidationPrice,
        )


def _session_candles(raw_candles: list[dict[str, Any]], context: StrategyEvaluationContext) -> list[dict[str, Any]]:
    by_timestamp: dict[datetime, dict[str, Any]] = {}
    for candle in raw_candles:
        timestamp = _timestamp(candle)
        if timestamp > context.evaluatedAt:
            continue
        if _new_york_datetime(timestamp).date() != context.sessionDate:
            continue
        by_timestamp[timestamp] = candle
    return [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]


def _timestamp(candle: dict[str, Any]) -> datetime:
    value = candle["timestamp"]
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _minutes_after_open(timestamp: datetime) -> float:
    local = _new_york_datetime(timestamp)
    marker = local.replace(hour=9, minute=30, second=0, microsecond=0)
    return (local - marker).total_seconds() / 60


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
