"""DST-aware NYSE/Nasdaq session calendar helpers for Regime classification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


EXCHANGE_TIMEZONE = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)
OPENING_MINUTES = 30
CLOSING_MINUTES = 30


@dataclass(frozen=True)
class ExchangeSession:
    status: str
    timestamp_et: str | None
    session_date: str | None
    market_open_et: str | None
    market_close_et: str | None
    reason: str
    is_early_close: bool = False
    minutes_from_open: int | None = None
    minutes_to_close: int | None = None


def regime_session_axis(timestamp: str) -> str:
    return exchange_session(timestamp).status


def exchange_session(timestamp: str) -> ExchangeSession:
    parsed = parse_exchange_timestamp(timestamp)
    if parsed is None:
        return ExchangeSession(
            status="outside_regular",
            timestamp_et=None,
            session_date=None,
            market_open_et=None,
            market_close_et=None,
            reason="invalid_timestamp",
        )
    session_day = parsed.date()
    calendar = exchange_session_bounds(session_day)
    if calendar is None:
        return ExchangeSession(
            status="outside_regular",
            timestamp_et=parsed.isoformat(),
            session_date=session_day.isoformat(),
            market_open_et=None,
            market_close_et=None,
            reason="holiday_or_weekend",
        )
    market_open, market_close, early_close = calendar
    if parsed < market_open or parsed >= market_close:
        return ExchangeSession(
            status="outside_regular",
            timestamp_et=parsed.isoformat(),
            session_date=session_day.isoformat(),
            market_open_et=market_open.isoformat(),
            market_close_et=market_close.isoformat(),
            reason="outside_exchange_session",
            is_early_close=early_close,
        )
    minutes_from_open = int((parsed - market_open).total_seconds() // 60)
    minutes_to_close = int((market_close - parsed).total_seconds() // 60)
    if minutes_from_open < OPENING_MINUTES:
        status = "opening"
    elif minutes_to_close <= CLOSING_MINUTES:
        status = "closing"
    elif parsed.time() < time(13, 0):
        status = "midday"
    else:
        status = "afternoon"
    return ExchangeSession(
        status=status,
        timestamp_et=parsed.isoformat(),
        session_date=session_day.isoformat(),
        market_open_et=market_open.isoformat(),
        market_close_et=market_close.isoformat(),
        reason="regular_exchange_session",
        is_early_close=early_close,
        minutes_from_open=minutes_from_open,
        minutes_to_close=minutes_to_close,
    )


def parse_exchange_timestamp(timestamp: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(EXCHANGE_TIMEZONE)


def exchange_session_bounds(session_day: date) -> tuple[datetime, datetime, bool] | None:
    if session_day.weekday() >= 5 or session_day in market_holidays(session_day.year):
        return None
    close_time = EARLY_CLOSE if session_day in early_close_days(session_day.year) else REGULAR_CLOSE
    return (
        datetime.combine(session_day, REGULAR_OPEN, EXCHANGE_TIMEZONE),
        datetime.combine(session_day, close_time, EXCHANGE_TIMEZONE),
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
    actual_christmas = date(year, 12, 25)
    if actual_christmas.weekday() == 0:
        return date(year, 12, 22)
    if actual_christmas.weekday() == 6:
        return date(year, 12, 23)
    return date(year, 12, 24)


def independence_day_early_close(year: int) -> date:
    actual = date(year, 7, 4)
    if actual.weekday() == 0:
        return date(year, 7, 1)
    if actual.weekday() in {1, 2, 3, 4}:
        return actual - timedelta(days=1)
    return date(year, 7, 3)


def day_after_thanksgiving(year: int) -> date:
    return nth_weekday(year, 11, 3, 4) + timedelta(days=1)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    current = date(year, month, 1)
    days_until = (weekday - current.weekday()) % 7
    return current + timedelta(days=days_until + ((n - 1) * 7))


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
