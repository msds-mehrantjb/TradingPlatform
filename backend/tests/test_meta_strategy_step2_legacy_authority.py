from __future__ import annotations

import importlib
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

DELETED_LEGACY_PATHS = (
    "backend/app/meta_strategy_training.py",
    "backend/app/ml/features.py",
    "backend/app/ml/meta_labeling.py",
    "backend/app/ml/forecast_oos.py",
    "backend/app/ml/inference.py",
)

PACKAGE_AUTHORITIES: dict[str, dict[str, Any]] = {
    "backend.app.algorithms.meta_strategy.training.training_core": {
        "owns": "model_training_validation_artifacts_calibration_and_promotion",
        "requiredSymbols": (
            "MetaTrainingConfig",
            "train_meta_strategy_baselines",
            "train_and_validate_meta_model_v2",
            "v2_training_compatibility_report",
            "build_meta_model_v2_validation_package",
            "load_meta_strategy_model_artifact_data",
            "predict_softmax_logistic_probabilities",
            "apply_probability_calibration_model",
            "evaluate_economic_promotion",
            "meta_strategy_artifact_path",
        ),
    },
    "backend.app.algorithms.meta_strategy.ml_features": {
        "owns": "candidate_meta_feature_generation_and_schema_hash",
        "requiredSymbols": (
            "MLFeatureSet",
            "build_candidate_meta_features",
            "candidate_meta_feature_schema",
            "candidate_meta_feature_schema_hash",
            "reject_forbidden_training_fields",
        ),
    },
    "backend.app.algorithms.meta_strategy.labeling.candidate_meta_labeling": {
        "owns": "candidate_triple_barrier_label_generation",
        "requiredSymbols": ("MetaLabelExecutionConfig", "create_candidate_meta_label"),
    },
    "backend.app.algorithms.meta_strategy.forecast.oos_features": {
        "owns": "out_of_sample_forecast_feature_generation",
        "requiredSymbols": (
            "ForecastFallbackFeature",
            "OutOfSampleForecastFeature",
            "generate_oos_forecast_features",
            "validate_oos_fold",
            "reject_full_history_forecast_artifact_for_historical_features",
            "select_live_forecast_feature",
        ),
    },
    "backend.app.algorithms.meta_strategy.inference.safe_inference": {
        "owns": "safe_meta_model_inference_policy",
        "requiredSymbols": (
            "SafeMLInferenceConfig",
            "SafeMLInferenceResult",
            "apply_safe_ml_inference",
            "candidate_success_probability",
            "expected_value_after_costs",
            "bounded_active_risk_cap",
            "out_of_distribution_score",
            "missingness_ratio",
        ),
    },
}

SHARED_BACKTESTING_AUTHORITIES: dict[str, dict[str, Any]] = {
    "backend.app.algorithms.meta_strategy.backtest": {
        "owns": "dedicated_meta_strategy_backtesting_runtime_parity_and_reports",
        "requiredSymbols": (
            "run_meta_strategy_backtest",
            "build_backtest_comparison_report",
            "build_walk_forward_comparison_report",
            "assert_backtest_runtime_parity",
        ),
    },
}


class MetaStrategyStep2LegacyAuthorityTest(unittest.TestCase):
    maxDiff = None

    def test_deleted_legacy_authoritative_modules_are_absent(self) -> None:
        missing = [path for path in DELETED_LEGACY_PATHS if (ROOT / path).exists()]

        self.assertEqual(missing, [])

    def test_every_current_public_ml_training_symbol_has_identified_package_source_module(self) -> None:
        symbol_authority: dict[str, str] = {}
        for module_name, spec in PACKAGE_AUTHORITIES.items():
            module = importlib.import_module(module_name)
            for symbol in spec["requiredSymbols"]:
                with self.subTest(module=module_name, symbol=symbol):
                    self.assertTrue(hasattr(module, symbol), f"{module_name}.{symbol} missing")
                    symbol_authority[f"{module_name}.{symbol}"] = module_name

        self.assertEqual(
            symbol_authority[
                "backend.app.algorithms.meta_strategy.ml_features.build_candidate_meta_features"
            ],
            "backend.app.algorithms.meta_strategy.ml_features",
        )
        self.assertEqual(
            symbol_authority[
                "backend.app.algorithms.meta_strategy.inference.safe_inference.apply_safe_ml_inference"
            ],
            "backend.app.algorithms.meta_strategy.inference.safe_inference",
        )
        self.assertEqual(
            symbol_authority[
                "backend.app.algorithms.meta_strategy.training.training_core.train_and_validate_meta_model_v2"
            ],
            "backend.app.algorithms.meta_strategy.training.training_core",
        )

    def test_no_current_meta_strategy_capability_is_omitted_from_migration_map(self) -> None:
        covered = {spec["owns"] for spec in PACKAGE_AUTHORITIES.values()}
        expected = {
            "model_training_validation_artifacts_calibration_and_promotion",
            "candidate_meta_feature_generation_and_schema_hash",
            "candidate_triple_barrier_label_generation",
            "out_of_sample_forecast_feature_generation",
            "safe_meta_model_inference_policy",
        }

        self.assertEqual(covered, expected)

    def test_shared_backtesting_meta_strategy_hooks_are_all_mapped_to_dedicated_package(self) -> None:
        for module_name, spec in SHARED_BACKTESTING_AUTHORITIES.items():
            module = importlib.import_module(module_name)
            for symbol in spec["requiredSymbols"]:
                with self.subTest(module=module_name, symbol=symbol):
                    self.assertTrue(hasattr(module, symbol), f"{module_name}.{symbol} missing")

    def test_api_v2_meta_strategy_routes_are_all_mapped_to_dedicated_router(self) -> None:
        module = importlib.import_module("backend.app.algorithms.meta_strategy.api")
        route_paths = {getattr(route, "path", "") for route in module.router.routes}

        self.assertIn("/api/meta-strategy/status", route_paths)
        self.assertIn("/api/meta-strategy/training/run", route_paths)
        self.assertIn("/api/meta-strategy/backtests/run", route_paths)


if __name__ == "__main__":
    unittest.main()
