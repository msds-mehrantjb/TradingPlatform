from __future__ import annotations

import unittest
from datetime import UTC, datetime

from backend.app.algorithms.voting_ensemble.ml_contracts import SafeMLInferenceResult
from backend.app.algorithms.voting_ensemble.order_planner import VotingEnsembleOrderPlanner
from backend.app.algorithms.voting_ensemble.risk_budget import resolve_voting_ensemble_risk_budget
from backend.app.domain.models import (
    AccountRiskState,
    BaselineTradingSettings,
    Direction,
    DynamicPolicyBounds,
    EffectiveTradePolicy,
    GateStatus,
    GlobalGateDecision,
    HardRiskLimits,
    OperatingMode,
    Signal,
    TradeCandidate,
)


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


class VotingEnsembleRiskBudgetTest(unittest.TestCase):
    def test_quantity_is_minimum_of_all_sizing_caps(self) -> None:
        budget = resolve_voting_ensemble_risk_budget(
            {
                "candidateSignal": "BUY",
                "riskPerTradePercent": 1.0,
                "orderAllocationPercent": 10.0,
                "dailyAllocationPercent": 30.0,
                "maximumPositionPercent": 50.0,
                "availableBuyingPower": 300.0,
                "availableFillableQuantity": 20,
                "currentOneMinuteVolume": 1000,
                "maximumVolumeParticipationPercent": 1.0,
                "profileMaximumShares": 7,
                "globalExposureAllowanceDollars": 600.0,
                "localExposureAllowanceDollars": 900.0,
                "voteEdge": 0.80,
                "independentFamilySupport": 2,
                "minimumIndependentFamilySupport": 2,
            },
            equity=10_000.0,
            entry_price=100.0,
            stop_distance=1.0,
        )

        self.assertEqual(budget.quantity, 3)
        self.assertEqual(budget.planned_risk, 3.0)
        self.assertIn("available_equity_buying_power_shares", budget.selected_cap_ids)
        self.assertEqual({cap.cap_id for cap in budget.caps}, {
            "risk_based_shares",
            "position_notional_cap_shares",
            "available_equity_buying_power_shares",
            "liquidity_based_shares",
            "participation_rate_shares",
            "profile_maximum_shares",
            "global_exposure_allowance_shares",
            "local_exposure_allowance_shares",
            "order_allocation_shares",
        })

    def test_zero_quantity_when_candidate_or_runtime_blocks_entry(self) -> None:
        cases = (
            ({"candidateSignal": "HOLD"}, "voting_ensemble.risk_budget.hold_candidate"),
            ({"gatesPassed": False}, "voting_ensemble.risk_budget.gates_failed"),
            ({"netEdgePassed": False}, "voting_ensemble.risk_budget.net_edge_failed"),
            ({"entriesBlocked": True}, "voting_ensemble.risk_budget.entries_blocked_by_profile"),
            ({"riskPerTradePercent": 0.0}, "voting_ensemble.risk_budget.zero_risk_budget"),
        )
        for overrides, reason_code in cases:
            with self.subTest(reason_code=reason_code):
                budget = resolve_voting_ensemble_risk_budget(
                    {**base_config(), **overrides},
                    equity=10_000.0,
                    entry_price=100.0,
                    stop_distance=1.0,
                )

                self.assertEqual(budget.quantity, 0)
                self.assertIn(reason_code, budget.reason_codes)

    def test_zero_quantity_when_stop_distance_or_minimum_size_invalidates_trade(self) -> None:
        invalid_stop = resolve_voting_ensemble_risk_budget(base_config(), equity=10_000.0, entry_price=100.0, stop_distance=0.0)
        self.assertEqual(invalid_stop.quantity, 0)
        self.assertIn("voting_ensemble.risk_budget.invalid_inputs", invalid_stop.reason_codes)

        below_minimum = resolve_voting_ensemble_risk_budget(
            {**base_config(), "availableFillableQuantity": 4, "minimumTradableSize": 5},
            equity=10_000.0,
            entry_price=100.0,
            stop_distance=1.0,
        )
        self.assertEqual(below_minimum.quantity, 0)
        self.assertIn("voting_ensemble.risk_budget.below_minimum_tradable_size", below_minimum.reason_codes)

    def test_vote_edge_sizing_is_bounded_and_configurable(self) -> None:
        blocked = resolve_voting_ensemble_risk_budget(
            {**base_config(), "voteEdge": 0.10, "minimumVoteEdgeForSizing": 0.20},
            equity=10_000.0,
            entry_price=100.0,
            stop_distance=1.0,
        )
        self.assertEqual(blocked.quantity, 0)
        self.assertIn("voting_ensemble.risk_budget.vote_edge_below_minimum", blocked.reason_codes)

        bounded = resolve_voting_ensemble_risk_budget(
            {**base_config(), "voteEdge": 0.35, "mediumVoteEdgeMultiplier": 0.40, "orderAllocationPercent": 100.0, "dailyAllocationPercent": 100.0},
            equity=10_000.0,
            entry_price=100.0,
            stop_distance=1.0,
        )
        full = resolve_voting_ensemble_risk_budget(
            {**base_config(), "orderAllocationPercent": 100.0, "dailyAllocationPercent": 100.0},
            equity=10_000.0,
            entry_price=100.0,
            stop_distance=1.0,
        )
        self.assertEqual(bounded.vote_edge_multiplier, 0.4)
        self.assertLess(bounded.quantity, full.quantity)

    def test_order_planner_does_not_default_zero_quantity_to_one_share(self) -> None:
        plan = VotingEnsembleOrderPlanner().order_plan(
            candidate=candidate(quantity=0),
            policy=policy(),
            gateDecision=gate_decision(eligible=True),
            mlDecision=ml_result(),
            decidedAt=NOW,
            sessionDate=NOW.date(),
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.orderType, "NO_ORDER")
        self.assertEqual(plan.quantity, 0)
        self.assertIn("voting_ensemble.order_planner.candidate_quantity_zero", plan.validationErrors)


def base_config() -> dict[str, object]:
    return {
        "candidateSignal": "BUY",
        "gatesPassed": True,
        "netEdgePassed": True,
        "profileAllowsEntries": True,
        "entriesBlocked": False,
        "riskPerTradePercent": 1.0,
        "orderAllocationPercent": 10.0,
        "dailyAllocationPercent": 30.0,
        "maximumPositionPercent": 50.0,
        "availableBuyingPower": 10_000.0,
        "availableFillableQuantity": 1000,
        "currentOneMinuteVolume": 100_000,
        "maximumVolumeParticipationPercent": 1.0,
        "profileMaximumShares": 1000,
        "globalExposureAllowanceDollars": 50_000.0,
        "localExposureAllowanceDollars": 50_000.0,
        "voteEdge": 0.80,
        "independentFamilySupport": 2,
        "minimumIndependentFamilySupport": 2,
    }


def candidate(*, quantity: int) -> TradeCandidate:
    return TradeCandidate(
        candidateId="candidate",
        symbol="SPY",
        signal=Signal.BUY,
        direction=Direction.LONG,
        entryPrice=100.0,
        stopPrice=99.0,
        targetPrice=101.5,
        quantity=quantity,
        confidence=0.8,
        expectedValue=0.25,
        explanation="test candidate",
        generatedAt=NOW,
        sessionDate=NOW.date(),
        configurationHash="candidate",
    )


def policy() -> EffectiveTradePolicy:
    return EffectiveTradePolicy(
        mode=OperatingMode.OFF,
        baselineSettings=BaselineTradingSettings(configurationHash="baseline"),
        hardRiskLimits=HardRiskLimits(configurationHash="limits"),
        dynamicBounds=DynamicPolicyBounds(
            minConfidence=0.0,
            minReliability=0.0,
            minRegimeFit=0.0,
            maxSpreadPercent=100.0,
            maxParticipationPercent=100.0,
            minLiquidityShares=0,
            configurationHash="bounds",
        ),
        accountRiskState=AccountRiskState(
            accountId="paper",
            equity=25_000.0,
            buyingPower=25_000.0,
            openPositionNotional=0.0,
            realizedPnlToday=0.0,
            tradesToday=0,
            observedAt=NOW,
            sessionDate=NOW.date(),
        ),
        maxQuantity=100,
        maxNotional=10_000.0,
        riskDollars=100.0,
        explanation="test policy",
        effectiveAt=NOW,
        sessionDate=NOW.date(),
        configurationHash="policy",
    )


def gate_decision(*, eligible: bool) -> GlobalGateDecision:
    return GlobalGateDecision(
        status=GateStatus.PASS if eligible else GateStatus.FAIL,
        eligible=eligible,
        dataReady=True,
        gateResults=[],
        reasonCodes=["test.gate"],
        explanation="test gate",
        checkedAt=NOW,
        sessionDate=NOW.date(),
        configurationHash="gate",
    )


def ml_result() -> SafeMLInferenceResult:
    return SafeMLInferenceResult(
        mode=OperatingMode.OFF,
        effectiveMode=OperatingMode.OFF,
        deterministicSignal=Signal.BUY,
        finalSignal=Signal.BUY,
        candidateAccepted=True,
        mlWouldAcceptCandidate=True,
        appliedToOrder=False,
        featureMissingness=0.0,
        modelHealth={"status": "off"},
        recommendedRiskCap=1.0,
        reasonCodes=["test.ml.off"],
        predictedAt=NOW,
        sessionDate=NOW.date(),
        configurationHash="ml",
    )


if __name__ == "__main__":
    unittest.main()
