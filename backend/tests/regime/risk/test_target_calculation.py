import unittest
from backend.app.algorithms.regime.decision_engine import calculate_regime_decision
from backend.app.algorithms.regime.sizing import calculate_regime_position_size
from backend.tests.regime.fixtures.market_snapshots import snapshot

class TargetCalculationTest(unittest.TestCase):
    def test_target_distance_uses_reward_risk(self):
        market = snapshot("up")
        decision = calculate_regime_decision(market, settings={"minimumWinningScore": 0, "minimumSignalEdge": 0, "minimumActiveStrategies": 1, "minimumIndependentFamilies": 1, "minimumRegimeConfidence": 0, "takeProfitR": 2})
        sizing = calculate_regime_position_size(decision, market)
        if sizing.target_price and sizing.stop_price:
            self.assertGreater(abs(sizing.target_price - market.latest.close), abs(market.latest.close - sizing.stop_price))

