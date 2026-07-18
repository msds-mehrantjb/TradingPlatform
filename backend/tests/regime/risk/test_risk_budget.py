import unittest
from backend.app.algorithms.regime.decision_engine import calculate_regime_decision
from backend.app.algorithms.regime.sizing import calculate_regime_position_size
from backend.tests.regime.fixtures.market_snapshots import snapshot

class RiskBudgetTest(unittest.TestCase):
    def test_hold_or_blocked_decision_sizes_zero(self):
        market = snapshot("up", context={"scheduledEconomicEvent": {"state": "blackout"}})
        decision = calculate_regime_decision(market)
        sizing = calculate_regime_position_size(decision, market)
        self.assertEqual(sizing.quantity, 0)

