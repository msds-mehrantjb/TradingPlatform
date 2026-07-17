from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator

from backend.app.domain.models import DomainModel, _require_utc


NEW_YORK = ZoneInfo("America/New_York")


class ExchangeSession(DomainModel):
    exchange: str = "XNYS"
    sessionId: str = Field(min_length=1)
    sessionDate: date
    timezone: str = "America/New_York"
    openTimestamp: datetime | None = None
    closeTimestamp: datetime | None = None
    isTradingSession: bool
    isHoliday: bool = False
    isEarlyClose: bool = False
    isUnexpectedClosure: bool = False
    closureReason: str | None = None
    provider: str = "exchange_calendar_service"
    timestampConvention: Literal["bar_start_utc"] = "bar_start_utc"

    @field_validator("openTimestamp", "closeTimestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None

    @property
    def can_trade(self) -> bool:
        return bool(self.isTradingSession and self.openTimestamp and self.closeTimestamp and not self.isUnexpectedClosure)

    def contains_timestamp(self, value: datetime) -> bool:
        if not self.can_trade:
            return False
        timestamp = _require_utc(value)
        return bool(self.openTimestamp <= timestamp < self.closeTimestamp)

    def minutes_after_open(self, value: datetime) -> float:
        if not self.openTimestamp:
            return 0.0
        return (_require_utc(value) - self.openTimestamp).total_seconds() / 60


class ExchangeCalendarService:
    """Platform exchange-calendar boundary used by strategies and feature snapshots."""

    def session_for_date(
        self,
        session_date: date,
        *,
        exchange: str = "XNYS",
        overrides: dict[str, Any] | None = None,
    ) -> ExchangeSession:
        override = _record((overrides or {}).get(session_date.isoformat()))
        if override:
            return self.session_from_payload({**override, "sessionDate": override.get("sessionDate") or session_date.isoformat()})
        holiday_name = nyse_holiday_name(session_date)
        if session_date.weekday() >= 5 or holiday_name:
            return ExchangeSession(
                exchange=exchange,
                sessionId=f"{exchange}:{session_date.isoformat()}",
                sessionDate=session_date,
                openTimestamp=None,
                closeTimestamp=None,
                isTradingSession=False,
                isHoliday=bool(holiday_name),
                closureReason=holiday_name or "weekend",
            )
        close_time = time(13, 0) if session_date in _nyse_early_closes(session_date.year) else time(16, 0)
        local_open = datetime.combine(session_date, time(9, 30), tzinfo=NEW_YORK)
        local_close = datetime.combine(session_date, close_time, tzinfo=NEW_YORK)
        return ExchangeSession(
            exchange=exchange,
            sessionId=f"{exchange}:{session_date.isoformat()}",
            sessionDate=session_date,
            openTimestamp=local_open.astimezone(UTC),
            closeTimestamp=local_close.astimezone(UTC),
            isTradingSession=True,
            isEarlyClose=close_time != time(16, 0),
        )

    def session_from_payload(self, payload: dict[str, Any]) -> ExchangeSession:
        return ExchangeSession.model_validate(payload)

    def session_from_raw_inputs(
        self,
        raw_inputs: dict[str, Any],
        *,
        fallback_session_date: date,
    ) -> ExchangeSession:
        payload = _record(raw_inputs.get("exchangeSession"))
        if payload:
            return self.session_from_payload(payload)
        return self.session_for_date(
            fallback_session_date,
            overrides=_record(raw_inputs.get("exchangeCalendarOverrides")),
        )


def nyse_holiday_name(day: date) -> str | None:
    holidays = {
        _observed(date(day.year, 1, 1)): "New Year's Day",
        _nth_weekday(day.year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(day.year, 2, 0, 3): "Washington's Birthday",
        _good_friday(day.year): "Good Friday",
        _last_weekday(day.year, 5, 0): "Memorial Day",
        _observed(date(day.year, 6, 19)): "Juneteenth",
        _observed(date(day.year, 7, 4)): "Independence Day",
        _nth_weekday(day.year, 9, 0, 1): "Labor Day",
        _nth_weekday(day.year, 11, 3, 4): "Thanksgiving Day",
        _observed(date(day.year, 12, 25)): "Christmas Day",
    }
    return holidays.get(day)


def _nyse_early_closes(year: int) -> set[date]:
    day_after_thanksgiving = _nth_weekday(year, 11, 3, 4) + timedelta(days=1)
    christmas_eve = date(year, 12, 24)
    independence_eve = date(year, 7, 3)
    return {
        day
        for day in (day_after_thanksgiving, christmas_eve, independence_eve)
        if day.weekday() < 5 and nyse_holiday_name(day) is None
    }


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _observed(holiday: date) -> date:
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    current = date(year, month, 1)
    offset = (weekday - current.weekday()) % 7
    return current + timedelta(days=offset + ((nth - 1) * 7))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        current = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        current = date(year, month + 1, 1) - timedelta(days=1)
    return current - timedelta(days=(current.weekday() - weekday) % 7)


def _good_friday(year: int) -> date:
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
