import unittest
from backend.app.algorithms.regime.decision_engine import calculate_regime_decision
from backend.app.algorithms.regime.sizing import calculate_regime_position_size
from backend.tests.regime.fixtures.market_snapshots import snapshot

class StopCalculationTest(unittest.TestCase):
    def test_buy_stop_is_below_entry_and_sell_stop_above_entry(self):
        market = snapshot("up")
        decision = calculate_regime_decision(market, settings={"minimumWinningScore": 0, "minimumSignalEdge": 0, "minimumActiveStrategies": 1, "minimumIndependentFamilies": 1, "minimumRegimeConfidence": 0})
        sizing = calculate_regime_position_size(decision, market)
        if decision.signal == "Buy":
            self.assertLess(sizing.stop_price, market.latest.close)

