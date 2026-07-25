"""Point-in-time feature calculations for Voting Ensemble snapshots."""

from __future__ import annotations

from statistics import mean, pstdev

from backend.app.algorithms.voting_ensemble.models import VotingCandle
from backend.app.algorithms.voting_ensemble.snapshot.models import SessionFeatureSnapshot


def session_features(candles: tuple[VotingCandle, ...]) -> SessionFeatureSnapshot:
    if not candles:
        return SessionFeatureSnapshot()
    vwap_values = [_vwap(candles[: index + 1]) for index in range(len(candles))]
    vwap = vwap_values[-1]
    prior_vwap = next((value for value in reversed(vwap_values[:-5]) if value is not None), None) if len(vwap_values) > 5 else None
    vwap_slope = None if vwap is None or prior_vwap is None else round(vwap - prior_vwap, 6)
    atr = _atr(candles)
    adx = _adx(candles)
    middle, upper, lower = _bollinger(candles)
    volume_average = mean(candle.volume for candle in candles[-20:]) if candles else None
    latest_volume = candles[-1].volume
    return SessionFeatureSnapshot(
        vwap=vwap,
        vwapSlope=vwap_slope,
        atr=atr,
        adx=adx,
        bollingerMiddle=middle,
        bollingerUpper=upper,
        bollingerLower=lower,
        volumeCurrent=latest_volume,
        volumeAverage20=round(volume_average, 6) if volume_average is not None else None,
        volumeRelative20=round(latest_volume / volume_average, 6) if volume_average and volume_average > 0 else None,
    )


def _vwap(candles: tuple[VotingCandle, ...]) -> float | None:
    total_volume = sum(candle.volume for candle in candles)
    if total_volume <= 0:
        return None
    value = sum(((candle.high + candle.low + candle.close) / 3.0) * candle.volume for candle in candles) / total_volume
    return round(value, 6)


def _atr(candles: tuple[VotingCandle, ...], period: int = 14) -> float | None:
    if len(candles) < 2:
        return None
    true_ranges: list[float] = []
    for previous, current in zip(candles[-period - 1 : -1], candles[-period:]):
        true_ranges.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
    if not true_ranges:
        return None
    return round(mean(true_ranges), 6)


def _adx(candles: tuple[VotingCandle, ...], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    true_ranges: list[float] = []
    for previous, current in zip(candles[-period - 1 : -1], candles[-period:]):
        up_move = current.high - previous.high
        down_move = previous.low - current.low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        true_ranges.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
    tr_sum = sum(true_ranges)
    if tr_sum <= 0:
        return 0.0
    plus_di = 100.0 * sum(plus_dm) / tr_sum
    minus_di = 100.0 * sum(minus_dm) / tr_sum
    denominator = plus_di + minus_di
    if denominator <= 0:
        return 0.0
    return round(100.0 * abs(plus_di - minus_di) / denominator, 6)


def _bollinger(candles: tuple[VotingCandle, ...], period: int = 20) -> tuple[float | None, float | None, float | None]:
    if len(candles) < period:
        return None, None, None
    closes = [candle.close for candle in candles[-period:]]
    middle = mean(closes)
    deviation = pstdev(closes)
    return round(middle, 6), round(middle + 2 * deviation, 6), round(middle - 2 * deviation, 6)

