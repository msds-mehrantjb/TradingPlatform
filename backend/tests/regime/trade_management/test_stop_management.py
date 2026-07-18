import unittest

from backend.app.algorithms.regime.trade_management import evaluate_regime_exit


class StopManagementTest(unittest.TestCase):
    def test_long_and_short_stops_exit_at_stop_price(self):
        long_exit = evaluate_regime_exit({"side": "Long", "stopPrice": 99, "targetPrice": 105}, {"low": 98, "high": 101, "close": 100}, "strong_uptrend")
        short_exit = evaluate_regime_exit({"side": "Short", "stopPrice": 101, "targetPrice": 95}, {"low": 98, "high": 102, "close": 100}, "strong_downtrend")

        self.assertEqual(long_exit["action"], "exit_long")
        self.assertEqual(long_exit["price"], 99)
        self.assertEqual(short_exit["action"], "exit_short")
        self.assertEqual(short_exit["price"], 101)

