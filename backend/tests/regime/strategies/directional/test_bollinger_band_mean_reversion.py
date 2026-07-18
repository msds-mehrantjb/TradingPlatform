import unittest
from backend.tests.regime.strategies.helpers import assert_alias_contract, assert_directional_strategy_contract

class BollingerBandMeanReversionTest(unittest.TestCase):
    def test_contract(self): assert_directional_strategy_contract(self, "bollinger_band_mean_reversion")
    def test_alias(self): assert_alias_contract(self, "bollinger_atr_reversion", "bollinger_band_mean_reversion")

