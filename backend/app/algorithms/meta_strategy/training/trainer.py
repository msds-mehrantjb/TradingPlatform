"""Meta-Strategy trainer facade."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.training import training_core


def train_meta_strategy_baselines(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("train_meta_strategy_baselines", *args, **kwargs)


def train_and_validate_meta_model_v2(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("train_and_validate_meta_model_v2", *args, **kwargs)


def validate_meta_training_requirements(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("validate_meta_training_requirements", *args, **kwargs)


def _legacy_call(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(training_core, name)(*args, **kwargs)


__all__ = [
    "train_and_validate_meta_model_v2",
    "train_meta_strategy_baselines",
    "validate_meta_training_requirements",
]
