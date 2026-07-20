"""Uncertainty calculations for Meta-Strategy inference."""

from __future__ import annotations


def probability_uncertainty(success_probability: float) -> float:
    value = max(0.0, min(1.0, float(success_probability)))
    return max(0.0, min(1.0, 1.0 - abs(value - 0.5) * 2.0))


__all__ = ["probability_uncertainty"]
