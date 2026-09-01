from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.app.algorithms.voting_ensemble.finalized_bar_producer import (
    _entry_window_open,
    _minute_of_day,
    _new_york_minute_of_day,
)
from backend.app.algorithms.voting_ensemble.trading_settings.resolver import (
    resolve_one_minute_trading_settings,
)


def bar_ending_at(eastern: str, *, month: int = 7) -> datetime:
    """A bar-end timestamp in UTC for the given exchange-local time."""
    hour, minute = (int(part) for part in eastern.split(":"))
    offset = 4 if month == 7 else 5  # EDT in July, EST in January
    return datetime(2026, month, 14, hour + offset, minute, tzinfo=timezone.utc)


class VotingEnsembleEntryWindowTest(unittest.TestCase):
    """The new-entry cutoff has to be the same rule live and in replay.

    entryWindowOpen was market_open, which said nothing the market-open gate did not
    already say. Live took new entries until the closing bell while
    run_voting_ensemble_backtest stopped at newTradesUntil, so the two ran different entry
    rules and replay understated late-session activity.
    """

    def setUp(self) -> None:
        self.settings = resolve_one_minute_trading_settings()

    def open_at(self, eastern: str, *, month: int = 7) -> bool:
        return _entry_window_open(
            market_open=True, settings=self.settings, bar_end=bar_ending_at(eastern, month=month)
        )

    def test_the_configured_window_is_the_one_being_applied(self) -> None:
        windows = self.settings.sessionWindows

        self.assertEqual(windows.sessionStart, "09:35")
        self.assertEqual(windows.newTradesUntil, "15:30")

    def test_entries_are_open_through_the_session_and_shut_after_the_cutoff(self) -> None:
        for eastern, expected in (
            ("09:34", False),  # before sessionStart
            ("09:35", True),   # the boundary itself is open
            ("12:00", True),
            ("15:29", True),
            ("15:30", True),   # newTradesUntil is inclusive
            ("15:31", False),  # the gap that used to stay open live
            ("15:59", False),
        ):
            with self.subTest(eastern=eastern):
                self.assertIs(self.open_at(eastern), expected)

    def test_a_closed_market_is_never_an_open_entry_window(self) -> None:
        self.assertFalse(
            _entry_window_open(market_open=False, settings=self.settings, bar_end=bar_ending_at("12:00"))
        )

    def test_the_cutoff_holds_across_daylight_saving(self) -> None:
        """The window is exchange-local, so it must not drift by an hour in winter."""
        for month in (1, 7):
            with self.subTest(month=month):
                self.assertTrue(self.open_at("15:30", month=month))
                self.assertFalse(self.open_at("15:31", month=month))

    def test_an_unresolved_window_keeps_the_previous_behaviour(self) -> None:
        """A settings problem must not silently halt every entry.

        Trading a little late is bounded; an unexplained full stop is not, so an absent
        window falls back to what this returned before the cutoff was applied.
        """
        self.assertTrue(
            _entry_window_open(market_open=True, settings=object(), bar_end=bar_ending_at("15:45"))
        )

    def test_boundary_parsing_rejects_what_it_cannot_read(self) -> None:
        self.assertEqual(_minute_of_day("15:30"), 930)
        self.assertEqual(_minute_of_day("09:35"), 575)
        for bad in (None, "", "1530", "not:anumber"):
            with self.subTest(value=bad):
                self.assertIsNone(_minute_of_day(bad))

    def test_exchange_local_minutes_track_the_zone_not_utc(self) -> None:
        # 20:29Z is 15:29 in January (EST) and 16:29 in July (EDT).
        self.assertEqual(_new_york_minute_of_day(datetime(2026, 1, 14, 20, 29, tzinfo=timezone.utc)), 929)
        self.assertEqual(_new_york_minute_of_day(datetime(2026, 7, 14, 20, 29, tzinfo=timezone.utc)), 989)


if __name__ == "__main__":
    unittest.main()
