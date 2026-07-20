"""Training reports and validation packages for Meta-Strategy models."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.training import training_core


def v2_training_compatibility_report(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("v2_training_compatibility_report", *args, **kwargs)


def v2_training_incompatibility_reasons(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("v2_training_incompatibility_reasons", *args, **kwargs)


def build_meta_model_v2_validation_package(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("build_meta_model_v2_validation_package", *args, **kwargs)


def promotion_criteria_report_from_result(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("promotion_criteria_report_from_result", *args, **kwargs)


def config_report(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("config_report", *args, **kwargs)


def __getattr__(name: str) -> Any:
    return getattr(training_core, name)


def _legacy_call(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(training_core, name)(*args, **kwargs)


__all__ = [
    "build_meta_model_v2_validation_package",
    "config_report",
    "promotion_criteria_report_from_result",
    "v2_training_compatibility_report",
    "v2_training_incompatibility_reasons",
]
