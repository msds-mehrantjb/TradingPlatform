"""Regime backtest trade ledger helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def close_trade(
    open_trade: dict[str, Any],
    candle: dict[str, Any],
    exit_price: float,
    reason: str,
    *,
    exit_cost: float = 0.0,
    exit_slippage: float = 0.0,
    exit_bar_index: int | None = None,
) -> dict[str, Any]:
    side = open_trade["side"]
    quantity = int(open_trade["quantity"])
    entry = float(open_trade["entryPrice"])
    gross_pnl = (exit_price - entry) * quantity if side == "Long" else (entry - exit_price) * quantity
    entry_cost = float(open_trade.get("entryCost") or 0.0)
    total_cost = entry_cost + float(exit_cost)
    net_pnl = gross_pnl - total_cost
    risk_per_share = abs(entry - float(open_trade.get("stopPrice") or entry)) or 0.01
    holding_bars = _holding_bars(open_trade, candle, exit_bar_index)
    return {
        **open_trade,
        "exitAt": candle["timestamp"],
        "exitPrice": exit_price,
        "exitReason": reason,
        "exitCost": float(exit_cost),
        "exitSlippage": float(exit_slippage),
        "totalCosts": total_cost,
        "grossPnl": gross_pnl,
        "netPnl": net_pnl,
        "pnl": net_pnl,
        "holdingBars": holding_bars,
        "rMultiple": net_pnl / max(0.01, risk_per_share * quantity),
    }


def _holding_bars(open_trade: dict[str, Any], candle: dict[str, Any], exit_bar_index: int | None) -> int:
    entry_index = open_trade.get("entryBarIndex")
    if isinstance(entry_index, int) and isinstance(exit_bar_index, int):
        return max(0, exit_bar_index - entry_index)
    entry_at = str(open_trade.get("entryAt") or "")
    exit_at = str(candle.get("timestamp") or "")
    try:
        start = _parse_timestamp(entry_at)
        end = _parse_timestamp(exit_at)
    except ValueError:
        return 0
    return max(0, int((end - start).total_seconds() // 60))


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
