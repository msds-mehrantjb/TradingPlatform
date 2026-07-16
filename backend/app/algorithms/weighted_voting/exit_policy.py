"""Exit policy checks for Weighted Voting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from backend.app.algorithms.weighted_voting.models import (
    WeightedDecision,
    WeightedEffectiveSettings,
    WeightedExitReason,
    WeightedMarketQuality,
    WeightedSide,
)


WEIGHTED_VOTING_EXIT_POLICY_VERSION = "weighted_voting_exit_policy_v2"


class WeightedExitAction(str, Enum):
    HOLD = "hold"
    EXIT = "exit"


@dataclass(frozen=True)
class WeightedVotingExitLifecycleState:
    trade_id: str
    symbol: str
    side: WeightedSide | str
    original_quantity: int
    remaining_quantity: int
    entry_price: float
    entry_timestamp: datetime
    protective_stop: float
    profit_target: float
    original_risk_per_share: float
    original_effective_settings: WeightedEffectiveSettings
    highest_price: float | None = None
    lowest_price: float | None = None
    break_even_active: bool = False
    deterioration_count: int = 0
    weighted_allocation_id: str = "weighted_voting"


@dataclass(frozen=True)
class WeightedVotingExitInputs:
    lifecycle: WeightedVotingExitLifecycleState
    current_price: float
    current_timestamp: datetime
    current_condition_quality: WeightedMarketQuality | str
    current_weighted_decision: WeightedDecision | None = None
    local_risk_exit: bool = False
    global_emergency_exit: bool = False
    end_of_session: bool = False
    new_entries_blocked: bool = False
    weighted_edge_threshold: float = 0.02
    deterioration_required_count: int = 2
    weighted_allocation_id: str = "weighted_voting"


@dataclass(frozen=True)
class WeightedVotingExitDecision:
    action: WeightedExitAction | str
    exit_reason: WeightedExitReason | str
    exit_quantity: int
    stop_price: float
    target_price: float
    risk_per_share: float
    updated_lifecycle: WeightedVotingExitLifecycleState
    reason_codes: tuple[str, ...]
    explanation: str


def open_exit_lifecycle(
    *,
    trade_id: str,
    symbol: str,
    side: WeightedSide | str,
    quantity: int,
    entry_price: float,
    entry_timestamp: datetime,
    stop_price: float,
    effective_settings: WeightedEffectiveSettings,
    weighted_allocation_id: str = "weighted_voting",
) -> WeightedVotingExitLifecycleState:
    if quantity <= 0:
        raise ValueError("Weighted Voting exit lifecycle requires positive entry quantity")
    risk = _risk_per_share(side, entry_price, stop_price)
    if risk <= 0:
        raise ValueError("Every Weighted Voting position must start with a protective stop")
    target = entry_price + risk * effective_settings.target_r if side == WeightedSide.BUY.value else entry_price - risk * effective_settings.target_r
    return WeightedVotingExitLifecycleState(
        trade_id=trade_id,
        symbol=symbol,
        side=side,
        original_quantity=quantity,
        remaining_quantity=quantity,
        entry_price=entry_price,
        entry_timestamp=entry_timestamp,
        protective_stop=stop_price,
        profit_target=target,
        original_risk_per_share=risk,
        original_effective_settings=effective_settings,
        highest_price=entry_price,
        lowest_price=entry_price,
        weighted_allocation_id=weighted_allocation_id,
    )


def evaluate_exit_lifecycle(inputs: WeightedVotingExitInputs) -> WeightedVotingExitDecision:
    lifecycle = inputs.lifecycle
    if inputs.weighted_allocation_id != lifecycle.weighted_allocation_id:
        return _decision(WeightedExitAction.HOLD, WeightedExitReason.NONE, lifecycle, lifecycle.protective_stop, (), "Weighted Voting may close only its own allocation.")
    updated = _mark_price_extremes(lifecycle, inputs.current_price)
    stop = updated.protective_stop
    reasons: list[str] = []

    break_even_stop = _break_even_stop(updated, inputs.current_price)
    if break_even_stop is not None:
        stop = _tighten_stop(updated.side, stop, break_even_stop)
        updated = _replace_stop(updated, stop, break_even_active=True)
        reasons.append("weighted_voting.exit.break_even_transition")

    trailing_stop = _condition_trailing_stop(updated, inputs.current_price, inputs.current_condition_quality)
    if trailing_stop is not None:
        stop = _tighten_stop(updated.side, stop, trailing_stop)
        updated = _replace_stop(updated, stop)
        reasons.append("weighted_voting.exit.condition_trailing_stop")

    stop = _never_widen_stop(updated.side, lifecycle.protective_stop, stop)
    updated = _replace_stop(updated, stop)
    if _stop_hit(updated.side, inputs.current_price, stop):
        return _decision(WeightedExitAction.EXIT, WeightedExitReason.STOP_HIT, updated, stop, tuple(reasons + ["weighted_voting.exit.protective_stop_hit"]), "Protective stop was hit.")
    if _target_hit(updated.side, inputs.current_price, updated.profit_target):
        return _decision(WeightedExitAction.EXIT, WeightedExitReason.TARGET_HIT, updated, stop, tuple(reasons + ["weighted_voting.exit.profit_target_hit"]), "Profit target was hit.")
    if inputs.global_emergency_exit:
        return _decision(WeightedExitAction.EXIT, WeightedExitReason.RISK_GATE, updated, stop, tuple(reasons + ["weighted_voting.exit.global_emergency_exit"]), "Global emergency exit closes Weighted Voting allocation.")
    if inputs.end_of_session:
        return _decision(WeightedExitAction.EXIT, WeightedExitReason.END_OF_DAY, updated, stop, tuple(reasons + ["weighted_voting.exit.end_of_session_liquidation"]), "End-of-session liquidation closes Weighted Voting allocation.")
    if inputs.local_risk_exit:
        return _decision(WeightedExitAction.EXIT, WeightedExitReason.RISK_GATE, updated, stop, tuple(reasons + ["weighted_voting.exit.local_risk_exit"]), "Local risk exit closes Weighted Voting allocation.")
    time_limit = updated.original_effective_settings.time_stop_minutes
    if time_limit > 0 and inputs.current_timestamp - updated.entry_timestamp >= timedelta(minutes=time_limit):
        return _decision(WeightedExitAction.EXIT, WeightedExitReason.TIME_EXIT, updated, stop, tuple(reasons + ["weighted_voting.exit.time_stop"]), "Time stop elapsed.")

    deterioration = _deterioration_detected(updated, inputs)
    deterioration_count = updated.deterioration_count + 1 if deterioration else 0
    updated = _replace_deterioration(updated, deterioration_count)
    if deterioration and deterioration_count >= inputs.deterioration_required_count:
        return _decision(WeightedExitAction.EXIT, WeightedExitReason.RISK_GATE, updated, stop, tuple(reasons + ["weighted_voting.exit.persistent_deterioration"]), "Weighted edge, opposite signal, or market quality deterioration persisted.")
    if deterioration:
        reasons.append("weighted_voting.exit.deterioration_observed")
    if inputs.new_entries_blocked:
        reasons.append("weighted_voting.exit.entries_blocked_but_protective_exit_available")
    return _decision(WeightedExitAction.HOLD, WeightedExitReason.NONE, updated, stop, tuple(reasons or ["weighted_voting.exit.hold"]), "No Weighted Voting exit condition is active.")


def exit_policy_status() -> dict[str, str]:
    return {
        "version": WEIGHTED_VOTING_EXIT_POLICY_VERSION,
        "status": "implemented",
        "explanation": "Weighted Voting exit lifecycle is deterministic and allocation-scoped.",
    }


def _risk_per_share(side: WeightedSide | str, entry_price: float, stop_price: float) -> float:
    if side == WeightedSide.BUY.value:
        return entry_price - stop_price
    if side == WeightedSide.SELL.value:
        return stop_price - entry_price
    return 0.0


def _mark_price_extremes(lifecycle: WeightedVotingExitLifecycleState, current_price: float) -> WeightedVotingExitLifecycleState:
    return WeightedVotingExitLifecycleState(
        **{
            **lifecycle.__dict__,
            "highest_price": max(lifecycle.highest_price or current_price, current_price),
            "lowest_price": min(lifecycle.lowest_price or current_price, current_price),
        }
    )


def _break_even_stop(lifecycle: WeightedVotingExitLifecycleState, current_price: float) -> float | None:
    settings = lifecycle.original_effective_settings
    if lifecycle.break_even_active:
        return None
    favorable = current_price - lifecycle.entry_price if lifecycle.side == WeightedSide.BUY.value else lifecycle.entry_price - current_price
    if favorable >= lifecycle.original_risk_per_share * settings.break_even_trigger_r:
        return lifecycle.entry_price
    return None


def _condition_trailing_stop(lifecycle: WeightedVotingExitLifecycleState, current_price: float, quality: WeightedMarketQuality | str) -> float | None:
    if quality != WeightedMarketQuality.CLEAN.value:
        return None
    distance = lifecycle.original_risk_per_share * max(0.25, lifecycle.original_effective_settings.trailing_stop_atr_multiplier)
    if lifecycle.side == WeightedSide.BUY.value and lifecycle.highest_price is not None and lifecycle.highest_price > lifecycle.entry_price:
        return lifecycle.highest_price - distance
    if lifecycle.side == WeightedSide.SELL.value and lifecycle.lowest_price is not None and lifecycle.lowest_price < lifecycle.entry_price:
        return lifecycle.lowest_price + distance
    return None


def _tighten_stop(side: WeightedSide | str, current_stop: float, candidate_stop: float) -> float:
    if side == WeightedSide.BUY.value:
        return max(current_stop, candidate_stop)
    return min(current_stop, candidate_stop)


def _never_widen_stop(side: WeightedSide | str, original_stop: float, candidate_stop: float) -> float:
    if side == WeightedSide.BUY.value:
        return max(original_stop, candidate_stop)
    return min(original_stop, candidate_stop)


def _replace_stop(lifecycle: WeightedVotingExitLifecycleState, stop: float, *, break_even_active: bool | None = None) -> WeightedVotingExitLifecycleState:
    return WeightedVotingExitLifecycleState(
        **{
            **lifecycle.__dict__,
            "protective_stop": stop,
            "break_even_active": lifecycle.break_even_active if break_even_active is None else break_even_active,
        }
    )


def _replace_deterioration(lifecycle: WeightedVotingExitLifecycleState, count: int) -> WeightedVotingExitLifecycleState:
    return WeightedVotingExitLifecycleState(**{**lifecycle.__dict__, "deterioration_count": count})


def _stop_hit(side: WeightedSide | str, current_price: float, stop: float) -> bool:
    return current_price <= stop if side == WeightedSide.BUY.value else current_price >= stop


def _target_hit(side: WeightedSide | str, current_price: float, target: float) -> bool:
    return current_price >= target if side == WeightedSide.BUY.value else current_price <= target


def _deterioration_detected(lifecycle: WeightedVotingExitLifecycleState, inputs: WeightedVotingExitInputs) -> bool:
    if inputs.current_condition_quality == WeightedMarketQuality.UNSTABLE.value:
        return True
    decision = inputs.current_weighted_decision
    if decision is None:
        return False
    if decision.vote_scores.winner_edge < inputs.weighted_edge_threshold:
        return True
    if lifecycle.side == WeightedSide.BUY.value and decision.signal == WeightedSide.SELL.value:
        return True
    if lifecycle.side == WeightedSide.SELL.value and decision.signal == WeightedSide.BUY.value:
        return True
    return False


def _decision(
    action: WeightedExitAction,
    reason: WeightedExitReason,
    lifecycle: WeightedVotingExitLifecycleState,
    stop: float,
    reason_codes: tuple[str, ...],
    explanation: str,
) -> WeightedVotingExitDecision:
    risk = _risk_per_share(lifecycle.side, lifecycle.entry_price, stop)
    return WeightedVotingExitDecision(
        action=action.value,
        exit_reason=reason.value,
        exit_quantity=lifecycle.remaining_quantity if action == WeightedExitAction.EXIT else 0,
        stop_price=stop,
        target_price=lifecycle.profit_target,
        risk_per_share=max(0.0, risk),
        updated_lifecycle=lifecycle,
        reason_codes=reason_codes,
        explanation=explanation,
    )
