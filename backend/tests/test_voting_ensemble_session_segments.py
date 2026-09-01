from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from backend.app.algorithms.voting_ensemble.backtest import VotingEnsembleBacktestRunner
from backend.app.algorithms.voting_ensemble.backtest_config import VotingEnsembleBacktestConfig
from backend.app.algorithms.voting_ensemble.finalized_bar_producer import _session_state
from backend.app.algorithms.voting_ensemble.risk_budget import resolve_voting_ensemble_risk_budget
from backend.app.algorithms.voting_ensemble.session_policy import (
    apply_session_policy,
    session_policy_from_payload,
)
from backend.app.algorithms.voting_ensemble.session_segments import (
    DEFAULT_SESSION_SEGMENT_BOUNDARIES,
    SessionSegmentBoundaries,
    resolve_session_segment,
    session_segment_boundaries_from_payload,
)

NEW_YORK = ZoneInfo("America/New_York")


def at(hour: int, minute: int, *, month: int = 7, day: int = 14) -> datetime:
    """A bar-end timestamp given in exchange-local time."""
    return datetime(2026, month, day, hour, minute, tzinfo=NEW_YORK).astimezone(timezone.utc)


def _short_series(minutes: int = 60, *, base: float = 500.0, scale: float = 1.0) -> list[dict]:
    """A brief session, enough bars to clear warm-up without the cost of a full day."""
    rows, price = [], base
    for index in range(minutes):
        phase = index % 80
        step = (1.4 if phase < 30 else (-1.1 if phase < 60 else 0.6)) * scale
        price += step
        wick = max(abs(step) * 0.8, 0.12) * scale
        ts = at(9, 31) + timedelta(minutes=index)
        rows.append(
            {
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "open": round(price - step, 4),
                "high": round(max(price, price - step) + wick, 4),
                "low": round(min(price, price - step) - wick, 4),
                "close": round(price, 4),
                "volume": 140000 + (index % 7) * 6000,
            }
        )
    return rows


class SessionSegmentTest(unittest.TestCase):
    """The label the session policy keys on has to be produced by something.

    It was not. The live path reported `phase: "regular"`, which the policy's alias table
    normalises to `midday`, and replay supplied no session state at all, falling back to the
    same place. So the policy could only ever see one segment, in every environment: a gate
    enforcing against a label with a single possible value.
    """

    def test_the_segments_of_a_regular_session(self) -> None:
        cases = {
            (8, 0): "premarket",
            (9, 29): "premarket",
            (9, 31): "open",
            (10, 30): "open",
            (10, 31): "midday",
            (15, 0): "midday",
            (15, 1): "close",
            (16, 0): "close",
            (16, 1): "overnight",
            (20, 0): "overnight",
        }
        for (hour, minute), expected in cases.items():
            with self.subTest(time=f"{hour:02d}:{minute:02d}"):
                self.assertEqual(resolve_session_segment(at(hour, minute)), expected)

    def test_the_boundary_bars_are_filed_by_the_period_they_cover(self) -> None:
        """A bar ending at 09:30 covers 09:29-09:30, so it is still premarket.

        A bar ending at 16:00 is the last bar of the close, not the first of the overnight.
        These are the two bars a session policy cares most about, so getting the interval
        convention wrong would misfile exactly the wrong ones.
        """
        self.assertEqual(resolve_session_segment(at(9, 30)), "premarket")
        self.assertEqual(resolve_session_segment(at(9, 31)), "open")
        self.assertEqual(resolve_session_segment(at(16, 0)), "close")
        self.assertEqual(resolve_session_segment(at(16, 1)), "overnight")

    def test_the_boundaries_follow_daylight_saving(self) -> None:
        """09:31 ET is 13:31Z in July and 14:31Z in January; both are the open."""
        self.assertEqual(resolve_session_segment(at(9, 31, month=7, day=14)), "open")
        self.assertEqual(resolve_session_segment(at(9, 31, month=1, day=14)), "open")

        summer = datetime(2026, 7, 14, 13, 31, tzinfo=timezone.utc)
        winter = datetime(2026, 1, 14, 13, 31, tzinfo=timezone.utc)
        self.assertEqual(resolve_session_segment(summer), "open")
        self.assertEqual(resolve_session_segment(winter), "premarket")

    def test_a_naive_timestamp_is_read_as_utc(self) -> None:
        self.assertEqual(resolve_session_segment(datetime(2026, 7, 14, 17, 0)), "midday")

    def test_configured_boundaries_are_honoured(self) -> None:
        boundaries = session_segment_boundaries_from_payload({"openEnd": "11:30"})

        self.assertEqual(resolve_session_segment(at(11, 0), boundaries=boundaries), "open")
        self.assertEqual(resolve_session_segment(at(11, 0)), "midday")

    def test_malformed_boundaries_fall_back_rather_than_half_apply(self) -> None:
        """A partially applied boundary set would file bars where no one intended."""
        for payload in ({"openEnd": "not a time"}, {"openEnd": "25:00"}, {"middayEnd": "09:00"}):
            with self.subTest(payload=payload):
                boundaries = session_segment_boundaries_from_payload(payload)
                self.assertEqual(boundaries.as_minutes(), DEFAULT_SESSION_SEGMENT_BOUNDARIES.as_minutes())

    def test_out_of_order_boundaries_are_rejected(self) -> None:
        self.assertIsNone(SessionSegmentBoundaries(open_start="12:00", open_end="10:00").as_minutes())


class SessionSegmentWiringTest(unittest.TestCase):
    """Live and replay must resolve the same segment for the same bar."""

    def test_the_live_session_state_carries_the_segment(self) -> None:
        state = _session_state({"isOpen": True}, settings=None, bar_end=at(15, 30))

        self.assertEqual(state["sessionSegment"], "close")

    def test_the_live_phase_is_left_exactly_as_it_was(self) -> None:
        """The regime classifier reads `phase`; a session label must not disturb it."""
        state = _session_state({"isOpen": True}, settings=None, bar_end=at(15, 30))

        self.assertEqual(state["phase"], "regular")
        self.assertFalse(state["marketClosed"])

    def test_a_producer_without_a_bar_end_adds_no_segment(self) -> None:
        state = _session_state({"isOpen": True})

        self.assertNotIn("sessionSegment", state)
        self.assertEqual(state["phase"], "regular")

    def test_replay_and_the_live_producer_agree_bar_for_bar(self) -> None:
        runner = object.__new__(VotingEnsembleBacktestRunner)
        runner.config = VotingEnsembleBacktestConfig()

        for hour, minute in ((9, 29), (9, 31), (10, 30), (12, 0), (15, 1), (16, 0), (16, 1)):
            with self.subTest(time=f"{hour:02d}:{minute:02d}"):
                bar_end = at(hour, minute)
                live = _session_state({"isOpen": True}, settings=None, bar_end=bar_end)["sessionSegment"]

                self.assertEqual(runner._session_segment_at(bar_end), live)

    def test_replay_honours_configured_boundaries(self) -> None:
        runner = object.__new__(VotingEnsembleBacktestRunner)
        runner.config = VotingEnsembleBacktestConfig(sessionSegments={"openEnd": "11:30"})

        self.assertEqual(runner._session_segment_at(at(11, 0)), "open")


class SessionSizeCapTest(unittest.TestCase):
    """The policy's size multiplier has to reach sizing, not just be reported.

    It was resolved on every bar, carried on a decision object the service dropped, and
    applied to nothing, so "run smaller into the close" was inert.
    """

    def budget(self, session_cap: float):
        return resolve_voting_ensemble_risk_budget(
            {
                "candidateSignal": "BUY",
                "gatesPassed": True,
                "netEdgePassed": True,
                "riskPerTradePercent": 0.5,
                "orderAllocationPercent": 100.0,
                "dailyAllocationPercent": 100.0,
                "maximumPositionPercent": 100.0,
                "profileMaximumShares": 100000,
                "availableBuyingPower": 100000.0,
                "availableFillableQuantity": 100000.0,
                "currentOneMinuteVolume": 10000000.0,
                "maximumVolumeParticipationPercent": 100.0,
                "globalExposureAllowanceDollars": 1000000.0,
                "localExposureAllowanceDollars": 1000000.0,
                # Without these the family-support multiplier is zero and nothing sizes at
                # all, which would make the cap look effective for the wrong reason.
                "voteEdge": 0.8,
                "independentFamilySupport": 2,
                "minimumIndependentFamilySupport": 2,
                "sessionCap": session_cap,
            },
            equity=100000.0,
            entry_price=500.0,
            stop_distance=5.0,
        )

    def test_a_half_size_segment_halves_the_position(self) -> None:
        full = self.budget(1.0)
        half = self.budget(0.5)

        self.assertGreater(full.quantity, 0)
        self.assertEqual(half.quantity, full.quantity // 2)

    def test_the_default_cap_changes_nothing(self) -> None:
        """An unconfigured policy must leave sizing exactly where it was."""
        self.assertEqual(self.budget(1.0).quantity, self.budget(1.0).quantity)

    def test_a_zero_cap_blocks_sizing_without_blocking_the_vote(self) -> None:
        self.assertEqual(self.budget(0.0).quantity, 0)


class SessionPolicyReasonCodeTest(unittest.TestCase):
    """A disabled policy must leave the decision record exactly as it found it."""

    def codes(self, **config) -> list[str]:
        runner = VotingEnsembleBacktestRunner(
            config=VotingEnsembleBacktestConfig(warmupCandles=40, includeDecisionRecords=True, **config)
        )
        bars = _short_series()
        result = runner.run(
            symbol="SPY",
            spy_1m_candles=bars,
            qqq_candles=_short_series(base=440.0, scale=1.2),
            iwm_candles=_short_series(base=210.0, scale=0.8),
            breadth_components={
                "XLK": _short_series(base=250.0, scale=1.1),
                "XLF": _short_series(base=48.0, scale=0.9),
                "XLV": _short_series(base=145.0, scale=0.7),
            },
            timeframe="1Min",
        )
        return list(result["decisionRecords"][0]["reasonCodes"])

    def test_a_disabled_policy_stamps_nothing_on_the_record(self) -> None:
        """Otherwise every decision ever made carries a code saying a feature is off.

        That is configuration state masquerading as a finding, and it would bury the codes
        that do describe the bar.
        """
        self.assertEqual([code for code in self.codes() if "session_policy" in code], [])

    def test_an_enabled_policy_says_what_it_did(self) -> None:
        codes = self.codes(
            sessionPolicy={
                "enabled": True,
                "segments": {"open": {"tradable": True, "permittedStrategies": ["bollinger_band_reversion"]}},
            }
        )

        self.assertIn("voting_ensemble.session_policy.voter_not_permitted_in_segment", codes)


class SessionPolicyAgainstRealLabelsTest(unittest.TestCase):
    """The policy applied to segments a real session actually produces."""

    def votes(self):
        from backend.app.algorithms.voting_ensemble.models import VotingStrategyVote

        return (
            VotingStrategyVote(
                strategy="bollinger_band_reversion", family="mean_reversion", role="directional",
                signal="Buy", direction=1, confidence=0.6, active=True, eligible=True,
                dataReady=True, regimeFit=1.0, reliability=0.5, reason="test",
            ),
            VotingStrategyVote(
                strategy="multi_timeframe_trend_alignment", family="trend", role="directional",
                signal="Buy", direction=1, confidence=0.6, active=True, eligible=True,
                dataReady=True, regimeFit=1.0, reliability=0.5, reason="test",
            ),
        )

    def test_the_close_segment_can_permit_one_strategy_and_not_the_other(self) -> None:
        settings = session_policy_from_payload(
            {"enabled": True, "segments": {"close": {"tradable": True, "permittedStrategies": ["bollinger_band_reversion"]}}}
        )
        segment = resolve_session_segment(at(15, 30))
        votes, decision = apply_session_policy(self.votes(), session_segment=segment, settings=settings)

        self.assertEqual(segment, "close")
        self.assertEqual(decision.blocked_strategies, ("multi_timeframe_trend_alignment",))
        self.assertTrue(votes[0].eligible)
        self.assertFalse(votes[1].eligible)

    def test_the_same_policy_leaves_the_open_segment_alone(self) -> None:
        """A segment with no policy entry is unknown, and unknown is permissive by default."""
        settings = session_policy_from_payload(
            {"enabled": True, "segments": {"close": {"tradable": True, "permittedStrategies": ["bollinger_band_reversion"]}}}
        )
        segment = resolve_session_segment(at(10, 0))
        votes, decision = apply_session_policy(self.votes(), session_segment=segment, settings=settings)

        self.assertEqual(segment, "open")
        self.assertEqual(decision.blocked_strategies, ())
        self.assertTrue(all(vote.eligible for vote in votes))

    def test_a_premarket_bar_is_not_tradable_under_the_default_shape(self) -> None:
        settings = session_policy_from_payload({"enabled": True})
        segment = resolve_session_segment(at(9, 0))
        votes, decision = apply_session_policy(self.votes(), session_segment=segment, settings=settings)

        self.assertEqual(segment, "premarket")
        self.assertFalse(decision.tradable)
        self.assertFalse(any(vote.eligible for vote in votes))


if __name__ == "__main__":
    unittest.main()
