"""Regime backtest trade ledger helpers."""

from __future__ import annotations


def close_trade(open_trade: dict, candle: dict, exit_price: float, reason: str) -> dict:
    side = open_trade["side"]
    quantity = int(open_trade["quantity"])
    entry = float(open_trade["entryPrice"])
    pnl = (exit_price - entry) * quantity if side == "Long" else (entry - exit_price) * quantity
    risk_per_share = abs(entry - float(open_trade.get("stopPrice") or entry)) or 0.01
    return {**open_trade, "exitAt": candle["timestamp"], "exitPrice": exit_price, "exitReason": reason, "pnl": pnl, "rMultiple": pnl / max(0.01, risk_per_share * quantity)}
