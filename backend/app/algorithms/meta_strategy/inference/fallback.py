"""Fallback policies for Meta-Strategy inference."""

from __future__ import annotations

from typing import Literal


FallbackBehavior = Literal["DETERMINISTIC_BASELINE", "NO_TRADE"]


def fallback_signal(deterministic_signal: str, *, hard_gates_passed: bool, candidate_eligible: bool, behavior: FallbackBehavior) -> str:
    if behavior == "NO_TRADE" or not hard_gates_passed or not candidate_eligible:
        return "HOLD"
    return deterministic_signal if deterministic_signal in {"BUY", "SELL"} else "HOLD"


def fallback_risk(deterministic_risk_multiplier: float, *, behavior: FallbackBehavior, final_signal: str) -> float:
    if behavior == "NO_TRADE" or final_signal == "HOLD":
        return 0.0
    return max(0.0, min(1.0, float(deterministic_risk_multiplier)))


__all__ = ["FallbackBehavior", "fallback_risk", "fallback_signal"]
