from __future__ import annotations

from datetime import UTC, datetime

from backend.app.algorithms.session import SessionPhase, resolve_session_clock


def test_session_calendar_handles_dst_summer_and_winter_open() -> None:
    summer = resolve_session_clock(datetime(2026, 7, 23, 13, 30, tzinfo=UTC))
    winter = resolve_session_clock(datetime(2026, 1, 20, 14, 30, tzinfo=UTC))

    assert summer.current_phase == SessionPhase.OPENING_AUCTION
    assert summer.minute_from_open == 0
    assert summer.exchange_open.hour == 13
    assert winter.current_phase == SessionPhase.OPENING_AUCTION
    assert winter.minute_from_open == 0
    assert winter.exchange_open.hour == 14


def test_session_calendar_identifies_early_close_weekend_and_holiday() -> None:
    early_close = resolve_session_clock(datetime(2026, 11, 27, 17, 55, tzinfo=UTC))
    weekend = resolve_session_clock(datetime(2026, 7, 25, 14, 0, tzinfo=UTC))
    holiday = resolve_session_clock(datetime(2026, 7, 3, 14, 0, tzinfo=UTC))

    assert early_close.early_close is True
    assert early_close.current_phase == SessionPhase.CLOSING_AUCTION
    assert early_close.minutes_until_close == 5
    assert weekend.current_phase == SessionPhase.CLOSED
    assert holiday.current_phase == SessionPhase.CLOSED


def test_session_calendar_premarket_never_becomes_opening_bar() -> None:
    clock = resolve_session_clock(datetime(2026, 7, 23, 12, 0, tzinfo=UTC))

    assert clock.current_phase == SessionPhase.PREMARKET
    assert clock.regular_session is False
    assert clock.minute_from_open is None
