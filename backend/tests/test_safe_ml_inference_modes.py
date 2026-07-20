from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from backend.app.domain.models import OperatingMode, Signal
from backend.app.algorithms.meta_strategy.inference.safe_inference import SafeMLInferenceConfig, apply_safe_ml_inference
from backend.app.algorithms.meta_strategy.ml_features import MLFeatureSet


SCHEMA_HASH = "candidate-meta-schema-test"


class SafeMLInferenceModesTest(unittest.TestCase):
    def test_shadow_mode_records_prediction_without_changing_order_acceptance(self) -> None:
        prediction = apply_safe_ml_inference(
            deterministic_signal=Signal.BUY,
            feature_set=feature_set(),
            model_artifact=artifact({"BUY": 0.30, "SELL": 0.10, "HOLD": 0.60}),
            config=SafeMLInferenceConfig(mode=OperatingMode.SHADOW, minSuccessProbability=0.60),
            hard_gates_passed=True,
            candidate_eligible=True,
            predicted_at=now(),
        )

        self.assertEqual(prediction.finalSignal, Signal.BUY.value)
        self.assertTrue(prediction.candidateAccepted)
        self.assertFalse(prediction.mlWouldAcceptCandidate)
        self.assertFalse(prediction.appliedToOrder)
        self.assertIn("ml.shadow_record_only", prediction.reasonCodes)

    def test_filter_mode_only_rejects_or_accepts_candidate_side(self) -> None:
        rejected = apply_safe_ml_inference(
            deterministic_signal=Signal.SELL,
            feature_set=feature_set(),
            model_artifact=artifact({"BUY": 0.80, "SELL": 0.10, "HOLD": 0.10}),
            config=SafeMLInferenceConfig(mode=OperatingMode.FILTER, minSuccessProbability=0.55),
            predicted_at=now(),
        )
        accepted = apply_safe_ml_inference(
            deterministic_signal=Signal.SELL,
            feature_set=feature_set(),
            model_artifact=artifact({"BUY": 0.10, "SELL": 0.80, "HOLD": 0.10}),
            config=SafeMLInferenceConfig(mode=OperatingMode.FILTER, minSuccessProbability=0.55),
            predicted_at=now(),
        )

        self.assertEqual(rejected.finalSignal, Signal.HOLD.value)
        self.assertFalse(rejected.candidateAccepted)
        self.assertIn("ml.current_candidate_probability_below_threshold", rejected.reasonCodes)
        self.assertEqual(accepted.finalSignal, Signal.SELL.value)
        self.assertTrue(accepted.candidateAccepted)
        self.assertEqual(accepted.recommendedRiskCap, 1.0)

    def test_active_mode_applies_only_bounded_risk_cap(self) -> None:
        prediction = apply_safe_ml_inference(
            deterministic_signal=Signal.BUY,
            feature_set=feature_set(),
            model_artifact=artifact({"BUY": 0.75, "SELL": 0.05, "HOLD": 0.20}),
            config=SafeMLInferenceConfig(
                mode=OperatingMode.ACTIVE,
                minSuccessProbability=0.55,
                activeMinRiskCap=0.20,
                activeMaxRiskCap=0.60,
            ),
            predicted_at=now(),
        )

        self.assertEqual(prediction.finalSignal, Signal.BUY.value)
        self.assertTrue(prediction.candidateAccepted)
        self.assertGreaterEqual(prediction.recommendedRiskCap, 0.20)
        self.assertLessEqual(prediction.recommendedRiskCap, 0.60)

    def test_ml_cannot_flip_side_or_create_trade_from_hold(self) -> None:
        buy_candidate = apply_safe_ml_inference(
            deterministic_signal=Signal.BUY,
            feature_set=feature_set(),
            model_artifact=artifact({"BUY": 0.10, "SELL": 0.90, "HOLD": 0.0}),
            config=SafeMLInferenceConfig(mode=OperatingMode.ACTIVE, minSuccessProbability=0.60),
            predicted_at=now(),
        )
        hold_candidate = apply_safe_ml_inference(
            deterministic_signal=Signal.HOLD,
            feature_set=feature_set(),
            model_artifact=artifact({"BUY": 0.90, "SELL": 0.05, "HOLD": 0.05}),
            config=SafeMLInferenceConfig(mode=OperatingMode.ACTIVE, minSuccessProbability=0.60),
            predicted_at=now(),
        )

        self.assertEqual(buy_candidate.finalSignal, Signal.HOLD.value)
        self.assertNotEqual(buy_candidate.finalSignal, Signal.SELL.value)
        self.assertEqual(hold_candidate.finalSignal, Signal.HOLD.value)
        self.assertIn("ml.cannot_create_trade_from_hold", hold_candidate.reasonCodes)

    def test_hard_gate_failure_cannot_be_bypassed(self) -> None:
        prediction = apply_safe_ml_inference(
            deterministic_signal=Signal.BUY,
            feature_set=feature_set(),
            model_artifact=artifact({"BUY": 0.95, "SELL": 0.01, "HOLD": 0.04}),
            config=SafeMLInferenceConfig(mode=OperatingMode.ACTIVE, minSuccessProbability=0.60),
            hard_gates_passed=False,
            predicted_at=now(),
        )

        self.assertFalse(prediction.candidateAccepted)
        self.assertEqual(prediction.finalSignal, Signal.HOLD.value)
        self.assertIn("ml.hard_gate_failed_no_bypass", prediction.reasonCodes)

    def test_schema_mismatch_falls_back_or_no_trades_by_configuration(self) -> None:
        fallback = apply_safe_ml_inference(
            deterministic_signal=Signal.BUY,
            feature_set=feature_set(schema_hash="new-schema"),
            model_artifact=artifact({"BUY": 0.95, "SELL": 0.01, "HOLD": 0.04}, schema_hash="old-schema"),
            config=SafeMLInferenceConfig(
                mode=OperatingMode.ACTIVE,
                fallbackBehavior="DETERMINISTIC_BASELINE",
                fallbackOnSchemaMismatch=True,
            ),
            predicted_at=now(),
        )
        no_trade = apply_safe_ml_inference(
            deterministic_signal=Signal.BUY,
            feature_set=feature_set(schema_hash="new-schema"),
            model_artifact=artifact({"BUY": 0.95, "SELL": 0.01, "HOLD": 0.04}, schema_hash="old-schema"),
            config=SafeMLInferenceConfig(
                mode=OperatingMode.ACTIVE,
                fallbackBehavior="NO_TRADE",
                fallbackOnSchemaMismatch=False,
            ),
            predicted_at=now(),
        )

        self.assertEqual(fallback.effectiveMode, OperatingMode.FALLBACK.value)
        self.assertEqual(fallback.finalSignal, Signal.BUY.value)
        self.assertIn("ml.feature_schema_mismatch", fallback.reasonCodes)
        self.assertEqual(no_trade.finalSignal, Signal.HOLD.value)
        self.assertFalse(no_trade.candidateAccepted)
        self.assertIn("ml.fallback_no_trade", no_trade.reasonCodes)

    def test_ood_triggers_explicit_fallback(self) -> None:
        prediction = apply_safe_ml_inference(
            deterministic_signal=Signal.BUY,
            feature_set=feature_set(),
            model_artifact=artifact({"BUY": 0.95, "SELL": 0.01, "HOLD": 0.04}, ood_score=0.95),
            config=SafeMLInferenceConfig(
                mode=OperatingMode.ACTIVE,
                fallbackBehavior="DETERMINISTIC_BASELINE",
                fallbackOnSchemaMismatch=True,
                maxOutOfDistributionScore=0.50,
            ),
            predicted_at=now(),
        )

        self.assertEqual(prediction.effectiveMode, OperatingMode.FALLBACK.value)
        self.assertEqual(prediction.finalSignal, Signal.BUY.value)
        self.assertIn("ml.out_of_distribution", prediction.reasonCodes)


def now() -> datetime:
    return datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def feature_set(*, schema_hash: str = SCHEMA_HASH, missing: bool = False) -> MLFeatureSet:
    missing_indicators = {"target_distance": False, "stop_distance": False, "expected_transaction_cost": missing}
    return MLFeatureSet(
        schemaHash=schema_hash,
        snapshotId="snapshot-safe-ml",
        symbol="SPY",
        decisionTimestampUtc=now().isoformat(),
        featureValues={
            "target_distance": 1.5,
            "stop_distance": 1.0,
            "expected_transaction_cost": 0.05,
        },
        missingIndicators=missing_indicators,
        forbiddenFieldsChecked=["finalOutcome", "fills", "metaModelPrediction"],
        explanation="Synthetic decision-time features for safe ML inference tests.",
    )


def artifact(probabilities: dict[str, float], *, schema_hash: str = SCHEMA_HASH, ood_score: float = 0.0) -> dict:
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
                "modelHealthScore": 1.0,
                "calibration": {"method": "none"},
            }
        },
    }


if __name__ == "__main__":
    unittest.main()
