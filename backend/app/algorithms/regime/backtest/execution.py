"""Regime backtest execution simulator."""

from __future__ import annotations

from typing import Any


def simulate_order_execution(
    intent: dict,
    candles: list[dict[str, Any]],
    *,
    start_index: int,
    settings: dict[str, Any],
    market_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Simulate durable paper-order mechanics from point-in-time candles."""
    model = market_model or {}
    quantity = int(intent.get("quantity") or 0)
    if quantity <= 0:
        return _miss("rejected", "regime.backtest.execution.zero_quantity", 0, start_index)
    latency_bars = max(0, int(model.get("latencyBars", 0)))
    ttl_seconds = int(settings.get("orderTimeToLiveSeconds") or 60)
    ttl_bars = max(1, int(model.get("ttlBars") or max(1, round(ttl_seconds / 60))))
    first_index = start_index + latency_bars
    last_index = min(len(candles) - 1, first_index + ttl_bars - 1)
    if first_index >= len(candles):
        return _miss("expired", "regime.backtest.execution.no_future_bar", 0, start_index)
    side = str(intent.get("side") or "").title()
    limit_price = float(intent.get("entry_price") or intent.get("entryPrice") or 0)
    if limit_price <= 0:
        return _miss("rejected", "regime.backtest.execution.invalid_limit_price", 0, start_index)
    order_type = str(intent.get("orderType") or model.get("orderType") or "limit").lower()
    trigger_price = float(intent.get("stop_trigger_price") or intent.get("stopTriggerPrice") or limit_price)
    for index in range(first_index, last_index + 1):
        candle = candles[index]
        if not _eligible(side, order_type, candle, limit_price, trigger_price):
            continue
        close = float(candle.get("close") or limit_price)
        volume = float(candle.get("volume") or 0)
        participation = float(settings.get("maxParticipationPercent") or 0.0)
        max_fill = int(volume * participation) if participation > 0 else quantity
        filled = max(0, min(quantity, max_fill))
        if filled <= 0:
            return _miss("missed", "regime.backtest.execution.participation_cap_zero", 0, index)
        quoted_spread = _spread_per_share(close, model, candle)
        cost = estimate_transaction_cost_per_share(close, quoted_spread, settings, model)
        raw_price = limit_price if order_type in {"limit", "stop_limit", "stop-limit"} else close
        price = raw_price + cost["slippagePerShare"] if side == "Buy" else raw_price - cost["slippagePerShare"]
        return {
            "status": "filled" if filled == quantity else "partially_filled",
            "filledQuantity": filled,
            "requestedQuantity": quantity,
            "entryPrice": price,
            "referencePrice": raw_price,
            "timestamp": candle.get("timestamp"),
            "barIndex": index,
            "latencyBars": latency_bars,
            "ttlBars": ttl_bars,
            "fees": cost["feesPerShare"] * filled,
            "slippage": cost["slippagePerShare"] * filled,
            "spreadCost": cost["halfSpreadPerShare"] * filled,
            "spreadPerShare": quoted_spread,
            "adverseSelectionCost": cost["adverseSelectionPerShare"] * filled,
            "totalCost": cost["totalCostPerShare"] * filled,
            "totalCostPerShare": cost["totalCostPerShare"],
            "reasonCodes": ("regime.backtest.execution.filled",),
        }
    return _miss("expired", "regime.backtest.execution.ttl_expired_or_limit_not_reached", quantity, last_index)


def simulate_next_bar_fill(intent: dict, candle: dict, cost_per_share: float = 0.02) -> dict:
    """Backward-compatible wrapper for older focused tests."""
    quantity = int(intent.get("quantity") or 0)
    price = float(candle.get("open") or candle.get("close") or 0)
    return {
        "filledQuantity": quantity,
        "entryPrice": price,
        "fees": abs(quantity) * cost_per_share,
        "slippage": abs(quantity) * cost_per_share,
    }


def estimate_transaction_cost_per_share(price: float, spread_per_share: float, settings: dict[str, Any], model: dict[str, Any] | None = None) -> dict[str, float]:
    market_model = model or {}
    slippage_bps = float(market_model.get("slippageBps", settings.get("maximumSlippageBps") or 0.0))
    adverse_bps = float(market_model.get("adverseSelectionBps", 0.0))
    fees = float(market_model.get("feePerShare", 0.0))
    half_spread = max(0.0, spread_per_share / 2.0)
    slippage = max(0.0, price * slippage_bps / 10_000)
    adverse = max(0.0, price * adverse_bps / 10_000)
    total = half_spread + slippage + fees + adverse
    return {
        "halfSpreadPerShare": half_spread,
        "slippagePerShare": slippage,
        "feesPerShare": fees,
        "adverseSelectionPerShare": adverse,
        "totalCostPerShare": total,
    }


def _eligible(side: str, order_type: str, candle: dict[str, Any], limit_price: float, trigger_price: float) -> bool:
    high = float(candle.get("high") or candle.get("close") or 0)
    low = float(candle.get("low") or candle.get("close") or 0)
    if order_type in {"stop_limit", "stop-limit"}:
        triggered = high >= trigger_price if side == "Buy" else low <= trigger_price
        if not triggered:
            return False
    if side == "Buy":
        return low <= limit_price
    if side == "Sell":
        return high >= limit_price
    return False


def _spread_per_share(price: float, model: dict[str, Any], candle: dict[str, Any]) -> float:
    if candle.get("bid") is not None and candle.get("ask") is not None:
        return max(0.0, float(candle["ask"]) - float(candle["bid"]))
    if model.get("fixedSpreadPerShare") is not None:
        return max(0.0, float(model["fixedSpreadPerShare"]))
    spread_bps = float(model.get("spreadBps", 1.0))
    return max(0.0, price * spread_bps / 10_000)


def _miss(status: str, reason: str, quantity: int, bar_index: int) -> dict[str, Any]:
    return {
        "status": status,
        "filledQuantity": 0,
        "requestedQuantity": quantity,
        "entryPrice": None,
        "timestamp": None,
        "barIndex": bar_index,
        "fees": 0.0,
        "slippage": 0.0,
        "spreadCost": 0.0,
        "adverseSelectionCost": 0.0,
        "totalCost": 0.0,
        "totalCostPerShare": 0.0,
        "reasonCodes": (reason,),
    }
