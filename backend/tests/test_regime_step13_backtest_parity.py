from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.regime.backtest.engine import run_regime_backtest
from backend.app.algorithms.regime.backtest.execution import simulate_order_execution
from backend.app.algorithms.regime.backtest.metrics import calculate_backtest_metrics
from backend.app.algorithms.regime.backtest.walk_forward import walk_forward_summary
from backend.app.algorithms.regime.configuration import validate_regime_trading_settings_snapshot
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.stateful_core import process_completed_bar


IDENTITY = {
    "algorithmId": "regime",
    "algorithmInstanceId": "regime-step13",
    "accountId": "paper-account-step13",
    "runtimeMode": "shadow",
    "symbol": "SPY",
}


class RegimeStep13BacktestParityTest(unittest.TestCase):
    def test_metrics_deduct_costs_and_drawdown_comes_from_equity_curve(self) -> None:
        trades = [
            {"tradeId": "t1", "exitAt": "2026-07-23T13:31:00Z", "grossPnl": 120.0, "netPnl": 100.0, "totalCosts": 20.0, "quantity": 10, "entryPrice": 100.0},
            {"tradeId": "t2", "exitAt": "2026-07-23T13:32:00Z", "grossPnl": -130.0, "netPnl": -150.0, "totalCosts": 20.0, "quantity": 10, "entryPrice": 101.0},
        ]

        metrics = calculate_backtest_metrics(trades, [], 1_000.0)

        self.assertEqual(metrics["grossPnl"], -10.0)
        self.assertEqual(metrics["netProfit"], -50.0)
        self.assertNotEqual(metrics["netProfit"], metrics["grossPnl"])
        self.assertEqual(metrics["maximumDrawdown"], 150.0)
        self.assertEqual([point["equity"] for point in metrics["equityCurve"]], [1_000.0, 1_100.0, 950.0])

    def test_execution_simulates_partial_fill_participation_and_costs(self) -> None:
        intent = {"symbol": "SPY", "side": "Buy", "quantity": 1_000, "entry_price": 100.0}
        future = [{"timestamp": "2026-07-23T13:31:00Z", "open": 100.1, "high": 100.2, "low": 99.9, "close": 100.0, "volume": 1_000}]

        fill = simulate_order_execution(
            intent,
            future,
            start_index=0,
            settings={"maxParticipationPercent": 0.02, "orderTimeToLiveSeconds": 60, "maximumSlippageBps": 4.0},
            market_model={"latencyBars": 0, "ttlBars": 1, "spreadBps": 2.0, "feePerShare": 0.01, "adverseSelectionBps": 1.0},
        )

        self.assertEqual(fill["status"], "partially_filled")
        self.assertEqual(fill["filledQuantity"], 20)
        self.assertGreater(fill["totalCost"], fill["fees"])

    def test_walk_forward_and_holdout_can_fail_from_calculated_evidence(self) -> None:
        candles = fixture_candles(12)
        trades = [
            {"exitAt": candles[2]["timestamp"], "netPnl": -5.0},
            {"exitAt": candles[-1]["timestamp"], "netPnl": 0.0},
        ]

        summary = walk_forward_summary(
            candles,
            trades,
            folds=3,
            holdout_fraction=0.25,
            minimum_fold_net_profit=0.0,
            minimum_holdout_net_profit=1.0,
        )

        self.assertFalse(summary["accepted"])
        self.assertFalse(summary["walkForwardStable"])
        self.assertFalse(summary["holdout"]["accepted"])

    def test_backtest_replay_matches_paper_shadow_stateful_core_for_identical_input(self) -> None:
        settings = settings_snapshot()
        candles = fixture_candles(72)
        account = {"availableBuyingPower": 25_000, "remainingAlgorithmRiskDollars": 500, "globalRiskCapacityQuantity": 1_000}
        result = run_regime_backtest(
            {
                "symbol": "SPY",
                "candles": candles,
                "__regime_settings_snapshot": settings,
                "runtimeMode": "shadow",
                "account": account,
            }
        )
        index = 10
        replay_decision = result["decisions"][index]
        direct = process_completed_bar(
            snapshot=build_regime_market_snapshot({"symbol": "SPY", "primaryCandles": candles[: index + 1], "oneMinuteCandles": candles[: index + 1]}),
            settings_snapshot=settings,
            previous_state=result["decisions"][index - 1]["runtimeState"],
            inventory_snapshot={**IDENTITY, "dataManifestHash": replay_decision["dataManifestHash"]},
            account_snapshot=account,
        )

        self.assertEqual(replay_decision["decisionId"], direct["decision"]["decision_id"])
        self.assertEqual(replay_decision["signal"], direct["decision"]["signal"])
        self.assertEqual(replay_decision["regime"], direct["decision"]["confirmed_state"]["confirmed_regime"])
        self.assertEqual(replay_decision["runtimeState"], direct["nextRuntimeState"])
        self.assertEqual(replay_decision["pointInTime"]["futureCandlesVisible"], 0)


def settings_snapshot() -> dict:
    return validate_regime_trading_settings_snapshot({"identity": IDENTITY}).as_dict()


def fixture_candles(count: int) -> list[dict[str, float | str]]:
    candles: list[dict[str, float | str]] = []
    price = 100.0
    start = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)
    for index in range(count):
        price += 0.08
        timestamp = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        candles.append(
            {
                "timestamp": timestamp,
                "open": price - 0.03,
                "high": price + 0.12,
                "low": price - 0.12,
                "close": price,
                "volume": 120_000 + index,
            }
        )
    return candles


if __name__ == "__main__":
    unittest.main()
