from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from pydantic import ValidationError

from backend.app.algorithms.weighted_voting.aggregation import aggregate_weighted_signals
from backend.app.algorithms.weighted_voting.dynamic_settings import default_dynamic_envelope, default_hard_limits, default_weighted_settings, resolve_effective_settings
from backend.app.algorithms.weighted_voting.global_interface import (
    WEIGHTED_VOTING_CAPITAL_PARTITION_ID,
    WeightedVotingGlobalRiskResponse,
    WeightedVotingStaticCentralGlobalRiskService,
    apply_global_response_to_weighted_voting_proposal,
    build_weighted_voting_global_risk_request,
    build_weighted_voting_global_order_proposal,
    global_gate_response_from_weighted_voting_risk,
    global_interface_status,
    validate_weighted_voting_global_risk_response,
)
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

    def test_weighted_voting_adapter_rejects_global_quantity_or_risk_increase(self) -> None:
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

        with self.assertRaises(ValueError):
            apply_global_response_to_weighted_voting_proposal(
                proposal,
                GlobalGateResponse(
                    action="ALLOW",
                    maximumAllowedQuantity=11,
                    maximumAdditionalRiskDollars=200.0,
                    evaluatedAt=NOW,
                    configurationHash="global-response-quantity-increase",
                ),
            )
        with self.assertRaises(ValueError):
            apply_global_response_to_weighted_voting_proposal(
                proposal,
                GlobalGateResponse(
                    action="ALLOW",
                    maximumAllowedQuantity=10,
                    maximumAdditionalRiskDollars=201.0,
                    evaluatedAt=NOW,
                    configurationHash="global-response-risk-increase",
                ),
            )

    def test_weighted_voting_adapter_rejects_bad_ownership_and_documents_allowed_actions(self) -> None:
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
        status = global_interface_status()

        self.assertEqual(status["algorithmId"], "weighted_voting")
        self.assertEqual(status["capitalPartitionId"], WEIGHTED_VOTING_CAPITAL_PARTITION_ID)
        self.assertIn("EMERGENCY_LIQUIDATE", status["allowedActions"])
        self.assertIn("REJECT", status["allowedActions"])
        self.assertNotIn("REDUCE_QUANTITY", status["allowedActions"])
        self.assertFalse(status["clientPayloadGlobalRiskAccepted"])
        self.assertIn("weighted_voting.global_interface.active_weights_not_mutated", status["immutabilityChecks"])

        tampered_payload = proposal.model_dump(mode="json")
        tampered_payload["capitalPartitionId"] = "wca.paper.default"
        tampered = GlobalOrderProposal.model_validate(tampered_payload)
        with self.assertRaises(ValueError):
            apply_global_response_to_weighted_voting_proposal(
                tampered,
                GlobalGateResponse(
                    action="REJECT_NEW_ENTRY",
                    maximumAllowedQuantity=0,
                    maximumAdditionalRiskDollars=0.0,
                    evaluatedAt=NOW,
                    configurationHash="global-response-reject",
                ),
            )

    def test_weighted_voting_global_risk_request_contains_authoritative_audit_fields(self) -> None:
        proposal = order_proposal(quantity=10, planned_risk=200.0)

        request = build_weighted_voting_global_risk_request(
            proposal=proposal,
            inventory_version=7,
            current_algorithm_exposure=1250.0,
            current_account_exposure=5000.0,
            daily_algorithm_pnl=-25.0,
            account_level_risk_observations={"equity": 100000.0},
            settings_version="settings-v1",
            requested_at=NOW,
        )

        self.assertEqual(request.algorithm_id, "weighted_voting")
        self.assertEqual(request.proposal_id, proposal.orderIntentId)
        self.assertEqual(request.capital_partition_id, WEIGHTED_VOTING_CAPITAL_PARTITION_ID)
        self.assertEqual(request.proposed_quantity, 10)
        self.assertEqual(request.proposed_notional, 1000.0)
        self.assertEqual(request.planned_risk, 200.0)
        self.assertEqual(request.current_algorithm_exposure, 1250.0)
        self.assertEqual(request.current_account_exposure, 5000.0)
        self.assertEqual(request.daily_algorithm_pnl, -25.0)
        self.assertEqual(request.settings_version, "settings-v1")
        self.assertEqual(request.inventory_version, 7)
        self.assertTrue(request.request_hash)

    def test_missing_stale_forged_and_mismatched_global_risk_responses_reject(self) -> None:
        request = build_weighted_voting_global_risk_request(
            proposal=order_proposal(quantity=10, planned_risk=200.0),
            inventory_version=1,
            current_algorithm_exposure=0.0,
            current_account_exposure=0.0,
            daily_algorithm_pnl=0.0,
            account_level_risk_observations={},
            settings_version="settings-v1",
            requested_at=NOW,
        )
        valid = WeightedVotingStaticCentralGlobalRiskService().evaluate(request)
        cases = (
            (None, "weighted_voting.global_risk.missing_response"),
            (valid.model_copy(update={"expiry_timestamp": NOW}), "weighted_voting.global_risk.response_stale"),
            (valid.model_copy(update={"proposal_id": "forged-proposal"}).with_hash(), "weighted_voting.global_risk.proposal_id_mismatch"),
            (valid.model_copy(update={"response_hash": "forged-hash"}), "weighted_voting.global_risk.response_hash_invalid"),
            (valid.model_construct(**{**valid.model_dump(), "algorithm_id": "wca"}), "weighted_voting.global_risk.algorithm_id_mismatch"),
        )

        for response, reason in cases:
            with self.subTest(reason=reason):
                rejected, reasons = validate_weighted_voting_global_risk_response(request=request, response=response, now=NOW)

                self.assertEqual(rejected.action, "REJECT")
                self.assertEqual(rejected.maximum_quantity, 0)
                self.assertEqual(rejected.maximum_additional_risk, 0.0)
                self.assertIn(reason, reasons)

    def test_reduce_response_applies_exact_quantity_and_risk(self) -> None:
        proposal = order_proposal(quantity=10, planned_risk=200.0)
        request = build_weighted_voting_global_risk_request(
            proposal=proposal,
            inventory_version=1,
            current_algorithm_exposure=0.0,
            current_account_exposure=0.0,
            daily_algorithm_pnl=0.0,
            account_level_risk_observations={},
            settings_version="settings-v1",
            requested_at=NOW,
        )
        weighted_response = WeightedVotingGlobalRiskResponse(
            request_id=request.request_id,
            proposal_id=request.proposal_id,
            action="REDUCE",
            maximum_quantity=6,
            maximum_additional_risk=100.0,
            reason_codes=("central_risk.reduce",),
            configuration_hash="central-risk-config",
            configuration_version="central-risk-v1",
            evaluated_timestamp=NOW,
            expiry_timestamp=NOW.replace(minute=31),
        ).with_hash()

        validated, _ = validate_weighted_voting_global_risk_response(request=request, response=weighted_response, now=NOW)
        applied = apply_global_response_to_weighted_voting_proposal(proposal, global_gate_response_from_weighted_voting_risk(validated))

        self.assertEqual(applied.action, "REDUCE_QUANTITY")
        self.assertEqual(applied.globallyAllowedQuantity, 5)
        self.assertEqual(applied.maximumAdditionalRiskDollars, 100.0)


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
                strategy_id="S2",
                strategy_name="S2 synthetic",
                strategy_version="weighted_strategy_test_v1",
                family=WeightedStrategyFamily.TREND,
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
