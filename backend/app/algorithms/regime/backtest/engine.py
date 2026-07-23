"""Backend-authoritative Regime backtest engine."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.regime.backtest.execution import simulate_next_bar_fill
from backend.app.algorithms.regime.backtest.ledger import close_trade
from backend.app.algorithms.regime.backtest.metrics import calculate_backtest_metrics
from backend.app.algorithms.regime.backtest.walk_forward import walk_forward_summary
from backend.app.algorithms.regime.execution_pipeline import execute_regime_pipeline
from backend.app.algorithms.regime.trade_management import evaluate_regime_exit


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
        history = candles[: index + 1]
        output = execute_regime_pipeline({"marketData": {"symbol": symbol, "primaryCandles": history}, "settings": settings, "account": payload.get("account") or {}})
        if open_trade is not None:
            exit_result = evaluate_regime_exit(open_trade, candle, output["decision"]["confirmed_state"]["confirmed_regime"])
            if exit_result["action"] != "hold":
                reason = str((exit_result.get("reasonCodes") or ("regime.exit.policy",))[0])
                trades.append(close_trade(open_trade, candle, float(exit_result.get("price") or candle.get("close", 0)), reason))
                open_trade = None
        decision_record = {
            "timestamp": candle.get("timestamp"),
            "signal": output["decision"]["signal"],
            "regime": output["decision"]["confirmed_state"]["confirmed_regime"],
            "strategyIds": [item["strategy_id"] for item in output["decision"]["strategy_outputs"] if item["eligible"]],
            "orderIntent": output["orderIntent"],
            "tradeManagement": output["tradeManagement"],
            "tradeBlockers": output["decision"]["trade_blockers"],
        }
        decisions.append(decision_record)
        next_candle = candles[index + 1] if index + 1 < len(candles) else None
        intent = output["orderIntent"]
        if open_trade is None and next_candle is not None and intent is not None and output["orderValidation"].get("valid"):
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
    first_day = str(candles[0].get("timestamp", "na"))[:10] if candles else "na"
    last_day = str(candles[-1].get("timestamp", "na"))[:10] if candles else "na"
    return {
        "algorithmId": "regime",
        "engineVersion": REGIME_BACKTEST_ENGINE_VERSION,
        "authoritativeEngine": "backend.app.algorithms.regime.backtest.engine",
        "symbol": symbol,
        "candles": len(candles),
        "decisions": decisions,
        "trades": trades,
        "totalPnl": metrics["netProfit"],
        "metrics": metrics,
        "walkForward": [walk_forward_summary(candles, trades)],
        "diagnostics": ("backend_authoritative_runtime", "next_bar_execution"),
        "artifactPath": f"backend/data/regime-backtests/{symbol}_{first_day}_{last_day}.json",
        "cacheKey": f"{symbol}:{first_day}:{last_day}:{len(candles)}",
        "storageKey": f"regime-backtest:{symbol}:{first_day}:{last_day}:{len(candles)}",
        "failureMessage": None,
    }
