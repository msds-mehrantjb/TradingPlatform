"""Missingness helpers for Meta-Strategy feature vectors."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.meta_strategy.feature_schema import MISSING_CATEGORY


def normalize_feature_value(value: Any, value_type: str) -> tuple[Any, bool]:
    if value is None:
        return (0.0 if value_type == "numeric" else MISSING_CATEGORY), True
    if value_type == "categorical":
        text = str(value)
        return (text if text else MISSING_CATEGORY), not bool(text)
    if isinstance(value, bool):
        return int(value), False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0, True
    if number != number:
        return 0.0, True
    return round(number, 8), False


def missingness_ratio(missing_indicators: dict[str, bool]) -> float:
    if not missing_indicators:
        return 1.0
    return round(sum(1 for value in missing_indicators.values() if value) / len(missing_indicators), 8)


__all__ = ["missingness_ratio", "normalize_feature_value"]
