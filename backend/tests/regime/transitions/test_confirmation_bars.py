import unittest
from backend.app.algorithms.regime.hysteresis import confirm_regime_transition
from backend.tests.regime.fixtures.classification_cases import classification

class ConfirmationBarsTest(unittest.TestCase):
    def test_candidate_waits_for_confirmation_bars(self):
        state = confirm_regime_transition(classification(raw_regime="strong_uptrend"))
        settings = {"confirmationBars": 2, "immediateConfidenceThreshold": 0.9, "minimumDwellBars": 0}
        candidate = confirm_regime_transition(classification(raw_regime="weak_downtrend", direction="weak_down", confidence=0.4), state, settings)
        confirmed = confirm_regime_transition(classification(raw_regime="weak_downtrend", direction="weak_down", confidence=0.4), candidate, settings)
        self.assertEqual(candidate.confirmed_regime, "strong_uptrend")
        self.assertEqual(confirmed.confirmed_regime, "weak_downtrend")
