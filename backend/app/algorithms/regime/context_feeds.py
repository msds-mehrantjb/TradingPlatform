"""Dedicated Regime context-feed adapters.

These adapters normalize shared market/context services into the immutable
shape consumed by the backend-authoritative Regime runtime.
"""

from __future__ import annotations

from typing import Any


UNKNOWN_CONTEXT_FEEDS: dict[str, dict[str, Any]] = {
    "quoteFreshness": {"status": "unknown", "ageMs": None, "spreadPercent": None},
    "qqqRelativeStrength": {"state": "unknown", "relativeToPrimaryPercent": None},
    "iwmRelativeStrength": {"state": "unknown", "relativeToPrimaryPercent": None},
    "marketBreadth": {"state": "unknown", "advanceDeclineRatio": None},
    "vix": {"state": "unknown", "value": None},
    "esFutures": {"trend": "unknown", "changePercent": None},
    "scheduledEconomicEvent": {"state": "unknown", "minutesUntilEvent": None},
    "haltLuldCircuitBreaker": {"haltState": "unknown", "circuitBreakerState": "unknown", "newEntriesBlocked": False},
}


def build_regime_context_feeds(raw: dict[str, Any] | None) -> dict[str, Any]:
    source = raw or {}
    return {
        "quoteFreshness": adapt_quote_freshness(source.get("quoteFreshness") or source.get("quote_freshness")),
        "qqqRelativeStrength": adapt_relative_strength(
            source.get("qqqRelativeStrength") or source.get("qqq_relative_strength") or source.get("qqq"),
            default_key="qqqRelativeStrength",
        ),
        "iwmRelativeStrength": adapt_relative_strength(
            source.get("iwmRelativeStrength") or source.get("iwm_relative_strength") or source.get("iwm"),
            default_key="iwmRelativeStrength",
        ),
        "marketBreadth": adapt_market_breadth(source.get("marketBreadth") or source.get("market_breadth") or source.get("breadth")),
        "vix": adapt_vix_context(source.get("vix") or source.get("vixContext") or source.get("vix_context")),
        "esFutures": adapt_es_futures_context(source.get("esFutures") or source.get("es_futures") or source.get("es")),
        "scheduledEconomicEvent": adapt_scheduled_event_state(
            source.get("scheduledEconomicEvent") or source.get("scheduled_economic_event") or source.get("eventState")
        ),
        "haltLuldCircuitBreaker": adapt_halt_luld_circuit_breaker(
            source.get("haltLuldCircuitBreaker") or source.get("halt_luld_circuit_breaker") or source.get("marketHaltState")
        ),
    }


def adapt_quote_freshness(raw: Any) -> dict[str, Any]:
    source = _dict(raw)
    age_ms = _number(_first(source, "ageMs", "age_ms", "quoteAgeMs", "quote_age_ms"))
    spread_percent = _spread_percent(source)
    status = str(_first(source, "status", "freshness") or "").lower()
    if status not in {"fresh", "stale", "unknown"}:
        status = ""
    if not status:
        if age_ms is None:
            status = "unknown"
        elif age_ms > 15_000:
            status = "stale"
        else:
            status = "fresh"
    return {"status": status, "ageMs": age_ms, "spreadPercent": spread_percent}


def adapt_relative_strength(raw: Any, *, default_key: str) -> dict[str, Any]:
    source = _dict(raw)
    value = _number(_first(source, "relativeToPrimaryPercent", "relative_to_primary_percent", "changePercent", "change_percent", "value"))
    state = str(_first(source, "state", "trend") or "").lower()
    if state not in {"outperforming", "underperforming", "neutral", "unknown"}:
        state = ""
    if not state:
        state = _signed_state(value, positive="outperforming", negative="underperforming", threshold=0.25)
    return {"state": state, "relativeToPrimaryPercent": value, "source": default_key}


def adapt_market_breadth(raw: Any) -> dict[str, Any]:
    source = _dict(raw)
    ratio = _number(_first(source, "advanceDeclineRatio", "advance_decline_ratio", "adRatio", "ad_ratio", "value"))
    state = str(_first(source, "state", "trend") or "").lower()
    if state not in {"positive", "negative", "neutral", "unknown"}:
        state = ""
    if not state:
        if ratio is None:
            state = "unknown"
        elif ratio >= 1.2:
            state = "positive"
        elif ratio <= 0.8:
            state = "negative"
        else:
            state = "neutral"
    return {"state": state, "advanceDeclineRatio": ratio}


def adapt_vix_context(raw: Any) -> dict[str, Any]:
    source = _dict(raw)
    value = _number(_first(source, "value", "last", "close"))
    state = str(_first(source, "state", "regime") or "").lower()
    if state not in {"calm", "normal", "elevated", "stress", "unknown"}:
        state = ""
    if not state:
        if value is None:
            state = "unknown"
        elif value >= 30:
            state = "stress"
        elif value >= 20:
            state = "elevated"
        elif value <= 13:
            state = "calm"
        else:
            state = "normal"
    return {"state": state, "value": value}


def adapt_es_futures_context(raw: Any) -> dict[str, Any]:
    source = _dict(raw)
    change = _number(_first(source, "changePercent", "change_percent", "percentChange", "percent_change", "value"))
    trend = str(_first(source, "trend", "state") or "").lower()
    if trend not in {"up", "down", "flat", "unknown"}:
        trend = ""
    if not trend:
        trend = _signed_state(change, positive="up", negative="down", threshold=0.15, neutral="flat")
    return {"trend": trend, "changePercent": change}


def adapt_scheduled_event_state(raw: Any) -> dict[str, Any]:
    source = _dict(raw)
    minutes = _number(_first(source, "minutesUntilEvent", "minutes_until_event", "minutesToEvent", "minutes_to_event"))
    state = str(_first(source, "state", "risk") or "").lower()
    if state not in {"none", "soon", "elevated", "blackout", "unknown"}:
        state = ""
    if not state:
        if minutes is None:
            state = "unknown"
        elif minutes <= 0:
            state = "blackout"
        elif minutes <= 15:
            state = "soon"
        elif minutes <= 60:
            state = "elevated"
        else:
            state = "none"
    return {"state": state, "minutesUntilEvent": minutes}


def adapt_halt_luld_circuit_breaker(raw: Any) -> dict[str, Any]:
    source = _dict(raw)
    halt_state = str(_first(source, "haltState", "halt_state") or "unknown").lower()
    circuit_state = str(_first(source, "circuitBreakerState", "circuit_breaker_state") or "unknown").lower()
    blocked = bool(_first(source, "newEntriesBlocked", "new_entries_blocked", "blocked"))
    if halt_state in {"halted", "paused"} or circuit_state in {"active", "triggered"}:
        blocked = True
    return {"haltState": halt_state, "circuitBreakerState": circuit_state, "newEntriesBlocked": blocked}


def _dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _first(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if source.get(key) is not None:
            return source[key]
    return None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _spread_percent(source: dict[str, Any]) -> float | None:
    explicit = _number(_first(source, "spreadPercent", "spread_percent"))
    if explicit is not None:
        return explicit
    bps = _number(_first(source, "spreadBps", "spread_bps"))
    if bps is not None:
        return bps / 10_000
    bid = _number(source.get("bid"))
    ask = _number(source.get("ask"))
    midpoint = ((bid or 0) + (ask or 0)) / 2
    if bid is None or ask is None or midpoint <= 0:
        return None
    return (ask - bid) / midpoint


def _signed_state(value: float | None, *, positive: str, negative: str, threshold: float, neutral: str = "neutral") -> str:
    if value is None:
        return "unknown"
    if value >= threshold:
        return positive
    if value <= -threshold:
        return negative
    return neutral
