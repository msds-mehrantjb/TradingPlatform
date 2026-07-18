"""Regime next-bar execution simulator."""

from __future__ import annotations


def simulate_next_bar_fill(intent: dict, candle: dict, cost_per_share: float = 0.02) -> dict:
    quantity = int(intent.get("quantity") or 0)
    price = float(candle.get("open") or candle.get("close") or 0)
    return {
        "filledQuantity": quantity,
        "entryPrice": price,
        "fees": abs(quantity) * cost_per_share,
        "slippage": abs(quantity) * cost_per_share,
    }

