"""Helpers for snapshot-native directional strategies."""

from __future__ import annotations

from statistics import mean

from backend.app.algorithms.voting_ensemble.models import VotingCandle
from backend.app.algorithms.voting_ensemble.snapshot.models import LevelSnapshot, VotingEnsembleEvaluationSnapshot


def spy_candles(snapshot: VotingEnsembleEvaluationSnapshot) -> tuple[VotingCandle, ...]:
    return tuple(item.candle for item in snapshot.spyOneMinuteCandles)


def five_minute_candles(snapshot: VotingEnsembleEvaluationSnapshot) -> tuple[VotingCandle, ...]:
    return tuple(item.candle for item in snapshot.aggregatedFiveMinuteEvidence.candles)


def fifteen_minute_candles(snapshot: VotingEnsembleEvaluationSnapshot) -> tuple[VotingCandle, ...]:
    return tuple(item.candle for item in snapshot.aggregatedFifteenMinuteEvidence.candles)


def trend_score(candles: tuple[VotingCandle, ...], lookback: int) -> float:
    window = candles[-lookback:]
    if len(window) < 2:
        return 0.0
    start = window[0].close
    end = window[-1].close
    avg_range = mean(max(0.01, candle.high - candle.low) for candle in window)
    return max(-1.0, min(1.0, (end - start) / max(avg_range * len(window), 0.01)))


def latest_close(snapshot: VotingEnsembleEvaluationSnapshot) -> float:
    candles = spy_candles(snapshot)
    return candles[-1].close if candles else 0.0


def reference_highs(snapshot: VotingEnsembleEvaluationSnapshot) -> tuple[tuple[str, float], ...]:
    levels = []
    for label, source in (
        ("prior_day_high", snapshot.priorDayLevels),
        ("premarket_high", snapshot.premarketLevels),
        ("opening_range_high", snapshot.openingRangeLevels),
    ):
        if source.high and source.high > 0:
            levels.append((label, source.high))
    return tuple(levels)


def reference_lows(snapshot: VotingEnsembleEvaluationSnapshot) -> tuple[tuple[str, float], ...]:
    levels = []
    for label, source in (
        ("prior_day_low", snapshot.priorDayLevels),
        ("premarket_low", snapshot.premarketLevels),
        ("opening_range_low", snapshot.openingRangeLevels),
    ):
        if source.low and source.low > 0:
            levels.append((label, source.low))
    return tuple(levels)


def candle_range(candle: VotingCandle) -> float:
    return max(0.0, candle.high - candle.low)


def close_location(candle: VotingCandle) -> float:
    spread = candle_range(candle)
    if spread <= 0:
        return 0.5
    return max(0.0, min(1.0, (candle.close - candle.low) / spread))


def lower_wick_ratio(candle: VotingCandle) -> float:
    spread = candle_range(candle)
    if spread <= 0:
        return 0.0
    return max(0.0, min(candle.open, candle.close) - candle.low) / spread


def upper_wick_ratio(candle: VotingCandle) -> float:
    spread = candle_range(candle)
    if spread <= 0:
        return 0.0
    return max(0.0, candle.high - max(candle.open, candle.close)) / spread


def level_values(levels: LevelSnapshot) -> tuple[float, ...]:
    return tuple(value for value in (levels.high, levels.low, levels.open, levels.close) if value and value > 0)

