"""Backend-owned Regime exit policy."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def evaluate_regime_exit(position: dict | None, candle: dict, confirmed_regime: str, settings: dict | None = None) -> dict:
    if not position:
        return {"action": "hold", "reasonCodes": ()}
    settings = settings or {}
    side = position.get("side", "Long")
    high = float(candle.get("high", candle.get("close", 0)))
    low = float(candle.get("low", candle.get("close", 0)))
    close = float(candle.get("close", 0))
    stop = float(position.get("stopPrice") or position.get("stop_price") or close)
    target = float(position.get("targetPrice") or position.get("target_price") or close)
    if side == "Long":
        if low <= stop:
            return {"action": "exit_long", "price": stop, "reasonCodes": ("regime.exit.stop_hit",)}
        if high >= target:
            return {"action": "exit_long", "price": target, "reasonCodes": ("regime.exit.target_hit",)}
    else:
        if high >= stop:
            return {"action": "exit_short", "price": stop, "reasonCodes": ("regime.exit.stop_hit",)}
        if low <= target:
            return {"action": "exit_short", "price": target, "reasonCodes": ("regime.exit.target_hit",)}
    if confirmed_regime in {"event_risk", "liquidity_stress", "extreme_volatility_no_trade"}:
        return {"action": "reduce_or_exit", "price": close, "reasonCodes": ("regime.exit.risk_off_regime",)}
    if position.get("signalInvalidated") or position.get("invalidatedBySignal"):
        return {"action": "exit_long" if side == "Long" else "exit_short", "price": close, "reasonCodes": ("regime.exit.signal_invalidated",)}
    if _maximum_holding_bars_reached(position, candle, settings):
        return {"action": "exit_long" if side == "Long" else "exit_short", "price": close, "reasonCodes": ("regime.exit.maximum_holding_time",)}
    if _flatten_time_reached(candle, settings):
        return {"action": "exit_long" if side == "Long" else "exit_short", "price": close, "reasonCodes": ("regime.exit.end_of_day_flatten",)}
    if bool(settings.get("trailingExitsEnabled")) or bool((settings.get("exit_policy") or settings.get("exitPolicy") or {}).get("trailingExitsEnabled")):
        distance = float((settings.get("exit_policy") or settings.get("exitPolicy") or {}).get("trailingStopDistance") or settings.get("trailingStopDistance") or abs(float(position.get("averageFillPrice") or close) - stop) or 0.01)
        if side == "Long":
            trailed_stop = max(stop, high - distance)
            if low <= trailed_stop:
                return {"action": "exit_long", "price": trailed_stop, "reasonCodes": ("regime.exit.trailing_stop_hit",)}
        else:
            trailed_stop = min(stop, low + distance)
            if high >= trailed_stop:
                return {"action": "exit_short", "price": trailed_stop, "reasonCodes": ("regime.exit.trailing_stop_hit",)}
    return {"action": "hold", "reasonCodes": ()}


def _maximum_holding_bars_reached(position: dict, candle: dict, settings: dict) -> bool:
    maximum = _int_setting(settings, "maximumHoldingBars", "maxHoldingBars")
    if maximum <= 0:
        return False
    entry_index = _optional_int(position.get("entryBarIndex") or position.get("entry_bar_index"))
    current_index = _optional_int(candle.get("barIndex") or candle.get("bar_index"))
    if entry_index is None or current_index is None:
        return False
    return current_index - entry_index >= maximum


def _flatten_time_reached(candle: dict, settings: dict) -> bool:
    if not settings:
        return False
    explicit = settings.get("endOfDayFlattenEnabled")
    exit_policy = settings.get("exit_policy") if isinstance(settings.get("exit_policy"), dict) else settings.get("exitPolicy") if isinstance(settings.get("exitPolicy"), dict) else {}
    if explicit is None:
        explicit = exit_policy.get("endOfDayFlattenEnabled")
    if explicit is False:
        return False
    timestamp = str(candle.get("timestamp") or candle.get("t") or "")
    if not timestamp:
        return False
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    eastern = parsed.astimezone(ZoneInfo("America/New_York"))
    configured = str(settings.get("flattenTimeEt") or exit_policy.get("flattenTimeEt") or "15:55")
    hour, minute = _parse_hhmm(configured)
    return (eastern.hour, eastern.minute) >= (hour, minute)


def _int_setting(settings: dict, *keys: str) -> int:
    exit_policy = settings.get("exit_policy") if isinstance(settings.get("exit_policy"), dict) else settings.get("exitPolicy") if isinstance(settings.get("exitPolicy"), dict) else {}
    for key in keys:
        for source in (settings, exit_policy):
            try:
                return max(0, int(source.get(key)))
            except (TypeError, ValueError):
                continue
    return 0


def _optional_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour, minute = value.split(":", 1)
        return max(0, min(23, int(hour))), max(0, min(59, int(minute[:2])))
    except (AttributeError, ValueError):
        return 15, 55
