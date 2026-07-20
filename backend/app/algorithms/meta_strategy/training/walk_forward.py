"""Nested chronological walk-forward training orchestration."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.training import training_core


def build_nested_walk_forward_plan(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("build_nested_walk_forward_plan", *args, **kwargs)


def build_validated_chronological_walk_forward_plan(examples: list[dict[str, Any]], config: Any) -> dict[str, Any]:
    from backend.app.algorithms.meta_strategy.training.chronological_validation import build_chronological_validation_report

    plan = build_nested_walk_forward_plan(examples, config)
    validation = build_chronological_validation_report(examples, plan, config)
    return {**plan, "chronologicalValidation": validation.__dict__}


def run_outer_walk_forward_fold(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("run_outer_walk_forward_fold", *args, **kwargs)


def fold_result_base(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("fold_result_base", *args, **kwargs)


def fold_report(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("fold_report", *args, **kwargs)


def collect_model_metrics_by_fold(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("collect_model_metrics_by_fold", *args, **kwargs)


def summarize_outer_results(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("summarize_outer_results", *args, **kwargs)


def _legacy_call(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(training_core, name)(*args, **kwargs)


__all__ = [
    "build_nested_walk_forward_plan",
    "build_validated_chronological_walk_forward_plan",
    "collect_model_metrics_by_fold",
    "fold_report",
    "fold_result_base",
    "run_outer_walk_forward_fold",
    "summarize_outer_results",
]
