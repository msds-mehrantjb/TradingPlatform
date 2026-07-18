import unittest

from backend.app.algorithms.regime.backtest.metrics import calculate_backtest_metrics


class BacktestMetricsTest(unittest.TestCase):
    def test_metrics_include_trade_and_no_trade_rates(self):
        metrics = calculate_backtest_metrics(
            [{"pnl": 100}, {"pnl": -50}],
            [{"signal": "Hold"}, {"signal": "Buy"}],
            10_000,
        )

        self.assertEqual(metrics["tradeCount"], 2)
        self.assertEqual(metrics["decisionCount"], 2)
        self.assertEqual(metrics["netProfit"], 50)
        self.assertEqual(metrics["noTradePercentage"], 0.5)

