"""Canonical Meta-Strategy session calendar contract."""

from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum
from zoneinfo import ZoneInfo


EXCHANGE_TIMEZONE = ZoneInfo("America/New_York")


class MetaStrategySession(str, Enum):
    PREMARKET = "PREMARKET"
    OPENING = "OPENING"
    MORNING = "MORNING"
    MIDDAY = "MIDDAY"
    AFTERNOON = "AFTERNOON"
    CLOSING = "CLOSING"
    AFTER_HOURS = "AFTER_HOURS"
    CLOSED = "CLOSED"


META_STRATEGY_SESSION_ALIASES = {
    "pre_market": MetaStrategySession.PREMARKET,
    "premarket": MetaStrategySession.PREMARKET,
    "open": MetaStrategySession.OPENING,
    "opening": MetaStrategySession.OPENING,
    "regular": MetaStrategySession.MORNING,
    "morning": MetaStrategySession.MORNING,
    "midday": MetaStrategySession.MIDDAY,
    "afternoon": MetaStrategySession.AFTERNOON,
    "power_hour": MetaStrategySession.CLOSING,
    "closing": MetaStrategySession.CLOSING,
    "after_hours": MetaStrategySession.AFTER_HOURS,
    "outside_session": MetaStrategySession.CLOSED,
    "closed": MetaStrategySession.CLOSED,
}

META_STRATEGY_MARKET_HOLIDAYS = frozenset(
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
META_STRATEGY_EARLY_CLOSES = {date(2026, 11, 27): time(13, 0)}


def canonical_session(value: str | MetaStrategySession) -> MetaStrategySession:
    if isinstance(value, MetaStrategySession):
        return value
    normalized = str(value).strip().lower()
    if normalized in META_STRATEGY_SESSION_ALIASES:
        return META_STRATEGY_SESSION_ALIASES[normalized]
    try:
        return MetaStrategySession[normalized.upper()]
    except KeyError as exc:
        raise ValueError(f"Unknown Meta-Strategy session: {value}") from exc


def meta_strategy_session_at(timestamp: datetime) -> MetaStrategySession:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("meta_strategy.session.timestamp_must_be_timezone_aware")
    local = timestamp.astimezone(EXCHANGE_TIMEZONE)
    session_date = local.date()
    if session_date.weekday() >= 5 or session_date in META_STRATEGY_MARKET_HOLIDAYS:
        return MetaStrategySession.CLOSED

    market_open = time(9, 30)
    market_close = META_STRATEGY_EARLY_CLOSES.get(session_date, time(16, 0))
    current = local.time()
    if current < time(4, 0):
        return MetaStrategySession.CLOSED
    if current < market_open:
        return MetaStrategySession.PREMARKET
    if current >= time(20, 0):
        return MetaStrategySession.CLOSED
    if current >= market_close:
        return MetaStrategySession.AFTER_HOURS
    if current < time(10, 0):
        return MetaStrategySession.OPENING
    if current < time(12, 0):
        return MetaStrategySession.MORNING
    if current < time(14, 0):
        return MetaStrategySession.MIDDAY
    if current < max(time(14, 0), _minutes_before(market_close, 30)):
        return MetaStrategySession.AFTERNOON
    return MetaStrategySession.CLOSING


def _minutes_before(value: time, minutes: int) -> time:
    total = value.hour * 60 + value.minute - minutes
    return time(max(0, total // 60), total % 60)


__all__ = [
    "EXCHANGE_TIMEZONE",
    "META_STRATEGY_EARLY_CLOSES",
    "META_STRATEGY_MARKET_HOLIDAYS",
    "META_STRATEGY_SESSION_ALIASES",
    "MetaStrategySession",
    "canonical_session",
    "meta_strategy_session_at",
]
