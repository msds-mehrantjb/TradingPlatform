"""Dedicated Meta-Strategy training package."""

from backend.app.algorithms.meta_strategy.training.baseline_comparison import (
    baseline_prediction,
    deterministic_baseline_prediction,
    reconstructed_baseline_predictions,
)
from backend.app.algorithms.meta_strategy.training.calibration_training import (
    apply_probability_calibration_model,
    evaluate_calibration_report,
    tune_probability_calibration,
    tune_probability_calibration_from_probability_rows,
)
from backend.app.algorithms.meta_strategy.training.configuration import (
    DEFAULT_META_LABEL_VERSION,
    DEFAULT_RANDOM_SEED,
    META_MODEL_V2_TRAINING_REPORT_VERSION,
    META_STRATEGY_FEATURE_SCHEMA_VERSION,
    MetaTrainingConfig,
)
from backend.app.algorithms.meta_strategy.training.chronological_validation import (
    ChronologicalValidationError,
    ChronologicalValidationReport,
    build_chronological_validation_report,
    prohibit_random_split_policy,
    validate_chronological_examples,
    validate_chronological_training_plan,
)
from backend.app.algorithms.meta_strategy.training.dataset import load_labeled_rows, training_example
from backend.app.algorithms.meta_strategy.training.economic_evaluation import (
    economic_performance,
    evaluate_economic_promotion_report,
    evaluate_model_economics,
    evaluate_economic_promotion,
)
from backend.app.algorithms.meta_strategy.training.trainer import (
    train_and_validate_meta_model_v2,
    train_meta_strategy_baselines,
)
from backend.app.algorithms.meta_strategy.training.training_report import (
    build_meta_model_v2_validation_package,
    v2_training_compatibility_report,
)
from backend.app.algorithms.meta_strategy.training.training_service import (
    assert_no_unowned_training_symbols,
    owner_for_training_symbol,
    training_symbol_ownership,
)
from backend.app.algorithms.meta_strategy.training.walk_forward import (
    build_nested_walk_forward_plan,
    build_validated_chronological_walk_forward_plan,
)

__all__ = [
    "DEFAULT_META_LABEL_VERSION",
    "DEFAULT_RANDOM_SEED",
    "ChronologicalValidationError",
    "ChronologicalValidationReport",
    "META_MODEL_V2_TRAINING_REPORT_VERSION",
    "META_STRATEGY_FEATURE_SCHEMA_VERSION",
    "MetaTrainingConfig",
    "apply_probability_calibration_model",
    "assert_no_unowned_training_symbols",
    "baseline_prediction",
    "build_meta_model_v2_validation_package",
    "build_chronological_validation_report",
    "build_nested_walk_forward_plan",
    "build_validated_chronological_walk_forward_plan",
    "deterministic_baseline_prediction",
    "economic_performance",
    "evaluate_calibration_report",
    "evaluate_economic_promotion_report",
    "evaluate_model_economics",
    "evaluate_economic_promotion",
    "load_labeled_rows",
    "owner_for_training_symbol",
    "prohibit_random_split_policy",
    "reconstructed_baseline_predictions",
    "train_and_validate_meta_model_v2",
    "train_meta_strategy_baselines",
    "training_example",
    "training_symbol_ownership",
    "tune_probability_calibration",
    "tune_probability_calibration_from_probability_rows",
    "v2_training_compatibility_report",
    "validate_chronological_examples",
    "validate_chronological_training_plan",
]
