import unittest
from backend.tests.regime.strategies.helpers import assert_directional_strategy_contract

class OpeningRangeBreakoutTest(unittest.TestCase):
    def test_contract(self): assert_directional_strategy_contract(self, "opening_range_breakout")

