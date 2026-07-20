"""Compatibility checks for Meta-Strategy model implementations."""

from __future__ import annotations

from backend.app.algorithms.meta_strategy.models.base import MetaStrategyModelBase


def assert_common_model_interface(models: list[MetaStrategyModelBase]) -> tuple[str, ...]:
    for model in models:
        if not callable(getattr(model, "fit", None)):
            raise TypeError(f"{model!r} does not implement fit")
        if not callable(getattr(model, "predict_probabilities", None)):
            raise TypeError(f"{model!r} does not implement predict_probabilities")
        if not callable(getattr(model, "predict_candidate", None)):
            raise TypeError(f"{model!r} does not implement predict_candidate")
    return tuple(model.model_id for model in models)


__all__ = ["assert_common_model_interface"]
