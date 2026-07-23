"""Dedicated Regime context-feed adapters.

These adapters normalize shared market/context services into the immutable
shape consumed by the backend-authoritative Regime runtime.
"""

from __future__ import annotations

from typing import Any


UNKNOWN_CONTEXT_FEEDS: dict[str, dict[str, Any]] = {
    "quoteFreshness": {
        "status": "unknown",
        "ageMs": None,
        "maxAgeMs": 15000,
        "bid": None,
        "ask": None,
        "spreadPercent": None,
        "spreadBps": None,
        "tradeCount": None,
        "tradeRatePerSecond": None,
        "expectedFillQuantity": None,
        "participationRate": None,
        "topOfBookDepth": None,
        "unitConvention": {
            "spreadPercent": "decimal_ratio",
            "spreadBps": "basis_points",
            "participationRate": "decimal_ratio",
        },
    },
    "qqqRelativeStrength": {"state": "unknown", "relativeToPrimaryPercent": None},
    "iwmRelativeStrength": {"state": "unknown", "relativeToPrimaryPercent": None},
    "marketBreadth": {"state": "unknown", "advanceDeclineRatio": None},
    "vix": {"state": "unknown", "value": None},
    "vix1d": {"state": "unknown", "value": None},
    "esFutures": {"trend": "unknown", "changePercent": None},
    "scheduledEconomicEvent": {"state": "unknown", "minutesUntilEvent": None, "eventType": None, "source": "missing"},
    "haltLuldCircuitBreaker": {"haltState": "unknown", "circuitBreakerState": "unknown", "newEntriesBlocked": False},
    "marketStructureLevels": {
        "priorDayHigh": None,
        "priorDayLow": None,
        "premarketHigh": None,
        "premarketLow": None,
        "openingRangeHigh": None,
        "openingRangeLow": None,
        "source": "missing",
    },
    "intradayVolatilityBaseline": {
        "calibrationStatus": "missing",
        "atrPercentile": None,
        "realizedVolatilityPercentile": None,
        "currentRangeVsExpected": None,
        "currentVolumeVsExpected": None,
        "expectedRange": None,
        "expectedVolume": None,
        "sampleSize": 0,
    },
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
        "vix1d": adapt_vix_context(source.get("vix1d") or source.get("vix1D") or source.get("vix1dContext") or source.get("vix1d_context")),
        "esFutures": adapt_es_futures_context(source.get("esFutures") or source.get("es_futures") or source.get("es")),
        "scheduledEconomicEvent": adapt_scheduled_event_state(
            source.get("scheduledEconomicEvent") or source.get("scheduled_economic_event") or source.get("eventState")
        ),
        "haltLuldCircuitBreaker": adapt_halt_luld_circuit_breaker(
            source.get("haltLuldCircuitBreaker") or source.get("halt_luld_circuit_breaker") or source.get("marketHaltState")
        ),
        "marketStructureLevels": adapt_market_structure_levels(
            source.get("marketStructureLevels")
            or source.get("market_structure_levels")
            or source.get("structureLevels")
            or source.get("structure_levels")
            or source.get("levels")
        ),
        "intradayVolatilityBaseline": adapt_intraday_volatility_baseline(
            source.get("intradayVolatilityBaseline")
            or source.get("intraday_volatility_baseline")
            or source.get("volatilityBaseline")
            or source.get("volatility_baseline")
            or source.get("timeOfDayVolatility")
            or source.get("time_of_day_volatility")
        ),
    }


def adapt_quote_freshness(raw: Any) -> dict[str, Any]:
    source = _dict(raw)
    age_ms = _number(_first(source, "ageMs", "age_ms", "quoteAgeMs", "quote_age_ms"))
    max_age_ms = _number(_first(source, "maxAgeMs", "max_age_ms", "maximumQuoteAgeMs", "maximum_quote_age_ms")) or 15_000
    bid = _number(_first(source, "bid", "bestBid", "best_bid"))
    ask = _number(_first(source, "ask", "bestAsk", "best_ask"))
    spread_percent = _spread_percent(source)
    spread_bps = spread_percent * 10_000 if spread_percent is not None else None
    trade_count = _number(_first(source, "tradeCount", "trade_count", "trades"))
    trade_rate = _number(_first(source, "tradeRatePerSecond", "trade_rate_per_second", "tradesPerSecond", "trades_per_second"))
    expected_fill_quantity = _number(_first(source, "expectedFillQuantity", "expected_fill_quantity", "expectedFillQty", "expected_fill_qty"))
    participation_rate = _decimal_ratio(_first(source, "participationRate", "participation_rate", "participationPercent", "participation_percent"))
    top_depth = _number(_first(source, "topOfBookDepth", "top_of_book_depth", "topBookDepth", "top_book_depth"))
    status = str(_first(source, "status", "freshness") or "").lower()
    if status not in {"fresh", "stale", "unknown"}:
        status = ""
    if not status:
        if age_ms is None:
            status = "unknown"
        elif age_ms > max_age_ms:
            status = "stale"
        else:
            status = "fresh"
    return {
        "status": status,
        "ageMs": age_ms,
        "maxAgeMs": max_age_ms,
        "bid": bid,
        "ask": ask,
        "spreadPercent": spread_percent,
        "spreadBps": spread_bps,
        "tradeCount": trade_count,
        "tradeRatePerSecond": trade_rate,
        "expectedFillQuantity": expected_fill_quantity,
        "participationRate": participation_rate,
        "topOfBookDepth": top_depth,
        "unitConvention": {
            "spreadPercent": "decimal_ratio",
            "spreadBps": "basis_points",
            "participationRate": "decimal_ratio",
        },
    }


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
    event_type = _event_type(_first(source, "eventType", "event_type", "type", "name", "eventName", "event_name"))
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
    return {"state": state, "minutesUntilEvent": minutes, "eventType": event_type, "source": str(_first(source, "source") or "context_feed") if source else "missing"}


def adapt_halt_luld_circuit_breaker(raw: Any) -> dict[str, Any]:
    source = _dict(raw)
    halt_state = str(_first(source, "haltState", "halt_state") or "unknown").lower()
    circuit_state = str(_first(source, "circuitBreakerState", "circuit_breaker_state") or "unknown").lower()
    blocked = bool(_first(source, "newEntriesBlocked", "new_entries_blocked", "blocked"))
    if halt_state in {"halted", "paused"} or circuit_state in {"active", "triggered"}:
        blocked = True
    return {"haltState": halt_state, "circuitBreakerState": circuit_state, "newEntriesBlocked": blocked}


def adapt_market_structure_levels(raw: Any) -> dict[str, Any]:
    source = _dict(raw)
    level_source = str(_first(source, "source") or "context_feed") if source else "missing"
    return {
        "priorDayHigh": _number(_first(source, "priorDayHigh", "prior_day_high", "pdh")),
        "priorDayLow": _number(_first(source, "priorDayLow", "prior_day_low", "pdl")),
        "premarketHigh": _number(_first(source, "premarketHigh", "premarket_high", "pmh")),
        "premarketLow": _number(_first(source, "premarketLow", "premarket_low", "pml")),
        "openingRangeHigh": _number(_first(source, "openingRangeHigh", "opening_range_high", "orh")),
        "openingRangeLow": _number(_first(source, "openingRangeLow", "opening_range_low", "orl")),
        "source": level_source,
    }


def adapt_intraday_volatility_baseline(raw: Any) -> dict[str, Any]:
    source = _dict(raw)
    atr_percentile = _percentile(_first(source, "atrPercentile", "atr_percentile", "atrPctile", "atr_pctile"))
    rv_percentile = _percentile(
        _first(
            source,
            "realizedVolatilityPercentile",
            "realized_volatility_percentile",
            "rvPercentile",
            "rv_percentile",
        )
    )
    range_vs_expected = _number(
        _first(source, "currentRangeVsExpected", "current_range_vs_expected", "rangeVsExpected", "range_vs_expected")
    )
    volume_vs_expected = _number(
        _first(source, "currentVolumeVsExpected", "current_volume_vs_expected", "volumeVsExpected", "volume_vs_expected")
    )
    expected_range = _number(_first(source, "expectedRange", "expected_range"))
    expected_volume = _number(_first(source, "expectedVolume", "expected_volume"))
    sample_size = int(_number(_first(source, "sampleSize", "sample_size", "samples")) or 0)
    status = str(_first(source, "calibrationStatus", "calibration_status", "status") or "").lower()
    if status not in {
        "ready",
        "calibrating",
        "missing",
        "insufficient_history",
        "inactive_until_live_paper_trading",
        "outside_regular_session",
        "missing_minute",
        "unknown",
    }:
        status = ""
    if not status:
        status = "ready" if atr_percentile is not None or rv_percentile is not None else "missing"
    return {
        "calibrationStatus": status,
        "atrPercentile": atr_percentile,
        "realizedVolatilityPercentile": rv_percentile,
        "currentRangeVsExpected": range_vs_expected,
        "currentVolumeVsExpected": volume_vs_expected,
        "expectedRange": expected_range,
        "expectedVolume": expected_volume,
        "sampleSize": sample_size,
        "artifactId": _first(source, "artifactId", "artifact_id"),
        "activationStatus": _first(source, "activationStatus", "activation_status"),
        "minuteOfSession": _number(_first(source, "minuteOfSession", "minute_of_session")),
        "source": _first(source, "source"),
    }


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


def _percentile(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if 1 < number <= 100:
        number /= 100
    return max(0.0, min(1.0, number))


def _spread_percent(source: dict[str, Any]) -> float | None:
    explicit = _decimal_ratio(_first(source, "spreadPercent", "spread_percent"))
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


def _decimal_ratio(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if abs(number) > 1:
        number /= 100
    return number


def _event_type(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    if not text:
        return None
    if "cpi" in text or "inflation" in text:
        return "cpi"
    if "fomc" in text:
        return "fomc"
    if "payroll" in text or "jobs" in text or "employment" in text or "nfp" in text:
        return "jobs"
    if "fed" in text or "powell" in text:
        return "fed"
    return text


def _signed_state(value: float | None, *, positive: str, negative: str, threshold: float, neutral: str = "neutral") -> str:
    if value is None:
        return "unknown"
    if value >= threshold:
        return positive
    if value <= -threshold:
        return negative
    return neutral
