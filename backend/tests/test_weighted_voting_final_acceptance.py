from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.algorithms.weighted_voting.aggregation import aggregate_weighted_signals
from backend.app.algorithms.weighted_voting.dynamic_settings import default_dynamic_envelope, default_hard_limits, default_weighted_settings, resolve_effective_settings
from backend.app.algorithms.weighted_voting.final_acceptance import (
    WEIGHTED_VOTING_FINAL_ACCEPTANCE_ITEMS,
    WEIGHTED_VOTING_FINAL_ACCEPTANCE_VERSION,
    WEIGHTED_VOTING_FINAL_ORDER_ACCEPTANCE_VERSION,
    WeightedVotingAcceptanceStatus,
    WeightedVotingFinalOrderAcceptanceInputs,
    build_weighted_voting_final_acceptance_report,
    evaluate_weighted_voting_final_order_acceptance,
    weighted_voting_acceptance_is_complete,
)
from backend.app.algorithms.weighted_voting.global_interface import apply_global_response_to_weighted_voting_proposal, build_global_order_proposal_from_weighted_voting_proposal
from backend.app.algorithms.weighted_voting.models import WeightedCandle, WeightedDataQualityStatus, WeightedMarketSnapshot, WeightedSide, WeightedStrategyFamily, WeightedVotingSignal
from backend.app.algorithms.weighted_voting.order_proposal import build_weighted_voting_order_proposal
from backend.app.algorithms.weighted_voting.position_sizing import WeightedVotingSizingCap, WeightedVotingSizingResult
from backend.app.gates import GlobalGateResponse


ROOT = Path(__file__).resolve().parents[2]
TS = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


class WeightedVotingFinalAcceptanceTest(unittest.TestCase):
    def test_final_acceptance_report_covers_every_required_statement(self) -> None:
        report = build_weighted_voting_final_acceptance_report()

        self.assertEqual(report["algorithmId"], "weighted_voting")
        self.assertEqual(report["version"], WEIGHTED_VOTING_FINAL_ACCEPTANCE_VERSION)
        self.assertEqual(len(report["items"]), 14)
        self.assertEqual(len(WEIGHTED_VOTING_FINAL_ACCEPTANCE_ITEMS), 14)
        self.assertEqual(report["counts"], {"pass": 14, "pending": 0, "fail": 0})
        self.assertTrue(report["complete"])
        self.assertTrue(weighted_voting_acceptance_is_complete())
        self.assertEqual(report["blockingStatements"], [])

    def test_all_user_final_acceptance_conditions_are_exactly_represented(self) -> None:
        statements = {item.statement for item in WEIGHTED_VOTING_FINAL_ACCEPTANCE_ITEMS}

        self.assertEqual(
            statements,
            {
                "It runs with all ML systems disabled.",
                "It runs when every other algorithm is unavailable.",
                "Changing another algorithm does not change its output.",
                "Weighted winner always determines candidate direction.",
                "Automatic mode cannot bypass local gates.",
                "Actual quotes are used for spread.",
                "Defaults remain the dynamic settings baseline.",
                "Dynamic values remain inside envelopes and hard limits.",
                "Backtesting and paper trading call the same decision functions.",
                "Weights use only completed prior data.",
                "Global gates only reduce, reject, or emergency-exit.",
                "Positions, P/L, risk, and capital remain attributable to Weighted Voting.",
                "The system remains paper-trading only.",
                "All unit, integration, isolation, property, and replay tests pass.",
            },
        )

    def test_every_required_item_is_passing_with_existing_evidence(self) -> None:
        for item in WEIGHTED_VOTING_FINAL_ACCEPTANCE_ITEMS:
            with self.subTest(statement=item.statement):
                self.assertTrue(item.required_for_completion)
                self.assertEqual(item.status, WeightedVotingAcceptanceStatus.PASS)
                self.assertTrue(item.evidence)
                for evidence in item.evidence:
                    if evidence.startswith(("backend/", "frontend/", "scripts/", "docs/")):
                        self.assertTrue((ROOT / evidence).exists(), f"{item.statement}: {evidence}")

    def test_documented_matrix_matches_executable_acceptance(self) -> None:
        doc = (ROOT / "docs" / "weighted_voting" / "final_acceptance_validation.md").read_text(encoding="utf-8")

        self.assertIn("Acceptance status: PASS", doc)
        doc_labels = {
            "It runs with all ML systems disabled.": "Runs with all ML systems disabled",
            "It runs when every other algorithm is unavailable.": "Runs when every other algorithm is unavailable",
            "Changing another algorithm does not change its output.": "Changing another algorithm does not change Weighted Voting output",
            "Weighted winner always determines candidate direction.": "Weighted winner always determines candidate direction",
            "Automatic mode cannot bypass local gates.": "Automatic mode cannot bypass local gates",
            "Actual quotes are used for spread.": "Actual quotes are used for spread",
            "Defaults remain the dynamic settings baseline.": "Defaults remain the dynamic settings baseline",
            "Dynamic values remain inside envelopes and hard limits.": "Dynamic values remain inside envelopes and hard limits",
            "Backtesting and paper trading call the same decision functions.": "Backtesting and paper trading call the same decision functions",
            "Weights use only completed prior data.": "Weights use only completed prior data",
            "Global gates only reduce, reject, or emergency-exit.": "Global gates only reduce, reject, exit-only, or emergency-exit",
            "Positions, P/L, risk, and capital remain attributable to Weighted Voting.": "Positions, P/L, risk, and capital remain attributable to Weighted Voting",
            "The system remains paper-trading only.": "System remains paper-trading only",
            "All unit, integration, isolation, property, and replay tests pass.": "Unit, integration, isolation, property, and replay tests pass",
        }
        for item in WEIGHTED_VOTING_FINAL_ACCEPTANCE_ITEMS:
            self.assertIn(doc_labels[item.statement], doc)
        self.assertIn("Weighted Voting V2 satisfies the final acceptance conditions", doc)

    def test_final_acceptance_gate_is_registered_with_ci_checks(self) -> None:
        ci_source = (ROOT / "scripts" / "ci_quality_gates.py").read_text(encoding="utf-8")

        self.assertIn("weighted-voting-final-acceptance", ci_source)
        self.assertIn("test_weighted_voting_final_acceptance.py", ci_source)
        self.assertIn("test_weighted_voting_step32_comprehensive.py", ci_source)

    def test_final_order_acceptance_passes_when_all_invariants_hold(self) -> None:
        inputs = final_order_inputs()

        result = evaluate_weighted_voting_final_order_acceptance(inputs)

        self.assertTrue(result.accepted)
        self.assertEqual(result.version, WEIGHTED_VOTING_FINAL_ORDER_ACCEPTANCE_VERSION)
        self.assertEqual(result.algorithm_id, "weighted_voting")
        self.assertEqual(result.approved_quantity, inputs.global_gate_application.globallyAllowedQuantity)
        self.assertEqual({check.check_id for check in result.checks}, {
            "decision_current",
            "market_data_fresh",
            "local_gates_passed",
            "quantity_positive",
            "stop_and_target_valid",
            "global_gates_allowed",
            "global_risk_not_increased",
            "global_direction_not_reversed",
            "position_owned_by_weighted_voting",
            "no_duplicate_order",
            "order_not_expired",
            "configuration_version_matches",
            "weight_version_matches",
        })
        self.assertEqual(result.reason_codes, ("weighted_voting.final_acceptance.accepted",))

    def test_final_order_acceptance_rejects_stale_duplicate_expired_or_version_mismatch(self) -> None:
        base = final_order_inputs()
        stale = final_order_inputs(current_time=TS + timedelta(minutes=10))
        duplicate = final_order_inputs(existing_order_ids=(base.order_proposal.proposal_id,))
        expired = final_order_inputs(current_time=TS + timedelta(minutes=6))
        mismatched = final_order_inputs(expected_weight_version="wrong_weight_version")

        self.assertIn("weighted_voting.final_acceptance.market_data_stale", evaluate_weighted_voting_final_order_acceptance(stale).reason_codes)
        self.assertIn("weighted_voting.final_acceptance.duplicate_order", evaluate_weighted_voting_final_order_acceptance(duplicate).reason_codes)
        self.assertIn("weighted_voting.final_acceptance.order_expired", evaluate_weighted_voting_final_order_acceptance(expired).reason_codes)
        self.assertIn("weighted_voting.final_acceptance.weight_version_mismatch", evaluate_weighted_voting_final_order_acceptance(mismatched).reason_codes)

    def test_final_order_acceptance_rejects_local_global_and_ownership_failures(self) -> None:
        local_failed = final_order_inputs(local_gates_passed=False)
        bad_position = final_order_inputs(position_algorithm_id="wca")
        rejected_global = final_order_inputs(global_allowed_quantity=0, global_action="REJECT_NEW_ENTRY")

        self.assertIn("weighted_voting.final_acceptance.local_gates_failed", evaluate_weighted_voting_final_order_acceptance(local_failed).reason_codes)
        self.assertIn("weighted_voting.final_acceptance.position_not_owned", evaluate_weighted_voting_final_order_acceptance(bad_position).reason_codes)
        self.assertIn("weighted_voting.final_acceptance.global_gate_not_allowed", evaluate_weighted_voting_final_order_acceptance(rejected_global).reason_codes)


def final_order_inputs(
    *,
    current_time: datetime = TS + timedelta(seconds=30),
    local_gates_passed: bool = True,
    existing_order_ids: tuple[str, ...] = (),
    position_algorithm_id: str = "weighted_voting",
    expected_weight_version: str | None = None,
    global_allowed_quantity: int = 10,
    global_action: str = "ALLOW",
) -> WeightedVotingFinalOrderAcceptanceInputs:
    decision = weighted_decision()
    settings = effective_settings()
    snapshot = market_snapshot()
    sizing = sizing_result()
    order_proposal = build_weighted_voting_order_proposal(
        decision=decision,
        sizing=sizing,
        effective_settings=settings,
        market_snapshot=snapshot,
        signals=tuple(strategy_signals()),
        trigger_price=100.05,
        limit_price=100.05,
        stop_price=99.55,
        target_price=101.05,
        created_at=TS,
        expires_at=TS + timedelta(minutes=5),
    )
    global_proposal = build_global_order_proposal_from_weighted_voting_proposal(
        proposal=order_proposal,
        decision=decision,
        sizing=sizing,
        effective_settings=settings,
    )
    global_application = apply_global_response_to_weighted_voting_proposal(
        global_proposal,
        GlobalGateResponse(
            action=global_action,  # type: ignore[arg-type]
            maximumAllowedQuantity=global_allowed_quantity,
            maximumAdditionalRiskDollars=min(sizing.effective_risk_dollars, 100.0 if global_allowed_quantity else 0.0),
            evaluatedAt=TS,
            configurationHash=f"global-{global_action.lower()}",
        ),
    )
    return WeightedVotingFinalOrderAcceptanceInputs(
        decision=decision,
        market_snapshot=snapshot,
        sizing_result=sizing,
        order_proposal=order_proposal,
        global_gate_application=global_application,
        local_gates_passed=local_gates_passed,
        current_time=current_time,
        expected_configuration_version=decision.configuration_version,
        expected_weight_version=expected_weight_version or decision.weight_version,
        existing_order_ids=existing_order_ids,
        position_algorithm_id=position_algorithm_id,
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
            explanation="Synthetic final acceptance signal.",
        )
    ]


def effective_settings():
    defaults = default_weighted_settings(timestamp=TS)
    envelope = default_dynamic_envelope(timestamp=TS)
    limits = default_hard_limits(timestamp=TS)
    return resolve_effective_settings(default_settings=defaults, dynamic_envelope=envelope, hard_limits=limits, timestamp=TS)


def market_snapshot() -> WeightedMarketSnapshot:
    return WeightedMarketSnapshot(
        symbol="SPY",
        data_timestamp=TS,
        one_minute_candles=(WeightedCandle(timestamp=TS, open=100.0, high=101.0, low=99.5, close=100.0, volume=100000),),
        bid=100.0,
        ask=100.05,
        data_manifest_hash="snapshot-hash",
        explanation="Synthetic final acceptance snapshot.",
    )


def sizing_result() -> WeightedVotingSizingResult:
    return WeightedVotingSizingResult(
        quantity=10,
        limiting_cap="risk",
        caps=(WeightedVotingSizingCap(cap_id="risk", quantity=10, reason_codes=("test.cap",), explanation="Synthetic cap."),),
        effective_risk_dollars=100.0,
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


if __name__ == "__main__":
    unittest.main()
