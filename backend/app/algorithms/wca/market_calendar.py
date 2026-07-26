"""WCA-owned market-calendar wrapper for protective position management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


WCA_MARKET_CALENDAR_VERSION = "wca_market_calendar_v1"
EXCHANGE_TIMEZONE = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


@dataclass(frozen=True)
class WcaMarketSession:
    session_date: date
    market_open: datetime
    market_close: datetime
    is_early_close: bool


class WcaMarketCalendar:
    def session_for(self, timestamp: datetime) -> WcaMarketSession | None:
        parsed = timestamp.astimezone(EXCHANGE_TIMEZONE)
        bounds = self.session_bounds(parsed.date())
        if bounds is None:
            return None
        market_open, market_close, early = bounds
        return WcaMarketSession(parsed.date(), market_open, market_close, early)

    def session_bounds(self, session_day: date) -> tuple[datetime, datetime, bool] | None:
        if session_day.weekday() >= 5 or session_day in market_holidays(session_day.year):
            return None
        close_time = EARLY_CLOSE if session_day in early_close_days(session_day.year) else REGULAR_CLOSE
        return (
            datetime.combine(session_day, REGULAR_OPEN, EXCHANGE_TIMEZONE),
            datetime.combine(session_day, close_time, EXCHANGE_TIMEZONE),
            close_time == EARLY_CLOSE,
        )

    def should_flatten(self, timestamp: datetime, *, buffer_minutes: int) -> bool:
        session = self.session_for(timestamp)
        if session is None:
            return True
        parsed = timestamp.astimezone(EXCHANGE_TIMEZONE)
        return parsed >= session.market_close - timedelta(minutes=max(0, buffer_minutes))


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
    candidates = {day_after_thanksgiving(year), christmas_eve_early_close(year), independence_day_early_close(year)}
    return {day for day in candidates if day.year == year and day.weekday() < 5 and day not in market_holidays(year)}


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
    return current + timedelta(days=((weekday - current.weekday()) % 7) + ((n - 1) * 7))


def last_weekday(year: int, month: int, weekday: int) -> date:
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    current = next_month - timedelta(days=1)
    return current - timedelta(days=(current.weekday() - weekday) % 7)


def good_friday(year: int) -> date:
    # Anonymous Gregorian Easter algorithm; Good Friday is two days before Easter Sunday.
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
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day) - timedelta(days=2)


__all__ = ["WCA_MARKET_CALENDAR_VERSION", "WcaMarketCalendar", "WcaMarketSession"]
