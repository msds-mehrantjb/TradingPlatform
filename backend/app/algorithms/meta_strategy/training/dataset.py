"""Dataset loading and row normalization for Meta-Strategy training."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.training import training_core


def load_labeled_rows(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("load_labeled_rows", *args, **kwargs)


def training_example(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("training_example", *args, **kwargs)


def example_timestamp(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("example_timestamp", *args, **kwargs)


def example_label_start(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("example_label_start", *args, **kwargs)


def example_label_end(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("example_label_end", *args, **kwargs)


def example_regime(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("example_regime", *args, **kwargs)


def binary_outcome(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("binary_outcome", *args, **kwargs)


def parse_datetime_utc(*args: Any, **kwargs: Any) -> Any:
    return _legacy_call("parse_datetime_utc", *args, **kwargs)


def __getattr__(name: str) -> Any:
    return getattr(training_core, name)


def _legacy_call(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(training_core, name)(*args, **kwargs)


__all__ = [
    "binary_outcome",
    "example_label_end",
    "example_label_start",
    "example_regime",
    "example_timestamp",
    "load_labeled_rows",
    "parse_datetime_utc",
    "training_example",
]
