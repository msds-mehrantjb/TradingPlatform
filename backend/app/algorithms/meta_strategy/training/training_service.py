"""Training service facade and migration ownership map."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from backend.app.algorithms.meta_strategy.training import training_core


TRAINING_PACKAGE = "backend.app.algorithms.meta_strategy.training"
TRAINING_CORE_MODULE = "backend.app.algorithms.meta_strategy.training.training_core"
TRAINING_CORE_FILE = Path(training_core.__file__).resolve()


OWNER_MODULES = (
    "configuration",
    "dataset",
    "chronological_split",
    "purging",
    "embargo",
    "walk_forward",
    "hyperparameter_tuning",
    "calibration_training",
    "baseline_comparison",
    "economic_evaluation",
    "trainer",
    "training_report",
    "training_service",
)


def save_latest_training_status(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("save_latest_training_status", *args, **kwargs)


def meta_strategy_artifact_path(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("meta_strategy_artifact_path", *args, **kwargs)


def load_meta_strategy_model_artifact(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("load_meta_strategy_model_artifact", *args, **kwargs)


def load_meta_strategy_model_artifact_data(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("load_meta_strategy_model_artifact_data", *args, **kwargs)


def training_symbol_ownership() -> dict[str, str]:
    return {name: owner_for_training_symbol(name) for name in legacy_training_symbols()}


def legacy_training_symbols() -> tuple[str, ...]:
    tree = ast.parse(TRAINING_CORE_FILE.read_text(encoding="utf-8"))
    return tuple(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    )


def owner_for_training_symbol(name: str) -> str:
    if name == "MetaTrainingConfig" or name.endswith("_report") or name in {"config_report", "promotion_criteria_report"}:
        return "configuration" if name == "MetaTrainingConfig" else "training_report"
    if name in {"train_meta_strategy_baselines", "train_and_validate_meta_model_v2", "validate_meta_training_requirements"}:
        return "trainer"
    if name in {
        "load_labeled_rows",
        "training_example",
        "example_timestamp",
        "example_label_start",
        "example_label_end",
        "example_regime",
        "binary_outcome",
        "parse_datetime_utc",
        "add_numeric_features",
        "normalize_label",
        "signal_to_label",
        "signal_value",
        "first_number",
        "number",
        "parse_optional_number",
        "feature_matrix",
        "feature_vector",
        "scaled_features",
        "feature_scaler",
    }:
        return "dataset"
    if name in {"row_window", "iso_or_none"}:
        return "chronological_split"
    if "purged" in name or name == "assert_fold_is_chronological_and_purged":
        return "purging"
    if "embargo" in name:
        return "embargo"
    if "walk_forward" in name or name in {"run_outer_walk_forward_fold", "fold_result_base", "fold_report", "summarize_outer_results", "collect_model_metrics_by_fold"}:
        return "walk_forward"
    if "calibration" in name or name in {"clamp_probability", "logit", "sigmoid", "probability_label", "normalize_probability_distribution"}:
        return "calibration_training"
    if "economic" in name or "drawdown" in name or "pnl" in name or "performance" in name or "expectancy" in name or "promotion" in name:
        return "economic_evaluation"
    if "baseline" in name or name.startswith("reconstructed_") or "family" in name or name in {"winner_from_scores", "signed_score_to_label"}:
        return "baseline_comparison"
    if "hyperparameter" in name or name.startswith("train_") or name.startswith("predict_") or name in {"best_split", "build_tree", "gini", "dot", "softmax", "safe_name", "evaluate_predictions", "model_artifact_with_hash", "stable_json_hash"}:
        return "hyperparameter_tuning"
    if "validation_package" in name or "compatibility" in name or "breakdown" in name or "schema_hash" in name:
        return "training_report"
    if name in {"save_latest_training_status", "meta_strategy_artifact_path", "load_meta_strategy_model_artifact", "load_meta_strategy_model_artifact_data"}:
        return "training_service"
    return "trainer"


def assert_no_unowned_training_symbols() -> dict[str, str]:
    ownership = training_symbol_ownership()
    missing = [name for name, owner in ownership.items() if owner not in OWNER_MODULES]
    if missing:
        raise AssertionError(f"Unowned Meta-Strategy training symbols: {missing}")
    return ownership


def _legacy_call(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(training_core, name)(*args, **kwargs)


__all__ = [
    "OWNER_MODULES",
    "TRAINING_PACKAGE",
    "TRAINING_CORE_FILE",
    "TRAINING_CORE_MODULE",
    "assert_no_unowned_training_symbols",
    "legacy_training_symbols",
    "load_meta_strategy_model_artifact",
    "load_meta_strategy_model_artifact_data",
    "meta_strategy_artifact_path",
    "owner_for_training_symbol",
    "save_latest_training_status",
    "training_symbol_ownership",
]
