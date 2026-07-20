"""Baseline reconstruction and comparison helpers for Meta-Strategy training."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.training import training_core


def deterministic_baseline_prediction(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("deterministic_baseline_prediction", *args, **kwargs)


def reconstructed_baseline_predictions(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("reconstructed_baseline_predictions", *args, **kwargs)


def baseline_prediction(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("baseline_prediction", *args, **kwargs)


def family_score_map(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("family_score_map", *args, **kwargs)


def __getattr__(name: str) -> Any:
    return getattr(training_core, name)


def _legacy_call(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(training_core, name)(*args, **kwargs)


__all__ = [
    "baseline_prediction",
    "deterministic_baseline_prediction",
    "family_score_map",
    "reconstructed_baseline_predictions",
]
