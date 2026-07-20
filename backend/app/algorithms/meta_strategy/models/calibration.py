"""Calibration helpers for Meta-Strategy model probabilities."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.models.probability_contract import normalize_probabilities
from backend.app.algorithms.meta_strategy.training import training_core


def apply_meta_strategy_calibration(probabilities: dict[str, float], calibration: dict[str, Any] | None) -> dict[str, float]:
    if not calibration:
        return normalize_probabilities(probabilities)
    return normalize_probabilities(_legacy_call("apply_probability_calibration_model", probabilities, calibration))


def tune_meta_strategy_calibration_from_oof_rows(rows: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    return _legacy_call("tune_probability_calibration_from_probability_rows", rows, **kwargs)


def _legacy_call(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(training_core, name)(*args, **kwargs)


__all__ = [
    "apply_meta_strategy_calibration",
    "tune_meta_strategy_calibration_from_oof_rows",
]
