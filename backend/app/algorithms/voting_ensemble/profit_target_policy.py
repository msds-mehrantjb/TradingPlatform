from __future__ import annotations

from typing import Any

from backend.app.domain.models import Signal


VOTING_ENSEMBLE_PROFIT_TARGET_POLICY_VERSION = "voting_ensemble_profit_target_policy_v1"
VOTING_ENSEMBLE_DEFAULT_TARGET_DISTANCE = 1.0
VOTING_ENSEMBLE_DEFAULT_TARGET_R = 1.5
VOTING_ENSEMBLE_MINIMUM_TARGET_DISTANCE = 0.01


def voting_ensemble_target_distance(config: dict[str, Any] | None = None) -> float:
    config = config or {}
    if "targetDistance" in config:
        return _positive_float(config.get("targetDistance"), VOTING_ENSEMBLE_DEFAULT_TARGET_DISTANCE)
    if "fixedTargetDistanceDollars" in config:
        return _positive_float(config.get("fixedTargetDistanceDollars"), VOTING_ENSEMBLE_DEFAULT_TARGET_DISTANCE)
    stop_distance = _optional_positive_float(config.get("fixedStopDistanceDollars") or config.get("stopDistance"))
    target_r = _positive_float(config.get("takeProfitR"), VOTING_ENSEMBLE_DEFAULT_TARGET_R)
    if stop_distance is not None:
        return max(VOTING_ENSEMBLE_MINIMUM_TARGET_DISTANCE, stop_distance * target_r)
    return VOTING_ENSEMBLE_DEFAULT_TARGET_DISTANCE


def initial_target_price(
    *,
    side: Signal | str,
    entry_price: float,
    target_distance: float | None = None,
) -> float:
    distance = max(VOTING_ENSEMBLE_MINIMUM_TARGET_DISTANCE, float(target_distance or VOTING_ENSEMBLE_DEFAULT_TARGET_DISTANCE))
    normalized = side.value if isinstance(side, Signal) else str(side).upper()
    if normalized == Signal.BUY.value:
        return round(entry_price + distance, 6)
    if normalized == Signal.SELL.value:
        return round(max(VOTING_ENSEMBLE_MINIMUM_TARGET_DISTANCE, entry_price - distance), 6)
    raise ValueError("initial target requires BUY or SELL side")


def profit_target_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_PROFIT_TARGET_POLICY_VERSION,
        "voting_ensemble.profit_target.fixed_distance",
    )


def _positive_float(value: Any, default: float) -> float:
    parsed = _optional_positive_float(value)
    return parsed if parsed is not None else default


def _optional_positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(VOTING_ENSEMBLE_MINIMUM_TARGET_DISTANCE, parsed) if parsed > 0 else None
