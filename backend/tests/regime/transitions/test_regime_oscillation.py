import unittest
from backend.app.algorithms.regime.hysteresis import confirm_regime_transition
from backend.tests.regime.fixtures.classification_cases import classification

class RegimeOscillationTest(unittest.TestCase):
    def test_candidate_replacement_resets_counter(self):
        state = confirm_regime_transition(classification(raw_regime="strong_uptrend"))
        first = confirm_regime_transition(classification(raw_regime="weak_downtrend", direction="weak_down", confidence=0.4), state, {"confirmationBars": 3, "immediateConfidenceThreshold": 0.9})
        second = confirm_regime_transition(classification(raw_regime="range_bound", direction="neutral", confidence=0.4), first, {"confirmationBars": 3, "immediateConfidenceThreshold": 0.9})
        self.assertEqual(second.candidate_regime, "range_bound")
        self.assertEqual(second.candidate_confirmation_count, 1)

