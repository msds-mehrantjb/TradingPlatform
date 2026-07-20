"""Dataset validation for Meta-Strategy labeling rows."""

from __future__ import annotations

from typing import Any, Mapping

from backend.app.algorithms.meta_strategy.feature_builder import MetaStrategyFeatureSet
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID


class MetaStrategyDatasetValidationError(ValueError):
    pass


FORBIDDEN_FEATURE_ROW_TOKENS = (
    "future",
    "label",
    "outcome",
    "fill",
    "pnl",
    "barrier",
    "exit",
)


def validate_feature_row_for_labeling(feature_row: MetaStrategyFeatureSet | Mapping[str, Any]) -> tuple[str, ...]:
    if isinstance(feature_row, MetaStrategyFeatureSet):
        algorithm_id = ALGORITHM_ID
        row = feature_row.featureValues
    else:
        algorithm_id = str(feature_row.get("algorithmId", ALGORITHM_ID))
        row = feature_row
    if algorithm_id != ALGORITHM_ID:
        raise MetaStrategyDatasetValidationError(f"Meta-Strategy dataset rejects row from algorithm {algorithm_id!r}")
    violations = tuple(sorted(_forbidden_paths(row, "featureRow")))
    if violations:
        raise MetaStrategyDatasetValidationError(f"Meta-Strategy dataset feature row contains future/label fields: {', '.join(violations)}")
    return ("meta_strategy.dataset.feature_row_point_in_time",)


def validate_label_end_timestamp(label_end_timestamp: Any) -> None:
    if label_end_timestamp is None:
        raise MetaStrategyDatasetValidationError("label_end_timestamp is mandatory")


def _forbidden_paths(value: Any, path: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            normalized = str(key).replace("-", "_").lower()
            if any(token in normalized for token in FORBIDDEN_FEATURE_ROW_TOKENS):
                found.append(child)
            found.extend(_forbidden_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_forbidden_paths(item, f"{path}[{index}]"))
    return found


__all__ = [
    "FORBIDDEN_FEATURE_ROW_TOKENS",
    "MetaStrategyDatasetValidationError",
    "validate_feature_row_for_labeling",
    "validate_label_end_timestamp",
]
