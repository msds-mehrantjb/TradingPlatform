"""Fail-closed Regime market-data validation before classification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any

from backend.app.algorithms.regime.contracts import RegimeCandle, RegimeMarketSnapshot
from backend.app.algorithms.regime.exchange_calendar import exchange_session


REGIME_MARKET_DATA_VALIDATION_VERSION = "regime_market_data_validation_v1"
SUPPORTED_SYMBOLS = frozenset({"SPY"})


@dataclass(frozen=True)
class RegimeMarketDataValidationResult:
    passed: bool
    reason_codes: tuple[str, ...]
    data_timestamp: str
    feature_timestamp: str
    missing_bar_count: int
    duplicate_timestamp_count: int
    future_dated: bool
    point_in_time_higher_timeframes: dict[str, Any]
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": "regime",
            "validationVersion": REGIME_MARKET_DATA_VALIDATION_VERSION,
            "passed": self.passed,
            "reasonCodes": list(self.reason_codes),
            "dataTimestamp": self.data_timestamp,
            "featureTimestamp": self.feature_timestamp,
            "missingBarCount": self.missing_bar_count,
            "duplicateTimestampCount": self.duplicate_timestamp_count,
            "futureDated": self.future_dated,
            "pointInTimeHigherTimeframes": dict(self.point_in_time_higher_timeframes),
            "evidence": dict(self.evidence),
        }


def validate_regime_market_data(
    snapshot: RegimeMarketSnapshot,
    *,
    settings: dict[str, Any] | None = None,
    observed_at: str | datetime | None = None,
) -> RegimeMarketDataValidationResult:
    settings = settings or {}
    source = snapshot.context_feeds.get("marketDataSource") if isinstance(snapshot.context_feeds.get("marketDataSource"), dict) else {}
    observed = _parse_timestamp(observed_at or source.get("observedAt"))
    one_minute = tuple(snapshot.one_minute_candles or snapshot.candles)
    primary = tuple(snapshot.candles)
    reason_codes: list[str] = []

    if snapshot.symbol.upper() not in SUPPORTED_SYMBOLS:
        reason_codes.append("regime.market_data.symbol_unsupported")
    if not primary or not one_minute:
        reason_codes.append("regime.market_data.missing_one_minute_bars")

    parsed_times = [_parse_timestamp(candle.timestamp) for candle in one_minute]
    latest = snapshot.latest
    latest_time = _parse_timestamp(latest.timestamp)
    if latest_time is None:
        reason_codes.append("regime.market_data.bar_timestamp_invalid")
    else:
        session = exchange_session(latest.timestamp)
        if session.status == "outside_regular":
            reason_codes.append("regime.market_data.outside_trading_session")

    completion_flags = tuple(source.get("oneMinuteCompletionFlags") or source.get("primaryCompletionFlags") or ())
    if any(flag is False for flag in completion_flags):
        reason_codes.append("regime.market_data.incomplete_bar")
    timeframe = str(source.get("timeframe") or "1Min")
    if timeframe not in {"1Min", "1min", "1m", "1M"}:
        reason_codes.append("regime.market_data.unexpected_bar_interval")

    malformed_count = 0
    negative_volume_count = 0
    for candle in one_minute:
        if not _ohlc_consistent(candle):
            malformed_count += 1
        if not isfinite(candle.volume) or candle.volume < 0:
            negative_volume_count += 1
    if malformed_count:
        reason_codes.append("regime.market_data.ohlc_inconsistent")
    if negative_volume_count:
        reason_codes.append("regime.market_data.negative_volume")

    input_timestamps = tuple(str(item) for item in (source.get("oneMinuteInputTimestamps") or (candle.timestamp for candle in one_minute)))
    duplicate_count = len(input_timestamps) - len(set(input_timestamps))
    if duplicate_count:
        reason_codes.append("regime.market_data.duplicate_timestamp")
    input_parsed = [_parse_timestamp(item) for item in input_timestamps]
    if any(item is None for item in input_parsed):
        reason_codes.append("regime.market_data.bar_timestamp_invalid")
    if any(left is not None and right is not None and right < left for left, right in zip(input_parsed, input_parsed[1:])):
        reason_codes.append("regime.market_data.out_of_order")

    missing_bar_count = _missing_bar_count(sorted(item for item in parsed_times if item is not None))
    if missing_bar_count:
        reason_codes.append("regime.market_data.missing_bars")
    if _unexpected_interval(sorted(item for item in parsed_times if item is not None)):
        reason_codes.append("regime.market_data.unexpected_bar_interval")

    future_dated = False
    if latest_time is not None and observed is not None:
        future_dated = latest_time > observed + timedelta(seconds=1)
        if future_dated:
            reason_codes.append("regime.market_data.future_dated_bar")
        age_seconds = max(0.0, (observed - latest_time).total_seconds())
        if age_seconds > float(settings.get("staleBarToleranceSeconds", 90)):
            reason_codes.append("regime.market_data.stale_bar")
    explicit_age_ms = _number(source.get("dataAgeMs"))
    if explicit_age_ms is not None and explicit_age_ms > float(settings.get("staleBarToleranceSeconds", 90)) * 1000:
        reason_codes.append("regime.market_data.stale_bar")

    quote_reasons = _quote_reason_codes(snapshot.context_feeds.get("quoteFreshness") or {}, settings)
    reason_codes.extend(_corporate_action_reason_codes(source.get("corporateAction") or {}))

    data_timestamp = latest.timestamp
    feature_timestamp = _feature_timestamp(snapshot)
    higher_timeframes = {
        "fiveMinute": {
            "source": "derived_from_finalized_one_minute",
            "confirmedCount": len(snapshot.five_minute_candles),
            "latestTimestamp": snapshot.five_minute_candles[-1].timestamp if snapshot.five_minute_candles else None,
            "suppliedCountIgnored": int(source.get("suppliedFiveMinuteCount") or 0),
            "partialConfirmedEvidenceAllowed": False,
        },
        "fifteenMinute": {
            "source": "derived_from_finalized_one_minute",
            "confirmedCount": len(snapshot.fifteen_minute_candles),
            "latestTimestamp": snapshot.fifteen_minute_candles[-1].timestamp if snapshot.fifteen_minute_candles else None,
            "suppliedCountIgnored": int(source.get("suppliedFifteenMinuteCount") or 0),
            "partialConfirmedEvidenceAllowed": False,
        },
    }
    unique_reasons = tuple(dict.fromkeys(reason_codes))
    return RegimeMarketDataValidationResult(
        passed=not unique_reasons,
        reason_codes=unique_reasons,
        data_timestamp=data_timestamp,
        feature_timestamp=feature_timestamp,
        missing_bar_count=missing_bar_count,
        duplicate_timestamp_count=duplicate_count,
        future_dated=future_dated,
        point_in_time_higher_timeframes=higher_timeframes,
        evidence={
            "symbol": snapshot.symbol,
            "latestBarTimestamp": data_timestamp,
            "primaryBarCount": len(primary),
            "oneMinuteBarCount": len(one_minute),
            "malformedOhlcCount": malformed_count,
            "negativeVolumeCount": negative_volume_count,
            "observedAt": _iso(observed) if observed else None,
            "quoteFreshness": snapshot.context_feeds.get("quoteFreshness") or {},
            "quoteValidationReasonCodes": quote_reasons,
        },
    )


def _quote_reason_codes(quote: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    if not quote:
        return ["regime.market_data.quote_missing"]
    reasons: list[str] = []
    bid = _number(quote.get("bid"))
    ask = _number(quote.get("ask"))
    if quote.get("status") == "stale":
        reasons.append("regime.market_data.quote_stale")
    age_ms = _number(quote.get("ageMs"))
    max_age_ms = min(float(quote.get("maxAgeMs") or 15_000), float(settings.get("quoteAgeToleranceSeconds", 5)) * 1000)
    if age_ms is not None and age_ms > max_age_ms:
        reasons.append("regime.market_data.quote_stale")
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask <= bid:
        reasons.append("regime.market_data.bid_ask_invalid")
    spread_bps = _number(quote.get("spreadBps"))
    if spread_bps is None and bid is not None and ask is not None and ask > bid:
        mid = (bid + ask) / 2
        spread_bps = ((ask - bid) / mid) * 10_000 if mid > 0 else None
    max_spread_bps = float(settings.get("maxSpreadPercent", 0.03)) * 10_000
    if spread_bps is not None and spread_bps > max_spread_bps:
        reasons.append("regime.market_data.spread_too_wide")
    return reasons


def _corporate_action_reason_codes(corporate_action: dict[str, Any]) -> list[str]:
    if not corporate_action:
        return []
    action_type = str(corporate_action.get("type") or corporate_action.get("eventType") or "").lower()
    if not action_type or action_type in {"none", "no_action"}:
        return []
    handled = bool(corporate_action.get("adjusted") or corporate_action.get("handled") or corporate_action.get("priceAdjusted"))
    return [] if handled else ["regime.market_data.corporate_action_unhandled"]


def _ohlc_consistent(candle: RegimeCandle) -> bool:
    values = (candle.open, candle.high, candle.low, candle.close)
    if any(not isfinite(value) or value <= 0 for value in values):
        return False
    return candle.high >= max(candle.open, candle.close, candle.low) and candle.low <= min(candle.open, candle.close, candle.high)


def _missing_bar_count(times: list[datetime]) -> int:
    missing = 0
    for left, right in zip(times, times[1:]):
        gap_minutes = int((right - left).total_seconds() // 60)
        if gap_minutes > 1:
            missing += gap_minutes - 1
    return missing


def _unexpected_interval(times: list[datetime]) -> bool:
    return any((right - left).total_seconds() != 60 for left, right in zip(times, times[1:]))


def _feature_timestamp(snapshot: RegimeMarketSnapshot) -> str:
    candidates = [snapshot.latest.timestamp]
    if snapshot.five_minute_candles:
        candidates.append(snapshot.five_minute_candles[-1].timestamp)
    if snapshot.fifteen_minute_candles:
        candidates.append(snapshot.fifteen_minute_candles[-1].timestamp)
    parsed = [(candidate, _parse_timestamp(candidate)) for candidate in candidates]
    valid = [item for item in parsed if item[1] is not None]
    if not valid:
        return snapshot.latest.timestamp
    return min(valid, key=lambda item: item[1])[0]


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "REGIME_MARKET_DATA_VALIDATION_VERSION",
    "RegimeMarketDataValidationResult",
    "validate_regime_market_data",
]
