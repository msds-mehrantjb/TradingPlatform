from __future__ import annotations

from typing import Any

from backend.app.domain.models import Signal


VOTING_ENSEMBLE_STOP_LOSS_POLICY_VERSION = "voting_ensemble_stop_loss_policy_v1"
VOTING_ENSEMBLE_DEFAULT_STOP_DISTANCE = 0.75
VOTING_ENSEMBLE_MINIMUM_STOP_DISTANCE = 0.01


def voting_ensemble_stop_distance(config: dict[str, Any] | None = None) -> float:
    config = config or {}
    try:
        requested = float(config.get("fixedStopDistanceDollars", VOTING_ENSEMBLE_DEFAULT_STOP_DISTANCE))
    except (TypeError, ValueError):
        requested = VOTING_ENSEMBLE_DEFAULT_STOP_DISTANCE
    return max(VOTING_ENSEMBLE_MINIMUM_STOP_DISTANCE, requested)


def initial_stop_price(
    *,
    side: Signal | str,
    entry_price: float,
    stop_distance: float | None = None,
    structural_invalidation_price: float | None = None,
) -> float:
    distance = max(VOTING_ENSEMBLE_MINIMUM_STOP_DISTANCE, float(stop_distance or VOTING_ENSEMBLE_DEFAULT_STOP_DISTANCE))
    normalized = side.value if isinstance(side, Signal) else str(side).upper()
    structural = _valid_structural_stop(normalized, entry_price, structural_invalidation_price)
    if normalized == Signal.BUY.value:
        fallback = entry_price - distance
        return round(min(fallback, structural) if structural is not None else fallback, 6)
    if normalized == Signal.SELL.value:
        fallback = entry_price + distance
        return round(max(fallback, structural) if structural is not None else fallback, 6)
    raise ValueError("initial stop requires BUY or SELL side")


def stop_loss_reason_codes(structural_invalidation_price: float | None = None) -> tuple[str, ...]:
    codes = [VOTING_ENSEMBLE_STOP_LOSS_POLICY_VERSION, "voting_ensemble.stop_loss.fixed_distance"]
    if structural_invalidation_price is not None:
        codes.append("voting_ensemble.stop_loss.structural_invalidation_considered")
    return tuple(codes)


def _valid_structural_stop(side: str, entry_price: float, value: float | None) -> float | None:
    if value is None:
        return None
    try:
        stop = float(value)
    except (TypeError, ValueError):
        return None
    if side == Signal.BUY.value and stop < entry_price:
        return stop
    if side == Signal.SELL.value and stop > entry_price:
        return stop
    return None
