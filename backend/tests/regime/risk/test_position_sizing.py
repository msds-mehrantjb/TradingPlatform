import unittest
from backend.app.algorithms.regime.decision_engine import calculate_regime_decision
from backend.app.algorithms.regime.sizing import calculate_regime_position_size
from backend.tests.regime.fixtures.market_snapshots import snapshot

class PositionSizingTest(unittest.TestCase):
    def test_final_quantity_uses_most_restrictive_cap(self):
        market = snapshot("up")
        decision = calculate_regime_decision(market, settings={"minimumWinningScore": 0, "minimumSignalEdge": 0, "minimumActiveStrategies": 1, "minimumIndependentFamilies": 1, "minimumRegimeConfidence": 0})
        sizing = calculate_regime_position_size(decision, market, {"availableBuyingPower": 10_000, "remainingAlgorithmRiskDollars": 100})
        self.assertEqual(sizing.quantity, min(cap["quantity"] for cap in sizing.quantity_caps))

