"""Meta-Strategy trade management and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from backend.app.algorithms.meta_strategy.exits import (
    MetaStrategyExitDecision,
    MetaStrategyExitInputs,
    MetaStrategyPositionState,
    evaluate_meta_strategy_exit,
    open_meta_strategy_position,
)
from backend.app.algorithms.meta_strategy.versions import META_STRATEGY_EXIT_POLICY_VERSION


MetaStrategyFillStatus = Literal["UNFILLED", "PARTIAL", "FILLED"]


@dataclass(frozen=True)
class MetaStrategyFillReconciliation:
    status: MetaStrategyFillStatus
    planned_quantity: int
    filled_quantity: int
    protective_order_quantity: int
    position: MetaStrategyPositionState | None
    reason_codes: tuple[str, ...]
    exit_policy_version: str = META_STRATEGY_EXIT_POLICY_VERSION


@dataclass(frozen=True)
class MetaStrategyPositionReconciliation:
    expected_quantity: int
    broker_quantity: int
    reconciled_position: MetaStrategyPositionState
    discrepancy: int
    reason_codes: tuple[str, ...]
    exit_policy_version: str = META_STRATEGY_EXIT_POLICY_VERSION


@dataclass(frozen=True)
class MetaStrategyTradeManagementResult:
    position: MetaStrategyPositionState
    exit_decision: MetaStrategyExitDecision
    reason_codes: tuple[str, ...]
    exit_policy_version: str = META_STRATEGY_EXIT_POLICY_VERSION


def reconcile_meta_strategy_fill(
    *,
    planned_quantity: int,
    filled_quantity: int,
    position_id: str,
    symbol: str,
    side: Literal["BUY", "SELL"],
    average_fill_price: float,
    filled_at: datetime,
    protective_stop: float,
    profit_target: float,
    maximum_holding_minutes: int,
) -> MetaStrategyFillReconciliation:
    planned = max(0, int(planned_quantity))
    filled = max(0, min(planned, int(filled_quantity)))
    if filled == 0:
        return MetaStrategyFillReconciliation(
            status="UNFILLED",
            planned_quantity=planned,
            filled_quantity=0,
            protective_order_quantity=0,
            position=None,
            reason_codes=("meta_strategy.trade.fill_unfilled",),
        )
    position = open_meta_strategy_position(
        position_id=position_id,
        symbol=symbol,
        side=side,
        quantity=filled,
        entry_price=average_fill_price,
        opened_at=filled_at,
        protective_stop=protective_stop,
        profit_target=profit_target,
        maximum_holding_minutes=maximum_holding_minutes,
    )
    status: MetaStrategyFillStatus = "FILLED" if filled == planned else "PARTIAL"
    reason = "meta_strategy.trade.fill_complete" if status == "FILLED" else "meta_strategy.trade.partial_fill_tracked"
    return MetaStrategyFillReconciliation(
        status=status,
        planned_quantity=planned,
        filled_quantity=filled,
        protective_order_quantity=filled,
        position=position,
        reason_codes=(reason, "meta_strategy.trade.protective_quantity_matches_fill"),
    )


def reconcile_meta_strategy_position(
    position: MetaStrategyPositionState,
    *,
    broker_quantity: int,
) -> MetaStrategyPositionReconciliation:
    actual = max(0, int(broker_quantity))
    expected = int(position.remaining_quantity)
    reconciled = _replace_position(position, remaining_quantity=actual, protective_order_quantity=actual)
    if actual == expected:
        reasons = ("meta_strategy.trade.position_reconciled",)
    elif actual == 0:
        reasons = ("meta_strategy.trade.position_closed_by_reconciliation",)
    else:
        reasons = ("meta_strategy.trade.position_quantity_reconciled",)
    return MetaStrategyPositionReconciliation(
        expected_quantity=expected,
        broker_quantity=actual,
        reconciled_position=reconciled,
        discrepancy=actual - expected,
        reason_codes=reasons,
    )


def apply_meta_strategy_partial_exit(
    position: MetaStrategyPositionState,
    *,
    exit_quantity: int,
) -> MetaStrategyPositionState:
    quantity = max(0, min(int(exit_quantity), position.remaining_quantity))
    return _replace_position(
        position,
        remaining_quantity=position.remaining_quantity - quantity,
        protective_order_quantity=position.remaining_quantity - quantity,
        partial_exit_taken=True,
    )


def manage_meta_strategy_trade(inputs: MetaStrategyExitInputs) -> MetaStrategyTradeManagementResult:
    decision = evaluate_meta_strategy_exit(inputs)
    return MetaStrategyTradeManagementResult(
        position=decision.updated_position,
        exit_decision=decision,
        reason_codes=tuple(dict.fromkeys(("meta_strategy.trade_management.evaluated", *decision.reason_codes))),
    )


def _replace_position(position: MetaStrategyPositionState, **changes: object) -> MetaStrategyPositionState:
    return MetaStrategyPositionState(**{**position.__dict__, **changes})


__all__ = [
    "MetaStrategyFillReconciliation",
    "MetaStrategyFillStatus",
    "MetaStrategyPositionReconciliation",
    "MetaStrategyTradeManagementResult",
    "apply_meta_strategy_partial_exit",
    "manage_meta_strategy_trade",
    "reconcile_meta_strategy_fill",
    "reconcile_meta_strategy_position",
]
