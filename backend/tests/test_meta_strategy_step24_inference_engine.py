from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import UTC, datetime

from backend.app.algorithms.meta_strategy.inference import (
    MetaStrategyInferenceConfig,
    MetaStrategyInferenceResult,
    MetaStrategyInferenceValidationError,
    apply_meta_strategy_inference,
    validate_inference_result,
)


SCHEMA_HASH = "meta-strategy-inference-schema"
NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


@dataclass(frozen=True)
class FeatureSet:
    schemaHash: str = SCHEMA_HASH
    featureValues: dict[str, float] | None = None
    missingIndicators: dict[str, bool] | None = None

    def __post_init__(self) -> None:
        if self.featureValues is None:
            object.__setattr__(
                self,
                "featureValues",
                {"target_distance": 1.5, "stop_distance": 1.0, "expected_transaction_cost": 0.05},
            )
        if self.missingIndicators is None:
            object.__setattr__(
                self,
                "missingIndicators",
                {"target_distance": False, "stop_distance": False, "expected_transaction_cost": False},
            )


def artifact(probabilities: dict[str, float], *, schema_hash: str = SCHEMA_HASH, ood_score: float = 0.0, health: float = 1.0) -> dict:
    return {
        "featureSchemaHash": schema_hash,
        "championModel": "logistic_regression_champion",
        "models": {
            "logistic_regression_champion": {
                "available": True,
                "kind": "fixed_probability_test_model",
                "featureSchemaHash": schema_hash,
                "fixedProbabilities": probabilities,
                "outOfDistributionScore": ood_score,
                "modelHealthScore": health,
                "calibration": {"method": "none"},
            }
        },
    }


class MetaStrategyStep24InferenceEngineTest(unittest.TestCase):
    def test_supported_modes_are_explicit_and_conservative(self) -> None:
        configs = {
            "OFF": MetaStrategyInferenceConfig(mode="OFF"),
            "SHADOW": MetaStrategyInferenceConfig(mode="SHADOW", minSuccessProbability=0.8),
            "FILTER": MetaStrategyInferenceConfig(mode="FILTER", minSuccessProbability=0.55),
            "RISK_REDUCTION": MetaStrategyInferenceConfig(mode="RISK_REDUCTION", minSuccessProbability=0.55, riskReductionMaxMultiplier=0.6),
            "FALLBACK": MetaStrategyInferenceConfig(mode="FALLBACK", fallbackBehavior="DETERMINISTIC_BASELINE"),
            "DISABLED": MetaStrategyInferenceConfig(mode="DISABLED"),
        }

        results = {
            mode: apply_meta_strategy_inference(
                deterministic_signal="BUY",
                feature_set=FeatureSet(),
                model_artifact=artifact({"BUY": 0.75, "SELL": 0.05, "HOLD": 0.20}),
                config=config,
                deterministic_risk_multiplier=0.8,
                predicted_at=NOW,
            )
            for mode, config in configs.items()
        }

        self.assertEqual(results["OFF"].finalSignal, "BUY")
        self.assertFalse(results["OFF"].appliedToOrder)
        self.assertEqual(results["DISABLED"].finalSignal, "BUY")
        self.assertFalse(results["SHADOW"].appliedToOrder)
        self.assertEqual(results["SHADOW"].finalSignal, "BUY")
        self.assertEqual(results["FILTER"].finalSignal, "BUY")
        self.assertEqual(results["RISK_REDUCTION"].finalSignal, "BUY")
        self.assertLessEqual(results["RISK_REDUCTION"].recommendedRiskMultiplier, 0.6)
        self.assertEqual(results["FALLBACK"].effectiveMode, "FALLBACK")
        self.assertEqual(results["FALLBACK"].finalSignal, "BUY")

    def test_hold_cannot_become_buy_or_sell(self) -> None:
        result = apply_meta_strategy_inference(
            deterministic_signal="HOLD",
            feature_set=FeatureSet(),
            model_artifact=artifact({"BUY": 0.95, "SELL": 0.01, "HOLD": 0.04}),
            config=MetaStrategyInferenceConfig(mode="RISK_REDUCTION", minSuccessProbability=0.55),
            predicted_at=NOW,
        )

        self.assertEqual(result.finalSignal, "HOLD")
        self.assertFalse(result.candidateAccepted)
        self.assertIn("meta_strategy.inference.cannot_create_trade_from_hold", result.reasonCodes)

    def test_buy_and_sell_candidates_cannot_flip_direction(self) -> None:
        buy_result = apply_meta_strategy_inference(
            deterministic_signal="BUY",
            feature_set=FeatureSet(),
            model_artifact=artifact({"BUY": 0.10, "SELL": 0.90, "HOLD": 0.0}),
            config=MetaStrategyInferenceConfig(mode="FILTER", minSuccessProbability=0.55),
            predicted_at=NOW,
        )
        sell_result = apply_meta_strategy_inference(
            deterministic_signal="SELL",
            feature_set=FeatureSet(),
            model_artifact=artifact({"BUY": 0.90, "SELL": 0.10, "HOLD": 0.0}),
            config=MetaStrategyInferenceConfig(mode="FILTER", minSuccessProbability=0.55),
            predicted_at=NOW,
        )

        self.assertEqual(buy_result.finalSignal, "HOLD")
        self.assertNotEqual(buy_result.finalSignal, "SELL")
        self.assertEqual(sell_result.finalSignal, "HOLD")
        self.assertNotEqual(sell_result.finalSignal, "BUY")

    def test_ml_cannot_bypass_safety_gates(self) -> None:
        result = apply_meta_strategy_inference(
            deterministic_signal="BUY",
            feature_set=FeatureSet(),
            model_artifact=artifact({"BUY": 0.95, "SELL": 0.01, "HOLD": 0.04}),
            config=MetaStrategyInferenceConfig(mode="RISK_REDUCTION", minSuccessProbability=0.55),
            hard_gates_passed=False,
            predicted_at=NOW,
        )

        self.assertEqual(result.finalSignal, "HOLD")
        self.assertFalse(result.candidateAccepted)
        self.assertIn("meta_strategy.inference.safety_gate_failed_no_bypass", result.reasonCodes)

    def test_ml_cannot_increase_deterministic_risk(self) -> None:
        result = apply_meta_strategy_inference(
            deterministic_signal="BUY",
            feature_set=FeatureSet(),
            model_artifact=artifact({"BUY": 0.95, "SELL": 0.01, "HOLD": 0.04}),
            config=MetaStrategyInferenceConfig(mode="RISK_REDUCTION", minSuccessProbability=0.55, riskReductionMaxMultiplier=1.0),
            deterministic_risk_multiplier=0.35,
            predicted_at=NOW,
        )

        self.assertLessEqual(result.recommendedRiskMultiplier, 0.35)

    def test_schema_ood_and_missing_artifact_use_fallback_without_increasing_risk(self) -> None:
        schema = apply_meta_strategy_inference(
            deterministic_signal="BUY",
            feature_set=FeatureSet(schemaHash="new-schema"),
            model_artifact=artifact({"BUY": 0.95, "SELL": 0.01, "HOLD": 0.04}, schema_hash="old-schema"),
            config=MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="DETERMINISTIC_BASELINE"),
            deterministic_risk_multiplier=0.4,
            predicted_at=NOW,
        )
        ood = apply_meta_strategy_inference(
            deterministic_signal="BUY",
            feature_set=FeatureSet(),
            model_artifact=artifact({"BUY": 0.95, "SELL": 0.01, "HOLD": 0.04}, ood_score=0.95),
            config=MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="DETERMINISTIC_BASELINE", maxOutOfDistributionScore=0.5),
            deterministic_risk_multiplier=0.4,
            predicted_at=NOW,
        )
        missing = apply_meta_strategy_inference(
            deterministic_signal="BUY",
            feature_set=FeatureSet(),
            model_artifact=None,
            config=MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="DETERMINISTIC_BASELINE"),
            deterministic_risk_multiplier=0.4,
            predicted_at=NOW,
        )

        for result in (schema, ood, missing):
            self.assertEqual(result.effectiveMode, "FALLBACK")
            self.assertEqual(result.finalSignal, "BUY")
            self.assertLessEqual(result.recommendedRiskMultiplier, 0.4)
        self.assertIn("meta_strategy.inference.feature_schema_mismatch", schema.reasonCodes)
        self.assertIn("meta_strategy.inference.out_of_distribution", ood.reasonCodes)
        self.assertIn("meta_strategy.inference.model_unavailable", missing.reasonCodes)

    def test_result_validation_rejects_invalid_policy_outputs(self) -> None:
        invalid = MetaStrategyInferenceResult(
            mode="FILTER",
            effectiveMode="FILTER",
            deterministicSignal="BUY",
            finalSignal="SELL",
            candidateAccepted=True,
            mlWouldAcceptCandidate=True,
            appliedToOrder=True,
            hardGatesPassed=True,
            deterministicRiskMultiplier=0.5,
            recommendedRiskMultiplier=0.5,
            sessionDate=NOW.date(),
            predictedAt=NOW,
        )
        with self.assertRaisesRegex(MetaStrategyInferenceValidationError, "buy_cannot_become_sell"):
            validate_inference_result(invalid)

        risk = MetaStrategyInferenceResult(
            mode="RISK_REDUCTION",
            effectiveMode="RISK_REDUCTION",
            deterministicSignal="BUY",
            finalSignal="BUY",
            candidateAccepted=True,
            mlWouldAcceptCandidate=True,
            appliedToOrder=True,
            hardGatesPassed=True,
            deterministicRiskMultiplier=0.3,
            recommendedRiskMultiplier=0.4,
            sessionDate=NOW.date(),
            predictedAt=NOW,
        )
        with self.assertRaisesRegex(MetaStrategyInferenceValidationError, "increase_deterministic_risk"):
            validate_inference_result(risk)


if __name__ == "__main__":
    unittest.main()
