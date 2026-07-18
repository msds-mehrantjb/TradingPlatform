import unittest
from backend.app.algorithms.regime.decision_engine import calculate_regime_decision
from backend.app.algorithms.regime.sizing import calculate_regime_position_size
from backend.tests.regime.fixtures.market_snapshots import snapshot

class BuyingPowerCapTest(unittest.TestCase):
    def test_zero_buying_power_blocks_quantity(self):
        market = snapshot("up")
        decision = calculate_regime_decision(market, settings={"minimumWinningScore": 0, "minimumSignalEdge": 0, "minimumActiveStrategies": 1, "minimumIndependentFamilies": 1, "minimumRegimeConfidence": 0})
        sizing = calculate_regime_position_size(decision, market, {"availableBuyingPower": 0, "remainingAlgorithmRiskDollars": 0})
        self.assertEqual(sizing.quantity, 0)

