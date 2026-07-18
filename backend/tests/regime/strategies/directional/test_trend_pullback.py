import unittest
from backend.tests.regime.strategies.helpers import assert_alias_contract, assert_directional_strategy_contract

class TrendPullbackTest(unittest.TestCase):
    def test_contract(self): assert_directional_strategy_contract(self, "trend_pullback")
    def test_alias(self): assert_alias_contract(self, "first_pullback_after_open", "trend_pullback")

