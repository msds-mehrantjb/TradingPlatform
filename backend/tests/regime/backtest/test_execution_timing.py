import unittest

from backend.app.algorithms.regime.backtest.execution import simulate_next_bar_fill


class BacktestExecutionTimingTest(unittest.TestCase):
    def test_fill_uses_next_bar_open_and_costs(self):
        fill = simulate_next_bar_fill({"quantity": 25}, {"open": 101.25, "close": 102.0}, cost_per_share=0.03)

        self.assertEqual(fill["filledQuantity"], 25)
        self.assertEqual(fill["entryPrice"], 101.25)
        self.assertEqual(fill["fees"], 0.75)
        self.assertEqual(fill["slippage"], 0.75)

