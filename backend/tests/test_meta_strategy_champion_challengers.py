from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.algorithms.meta_strategy.training.training_core import (
    load_meta_strategy_model_artifact_data,
    train_meta_strategy_baselines,
)
from backend.tests.test_meta_strategy_nested_training import labeled_row, patched_training_io


class MetaStrategyChampionChallengerTest(unittest.TestCase):
    def test_champion_challengers_and_optional_unavailable_are_reported(self) -> None:
        with patched_training_io([labeled_row(index) for index in range(180)]), patched_optional_boosters_unavailable():
            result = train_meta_strategy_baselines(
                decision_snapshot_dir=Path("unused"),
                symbol="SPY",
                minimum_total_candidates=120,
                minimum_buy_candidates=20,
                minimum_sell_candidates=20,
                minimum_positive_outcomes=40,
                minimum_negative_outcomes=20,
                minimum_candidates_per_outer_fold=12,
                minimum_trading_sessions=4,
                minimum_regimes_represented=2,
                minimum_calibration_rows=20,
                minimum_isotonic_rows=40,
                outer_folds=2,
                inner_folds=2,
                maximum_holding_horizon_minutes=10,
                embargo_minutes=10,
                random_seed=17,
            )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["championModel"], "logistic_regression_champion")
        self.assertIn("random_forest_challenger", result["challengerModels"])
        self.assertEqual(result["models"]["logistic_regression_champion"]["role"], "champion")
        self.assertTrue(result["models"]["logistic_regression_champion"]["available"])
        self.assertEqual(result["models"]["random_forest_challenger"]["role"], "challenger")
        self.assertTrue(result["models"]["random_forest_challenger"]["available"])
        self.assertIn("xgboost_challenger", result["unavailableChallengers"])
        self.assertIn("lightgbm_challenger", result["unavailableChallengers"])
        for model_name in ["logistic_regression_champion", "random_forest_challenger"]:
            model = result["models"][model_name]
            self.assertEqual(model["featureSchemaHash"], result["featureSchemaHash"])
            self.assertEqual(model["labelVersion"], "candidate_triple_barrier_v1")
            self.assertIn("trainingWindow", model)
            self.assertIn("hyperparameters", model)
            self.assertIn("calibrationMethod", model)
            self.assertIn("thresholds", model)
            self.assertIn("metricsByFold", model)
            self.assertIn("finalHoldoutMetrics", model)
            self.assertIn("modelHash", model)

    def test_training_hashes_are_reproducible_with_same_data_and_seed(self) -> None:
        rows = [labeled_row(index) for index in range(180)]
        with patched_training_io(rows), patched_optional_boosters_unavailable():
            first = train_meta_strategy_baselines(
                decision_snapshot_dir=Path("unused"),
                symbol="SPY",
                minimum_total_candidates=120,
                minimum_buy_candidates=20,
                minimum_sell_candidates=20,
                minimum_positive_outcomes=40,
                minimum_negative_outcomes=20,
                minimum_candidates_per_outer_fold=12,
                minimum_trading_sessions=4,
                minimum_regimes_represented=2,
                minimum_calibration_rows=20,
                minimum_isotonic_rows=40,
                outer_folds=2,
                inner_folds=2,
                maximum_holding_horizon_minutes=10,
                embargo_minutes=10,
                random_seed=17,
            )
        with patched_training_io(rows), patched_optional_boosters_unavailable():
            second = train_meta_strategy_baselines(
                decision_snapshot_dir=Path("unused"),
                symbol="SPY",
                minimum_total_candidates=120,
                minimum_buy_candidates=20,
                minimum_sell_candidates=20,
                minimum_positive_outcomes=40,
                minimum_negative_outcomes=20,
                minimum_candidates_per_outer_fold=12,
                minimum_trading_sessions=4,
                minimum_regimes_represented=2,
                minimum_calibration_rows=20,
                minimum_isotonic_rows=40,
                outer_folds=2,
                inner_folds=2,
                maximum_holding_horizon_minutes=10,
                embargo_minutes=10,
                random_seed=17,
            )

        self.assertEqual(first["featureSchemaHash"], second["featureSchemaHash"])
        self.assertEqual(
            first["models"]["logistic_regression_champion"]["modelHash"],
            second["models"]["logistic_regression_champion"]["modelHash"],
        )
        self.assertEqual(
            first["models"]["random_forest_challenger"]["modelHash"],
            second["models"]["random_forest_challenger"]["modelHash"],
        )

    def test_artifact_loader_rejects_wrong_feature_schema(self) -> None:
        with patched_training_io([labeled_row(index) for index in range(180)]), patched_optional_boosters_unavailable():
            result = train_meta_strategy_baselines(
                decision_snapshot_dir=Path("unused"),
                symbol="SPY",
                minimum_total_candidates=120,
                minimum_buy_candidates=20,
                minimum_sell_candidates=20,
                minimum_positive_outcomes=40,
                minimum_negative_outcomes=20,
                minimum_candidates_per_outer_fold=12,
                minimum_trading_sessions=4,
                minimum_regimes_represented=2,
                minimum_calibration_rows=20,
                minimum_isotonic_rows=40,
                outer_folds=2,
                inner_folds=2,
                maximum_holding_horizon_minutes=10,
                embargo_minutes=10,
            )

        loaded = load_meta_strategy_model_artifact_data(result, expected_feature_schema_hash=result["featureSchemaHash"])
        self.assertEqual(loaded["featureSchemaHash"], result["featureSchemaHash"])
        with self.assertRaisesRegex(ValueError, "feature schema mismatch"):
            load_meta_strategy_model_artifact_data(result, expected_feature_schema_hash="wrong-schema")


class patched_optional_boosters_unavailable:
    def __init__(self) -> None:
        self.patches = [
            patch(
                "backend.app.algorithms.meta_strategy.training.training_core.train_xgboost_booster",
                return_value={"available": False, "reason": "xgboost import failed: not installed"},
            ),
            patch(
                "backend.app.algorithms.meta_strategy.training.training_core.train_lightgbm_booster",
                return_value={"available": False, "reason": "lightgbm import failed: not installed"},
            ),
        ]

    def __enter__(self):
        for item in self.patches:
            item.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        for item in reversed(self.patches):
            item.__exit__(exc_type, exc, tb)
        return False


if __name__ == "__main__":
    unittest.main()
