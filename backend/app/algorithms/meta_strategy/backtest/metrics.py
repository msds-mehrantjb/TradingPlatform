"""Backtest metrics for Meta-Strategy simulations."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.meta_strategy.backtest.ledger import MetaStrategyBacktestLedger
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID


@dataclass(frozen=True)
class MetaStrategyBacktestMetrics:
    algorithm_id: str
    trade_count: int
    net_pnl: float
    gross_profit: float
    gross_loss: float
    profit_factor: float
    win_rate: float
    total_fees: float
    partial_fill_count: int


def calculate_backtest_metrics(ledger: MetaStrategyBacktestLedger) -> MetaStrategyBacktestMetrics:
    wins = [trade for trade in ledger.trades if trade.net_pnl > 0]
    losses = [trade for trade in ledger.trades if trade.net_pnl < 0]
    gross_profit = sum(trade.net_pnl for trade in wins)
    gross_loss = abs(sum(trade.net_pnl for trade in losses))
    trade_count = len(ledger.trades)
    return MetaStrategyBacktestMetrics(
        algorithm_id=ALGORITHM_ID,
        trade_count=trade_count,
        net_pnl=ledger.net_pnl,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=float("inf") if gross_profit > 0 and gross_loss == 0 else gross_profit / gross_loss if gross_loss else 0.0,
        win_rate=len(wins) / trade_count if trade_count else 0.0,
        total_fees=ledger.total_fees,
        partial_fill_count=sum(1 for trade in ledger.trades if trade.partial_fill),
    )


__all__ = ["MetaStrategyBacktestMetrics", "calculate_backtest_metrics"]
