"""Authoritative NYSE/Arca session clock for the Session subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig
from backend.app.algorithms.session.models import SessionPhase


EXCHANGE_TIMEZONE = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


@dataclass(frozen=True)
class SessionClock:
    session_date: str | None
    exchange_open: datetime | None
    exchange_close: datetime | None
    early_close: bool
    current_phase: SessionPhase
    minute_from_open: int | None
    minutes_until_close: int | None
    regular_session: bool
    phase_start: datetime | None
    phase_end: datetime | None
    next_phase: SessionPhase | None
    event_timestamp_utc: datetime
    event_timestamp_et: datetime
    exchange_timezone: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sessionDate": self.session_date,
            "exchangeOpen": _iso_or_none(self.exchange_open),
            "exchangeClose": _iso_or_none(self.exchange_close),
            "earlyClose": self.early_close,
            "currentPhase": self.current_phase.value,
            "minuteFromOpen": self.minute_from_open,
            "minutesUntilClose": self.minutes_until_close,
            "regularSession": self.regular_session,
            "phaseStart": _iso_or_none(self.phase_start),
            "phaseEnd": _iso_or_none(self.phase_end),
            "nextPhase": self.next_phase.value if self.next_phase else None,
            "eventTimestampUtc": self.event_timestamp_utc.isoformat(),
            "eventTimestampEt": self.event_timestamp_et.isoformat(),
            "exchangeTimezone": self.exchange_timezone,
            "reason": self.reason,
        }


def resolve_session_clock(timestamp: datetime | str, *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> SessionClock:
    event_utc = parse_session_timestamp_utc(timestamp)
    exchange_tz = ZoneInfo(config.exchange_timezone)
    event_et = event_utc.astimezone(exchange_tz)
    session_day = event_et.date()
    bounds = exchange_session_bounds(session_day, config=config)

    if bounds is None:
        return _closed_clock(
            event_utc,
            event_et,
            config=config,
            session_date=session_day.isoformat(),
            reason="holiday_or_weekend",
        )

    exchange_open, exchange_close, early_close = bounds
    premarket_start = _local_datetime(session_day, config.premarket_start, exchange_tz)
    postmarket_end = _local_datetime(session_day, config.postmarket_end, exchange_tz)

    if event_et < premarket_start or event_et >= postmarket_end:
        return _closed_clock(event_utc, event_et, config=config, session_date=session_day.isoformat(), reason="outside_extended_session")
    if event_et < exchange_open:
        return _clock(
            event_utc,
            event_et,
            config=config,
            session_date=session_day.isoformat(),
            exchange_open=exchange_open,
            exchange_close=exchange_close,
            early_close=early_close,
            phase=SessionPhase.PREMARKET,
            phase_start=premarket_start,
            phase_end=exchange_open,
            next_phase=SessionPhase.OPENING_AUCTION,
            reason="premarket",
        )
    if event_et >= exchange_close:
        return _clock(
            event_utc,
            event_et,
            config=config,
            session_date=session_day.isoformat(),
            exchange_open=exchange_open,
            exchange_close=exchange_close,
            early_close=early_close,
            phase=SessionPhase.POSTMARKET,
            phase_start=exchange_close,
            phase_end=postmarket_end,
            next_phase=SessionPhase.CLOSED,
            reason="postmarket",
        )

    phase, phase_start, phase_end, next_phase = _regular_phase(event_et, exchange_open, exchange_close, config)
    return _clock(
        event_utc,
        event_et,
        config=config,
        session_date=session_day.isoformat(),
        exchange_open=exchange_open,
        exchange_close=exchange_close,
        early_close=early_close,
        phase=phase,
        phase_start=phase_start,
        phase_end=phase_end,
        next_phase=next_phase,
        reason="regular_session",
    )


def parse_session_timestamp_utc(timestamp: datetime | str) -> datetime:
    if isinstance(timestamp, datetime):
        parsed = timestamp
    else:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Session calendar timestamps must be timezone-aware")
    return parsed.astimezone(UTC)


def exchange_session_bounds(session_day: date, *, config: SessionConfig = DEFAULT_SESSION_CONFIG) -> tuple[datetime, datetime, bool] | None:
    exchange_tz = ZoneInfo(config.exchange_timezone)
    if session_day.weekday() >= 5 or session_day in market_holidays(session_day.year):
        return None
    close_time = EARLY_CLOSE if session_day in early_close_days(session_day.year) else config.market_close
    return (
        _local_datetime(session_day, config.market_open, exchange_tz),
        _local_datetime(session_day, close_time, exchange_tz),
        close_time == EARLY_CLOSE,
    )


def market_holidays(year: int) -> set[date]:
    holidays = {
        observed_fixed_holiday(year, 1, 1),
        nth_weekday(year, 1, 0, 3),
        nth_weekday(year, 2, 0, 3),
        good_friday(year),
        last_weekday(year, 5, 0),
        observed_fixed_holiday(year, 6, 19),
        observed_fixed_holiday(year, 7, 4),
        nth_weekday(year, 9, 0, 1),
        nth_weekday(year, 11, 3, 4),
        observed_fixed_holiday(year, 12, 25),
    }
    return {holiday for holiday in holidays if holiday.year == year}


def early_close_days(year: int) -> set[date]:
    candidates = {
        day_after_thanksgiving(year),
        christmas_eve_early_close(year),
        independence_day_early_close(year),
    }
    return {
        candidate
        for candidate in candidates
        if candidate.year == year and candidate.weekday() < 5 and candidate not in market_holidays(year)
    }


def observed_fixed_holiday(year: int, month: int, day: int) -> date:
    actual = date(year, month, day)
    if actual.weekday() == 5:
        return actual - timedelta(days=1)
    if actual.weekday() == 6:
        return actual + timedelta(days=1)
    return actual


def christmas_eve_early_close(year: int) -> date:
    christmas = date(year, 12, 25)
    if christmas.weekday() == 0:
        return date(year, 12, 22)
    if christmas.weekday() == 6:
        return date(year, 12, 23)
    return date(year, 12, 24)


def independence_day_early_close(year: int) -> date:
    independence_day = date(year, 7, 4)
    if independence_day.weekday() == 0:
        return date(year, 7, 1)
    if independence_day.weekday() in {1, 2, 3, 4}:
        return independence_day - timedelta(days=1)
    return date(year, 7, 3)


def day_after_thanksgiving(year: int) -> date:
    return nth_weekday(year, 11, 3, 4) + timedelta(days=1)


def nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    current = date(year, month, 1)
    offset = (weekday - current.weekday()) % 7
    return current + timedelta(days=offset + ((nth - 1) * 7))


def last_weekday(year: int, month: int, weekday: int) -> date:
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    current = next_month - timedelta(days=1)
    return current - timedelta(days=(current.weekday() - weekday) % 7)


def good_friday(year: int) -> date:
    return easter_sunday(year) - timedelta(days=2)


def easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + (2 * e) + (2 * i) - h - k) % 7
    m = (a + (11 * h) + (22 * l)) // 451
    month = (h + l - (7 * m) + 114) // 31
    day = ((h + l - (7 * m) + 114) % 31) + 1
    return date(year, month, day)


def _regular_phase(
    timestamp_et: datetime,
    exchange_open: datetime,
    exchange_close: datetime,
    config: SessionConfig,
) -> tuple[SessionPhase, datetime, datetime, SessionPhase]:
    session_day = timestamp_et.date()
    exchange_tz = ZoneInfo(config.exchange_timezone)
    opening_discovery_end = exchange_open + timedelta(minutes=config.opening_discovery_minutes)
    opening_range_end = exchange_open + timedelta(minutes=config.opening_range_minutes)
    morning_end = min(_local_datetime(session_day, config.morning_end, exchange_tz), exchange_close)
    midday_end = min(_local_datetime(session_day, config.midday_end, exchange_tz), exchange_close)
    power_hour_start = min(_local_datetime(session_day, config.power_hour_start, exchange_tz), exchange_close)
    new_entry_cutoff = max(
        exchange_open,
        exchange_close - timedelta(minutes=config.new_entry_cutoff_minutes_before_close),
    )
    closing_start = max(exchange_open, exchange_close - timedelta(minutes=config.closing_auction_minutes))

    if timestamp_et == exchange_open:
        return SessionPhase.OPENING_AUCTION, exchange_open, exchange_open, SessionPhase.OPENING_DISCOVERY
    if timestamp_et < opening_discovery_end:
        return SessionPhase.OPENING_DISCOVERY, exchange_open, opening_discovery_end, SessionPhase.OPENING_RANGE
    if timestamp_et < opening_range_end:
        return SessionPhase.OPENING_RANGE, opening_discovery_end, opening_range_end, SessionPhase.MORNING
    if timestamp_et >= closing_start:
        return SessionPhase.CLOSING_AUCTION, closing_start, exchange_close, SessionPhase.POSTMARKET
    if timestamp_et >= power_hour_start and timestamp_et < new_entry_cutoff:
        return SessionPhase.POWER_HOUR, power_hour_start, new_entry_cutoff, SessionPhase.CLOSING_AUCTION
    if timestamp_et < morning_end:
        return SessionPhase.MORNING, opening_range_end, morning_end, SessionPhase.MIDDAY
    if timestamp_et < midday_end or power_hour_start >= exchange_close:
        next_phase = SessionPhase.CLOSING_AUCTION if min(midday_end, closing_start) == closing_start else SessionPhase.AFTERNOON
        return SessionPhase.MIDDAY, morning_end, min(midday_end, closing_start), next_phase
    return SessionPhase.AFTERNOON, midday_end, min(power_hour_start, closing_start), SessionPhase.POWER_HOUR


def _clock(
    event_utc: datetime,
    event_et: datetime,
    *,
    config: SessionConfig,
    session_date: str,
    exchange_open: datetime,
    exchange_close: datetime,
    early_close: bool,
    phase: SessionPhase,
    phase_start: datetime,
    phase_end: datetime,
    next_phase: SessionPhase | None,
    reason: str,
) -> SessionClock:
    in_regular = exchange_open <= event_et < exchange_close
    return SessionClock(
        session_date=session_date,
        exchange_open=exchange_open.astimezone(UTC),
        exchange_close=exchange_close.astimezone(UTC),
        early_close=early_close,
        current_phase=phase,
        minute_from_open=int((event_et - exchange_open).total_seconds() // 60) if in_regular else None,
        minutes_until_close=int((exchange_close - event_et).total_seconds() // 60) if in_regular else None,
        regular_session=in_regular,
        phase_start=phase_start.astimezone(UTC),
        phase_end=phase_end.astimezone(UTC),
        next_phase=next_phase,
        event_timestamp_utc=event_utc,
        event_timestamp_et=event_et,
        exchange_timezone=config.exchange_timezone,
        reason=reason,
    )


def _closed_clock(
    event_utc: datetime,
    event_et: datetime,
    *,
    config: SessionConfig,
    session_date: str | None,
    reason: str,
) -> SessionClock:
    return SessionClock(
        session_date=session_date,
        exchange_open=None,
        exchange_close=None,
        early_close=False,
        current_phase=SessionPhase.CLOSED,
        minute_from_open=None,
        minutes_until_close=None,
        regular_session=False,
        phase_start=None,
        phase_end=None,
        next_phase=None,
        event_timestamp_utc=event_utc,
        event_timestamp_et=event_et,
        exchange_timezone=config.exchange_timezone,
        reason=reason,
    )


def _local_datetime(session_day: date, local_time: time, exchange_tz: ZoneInfo) -> datetime:
    return datetime.combine(session_day, local_time, tzinfo=exchange_tz)


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
