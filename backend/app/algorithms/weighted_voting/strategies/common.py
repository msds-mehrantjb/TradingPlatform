"""Pure strategy helpers for Weighted Voting rule modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from math import isfinite

from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingCandle, WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedDataQualityStatus, WeightedSide, WeightedVotingSignal, WeightedVotingStrategyFamily


SESSION_OPEN = time(9, 30)


@dataclass(frozen=True)
class StrategyContext:
    snapshot: WeightedVotingMarketSnapshot
    candles: tuple[WeightedVotingCandle, ...]
    latest: WeightedVotingCandle
    config: WeightedVotingConfig


@dataclass(frozen=True)
class OpeningRange:
    high: float
    low: float
    candles: tuple[WeightedVotingCandle, ...]


def completed_context(snapshot: WeightedVotingMarketSnapshot, config: WeightedVotingConfig) -> StrategyContext | None:
    candles = tuple(sorted((candle for candle in snapshot.one_minute_candles if candle.timestamp <= snapshot.data_timestamp), key=lambda item: item.timestamp))
    if not candles:
        return None
    return StrategyContext(snapshot=snapshot, candles=candles, latest=candles[-1], config=config)


def is_stale(context: StrategyContext) -> bool:
    return (context.snapshot.data_timestamp - context.latest.timestamp).total_seconds() > context.config.stale_after_seconds


def eastern_minutes(timestamp: datetime) -> int:
    local = eastern_datetime(timestamp)
    return local.hour * 60 + local.minute


def eastern_datetime(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        aware = timestamp.replace(tzinfo=timezone.utc)
    else:
        aware = timestamp.astimezone(timezone.utc)
    offset_hours = -4 if _is_us_eastern_dst(aware) else -5
    return aware.astimezone(timezone(timedelta(hours=offset_hours)))


def in_window(timestamp: datetime, start: str, end: str) -> bool:
    minutes = eastern_minutes(timestamp)
    start_minutes = _parse_hhmm(start)
    end_minutes = _parse_hhmm(end)
    return start_minutes <= minutes <= end_minutes


def regular_session_candles(candles: tuple[WeightedVotingCandle, ...]) -> tuple[WeightedVotingCandle, ...]:
    regular = [candle for candle in candles if in_window(candle.timestamp, "09:30", "16:00")]
    if not regular:
        return ()
    latest_day = eastern_datetime(regular[-1].timestamp).date()
    return tuple(candle for candle in regular if eastern_datetime(candle.timestamp).date() == latest_day)


def opening_range(candles: tuple[WeightedVotingCandle, ...], minutes: int) -> OpeningRange | None:
    session = regular_session_candles(candles)
    opening = tuple(candle for candle in session if eastern_minutes(candle.timestamp) < _parse_hhmm("09:30") + minutes)
    if len(opening) < max(3, minutes // 2):
        return None
    return OpeningRange(high=max(candle.high for candle in opening), low=min(candle.low for candle in opening), candles=opening)


def simple_moving_average(values: list[float], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    return sum(values[-window:]) / window


def vwap(candles: tuple[WeightedVotingCandle, ...]) -> float | None:
    total_volume = sum(candle.volume for candle in candles)
    if total_volume <= 0:
        return None
    return sum(((candle.high + candle.low + candle.close) / 3) * candle.volume for candle in candles) / total_volume


def average_true_range(candles: tuple[WeightedVotingCandle, ...], window: int = 14) -> float | None:
    if len(candles) < 2:
        return None
    ranges: list[float] = []
    comparable = candles[-(window + 1):]
    for previous, current in zip(comparable, comparable[1:]):
        ranges.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
    if not ranges:
        return None
    return sum(ranges) / len(ranges)


def bollinger(values: list[float], window: int = 20, deviations: float = 2.0) -> tuple[float, float, float] | None:
    if len(values) < window:
        return None
    sample = values[-window:]
    mean = sum(sample) / len(sample)
    variance = sum((value - mean) ** 2 for value in sample) / len(sample)
    stdev = variance ** 0.5
    return mean, mean + deviations * stdev, mean - deviations * stdev


def average_volume(candles: tuple[WeightedVotingCandle, ...], window: int = 20) -> float:
    sample = candles[-window:] if len(candles) >= window else candles
    if not sample:
        return 0.0
    return sum(candle.volume for candle in sample) / len(sample)


def slope(values: list[float], lookback: int) -> float:
    if len(values) <= lookback:
        return 0.0
    base = values[-1 - lookback]
    if base <= 0:
        return 0.0
    return (values[-1] - base) / base


def body_ratio(candle: WeightedVotingCandle) -> float:
    full_range = max(candle.high - candle.low, 0.000001)
    return abs(candle.close - candle.open) / full_range


def upper_wick_ratio(candle: WeightedVotingCandle) -> float:
    full_range = max(candle.high - candle.low, 0.000001)
    return (candle.high - max(candle.open, candle.close)) / full_range


def lower_wick_ratio(candle: WeightedVotingCandle) -> float:
    full_range = max(candle.high - candle.low, 0.000001)
    return (min(candle.open, candle.close) - candle.low) / full_range


def trend_strength(candles: tuple[WeightedVotingCandle, ...]) -> float:
    closes = [candle.close for candle in candles]
    if len(closes) < 20:
        return 0.0
    atr = average_true_range(candles, 14) or 0.0
    normalized_slope = abs(slope(closes, min(20, len(closes) - 1)))
    atr_ratio = atr / closes[-1] if closes[-1] else 0.0
    return normalized_slope + atr_ratio


def reject_bad_context(
    strategy_id: str,
    name: str,
    family: WeightedVotingStrategyFamily,
    snapshot: WeightedVotingMarketSnapshot,
    config: WeightedVotingConfig,
    minimum_warmup: int,
    window: tuple[str, str],
) -> StrategyContext | WeightedVotingSignal:
    context = completed_context(snapshot, config)
    if context is None or len(context.candles) < minimum_warmup:
        return hold_signal(
            strategy_id,
            name,
            family,
            snapshot.data_timestamp,
            False,
            ("weighted_voting.insufficient_data",),
            f"{name} needs at least {minimum_warmup} completed one-minute candles.",
            data_quality_status=WeightedDataQualityStatus.UNAVAILABLE,
            actual_data_freshness_seconds=None,
            required_data_freshness_seconds=config.stale_after_seconds,
        )
    if is_stale(context):
        return hold_signal(
            strategy_id,
            name,
            family,
            snapshot.data_timestamp,
            False,
            ("weighted_voting.stale_data",),
            f"{name} data is stale at {snapshot.data_timestamp.isoformat()}.",
            data_quality_status=WeightedDataQualityStatus.UNAVAILABLE,
            actual_data_freshness_seconds=data_freshness_seconds(context),
            required_data_freshness_seconds=config.stale_after_seconds,
        )
    if not in_window(context.latest.timestamp, window[0], window[1]):
        return hold_signal(
            strategy_id,
            name,
            family,
            snapshot.data_timestamp,
            True,
            ("weighted_voting.invalid_session",),
            f"{name} runs only from {window[0]} to {window[1]} ET.",
            data_quality_status=WeightedDataQualityStatus.UNAVAILABLE,
            actual_data_freshness_seconds=data_freshness_seconds(context),
            required_data_freshness_seconds=config.stale_after_seconds,
        )
    return context


def directional_signal(
    strategy_id: str,
    name: str,
    family: WeightedVotingStrategyFamily,
    side: WeightedSide,
    data_timestamp: datetime,
    confidence: float,
    expected_return: float,
    reason_codes: tuple[str, ...],
    explanation: str,
    *,
    data_quality_status: WeightedDataQualityStatus = WeightedDataQualityStatus.FULL,
    actual_data_freshness_seconds: float | None = 0.0,
    required_data_freshness_seconds: float = 300.0,
    invalidation_level: float | None = None,
) -> WeightedVotingSignal:
    confidence = clamp(confidence, 0.51, 0.86)
    opposite = max(0.04, (1.0 - confidence) * 0.28)
    hold = 1.0 - confidence - opposite
    if side == WeightedSide.BUY:
        p_buy, p_sell, p_hold = confidence, opposite, hold
    else:
        p_buy, p_sell, p_hold = opposite, confidence, hold
    p_buy = round(p_buy, 6)
    p_sell = round(p_sell, 6)
    p_hold = round(1.0 - p_buy - p_sell, 6)
    return WeightedVotingSignal(
        strategy_id=strategy_id,
        strategy_name=name,
        strategy_version=f"weighted_strategy_{strategy_id}_v1",
        family=family,
        signal=side,
        p_buy=p_buy,
        p_sell=p_sell,
        p_hold=p_hold,
        directional_confidence=round(confidence, 6),
        signal_strength=round(confidence, 6),
        expected_raw_movement=round(expected_return, 6),
        expected_return=round(expected_return, 6),
        expected_return_after_costs=round(expected_return * 0.85, 6),
        strength=round(confidence, 6),
        final_weight=0.0,
        eligible=True,
        data_ready=True,
        required_data_freshness_seconds=required_data_freshness_seconds,
        actual_data_freshness_seconds=actual_data_freshness_seconds,
        data_quality_status=data_quality_status,
        invalidation_level=invalidation_level,
        data_timestamp=data_timestamp,
        reason_codes=reason_codes,
        explanation=explanation,
    )


def hold_signal(
    strategy_id: str,
    name: str,
    family: WeightedVotingStrategyFamily,
    data_timestamp: datetime,
    data_ready: bool,
    reason_codes: tuple[str, ...],
    explanation: str,
    confidence: float = 0.72,
    data_quality_status: WeightedDataQualityStatus = WeightedDataQualityStatus.FULL,
    actual_data_freshness_seconds: float | None = 0.0,
    required_data_freshness_seconds: float = 300.0,
    invalidation_level: float | None = None,
) -> WeightedVotingSignal:
    if not data_ready:
        data_quality_status = WeightedDataQualityStatus.UNAVAILABLE
    return WeightedVotingSignal(
        strategy_id=strategy_id,
        strategy_name=name,
        strategy_version=f"weighted_strategy_{strategy_id}_v1",
        family=family,
        signal=WeightedSide.HOLD,
        p_buy=0.0,
        p_sell=0.0,
        p_hold=1.0,
        directional_confidence=0.0,
        signal_strength=0.0,
        expected_raw_movement=0.0,
        expected_return=0.0,
        expected_return_after_costs=0.0,
        strength=0.0,
        final_weight=0.0,
        eligible=False,
        data_ready=data_ready,
        required_data_freshness_seconds=required_data_freshness_seconds,
        actual_data_freshness_seconds=actual_data_freshness_seconds,
        data_quality_status=data_quality_status,
        invalidation_level=invalidation_level,
        data_timestamp=data_timestamp,
        reason_codes=reason_codes,
        explanation=explanation,
    )


def clamp(value: float, minimum: float, maximum: float) -> float:
    if not isfinite(value):
        return minimum
    return max(minimum, min(maximum, value))


def data_freshness_seconds(context: StrategyContext) -> float:
    return max(0.0, (context.snapshot.data_timestamp - context.latest.timestamp).total_seconds())


def _parse_hhmm(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _is_us_eastern_dst(timestamp_utc: datetime) -> bool:
    year = timestamp_utc.year
    march_start = _nth_weekday_utc(year, 3, 6, 2, 7)
    november_end = _nth_weekday_utc(year, 11, 6, 1, 6)
    return march_start <= timestamp_utc.replace(tzinfo=timezone.utc) < november_end


def _nth_weekday_utc(year: int, month: int, weekday: int, occurrence: int, hour_utc: int) -> datetime:
    day = datetime(year, month, 1, hour_utc, tzinfo=timezone.utc)
    days_until = (weekday - day.weekday()) % 7
    return day + timedelta(days=days_until + 7 * (occurrence - 1))
