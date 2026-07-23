import unittest
from unittest.mock import patch

from backend.app.algorithms.regime.backtest.engine import run_regime_backtest


def candle(timestamp: str, *, open_price: float, high: float, low: float, close: float) -> dict:
    return {
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100000,
    }


def pipeline_output(*, intent: dict | None, valid: bool, signal: str = "Hold") -> dict:
    return {
        "decision": {
            "signal": signal,
            "confirmed_state": {"confirmed_regime": "strong_downtrend"},
            "strategy_outputs": [],
            "trade_blockers": (),
        },
        "orderIntent": intent,
        "orderValidation": {"valid": valid},
        "tradeManagement": {"action": "hold"},
    }


class BacktestEngineCorrectnessTest(unittest.TestCase):
    def test_missing_order_intent_does_not_reference_uninitialized_local(self):
        candles = [
            candle("2026-07-23T13:30:00Z", open_price=100, high=101, low=99, close=100),
            candle("2026-07-23T13:31:00Z", open_price=100, high=101, low=99, close=100),
        ]
        with patch(
            "backend.app.algorithms.regime.backtest.engine.execute_regime_pipeline",
            return_value=pipeline_output(intent=None, valid=True),
        ):
            result = run_regime_backtest({"symbol": "SPY", "candles": candles})

        self.assertEqual(result["trades"], [])
        self.assertEqual(len(result["decisions"]), 2)

    def test_short_position_uses_high_for_stop_and_low_for_target(self):
        candles = [
            candle("2026-07-23T13:30:00Z", open_price=100, high=100.5, low=99.5, close=100),
            candle("2026-07-23T13:31:00Z", open_price=100, high=102, low=98, close=100),
        ]
        sell_intent = {
            "side": "Sell",
            "quantity": 10,
            "stop_price": 101,
            "target_price": 95,
        }
        outputs = [
            pipeline_output(intent=sell_intent, valid=True, signal="Sell"),
            pipeline_output(intent=None, valid=False),
        ]
        with patch(
            "backend.app.algorithms.regime.backtest.engine.execute_regime_pipeline",
            side_effect=outputs,
        ):
            result = run_regime_backtest({"symbol": "SPY", "candles": candles})

        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0]["side"], "Short")
        self.assertEqual(result["trades"][0]["exitReason"], "regime.exit.stop_hit")
        self.assertEqual(result["trades"][0]["exitPrice"], 101)
        self.assertLess(result["trades"][0]["pnl"], 0)


if __name__ == "__main__":
    unittest.main()
