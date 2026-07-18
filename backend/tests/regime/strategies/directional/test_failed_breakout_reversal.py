import unittest
from backend.tests.regime.strategies.helpers import assert_alias_contract, assert_directional_strategy_contract

class FailedBreakoutReversalTest(unittest.TestCase):
    def test_contract(self): assert_directional_strategy_contract(self, "failed_breakout_reversal")
    def test_alias(self): assert_alias_contract(self, "failed_breakout_strategy", "failed_breakout_reversal")

