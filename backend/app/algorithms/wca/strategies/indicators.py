"""Read-only indicator helpers for isolated WCA strategies."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.wca.contracts import WcaCandle, WcaEvaluationStatus, WcaMarketSnapshot, WcaSide, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import WcaStrategyDefinition


def definition_for(strategy: object) -> WcaStrategyDefinition:
    return WcaStrategyDefinition(
        getattr(strategy, "strategy_id"),
        getattr(strategy, "slug"),
        getattr(strategy, "name"),
        getattr(strategy, "family"),
        getattr(strategy, "base_weight"),
    )


def invalid_result(snapshot: WcaMarketSnapshot, strategy: object) -> WcaStrategyEvaluation | None:
    if not snapshot.data_ready:
        return invalid_strategy(strategy, "wca.data.not_ready", "Market snapshot is not data-ready.")
    candles = completed_candles(snapshot)
    if not candles:
        return invalid_strategy(strategy, "wca.data.missing_candles", "No completed candles are available.")
    if any(c.close <= 0 or c.high < c.low or c.volume < 0 for c in candles):
        return invalid_strategy(strategy, "wca.data.invalid_candle", "Snapshot contains invalid candle data.")
    return None


def active(strategy: object, signal: WcaSide, confidence: float, explanation: str) -> WcaStrategyEvaluation:
    confidence_value = round(max(0, min(1, confidence)), 4)
    direction = 1 if signal == WcaSide.BUY else -1 if signal == WcaSide.SELL else 0
    contribution = round(direction * getattr(strategy, "base_weight") * confidence_value, 4)
    return WcaStrategyEvaluation(
        strategy_id=getattr(strategy, "strategy_id"),
        strategy_version=getattr(strategy, "version"),
        name=getattr(strategy, "name"),
        status=WcaEvaluationStatus.ACTIVE,
        signal=signal,
        confidence=confidence_value,
        raw_confidence=confidence_value,
        calibrated_confidence=confidence_value,
        direction=signal,
        applicability=WcaEvaluationStatus.ACTIVE,
        evidence_strength=confidence_value,
        data_quality_status=WcaEvaluationStatus.ACTIVE,
        calibration_version="wca_confidence_calibration_disabled_v1",
        base_weight=getattr(strategy, "base_weight"),
        effective_weight=getattr(strategy, "base_weight"),
        contribution=contribution,
        reason_codes=(f"wca.strategy.{getattr(strategy, 'slug')}",),
        explanation=explanation,
    )


def not_applicable(strategy: object, reason_code: str, explanation: str) -> WcaStrategyEvaluation:
    return WcaStrategyEvaluation(
        strategy_id=getattr(strategy, "strategy_id"),
        strategy_version=getattr(strategy, "version"),
        name=getattr(strategy, "name"),
        status=WcaEvaluationStatus.NOT_APPLICABLE,
        signal=WcaSide.HOLD,
        confidence=0,
        raw_confidence=0,
        calibrated_confidence=0,
        direction=WcaSide.HOLD,
        applicability=WcaEvaluationStatus.NOT_APPLICABLE,
        evidence_strength=0,
        data_quality_status=WcaEvaluationStatus.NOT_APPLICABLE,
        calibration_version="wca_confidence_calibration_disabled_v1",
        base_weight=getattr(strategy, "base_weight"),
        effective_weight=0,
        contribution=0,
        reason_codes=(reason_code,),
        explanation=explanation,
    )


def invalid_strategy(strategy: object, reason_code: str, explanation: str) -> WcaStrategyEvaluation:
    return WcaStrategyEvaluation(
        strategy_id=getattr(strategy, "strategy_id"),
        strategy_version=getattr(strategy, "version"),
        name=getattr(strategy, "name"),
        status=WcaEvaluationStatus.INVALID,
        signal=WcaSide.HOLD,
        confidence=0,
        raw_confidence=0,
        calibrated_confidence=0,
        direction=WcaSide.HOLD,
        applicability=WcaEvaluationStatus.INVALID,
        evidence_strength=0,
        data_quality_status=WcaEvaluationStatus.INVALID,
        calibration_version="wca_confidence_calibration_disabled_v1",
        base_weight=getattr(strategy, "base_weight"),
        effective_weight=0,
        contribution=0,
        reason_codes=(reason_code,),
        explanation=explanation,
    )


def completed_candles(snapshot: WcaMarketSnapshot) -> tuple[WcaCandle, ...]:
    return tuple(sorted(snapshot.candles, key=lambda candle: candle.timestamp))


def eastern_minutes(timestamp: datetime) -> int:
    local = timestamp.astimezone(eastern_timezone(timestamp))
    return local.hour * 60 + local.minute


def outside_regular_session(snapshot: WcaMarketSnapshot) -> bool:
    minutes = eastern_minutes(snapshot.data_timestamp)
    return minutes < 9 * 60 + 30 or minutes >= 16 * 60


def same_session_candles(candles: tuple[WcaCandle, ...], timestamp: datetime) -> tuple[WcaCandle, ...]:
    day = timestamp.astimezone(eastern_timezone(timestamp)).date()
    return tuple(c for c in candles if c.timestamp.astimezone(eastern_timezone(c.timestamp)).date() == day and 9 * 60 + 30 <= eastern_minutes(c.timestamp) < 16 * 60)


def previous_regular_close(candles: tuple[WcaCandle, ...], timestamp: datetime) -> float | None:
    day = timestamp.astimezone(eastern_timezone(timestamp)).date()
    prior = tuple(c for c in candles if c.timestamp.astimezone(eastern_timezone(c.timestamp)).date() < day and 9 * 60 + 30 <= eastern_minutes(c.timestamp) < 16 * 60)
    return prior[-1].close if prior else None


def eastern_timezone(timestamp: datetime) -> timezone:
    utc_timestamp = timestamp.astimezone(timezone.utc)
    year = utc_timestamp.year
    dst_start = nth_weekday_utc(year, 3, 6, 2, 7)
    dst_end = nth_weekday_utc(year, 11, 6, 1, 6)
    offset_hours = -4 if dst_start <= utc_timestamp < dst_end else -5
    return timezone(timedelta(hours=offset_hours))


def nth_weekday_utc(year: int, month: int, weekday: int, occurrence: int, local_hour_after_standard_midnight: int) -> datetime:
    first = datetime(year, month, 1, tzinfo=timezone.utc)
    days_until_weekday = (weekday - first.weekday()) % 7
    day = 1 + days_until_weekday + (occurrence - 1) * 7
    return datetime(year, month, day, local_hour_after_standard_midnight, tzinfo=timezone.utc)


def sma(candles: tuple[WcaCandle, ...], period: int) -> float:
    closes = tuple(c.close for c in candles[-period:])
    return sum(closes) / len(closes)


def average_volume(candles: tuple[WcaCandle, ...], period: int) -> float:
    if not candles:
        return 0
    values = tuple(c.volume for c in candles[-period:])
    return sum(values) / len(values)


def vwap(candles: tuple[WcaCandle, ...]) -> float:
    if candles[-1].vwap is not None:
        return candles[-1].vwap
    total_volume = sum(c.volume for c in candles)
    if total_volume <= 0:
        return sum(c.close for c in candles) / len(candles)
    return sum(((c.high + c.low + c.close) / 3) * c.volume for c in candles) / total_volume


def atr(candles: tuple[WcaCandle, ...], period: int) -> float:
    if len(candles) < 2:
        return 0
    selected = candles[-(period + 1):]
    ranges = tuple(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)) for previous, current in zip(selected, selected[1:]))
    return sum(ranges) / len(ranges) if ranges else 0


def rsi(closes: tuple[float, ...], period: int) -> float:
    deltas = tuple(closes[index] - closes[index - 1] for index in range(1, len(closes)))
    recent = deltas[-period:]
    gains = tuple(max(delta, 0) for delta in recent)
    losses = tuple(abs(min(delta, 0)) for delta in recent)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def standard_deviation(values: tuple[float, ...]) -> float:
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def strong_trend(candles: tuple[WcaCandle, ...]) -> bool:
    if len(candles) < 20:
        return False
    close = candles[-1].close
    return abs(sma(candles, 10) - sma(candles, 20)) / close > 0.004


def directional_expansion(candles: tuple[WcaCandle, ...], atr_value: float) -> bool:
    latest = candles[-1]
    direction = 1 if latest.close > latest.open else -1 if latest.close < latest.open else 0
    if direction == 0:
        return False
    prior_direction = 1 if candles[-2].close > candles[-2].open else -1 if candles[-2].close < candles[-2].open else 0
    return prior_direction == direction and (latest.high - latest.low) > atr_value * 2
