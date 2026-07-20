from __future__ import annotations

import unittest

from backend.app.algorithms.meta_strategy.inference import MetaStrategyInferenceConfig, apply_meta_strategy_inference
from backend.tests.test_meta_strategy_step24_inference_engine import FeatureSet, NOW, artifact


class MetaStrategyStep25CandidateConditionalInferenceTest(unittest.TestCase):
    def test_model_output_is_candidate_conditional_and_auditable(self) -> None:
        result = apply_meta_strategy_inference(
            deterministic_signal="BUY",
            feature_set=FeatureSet(),
            model_artifact=artifact({"BUY": 0.70, "SELL": 0.20, "HOLD": 0.10}, ood_score=0.12),
            config=MetaStrategyInferenceConfig(mode="FILTER", minSuccessProbability=0.55),
            predicted_at=NOW,
        )

        self.assertEqual(result.candidateSide, "BUY")
        self.assertEqual(result.probabilityOfSuccess, result.probabilityTargetFirst)
        self.assertAlmostEqual(result.probabilityTargetFirst or 0.0, 0.70)
        self.assertAlmostEqual(result.probabilityStopFirst or 0.0, 0.20)
        self.assertAlmostEqual(result.probabilityTimeout or 0.0, 0.10)
        self.assertIsNotNone(result.uncertainty)
        self.assertEqual(result.outOfDistributionScore, 0.12)
        self.assertEqual(result.decisionAction, "ACCEPT")
        self.assertEqual(result.auditTrail["modelOutputType"], "candidate_conditional")
        self.assertTrue(result.auditTrail["oppositeDirectionPredictionsRejectOnly"])
        self.assertEqual(result.auditTrail["candidateConditionalOutput"]["candidate_side"], "BUY")

    def test_opposite_direction_prediction_rejects_instead_of_reversing_candidate(self) -> None:
        result = apply_meta_strategy_inference(
            deterministic_signal="BUY",
            feature_set=FeatureSet(),
            model_artifact=artifact({"BUY": 0.10, "SELL": 0.85, "HOLD": 0.05}),
            config=MetaStrategyInferenceConfig(mode="FILTER", minSuccessProbability=0.55),
            predicted_at=NOW,
        )

        self.assertEqual(result.candidateSide, "BUY")
        self.assertEqual(result.finalSignal, "HOLD")
        self.assertEqual(result.decisionAction, "REJECT")
        self.assertNotEqual(result.finalSignal, "SELL")
        self.assertAlmostEqual(result.probabilityTargetFirst or 0.0, 0.10)
        self.assertAlmostEqual(result.probabilityStopFirst or 0.0, 0.85)
        self.assertIn("meta_strategy.inference.current_candidate_probability_below_threshold", result.reasonCodes)

    def test_sell_candidate_uses_sell_probability_as_target_first(self) -> None:
        result = apply_meta_strategy_inference(
            deterministic_signal="SELL",
            feature_set=FeatureSet(),
            model_artifact=artifact({"BUY": 0.15, "SELL": 0.75, "HOLD": 0.10}),
            config=MetaStrategyInferenceConfig(mode="FILTER", minSuccessProbability=0.55),
            predicted_at=NOW,
        )

        self.assertEqual(result.candidateSide, "SELL")
        self.assertEqual(result.finalSignal, "SELL")
        self.assertEqual(result.decisionAction, "ACCEPT")
        self.assertAlmostEqual(result.probabilityTargetFirst or 0.0, 0.75)
        self.assertAlmostEqual(result.probabilityStopFirst or 0.0, 0.15)

    def test_risk_reduction_returns_reduce_risk_action_without_direction_change(self) -> None:
        result = apply_meta_strategy_inference(
            deterministic_signal="BUY",
            feature_set=FeatureSet(),
            model_artifact=artifact({"BUY": 0.65, "SELL": 0.25, "HOLD": 0.10}),
            config=MetaStrategyInferenceConfig(mode="RISK_REDUCTION", minSuccessProbability=0.55, riskReductionMaxMultiplier=0.7),
            deterministic_risk_multiplier=0.9,
            predicted_at=NOW,
        )

        self.assertEqual(result.finalSignal, "BUY")
        self.assertEqual(result.decisionAction, "REDUCE_RISK")
        self.assertLess(result.recommendedRiskMultiplier, result.deterministicRiskMultiplier)

    def test_fallback_returns_fallback_action_with_deterministic_direction_preserved(self) -> None:
        result = apply_meta_strategy_inference(
            deterministic_signal="SELL",
            feature_set=FeatureSet(),
            model_artifact=None,
            config=MetaStrategyInferenceConfig(mode="FILTER", fallbackBehavior="DETERMINISTIC_BASELINE"),
            deterministic_risk_multiplier=0.4,
            predicted_at=NOW,
        )

        self.assertEqual(result.effectiveMode, "FALLBACK")
        self.assertEqual(result.decisionAction, "FALLBACK")
        self.assertEqual(result.candidateSide, "SELL")
        self.assertEqual(result.finalSignal, "SELL")
        self.assertLessEqual(result.recommendedRiskMultiplier, 0.4)


if __name__ == "__main__":
    unittest.main()
