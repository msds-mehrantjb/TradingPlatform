from __future__ import annotations

import copy
import unittest

from backend.app.algorithms.meta_strategy.inference import (
    MetaStrategyInferenceConfig,
    MetaStrategyInferenceResult,
    MetaStrategyInferenceValidationError,
    apply_meta_strategy_inference,
    validate_inference_result,
)
from backend.tests.test_meta_strategy_step24_inference_engine import FeatureSet, NOW, artifact


def unhealthy_artifact(**model_updates: object) -> dict:
    payload = artifact({"BUY": 0.80, "SELL": 0.10, "HOLD": 0.10})
    payload["createdAt"] = NOW.isoformat()
    payload["models"]["logistic_regression_champion"].update(model_updates)
    return payload


class MetaStrategyStep26RuntimeHealthGatesTest(unittest.TestCase):
    def test_runtime_health_failures_use_deterministic_fallback_without_ml_control(self) -> None:
        cases = {
            "missingness": (
                FeatureSet(missingIndicators={"target_distance": True, "stop_distance": True, "expected_transaction_cost": False}),
                unhealthy_artifact(),
                MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="DETERMINISTIC_BASELINE", maxFeatureMissingness=0.25),
                "meta_strategy.inference.runtime_health.feature_missingness_too_high",
            ),
            "out_of_distribution": (
                FeatureSet(),
                unhealthy_artifact(outOfDistributionScore=0.95),
                MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="DETERMINISTIC_BASELINE", maxOutOfDistributionScore=0.50),
                "meta_strategy.inference.runtime_health.out_of_distribution",
            ),
            "calibration": (
                FeatureSet(),
                unhealthy_artifact(calibration={"method": "sigmoid", "status": "FAILED", "approved": True}),
                MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="DETERMINISTIC_BASELINE"),
                "meta_strategy.inference.runtime_health.calibration_invalid",
            ),
            "model_health": (
                FeatureSet(),
                unhealthy_artifact(modelHealthScore=0.20),
                MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="DETERMINISTIC_BASELINE", minModelHealthScore=0.70),
                "meta_strategy.inference.runtime_health.model_health_too_low",
            ),
            "artifact_age": (
                FeatureSet(),
                {**unhealthy_artifact(), "createdAt": "2025-01-01T00:00:00+00:00"},
                MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="DETERMINISTIC_BASELINE", maxArtifactAgeDays=1),
                "meta_strategy.inference.runtime_health.artifact_too_old",
            ),
            "prediction_latency": (
                FeatureSet(),
                unhealthy_artifact(predictionLatencyMs=50),
                MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="DETERMINISTIC_BASELINE", maxPredictionLatencyMs=10),
                "meta_strategy.inference.runtime_health.prediction_latency_too_high",
            ),
            "candidate_side": (
                FeatureSet(),
                unhealthy_artifact(candidateSidePrediction="SELL"),
                MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="DETERMINISTIC_BASELINE"),
                "meta_strategy.inference.runtime_health.candidate_side_mismatch",
            ),
        }

        for name, (feature_set, model_artifact, config, reason_code) in cases.items():
            with self.subTest(name=name):
                result = apply_meta_strategy_inference(
                    deterministic_signal="BUY",
                    feature_set=feature_set,
                    model_artifact=copy.deepcopy(model_artifact),
                    config=config,
                    deterministic_risk_multiplier=0.45,
                    predicted_at=NOW,
                )

                self.assertEqual(result.effectiveMode, "FALLBACK")
                self.assertEqual(result.decisionAction, "FALLBACK")
                self.assertEqual(result.finalSignal, "BUY")
                self.assertFalse(result.appliedToOrder)
                self.assertFalse(result.mlWouldAcceptCandidate)
                self.assertLessEqual(result.recommendedRiskMultiplier, 0.45)
                self.assertIn(reason_code, result.reasonCodes)
                self.assertFalse(result.auditTrail["runtimeHealth"]["passed"])

    def test_failed_runtime_health_can_hold_according_to_configuration(self) -> None:
        result = apply_meta_strategy_inference(
            deterministic_signal="BUY",
            feature_set=FeatureSet(),
            model_artifact=unhealthy_artifact(outOfDistributionScore=0.95),
            config=MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="NO_TRADE", maxOutOfDistributionScore=0.50),
            deterministic_risk_multiplier=0.45,
            predicted_at=NOW,
        )

        self.assertEqual(result.effectiveMode, "FALLBACK")
        self.assertEqual(result.finalSignal, "HOLD")
        self.assertEqual(result.recommendedRiskMultiplier, 0.0)
        self.assertFalse(result.appliedToOrder)
        self.assertFalse(result.mlWouldAcceptCandidate)
        self.assertIn("meta_strategy.inference.runtime_health.out_of_distribution", result.reasonCodes)

    def test_schema_and_artifact_compatibility_are_runtime_health_gates(self) -> None:
        schema = apply_meta_strategy_inference(
            deterministic_signal="BUY",
            feature_set=FeatureSet(schemaHash="new-schema"),
            model_artifact=artifact({"BUY": 0.95, "SELL": 0.01, "HOLD": 0.04}, schema_hash="old-schema"),
            config=MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="NO_TRADE"),
            predicted_at=NOW,
        )
        missing = apply_meta_strategy_inference(
            deterministic_signal="BUY",
            feature_set=FeatureSet(),
            model_artifact=None,
            config=MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="NO_TRADE"),
            predicted_at=NOW,
        )

        self.assertFalse(schema.auditTrail["runtimeHealth"]["passed"])
        self.assertFalse(missing.auditTrail["runtimeHealth"]["passed"])
        self.assertIn("meta_strategy.inference.runtime_health.feature_schema_mismatch", schema.reasonCodes)
        self.assertIn("meta_strategy.inference.runtime_health.artifact_unavailable", missing.reasonCodes)
        self.assertFalse(schema.appliedToOrder)
        self.assertFalse(missing.appliedToOrder)

    def test_validation_rejects_ml_application_after_failed_runtime_health(self) -> None:
        invalid = MetaStrategyInferenceResult(
            mode="FILTER",
            effectiveMode="FILTER",
            deterministicSignal="BUY",
            finalSignal="BUY",
            candidateAccepted=True,
            mlWouldAcceptCandidate=True,
            appliedToOrder=True,
            hardGatesPassed=True,
            deterministicRiskMultiplier=0.5,
            recommendedRiskMultiplier=0.5,
            modelHealth={"runtimeHealth": {"passed": False}},
            sessionDate=NOW.date(),
            predictedAt=NOW,
        )

        with self.assertRaisesRegex(MetaStrategyInferenceValidationError, "failed_runtime_health_cannot_apply_ml"):
            validate_inference_result(invalid)


if __name__ == "__main__":
    unittest.main()
