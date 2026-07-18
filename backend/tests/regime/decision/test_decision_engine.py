import unittest
from backend.app.algorithms.regime.decision_engine import calculate_regime_decision
from backend.tests.regime.fixtures.market_snapshots import snapshot

class DecisionEngineTest(unittest.TestCase):
    def test_decision_engine_is_deterministic_and_backend_owned(self):
        market = snapshot("up")
        first = calculate_regime_decision(market)
        second = calculate_regime_decision(market)
        self.assertEqual(first, second)
        self.assertEqual(first.algorithm_id, "regime")

