"""Regime indicator calculations owned by the backend runtime."""

from __future__ import annotations

from math import sqrt
from statistics import mean

from backend.app.algorithms.regime.contracts import RegimeCandle


def sma(values: list[float], period: int) -> float | None:
    return mean(values[-period:]) if len(values) >= period else None


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    current = mean(values[:period])
    for value in values[period:]:
        current = (value * alpha) + (current * (1 - alpha))
    return current


def atr(candles: tuple[RegimeCandle, ...], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    ranges: list[float] = []
    for index in range(1, len(candles)):
        candle = candles[index]
        previous = candles[index - 1]
        ranges.append(max(candle.high - candle.low, abs(candle.high - previous.close), abs(candle.low - previous.close)))
    return mean(ranges[-period:])


def directional_movement(candles: tuple[RegimeCandle, ...], period: int = 14) -> dict[str, float | None]:
    if len(candles) < period + 1:
        return {"adx": None, "plusDi": None, "minusDi": None, "directionalMovementSpread": None}
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    true_ranges: list[float] = []
    for index in range(1, len(candles)):
        current = candles[index]
        previous = candles[index - 1]
        up_move = current.high - previous.high
        down_move = previous.low - current.low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        true_ranges.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
    recent_tr = sum(true_ranges[-period:])
    if recent_tr <= 0:
        return {"adx": 0.0, "plusDi": 0.0, "minusDi": 0.0, "directionalMovementSpread": 0.0}
    plus_di = 100 * sum(plus_dm[-period:]) / recent_tr
    minus_di = 100 * sum(minus_dm[-period:]) / recent_tr
    dx_values: list[float] = []
    for offset in range(period, len(true_ranges) + 1):
        window_tr = sum(true_ranges[offset - period : offset])
        if window_tr <= 0:
            continue
        window_plus = 100 * sum(plus_dm[offset - period : offset]) / window_tr
        window_minus = 100 * sum(minus_dm[offset - period : offset]) / window_tr
        denominator = window_plus + window_minus
        dx_values.append(0.0 if denominator <= 0 else 100 * abs(window_plus - window_minus) / denominator)
    adx = mean(dx_values[-period:]) if dx_values else 0.0
    return {
        "adx": adx,
        "plusDi": plus_di,
        "minusDi": minus_di,
        "directionalMovementSpread": (plus_di - minus_di) / 100,
    }


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for left, right in zip(values[-period - 1 : -1], values[-period:]):
        delta = right - left
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_loss = mean(losses)
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + (mean(gains) / avg_loss)))


def realized_volatility(values: list[float], period: int = 20) -> float | None:
    if len(values) < period + 1:
        return None
    returns = [(values[i] - values[i - 1]) / max(values[i - 1], 0.01) for i in range(len(values) - period, len(values))]
    avg = mean(returns)
    variance = mean([(item - avg) ** 2 for item in returns])
    return sqrt(variance)


def efficiency_ratio(values: list[float], period: int = 20) -> float | None:
    if len(values) < period + 1:
        return None
    net_change = abs(values[-1] - values[-period - 1])
    path_length = sum(abs(values[index] - values[index - 1]) for index in range(len(values) - period, len(values)))
    if path_length <= 0:
        return 0.0
    return max(0.0, min(1.0, net_change / path_length))


def rate_of_change(values: list[float], period: int = 10) -> float | None:
    if len(values) < period + 1:
        return None
    previous = values[-period - 1]
    return (values[-1] - previous) / max(previous, 0.01)


def vwap(candles: tuple[RegimeCandle, ...]) -> float:
    explicit = [candle.vwap for candle in candles if candle.vwap is not None]
    if explicit:
        return float(explicit[-1])
    volume = sum(max(0, candle.volume) for candle in candles)
    if volume <= 0:
        return candles[-1].close
    return sum(((candle.high + candle.low + candle.close) / 3) * max(0, candle.volume) for candle in candles) / volume


def relative_volume(candles: tuple[RegimeCandle, ...], period: int = 20) -> float:
    if len(candles) < 2:
        return 1.0
    baseline = candles[-period - 1 : -1] if len(candles) > period else candles[:-1]
    avg_volume = mean([max(1.0, candle.volume) for candle in baseline]) if baseline else max(1.0, candles[-1].volume)
    return candles[-1].volume / max(avg_volume, 1.0)


def macd_histogram(values: list[float]) -> float | None:
    fast = ema(values, 12)
    slow = ema(values, 26)
    if fast is None or slow is None:
        return None
    return fast - slow


def macd_histogram_slope(values: list[float]) -> float | None:
    if len(values) < 27:
        return None
    current = macd_histogram(values)
    previous = macd_histogram(values[:-1])
    if current is None or previous is None:
        return None
    return current - previous
