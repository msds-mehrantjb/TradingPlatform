from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from backend.app.algorithms.session import SessionConfig, SessionPhase, classify_session, resolve_session_clock


def test_session_step3_winter_and_summer_open_resolve_0930_et() -> None:
    winter = resolve_session_clock("2026-01-05T14:30:00Z")
    summer = resolve_session_clock("2026-07-23T13:30:00Z")

    assert winter.current_phase == SessionPhase.OPENING_AUCTION
    assert winter.regular_session is True
    assert winter.minute_from_open == 0
    assert winter.exchange_open == datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    assert winter.exchange_close == datetime(2026, 1, 5, 21, 0, tzinfo=UTC)

    assert summer.current_phase == SessionPhase.OPENING_AUCTION
    assert summer.regular_session is True
    assert summer.minute_from_open == 0
    assert summer.exchange_open == datetime(2026, 7, 23, 13, 30, tzinfo=UTC)
    assert summer.exchange_close == datetime(2026, 7, 23, 20, 0, tzinfo=UTC)


def test_session_step3_dst_transition_weeks_use_exchange_timezone() -> None:
    before_spring = resolve_session_clock("2026-03-06T14:30:00Z")
    after_spring = resolve_session_clock("2026-03-09T13:30:00Z")
    before_fall = resolve_session_clock("2026-10-30T13:30:00Z")
    after_fall = resolve_session_clock("2026-11-02T14:30:00Z")

    assert before_spring.current_phase == SessionPhase.OPENING_AUCTION
    assert after_spring.current_phase == SessionPhase.OPENING_AUCTION
    assert before_fall.current_phase == SessionPhase.OPENING_AUCTION
    assert after_fall.current_phase == SessionPhase.OPENING_AUCTION
    assert before_spring.exchange_open == datetime(2026, 3, 6, 14, 30, tzinfo=UTC)
    assert after_spring.exchange_open == datetime(2026, 3, 9, 13, 30, tzinfo=UTC)
    assert before_fall.exchange_open == datetime(2026, 10, 30, 13, 30, tzinfo=UTC)
    assert after_fall.exchange_open == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)


def test_session_step3_july_premarket_cannot_be_opening_regular_session() -> None:
    premarket = resolve_session_clock("2026-07-23T13:29:59Z")
    opening = resolve_session_clock("2026-07-23T13:30:00Z")
    opening_range = resolve_session_clock("2026-07-23T13:35:00Z")

    assert premarket.current_phase == SessionPhase.PREMARKET
    assert premarket.regular_session is False
    assert premarket.minute_from_open is None
    assert opening.current_phase == SessionPhase.OPENING_AUCTION
    assert opening.regular_session is True
    assert opening_range.current_phase == SessionPhase.OPENING_RANGE


def test_session_step3_early_close_changes_close_and_phase_cutoff() -> None:
    midday = resolve_session_clock("2026-11-27T17:30:00Z")
    closing = resolve_session_clock("2026-11-27T17:50:00Z")
    postmarket = resolve_session_clock("2026-11-27T18:00:00Z")

    assert midday.early_close is True
    assert midday.exchange_close == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    assert midday.current_phase == SessionPhase.MIDDAY
    assert midday.next_phase == SessionPhase.CLOSING_AUCTION
    assert closing.current_phase == SessionPhase.CLOSING_AUCTION
    assert closing.minutes_until_close == 10
    assert postmarket.current_phase == SessionPhase.POSTMARKET
    assert postmarket.regular_session is False


@pytest.mark.parametrize(
    "timestamp",
    (
        "2026-07-03T14:30:00Z",
        "2026-12-25T15:00:00Z",
        "2026-07-25T14:30:00Z",
    ),
)
def test_session_step3_holidays_and_weekends_are_closed(timestamp: str) -> None:
    clock = resolve_session_clock(timestamp)

    assert clock.current_phase == SessionPhase.CLOSED
    assert clock.regular_session is False
    assert clock.exchange_open is None
    assert clock.exchange_close is None


def test_session_step3_premarket_postmarket_and_closed_market_timestamps() -> None:
    closed = resolve_session_clock("2026-07-23T07:00:00Z")
    premarket = resolve_session_clock("2026-07-23T12:00:00Z")
    postmarket = resolve_session_clock("2026-07-23T21:00:00Z")
    late_closed = resolve_session_clock("2026-07-24T00:00:00Z")

    assert closed.current_phase == SessionPhase.CLOSED
    assert premarket.current_phase == SessionPhase.PREMARKET
    assert postmarket.current_phase == SessionPhase.POSTMARKET
    assert late_closed.current_phase == SessionPhase.CLOSED


@pytest.mark.parametrize(
    ("timestamp", "phase", "next_phase"),
    (
        ("2026-07-23T13:30:00Z", SessionPhase.OPENING_AUCTION, SessionPhase.OPENING_DISCOVERY),
        ("2026-07-23T13:30:01Z", SessionPhase.OPENING_DISCOVERY, SessionPhase.OPENING_RANGE),
        ("2026-07-23T13:35:00Z", SessionPhase.OPENING_RANGE, SessionPhase.MORNING),
        ("2026-07-23T14:00:00Z", SessionPhase.MORNING, SessionPhase.MIDDAY),
        ("2026-07-23T15:30:00Z", SessionPhase.MIDDAY, SessionPhase.AFTERNOON),
        ("2026-07-23T18:00:00Z", SessionPhase.AFTERNOON, SessionPhase.POWER_HOUR),
        ("2026-07-23T19:00:00Z", SessionPhase.POWER_HOUR, SessionPhase.CLOSING_AUCTION),
        ("2026-07-23T19:45:00Z", SessionPhase.CLOSING_AUCTION, SessionPhase.POSTMARKET),
        ("2026-07-23T20:00:00Z", SessionPhase.POSTMARKET, SessionPhase.CLOSED),
    ),
)
def test_session_step3_exact_phase_boundaries(timestamp: str, phase: SessionPhase, next_phase: SessionPhase) -> None:
    clock = resolve_session_clock(timestamp)

    assert clock.current_phase == phase
    assert clock.next_phase == next_phase
    assert clock.phase_start is None or clock.phase_start.tzinfo == UTC
    assert clock.phase_end is None or clock.phase_end.tzinfo == UTC


def test_session_step3_local_timezone_does_not_affect_aware_inputs() -> None:
    utc_timestamp = datetime(2026, 7, 23, 13, 35, tzinfo=UTC)
    pacific_equivalent = datetime(2026, 7, 23, 6, 35, tzinfo=timezone(timedelta(hours=-7)))

    assert resolve_session_clock(utc_timestamp).as_dict() == resolve_session_clock(pacific_equivalent).as_dict()


def test_session_step3_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_session_clock(datetime(2026, 7, 23, 13, 30))


def test_session_step3_classifier_evidence_uses_authoritative_clock() -> None:
    classification = classify_session("SPY", [_candle(index) for index in range(30)])

    assert classification.phase == SessionPhase.OPENING_RANGE
    assert classification.session_date == "2026-07-23"
    assert classification.evidence["sessionClock"]["exchangeOpen"] == "2026-07-23T13:30:00+00:00"
    assert classification.evidence["sessionClock"]["currentPhase"] == "opening_range"


def test_session_step3_boundaries_are_configurable() -> None:
    config = SessionConfig(opening_range_minutes=20, closing_auction_minutes=30)

    assert resolve_session_clock("2026-07-23T13:50:00Z", config=config).current_phase == SessionPhase.MORNING
    assert resolve_session_clock("2026-07-23T19:30:00Z", config=config).current_phase == SessionPhase.CLOSING_AUCTION


def _candle(index: int) -> dict[str, object]:
    close = 100 + index * 0.02
    return {
        "timestamp": (datetime(2026, 7, 23, 13, 30, tzinfo=UTC) + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
        "open": close - 0.03,
        "high": close + 0.08,
        "low": close - 0.07,
        "close": close,
        "volume": 100_000,
    }
