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
