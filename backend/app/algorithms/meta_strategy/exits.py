"""Deterministic Meta-Strategy exit primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import floor
from typing import Literal

from backend.app.algorithms.meta_strategy.versions import META_STRATEGY_EXIT_POLICY_VERSION


MetaStrategyExitSide = Literal["BUY", "SELL"]
MetaStrategyExitAction = Literal["HOLD", "EXIT", "PARTIAL_EXIT"]
MetaStrategyExitReason = Literal[
    "NONE",
    "PROTECTIVE_STOP",
    "PROFIT_TARGET",
    "MAXIMUM_HOLD",
    "SESSION_END",
    "EVENT_RISK",
    "LIQUIDITY_EMERGENCY",
    "GLOBAL_EMERGENCY",
    "PARTIAL_TARGET",
    "RECONCILIATION",
]


@dataclass(frozen=True)
class MetaStrategyExitCandle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class MetaStrategyPositionState:
    position_id: str
    symbol: str
    side: MetaStrategyExitSide
    original_quantity: int
    remaining_quantity: int
    entry_price: float
    opened_at: datetime
    protective_stop: float
    profit_target: float
    maximum_holding_minutes: int
    protective_order_quantity: int
    partial_exit_taken: bool = False
    exit_policy_version: str = META_STRATEGY_EXIT_POLICY_VERSION

    def __post_init__(self) -> None:
        if self.original_quantity <= 0 or self.remaining_quantity < 0:
            raise ValueError("meta_strategy.exit.invalid_quantity")
        if self.entry_price <= 0 or self.protective_stop <= 0 or self.profit_target <= 0:
            raise ValueError("meta_strategy.exit.invalid_prices")
        if _risk_per_share(self.side, self.entry_price, self.protective_stop) <= 0:
            raise ValueError("meta_strategy.exit.protective_stop_required")
        if _target_distance(self.side, self.entry_price, self.profit_target) <= 0:
            raise ValueError("meta_strategy.exit.profit_target_required")
        if self.maximum_holding_minutes <= 0:
            raise ValueError("meta_strategy.exit.maximum_holding_time_required")


@dataclass(frozen=True)
class MetaStrategyExitInputs:
    position: MetaStrategyPositionState
    candle: MetaStrategyExitCandle
    session_end_exit: bool = False
    event_risk_exit: bool = False
    liquidity_emergency_exit: bool = False
    global_emergency_exit: bool = False
    proposed_stop: float | None = None
    ml_delay_requested: bool = False
    partial_exit_enabled: bool = True
    partial_exit_fraction: float = 0.50
    partial_exit_trigger_r: float = 1.0


@dataclass(frozen=True)
class MetaStrategyExitDecision:
    action: MetaStrategyExitAction
    exit_reason: MetaStrategyExitReason
    exit_quantity: int
    exit_price: float | None
    stop_price: float
    target_price: float
    updated_position: MetaStrategyPositionState
    gap_through_stop: bool
    ml_stop_update_applied: bool
    ml_delay_applied: bool
    reason_codes: tuple[str, ...]
    exit_policy_version: str = META_STRATEGY_EXIT_POLICY_VERSION


def open_meta_strategy_position(
    *,
    position_id: str,
    symbol: str,
    side: MetaStrategyExitSide,
    quantity: int,
    entry_price: float,
    opened_at: datetime,
    protective_stop: float,
    profit_target: float,
    maximum_holding_minutes: int,
) -> MetaStrategyPositionState:
    return MetaStrategyPositionState(
        position_id=position_id,
        symbol=symbol,
        side=side,
        original_quantity=int(quantity),
        remaining_quantity=int(quantity),
        entry_price=float(entry_price),
        opened_at=opened_at,
        protective_stop=float(protective_stop),
        profit_target=float(profit_target),
        maximum_holding_minutes=int(maximum_holding_minutes),
        protective_order_quantity=int(quantity),
    )


def tighten_meta_strategy_stop(
    *,
    side: MetaStrategyExitSide,
    current_stop: float,
    proposed_stop: float | None,
) -> tuple[float, tuple[str, ...]]:
    if proposed_stop is None:
        return current_stop, ("meta_strategy.exit.stop_removal_rejected",)
    if proposed_stop <= 0:
        return current_stop, ("meta_strategy.exit.invalid_stop_update_rejected",)
    if side == "BUY":
        if proposed_stop < current_stop:
            return current_stop, ("meta_strategy.exit.stop_widening_rejected",)
        return proposed_stop, ("meta_strategy.exit.stop_tightened_or_unchanged",)
    if proposed_stop > current_stop:
        return current_stop, ("meta_strategy.exit.stop_widening_rejected",)
    return proposed_stop, ("meta_strategy.exit.stop_tightened_or_unchanged",)


def evaluate_meta_strategy_exit(inputs: MetaStrategyExitInputs) -> MetaStrategyExitDecision:
    position = inputs.position
    stop, stop_reasons = tighten_meta_strategy_stop(
        side=position.side,
        current_stop=position.protective_stop,
        proposed_stop=inputs.proposed_stop if inputs.proposed_stop is not None else position.protective_stop,
    )
    updated = _replace_position(position, protective_stop=stop)
    reasons = list(stop_reasons)
    if inputs.ml_delay_requested:
        reasons.append("meta_strategy.exit.ml_delay_rejected")

    if inputs.global_emergency_exit:
        return _exit(inputs, updated, "GLOBAL_EMERGENCY", inputs.candle.close, reasons + ["meta_strategy.exit.global_emergency_exit"])
    if inputs.liquidity_emergency_exit:
        return _exit(inputs, updated, "LIQUIDITY_EMERGENCY", inputs.candle.close, reasons + ["meta_strategy.exit.liquidity_emergency_exit"])
    if inputs.event_risk_exit:
        return _exit(inputs, updated, "EVENT_RISK", inputs.candle.close, reasons + ["meta_strategy.exit.event_risk_exit"])

    gap_through, stop_price = _stop_hit_price(updated, inputs.candle)
    if stop_price is not None:
        code = "meta_strategy.exit.gap_through_stop" if gap_through else "meta_strategy.exit.protective_stop_hit"
        return _exit(inputs, updated, "PROTECTIVE_STOP", stop_price, reasons + [code], gap_through_stop=gap_through)

    target_price = _target_hit_price(updated, inputs.candle)
    if target_price is not None:
        return _exit(inputs, updated, "PROFIT_TARGET", target_price, reasons + ["meta_strategy.exit.profit_target_hit"])

    if _maximum_hold_elapsed(updated, inputs.candle.timestamp):
        return _exit(inputs, updated, "MAXIMUM_HOLD", inputs.candle.close, reasons + ["meta_strategy.exit.maximum_hold_exit"])
    if inputs.session_end_exit:
        return _exit(inputs, updated, "SESSION_END", inputs.candle.close, reasons + ["meta_strategy.exit.session_end_exit"])

    if inputs.partial_exit_enabled and not updated.partial_exit_taken:
        partial_price = _partial_exit_price(updated, inputs.candle, inputs.partial_exit_trigger_r)
        if partial_price is not None:
            quantity = _partial_exit_quantity(updated.remaining_quantity, inputs.partial_exit_fraction)
            after_partial = _replace_position(updated, remaining_quantity=updated.remaining_quantity - quantity, partial_exit_taken=True)
            return MetaStrategyExitDecision(
                action="PARTIAL_EXIT",
                exit_reason="PARTIAL_TARGET",
                exit_quantity=quantity,
                exit_price=partial_price,
                stop_price=after_partial.protective_stop,
                target_price=after_partial.profit_target,
                updated_position=after_partial,
                gap_through_stop=False,
                ml_stop_update_applied=False,
                ml_delay_applied=False,
                reason_codes=tuple(dict.fromkeys(reasons + ["meta_strategy.exit.partial_exit"])),
            )

    return MetaStrategyExitDecision(
        action="HOLD",
        exit_reason="NONE",
        exit_quantity=0,
        exit_price=None,
        stop_price=updated.protective_stop,
        target_price=updated.profit_target,
        updated_position=updated,
        gap_through_stop=False,
        ml_stop_update_applied=False,
        ml_delay_applied=False,
        reason_codes=tuple(dict.fromkeys(reasons + ["meta_strategy.exit.hold"])),
    )


def _exit(
    inputs: MetaStrategyExitInputs,
    position: MetaStrategyPositionState,
    reason: MetaStrategyExitReason,
    price: float,
    reason_codes: list[str],
    *,
    gap_through_stop: bool = False,
) -> MetaStrategyExitDecision:
    closed = _replace_position(position, remaining_quantity=0, protective_order_quantity=0)
    return MetaStrategyExitDecision(
        action="EXIT",
        exit_reason=reason,
        exit_quantity=position.remaining_quantity,
        exit_price=float(price),
        stop_price=closed.protective_stop,
        target_price=closed.profit_target,
        updated_position=closed,
        gap_through_stop=gap_through_stop,
        ml_stop_update_applied=False,
        ml_delay_applied=False,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
    )


def _stop_hit_price(position: MetaStrategyPositionState, candle: MetaStrategyExitCandle) -> tuple[bool, float | None]:
    if position.side == "BUY":
        if candle.open <= position.protective_stop:
            return True, candle.open
        if candle.low <= position.protective_stop:
            return False, position.protective_stop
    else:
        if candle.open >= position.protective_stop:
            return True, candle.open
        if candle.high >= position.protective_stop:
            return False, position.protective_stop
    return False, None


def _target_hit_price(position: MetaStrategyPositionState, candle: MetaStrategyExitCandle) -> float | None:
    if position.side == "BUY" and candle.high >= position.profit_target:
        return position.profit_target
    if position.side == "SELL" and candle.low <= position.profit_target:
        return position.profit_target
    return None


def _partial_exit_price(position: MetaStrategyPositionState, candle: MetaStrategyExitCandle, trigger_r: float) -> float | None:
    trigger = position.entry_price + _risk_per_share(position.side, position.entry_price, position.protective_stop) * max(0.0, trigger_r)
    if position.side == "SELL":
        trigger = position.entry_price - _risk_per_share(position.side, position.entry_price, position.protective_stop) * max(0.0, trigger_r)
    if position.side == "BUY" and candle.high >= trigger:
        return trigger
    if position.side == "SELL" and candle.low <= trigger:
        return trigger
    return None


def _maximum_hold_elapsed(position: MetaStrategyPositionState, timestamp: datetime) -> bool:
    return timestamp - position.opened_at >= timedelta(minutes=position.maximum_holding_minutes)


def _partial_exit_quantity(remaining_quantity: int, fraction: float) -> int:
    return max(1, min(remaining_quantity, floor(remaining_quantity * max(0.0, min(1.0, fraction)))))


def _risk_per_share(side: MetaStrategyExitSide, entry_price: float, stop_price: float) -> float:
    return entry_price - stop_price if side == "BUY" else stop_price - entry_price


def _target_distance(side: MetaStrategyExitSide, entry_price: float, target_price: float) -> float:
    return target_price - entry_price if side == "BUY" else entry_price - target_price


def _replace_position(position: MetaStrategyPositionState, **changes: object) -> MetaStrategyPositionState:
    return MetaStrategyPositionState(**{**position.__dict__, **changes})


__all__ = [
    "MetaStrategyExitAction",
    "MetaStrategyExitCandle",
    "MetaStrategyExitDecision",
    "MetaStrategyExitInputs",
    "MetaStrategyExitReason",
    "MetaStrategyExitSide",
    "MetaStrategyPositionState",
    "evaluate_meta_strategy_exit",
    "open_meta_strategy_position",
    "tighten_meta_strategy_stop",
]
