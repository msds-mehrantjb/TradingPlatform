from __future__ import annotations

import importlib
import unittest
from unittest.mock import patch

from backend.app.algorithms.meta_strategy.models import (
    LightGBMChallenger,
    LogisticRegressionChampion,
    RandomForestChallenger,
    XGBoostChallenger,
    assert_common_model_interface,
    load_meta_strategy_model_artifact_data,
    model_artifact_payload,
    train_optional_challenger_models,
)
from backend.tests.test_meta_strategy_nested_training import examples


def training_rows(count: int = 90) -> list[dict]:
    return examples(count)


FEATURE_NAMES = [
    "trend_buy_score",
    "trend_sell_score",
    "breakout_buy_score",
    "breakout_sell_score",
    "relativeVolume",
]


class MetaStrategyStep20ModelsTest(unittest.TestCase):
    def test_all_models_implement_common_interface(self) -> None:
        models = [
            LogisticRegressionChampion(),
            RandomForestChallenger(tree_count=5, max_depth=3),
            XGBoostChallenger(),
            LightGBMChallenger(),
        ]

        self.assertEqual(
            assert_common_model_interface(models),
            ("logistic_regression_champion", "random_forest_challenger", "xgboost_challenger", "lightgbm_challenger"),
        )

    def test_logistic_champion_outputs_calibrated_candidate_conditional_probability(self) -> None:
        model = LogisticRegressionChampion(
            calibration={
                "method": "identity",
                "source": "inner_out_of_fold",
                "probabilitySizingApproved": True,
            }
        ).fit(training_rows(), FEATURE_NAMES)

        result = model.predict_candidate(training_rows()[0]["features"], candidate_side="BUY")

        self.assertEqual(model.role, "champion")
        self.assertEqual(result.modelId, "logistic_regression_champion")
        self.assertAlmostEqual(sum(result.calibratedProbabilities.values()), 1.0, places=6)
        self.assertEqual(result.candidateSuccessProbability, result.calibratedProbabilities["BUY"])
        self.assertIn(result.predictedLabel, {"BUY", "SELL", "HOLD"})
        self.assertIn("meta_strategy.model.candidate_conditional_probability", result.reasonCodes)

    def test_random_forest_challenger_outputs_candidate_conditional_probability(self) -> None:
        model = RandomForestChallenger(tree_count=5, max_depth=3).fit(training_rows(), FEATURE_NAMES)

        result = model.predict_candidate(training_rows()[1]["features"], candidate_side="SELL")

        self.assertEqual(model.role, "challenger")
        self.assertEqual(result.modelId, "random_forest_challenger")
        self.assertAlmostEqual(sum(result.calibratedProbabilities.values()), 1.0, places=6)
        self.assertEqual(result.candidateSuccessProbability, result.calibratedProbabilities["SELL"])

    def test_missing_optional_dependencies_do_not_break_algorithm(self) -> None:
        real_import = importlib.import_module

        def fake_import(name: str, package: str | None = None):
            if name in {"xgboost", "lightgbm"}:
                raise ImportError("not installed for test")
            return real_import(name, package)

        with patch("importlib.import_module", side_effect=fake_import):
            challengers = train_optional_challenger_models(training_rows(30), FEATURE_NAMES)

        self.assertFalse(challengers["xgboost_challenger"].available)
        self.assertFalse(challengers["lightgbm_challenger"].available)
        self.assertIn("import failed", challengers["xgboost_challenger"].fitted_payload["reason"])
        self.assertIn("import failed", challengers["lightgbm_challenger"].fitted_payload["reason"])

    def test_artifact_payload_and_loader_validate_schema_and_model_hashes(self) -> None:
        feature_schema_hash = "feature-schema-a"
        model = LogisticRegressionChampion().fit(training_rows(), FEATURE_NAMES)
        model_payload = model_artifact_payload(
            model,
            feature_schema_hash=feature_schema_hash,
            label_version="candidate_triple_barrier_v1",
            training_window={"start": "2026-01-05T14:30:00+00:00", "end": "2026-01-05T15:30:00+00:00"},
        )
        artifact = {"featureSchemaHash": feature_schema_hash, "models": {"logistic_regression_champion": model_payload}}

        loaded = load_meta_strategy_model_artifact_data(artifact, expected_feature_schema_hash=feature_schema_hash)

        self.assertEqual(loaded["featureSchemaHash"], feature_schema_hash)
        self.assertEqual(model_payload["modelId"], "logistic_regression_champion")
        self.assertIn("modelHash", model_payload)
        with self.assertRaisesRegex(ValueError, "feature schema mismatch"):
            load_meta_strategy_model_artifact_data(artifact, expected_feature_schema_hash="wrong-schema")


if __name__ == "__main__":
    unittest.main()
