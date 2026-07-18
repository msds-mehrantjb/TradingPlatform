import unittest

from backend.app.algorithms.regime.trade_management import evaluate_regime_exit


class RegimeTransitionExitTest(unittest.TestCase):
    def test_risk_off_regime_reduces_or_exits_open_position(self):
        result = evaluate_regime_exit({"side": "Long", "stopPrice": 95, "targetPrice": 110}, {"low": 99, "high": 101, "close": 100}, "event_risk")

        self.assertEqual(result["action"], "reduce_or_exit")
        self.assertIn("regime.exit.risk_off_regime", result["reasonCodes"])

