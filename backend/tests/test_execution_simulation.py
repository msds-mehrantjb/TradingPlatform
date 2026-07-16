from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from backend.app.domain.feature_engine import MarketCandle
from backend.app.domain.models import Signal
from backend.app.execution import ExecutionSimulationConfig, RealisticExecutionSimulator
from backend.tests.test_candidate_meta_labeling import order_plan


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


class RealisticExecutionSimulationTest(unittest.TestCase):
    def test_buy_market_entry_uses_ask_side_and_sell_exit_uses_bid_side(self) -> None:
        plan = order_plan(Signal.BUY).model_copy(update={"orderType": "MARKET", "quantity": 10})
        simulator = RealisticExecutionSimulator(ExecutionSimulationConfig(bidAskSpreadDollars=0.04, slippagePerShare=0.01, feesPerShare=0.005))

        execution = simulator.simulate(plan, [candle(1, open=100.0, high=102.0, low=99.8, close=101.0)], NOW)

        self.assertEqual(execution.fill.status, "FILLED")
        self.assertAlmostEqual(execution.fill.averagePrice, 100.03)
        self.assertIn("execution.buy_entry_uses_ask", execution.fill.reasonCodes)
        self.assertIn("execution.sell_exit_uses_bid", execution.exit.reasonCodes)
        self.assertGreater(execution.fill.costs["total"], 0.0)
        self.assertGreater(execution.exit.costs["total"], 0.0)

    def test_stop_limit_nonfill_risk_is_represented(self) -> None:
        plan = order_plan(Signal.BUY).model_copy(
            update={
                "orderType": "STOP_LIMIT",
                "entryPrice": 101.0,
                "limitPrice": 100.5,
                "stopPrice": 99.0,
                "targetPrice": 103.0,
                "quantity": 10,
            }
        )

        execution = RealisticExecutionSimulator().simulate(plan, [candle(1, open=102.0, high=102.5, low=101.2, close=102.0)], NOW)

        self.assertEqual(execution.fill.status, "EXPIRED")
        self.assertEqual(execution.fill.filledQuantity, 0)
        self.assertIn("execution.order_unfilled", execution.fill.reasonCodes)

    def test_partial_fill_uses_volume_participation(self) -> None:
        plan = order_plan(Signal.BUY).model_copy(update={"orderType": "MARKET", "quantity": 100})
        simulator = RealisticExecutionSimulator(ExecutionSimulationConfig(maxVolumeParticipation=0.10))

        execution = simulator.simulate(plan, [candle(1, open=100.0, high=102.0, low=99.5, close=101.0, volume=250)], NOW)

        self.assertEqual(execution.fill.status, "PARTIAL")
        self.assertEqual(execution.fill.filledQuantity, 25)
        self.assertIn("execution.partial_fill_volume_participation", execution.fill.reasonCodes)

    def test_unfilled_limit_order_expires_and_is_not_credited(self) -> None:
        plan = order_plan(Signal.BUY).model_copy(update={"orderType": "LIMIT", "limitPrice": 95.0, "quantity": 10})
        simulator = RealisticExecutionSimulator(ExecutionSimulationConfig(orderExpirationSeconds=60))

        execution = simulator.simulate(plan, [candle(2, open=100.0, high=101.0, low=99.0, close=100.5)], NOW)

        self.assertEqual(execution.fill.status, "EXPIRED")
        self.assertEqual(execution.fill.filledQuantity, 0)
        self.assertIsNone(execution.exit)

    def test_same_bar_target_stop_ambiguity_is_visible_and_stop_first(self) -> None:
        plan = order_plan(Signal.BUY).model_copy(update={"orderType": "MARKET", "quantity": 10})
        simulator = RealisticExecutionSimulator(ExecutionSimulationConfig(bidAskSpreadDollars=0.0, slippagePerShare=0.0))

        execution = simulator.simulate(
            plan,
            [
                candle(1, open=100.0, high=100.2, low=99.8, close=100.0),
                candle(2, open=100.0, high=106.0, low=94.0, close=100.0),
            ],
            NOW,
        )

        self.assertEqual(execution.exit.exitReason, "protective_stop")
        self.assertIn("execution.same_bar_target_stop_ambiguous", execution.exit.reasonCodes)
        self.assertIn("execution.conservative_stop_first", execution.exit.reasonCodes)

    def test_strategy_invalidation_can_exit_before_price_stop(self) -> None:
        plan = order_plan(Signal.BUY).model_copy(
            update={
                "orderType": "MARKET",
                "quantity": 10,
                "stopPrice": 98.0,
                "targetPrice": 105.0,
                "strategyInvalidationPrice": 99.2,
            }
        )
        simulator = RealisticExecutionSimulator(ExecutionSimulationConfig(bidAskSpreadDollars=0.0, slippagePerShare=0.0))

        execution = simulator.simulate(
            plan,
            [
                candle(1, open=100.0, high=100.2, low=99.8, close=100.0),
                candle(2, open=99.6, high=99.8, low=99.0, close=99.1),
            ],
            NOW,
        )

        self.assertEqual(execution.exit.exitReason, "strategy_invalidation")
        self.assertIn("execution.strategy_invalidation_exit", execution.exit.reasonCodes)

    def test_time_stop_exits_before_end_of_day_when_trade_stalls(self) -> None:
        plan = order_plan(Signal.BUY).model_copy(
            update={
                "orderType": "MARKET",
                "quantity": 10,
                "stopPrice": 95.0,
                "targetPrice": 110.0,
                "maximumHoldingMinutes": 2,
            }
        )
        simulator = RealisticExecutionSimulator(ExecutionSimulationConfig(bidAskSpreadDollars=0.0, slippagePerShare=0.0))

        execution = simulator.simulate(
            plan,
            [
                candle(1, open=100.0, high=100.2, low=99.8, close=100.0),
                candle(2, open=100.0, high=100.1, low=99.9, close=100.0),
                candle(3, open=100.0, high=100.1, low=99.9, close=100.0),
            ],
            NOW,
        )

        self.assertEqual(execution.exit.exitReason, "time_stop")
        self.assertIn("execution.time_stop_exit", execution.exit.reasonCodes)

    def test_end_of_day_behavior_remains_replay_default(self) -> None:
        plan = order_plan(Signal.BUY).model_copy(update={"orderType": "MARKET", "quantity": 10, "stopPrice": 95.0, "targetPrice": 110.0})

        execution = RealisticExecutionSimulator(ExecutionSimulationConfig(bidAskSpreadDollars=0.0, slippagePerShare=0.0)).simulate(
            plan,
            [
                candle(1, open=100.0, high=100.2, low=99.8, close=100.0),
                candle(2, open=100.0, high=100.1, low=99.9, close=100.0),
            ],
            NOW,
        )

        self.assertEqual(execution.exit.exitReason, "end_of_day")
        self.assertIn("execution.end_of_day_exit", execution.exit.reasonCodes)

    def test_partial_fill_exit_uses_filled_quantity_for_protective_orders(self) -> None:
        plan = order_plan(Signal.BUY).model_copy(update={"orderType": "MARKET", "quantity": 100, "stopPrice": 99.0, "targetPrice": 103.0})
        simulator = RealisticExecutionSimulator(ExecutionSimulationConfig(maxVolumeParticipation=0.10, bidAskSpreadDollars=0.0, slippagePerShare=0.0))

        execution = simulator.simulate(
            plan,
            [
                candle(1, open=100.0, high=100.2, low=99.8, close=100.0, volume=250),
                candle(2, open=100.0, high=100.2, low=98.5, close=99.0, volume=1000),
            ],
            NOW,
        )

        self.assertEqual(execution.fill.filledQuantity, 25)
        self.assertEqual(execution.exit.exitReason, "protective_stop")
        self.assertAlmostEqual(execution.exit.pnl, -25.0)


def candle(
    minutes: int,
    *,
    open: float,
    high: float,
    low: float,
    close: float,
    volume: int = 1000,
) -> MarketCandle:
    return MarketCandle(
        timestamp=NOW + timedelta(minutes=minutes),
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        tradeCount=volume,
        symbol="SPY",
        timeframe="1Min",
    )


if __name__ == "__main__":
    unittest.main()
