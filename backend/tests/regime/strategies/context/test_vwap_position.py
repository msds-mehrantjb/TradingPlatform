import unittest
from backend.tests.regime.strategies.helpers import assert_non_directional_contract

class VwapPositionTest(unittest.TestCase):
    def test_contract(self): assert_non_directional_contract(self, "vwap_position", "regime_context")

