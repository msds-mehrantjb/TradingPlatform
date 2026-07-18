import unittest
from backend.app.algorithms.regime.strategy_registry import REGIME_STRATEGY_DEFINITIONS

class ContributionCapsTest(unittest.TestCase):
    def test_strategy_weights_are_bounded(self):
        self.assertTrue(all(0 <= item.base_weight <= 0.15 for item in REGIME_STRATEGY_DEFINITIONS))

