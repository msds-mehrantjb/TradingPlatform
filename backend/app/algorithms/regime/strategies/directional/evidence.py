"""Shared point-in-time evidence helpers for Regime directional strategies."""

from __future__ import annotations

from dataclasses import asdict
from statistics import mean
from typing import Any

from backend.app.algorithms.regime.contracts import RegimeCandle, RegimeClassification, RegimeMarketSnapshot
from backend.app.algorithms.regime.exchange_calendar import exchange_session
from backend.app.algorithms.regime.indicators import (
    atr,
    directional_movement,
    efficiency_ratio,
    ema,
    macd_histogram,
    macd_histogram_slope,
    relative_volume,
    rsi,
    vwap,
)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def settings_payload(settings: Any) -> dict[str, Any]:
    return asdict(settings) if hasattr(settings, "__dataclass_fields__") else dict(settings or {})


def closes(snapshot: RegimeMarketSnapshot) -> list[float]:
    return [candle.close for candle in snapshot.candles]


def current_vwap(snapshot: RegimeMarketSnapshot, classification: RegimeClassification) -> float | None:
    raw = classification.features.get("vwap")
    return float(raw) if raw is not None else vwap(snapshot.candles)


def current_atr(snapshot: RegimeMarketSnapshot) -> float | None:
    return atr(snapshot.candles, 14)


def atr_distance(snapshot: RegimeMarketSnapshot, left: float, right: float) -> float | None:
    current = current_atr(snapshot)
    if current is None or current <= 0:
        return None
    return abs(left - right) / current


def ema_value(snapshot: RegimeMarketSnapshot, period: int) -> float | None:
    return ema(closes(snapshot), period)


def ema_slope(snapshot: RegimeMarketSnapshot, period: int, bars: int = 5) -> float | None:
    values = closes(snapshot)
    if len(values) < period + bars:
        return None
    current = ema(values, period)
    previous = ema(values[:-bars], period)
    if current is None or previous is None:
        return None
    return (current - previous) / max(snapshot.latest.close, 0.01)


def vwap_slope(snapshot: RegimeMarketSnapshot, bars: int = 5) -> float | None:
    if len(snapshot.candles) <= bars:
        return None
    current = vwap(snapshot.candles)
    previous = vwap(snapshot.candles[:-bars])
    return (current - previous) / max(snapshot.latest.close, 0.01)


def trend_evidence(snapshot: RegimeMarketSnapshot, classification: RegimeClassification) -> dict[str, Any]:
    close = snapshot.latest.close
    ema20 = ema_value(snapshot, 20)
    ema50 = ema_value(snapshot, 50)
    slope20 = ema_slope(snapshot, 20)
    slope50 = ema_slope(snapshot, 50)
    vw = current_vwap(snapshot, classification)
    movement = directional_movement(snapshot.candles)
    efficiency = efficiency_ratio(closes(snapshot))
    missing = [
        name
        for name, value in {
            "ema20": ema20,
            "ema50": ema50,
            "ema20Slope": slope20,
            "ema50Slope": slope50,
            "vwap": vw,
            "adx": movement.get("adx"),
            "plusDi": movement.get("plusDi"),
            "minusDi": movement.get("minusDi"),
        }.items()
        if value is None
    ]
    direction = "none"
    if not missing:
        if ema20 > ema50 and slope20 > 0 and slope50 >= 0 and close > vw and movement["plusDi"] > movement["minusDi"]:
            direction = "up"
        elif ema20 < ema50 and slope20 < 0 and slope50 <= 0 and close < vw and movement["minusDi"] > movement["plusDi"]:
            direction = "down"
    return {
        "close": close,
        "ema20": ema20,
        "ema50": ema50,
        "ema20Slope": slope20,
        "ema50Slope": slope50,
        "vwap": vw,
        "adx": movement.get("adx"),
        "plusDi": movement.get("plusDi"),
        "minusDi": movement.get("minusDi"),
        "efficiencyRatio": efficiency,
        "direction": direction,
        "higherTimeframePermission": _higher_timeframe_permission(snapshot, direction),
        "missingInputs": tuple(missing),
    }


def _higher_timeframe_permission(snapshot: RegimeMarketSnapshot, direction: str) -> bool:
    if direction == "none":
        return False
    source = snapshot.five_minute_candles or snapshot.candles
    values = [item.close for item in source]
    if len(values) < 20:
        return True
    slope = values[-1] - values[-min(12, len(values))]
    return slope > 0 if direction == "up" else slope < 0


def extension_atr(snapshot: RegimeMarketSnapshot, reference: float | None) -> float | None:
    if reference is None:
        return None
    return atr_distance(snapshot, snapshot.latest.close, reference)


def relative_vol(snapshot: RegimeMarketSnapshot) -> float:
    return relative_volume(snapshot.candles)


def recent_volume_contraction(snapshot: RegimeMarketSnapshot, lookback: int = 5, baseline: int = 20) -> bool | None:
    if len(snapshot.candles) < baseline + lookback:
        return None
    pullback = mean(max(1.0, c.volume) for c in snapshot.candles[-lookback:])
    base = mean(max(1.0, c.volume) for c in snapshot.candles[-baseline - lookback : -lookback])
    return pullback <= base


def confirmation_candle(snapshot: RegimeMarketSnapshot, direction: str) -> bool:
    latest = snapshot.latest
    body_up = latest.close > latest.open
    body_down = latest.close < latest.open
    mid = (latest.high + latest.low) / 2
    return bool((direction == "up" and body_up and latest.close >= mid) or (direction == "down" and body_down and latest.close <= mid))


def rolling_reference(snapshot: RegimeMarketSnapshot, lookback: int = 20) -> dict[str, float | None]:
    if len(snapshot.candles) <= lookback:
        return {"high": None, "low": None}
    window = snapshot.candles[-lookback - 1 : -1]
    return {"high": max(c.high for c in window), "low": min(c.low for c in window)}


def opening_range(snapshot: RegimeMarketSnapshot, minutes: int = 30) -> dict[str, Any]:
    if len(snapshot.one_minute_candles) < minutes + 1:
        return {"complete": False, "high": None, "low": None, "sessionStatus": None, "minutesFromOpen": None}
    session = exchange_session(snapshot.latest.timestamp)
    if session.minutes_from_open is None:
        return {"complete": False, "high": None, "low": None, "sessionStatus": session.status, "minutesFromOpen": None}
    early = tuple(snapshot.one_minute_candles[:minutes])
    return {
        "complete": session.minutes_from_open >= minutes,
        "high": max(c.high for c in early),
        "low": min(c.low for c in early),
        "sessionStatus": session.status,
        "minutesFromOpen": session.minutes_from_open,
    }


def range_expansion(snapshot: RegimeMarketSnapshot, lookback: int = 20) -> float | None:
    if len(snapshot.candles) <= lookback:
        return None
    latest_range = snapshot.latest.high - snapshot.latest.low
    baseline = mean(max(0.01, c.high - c.low) for c in snapshot.candles[-lookback - 1 : -1])
    return latest_range / max(baseline, 0.01)


def compression(snapshot: RegimeMarketSnapshot, lookback: int = 12, baseline: int = 30) -> float | None:
    if len(snapshot.candles) < lookback + baseline:
        return None
    recent = mean(max(0.01, c.high - c.low) for c in snapshot.candles[-lookback - 1 : -1])
    base = mean(max(0.01, c.high - c.low) for c in snapshot.candles[-lookback - baseline - 1 : -lookback - 1])
    return recent / max(base, 0.01)


def bollinger(snapshot: RegimeMarketSnapshot, period: int = 20, width: float = 2.0) -> dict[str, float | None]:
    values = closes(snapshot)
    if len(values) < period:
        return {"middle": None, "upper": None, "lower": None, "bandwidth": None, "zscore": None}
    window = values[-period:]
    middle = mean(window)
    variance = mean((item - middle) ** 2 for item in window)
    stdev = variance ** 0.5
    upper = middle + width * stdev
    lower = middle - width * stdev
    zscore = 0.0 if stdev <= 0 else (values[-1] - middle) / stdev
    return {"middle": middle, "upper": upper, "lower": lower, "bandwidth": (upper - lower) / max(middle, 0.01), "zscore": zscore}


def rsi_value(snapshot: RegimeMarketSnapshot) -> float | None:
    return rsi(closes(snapshot))


def macd_evidence(snapshot: RegimeMarketSnapshot) -> dict[str, float | None]:
    values = closes(snapshot)
    return {
        "histogram": macd_histogram(values),
        "slope": macd_histogram_slope(values),
        "previousHistogram": macd_histogram(values[:-1]) if len(values) > 27 else None,
    }


def swing_structure(snapshot: RegimeMarketSnapshot, lookback: int = 3) -> dict[str, Any]:
    candles = snapshot.candles
    if len(candles) < lookback * 4:
        return {"swingHighs": (), "swingLows": (), "state": "unknown", "missingInputs": ("swingHistory",)}
    highs: list[float] = []
    lows: list[float] = []
    for index in range(lookback, len(candles) - lookback):
        center = candles[index]
        left = candles[index - lookback : index]
        right = candles[index + 1 : index + lookback + 1]
        if center.high > max(c.high for c in left + right):
            highs.append(center.high)
        if center.low < min(c.low for c in left + right):
            lows.append(center.low)
    state = "mixed"
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            state = "up"
        elif highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            state = "down"
        elif abs(highs[-1] - highs[-2]) / max(highs[-2], 0.01) < 0.002 and abs(lows[-1] - lows[-2]) / max(lows[-2], 0.01) < 0.002:
            state = "range"
    latest = candles[-1]
    bos = "none"
    if highs and latest.close > highs[-1]:
        bos = "up"
    elif lows and latest.close < lows[-1]:
        bos = "down"
    return {"swingHighs": tuple(highs[-3:]), "swingLows": tuple(lows[-3:]), "state": state, "breakOfStructure": bos, "missingInputs": ()}


def previous_regular_close(snapshot: RegimeMarketSnapshot) -> float | None:
    raw = snapshot.context_feeds.get("previousRegularClose") or snapshot.context_feeds.get("previousClose")
    return float(raw) if raw is not None else None


def premarket_levels(snapshot: RegimeMarketSnapshot) -> dict[str, float | None]:
    levels = snapshot.context_feeds.get("marketStructureLevels") or {}
    return {
        "high": float(levels["premarketHigh"]) if levels.get("premarketHigh") is not None else None,
        "low": float(levels["premarketLow"]) if levels.get("premarketLow") is not None else None,
    }


def cost_bps(snapshot: RegimeMarketSnapshot, classification: RegimeClassification) -> float:
    liquidity = classification.evidence.get("liquidityEvidence", {})
    return float(liquidity.get("spreadBps") or snapshot.context_feeds.get("estimatedTransactionCostBps") or 2.0)


def expected_edge_bps(distance: float, price: float) -> float:
    return abs(distance) / max(price, 0.01) * 10_000
