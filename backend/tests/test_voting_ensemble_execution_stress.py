from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.voting_ensemble.backtest import VotingEnsembleBacktestRunner
from backend.app.algorithms.voting_ensemble.backtest_config import VotingEnsembleBacktestConfig
from backend.app.algorithms.voting_ensemble.exit_policy import VotingEnsembleExecutionSimulator, voting_ensemble_execution_config
from backend.app.domain.feature_engine import MarketCandle
from backend.app.execution.simulation import ExecutionSimulationConfig
from backend.tests.test_voting_ensemble_backtest_runner import AlwaysBuyService, candles
from backend.tests.test_voting_ensemble_execution_adapter import order_plan


NOW = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


class VotingEnsembleExecutionStressTest(unittest.TestCase):
    def test_cost_multiplier_scenarios_reduce_net_performance(self) -> None:
        plan = order_plan(quantity=10)
        future = [market_candle(1, low=99.8, high=101.0, close=100.7)]
        baseline = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config()).simulate(plan, future, NOW)
        two_x = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config().model_copy(update={"scenarioName": "two_times_costs", "costMultiplier": 2.0})).simulate(plan, future, NOW)
        three_x = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config().model_copy(update={"scenarioName": "three_times_costs", "costMultiplier": 3.0})).simulate(plan, future, NOW)

        self.assertGreater(two_x.fill.costs["total"], baseline.fill.costs["total"])
        self.assertGreater(three_x.fill.costs["total"], two_x.fill.costs["total"])
        self.assertLessEqual(three_x.exit.pnl, three_x.exit.grossPnl)

    def test_latency_stale_quote_thin_liquidity_halt_and_rejection_scenarios(self) -> None:
        plan = order_plan(quantity=10)
        future = [market_candle(1, volume=100), market_candle(2, volume=100)]

        stale = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config().model_copy(update={"scenarioName": "stale_quotes", "quoteAgeSeconds": 10.0, "maxQuoteAgeSeconds": 5.0})).simulate(plan, future, NOW)
        thin = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config().model_copy(update={"scenarioName": "thin_liquidity", "liquidityHaircut": 0.5, "partialFillRatio": 0.2})).simulate(plan, future, NOW)
        halted = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config().model_copy(update={"scenarioName": "exchange_halt", "exchangeHalt": True})).simulate(plan, future, NOW)
        rejected = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config().model_copy(update={"scenarioName": "broker_rejection", "brokerReject": True})).simulate(plan, future, NOW)
        latency = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config().model_copy(update={"scenarioName": "elevated_latency", "queueLatencySeconds": 600})).simulate(plan, future, NOW)

        self.assertIn("execution.stale_quote", stale.fill.reasonCodes)
        self.assertEqual(thin.fill.status, "PARTIAL")
        self.assertIn("execution.exchange_halt", halted.fill.reasonCodes)
        self.assertIn("execution.broker_rejection", rejected.fill.reasonCodes)
        self.assertIn(latency.fill.status, {"UNFILLED", "EXPIRED"})

    def test_event_volatility_and_opening_spread_expansion_increase_costs(self) -> None:
        plan = order_plan(quantity=10)
        future = [market_candle(1, low=99.8, high=102.5, close=101.5)]
        baseline = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config()).simulate(plan, future, NOW)
        event = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config().model_copy(update={"scenarioName": "high_volatility_event_period", "eventShock": True, "volatilitySlippageMultiplier": 2.0, "spreadWideningMultiplier": 2.0})).simulate(plan, future, NOW)
        opening = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config().model_copy(update={"scenarioName": "opening_session_spread_expansion", "openingSessionSpreadMultiplier": 3.0, "spreadWideningMultiplier": 1.5})).simulate(plan, future, NOW)

        self.assertGreater(event.fill.costs["total"], baseline.fill.costs["total"])
        self.assertGreater(opening.fill.costs["spread"], baseline.fill.costs["spread"])
        self.assertIn("execution.scenario:high_volatility_event_period", event.reasonCodes)

    def test_limit_expiry_cancel_replace_gaps_and_same_bar_ambiguity(self) -> None:
        plan = order_plan(quantity=10)
        expires = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config().model_copy(update={"orderExpirationSeconds": 1})).simulate(
            plan,
            [market_candle(2, open_=101.2, low=101.0, high=101.5, close=101.3), market_candle(10, low=99.8, high=101.0)],
            NOW,
        )
        replaced = VotingEnsembleExecutionSimulator(
            voting_ensemble_execution_config().model_copy(update={"cancelReplaceEnabled": True, "cancelReplaceAfterSeconds": 1, "maxCancelReplaceAttempts": 1, "replacementPriceOffsetBps": 30.0})
        ).simulate(plan, [market_candle(2, open_=100.5, low=100.1, high=101.0, close=100.6)], NOW)
        ambiguous = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config()).simulate(
            plan,
            [market_candle(1, low=99.8, high=101.0), market_candle(2, low=98.0, high=102.0)],
            NOW,
        )
        stop_gap = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config().model_copy(update={"stopGapSlippageMultiplier": 2.0})).simulate(
            plan,
            [market_candle(1, low=99.8, high=101.0), market_candle(2, open_=98.5, low=98.0, high=99.0, close=98.7)],
            NOW,
        )
        target_gap = VotingEnsembleExecutionSimulator(voting_ensemble_execution_config().model_copy(update={"targetGapSlippageMultiplier": 2.0})).simulate(
            plan,
            [market_candle(1, low=99.8, high=101.0), market_candle(2, open_=102.0, low=101.8, high=102.5, close=102.2)],
            NOW,
        )

        self.assertEqual(expires.fill.status, "EXPIRED")
        self.assertIn("execution.cancel_replace_adjusted_limit", replaced.fill.reasonCodes)
        self.assertIn("execution.same_bar_target_stop_ambiguous", ambiguous.exit.reasonCodes)
        self.assertIn("execution.stop_gap", stop_gap.exit.reasonCodes)
        self.assertIn("execution.target_gap", target_gap.exit.reasonCodes)

    def test_backtest_reports_gross_net_stress_and_blocks_promotion_on_net_failure(self) -> None:
        result = VotingEnsembleBacktestRunner(
            service=AlwaysBuyService(),
            config=VotingEnsembleBacktestConfig(warmupCandles=3, includeDecisionRecords=True),
        ).run(symbol="SPY", spy_1m_candles=candles(8), timeframe="1Min")

        self.assertIn("grossTotalPnl", result)
        self.assertIn("netTotalPnl", result)
        stress = result["costStress"]
        self.assertTrue(stress["scenarioDriven"])
        self.assertIn("three_times_costs", stress["scenarioResults"])
        self.assertIn("netPerformanceByStrategy", stress)
        self.assertIn("netPerformanceByFamily", stress)
        self.assertIn("netPerformanceByRegime", stress)
        self.assertIn("netPerformanceBySession", stress)
        self.assertEqual(stress["promotionGate"]["basis"], "net_performance_after_estimated_costs")


def market_candle(
    minutes: int,
    *,
    open_: float = 100.0,
    low: float = 99.8,
    high: float = 101.0,
    close: float = 100.5,
    volume: float = 1000.0,
) -> MarketCandle:
    return MarketCandle(
        timestamp=NOW + timedelta(minutes=minutes),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        symbol="SPY",
        timeframe="1Min",
    )


if __name__ == "__main__":
    unittest.main()
