from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import backend.app.algorithms.voting_ensemble.finalized_bar_producer as producer
from backend.app.algorithms.voting_ensemble.backtest import VotingEnsembleBacktestRunner
from backend.app.algorithms.voting_ensemble.backtest_config import VotingEnsembleBacktestConfig

NEW_YORK = ZoneInfo("America/New_York")


class _Settings:
    class sessionWindows:
        sessionStart = "09:35"
        newTradesUntil = "15:30"


def runner(**config) -> VotingEnsembleBacktestRunner:
    instance = object.__new__(VotingEnsembleBacktestRunner)
    instance.config = VotingEnsembleBacktestConfig(**config)
    return instance


def bar(hour: int, minute: int, *, day: int = 15, month: int = 7) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=NEW_YORK)


class LiveReplayParityTest(unittest.TestCase):
    """Live and replay must reach the same verdict for the same bar.

    They do not share a payload builder: the live path constructs a finalized-bar event and
    the replay runner builds a snapshot directly. That structural difference is tolerable
    only while the fields that decide anything agree, and nothing enforces that by
    construction -- so it is enforced here instead.

    Each of these agreed only after a specific defect was fixed. The event veto was lost in
    a snapshot round trip, the session segment was produced by nothing, and replay applied no
    entry window at all. All three looked fine from one side.
    """

    BARS = ((9, 34), (9, 36), (10, 30), (12, 0), (15, 0), (15, 30), (15, 31), (15, 52))

    def test_the_session_segment_agrees_bar_for_bar(self) -> None:
        replay = runner()
        for hour, minute in self.BARS:
            with self.subTest(time=f"{hour:02d}:{minute:02d}"):
                moment = bar(hour, minute)
                live = producer._session_state({"isOpen": True}, settings=_Settings, bar_end=moment)

                self.assertEqual(replay._session_segment_at(moment, "SPY"), live["sessionSegment"])

    def test_the_entry_window_agrees_bar_for_bar(self) -> None:
        replay = runner()
        for hour, minute in self.BARS:
            with self.subTest(time=f"{hour:02d}:{minute:02d}"):
                moment = bar(hour, minute)
                live = producer._entry_window_open(market_open=True, settings=_Settings, bar_end=moment)
                replayed = replay._operational_snapshot("SPY", bar_end=moment)["entryWindowOpen"]

                self.assertEqual(replayed, live)

    def test_the_entry_window_closes_on_both_sides_before_the_bell(self) -> None:
        """The specific behaviour, asserted directly rather than only as agreement.

        Two implementations can agree by being wrong in the same way, so the rule itself is
        pinned: 15:30 is the last bar that may open a position, 15:31 is not.
        """
        replay = runner()

        self.assertTrue(replay._operational_snapshot("SPY", bar_end=bar(15, 30))["entryWindowOpen"])
        self.assertFalse(replay._operational_snapshot("SPY", bar_end=bar(15, 31))["entryWindowOpen"])
        self.assertTrue(producer._entry_window_open(market_open=True, settings=_Settings, bar_end=bar(15, 30)))
        self.assertFalse(producer._entry_window_open(market_open=True, settings=_Settings, bar_end=bar(15, 31)))

    def test_the_event_veto_agrees_bar_for_bar(self) -> None:
        from backend.app.algorithms.voting_ensemble.event_calendar import (
            event_calendar_from_payload,
            resolve_event_veto,
        )

        calendar = {
            "enabled": True,
            "events": [
                {
                    "eventType": "CPI",
                    "eventFamily": "Inflation",
                    "importance": "high",
                    "scheduledAt": "2026-07-15T16:00:00Z",
                }
            ],
        }
        replay = runner(eventCalendar=calendar)
        settings = event_calendar_from_payload(calendar)

        for hour, minute in self.BARS:
            with self.subTest(time=f"{hour:02d}:{minute:02d}"):
                moment = bar(hour, minute)
                live = resolve_event_veto(bar_end=moment, settings=settings).as_event_state()

                self.assertEqual(replay._event_state_at(moment, "SPY"), live)


if __name__ == "__main__":
    unittest.main()
