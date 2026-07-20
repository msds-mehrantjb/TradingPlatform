"""Pure point-in-time indicator helpers for Meta-Strategy snapshots."""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime, timedelta
from statistics import mean, pstdev
from typing import Any


def completed_candles(
    candles: Iterable[Any],
    decision_timestamp: datetime,
    *,
    finalization_lag_seconds: int = 0,
) -> tuple[Any, ...]:
    cutoff = decision_timestamp - timedelta(seconds=max(0, finalization_lag_seconds))
    return tuple(
        sorted(
            (candle for candle in candles if bar_end_timestamp(candle) <= cutoff),
            key=lambda candle: timestamp_value(candle, "timestamp"),
        )
    )


def latest_at_or_before(rows: Iterable[Any], decision_timestamp: datetime) -> Any | None:
    eligible = [row for row in rows if timestamp_value(row, "timestamp") <= decision_timestamp]
    return max(eligible, key=lambda row: timestamp_value(row, "timestamp")) if eligible else None


def bar_end_timestamp(candle: Any) -> datetime:
    return timestamp_value(candle, "timestamp") + timeframe_duration(str(field_value(candle, "timeframe", "1Min")))


def timeframe_duration(timeframe: str) -> timedelta:
    normalized = timeframe.lower()
    if normalized in {"5min", "5m", "five_minute"}:
        return timedelta(minutes=5)
    if normalized in {"15min", "15m", "fifteen_minute"}:
        return timedelta(minutes=15)
    return timedelta(minutes=1)


def vwap(candles: Iterable[Any]) -> float | None:
    volume_total = 0.0
    price_volume_total = 0.0
    for candle in candles:
        volume = numeric_field(candle, "volume")
        typical = (numeric_field(candle, "high") + numeric_field(candle, "low") + numeric_field(candle, "close")) / 3
        volume_total += volume
        price_volume_total += typical * volume
    return price_volume_total / volume_total if volume_total else None


def sma(values: Iterable[float], period: int) -> float | None:
    ready = list(values)
    if len(ready) < period:
        return None
    return mean(ready[-period:])


def ema_series(values: Iterable[float], period: int) -> tuple[float | None, ...]:
    ready = list(values)
    if not ready:
        return ()
    alpha = 2 / (period + 1)
    result: list[float | None] = []
    ema_value: float | None = None
    for index, value in enumerate(ready):
        if index + 1 < period:
            result.append(None)
            continue
        if ema_value is None:
            ema_value = mean(ready[index + 1 - period : index + 1])
        else:
            ema_value = (value * alpha) + (ema_value * (1 - alpha))
        result.append(ema_value)
    return tuple(result)


def ema(values: Iterable[float], period: int) -> float | None:
    return last_ready(ema_series(values, period))


def atr(candles: Iterable[Any], period: int = 14) -> float | None:
    rows = list(candles)
    if len(rows) <= period:
        return None
    true_ranges: list[float] = []
    for index, candle in enumerate(rows):
        high = numeric_field(candle, "high")
        low = numeric_field(candle, "low")
        if index == 0:
            true_range = high - low
        else:
            previous_close = numeric_field(rows[index - 1], "close")
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(true_range)
    return mean(true_ranges[-period:])


def adx(candles: Iterable[Any], period: int = 14) -> float | None:
    rows = list(candles)
    if len(rows) <= period * 2:
        return None
    dx_values: list[float] = []
    for index in range(1, len(rows)):
        current = rows[index]
        previous = rows[index - 1]
        up_move = numeric_field(current, "high") - numeric_field(previous, "high")
        down_move = numeric_field(previous, "low") - numeric_field(current, "low")
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0
        true_range = max(
            numeric_field(current, "high") - numeric_field(current, "low"),
            abs(numeric_field(current, "high") - numeric_field(previous, "close")),
            abs(numeric_field(current, "low") - numeric_field(previous, "close")),
        )
        if true_range <= 0:
            dx_values.append(0.0)
            continue
        plus_di = 100 * (plus_dm / true_range)
        minus_di = 100 * (minus_dm / true_range)
        denominator = plus_di + minus_di
        dx_values.append(0.0 if denominator == 0 else 100 * abs(plus_di - minus_di) / denominator)
    return mean(dx_values[-period:]) if len(dx_values) >= period else None


def rsi(values: Iterable[float], period: int = 14) -> float | None:
    ready = list(values)
    if len(ready) <= period:
        return None
    gains = []
    losses = []
    for index in range(len(ready) - period, len(ready)):
        change = ready[index] - ready[index - 1]
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    average_loss = mean(losses)
    if average_loss == 0:
        return 100.0
    return 100 - (100 / (1 + (mean(gains) / average_loss)))


def macd(values: Iterable[float]) -> dict[str, float] | None:
    ready = list(values)
    ema12 = [value for value in ema_series(ready, 12) if value is not None]
    ema26 = [value for value in ema_series(ready, 26) if value is not None]
    if not ema12 or not ema26:
        return None
    aligned = min(len(ema12), len(ema26))
    macd_values = [ema12[-aligned + index] - ema26[-aligned + index] for index in range(aligned)]
    signal_values = [value for value in ema_series(macd_values, 9) if value is not None]
    if not signal_values:
        return None
    macd_value = macd_values[-1]
    signal_value = signal_values[-1]
    return {"macd": macd_value, "signal": signal_value, "histogram": macd_value - signal_value}


def bollinger_bands(values: Iterable[float], period: int = 20, deviations: float = 2.0) -> dict[str, float] | None:
    ready = list(values)
    if len(ready) < period:
        return None
    sample = ready[-period:]
    middle = mean(sample)
    deviation = pstdev(sample)
    return {"upper": middle + deviation * deviations, "middle": middle, "lower": middle - deviation * deviations}


def relative_volume(candles: Iterable[Any], period: int = 20) -> float | None:
    volumes = [numeric_field(candle, "volume") for candle in candles]
    if len(volumes) <= period:
        return None
    baseline = mean(volumes[-period - 1 : -1])
    return volumes[-1] / baseline if baseline else None


def spread_dollars(quote: Any | None) -> float | None:
    if quote is None:
        return None
    return max(0.0, numeric_field(quote, "ask") - numeric_field(quote, "bid"))


def spread_bps(quote: Any | None) -> float | None:
    spread = spread_dollars(quote)
    if spread is None:
        return None
    midpoint = (numeric_field(quote, "ask") + numeric_field(quote, "bid")) / 2
    return (spread / midpoint) * 10_000 if midpoint > 0 else None


def liquidity_state(candles: Iterable[Any], quote: Any | None, *, relative_volume_value: float | None = None) -> dict[str, Any]:
    rows = list(candles)
    latest_volume = numeric_field(rows[-1], "volume") if rows else 0.0
    bps = spread_bps(quote)
    score = 1.0
    if bps is None:
        score -= 0.3
    elif bps > 20:
        score -= 0.5
    elif bps > 8:
        score -= 0.2
    if latest_volume < 10_000:
        score -= 0.3
    if relative_volume_value is not None and relative_volume_value < 0.5:
        score -= 0.2
    score = max(0.0, min(1.0, score))
    level = "good" if score >= 0.75 else "reduced" if score >= 0.45 else "poor"
    return {"level": level, "score": score, "latestVolume": latest_volume, "spreadBps": bps}


def session_phase(decision_timestamp: datetime) -> str:
    minutes = (decision_timestamp.hour * 60) + decision_timestamp.minute
    if minutes < (14 * 60 + 30) or minutes >= (21 * 60):
        return "outside_session"
    if minutes < (15 * 60):
        return "opening"
    if minutes < (17 * 60):
        return "morning"
    if minutes < (19 * 60):
        return "midday"
    if minutes < (20 * 60 + 30):
        return "afternoon"
    return "closing"


def gap_state(candles: Iterable[Any], prior_close: float | None) -> dict[str, Any]:
    rows = list(candles)
    if not rows or prior_close is None or prior_close <= 0:
        return {"state": "unknown", "gapPercent": None}
    gap_percent = ((numeric_field(rows[0], "open") - prior_close) / prior_close) * 100
    if gap_percent >= 0.75:
        state = "gap_up"
    elif gap_percent <= -0.75:
        state = "gap_down"
    else:
        state = "flat_open"
    return {"state": state, "gapPercent": gap_percent}


def relative_strength_context(spy: Any | None, qqq: Any | None, iwm: Any | None) -> dict[str, Any]:
    return {
        "qqqClose": numeric_field(qqq, "close") if qqq is not None else None,
        "iwmClose": numeric_field(iwm, "close") if iwm is not None else None,
        "spyVsQqq": _relative_strength(spy, qqq),
        "spyVsIwm": _relative_strength(spy, iwm),
    }


def breadth_state(components: dict[str, Any]) -> dict[str, Any]:
    returns = {
        symbol: (numeric_field(candle, "close") - numeric_field(candle, "open")) / numeric_field(candle, "open")
        for symbol, candle in components.items()
        if numeric_field(candle, "open") > 0
    }
    average_return = mean(returns.values()) if returns else None
    return {
        "componentCount": len(components),
        "averageReturn": average_return,
        "positiveShare": sum(1 for value in returns.values() if value > 0) / len(returns) if returns else None,
        "components": returns,
    }


def close_values(candles: Iterable[Any]) -> tuple[float, ...]:
    return tuple(numeric_field(candle, "close") for candle in candles)


def numeric_field(row: Any, name: str) -> float:
    value = field_value(row, name)
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    return numeric


def timestamp_value(row: Any, name: str) -> datetime:
    value = field_value(row, name)
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def field_value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def last_ready(values: Iterable[float | None]) -> float | None:
    ready = [value for value in values if value is not None]
    return ready[-1] if ready else None


def _relative_strength(spy: Any | None, other: Any | None) -> float | None:
    if spy is None or other is None or numeric_field(other, "close") <= 0:
        return None
    return numeric_field(spy, "close") / numeric_field(other, "close")
