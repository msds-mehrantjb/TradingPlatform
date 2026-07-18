"""Execution simulation helpers for Weighted Voting backtests."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor

from backend.app.algorithms.weighted_voting.entry_policy import WeightedEntryPolicyResult, WeightedEntryType
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingCandle
from backend.app.algorithms.weighted_voting.models import WeightedSide


WEIGHTED_VOTING_EXECUTION_SIMULATOR_VERSION = "weighted_voting_execution_simulator_v2"


@dataclass(frozen=True)
class WeightedBacktestExecutionCostModel:
    entry_slippage_per_share: float = 0.01
    exit_slippage_per_share: float = 0.01
    fee_per_share: float = 0.005
    regulatory_fee_per_share: float = 0.0
    minimum_fee: float = 0.0


@dataclass(frozen=True)
class WeightedBacktestPendingOrder:
    order_id: str
    side: WeightedSide | str
    requested_quantity: int
    decision_candle_index: int
    earliest_entry_index: int
    entry_policy: WeightedEntryPolicyResult
    participation_rate: float
    spread: float
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class WeightedBacktestFill:
    filled: bool
    quantity: int
    requested_quantity: int
    fill_price: float | None
    partial: bool
    reason_codes: tuple[str, ...]
    explanation: str


def simulator_status() -> dict[str, object]:
    return {
        "version": WEIGHTED_VOTING_EXECUTION_SIMULATOR_VERSION,
        "status": "implemented",
        "ownedCosts": ["slippage", "spread", "fees", "regulatory_costs"],
        "ownedFillBehavior": ["next_candle_entry", "participation_limit", "partial_fills", "unfilled_orders"],
        "explanation": "Weighted Voting backtests simulate fills, slippage, fees, participation limits, partial fills, and unfilled orders.",
    }


def simulate_entry_fill(
    *,
    order: WeightedBacktestPendingOrder,
    candle_index: int,
    candle: WeightedVotingCandle,
    cost_model: WeightedBacktestExecutionCostModel,
) -> WeightedBacktestFill:
    if candle_index < order.earliest_entry_index:
        return _unfilled(order, "weighted_voting.backtest.fill.waiting_for_next_candle", "Signal on candle T cannot enter before candle T+1.")
    if order.requested_quantity <= 0:
        return _unfilled(order, "weighted_voting.backtest.fill.zero_quantity", "Order quantity is zero.")
    if not order.entry_policy.accepted or order.entry_policy.limit_price is None:
        return _unfilled(order, "weighted_voting.backtest.fill.entry_policy_rejected", "Entry policy did not accept the order.")
    if not _entry_price_touched(order, candle):
        return _unfilled(order, "weighted_voting.backtest.fill.price_not_touched", "Historical candle did not touch the entry policy price.")

    participation_quantity = max(0, floor(candle.volume * order.participation_rate))
    quantity = min(order.requested_quantity, participation_quantity)
    if quantity <= 0:
        return _unfilled(order, "weighted_voting.backtest.fill.participation_zero", "Participation limit allowed no fill.")
    fill_price = _entry_fill_price(order, cost_model)
    return WeightedBacktestFill(
        filled=True,
        quantity=quantity,
        requested_quantity=order.requested_quantity,
        fill_price=round(fill_price, 10),
        partial=quantity < order.requested_quantity,
        reason_codes=tuple(
            dict.fromkeys(
                order.reason_codes
                + (
                    "weighted_voting.backtest.fill.filled",
                    "weighted_voting.backtest.fill.partial" if quantity < order.requested_quantity else "weighted_voting.backtest.fill.complete",
                )
            )
        ),
        explanation="Entry fill used the accepted Weighted Voting entry policy, next-candle timing, configured slippage, actual spread, and participation limits.",
    )


def entry_fee(quantity: int, cost_model: WeightedBacktestExecutionCostModel) -> float:
    if quantity <= 0:
        return 0.0
    return max(cost_model.minimum_fee, quantity * cost_model.fee_per_share)


def exit_fee(quantity: int, cost_model: WeightedBacktestExecutionCostModel) -> float:
    if quantity <= 0:
        return 0.0
    return max(cost_model.minimum_fee, quantity * (cost_model.fee_per_share + cost_model.regulatory_fee_per_share))


def conservative_exit_price(*, side: WeightedSide | str, raw_exit_price: float, cost_model: WeightedBacktestExecutionCostModel, spread: float) -> float:
    half_spread = spread / 2.0
    if side == WeightedSide.BUY.value:
        return round(raw_exit_price - cost_model.exit_slippage_per_share - half_spread, 10)
    return round(raw_exit_price + cost_model.exit_slippage_per_share + half_spread, 10)


def _entry_price_touched(order: WeightedBacktestPendingOrder, candle: WeightedVotingCandle) -> bool:
    policy = order.entry_policy
    trigger = policy.trigger_price
    limit = policy.limit_price
    if limit is None:
        return False
    if policy.entry_type == WeightedEntryType.LIMIT.value:
        return candle.low <= limit if order.side == WeightedSide.BUY.value else candle.high >= limit
    if policy.entry_type in (WeightedEntryType.STOP_LIMIT.value, WeightedEntryType.CONFIRMATION_LIMIT.value):
        if trigger is None:
            return False
        if order.side == WeightedSide.BUY.value:
            return candle.high >= trigger and candle.low <= limit
        return candle.low <= trigger and candle.high >= limit
    return False


def _entry_fill_price(order: WeightedBacktestPendingOrder, cost_model: WeightedBacktestExecutionCostModel) -> float:
    limit = float(order.entry_policy.limit_price or 0.0)
    half_spread = order.spread / 2.0
    if order.side == WeightedSide.BUY.value:
        return limit + cost_model.entry_slippage_per_share + half_spread
    return max(0.01, limit - cost_model.entry_slippage_per_share - half_spread)


def _unfilled(order: WeightedBacktestPendingOrder, reason_code: str, explanation: str) -> WeightedBacktestFill:
    return WeightedBacktestFill(
        filled=False,
        quantity=0,
        requested_quantity=order.requested_quantity,
        fill_price=None,
        partial=False,
        reason_codes=tuple(dict.fromkeys(order.reason_codes + (reason_code,))),
        explanation=explanation,
    )
