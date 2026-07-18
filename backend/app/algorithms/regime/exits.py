"""Backend-owned Regime exit policy."""

from __future__ import annotations


def evaluate_regime_exit(position: dict | None, candle: dict, confirmed_regime: str) -> dict:
    if not position:
        return {"action": "hold", "reasonCodes": ()}
    side = position.get("side", "Long")
    high = float(candle.get("high", candle.get("close", 0)))
    low = float(candle.get("low", candle.get("close", 0)))
    close = float(candle.get("close", 0))
    stop = float(position.get("stopPrice") or position.get("stop_price") or close)
    target = float(position.get("targetPrice") or position.get("target_price") or close)
    if side == "Long":
        if low <= stop:
            return {"action": "exit_long", "price": stop, "reasonCodes": ("regime.exit.stop_hit",)}
        if high >= target:
            return {"action": "exit_long", "price": target, "reasonCodes": ("regime.exit.target_hit",)}
    else:
        if high >= stop:
            return {"action": "exit_short", "price": stop, "reasonCodes": ("regime.exit.stop_hit",)}
        if low <= target:
            return {"action": "exit_short", "price": target, "reasonCodes": ("regime.exit.target_hit",)}
    if confirmed_regime in {"event_risk", "liquidity_stress", "extreme_volatility_no_trade"}:
        return {"action": "reduce_or_exit", "price": close, "reasonCodes": ("regime.exit.risk_off_regime",)}
    return {"action": "hold", "reasonCodes": ()}

