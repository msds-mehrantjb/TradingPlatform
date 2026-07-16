from __future__ import annotations

from backend.app.domain.models import Direction, DynamicPolicyBounds, Signal, TradeCandidate
from backend.app.trading_policy.models import ExitPlan


def build_exit_plan(
    candidate: TradeCandidate,
    *,
    target_r: float,
    holding_minutes: int,
    bounds: DynamicPolicyBounds,
    stop_price: float | None = None,
    target_price: float | None = None,
    protective_quantity: int = 0,
    bracket_oco_supported: bool = True,
) -> ExitPlan | None:
    resolved_stop = stop_price if stop_price is not None else candidate.stopPrice
    resolved_target = target_price if target_price is not None else candidate.targetPrice
    if resolved_stop is None or resolved_target is None:
        return None
    bounded_target_r = min(bounds.maximumTargetR, max(bounds.minimumTargetR, target_r))
    bounded_holding = min(bounds.maximumHoldingMinutes, max(bounds.minimumHoldingMinutes, holding_minutes))
    invalidation_price = _strategy_invalidation_price(candidate)
    return ExitPlan(
        initialProtectiveStop=resolved_stop,
        profitTarget=resolved_target,
        maximumHoldingMinutes=bounded_holding,
        strategyInvalidationPrice=invalidation_price,
        endOfDayExit=True,
        protectiveOrderQuantity=max(0, int(protective_quantity)),
        bracketOcoSupported=bracket_oco_supported,
        bracketOcoPlan=bracket_oco_supported,
        breakEvenStopEnabled=False,
        trailingStopEnabled=False,
        partialExitEnabled=False,
        pyramidingEnabled=False,
        timeStopReason="exit.time_stop_maximum_holding_minutes",
        invalidationExitReason="exit.strategy_invalidation",
        exitAssumptions=[
            "initial_protective_stop_submitted_for_actual_fill_quantity",
            "profit_target_submitted_as_bracket_or_oco_when_supported",
            "maximum_holding_time_matches_replay_time_stop",
            "end_of_day_exit_matches_replay_liquidation",
            "optional_break_even_trailing_partial_and_pyramiding_disabled",
        ],
        reasonCodes=["exit.initial_stop", "exit.profit_target", "exit.time_stop", "exit.strategy_invalidation", "exit.end_of_day"],
        stopPrice=resolved_stop,
        targetPrice=resolved_target,
        targetR=round(bounded_target_r, 4),
        holdingPeriodMinutes=bounded_holding,
        exitStyle="bracket_with_time_stop",
        explanation="Exit plan submits initial protective stop, profit target, time stop, strategy invalidation exit, and end-of-day exit; optional advanced exits are disabled.",
    )


def protective_quantity_for_fill(*, planned_quantity: int, filled_quantity: int) -> int:
    return max(0, min(int(planned_quantity), int(filled_quantity)))


def protective_stop_update(
    *,
    side: Signal,
    entry_price: float,
    current_stop: float,
    proposed_stop: float,
) -> tuple[float, list[str]]:
    if _would_widen_stop(side=side, entry_price=entry_price, current_stop=current_stop, proposed_stop=proposed_stop):
        return current_stop, ["exit.stop_widening_rejected"]
    return proposed_stop, ["exit.stop_maintains_or_reduces_risk"]


def _would_widen_stop(*, side: Signal, entry_price: float, current_stop: float, proposed_stop: float) -> bool:
    normalized = Signal(side)
    current_risk = abs(entry_price - current_stop)
    proposed_risk = abs(entry_price - proposed_stop)
    if normalized == Signal.BUY and proposed_stop < current_stop:
        return True
    if normalized == Signal.SELL and proposed_stop > current_stop:
        return True
    return proposed_risk > current_risk


def _strategy_invalidation_price(candidate: TradeCandidate) -> float | None:
    for key in ("strategyInvalidationPrice", "structuralInvalidationPrice", "sweepExtreme", "failedBreakoutExtreme"):
        value = candidate.features.get(key)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric > 0:
            return numeric
    return None
