"""Backend-authoritative Regime backtest engine."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.regime.backtest.execution import simulate_next_bar_fill
from backend.app.algorithms.regime.backtest.ledger import close_trade
from backend.app.algorithms.regime.backtest.metrics import calculate_backtest_metrics
from backend.app.algorithms.regime.backtest.walk_forward import walk_forward_summary
from backend.app.algorithms.regime.execution_pipeline import execute_regime_pipeline


REGIME_BACKTEST_ENGINE_VERSION = "regime_backtest_v3_backend"


def run_regime_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    symbol = str(payload.get("symbol") or "SPY").upper()
    candles = sorted(payload.get("candles") or payload.get("primaryCandles") or [], key=lambda item: item.get("timestamp", ""))
    settings = payload.get("settings") or {}
    starting_capital = float(payload.get("startingCapital") or settings.get("startingCapital") or 25_000)
    decisions: list[dict] = []
    trades: list[dict] = []
    open_trade: dict | None = None
    for index, candle in enumerate(candles):
        if open_trade is not None:
            stop = float(open_trade["stopPrice"])
            target = float(open_trade["targetPrice"])
            if float(candle.get("low", 0)) <= stop:
                trades.append(close_trade(open_trade, candle, stop, "stop_hit"))
                open_trade = None
            elif float(candle.get("high", 0)) >= target:
                trades.append(close_trade(open_trade, candle, target, "target_hit"))
                open_trade = None
        history = candles[: index + 1]
        output = execute_regime_pipeline({"marketData": {"symbol": symbol, "primaryCandles": history}, "settings": settings, "account": payload.get("account") or {}})
        decision_record = {
            "timestamp": candle.get("timestamp"),
            "signal": output["decision"]["signal"],
            "regime": output["decision"]["confirmed_state"]["confirmed_regime"],
            "strategyIds": [item["strategy_id"] for item in output["decision"]["strategy_outputs"] if item["eligible"]],
            "orderIntent": output["orderIntent"],
            "tradeBlockers": output["decision"]["trade_blockers"],
        }
        decisions.append(decision_record)
        next_candle = candles[index + 1] if index + 1 < len(candles) else None
        intent = output["orderIntent"]
        if open_trade is None and next_candle and intent and output["orderValidation"]["valid"]:
            fill = simulate_next_bar_fill(intent, next_candle)
            if fill["filledQuantity"] > 0:
                open_trade = {
                    "tradeId": f"{symbol}-{len(trades)+1}",
                    "side": "Long" if intent["side"] == "Buy" else "Short",
                    "quantity": fill["filledQuantity"],
                    "entryAt": next_candle["timestamp"],
                    "entryPrice": fill["entryPrice"],
                    "stopPrice": intent["stop_price"],
                    "targetPrice": intent["target_price"],
                }
    if open_trade and candles:
        trades.append(close_trade(open_trade, candles[-1], float(candles[-1].get("close", 0)), "end_of_backtest"))
    metrics = calculate_backtest_metrics(trades, decisions, starting_capital)
    return {
        "algorithmId": "regime",
        "engineVersion": REGIME_BACKTEST_ENGINE_VERSION,
        "authoritativeEngine": "backend.app.algorithms.regime.backtest.engine",
        "symbol": symbol,
        "candles": len(candles),
        "decisions": decisions,
        "trades": trades,
        "metrics": metrics,
        "walkForward": walk_forward_summary(candles, trades),
        "diagnostics": ("backend_authoritative_runtime", "next_bar_execution"),
    }

