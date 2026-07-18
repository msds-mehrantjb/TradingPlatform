import unittest
from backend.tests.regime.strategies.helpers import assert_non_directional_contract

class AtrVolatilityRegimeTest(unittest.TestCase):
    def test_contract(self): assert_non_directional_contract(self, "atr_volatility_regime", "regime_context")

