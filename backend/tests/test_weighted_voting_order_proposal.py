from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.weighted_voting.aggregation import aggregate_weighted_signals
from backend.app.algorithms.weighted_voting.dynamic_settings import default_dynamic_envelope, default_hard_limits, default_weighted_settings, resolve_effective_settings
from backend.app.algorithms.weighted_voting.global_interface import build_global_order_proposal_from_weighted_voting_proposal, global_interface_status
from backend.app.algorithms.weighted_voting.models import (
    WeightedCandle,
    WeightedDataQualityStatus,
    WeightedMarketSnapshot,
    WeightedSide,
    WeightedStrategyFamily,
    WeightedVotingSignal,
)
from backend.app.algorithms.weighted_voting.order_proposal import (
    WEIGHTED_VOTING_ORDER_PROPOSAL_OWNERSHIP,
    WEIGHTED_VOTING_ORDER_PROPOSAL_VERSION,
    build_weighted_voting_order_proposal,
)
from backend.app.algorithms.weighted_voting.position_sizing import WeightedVotingSizingCap, WeightedVotingSizingResult


TS = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


class WeightedVotingOrderProposalTest(unittest.TestCase):
    def test_order_proposal_contains_required_weighted_voting_fields(self) -> None:
        decision = weighted_decision()
        settings = effective_settings()
        snapshot = market_snapshot()
        sizing = sizing_result(quantity=12, risk=240.0)
        signals = tuple(strategy_signals())

        proposal = build_weighted_voting_order_proposal(
            decision=decision,
            sizing=sizing,
            effective_settings=settings,
            market_snapshot=snapshot,
            signals=signals,
            trigger_price=100.05,
            limit_price=100.06,
            stop_price=99.55,
            target_price=101.05,
            order_type="limit",
            time_in_force="day",
            created_at=TS,
            expires_at=TS + timedelta(minutes=5),
        )
        payload = proposal.as_dict()

        for field in (
            "algorithm_id",
            "decision_id",
            "symbol",
            "side",
            "quantity",
            "order_type",
            "trigger_price",
            "limit_price",
            "stop_price",
            "target_price",
            "time_in_force",
            "strategy_versions",
            "weight_version",
            "settings_version",
            "risk_profile_version",
            "market_snapshot_hash",
            "created_at",
            "expires_at",
            "reason_codes",
        ):
            self.assertIn(field, payload)
        self.assertEqual(proposal.algorithm_id, "weighted_voting")
        self.assertEqual(proposal.ownership, WEIGHTED_VOTING_ORDER_PROPOSAL_OWNERSHIP)
        self.assertEqual(proposal.proposal_version, WEIGHTED_VOTING_ORDER_PROPOSAL_VERSION)
        self.assertEqual(proposal.decision_id, decision.decision_id)
        self.assertEqual(proposal.symbol, "SPY")
        self.assertEqual(proposal.side, WeightedSide.BUY.value)
        self.assertEqual(proposal.quantity, 12)
        self.assertEqual(proposal.strategy_versions, {"S1": "weighted_strategy_test_v1"})
        self.assertEqual(proposal.weight_version, decision.weight_version)
        self.assertEqual(proposal.settings_version, settings.settings_version)
        self.assertEqual(proposal.market_snapshot_hash, snapshot.data_manifest_hash)
        self.assertIn("weighted_voting.order_proposal.created", proposal.reason_codes)
        self.assertTrue(proposal.configuration_hash)

    def test_global_adapter_submits_owned_weighted_voting_proposal(self) -> None:
        decision = weighted_decision()
        settings = effective_settings()
        sizing = sizing_result(quantity=7, risk=140.0)
        proposal = build_weighted_voting_order_proposal(
            decision=decision,
            sizing=sizing,
            effective_settings=settings,
            market_snapshot=market_snapshot(),
            signals=tuple(strategy_signals()),
            trigger_price=100.05,
            limit_price=100.06,
            stop_price=99.55,
            target_price=101.05,
            created_at=TS,
        )

        global_proposal = build_global_order_proposal_from_weighted_voting_proposal(
            proposal=proposal,
            decision=decision,
            sizing=sizing,
            effective_settings=settings,
        )

        self.assertEqual(global_proposal.algorithmId, "weighted_voting")
        self.assertEqual(global_proposal.decisionId, proposal.decision_id)
        self.assertEqual(global_proposal.quantity, proposal.quantity)
        self.assertEqual(global_proposal.triggerPrice, proposal.trigger_price)
        self.assertEqual(global_proposal.limitPrice, proposal.limit_price)
        self.assertEqual(global_proposal.settingsSnapshot["weightedOrderProposal"]["ownership"], WEIGHTED_VOTING_ORDER_PROPOSAL_OWNERSHIP)
        self.assertIn(proposal.proposal_id, global_proposal.orderIntentId)

    def test_global_interface_declares_shared_service_permissions_and_forbidden_mutations(self) -> None:
        status = global_interface_status()

        self.assertEqual(status["algorithmId"], "weighted_voting")
        self.assertIn("reduce_quantity", status["sharedServiceAllowedActions"])
        self.assertIn("reject_order", status["sharedServiceAllowedActions"])
        self.assertIn("execute_approved_order", status["sharedServiceAllowedActions"])
        self.assertIn("reverse_trade_direction", status["sharedServiceForbiddenActions"])
        self.assertIn("increase_requested_quantity", status["sharedServiceForbiddenActions"])
        self.assertIn("change_strategy_weights", status["sharedServiceForbiddenActions"])
        self.assertIn("change_local_settings", status["sharedServiceForbiddenActions"])
        self.assertFalse(status["sharedServiceBoundary"]["sharedServicesMayGenerateSignal"])
        self.assertTrue(status["sharedServiceBoundary"]["ownershipRequiredForPositionMutation"])

    def test_foreign_algorithm_proposals_are_rejected(self) -> None:
        decision = weighted_decision()
        settings = effective_settings()
        proposal = build_weighted_voting_order_proposal(
            decision=decision,
            sizing=sizing_result(quantity=1, risk=10.0),
            effective_settings=settings,
            market_snapshot=market_snapshot(),
            created_at=TS,
        )
        foreign = object.__new__(proposal.__class__)
        for key, value in proposal.__dict__.items():
            object.__setattr__(foreign, key, value)
        object.__setattr__(foreign, "algorithm_id", "wca")

        with self.assertRaises(ValueError):
            build_global_order_proposal_from_weighted_voting_proposal(
                proposal=foreign,
                decision=decision,
                sizing=sizing_result(quantity=1, risk=10.0),
                effective_settings=settings,
            )


def weighted_decision():
    return aggregate_weighted_signals(strategy_signals(), decision_timestamp=TS)


def strategy_signals() -> list[WeightedVotingSignal]:
    return [
        WeightedVotingSignal(
            strategy_id="S1",
            strategy_name="S1 synthetic",
            strategy_version="weighted_strategy_test_v1",
            family=WeightedStrategyFamily.BREAKOUT,
            signal=WeightedSide.BUY,
            p_buy=0.8,
            p_sell=0.1,
            p_hold=0.1,
            directional_confidence=0.8,
            signal_strength=0.8,
            expected_raw_movement=0.002,
            expected_return=0.002,
            expected_return_after_costs=0.0015,
            strength=0.8,
            final_weight=1.0,
            eligible=True,
            data_ready=True,
            data_quality_status=WeightedDataQualityStatus.FULL,
            data_timestamp=TS,
            explanation="Synthetic order proposal signal.",
        )
    ]


def sizing_result(*, quantity: int, risk: float) -> WeightedVotingSizingResult:
    return WeightedVotingSizingResult(
        quantity=quantity,
        limiting_cap="risk",
        caps=(WeightedVotingSizingCap(cap_id="risk", quantity=quantity, reason_codes=("test.cap",), explanation="Synthetic cap."),),
        effective_risk_dollars=risk,
        stop_distance=0.5,
        structural_stop_distance=0.5,
        atr_stop_distance=0.4,
        minimum_price_stop_distance=0.1,
        spread_safety_buffer=0.03,
        actual_bid=100.0,
        actual_ask=100.05,
        actual_spread=0.05,
        slippage_per_share=0.01,
        current_one_minute_volume=100000.0,
        average_one_minute_volume=100000.0,
        reason_codes=("test.sizing",),
        explanation="Synthetic sizing.",
    )


def market_snapshot() -> WeightedMarketSnapshot:
    return WeightedMarketSnapshot(
        symbol="SPY",
        data_timestamp=TS,
        one_minute_candles=(WeightedCandle(timestamp=TS, open=100.0, high=101.0, low=99.5, close=100.0, volume=100000),),
        bid=100.0,
        ask=100.05,
        data_manifest_hash="snapshot-hash",
        explanation="Synthetic market snapshot.",
    )


def effective_settings():
    defaults = default_weighted_settings(timestamp=TS)
    envelope = default_dynamic_envelope(timestamp=TS)
    limits = default_hard_limits(timestamp=TS)
    return resolve_effective_settings(default_settings=defaults, dynamic_envelope=envelope, hard_limits=limits, timestamp=TS)


if __name__ == "__main__":
    unittest.main()
