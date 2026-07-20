"""Chronological split helpers for Meta-Strategy training."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.training import training_core


def row_window(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("row_window", *args, **kwargs)


def iso_or_none(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("iso_or_none", *args, **kwargs)


def _legacy_call(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(training_core, name)(*args, **kwargs)


__all__ = ["iso_or_none", "row_window"]
