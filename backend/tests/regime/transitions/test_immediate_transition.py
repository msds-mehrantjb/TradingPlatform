import unittest
from backend.app.algorithms.regime.hysteresis import confirm_regime_transition
from backend.tests.regime.fixtures.classification_cases import classification

class ImmediateTransitionTest(unittest.TestCase):
    def test_risk_off_transition_is_immediate(self):
        state = confirm_regime_transition(classification(raw_regime="strong_uptrend"))
        risk = confirm_regime_transition(classification(raw_regime="event_risk", event_risk="blackout"), state, {"confirmationBars": 99})
        self.assertEqual(risk.confirmed_regime, "event_risk")
        self.assertEqual(risk.transition_reason, "risk_off_immediate")

