from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import Enum
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


class FirstPullbackState(str, Enum):
    WAITING_FOR_OPENING_IMPULSE = "waiting_for_opening_impulse"
    IMPULSE_IDENTIFIED = "impulse_identified"
    WAITING_FOR_PULLBACK = "waiting_for_pullback"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    COMPLETED = "completed"
    INVALIDATED = "invalidated"


class FirstPullbackAfterOpenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "first_pullback_after_open_v1"
    sessionStartMinute: int = Field(default=0, ge=0, le=390)
    impulseWindowEndMinute: int = Field(default=45, ge=1, le=390)
    entryWindowEndMinute: int = Field(default=150, ge=1, le=390)
    atrLookbackCandles: int = Field(default=14, ge=2, le=60)
    minImpulseCandles: int = Field(default=3, ge=2, le=20)
    maxImpulseCandles: int = Field(default=8, ge=2, le=30)
    minImpulseAtrMultiple: float = Field(default=1.2, ge=0)
    minImpulsePercent: float = Field(default=0.0025, ge=0)
    minImpulseStructureBreakAtr: float = Field(default=0.2, ge=0)
    minRelativeVolume: float = Field(default=1.15, ge=0)
    requireRelativeVolumeWhenAvailable: bool = True
    pullbackRetracementMin: float = Field(default=0.25, ge=0, le=1)
    pullbackRetracementMax: float = Field(default=0.65, ge=0, le=1)
    pullbackZoneAtrTolerance: float = Field(default=0.25, ge=0)
    maxPullbackVolumeRatio: float = Field(default=0.8, ge=0, le=2)
    requireReducedPullbackVolume: bool = True
    originBreakAtrBuffer: float = Field(default=0.05, ge=0)
    confirmationCloseBeyondPullbackAtr: float = Field(default=0.05, ge=0)
    confirmationMinimumBodyAtr: float = Field(default=0.1, ge=0)

    @model_validator(mode="after")
    def windows_and_retracement_must_be_ordered(self) -> FirstPullbackAfterOpenConfig:
        if self.entryWindowEndMinute <= self.impulseWindowEndMinute:
            raise ValueError("entryWindowEndMinute must be after impulseWindowEndMinute")
        if self.pullbackRetracementMin > self.pullbackRetracementMax:
            raise ValueError("pullbackRetracementMin cannot exceed pullbackRetracementMax")
        if self.minImpulseCandles > self.maxImpulseCandles:
            raise ValueError("minImpulseCandles cannot exceed maxImpulseCandles")
        return self

    @property
    def configurationHash(self) -> str:
        payload = self.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Impulse:
    direction: Signal
    startIndex: int
    endIndex: int
    originPrice: float
    extremePrice: float
    atr: float
    displacementAtr: float
    displacementPercent: float
    relativeVolume: float | None
    averageVolume: float


@dataclass(frozen=True)
class Pullback:
    startIndex: int
    endIndex: int
    pullbackExtreme: float
    averageVolume: float
    retracement: float


@dataclass(frozen=True)
class StateMachineResult:
    state: FirstPullbackState
    signal: Signal
    confidence: float
    setupDetected: bool
    reasonCodes: list[str]
    explanation: str
    impulse: Impulse | None = None
    pullback: Pullback | None = None
    structuralInvalidationPrice: float | None = None


class FirstPullbackAfterOpenStrategy:
    registryEntry = resolve_strategy("first_pullback_after_open")

    def __init__(self, config: FirstPullbackAfterOpenConfig | None = None) -> None:
        self.config = config or FirstPullbackAfterOpenConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> StrategySignal:
        required_features = self.required_feature_names()
        if not context.featureSnapshot.dataReady:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Feature snapshot is not ready for first pullback after open.",
            )
        if not required_features_ready(context.featureSnapshot, required_features):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Required feature measurements are unavailable for first pullback after open.",
            )

        candles = _session_candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [], context)
        if len(candles) < self.config.atrLookbackCandles + self.config.minImpulseCandles:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Insufficient completed 1-minute candles for first pullback after open.",
            )

        result = self._run_state_machine(candles)
        if result.signal in {Signal.BUY, Signal.SELL}:
            return strategy_signal(
                context,
                signal=result.signal,
                confidence=result.confidence,
                eligible=True,
                setupDetected=True,
                regimeFit=self._regime_fit(result),
                reliability=self._reliability(result),
                reasonCodes=result.reasonCodes,
                explanation=result.explanation,
                featureNames=required_features,
                structuralInvalidationPrice=result.structuralInvalidationPrice,
            )

        return hold_signal(
            context,
            confidence=result.confidence,
            setupDetected=result.setupDetected,
            regimeFit=0.0 if result.state == FirstPullbackState.INVALIDATED else 0.4,
            reliability=self._reliability(result),
            reasonCodes=result.reasonCodes,
            explanation=result.explanation,
            featureNames=required_features,
            structuralInvalidationPrice=result.structuralInvalidationPrice,
        )

    def required_feature_names(self) -> tuple[str, ...]:
        return (
            "sessionVwap",
            "spy1mEma9",
            "spy1mEma20",
            "spy1mAtr14",
            "spy1mRelativeVolume",
            "timeSinceMarketOpenMinutes",
        )

    def _run_state_machine(self, candles: list[dict[str, Any]]) -> StateMachineResult:
        state = FirstPullbackState.WAITING_FOR_OPENING_IMPULSE
        impulse: Impulse | None = None
        pullback: Pullback | None = None
        pullback_start: int | None = None
        pullback_extreme: float | None = None
        pullback_volumes: list[float] = []
        atr_series = _atr_series(candles, self.config.atrLookbackCandles)
        ema9_series = _ema_series([float(candle["close"]) for candle in candles], 9)
        ema20_series = _ema_series([float(candle["close"]) for candle in candles], 20)
        vwap_series = _vwap_series(candles)

        for index, candle in enumerate(candles):
            minute = _minutes_after_open(_timestamp(candle))
            if minute < self.config.sessionStartMinute:
                continue
            if minute > self.config.entryWindowEndMinute:
                break

            atr = atr_series[index]
            if atr is None:
                continue

            if state == FirstPullbackState.WAITING_FOR_OPENING_IMPULSE:
                if minute > self.config.impulseWindowEndMinute:
                    return self._hold_no_impulse()
                impulse = self._detect_impulse(candles, atr_series, index)
                if impulse is None:
                    continue
                state = FirstPullbackState.IMPULSE_IDENTIFIED

            if state == FirstPullbackState.IMPULSE_IDENTIFIED:
                state = FirstPullbackState.WAITING_FOR_PULLBACK
                continue

            if state == FirstPullbackState.WAITING_FOR_PULLBACK and impulse is not None:
                if self._breaks_impulse_origin(candle, impulse):
                    return self._invalidated(impulse, "first_pullback.impulse_origin_broken")
                if self._moves_against_impulse(candle, impulse) and self._touches_pullback_zone(candle, impulse, atr, ema9_series[index], ema20_series[index], vwap_series[index]):
                    pullback_start = index
                    pullback_extreme = float(candle["low"] if impulse.direction == Signal.BUY else candle["high"])
                    pullback_volumes = [float(candle["volume"])]
                    state = FirstPullbackState.WAITING_FOR_CONFIRMATION
                continue

            if state == FirstPullbackState.WAITING_FOR_CONFIRMATION and impulse is not None:
                if self._breaks_impulse_origin(candle, impulse):
                    return self._invalidated(impulse, "first_pullback.impulse_origin_broken")

                if pullback_start is None or pullback_extreme is None:
                    return self._invalidated(impulse, "first_pullback.state_malformed")

                pullback = Pullback(
                    startIndex=pullback_start,
                    endIndex=index - 1,
                    pullbackExtreme=pullback_extreme,
                    averageVolume=mean(pullback_volumes),
                    retracement=self._retracement(impulse, pullback_extreme),
                )
                if self._confirmation_candle(candles, index, impulse, pullback, atr):
                    if index < len(candles) - 1:
                        return self._hold_completed(impulse, pullback)
                    return self._completed(impulse, pullback)

                current_extreme = float(candle["low"] if impulse.direction == Signal.BUY else candle["high"])
                if impulse.direction == Signal.BUY:
                    pullback_extreme = min(pullback_extreme, current_extreme)
                else:
                    pullback_extreme = max(pullback_extreme, current_extreme)
                pullback_volumes.append(float(candle["volume"]))

        if state == FirstPullbackState.WAITING_FOR_OPENING_IMPULSE:
            return self._hold_no_impulse()
        if state in {FirstPullbackState.IMPULSE_IDENTIFIED, FirstPullbackState.WAITING_FOR_PULLBACK}:
            return self._hold_waiting_for_pullback(impulse)
        return self._hold_waiting_for_confirmation(impulse, pullback)

    def _detect_impulse(self, candles: list[dict[str, Any]], atr_series: list[float | None], end_index: int) -> Impulse | None:
        for length in range(self.config.minImpulseCandles, self.config.maxImpulseCandles + 1):
            start_index = end_index - length + 1
            if start_index < 0:
                continue
            start = candles[start_index]
            end = candles[end_index]
            atr = atr_series[end_index]
            if atr is None:
                continue
            origin = float(start["open"])
            close = float(end["close"])
            highest_high = max(float(candle["high"]) for candle in candles[start_index : end_index + 1])
            lowest_low = min(float(candle["low"]) for candle in candles[start_index : end_index + 1])
            direction = Signal.BUY if close > origin else Signal.SELL if close < origin else Signal.HOLD
            if direction == Signal.HOLD:
                continue
            extreme = highest_high if direction == Signal.BUY else lowest_low
            displacement = abs(close - origin)
            displacement_percent = displacement / origin if origin else 0
            displacement_atr = displacement / atr if atr else 0
            structure_break = abs(extreme - origin) / atr if atr else 0
            relative_volume = _relative_volume(candles, start_index, end_index)

            if displacement_atr < self.config.minImpulseAtrMultiple:
                continue
            if displacement_percent < self.config.minImpulsePercent:
                continue
            if structure_break < self.config.minImpulseStructureBreakAtr:
                continue
            if (
                self.config.requireRelativeVolumeWhenAvailable
                and relative_volume is not None
                and relative_volume < self.config.minRelativeVolume
            ):
                continue

            if not _impulse_structure_ok(candles, start_index, end_index, direction):
                continue

            return Impulse(
                direction=direction,
                startIndex=start_index,
                endIndex=end_index,
                originPrice=origin,
                extremePrice=extreme,
                atr=atr,
                displacementAtr=displacement_atr,
                displacementPercent=displacement_percent,
                relativeVolume=relative_volume,
                averageVolume=mean(float(candle["volume"]) for candle in candles[start_index : end_index + 1]),
            )
        return None

    def _touches_pullback_zone(
        self,
        candle: dict[str, Any],
        impulse: Impulse,
        atr: float,
        ema9: float | None,
        ema20: float | None,
        vwap: float | None,
    ) -> bool:
        low = float(candle["low"])
        high = float(candle["high"])
        retracement_min, retracement_max = self._retracement_zone(impulse)
        tolerance = atr * self.config.pullbackZoneAtrTolerance
        zone_values = [value for value in (ema9, ema20, vwap) if value is not None]

        if impulse.direction == Signal.BUY:
            touched_retracement = low <= retracement_max + tolerance and high >= retracement_min - tolerance
            touched_dynamic = any(low <= value + tolerance <= high + tolerance for value in zone_values)
            preserved = low >= impulse.originPrice - (atr * self.config.originBreakAtrBuffer)
            return preserved and (touched_retracement or touched_dynamic)

        touched_retracement = high >= retracement_min - tolerance and low <= retracement_max + tolerance
        touched_dynamic = any(high >= value - tolerance >= low - tolerance for value in zone_values)
        preserved = high <= impulse.originPrice + (atr * self.config.originBreakAtrBuffer)
        return preserved and (touched_retracement or touched_dynamic)

    def _moves_against_impulse(self, candle: dict[str, Any], impulse: Impulse) -> bool:
        open_price = float(candle["open"])
        close = float(candle["close"])
        if impulse.direction == Signal.BUY:
            return close < open_price
        return close > open_price

    def _confirmation_candle(
        self,
        candles: list[dict[str, Any]],
        index: int,
        impulse: Impulse,
        pullback: Pullback,
        atr: float,
    ) -> bool:
        if index <= pullback.startIndex:
            return False
        candle = candles[index]
        previous = candles[index - 1]
        open_price = float(candle["open"])
        close = float(candle["close"])
        body = abs(close - open_price)
        if body < atr * self.config.confirmationMinimumBodyAtr:
            return False
        if self.config.requireReducedPullbackVolume and pullback.averageVolume > impulse.averageVolume * self.config.maxPullbackVolumeRatio:
            return False

        threshold = atr * self.config.confirmationCloseBeyondPullbackAtr
        if impulse.direction == Signal.BUY:
            return close > open_price and close >= float(previous["high"]) + threshold
        return close < open_price and close <= float(previous["low"]) - threshold

    def _breaks_impulse_origin(self, candle: dict[str, Any], impulse: Impulse) -> bool:
        buffer = impulse.atr * self.config.originBreakAtrBuffer
        if impulse.direction == Signal.BUY:
            return float(candle["low"]) < impulse.originPrice - buffer
        return float(candle["high"]) > impulse.originPrice + buffer

    def _retracement_zone(self, impulse: Impulse) -> tuple[float, float]:
        impulse_range = abs(impulse.extremePrice - impulse.originPrice)
        if impulse.direction == Signal.BUY:
            shallow = impulse.extremePrice - (impulse_range * self.config.pullbackRetracementMin)
            deep = impulse.extremePrice - (impulse_range * self.config.pullbackRetracementMax)
            return deep, shallow
        shallow = impulse.extremePrice + (impulse_range * self.config.pullbackRetracementMin)
        deep = impulse.extremePrice + (impulse_range * self.config.pullbackRetracementMax)
        return shallow, deep

    def _retracement(self, impulse: Impulse, pullback_extreme: float) -> float:
        impulse_range = abs(impulse.extremePrice - impulse.originPrice)
        if impulse_range == 0:
            return 0
        return abs(impulse.extremePrice - pullback_extreme) / impulse_range

    def _completed(self, impulse: Impulse, pullback: Pullback) -> StateMachineResult:
        confidence = self._confidence(impulse, pullback)
        return StateMachineResult(
            state=FirstPullbackState.COMPLETED,
            signal=impulse.direction,
            confidence=confidence,
            setupDetected=True,
            reasonCodes=[
                "first_pullback.completed",
                f"state:{FirstPullbackState.COMPLETED.value}",
                f"direction:{impulse.direction.value.lower()}",
            ],
            explanation=(
                f"{impulse.direction.value} first pullback after open: impulse "
                f"{impulse.displacementAtr:.2f} ATR, retracement {pullback.retracement:.2f}, confirmation candle complete."
            ),
            impulse=impulse,
            pullback=pullback,
            structuralInvalidationPrice=round(impulse.originPrice, 4),
        )

    def _invalidated(self, impulse: Impulse | None, reason_code: str) -> StateMachineResult:
        return StateMachineResult(
            state=FirstPullbackState.INVALIDATED,
            signal=Signal.HOLD,
            confidence=0.0,
            setupDetected=impulse is not None,
            reasonCodes=[reason_code, f"state:{FirstPullbackState.INVALIDATED.value}"],
            explanation="HOLD because the first pullback setup was invalidated before confirmation.",
            impulse=impulse,
            structuralInvalidationPrice=round(impulse.originPrice, 4) if impulse else None,
        )

    def _hold_no_impulse(self) -> StateMachineResult:
        return StateMachineResult(
            state=FirstPullbackState.WAITING_FOR_OPENING_IMPULSE,
            signal=Signal.HOLD,
            confidence=0.1,
            setupDetected=False,
            reasonCodes=["first_pullback.no_opening_impulse", f"state:{FirstPullbackState.WAITING_FOR_OPENING_IMPULSE.value}"],
            explanation="HOLD because no qualifying opening impulse has been identified.",
        )

    def _hold_waiting_for_pullback(self, impulse: Impulse | None) -> StateMachineResult:
        return StateMachineResult(
            state=FirstPullbackState.WAITING_FOR_PULLBACK,
            signal=Signal.HOLD,
            confidence=0.25,
            setupDetected=impulse is not None,
            reasonCodes=["first_pullback.waiting_for_pullback", f"state:{FirstPullbackState.WAITING_FOR_PULLBACK.value}"],
            explanation="HOLD because the opening impulse is present but the first pullback has not qualified.",
            impulse=impulse,
            structuralInvalidationPrice=round(impulse.originPrice, 4) if impulse else None,
        )

    def _hold_waiting_for_confirmation(self, impulse: Impulse | None, pullback: Pullback | None) -> StateMachineResult:
        return StateMachineResult(
            state=FirstPullbackState.WAITING_FOR_CONFIRMATION,
            signal=Signal.HOLD,
            confidence=0.35,
            setupDetected=impulse is not None and pullback is not None,
            reasonCodes=["first_pullback.waiting_for_confirmation", f"state:{FirstPullbackState.WAITING_FOR_CONFIRMATION.value}"],
            explanation="HOLD because the first pullback is present but no continuation confirmation candle has completed.",
            impulse=impulse,
            pullback=pullback,
            structuralInvalidationPrice=round(impulse.originPrice, 4) if impulse else None,
        )

    def _hold_completed(self, impulse: Impulse, pullback: Pullback) -> StateMachineResult:
        return StateMachineResult(
            state=FirstPullbackState.COMPLETED,
            signal=Signal.HOLD,
            confidence=0.2,
            setupDetected=False,
            reasonCodes=["first_pullback.already_completed", f"state:{FirstPullbackState.COMPLETED.value}"],
            explanation="HOLD because the first qualifying pullback already completed earlier in the session.",
            impulse=impulse,
            pullback=pullback,
            structuralInvalidationPrice=round(impulse.originPrice, 4),
        )

    def _confidence(self, impulse: Impulse, pullback: Pullback) -> float:
        displacement_score = min(1.0, impulse.displacementAtr / max(self.config.minImpulseAtrMultiple * 2, 0.01))
        volume_score = 0.7 if impulse.relativeVolume is None else min(1.0, impulse.relativeVolume / max(self.config.minRelativeVolume * 1.5, 0.01))
        pullback_depth_score = 1.0 - min(1.0, abs(pullback.retracement - 0.45))
        pullback_volume_score = 1.0 - min(1.0, pullback.averageVolume / max(impulse.averageVolume, 1))
        confidence = (0.35 * displacement_score) + (0.2 * volume_score) + (0.25 * pullback_depth_score) + (0.2 * pullback_volume_score)
        return round(max(0.0, min(1.0, confidence)), 4)

    def _reliability(self, result: StateMachineResult) -> float:
        if result.state == FirstPullbackState.INVALIDATED:
            return 0.0
        if result.impulse is None:
            return 0.35
        volume_quality = 0.75 if result.impulse.relativeVolume is None else min(1.0, result.impulse.relativeVolume / max(self.config.minRelativeVolume, 0.01))
        state_quality = {
            FirstPullbackState.WAITING_FOR_PULLBACK: 0.55,
            FirstPullbackState.WAITING_FOR_CONFIRMATION: 0.7,
            FirstPullbackState.COMPLETED: 0.9,
        }.get(result.state, 0.4)
        return round(max(0.0, min(1.0, (0.6 * state_quality) + (0.4 * volume_quality))), 4)

    def _regime_fit(self, result: StateMachineResult) -> float:
        if result.impulse is None:
            return 0.0
        retracement_fit = 0.7
        if result.pullback is not None:
            retracement_fit = 1.0 - min(1.0, abs(result.pullback.retracement - 0.45))
        return round(max(0.0, min(1.0, (0.55 * min(1.0, result.impulse.displacementAtr / 2.0)) + (0.45 * retracement_fit))), 4)


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


def _impulse_structure_ok(candles: list[dict[str, Any]], start_index: int, end_index: int, direction: Signal) -> bool:
    highs = [float(candle["high"]) for candle in candles[start_index : end_index + 1]]
    lows = [float(candle["low"]) for candle in candles[start_index : end_index + 1]]
    if len(highs) < 3:
        return False
    if direction == Signal.BUY:
        return highs[-1] >= max(highs[:-1]) and lows[-1] >= min(lows[:-1])
    return lows[-1] <= min(lows[:-1]) and highs[-1] <= max(highs[:-1])


def _relative_volume(candles: list[dict[str, Any]], start_index: int, end_index: int) -> float | None:
    lookback = candles[max(0, start_index - 20) : start_index]
    if len(lookback) < 5:
        return None
    baseline = mean(float(candle["volume"]) for candle in lookback)
    if baseline <= 0:
        return None
    impulse_average = mean(float(candle["volume"]) for candle in candles[start_index : end_index + 1])
    return impulse_average / baseline


def _atr_series(candles: list[dict[str, Any]], period: int) -> list[float | None]:
    result: list[float | None] = []
    true_ranges: list[float] = []
    for index, candle in enumerate(candles):
        high = float(candle["high"])
        low = float(candle["low"])
        if index == 0:
            true_range = high - low
        else:
            previous_close = float(candles[index - 1]["close"])
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(true_range)
        result.append(mean(true_ranges[-period:]) if len(true_ranges) >= period else None)
    return result


def _ema_series(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = []
    ema_value: float | None = None
    alpha = 2 / (period + 1)
    for index, value in enumerate(values):
        if index + 1 < period:
            result.append(None)
            continue
        if ema_value is None:
            ema_value = mean(values[index + 1 - period : index + 1])
        else:
            ema_value = (value * alpha) + (ema_value * (1 - alpha))
        result.append(ema_value)
    return result


def _vwap_series(candles: list[dict[str, Any]]) -> list[float | None]:
    volume_total = 0.0
    price_volume_total = 0.0
    result: list[float | None] = []
    for candle in candles:
        volume = float(candle["volume"])
        typical = (float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3
        volume_total += volume
        price_volume_total += typical * volume
        result.append(price_volume_total / volume_total if volume_total else None)
    return result


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
