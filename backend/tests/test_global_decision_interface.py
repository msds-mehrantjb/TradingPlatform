from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from pydantic import ValidationError

from backend.app.algorithms.weighted_voting.aggregation import aggregate_weighted_signals
from backend.app.algorithms.weighted_voting.dynamic_settings import default_dynamic_envelope, default_hard_limits, default_weighted_settings, resolve_effective_settings
from backend.app.algorithms.weighted_voting.global_interface import build_weighted_voting_global_order_proposal, apply_global_response_to_weighted_voting_proposal
from backend.app.algorithms.weighted_voting.models import WeightedDataQualityStatus, WeightedSide, WeightedStrategyFamily, WeightedVotingSignal
from backend.app.algorithms.weighted_voting.position_sizing import WeightedVotingSizingCap, WeightedVotingSizingResult
from backend.app.gates import GlobalGateResponse, GlobalOrderProposal, apply_global_gate_response


NOW = datetime(2026, 7, 14, 15, 30, tzinfo=UTC)
SESSION_DATE = date(2026, 7, 14)


class GlobalDecisionInterfaceTest(unittest.TestCase):
    def test_global_response_schema_cannot_change_side_or_strategy_state(self) -> None:
        with self.assertRaises(ValidationError):
            GlobalGateResponse(
                action="ALLOW",
                maximumAllowedQuantity=10,
                maximumAdditionalRiskDollars=100.0,
                side="SELL",
                evaluatedAt=NOW,
                configurationHash="global-response",
            )

    def test_quantity_reduction_is_auditable_and_side_is_immutable(self) -> None:
        proposal = order_proposal(quantity=12, planned_risk=240.0)
        response = GlobalGateResponse(
            action="REDUCE_QUANTITY",
            maximumAllowedQuantity=6,
            maximumAdditionalRiskDollars=100.0,
            rejectionReasons=("global.risk.max_additional_risk",),
            evaluatedAt=NOW,
            configurationHash="global-response",
        )

        applied = apply_global_gate_response(proposal, response)

        self.assertEqual(applied.action, "REDUCE_QUANTITY")
        self.assertEqual(applied.side, "BUY")
        self.assertEqual(applied.proposedQuantity, 12)
        self.assertEqual(applied.globallyAllowedQuantity, 5)
        self.assertTrue(applied.quantityReduced)
        self.assertIn("global_gate.side_immutable", applied.immutableChecks)
        self.assertIn("global.risk.max_additional_risk", applied.rejectionReasons)

    def test_exit_only_blocks_new_entries_without_mutating_proposal(self) -> None:
        proposal = order_proposal(quantity=10)
        response = GlobalGateResponse(
            action="EXIT_ONLY",
            maximumAllowedQuantity=0,
            maximumAdditionalRiskDollars=0.0,
            rejectionReasons=("global.operational.entry_cutoff",),
            evaluatedAt=NOW,
            configurationHash="global-response-exit-only",
        )

        applied = apply_global_gate_response(proposal, response)

        self.assertEqual(applied.globallyAllowedQuantity, 0)
        self.assertTrue(applied.riskReducingExitAllowed)
        self.assertEqual(applied.side, proposal.side)
        self.assertEqual(proposal.quantity, 10)
        self.assertEqual(proposal.entryFormula["kind"], "limit")

    def test_weighted_voting_adapter_builds_complete_one_way_proposal(self) -> None:
        decision = weighted_decision()
        sizing = sizing_result(quantity=14, risk=280.0)
        settings = effective_settings()

        proposal = build_weighted_voting_global_order_proposal(
            decision=decision,
            sizing=sizing,
            effective_settings=settings,
            symbol="SPY",
            trigger_price=100.05,
            limit_price=100.05,
            stop_price=99.55,
            target_price=101.05,
            proposed_at=NOW,
        )

        self.assertEqual(proposal.algorithmId, "weighted_voting")
        self.assertEqual(proposal.capitalPartitionId, "weighted_voting.paper.default")
        self.assertEqual(proposal.side, "BUY")
        self.assertEqual(proposal.quantity, 14)
        self.assertEqual(proposal.triggerPrice, 100.05)
        self.assertEqual(proposal.limitPrice, 100.05)
        self.assertEqual(proposal.stopPrice, 99.55)
        self.assertEqual(proposal.targetPrice, 101.05)
        self.assertEqual(proposal.plannedRiskDollars, 280.0)
        self.assertIn("base_risk_per_trade_percent", proposal.settingsSnapshot)
        self.assertTrue(proposal.strategyStateHash)

    def test_weighted_voting_adapter_applies_response_without_changing_side_or_hash(self) -> None:
        proposal = build_weighted_voting_global_order_proposal(
            decision=weighted_decision(),
            sizing=sizing_result(quantity=10, risk=200.0),
            effective_settings=effective_settings(),
            symbol="SPY",
            trigger_price=100.05,
            limit_price=100.05,
            stop_price=99.55,
            target_price=101.05,
            proposed_at=NOW,
        )
        response = GlobalGateResponse(
            action="REJECT_NEW_ENTRY",
            maximumAllowedQuantity=0,
            maximumAdditionalRiskDollars=0.0,
            rejectionReasons=("global.risk.total_open_risk",),
            evaluatedAt=NOW,
            configurationHash="global-response-reject",
        )

        applied = apply_global_response_to_weighted_voting_proposal(proposal, response)

        self.assertEqual(applied.side, proposal.side)
        self.assertEqual(applied.proposedQuantity, 10)
        self.assertEqual(applied.globallyAllowedQuantity, 0)
        self.assertTrue(applied.quantityReduced)
        self.assertIn("global_gate.strategy_state_not_modified", applied.immutableChecks)


def order_proposal(quantity: int = 10, planned_risk: float = 100.0) -> GlobalOrderProposal:
    return GlobalOrderProposal(
        algorithmId="weighted_voting",
        capitalPartitionId="weighted_voting.paper.default",
        decisionId="decision-1",
        orderIntentId="decision-1.order",
        intent="new_entry",
        symbol="SPY",
        side="BUY",
        quantity=quantity,
        triggerPrice=100.0,
        limitPrice=100.0,
        stopPrice=99.0,
        targetPrice=102.0,
        plannedRiskDollars=planned_risk,
        settingsSnapshot={"settings_version": "test"},
        entryFormula={"kind": "limit"},
        stopFormula={"kind": "atr"},
        targetFormula={"kind": "r_multiple"},
        strategyStateHash="strategy-state",
        proposedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="proposal-hash",
    )


def weighted_decision():
    return aggregate_weighted_signals(
        [
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
                data_timestamp=NOW,
                explanation="Synthetic signal.",
            )
        ],
        decision_timestamp=NOW,
    )


def sizing_result(quantity: int, risk: float) -> WeightedVotingSizingResult:
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


def effective_settings():
    defaults = default_weighted_settings(timestamp=NOW)
    envelope = default_dynamic_envelope(timestamp=NOW)
    limits = default_hard_limits(timestamp=NOW)
    return resolve_effective_settings(default_settings=defaults, dynamic_envelope=envelope, hard_limits=limits, timestamp=NOW)


if __name__ == "__main__":
    unittest.main()
