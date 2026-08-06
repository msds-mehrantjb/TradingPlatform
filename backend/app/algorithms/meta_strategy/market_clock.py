"""Market-clock authority for Meta-Strategy paper execution."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo


EXCHANGE_TIMEZONE = ZoneInfo("America/New_York")
META_STRATEGY_MARKET_CLOCK_VERSION = "meta_strategy_market_clock_v1"
META_STRATEGY_MARKET_CLOCK_MAX_AGE_SECONDS = 30
LOCAL_REPLAY_MARKET_HOLIDAYS = frozenset(
    {
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    }
)
LOCAL_REPLAY_EARLY_CLOSES = {date(2026, 11, 27): time(13, 0)}


class MetaStrategyMarketClockError(ValueError):
    pass


class MetaStrategyMarketClockSource(Protocol):
    def get_clock(self) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class MetaStrategyMarketClockSnapshot:
    source: str
    is_open: bool
    status: str
    captured_at: datetime
    data_source_timestamp: datetime
    evaluated_at: datetime
    next_open: datetime | None = None
    next_close: datetime | None = None
    regular_session_open: datetime | None = None
    regular_session_close: datetime | None = None
    holiday: bool = False
    early_close: bool = False
    authoritative: bool = False
    fresh: bool = False
    can_authorize_new_entries: bool = False
    reason_codes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": META_STRATEGY_MARKET_CLOCK_VERSION,
            "source": self.source,
            "isOpen": self.is_open,
            "status": self.status,
            "capturedAt": self.captured_at.isoformat(),
            "dataSourceTimestamp": self.data_source_timestamp.isoformat(),
            "evaluatedAt": self.evaluated_at.isoformat(),
            "nextOpen": self.next_open.isoformat() if self.next_open else None,
            "nextClose": self.next_close.isoformat() if self.next_close else None,
            "regularSessionOpen": self.regular_session_open.isoformat() if self.regular_session_open else None,
            "regularSessionClose": self.regular_session_close.isoformat() if self.regular_session_close else None,
            "holiday": self.holiday,
            "earlyClose": self.early_close,
            "authoritativeReadOnly": self.authoritative,
            "fresh": self.fresh,
            "canAuthorizeNewEntries": self.can_authorize_new_entries,
            "reasonCodes": self.reason_codes,
        }


def read_market_clock_snapshot(
    source: Any | None,
    *,
    evaluated_at: datetime,
    max_age_seconds: int | None = None,
    allow_local_fallback: bool = False,
) -> MetaStrategyMarketClockSnapshot | None:
    _require_aware(evaluated_at, "evaluated_at")
    raw = _call_market_clock_source(source, evaluated_at=evaluated_at)
    if raw is None and allow_local_fallback:
        raw = local_replay_market_clock(evaluated_at)
    if raw is None:
        return None
    return normalize_market_clock_payload(raw, evaluated_at=evaluated_at, max_age_seconds=_market_clock_max_age_seconds(max_age_seconds))


def normalize_market_clock_payload(
    payload: Mapping[str, Any],
    *,
    evaluated_at: datetime,
    max_age_seconds: int | None = None,
) -> MetaStrategyMarketClockSnapshot:
    _require_aware(evaluated_at, "evaluated_at")
    source = str(payload.get("source") or "unknown_market_clock")
    captured_at = _required_payload_time(payload, "capturedAt", "captured_at", "timestamp", "serverTime", fallback=evaluated_at)
    data_source_timestamp = _required_payload_time(payload, "dataSourceTimestamp", "data_source_timestamp", "timestamp", "capturedAt", "captured_at", fallback=captured_at)
    next_open = _optional_payload_time(payload, "nextOpen", "next_open", "nextMarketOpen", "next_market_open")
    next_close = _optional_payload_time(payload, "nextClose", "next_close", "nextMarketClose", "next_market_close")
    regular_open = _optional_payload_time(payload, "regularSessionOpen", "regular_session_open", "sessionOpen", "open")
    regular_close = _optional_payload_time(payload, "regularSessionClose", "regular_session_close", "sessionClose", "close")
    is_open = _clock_is_open(payload)
    if is_open is None:
        is_open = False
    status = str(payload.get("status") or payload.get("state") or ("open" if is_open else "closed")).lower()
    local_source = _is_local_fallback_source(source, payload)
    authoritative = bool(payload.get("authoritativeReadOnly", payload.get("authoritative", not local_source))) and not local_source
    age_seconds = max(0.0, (evaluated_at - data_source_timestamp).total_seconds())
    freshness_limit = _market_clock_max_age_seconds(max_age_seconds)
    fresh = data_source_timestamp <= evaluated_at and age_seconds <= freshness_limit
    contradictory = _market_clock_contradictory(is_open, status)
    reason_codes = list(payload.get("reasonCodes") or payload.get("reason_codes") or ())
    if local_source:
        reason_codes.append("meta_strategy.market_clock.local_fallback_not_authoritative")
    if not authoritative:
        reason_codes.append("meta_strategy.market_clock.not_authoritative")
    if not fresh:
        reason_codes.append("meta_strategy.market_clock.stale")
    if contradictory:
        reason_codes.append("meta_strategy.market_clock.contradictory")
    if is_open:
        reason_codes.append("meta_strategy.market_clock.open")
    else:
        reason_codes.append("meta_strategy.market_clock.closed")
    can_authorize = authoritative and fresh and not contradictory and is_open
    return MetaStrategyMarketClockSnapshot(
        source=source,
        is_open=is_open,
        status=status,
        captured_at=captured_at,
        data_source_timestamp=data_source_timestamp,
        evaluated_at=evaluated_at,
        next_open=next_open,
        next_close=next_close,
        regular_session_open=regular_open,
        regular_session_close=regular_close,
        holiday=bool(payload.get("holiday", payload.get("isHoliday", False))),
        early_close=bool(payload.get("earlyClose", payload.get("early_close", False))),
        authoritative=authoritative,
        fresh=fresh,
        can_authorize_new_entries=can_authorize,
        reason_codes=tuple(dict.fromkeys(str(code) for code in reason_codes if code)),
    )


def local_replay_market_clock(timestamp: datetime) -> dict[str, Any]:
    _require_aware(timestamp, "timestamp")
    local = timestamp.astimezone(EXCHANGE_TIMEZONE)
    session_date = local.date()
    regular_open = datetime.combine(session_date, time(9, 30), tzinfo=EXCHANGE_TIMEZONE)
    regular_close = datetime.combine(session_date, LOCAL_REPLAY_EARLY_CLOSES.get(session_date, time(16, 0)), tzinfo=EXCHANGE_TIMEZONE)
    holiday = session_date.weekday() >= 5 or session_date in LOCAL_REPLAY_MARKET_HOLIDAYS
    is_open = (not holiday) and regular_open <= local < regular_close
    return {
        "source": "meta_strategy.local_replay_calendar",
        "capturedAt": timestamp.isoformat(),
        "dataSourceTimestamp": timestamp.isoformat(),
        "isOpen": is_open,
        "status": "open" if is_open else "closed",
        "nextOpen": _next_local_open(local).isoformat(),
        "nextClose": (regular_close if is_open else _next_local_close(local)).isoformat(),
        "regularSessionOpen": regular_open.isoformat(),
        "regularSessionClose": regular_close.isoformat(),
        "holiday": holiday,
        "earlyClose": session_date in LOCAL_REPLAY_EARLY_CLOSES,
        "authoritativeReadOnly": False,
        "reasonCodes": ("meta_strategy.market_clock.local_replay_calendar",),
    }


def _call_market_clock_source(source: Any | None, *, evaluated_at: datetime) -> Mapping[str, Any] | None:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source
    for method_name in ("read_market_clock", "get_market_clock", "get_clock", "market_clock"):
        method = getattr(source, method_name, None)
        if not callable(method):
            continue
        try:
            value = method(at=evaluated_at)
        except TypeError:
            value = method()
        return value if isinstance(value, Mapping) else None
    return None


def _clock_is_open(payload: Mapping[str, Any]) -> bool | None:
    for key in ("isOpen", "is_open", "marketOpen", "market_open"):
        if key in payload:
            return bool(payload[key])
    status = str(payload.get("status") or payload.get("state") or "").lower()
    if status in {"open", "regular", "regular_session"}:
        return True
    if status in {"closed", "pre_market", "post_market", "halted", "unavailable"}:
        return False
    return None


def _market_clock_contradictory(is_open: bool, status: str) -> bool:
    if is_open and status in {"closed", "halted", "unavailable"}:
        return True
    if not is_open and status in {"open", "regular", "regular_session"}:
        return True
    return False


def _market_clock_max_age_seconds(max_age_seconds: int | None) -> int:
    if max_age_seconds is not None:
        return max(0, int(max_age_seconds))
    raw = os.getenv("META_STRATEGY_MARKET_CLOCK_FRESHNESS_LIMIT_SECONDS")
    if raw is None:
        return META_STRATEGY_MARKET_CLOCK_MAX_AGE_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return META_STRATEGY_MARKET_CLOCK_MAX_AGE_SECONDS
    return max(0, value)


def _is_local_fallback_source(source: str, payload: Mapping[str, Any]) -> bool:
    normalized = source.lower()
    if bool(payload.get("localFallback") or payload.get("replayCalendar") or payload.get("deterministicReplayCalendar")):
        return True
    return "local" in normalized or "replay" in normalized or "backtest" in normalized


def _required_payload_time(payload: Mapping[str, Any], *keys: str, fallback: datetime) -> datetime:
    value = _optional_payload_time(payload, *keys)
    return value if value is not None else fallback


def _optional_payload_time(payload: Mapping[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        if payload.get(key) is None:
            continue
        parsed = _parse_aware_datetime(payload[key], key)
        return parsed.astimezone(UTC)
    return None


def _parse_aware_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise MetaStrategyMarketClockError(f"meta_strategy.market_clock.{field_name}_invalid")
    _require_aware(parsed, field_name)
    return parsed


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MetaStrategyMarketClockError(f"meta_strategy.market_clock.{field_name}_must_be_timezone_aware")


def _next_local_open(local_timestamp: datetime) -> datetime:
    candidate = local_timestamp
    if not _local_session_open_day(candidate.date()) or local_timestamp.time() >= time(16, 0):
        candidate = candidate + timedelta(days=1)
    while not _local_session_open_day(candidate.date()):
        candidate = candidate + timedelta(days=1)
    return datetime.combine(candidate.date(), time(9, 30), tzinfo=EXCHANGE_TIMEZONE)


def _next_local_close(local_timestamp: datetime) -> datetime:
    candidate = _next_local_open(local_timestamp)
    close_time = LOCAL_REPLAY_EARLY_CLOSES.get(candidate.date(), time(16, 0))
    return datetime.combine(candidate.date(), close_time, tzinfo=EXCHANGE_TIMEZONE)


def _local_session_open_day(value: date) -> bool:
    return value.weekday() < 5 and value not in LOCAL_REPLAY_MARKET_HOLIDAYS


__all__ = [
    "EXCHANGE_TIMEZONE",
    "LOCAL_REPLAY_EARLY_CLOSES",
    "LOCAL_REPLAY_MARKET_HOLIDAYS",
    "META_STRATEGY_MARKET_CLOCK_MAX_AGE_SECONDS",
    "META_STRATEGY_MARKET_CLOCK_VERSION",
    "MetaStrategyMarketClockError",
    "MetaStrategyMarketClockSnapshot",
    "MetaStrategyMarketClockSource",
    "local_replay_market_clock",
    "normalize_market_clock_payload",
    "read_market_clock_snapshot",
]
