import unittest
from backend.app.algorithms.regime.hysteresis import confirm_regime_transition
from backend.tests.regime.fixtures.classification_cases import classification

class HysteresisTest(unittest.TestCase):
    def test_initial_confirmed_regime_and_repeated_hold(self):
        first = confirm_regime_transition(classification(raw_regime="strong_uptrend"))
        second = confirm_regime_transition(classification(raw_regime="strong_uptrend"), first)
        self.assertEqual(first.confirmed_regime, "strong_uptrend")
        self.assertEqual(second.transition_reason, "confirmed_regime_held")

