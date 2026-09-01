from __future__ import annotations

import unittest

from backend.app.algorithms.voting_ensemble.models import VotingStrategyVote
from backend.app.algorithms.voting_ensemble.session_policy import (
    SESSION_POLICY_DISABLED_REASON,
    SESSION_POLICY_SEGMENT_CLOSED_REASON,
    SESSION_POLICY_SEGMENT_UNKNOWN_REASON,
    SESSION_POLICY_VOTER_BLOCKED_REASON,
    apply_session_policy,
    default_session_policy,
    session_policy_from_payload,
)


def vote(strategy: str) -> VotingStrategyVote:
    return VotingStrategyVote(
        strategy=strategy,
        family="trend",
        role="directional",
        signal="Buy",
        direction=1,
        confidence=0.7,
        active=True,
        eligible=True,
        dataReady=True,
        regimeFit=1.0,
        reliability=0.5,
        reason="synthetic vote for session policy coverage",
    )


VOTES = (vote("opening_range_breakout"), vote("bollinger_band_reversion"), vote("liquidity_sweep_reversal"))


def enabled_policy():
    return session_policy_from_payload(
        {
            "enabled": True,
            "segments": {
                "open": {"tradable": True, "permittedStrategies": ["opening_range_breakout"], "maxPositionMultiplier": 1.0},
                "close": {"tradable": True, "permittedStrategies": ["bollinger_band_reversion"], "maxPositionMultiplier": 0.5},
                "premarket": {"tradable": False, "maxPositionMultiplier": 0.0},
            },
        }
    )


class SessionPolicyDefaultsTest(unittest.TestCase):
    def test_the_policy_ships_disabled(self) -> None:
        """It changes which strategies influence live decisions, so it is opted into."""
        self.assertFalse(default_session_policy().enabled)

    def test_disabled_leaves_every_vote_alone(self) -> None:
        votes, decision = apply_session_policy(VOTES, session_segment="close")

        self.assertEqual([item.strategy for item in votes if item.eligible], [item.strategy for item in VOTES])
        self.assertEqual(decision.max_position_multiplier, 1.0)
        self.assertIn(SESSION_POLICY_DISABLED_REASON, decision.reason_codes)

    def test_a_malformed_policy_falls_back_to_disabled(self) -> None:
        """Half a gate is worse than none: it is not obvious which half is running."""
        self.assertFalse(session_policy_from_payload("not a mapping").enabled)
        self.assertFalse(session_policy_from_payload(None).enabled)


class SessionPolicyGateTest(unittest.TestCase):
    def test_only_permitted_strategies_vote_in_a_segment(self) -> None:
        votes, decision = apply_session_policy(VOTES, session_segment="close", settings=enabled_policy())

        self.assertEqual([item.strategy for item in votes if item.eligible], ["bollinger_band_reversion"])
        self.assertEqual(
            set(decision.blocked_strategies), {"opening_range_breakout", "liquidity_sweep_reversal"}
        )

    def test_a_blocked_vote_is_marked_not_dropped(self) -> None:
        """A dropped vote cannot be told apart from a strategy that said nothing."""
        votes, _ = apply_session_policy(VOTES, session_segment="close", settings=enabled_policy())

        self.assertEqual(len(votes), len(VOTES))
        blocked = next(item for item in votes if item.strategy == "opening_range_breakout")
        self.assertFalse(blocked.eligible)
        self.assertFalse(blocked.active)
        self.assertTrue(blocked.features["sessionPolicyBlocked"])
        self.assertEqual(blocked.features["sessionPolicyReasonCode"], SESSION_POLICY_VOTER_BLOCKED_REASON)

    def test_a_non_tradable_segment_blocks_everything(self) -> None:
        votes, decision = apply_session_policy(VOTES, session_segment="premarket", settings=enabled_policy())

        self.assertEqual([item for item in votes if item.eligible], [])
        self.assertFalse(decision.tradable)
        self.assertEqual(decision.max_position_multiplier, 0.0)
        self.assertIn(SESSION_POLICY_SEGMENT_CLOSED_REASON, decision.reason_codes)

    def test_the_segment_size_cap_is_reported(self) -> None:
        _, decision = apply_session_policy(VOTES, session_segment="close", settings=enabled_policy())

        self.assertEqual(decision.max_position_multiplier, 0.5)

    def test_segment_aliases_resolve_to_the_configured_segment(self) -> None:
        for alias in ("pre-market", "PRE_MARKET", " premarket "):
            with self.subTest(alias=alias):
                _, decision = apply_session_policy(VOTES, session_segment=alias, settings=enabled_policy())
                self.assertFalse(decision.tradable)

    def test_an_unknown_segment_stays_permissive_and_says_so(self) -> None:
        """An unrecognised label must not silently halt the algorithm."""
        votes, decision = apply_session_policy(VOTES, session_segment="lunar_eclipse", settings=enabled_policy())

        self.assertEqual(len([item for item in votes if item.eligible]), len(VOTES))
        self.assertIn(SESSION_POLICY_SEGMENT_UNKNOWN_REASON, decision.reason_codes)

    def test_an_unknown_segment_can_be_made_restrictive(self) -> None:
        policy = session_policy_from_payload(
            {"enabled": True, "unknownSegmentIsTradable": False, "segments": {"open": {"tradable": True}}}
        )

        votes, decision = apply_session_policy(VOTES, session_segment="lunar_eclipse", settings=policy)

        self.assertEqual([item for item in votes if item.eligible], [])
        self.assertFalse(decision.tradable)

    def test_size_multipliers_are_clamped_into_range(self) -> None:
        policy = session_policy_from_payload(
            {"enabled": True, "segments": {"open": {"maxPositionMultiplier": 9.0}, "close": {"maxPositionMultiplier": -3.0}}}
        )

        self.assertEqual(apply_session_policy(VOTES, session_segment="open", settings=policy)[1].max_position_multiplier, 1.0)
        self.assertEqual(apply_session_policy(VOTES, session_segment="close", settings=policy)[1].max_position_multiplier, 0.0)

    def test_an_empty_permitted_list_means_none_not_all(self) -> None:
        """None and () are different statements and must not collapse together."""
        policy = session_policy_from_payload(
            {"enabled": True, "segments": {"open": {"tradable": True, "permittedStrategies": []}}}
        )

        votes, _ = apply_session_policy(VOTES, session_segment="open", settings=policy)

        self.assertEqual([item for item in votes if item.eligible], [])


if __name__ == "__main__":
    unittest.main()
