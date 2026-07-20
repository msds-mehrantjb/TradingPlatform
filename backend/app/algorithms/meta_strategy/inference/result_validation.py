"""Final invariant checks for Meta-Strategy inference results."""

from __future__ import annotations

from typing import Any


class MetaStrategyInferenceValidationError(ValueError):
    pass


def validate_inference_result(result: Any) -> Any:
    deterministic = str(result.deterministicSignal)
    final = str(result.finalSignal)
    if deterministic == "HOLD" and final != "HOLD":
        raise MetaStrategyInferenceValidationError("meta_strategy.inference.hold_cannot_become_trade")
    if deterministic == "BUY" and final == "SELL":
        raise MetaStrategyInferenceValidationError("meta_strategy.inference.buy_cannot_become_sell")
    if deterministic == "SELL" and final == "BUY":
        raise MetaStrategyInferenceValidationError("meta_strategy.inference.sell_cannot_become_buy")
    if not result.hardGatesPassed and result.candidateAccepted:
        raise MetaStrategyInferenceValidationError("meta_strategy.inference.ml_cannot_bypass_safety_gates")
    runtime_health = (getattr(result, "modelHealth", None) or {}).get("runtimeHealth")
    if runtime_health and not runtime_health.get("passed", True) and result.appliedToOrder:
        raise MetaStrategyInferenceValidationError("meta_strategy.inference.failed_runtime_health_cannot_apply_ml")
    if float(result.recommendedRiskMultiplier) > float(result.deterministicRiskMultiplier) + 1e-9:
        raise MetaStrategyInferenceValidationError("meta_strategy.inference.ml_cannot_increase_deterministic_risk")
    return result


__all__ = ["MetaStrategyInferenceValidationError", "validate_inference_result"]
