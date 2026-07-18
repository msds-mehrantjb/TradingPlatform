import unittest
from backend.tests.regime.strategies.helpers import assert_directional_strategy_contract

class VwapMeanReversionTest(unittest.TestCase):
    def test_contract(self): assert_directional_strategy_contract(self, "vwap_mean_reversion")

