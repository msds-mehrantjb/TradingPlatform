import unittest
from backend.tests.regime.strategies.helpers import assert_directional_strategy_contract

class IntradayBreakoutTest(unittest.TestCase):
    def test_contract(self): assert_directional_strategy_contract(self, "intraday_breakout")

