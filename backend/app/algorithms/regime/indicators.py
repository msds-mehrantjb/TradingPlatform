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

