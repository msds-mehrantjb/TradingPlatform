import unittest
from backend.app.algorithms.regime.router import route_regime_strategies
from backend.tests.regime.fixtures.classification_cases import classification
from backend.tests.regime.fixtures.market_snapshots import snapshot

class RouterTest(unittest.TestCase):
    def test_risk_off_regime_skips_directional_strategies(self):
        routing = route_regime_strategies(snapshot("up"), classification(raw_regime="event_risk", event_risk="blackout"))
        self.assertGreater(len(routing["skippedStrategies"]), 0)
        self.assertEqual(routing["selectedStrategyIds"], ())

