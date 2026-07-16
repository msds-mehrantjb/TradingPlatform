from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.app.backtesting import (
    MLRiskModifierConfig,
    MLRiskModifierDecision,
    build_ml_risk_modifier_experiment_report,
    ml_risk_modifier_config,
)
from backend.app.domain.models import Signal
from backend.tests.test_dynamic_policy_activation import comparison
from backend.tests.test_dynamic_policy_shadow import NOW, snapshot


class MLRiskModifierExperimentTest(unittest.TestCase):
    def test_feature_is_disabled_by_default_and_uses_deterministic_fallback(self) -> None:
        report = build_ml_risk_modifier_experiment_report(
            snapshot=ml_snapshot(),
            requestedStages=["RISK_REDUCTION"],
            stageComparisons=[comparison("RISK_REDUCTION")],
            generatedAt=NOW,
        )

        decision = report.mlRiskModifierDecision
        self.assertFalse(report.config.experimentEnabled)
        self.assertTrue(report.featureDisabledByDefault)
        self.assertTrue(report.deterministicPolicyFallback)
        self.assertTrue(decision.deterministicFallbackUsed)
        self.assertFalse(decision.featureEnabled)
        self.assertEqual(decision.mlRiskMultiplier, 1.0)
        self.assertEqual(decision.modifiedRiskDollars, decision.deterministicRiskDollars)
        self.assertIn("ml_risk_modifier.disabled_by_default", report.reasonCodes)

    def test_enabled_experiment_is_independently_measurable_and_only_reduces_risk(self) -> None:
        report = build_ml_risk_modifier_experiment_report(
            snapshot=ml_snapshot(success=0.80, expected_value=0.50, uncertainty=0.30, ood=0.20, slippage_bps=6.0),
            requestedStages=["RISK_REDUCTION"],
            stageComparisons=[comparison("RISK_REDUCTION")],
            config=enabled_config(),
            generatedAt=NOW,
        )

        decision = report.mlRiskModifierDecision
        self.assertTrue(report.independentlyMeasurable)
        self.assertFalse(decision.deterministicFallbackUsed)
        self.assertTrue(decision.featureEnabled)
        self.assertLessEqual(decision.modifiedRiskDollars, decision.deterministicRiskDollars)
        self.assertLessEqual(decision.modifiedRiskDollars, decision.baselineRiskDollars)
        self.assertLessEqual(decision.modifiedRiskDollars, decision.hardRiskCapDollars)
        self.assertGreater(decision.riskReductionDollars, 0)
        self.assertEqual({factor.factorName for factor in decision.factors}, {
            "successProbabilityCap",
            "expectedValueCap",
            "uncertaintyCap",
            "outOfDistributionCap",
            "expectedSlippageCap",
        })
        self.assertIn("ml_risk_modifier.independent_experiment", report.reasonCodes)

    def test_uncertain_or_ood_predictions_only_reduce_risk_or_recommend_no_trade(self) -> None:
        uncertain = build_ml_risk_modifier_experiment_report(
            snapshot=ml_snapshot(success=0.75, expected_value=0.8, uncertainty=0.90, ood=0.20, slippage_bps=3.0),
            requestedStages=["RISK_REDUCTION"],
            stageComparisons=[comparison("RISK_REDUCTION")],
            config=enabled_config(),
            generatedAt=NOW,
        )
        ood = build_ml_risk_modifier_experiment_report(
            snapshot=ml_snapshot(success=0.75, expected_value=0.8, uncertainty=0.30, ood=0.95, slippage_bps=3.0),
            requestedStages=["RISK_REDUCTION"],
            stageComparisons=[comparison("RISK_REDUCTION")],
            config=enabled_config(),
            generatedAt=NOW,
        )

        self.assertTrue(uncertain.mlRiskModifierDecision.noTradeRecommended)
        self.assertEqual(uncertain.mlRiskModifierDecision.modifiedRiskDollars, 0.0)
        self.assertIn("ml_risk_modifier.uncertainty_too_high", uncertain.mlRiskModifierDecision.reasonCodes)
        self.assertTrue(ood.mlRiskModifierDecision.noTradeRecommended)
        self.assertEqual(ood.mlRiskModifierDecision.modifiedRiskDollars, 0.0)
        self.assertIn("ml_risk_modifier.ood_too_high", ood.mlRiskModifierDecision.reasonCodes)

    def test_experiment_requires_stable_filter_and_dynamic_policy(self) -> None:
        with self.assertRaisesRegex(ValidationError, "requires stable ML filter"):
            MLRiskModifierConfig(experimentEnabled=True)

    def test_forbidden_capabilities_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "bounded additional risk cap"):
            MLRiskModifierConfig(allowStopModification=True)
        with self.assertRaisesRegex(ValidationError, "bounded additional risk cap"):
            MLRiskModifierConfig(allowDirectionCreation=True)
        with self.assertRaisesRegex(ValidationError, "bounded additional risk cap"):
            MLRiskModifierConfig(allowHardLimitOverride=True)
        with self.assertRaisesRegex(ValidationError, "bounded additional risk cap"):
            MLRiskModifierConfig(allowLosingPositionIncrease=True)

    def test_decision_rejects_excess_risk_stop_changes_direction_creation_and_losing_increase(self) -> None:
        report = build_ml_risk_modifier_experiment_report(
            snapshot=ml_snapshot(),
            requestedStages=["RISK_REDUCTION"],
            stageComparisons=[comparison("RISK_REDUCTION")],
            config=enabled_config(),
            generatedAt=NOW,
        )
        payload = report.mlRiskModifierDecision.model_dump(mode="json")
        payload["modifiedRiskDollars"] = payload["deterministicRiskDollars"] + 1
        with self.assertRaisesRegex(ValidationError, "cannot exceed deterministic"):
            MLRiskModifierDecision.model_validate(payload)
        payload = report.mlRiskModifierDecision.model_dump(mode="json")
        payload["stopUnchanged"] = False
        with self.assertRaisesRegex(ValidationError, "cannot widen or modify stops"):
            MLRiskModifierDecision.model_validate(payload)
        payload = report.mlRiskModifierDecision.model_dump(mode="json")
        payload["deterministicSignal"] = "HOLD"
        payload["finalSignal"] = "BUY"
        payload["directionCreated"] = True
        with self.assertRaisesRegex(ValidationError, "cannot create a direction"):
            MLRiskModifierDecision.model_validate(payload)
        payload = report.mlRiskModifierDecision.model_dump(mode="json")
        payload["losingPositionIncreased"] = True
        with self.assertRaisesRegex(ValidationError, "cannot increase a losing position"):
            MLRiskModifierDecision.model_validate(payload)


def enabled_config() -> MLRiskModifierConfig:
    return ml_risk_modifier_config(
        experiment_enabled=True,
        ml_filter_stable=True,
        deterministic_dynamic_policy_stable=True,
    )


def ml_snapshot(
    *,
    success: float = 0.80,
    expected_value: float = 0.60,
    uncertainty: float = 0.30,
    ood: float = 0.20,
    slippage_bps: float = 4.0,
):
    result = snapshot()
    candidate = dict(result.deterministicCandidate or {})
    features = dict(candidate.get("features") or {})
    features["expectedSlippageBps"] = slippage_bps
    candidate["features"] = features
    ml = dict(result.mlInference)
    ml.update(
        {
            "mode": "FILTER",
            "effectiveMode": "FILTER",
            "deterministicSignal": Signal.BUY.value,
            "finalSignal": Signal.BUY.value,
            "candidateAccepted": True,
            "appliedToOrder": True,
            "successProbability": success,
            "calibratedProbability": success,
            "expectedValueAfterCosts": expected_value,
            "uncertainty": uncertainty,
            "outOfDistributionScore": ood,
            "reasonCodes": ["ml.mode_filter"],
        }
    )
    return result.model_copy(update={"deterministicCandidate": candidate, "mlInference": ml})


if __name__ == "__main__":
    unittest.main()
