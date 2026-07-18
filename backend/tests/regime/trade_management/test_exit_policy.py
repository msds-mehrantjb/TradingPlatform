import unittest

from backend.app.algorithms.regime.trade_management import evaluate_regime_exit


class ExitPolicyTest(unittest.TestCase):
    def test_empty_position_holds(self):
        self.assertEqual(evaluate_regime_exit(None, {"close": 100}, "strong_uptrend")["action"], "hold")

