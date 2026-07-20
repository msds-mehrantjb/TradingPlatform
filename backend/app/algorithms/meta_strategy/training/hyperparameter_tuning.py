"""Hyperparameter and model training helpers for Meta-Strategy training."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.training import training_core


def select_hyperparameters_from_inner_folds(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("select_hyperparameters_from_inner_folds", *args, **kwargs)


def select_random_forest_hyperparameters_from_inner_folds(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("select_random_forest_hyperparameters_from_inner_folds", *args, **kwargs)


def select_consensus_hyperparameters(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("select_consensus_hyperparameters", *args, **kwargs)


def train_optional_challengers(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("train_optional_challengers", *args, **kwargs)


def train_softmax_logistic(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("train_softmax_logistic", *args, **kwargs)


def train_random_forest(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("train_random_forest", *args, **kwargs)


def __getattr__(name: str) -> Any:
    return getattr(training_core, name)


def _legacy_call(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(training_core, name)(*args, **kwargs)


__all__ = [
    "select_consensus_hyperparameters",
    "select_hyperparameters_from_inner_folds",
    "select_random_forest_hyperparameters_from_inner_folds",
    "train_optional_challengers",
    "train_random_forest",
    "train_softmax_logistic",
]
