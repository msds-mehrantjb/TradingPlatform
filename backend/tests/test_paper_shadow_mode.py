from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from pydantic import ValidationError

from backend.app.backtesting import (
    CurrentBaselineDecision,
    ReplayDecisionSnapshot,
    build_paper_shadow_report,
    paper_shadow_application_config,
)
from backend.app.domain.models import Signal


START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)


class PaperShadowModeTest(unittest.TestCase):
    def test_config_runs_complete_deterministic_path_without_ml_or_dynamic_policy(self) -> None:
        config = paper_shadow_application_config().as_dict()
        flags = config["featureFlags"]

        self.assertTrue(flags["strategyEngineV2Enabled"])
        self.assertTrue(flags["familyEnsembleV2Enabled"])
        self.assertTrue(flags["globalGateEngineEnabled"])
        self.assertFalse(flags["metaModelV2Enabled"])
        self.assertFalse(flags["dynamicTradingPolicyEnabled"])

    def test_report_records_what_v2_would_have_done_without_submission(self) -> None:
        report = build_paper_shadow_report(
            v2Snapshot=snapshot(order_eligible=True),
            baselineDecision=baseline(Signal.HOLD, would_trade=False),
            generatedAt=START,
        )

        self.assertTrue(report.mode.globalGateEngineEnabled)
        self.assertEqual(report.mode.policyMode, "STATIC_BASELINE")
        self.assertEqual(report.mode.mlMode, "OFF")
        self.assertFalse(report.mode.paperOrderSubmissionEnabled)
        self.assertFalse(report.automaticPaperSubmission)
        self.assertEqual(report.v2DecisionSnapshot["submissionStatus"], "NOT_SUBMITTED_SHADOW_ONLY")
        self.assertTrue(report.comparison.signalChanged)
        self.assertFalse(report.comparison.baselineWouldTrade)
        self.assertTrue(report.comparison.v2WouldPlanOrder)
        self.assertEqual(report.comparison.orderQuantityDelta, 1)
        self.assertTrue(report.inputFreshness.fresh)
        self.assertTrue(report.inputFreshness.reproducible)
        self.assertTrue(report.activationReady)
        self.assertEqual(report.operationalProblems[0].problemId, "no_unresolved_operational_problem")
        self.assertIn("paper_shadow.no_automatic_submission", report.reasonCodes)

    def test_stale_or_unreproducible_inputs_create_activation_blocker(self) -> None:
        stale = snapshot(order_eligible=False, data_ready=False, max_input=START + timedelta(minutes=1))
        report = build_paper_shadow_report(
            v2Snapshot=stale,
            baselineDecision=baseline(Signal.HOLD, would_trade=False),
            generatedAt=START,
        )

        self.assertFalse(report.inputFreshness.fresh)
        self.assertFalse(report.activationReady)
        blocker_ids = {problem.problemId for problem in report.operationalProblems if problem.mustResolveBeforeActivation}
        self.assertIn("input_freshness_or_reproducibility", blocker_ids)
        self.assertIn("global_gate_block", blocker_ids)

    def test_ml_or_dynamic_policy_cannot_affect_paper_shadow_orders(self) -> None:
        bad = snapshot(order_eligible=True)
        bad = bad.model_copy(
            update={
                "mlInference": {**bad.mlInference, "effectiveMode": "ACTIVE", "appliedToOrder": True},
                "effectivePolicy": {**bad.effectivePolicy, "mode": "ACTIVE"},
            }
        )

        with self.assertRaisesRegex(ValidationError, "ML"):
            build_paper_shadow_report(
                v2Snapshot=bad,
                baselineDecision=baseline(Signal.BUY, would_trade=True),
                generatedAt=START,
            )


def baseline(signal: Signal, *, would_trade: bool) -> CurrentBaselineDecision:
    return CurrentBaselineDecision(
        decisionTimestampUtc=START,
        signal=signal,
        wouldTrade=would_trade,
        orderQuantity=1 if would_trade else None,
        expectedNotional=100 if would_trade else None,
        rawDecision={"signal": signal.value},
        explanation="Synthetic current baseline decision.",
    )


def snapshot(
    *,
    order_eligible: bool,
    data_ready: bool = True,
    max_input: datetime = START,
) -> ReplayDecisionSnapshot:
    return ReplayDecisionSnapshot(
        snapshotId="paper-shadow-v2-1",
        symbol="SPY",
        decisionTimestampUtc=START,
        sessionDate=SESSION_DATE,
        maxInputTimestampUtc=max_input,
        featureSnapshot={
            "engineVersion": "point_in_time_feature_engine_v1",
            "dataReady": data_ready,
            "reasonCodes": [] if data_ready else ["quote_stale"],
        },
        strategyOutputs=[{"strategyId": "multi_timeframe_trend_alignment", "dataReady": data_ready}],
        contextOutputs=[{"contextId": "relative_strength_qqq_iwm", "dataReady": data_ready}],
        regimeState={"label": "strong_trend"},
        gateDecision={
            "eligible": order_eligible,
            "configurationHash": "gate-hash",
            "reasonCodes": [] if order_eligible else ["gate.data.stale_quote"],
        },
        deterministicCandidate={"candidateId": "candidate-paper-shadow", "signal": "BUY"},
        ensembleDecision={
            "signal": "BUY",
            "configurationHash": "ensemble-hash",
            "finalScore": 0.72,
        },
        mlInference={
            "mode": "OFF",
            "effectiveMode": "OFF",
            "appliedToOrder": False,
            "candidateAccepted": order_eligible,
        },
        effectivePolicy={
            "mode": "OFF",
            "configurationHash": "policy-hash",
            "baselineSettings": {"settingsVersion": "replay_baseline_settings_v2"},
        },
        orderPlan=(
            {
                "orderPlanId": "order-paper-shadow",
                "orderType": "LIMIT",
                "eligible": True,
                "quantity": 1,
                "entryPrice": 100,
                "limitPrice": 100,
                "validationErrors": [],
            }
            if order_eligible
            else {
                "orderPlanId": "blocked-paper-shadow",
                "orderType": "NO_ORDER",
                "eligible": False,
                "quantity": 0,
                "entryPrice": 100,
                "validationErrors": ["gate.data.stale_quote"],
            }
        ),
        fill=None,
        exit=None,
        reasonCodes=["synthetic.paper_shadow"],
    )


if __name__ == "__main__":
    unittest.main()
