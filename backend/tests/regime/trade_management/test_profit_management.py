import unittest

from backend.app.algorithms.regime.trade_management import evaluate_regime_exit


class ProfitManagementTest(unittest.TestCase):
    def test_targets_exit_at_target_price(self):
        long_exit = evaluate_regime_exit({"side": "Long", "stopPrice": 99, "targetPrice": 105}, {"low": 100, "high": 106, "close": 104}, "strong_uptrend")
        short_exit = evaluate_regime_exit({"side": "Short", "stopPrice": 101, "targetPrice": 95}, {"low": 94, "high": 100, "close": 96}, "strong_downtrend")

        self.assertEqual(long_exit["reasonCodes"], ("regime.exit.target_hit",))
        self.assertEqual(long_exit["price"], 105)
        self.assertEqual(short_exit["reasonCodes"], ("regime.exit.target_hit",))
        self.assertEqual(short_exit["price"], 95)

