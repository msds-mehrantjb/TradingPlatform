"""Out-of-distribution scoring for Meta-Strategy feature vectors."""

from __future__ import annotations

from typing import Any


def meta_strategy_out_of_distribution_score(feature_values: dict[str, Any], reference_ranges: dict[str, tuple[float, float]] | None = None) -> float:
    ranges = reference_ranges or DEFAULT_REFERENCE_RANGES
    if not ranges:
        return 0.0
    checked = 0
    violations = 0
    for name, (minimum, maximum) in ranges.items():
        value = feature_values.get(name)
        if not isinstance(value, (int, float)):
            continue
        checked += 1
        if float(value) < minimum or float(value) > maximum:
            violations += 1
    return round(violations / checked, 8) if checked else 0.0


DEFAULT_REFERENCE_RANGES = {
    "deterministic_score": (-1.0, 1.0),
    "signal_margin": (0.0, 1.0),
    "reward_risk_ratio": (0.0, 10.0),
    "expected_transaction_cost": (0.0, 5.0),
    "relative_volume": (0.0, 20.0),
    "spread_dollars": (0.0, 5.0),
}


__all__ = ["DEFAULT_REFERENCE_RANGES", "meta_strategy_out_of_distribution_score"]
