from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.app.backtesting import (
    DynamicPolicyRollbackControls,
    DynamicPolicyStageComparisonReport,
    build_dynamic_policy_activation_report,
)
from backend.tests.test_dynamic_policy_shadow import NOW, snapshot


class DynamicPolicyActivationTest(unittest.TestCase):
    def test_staged_activation_applies_dynamic_order_fields_and_keeps_guardrails(self) -> None:
        stages = ["RISK_REDUCTION", "STOP_AND_QUANTITY", "STRATEGY_FAMILY_ENTRY", "TARGET_AND_TIME_STOP"]
        report = build_dynamic_policy_activation_report(
            snapshot=snapshot(),
            requestedStages=stages,
            stageComparisons=[comparison(stage) for stage in stages],
            generatedAt=NOW,
        )

        self.assertEqual(report.activeStages, stages)
        self.assertEqual(report.rolledBackStages, [])
        self.assertTrue(report.globalRiskAuthoritative)
        self.assertTrue(report.brokerReconciliationAuthoritative)
        self.assertTrue(report.mlLimitedToTradeFiltering)
        self.assertFalse(report.pyramidingEnabled)
        self.assertFalse(report.partialExitsEnabled)
        self.assertFalse(report.trailingBehaviorEnabled)
        self.assertTrue(report.orderPolicyMatch.matchesDisplayedPolicy)
        self.assertIsNotNone(report.activatedPaperOrderPlan)
        assert report.dynamicPolicy is not None
        assert report.activatedPaperOrderPlan is not None
        self.assertEqual(report.activatedPaperOrderPlan["quantity"], report.dynamicPolicy["quantity"])
        self.assertEqual(report.activatedPaperOrderPlan["orderType"], report.dynamicPolicy["entryPlan"]["orderType"])
        self.assertEqual(report.activatedPaperOrderPlan["maximumHoldingMinutes"], report.dynamicPolicy["holdingPeriodMinutes"])
        self.assertIn("dynamic_policy.stage_active.risk_reduction", report.reasonCodes)
        self.assertIn("dynamic_policy.stage_active.target_and_time_stop", report.reasonCodes)

    def test_stage_requires_walk_forward_and_paper_shadow_comparison(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing dynamic policy stage comparison report"):
            build_dynamic_policy_activation_report(
                snapshot=snapshot(),
                requestedStages=["RISK_REDUCTION", "STOP_AND_QUANTITY"],
                stageComparisons=[comparison("RISK_REDUCTION")],
                generatedAt=NOW,
            )

    def test_rollback_disables_capability_independently(self) -> None:
        report = build_dynamic_policy_activation_report(
            snapshot=snapshot(),
            requestedStages=["RISK_REDUCTION", "STOP_AND_QUANTITY"],
            stageComparisons=[comparison("RISK_REDUCTION"), comparison("STOP_AND_QUANTITY")],
            rollback=DynamicPolicyRollbackControls(disableStopAndQuantity=True, reasonCodes=["rollback.stop_quantity"]),
            generatedAt=NOW,
        )

        self.assertEqual(report.activeStages, ["RISK_REDUCTION"])
        self.assertEqual(report.rolledBackStages, ["STOP_AND_QUANTITY"])
        self.assertIn("dynamic_policy.stage_rollback.stop_and_quantity", report.reasonCodes)
        self.assertNotIn("quantity", report.orderPolicyMatch.checkedFields)

    def test_trailing_behavior_remains_disabled_until_separate_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "trailing behavior requires separate validation"):
            build_dynamic_policy_activation_report(
                snapshot=snapshot(),
                requestedStages=["TRAILING_BEHAVIOR"],
                stageComparisons=[comparison("TRAILING_BEHAVIOR")],
                rollback=DynamicPolicyRollbackControls(disableTrailingBehavior=False),
                generatedAt=NOW,
            )

    def test_report_rejects_order_policy_mismatch(self) -> None:
        report = build_dynamic_policy_activation_report(
            snapshot=snapshot(),
            requestedStages=["RISK_REDUCTION"],
            stageComparisons=[comparison("RISK_REDUCTION")],
            generatedAt=NOW,
        )
        payload = report.model_dump(mode="json")
        payload["orderPolicyMatch"]["matchesDisplayedPolicy"] = False
        payload["orderPolicyMatch"]["mismatches"] = ["quantity"]

        with self.assertRaisesRegex(ValidationError, "paper orders must match"):
            type(report).model_validate(payload)

    def test_bad_stage_evidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "cannot activate"):
            DynamicPolicyStageComparisonReport(
                stage="RISK_REDUCTION",
                walkForwardReplayWindow="2026-01",
                paperShadowWindow="2026-02",
                walkForwardRiskAdjustedDelta=-0.01,
                paperShadowRiskAdjustedDelta=0.0,
                walkForwardSampleCount=50,
                paperShadowSampleCount=10,
                improvesOrPreservesRiskAdjustedResults=False,
                explanation="Bad evidence should not activate.",
            )


def comparison(stage: str) -> DynamicPolicyStageComparisonReport:
    return DynamicPolicyStageComparisonReport(
        stage=stage,  # type: ignore[arg-type]
        walkForwardReplayWindow="2026-01-01/2026-02-01",
        paperShadowWindow="2026-02-02/2026-02-15",
        walkForwardRiskAdjustedDelta=0.01,
        paperShadowRiskAdjustedDelta=0.0,
        walkForwardSampleCount=100,
        paperShadowSampleCount=25,
        improvesOrPreservesRiskAdjustedResults=True,
        reasonCodes=[f"comparison.{stage.lower()}.passed"],
        explanation="Synthetic comparison preserves or improves risk-adjusted results.",
    )


if __name__ == "__main__":
    unittest.main()
