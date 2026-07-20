"""Backtest ledger construction for Meta-Strategy simulations."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.meta_strategy.execution_pipeline import MetaStrategyExecutionPipelineResult
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID


@dataclass(frozen=True)
class MetaStrategyBacktestTrade:
    algorithm_id: str
    decision_id: str
    symbol: str
    side: str
    requested_quantity: int
    filled_quantity: int
    entry_price: float
    exit_price: float
    gross_pnl: float
    fees: float
    net_pnl: float
    partial_fill: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class MetaStrategyBacktestLedger:
    algorithm_id: str
    trades: tuple[MetaStrategyBacktestTrade, ...]
    total_fees: float
    net_pnl: float


def ledger_from_pipeline_results(results: tuple[MetaStrategyExecutionPipelineResult, ...], *, fee_per_share: float) -> MetaStrategyBacktestLedger:
    trades = tuple(_trade_from_result(result, fee_per_share=fee_per_share) for result in results if result.order_intent is not None)
    return MetaStrategyBacktestLedger(
        algorithm_id=ALGORITHM_ID,
        trades=trades,
        total_fees=sum(trade.fees for trade in trades),
        net_pnl=sum(trade.net_pnl for trade in trades),
    )


def _trade_from_result(result: MetaStrategyExecutionPipelineResult, *, fee_per_share: float) -> MetaStrategyBacktestTrade:
    order = result.order_intent
    if order is None:
        raise ValueError("cannot create ledger trade without order intent")
    requested = int(order.quantity)
    filled = max(0, int(result.broker_result.get("filledQuantity") or 0))
    entry = float(result.geometry.entry_reference or result.snapshot.last_price)
    if order.side == "BUY":
        exit_price = float(result.geometry.geometry.target_price or entry)
        gross = (exit_price - entry) * filled
    else:
        exit_price = float(result.geometry.geometry.target_price or entry)
        gross = (entry - exit_price) * filled
    fees = filled * float(fee_per_share)
    return MetaStrategyBacktestTrade(
        algorithm_id=ALGORITHM_ID,
        decision_id=result.snapshot.decision_id,
        symbol=order.symbol,
        side=order.side,
        requested_quantity=requested,
        filled_quantity=filled,
        entry_price=entry,
        exit_price=exit_price,
        gross_pnl=gross,
        fees=fees,
        net_pnl=gross - fees,
        partial_fill=0 < filled < requested,
        reason_codes=tuple(dict.fromkeys((*result.reason_codes, "meta_strategy.backtest.ledger_recorded"))),
    )


__all__ = [
    "MetaStrategyBacktestLedger",
    "MetaStrategyBacktestTrade",
    "ledger_from_pipeline_results",
]
