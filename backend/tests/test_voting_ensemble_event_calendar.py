from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.voting_ensemble.event_calendar import (
    EVENT_REASON_AUCTION,
    EVENT_REASON_CLEAR,
    EVENT_REASON_DISABLED,
    EVENT_REASON_ROLL,
    EVENT_REASON_SCHEDULED,
    EVENT_STATE_CLEAR,
    default_event_calendar,
    event_calendar_from_payload,
    resolve_event_veto,
)
from backend.app.algorithms.voting_ensemble.strategies.context.pipeline import _event_blackout_active


FOMC = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)  # 14:00 ET


def calendar_with_fomc(**overrides):
    payload = {
        "enabled": True,
        "events": [
            {
                "eventType": "FOMC statement",
                "eventFamily": "Federal Reserve",
                "importance": "high",
                "scheduledAt": FOMC.isoformat(),
                "preEventBlackoutMinutes": 15,
                "postEventStabilizationMinutes": 30,
            }
        ],
    }
    payload.update(overrides)
    return event_calendar_from_payload(payload)


class EventCalendarDefaultsTest(unittest.TestCase):
    def test_the_veto_ships_disabled(self) -> None:
        self.assertFalse(default_event_calendar().enabled)

    def test_disabled_never_blocks(self) -> None:
        decision = resolve_event_veto(bar_end=FOMC)

        self.assertFalse(decision.blackout_active)
        self.assertIn(EVENT_REASON_DISABLED, decision.reason_codes)

    def test_a_malformed_calendar_falls_back_to_disabled(self) -> None:
        """A veto covering only some of its events hides the gap it leaves."""
        self.assertFalse(event_calendar_from_payload("nonsense").enabled)
        self.assertFalse(event_calendar_from_payload(None).enabled)

    def test_an_undated_event_is_skipped_rather_than_guessed(self) -> None:
        calendar = event_calendar_from_payload(
            {"enabled": True, "events": [{"eventType": "CPI", "importance": "high"}]}
        )

        self.assertEqual(calendar.events, ())


class ScheduledEventVetoTest(unittest.TestCase):
    def veto_at(self, offset_minutes: int, calendar=None):
        return resolve_event_veto(
            bar_end=FOMC + timedelta(minutes=offset_minutes), settings=calendar or calendar_with_fomc()
        )

    def test_the_window_opens_before_and_closes_after_the_event(self) -> None:
        for offset, blocked in ((-40, False), (-16, False), (-15, True), (-1, True), (0, True), (20, True), (30, True), (31, False)):
            with self.subTest(offset=offset):
                self.assertIs(self.veto_at(offset).blackout_active, blocked)

    def test_a_blocked_bar_reaches_the_gate_predicate(self) -> None:
        """This is the gap: the gate existed and nothing ever fed it."""
        state = self.veto_at(-5).as_event_state()

        self.assertTrue(_event_blackout_active(state))
        self.assertEqual(state["activeEvent"], "FOMC statement")

    def test_a_clear_bar_does_not_trip_the_gate(self) -> None:
        state = self.veto_at(-40).as_event_state()

        self.assertFalse(_event_blackout_active(state))
        self.assertEqual(state["state"], EVENT_STATE_CLEAR)
        self.assertIn(EVENT_REASON_CLEAR, self.veto_at(-40).reason_codes)

    def test_a_low_importance_event_is_reported_but_does_not_block(self) -> None:
        """A minor print should not halt the day."""
        calendar = event_calendar_from_payload(
            {
                "enabled": True,
                "events": [
                    {"eventType": "housing starts", "importance": "low", "scheduledAt": FOMC.isoformat()}
                ],
            }
        )

        self.assertFalse(self.veto_at(0, calendar).blackout_active)

    def test_the_scheduled_reason_is_reported(self) -> None:
        self.assertIn(EVENT_REASON_SCHEDULED, self.veto_at(-5).reason_codes)


class AuctionAndRollVetoTest(unittest.TestCase):
    def test_the_opening_and_closing_auctions_are_blocked(self) -> None:
        calendar = calendar_with_fomc()
        opening = resolve_event_veto(bar_end=datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc), settings=calendar)
        closing = resolve_event_veto(bar_end=datetime(2026, 7, 14, 19, 58, tzinfo=timezone.utc), settings=calendar)

        self.assertEqual(opening.active_event, "opening_auction")
        self.assertEqual(closing.active_event, "closing_auction")
        for decision in (opening, closing):
            self.assertTrue(decision.blackout_active)
            self.assertIn(EVENT_REASON_AUCTION, decision.reason_codes)

    def test_auction_blocking_can_be_turned_off_independently(self) -> None:
        calendar = calendar_with_fomc(blockOpeningAuction=False, blockClosingAuction=False)

        self.assertFalse(
            resolve_event_veto(bar_end=datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc), settings=calendar).blackout_active
        )

    def test_mid_session_is_not_an_auction(self) -> None:
        calendar = calendar_with_fomc()

        self.assertFalse(
            resolve_event_veto(bar_end=datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc), settings=calendar).blackout_active
        )

    def test_contract_roll_blocks_the_days_before_the_roll(self) -> None:
        calendar = event_calendar_from_payload(
            {"enabled": True, "contractRollDates": ["2026-07-15"], "contractRollBlackoutDays": 1}
        )

        blocked = resolve_event_veto(bar_end=datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc), settings=calendar)
        clear = resolve_event_veto(bar_end=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc), settings=calendar)

        self.assertTrue(blocked.blackout_active)
        self.assertIn(EVENT_REASON_ROLL, blocked.reason_codes)
        self.assertFalse(clear.blackout_active)

    def test_no_roll_dates_means_no_roll_blackout(self) -> None:
        """Inert until an instrument actually declares a roll date."""
        self.assertFalse(
            resolve_event_veto(
                bar_end=datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc), settings=calendar_with_fomc()
            ).blackout_active
        )


class EventVetoTimezoneTest(unittest.TestCase):
    def test_auction_windows_are_exchange_local_across_daylight_saving(self) -> None:
        calendar = event_calendar_from_payload({"enabled": True})

        # 09:30 ET is 13:30Z in July (EDT) and 14:30Z in January (EST).
        for moment in (
            datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc),
            datetime(2026, 1, 14, 14, 30, tzinfo=timezone.utc),
        ):
            with self.subTest(moment=moment):
                self.assertEqual(resolve_event_veto(bar_end=moment, settings=calendar).active_event, "opening_auction")


if __name__ == "__main__":
    unittest.main()


class ContractRollTest(unittest.TestCase):
    """The roll veto was written and left inert: nothing ever supplied a roll date.

    Liquidity splits across two contracts as a future approaches expiry, so the quotes the
    algorithm sizes against stop describing one market. The machinery to refuse that window
    already existed; only the dates were missing.
    """

    def runner(self, **calendar):
        from backend.app.algorithms.voting_ensemble.backtest import VotingEnsembleBacktestRunner
        from backend.app.algorithms.voting_ensemble.backtest_config import VotingEnsembleBacktestConfig

        runner = object.__new__(VotingEnsembleBacktestRunner)
        runner.config = VotingEnsembleBacktestConfig(eventCalendar={"enabled": True, **calendar})
        return runner

    def blackout(self, runner, day: int, symbol: str, month: int = 9) -> bool:
        from datetime import datetime, timezone

        state = runner._event_state_at(datetime(2026, month, day, 17, 0, tzinfo=timezone.utc), symbol)
        return bool(state["eventBlackoutActive"])

    def test_the_imm_dates_are_the_third_friday_of_the_quarter_months(self) -> None:
        from backend.app.algorithms.voting_ensemble.event_calendar import quarterly_imm_roll_dates

        dates = quarterly_imm_roll_dates(2026)

        self.assertEqual([d.isoformat() for d in dates], ["2026-03-20", "2026-06-19", "2026-09-18", "2026-12-18"])
        for value in dates:
            with self.subTest(date=value):
                self.assertEqual(value.weekday(), 4)  # Friday
                self.assertTrue(15 <= value.day <= 21)  # the third one

    def test_a_future_stops_entering_into_its_expiry(self) -> None:
        runner = self.runner()

        self.assertFalse(self.blackout(runner, 16, "MES"))
        self.assertTrue(self.blackout(runner, 17, "MES"))
        self.assertTrue(self.blackout(runner, 18, "MES"))
        self.assertFalse(self.blackout(runner, 19, "MES"))

    def test_an_equity_never_rolls(self) -> None:
        runner = self.runner()

        for day in (17, 18):
            with self.subTest(day=day):
                self.assertFalse(self.blackout(runner, day, "SPY"))

    def test_the_roll_window_is_configurable(self) -> None:
        """A desk that stands aside for the whole roll week says so."""
        runner = self.runner(contractRollBlackoutDays=7)

        self.assertFalse(self.blackout(runner, 10, "MES"))
        self.assertTrue(self.blackout(runner, 11, "MES"))
        self.assertTrue(self.blackout(runner, 18, "MES"))

    def test_a_configured_calendar_keeps_its_own_dates(self) -> None:
        """An explicit list wins; a desk's known roll calendar is not silently replaced."""
        from datetime import date

        from backend.app.algorithms.voting_ensemble.event_calendar import (
            calendar_with_instrument_rolls,
            event_calendar_from_payload,
        )
        from backend.app.market_feed import instrument_for_symbol

        configured = event_calendar_from_payload(
            {"enabled": True, "contractRollDates": ["2026-08-14"]}
        )
        merged = calendar_with_instrument_rolls(
            configured, instrument_for_symbol("MES"), around=date(2026, 9, 14)
        )

        self.assertEqual(merged.contract_roll_dates, configured.contract_roll_dates)

    def test_the_roll_respects_the_calendar_switch(self) -> None:
        """Like every other veto here, it ships off and is turned on deliberately."""
        from datetime import datetime, timezone

        from backend.app.algorithms.voting_ensemble.backtest import VotingEnsembleBacktestRunner
        from backend.app.algorithms.voting_ensemble.backtest_config import VotingEnsembleBacktestConfig

        runner = object.__new__(VotingEnsembleBacktestRunner)
        runner.config = VotingEnsembleBacktestConfig()

        state = runner._event_state_at(datetime(2026, 9, 18, 17, 0, tzinfo=timezone.utc), "MES")
        self.assertFalse(state["eventBlackoutActive"])


class CalendarStalenessTest(unittest.TestCase):
    """A rotted calendar is indistinguishable from a calm market, and fails open.

    The veto scans a dated event list. If nobody updates that list, no event covers the bar,
    the veto reports clear, and entries proceed straight through FOMC day on the strength of
    an empty file. That is the failure this closes: silence has to mean "checked and clear",
    not "nobody looked".
    """

    CPI = {
        "eventType": "CPI",
        "eventFamily": "Inflation",
        "importance": "high",
        "scheduledAt": "2026-01-15T13:30:00Z",
    }

    def veto(self, when: str, **payload):
        from datetime import datetime

        from backend.app.algorithms.voting_ensemble.event_calendar import (
            event_calendar_from_payload,
            resolve_event_veto,
        )

        return resolve_event_veto(
            bar_end=datetime.fromisoformat(when),
            settings=event_calendar_from_payload({"enabled": True, **payload}),
        )

    def test_a_recent_calendar_is_trusted(self) -> None:
        decision = self.veto("2026-02-14T14:00:00+00:00", events=[self.CPI])

        self.assertFalse(decision.blackout_active)
        self.assertEqual(decision.state, "clear")

    def test_a_calendar_nobody_has_updated_stops_entries(self) -> None:
        decision = self.veto("2026-08-03T14:00:00+00:00", events=[self.CPI])

        self.assertTrue(decision.blackout_active)
        self.assertEqual(decision.state, "stale")
        self.assertIn("voting_ensemble.event_calendar.stale", decision.reason_codes)

    def test_an_explicit_coverage_date_is_the_honest_signal(self) -> None:
        """A calendar that says how far it reaches is believed over the age of its contents."""
        inside = self.veto("2026-08-03T14:00:00+00:00", events=[self.CPI], validUntil="2026-12-31")
        expired = self.veto("2026-08-03T14:00:00+00:00", events=[self.CPI], validUntil="2026-06-30")

        self.assertFalse(inside.blackout_active)
        self.assertTrue(expired.blackout_active)
        self.assertEqual(expired.state, "stale")

    def test_the_staleness_window_is_configurable(self) -> None:
        recent = self.veto("2026-03-01T14:00:00+00:00", events=[self.CPI], staleAfterDays=90)
        strict = self.veto("2026-03-01T14:00:00+00:00", events=[self.CPI], staleAfterDays=7)

        self.assertFalse(recent.blackout_active)
        self.assertTrue(strict.blackout_active)

    def test_it_can_be_switched_off(self) -> None:
        """Every gate here needs a way to disable it, including this one."""
        decision = self.veto("2026-08-03T14:00:00+00:00", events=[self.CPI], staleBlocksEntries=False)

        self.assertFalse(decision.blackout_active)

    def test_a_rules_only_calendar_cannot_rot(self) -> None:
        """Auction windows and a roll schedule are derived, not maintained, so they never age."""
        decision = self.veto("2030-01-01T14:00:00+00:00")

        self.assertFalse(decision.blackout_active)

    def test_a_disabled_calendar_is_left_alone(self) -> None:
        from datetime import datetime

        from backend.app.algorithms.voting_ensemble.event_calendar import (
            event_calendar_from_payload,
            resolve_event_veto,
        )

        decision = resolve_event_veto(
            bar_end=datetime.fromisoformat("2026-08-03T14:00:00+00:00"),
            settings=event_calendar_from_payload({"enabled": False, "events": [self.CPI]}),
        )

        self.assertFalse(decision.blackout_active)
        self.assertIn("voting_ensemble.event_calendar.disabled", decision.reason_codes)
