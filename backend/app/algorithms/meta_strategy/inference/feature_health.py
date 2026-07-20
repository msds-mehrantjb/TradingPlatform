"""Feature health checks for Meta-Strategy inference."""

from __future__ import annotations

from typing import Any


def feature_missingness_ratio(feature_set: Any) -> float:
    indicators = getattr(feature_set, "missingIndicators", None) or getattr(feature_set, "missing_indicators", None) or {}
    if not indicators:
        return 0.0
    return _bounded(sum(1 for value in indicators.values() if value) / len(indicators))


def out_of_distribution_score(feature_set: Any, model: dict[str, Any] | None = None) -> float:
    explicit = (model or {}).get("outOfDistributionScore")
    if explicit is not None:
        return _bounded(float(explicit))
    values = getattr(feature_set, "featureValues", None) or getattr(feature_set, "feature_values", None) or {}
    numeric = []
    for value in values.values():
        try:
            numeric.append(float(value))
        except (TypeError, ValueError):
            continue
    if not numeric:
        return 1.0
    large = sum(1 for value in numeric if abs(value) > 5.0)
    return _bounded((large / len(numeric)) + (feature_missingness_ratio(feature_set) * 0.5))


def feature_schema_hash(feature_set: Any) -> str:
    return str(getattr(feature_set, "schemaHash", None) or getattr(feature_set, "schema_hash", None) or "")


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


__all__ = ["feature_missingness_ratio", "feature_schema_hash", "out_of_distribution_score"]
