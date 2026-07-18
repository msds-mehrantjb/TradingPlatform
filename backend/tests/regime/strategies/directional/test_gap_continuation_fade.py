import unittest
from backend.tests.regime.strategies.helpers import assert_directional_strategy_contract

class GapContinuationFadeTest(unittest.TestCase):
    def test_contract(self): assert_directional_strategy_contract(self, "gap_continuation_fade")

