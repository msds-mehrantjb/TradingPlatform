"""Purging rules for Meta-Strategy walk-forward training."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.training import training_core


def chronological_purged_folds(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("chronological_purged_folds", *args, **kwargs)


def assert_fold_is_chronological_and_purged(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("assert_fold_is_chronological_and_purged", *args, **kwargs)


def _legacy_call(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(training_core, name)(*args, **kwargs)


__all__ = ["assert_fold_is_chronological_and_purged", "chronological_purged_folds"]
