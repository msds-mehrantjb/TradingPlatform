"""Deterministic WCA backtest execution simulation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.app.algorithms.wca.contracts import BacktestRunConfiguration, ProposedOrder, WcaCandle, WcaSide


WCA_BACKTEST_EXECUTION_SIMULATION_VERSION = "wca_backtest_execution_simulator_v1"
WcaBacktestExecutionStatus = Literal["FILLED", "PARTIALLY_FILLED", "UNFILLED", "CANCELLED", "EXPIRED", "REJECTED"]


@dataclass(frozen=True)
class WcaBacktestExecutionResult:
    status: WcaBacktestExecutionStatus
    requested_quantity: int
    filled_quantity: int
    fill_price: float | None
    reason_codes: tuple[str, ...]

    @property
    def filled(self) -> bool:
        return self.filled_quantity > 0 and self.fill_price is not None


def simulate_wca_backtest_execution(
    *,
    order: ProposedOrder | None,
    next_bar: WcaCandle,
    config: BacktestRunConfiguration,
    side_allowed: bool,
) -> WcaBacktestExecutionResult:
    if order is None or order.quantity <= 0:
        return _result("REJECTED", 0, 0, None, "wca.backtest.execution.no_order")
    if not side_allowed:
        return _result("CANCELLED", order.quantity, 0, None, "wca.backtest.execution.side_mode_cancelled")
    limit_price = order.limit_price or order.trigger_price
    if limit_price is None or limit_price <= 0:
        return _result("REJECTED", order.quantity, 0, None, "wca.backtest.execution.invalid_limit")
    side = _side_value(order.side)
    if side not in {WcaSide.BUY.value, WcaSide.SELL.value}:
        return _result("REJECTED", order.quantity, 0, None, "wca.backtest.execution.invalid_side")
    if not _limit_touched(side, limit_price, next_bar):
        return _result("EXPIRED", order.quantity, 0, None, "wca.backtest.execution.limit_not_touched_next_bar")

    capacity = int(next_bar.volume * (config.max_participation_percent / 100.0))
    capped_quantity = min(order.quantity, max(0, capacity))
    if capped_quantity <= 0:
        return _result("UNFILLED", order.quantity, 0, None, "wca.backtest.execution.volume_capacity_zero")
    if capped_quantity < order.quantity and not config.allow_partial_fills:
        return _result("UNFILLED", order.quantity, 0, None, "wca.backtest.execution.partial_fill_disabled")

    status: WcaBacktestExecutionStatus = "PARTIALLY_FILLED" if capped_quantity < order.quantity else "FILLED"
    return _result(
        status,
        order.quantity,
        capped_quantity,
        _fill_price(side, limit_price, next_bar, config),
        "wca.backtest.execution.partial_fill" if status == "PARTIALLY_FILLED" else "wca.backtest.execution.filled",
    )


def _limit_touched(side: str, limit_price: float, next_bar: WcaCandle) -> bool:
    if side == WcaSide.BUY.value:
        return next_bar.low <= limit_price
    return next_bar.high >= limit_price


def _fill_price(side: str, limit_price: float, next_bar: WcaCandle, config: BacktestRunConfiguration) -> float:
    impact = limit_price * (config.market_impact_bps / 10000.0)
    explicit_cost = config.slippage_per_share + config.fee_per_share + impact
    if side == WcaSide.BUY.value:
        raw = min(limit_price, max(next_bar.open, next_bar.low))
        return round(raw + explicit_cost, 10)
    raw = max(limit_price, min(next_bar.open, next_bar.high))
    return round(max(0.01, raw - explicit_cost), 10)


def _result(status: WcaBacktestExecutionStatus, requested: int, filled: int, price: float | None, reason: str) -> WcaBacktestExecutionResult:
    return WcaBacktestExecutionResult(
        status=status,
        requested_quantity=requested,
        filled_quantity=filled,
        fill_price=price,
        reason_codes=(WCA_BACKTEST_EXECUTION_SIMULATION_VERSION, reason),
    )


def _side_value(side: WcaSide | str) -> str:
    return side.value if isinstance(side, WcaSide) else str(side)


__all__ = [
    "WCA_BACKTEST_EXECUTION_SIMULATION_VERSION",
    "WcaBacktestExecutionResult",
    "simulate_wca_backtest_execution",
]
