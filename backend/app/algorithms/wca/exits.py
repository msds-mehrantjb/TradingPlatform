"""WCA exit lifecycle helpers used by paper and backtest paths."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.app.algorithms.wca.contracts import BacktestTrade, WcaCandle, WcaSide
from backend.app.algorithms.wca.strategies.indicators import eastern_minutes


@dataclass(frozen=True)
class WcaBacktestOpenPosition:
    trade_id: str
    decision_id: str
    symbol: str
    side: WcaSide | str
    quantity: int
    entry_at: datetime
    entry_price: float
    stop_price: float
    target_price: float


@dataclass(frozen=True)
class WcaExitEvaluation:
    should_exit: bool
    reason: str = ""
    exit_price: float | None = None
    reason_codes: tuple[str, ...] = ()


def evaluate_wca_exit(
    *,
    position: WcaBacktestOpenPosition,
    candle: WcaCandle,
    opposite_signal: WcaSide | str = WcaSide.HOLD,
    emergency_exit: bool = False,
    session_exit_minutes: int = 15 * 60 + 59,
) -> WcaExitEvaluation:
    """Evaluate risk-reducing exits using only data available on the current bar."""

    if emergency_exit:
        return WcaExitEvaluation(True, "Emergency exit", candle.close, ("wca.exit.emergency",))
    side = _side_value(position.side)
    if side == WcaSide.BUY.value:
        if candle.low <= position.stop_price:
            return WcaExitEvaluation(True, "Protective stop", position.stop_price, ("wca.exit.protective_stop",))
        if candle.high >= position.target_price:
            return WcaExitEvaluation(True, "Profit target", position.target_price, ("wca.exit.profit_target",))
        if _side_value(opposite_signal) == WcaSide.SELL.value:
            return WcaExitEvaluation(True, "Opposite WCA signal", candle.close, ("wca.exit.opposite_signal",))
    elif side == WcaSide.SELL.value:
        if candle.high >= position.stop_price:
            return WcaExitEvaluation(True, "Protective stop", position.stop_price, ("wca.exit.protective_stop",))
        if candle.low <= position.target_price:
            return WcaExitEvaluation(True, "Profit target", position.target_price, ("wca.exit.profit_target",))
        if _side_value(opposite_signal) == WcaSide.BUY.value:
            return WcaExitEvaluation(True, "Opposite WCA signal", candle.close, ("wca.exit.opposite_signal",))
    if eastern_minutes(candle.timestamp) >= session_exit_minutes:
        return WcaExitEvaluation(True, "End of session", candle.close, ("wca.exit.session_close",))
    return WcaExitEvaluation(False)


def close_wca_backtest_trade(position: WcaBacktestOpenPosition, *, exit_at: datetime, exit_price: float, exit_reason: str, cost_per_share: float = 0.0) -> BacktestTrade:
    side = _side_value(position.side)
    if side == WcaSide.SELL.value:
        pnl = (position.entry_price - exit_price - cost_per_share) * position.quantity
    else:
        pnl = (exit_price - position.entry_price - cost_per_share) * position.quantity
    return BacktestTrade(
        trade_id=position.trade_id,
        decision_id=position.decision_id,
        symbol=position.symbol,
        side=side,
        quantity=position.quantity,
        entry_at=position.entry_at,
        exit_at=exit_at,
        entry_price=round(position.entry_price, 10),
        exit_price=round(max(0.01, exit_price), 10),
        pnl=round(pnl, 10),
        exit_reason=exit_reason,
    )


def mark_to_market_pnl(position: WcaBacktestOpenPosition | None, mark_price: float, cost_per_share: float = 0.0) -> float:
    if position is None:
        return 0.0
    if _side_value(position.side) == WcaSide.SELL.value:
        return (position.entry_price - mark_price - cost_per_share) * position.quantity
    return (mark_price - position.entry_price - cost_per_share) * position.quantity


def _side_value(side: WcaSide | str) -> str:
    return side.value if isinstance(side, WcaSide) else str(side)


__all__ = [
    "BacktestTrade",
    "WcaBacktestOpenPosition",
    "WcaExitEvaluation",
    "WcaSide",
    "close_wca_backtest_trade",
    "evaluate_wca_exit",
    "mark_to_market_pnl",
]
