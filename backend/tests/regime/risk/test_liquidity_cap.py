import unittest
from backend.app.algorithms.regime.decision_engine import calculate_regime_decision
from backend.app.algorithms.regime.sizing import calculate_regime_position_size
from backend.tests.regime.fixtures.market_snapshots import snapshot

class LiquidityCapTest(unittest.TestCase):
    def test_low_volume_caps_quantity(self):
        market = snapshot("up")
        decision = calculate_regime_decision(market, settings={"minimumWinningScore": 0, "minimumSignalEdge": 0, "minimumActiveStrategies": 1, "minimumIndependentFamilies": 1, "minimumRegimeConfidence": 0, "maxParticipationPercent": 0.001})
        sizing = calculate_regime_position_size(decision, market)
        self.assertTrue(any(cap["label"] == "liquidity" for cap in sizing.quantity_caps))

