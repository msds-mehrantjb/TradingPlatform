from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from backend.app.algorithms.meta_strategy.market_clock import (
    MetaStrategyMarketClockError,
    local_replay_market_clock,
    normalize_market_clock_payload,
)


NY = ZoneInfo("America/New_York")


class MetaStrategyMarketClockTest(unittest.TestCase):
    def test_normal_weekday_before_open(self) -> None:
        at = datetime(2026, 1, 5, 9, 0, tzinfo=NY)
        snapshot = normalize_market_clock_payload(authoritative_payload(at, is_open=False, status="closed"), evaluated_at=at)

        self.assertFalse(snapshot.is_open)
        self.assertFalse(snapshot.can_authorize_new_entries)

    def test_exactly_at_regular_open_authorizes_when_broker_clock_is_fresh(self) -> None:
        at = datetime(2026, 1, 5, 9, 30, tzinfo=NY)
        snapshot = normalize_market_clock_payload(authoritative_payload(at, is_open=True), evaluated_at=at)

        self.assertTrue(snapshot.is_open)
        self.assertTrue(snapshot.can_authorize_new_entries)

    def test_during_regular_session_authorizes(self) -> None:
        at = datetime(2026, 1, 5, 12, 15, tzinfo=NY)
        snapshot = normalize_market_clock_payload(authoritative_payload(at, is_open=True), evaluated_at=at)

        self.assertTrue(snapshot.can_authorize_new_entries)
        self.assertEqual(snapshot.regular_session_open.astimezone(NY).hour, 9)

    def test_exactly_at_regular_close_is_closed(self) -> None:
        at = datetime(2026, 1, 5, 16, 0, tzinfo=NY)
        snapshot = normalize_market_clock_payload(authoritative_payload(at, is_open=False, status="closed"), evaluated_at=at)

        self.assertFalse(snapshot.is_open)
        self.assertFalse(snapshot.can_authorize_new_entries)

    def test_weekend_and_full_holiday_are_closed_in_local_replay_calendar(self) -> None:
        weekend = normalize_market_clock_payload(local_replay_market_clock(datetime(2026, 1, 3, 12, 0, tzinfo=NY)), evaluated_at=datetime(2026, 1, 3, 12, 0, tzinfo=NY))
        holiday = normalize_market_clock_payload(local_replay_market_clock(datetime(2026, 1, 1, 12, 0, tzinfo=NY)), evaluated_at=datetime(2026, 1, 1, 12, 0, tzinfo=NY))

        self.assertFalse(weekend.is_open)
        self.assertTrue(weekend.holiday)
        self.assertFalse(holiday.is_open)
        self.assertTrue(holiday.holiday)

    def test_early_close_day_before_and_after_close(self) -> None:
        before = normalize_market_clock_payload(local_replay_market_clock(datetime(2026, 11, 27, 12, 59, tzinfo=NY)), evaluated_at=datetime(2026, 11, 27, 12, 59, tzinfo=NY))
        after = normalize_market_clock_payload(local_replay_market_clock(datetime(2026, 11, 27, 13, 0, tzinfo=NY)), evaluated_at=datetime(2026, 11, 27, 13, 0, tzinfo=NY))

        self.assertTrue(before.is_open)
        self.assertTrue(before.early_close)
        self.assertFalse(before.can_authorize_new_entries)
        self.assertFalse(after.is_open)
        self.assertTrue(after.early_close)

    def test_daylight_saving_transitions_keep_timezone_aware_boundaries(self) -> None:
        spring = normalize_market_clock_payload(authoritative_payload(datetime(2026, 3, 9, 9, 30, tzinfo=NY), is_open=True), evaluated_at=datetime(2026, 3, 9, 9, 30, tzinfo=NY))
        fall = normalize_market_clock_payload(authoritative_payload(datetime(2026, 11, 2, 9, 30, tzinfo=NY), is_open=True), evaluated_at=datetime(2026, 11, 2, 9, 30, tzinfo=NY))

        self.assertEqual(spring.regular_session_open.astimezone(NY).utcoffset(), timedelta(hours=-4))
        self.assertEqual(fall.regular_session_open.astimezone(NY).utcoffset(), timedelta(hours=-5))
        self.assertTrue(spring.can_authorize_new_entries)
        self.assertTrue(fall.can_authorize_new_entries)

    def test_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaisesRegex(MetaStrategyMarketClockError, "timezone_aware"):
            normalize_market_clock_payload(
                {"source": "broker", "capturedAt": datetime(2026, 1, 5, 9, 30), "isOpen": True},
                evaluated_at=datetime(2026, 1, 5, 9, 30, tzinfo=NY),
            )

    def test_stale_broker_clock_cannot_authorize(self) -> None:
        at = datetime(2026, 1, 5, 10, 0, tzinfo=NY)
        stale = authoritative_payload(at - timedelta(minutes=2), is_open=True)
        snapshot = normalize_market_clock_payload(stale, evaluated_at=at)

        self.assertFalse(snapshot.fresh)
        self.assertFalse(snapshot.can_authorize_new_entries)
        self.assertIn("meta_strategy.market_clock.stale", snapshot.reason_codes)

    def test_local_fallback_cannot_authorize_live_time_entry(self) -> None:
        at = datetime(2026, 1, 5, 10, 0, tzinfo=NY)
        snapshot = normalize_market_clock_payload(local_replay_market_clock(at), evaluated_at=at)

        self.assertTrue(snapshot.is_open)
        self.assertFalse(snapshot.authoritative)
        self.assertFalse(snapshot.can_authorize_new_entries)
        self.assertIn("meta_strategy.market_clock.local_fallback_not_authoritative", snapshot.reason_codes)


def authoritative_payload(at: datetime, *, is_open: bool, status: str | None = None) -> dict:
    session_date = at.astimezone(NY).date()
    return {
        "source": "alpaca_paper_clock",
        "capturedAt": at.astimezone(UTC).isoformat(),
        "dataSourceTimestamp": at.astimezone(UTC).isoformat(),
        "isOpen": is_open,
        "status": status or ("open" if is_open else "closed"),
        "nextOpen": datetime.combine(session_date, datetime.min.time().replace(hour=9, minute=30), tzinfo=NY).isoformat(),
        "nextClose": datetime.combine(session_date, datetime.min.time().replace(hour=16), tzinfo=NY).isoformat(),
        "regularSessionOpen": datetime.combine(session_date, datetime.min.time().replace(hour=9, minute=30), tzinfo=NY).isoformat(),
        "regularSessionClose": datetime.combine(session_date, datetime.min.time().replace(hour=16), tzinfo=NY).isoformat(),
        "authoritativeReadOnly": True,
    }


if __name__ == "__main__":
    unittest.main()
