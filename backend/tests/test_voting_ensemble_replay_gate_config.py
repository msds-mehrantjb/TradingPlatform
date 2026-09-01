from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.voting_ensemble.backtest import VotingEnsembleBacktestRunner
from backend.app.algorithms.voting_ensemble.backtest_config import VotingEnsembleBacktestConfig
from backend.app.algorithms.voting_ensemble.event_calendar import (
    event_calendar_from_payload,
    resolve_event_veto,
)
from backend.app.algorithms.voting_ensemble.strategies.context.pipeline import _event_blackout_active


CPI = datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc)
CALENDAR = {
    "enabled": True,
    "events": [
        {
            "eventType": "CPI",
            "eventFamily": "Inflation",
            "importance": "high",
            "scheduledAt": CPI.isoformat(),
        }
    ],
}
SESSION_POLICY = {
    "enabled": True,
    "segments": {"close": {"tradable": True, "permittedStrategies": ["bollinger_band_reversion"]}},
}


def runner_with(**config) -> VotingEnsembleBacktestRunner:
    """A runner with only the config attached, for the resolution path under test."""
    runner = object.__new__(VotingEnsembleBacktestRunner)
    runner.config = VotingEnsembleBacktestConfig(**config)
    return runner


class ReplayGateConfigTest(unittest.TestCase):
    """A baseline recorded without its gate configuration cannot be reproduced.

    The calendar and the segment map are what decide which bars were vetoed and whose vote
    counted, so replay has to be able to run under the same ones the live run used.
    """

    def test_the_backtest_config_carries_both_gate_configurations(self) -> None:
        config = VotingEnsembleBacktestConfig(sessionPolicy=SESSION_POLICY, eventCalendar=CALENDAR)

        self.assertEqual(config.sessionPolicy, SESSION_POLICY)
        self.assertEqual(config.eventCalendar, CALENDAR)

    def test_an_unconfigured_replay_vetoes_nothing(self) -> None:
        """The shipped default must leave recorded baselines untouched."""
        runner = runner_with()

        self.assertFalse(_event_blackout_active(runner._event_state_at(CPI - timedelta(minutes=5))))
        self.assertFalse(_event_blackout_active(runner._event_state_at(CPI)))

    def test_a_configured_replay_vetoes_the_same_bars_as_live(self) -> None:
        runner = runner_with(eventCalendar=CALENDAR)

        self.assertTrue(_event_blackout_active(runner._event_state_at(CPI - timedelta(minutes=5))))
        self.assertFalse(_event_blackout_active(runner._event_state_at(CPI - timedelta(minutes=90))))

    def test_replay_and_the_live_producer_agree_bar_for_bar(self) -> None:
        """Same module, same bar-end input: one implementation, not two that resemble each other."""
        runner = runner_with(eventCalendar=CALENDAR)
        calendar = event_calendar_from_payload(CALENDAR)

        for offset in (-90, -16, -15, -1, 0, 10, 30, 31):
            with self.subTest(offset=offset):
                bar_end = CPI + timedelta(minutes=offset)
                live = resolve_event_veto(bar_end=bar_end, settings=calendar).as_event_state()

                self.assertEqual(runner._event_state_at(bar_end), live)

    def test_the_veto_state_carries_provenance_so_the_snapshot_accepts_it(self) -> None:
        """An event payload without provenance is a freshness failure, and rightly so."""
        state = runner_with(eventCalendar=CALENDAR)._event_state_at(CPI)

        self.assertIsNotNone(state["providerTimestamp"])
        self.assertIsNotNone(state["receiptTimestamp"])

    def test_a_malformed_calendar_in_replay_falls_back_to_no_veto(self) -> None:
        runner = runner_with(eventCalendar={"enabled": True, "events": [{"eventType": "CPI"}]})

        self.assertFalse(_event_blackout_active(runner._event_state_at(CPI)))


if __name__ == "__main__":
    unittest.main()
