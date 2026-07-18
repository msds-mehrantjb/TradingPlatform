"""Regime backtest trade ledger helpers."""

from __future__ import annotations


def close_trade(open_trade: dict, candle: dict, exit_price: float, reason: str) -> dict:
    side = open_trade["side"]
    quantity = int(open_trade["quantity"])
    entry = float(open_trade["entryPrice"])
    pnl = (exit_price - entry) * quantity if side == "Long" else (entry - exit_price) * quantity
    return {**open_trade, "exitAt": candle["timestamp"], "exitPrice": exit_price, "exitReason": reason, "pnl": pnl}

