import unittest
from backend.tests.regime.strategies.helpers import assert_non_directional_contract

class AdxTrendStrengthTest(unittest.TestCase):
    def test_contract(self): assert_non_directional_contract(self, "adx_trend_strength", "confirmation")

