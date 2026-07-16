from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from pydantic import ValidationError

from backend.app.backtesting import (
    ReplayDecisionSnapshot,
    build_deterministic_v2_activation_report,
    deterministic_v2_active_application_config,
)
from backend.app.domain.models import Signal


NOW = datetime(2026, 1, 5, 15, 30, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)


class DeterministicV2ActivationTest(unittest.TestCase):
    def test_activation_config_enables_v2_static_baseline_and_keeps_shadow_modes(self) -> None:
        config = deterministic_v2_active_application_config().as_dict()
        flags = config["featureFlags"]

        self.assertTrue(flags["strategyEngineV2Enabled"])
        self.assertTrue(flags["familyEnsembleV2Enabled"])
        self.assertTrue(flags["globalGateEngineEnabled"])
        self.assertFalse(flags["metaModelV2Enabled"])
        self.assertFalse(flags["dynamicTradingPolicyEnabled"])

    def test_eligible_order_requires_global_gate_and_static_baseline_policy(self) -> None:
        report = build_deterministic_v2_activation_report(snapshot=snapshot(gate_eligible=True, order_eligible=True), generatedAt=NOW)

        self.assertTrue(report.automaticPaperEntryAllowed)
        self.assertFalse(report.submittedPaperOrder)
        self.assertEqual(report.activationConfig.metaModelV2Mode, "SHADOW")
        self.assertEqual(report.activationConfig.dynamicTradingPolicyMode, "SHADOW")
        self.assertFalse(report.activationConfig.mlMayAffectExecution)
        self.assertFalse(report.activationConfig.dynamicPolicyMayAffectExecution)
        self.assertEqual(report.staticBaselinePolicy["baselineSettings"]["settingsVersion"], "replay_baseline_settings_v2")
        self.assertEqual(report.mlShadow.mode, "SHADOW")
        self.assertFalse(report.mlShadow.appliedToExecution)
        self.assertEqual(report.dynamicPolicyShadow.mode, "SHADOW")
        self.assertFalse(report.dynamicPolicyShadow.appliedToExecution)
        self.assertIn("deterministic_v2.paper_entry_allowed_after_global_gates", report.reasonCodes)

    def test_gate_block_prevents_automatic_paper_entry(self) -> None:
        report = build_deterministic_v2_activation_report(snapshot=snapshot(gate_eligible=False, order_eligible=True), generatedAt=NOW)

        self.assertFalse(report.automaticPaperEntryAllowed)
        self.assertIn("deterministic_v2.paper_entry_not_allowed", report.reasonCodes)

    def test_submitted_order_model_rejects_missing_global_gate_pass(self) -> None:
        report = build_deterministic_v2_activation_report(snapshot=snapshot(gate_eligible=False, order_eligible=True), generatedAt=NOW)
        payload = report.model_dump(mode="json")
        payload["submittedPaperOrder"] = True

        with self.assertRaisesRegex(ValidationError, "must pass global gates"):
            type(report).model_validate(payload)

    def test_rollback_modes_return_to_v1_or_disable_entries_immediately(self) -> None:
        v1 = build_deterministic_v2_activation_report(
            snapshot=snapshot(gate_eligible=True, order_eligible=True),
            rollbackMode="V1",
            generatedAt=NOW,
        )
        disabled = build_deterministic_v2_activation_report(
            snapshot=snapshot(gate_eligible=True, order_eligible=True),
            rollbackMode="DISABLE_AUTOMATIC_ENTRIES",
            generatedAt=NOW,
        )

        self.assertFalse(v1.automaticPaperEntryAllowed)
        self.assertEqual(v1.rollback.effectiveExecutionPath, "V1_ROLLBACK")
        self.assertIn("rollback.v1_baseline_selected", v1.rollback.reasonCodes)
        self.assertFalse(disabled.automaticPaperEntryAllowed)
        self.assertTrue(disabled.rollback.automaticEntriesDisabled)
        self.assertEqual(disabled.rollback.effectiveExecutionPath, "AUTOMATIC_ENTRIES_DISABLED")

    def test_ml_or_dynamic_policy_execution_effect_is_rejected(self) -> None:
        report = build_deterministic_v2_activation_report(snapshot=snapshot(gate_eligible=True, order_eligible=True), generatedAt=NOW)
        payload = report.model_dump(mode="json")
        payload["mlShadow"]["appliedToExecution"] = True

        with self.assertRaisesRegex(ValidationError, "shadow record cannot apply"):
            type(report).model_validate(payload)


def snapshot(*, gate_eligible: bool, order_eligible: bool) -> ReplayDecisionSnapshot:
    return ReplayDecisionSnapshot(
        snapshotId="active-v2-snapshot",
        symbol="SPY",
        decisionTimestampUtc=NOW,
        sessionDate=SESSION_DATE,
        maxInputTimestampUtc=NOW,
        featureSnapshot={"engineVersion": "point_in_time_feature_engine_v1", "dataReady": True},
        strategyOutputs=[{"strategyId": "multi_timeframe_trend_alignment", "signal": "BUY", "dataReady": True}],
        contextOutputs=[{"contextId": "relative_strength_qqq_iwm", "dataReady": True}],
        regimeState={"label": "strong_trend"},
        gateDecision={
            "eligible": gate_eligible,
            "configurationHash": "gate-hash",
            "reasonCodes": [] if gate_eligible else ["gate.data.stale_quote"],
        },
        deterministicCandidate={"candidateId": "candidate-active-v2", "signal": "BUY"},
        ensembleDecision={
            "signal": "BUY",
            "configurationHash": "ensemble-hash",
            "engineVersion": "family_aware_deterministic_ensemble_v1",
        },
        mlInference={
            "mode": "SHADOW",
            "effectiveMode": "SHADOW",
            "appliedToOrder": False,
            "candidateAccepted": True,
            "reasonCodes": ["ml.shadow_record_only"],
        },
        effectivePolicy={
            "mode": "OFF",
            "configurationHash": "policy-hash",
            "baselineSettings": {"settingsVersion": "replay_baseline_settings_v2"},
            "riskDollars": 100.0,
        },
        orderPlan=(
            {
                "orderPlanId": "active-v2-order",
                "orderType": "LIMIT",
                "eligible": True,
                "quantity": 1,
                "entryPrice": 100,
                "limitPrice": 100,
                "validationErrors": [],
            }
            if order_eligible
            else {
                "orderPlanId": "active-v2-no-order",
                "orderType": "NO_ORDER",
                "eligible": False,
                "quantity": 0,
                "entryPrice": 100,
                "validationErrors": ["order.blocked"],
            }
        ),
        fill=None,
        exit=None,
        reasonCodes=["synthetic.activation"],
    )


if __name__ == "__main__":
    unittest.main()
