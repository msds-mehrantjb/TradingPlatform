from __future__ import annotations

from statistics import mean
from typing import Any


class PointInTimeIndicatorService:
    """Central point-in-time indicator calculations shared by feature and strategy code."""

    def close_series(self, candles: list[Any]) -> list[float]:
        return [float(_field(candle, "close")) for candle in candles]

    def ema_series(self, values: list[float], period: int) -> list[float | None]:
        if not values:
            return []
        alpha = 2 / (period + 1)
        result: list[float | None] = []
        ema_value: float | None = None
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

    def atr_series(self, candles: list[Any], period: int) -> list[float | None]:
        result: list[float | None] = []
        true_ranges: list[float] = []
        for index, candle in enumerate(candles):
            high = float(_field(candle, "high"))
            low = float(_field(candle, "low"))
            if index == 0:
                true_range = high - low
            else:
                previous_close = float(_field(candles[index - 1], "close"))
                true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
            true_ranges.append(true_range)
            result.append(mean(true_ranges[-period:]) if index >= period else None)
        return result

    def vwap_series(self, candles: list[Any]) -> list[float | None]:
        volume_total = 0.0
        price_volume_total = 0.0
        result: list[float | None] = []
        for candle in candles:
            volume = float(_field(candle, "volume"))
            typical = (
                float(_field(candle, "high"))
                + float(_field(candle, "low"))
                + float(_field(candle, "close"))
            ) / 3
            volume_total += volume
            price_volume_total += typical * volume
            result.append(price_volume_total / volume_total if volume_total else None)
        return result


def _field(candle: Any, name: str) -> Any:
    if isinstance(candle, dict):
        return candle[name]
    return getattr(candle, name)
