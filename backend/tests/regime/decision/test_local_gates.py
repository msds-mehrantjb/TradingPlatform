import unittest
from backend.app.algorithms.regime.configuration import validate_regime_settings
from backend.app.algorithms.regime.local_gates import evaluate_regime_local_gates
from backend.tests.regime.fixtures.classification_cases import classification

class LocalGatesTest(unittest.TestCase):
    def test_each_gate_trigger_has_reason_and_clear_case(self):
        settings = validate_regime_settings()
        blocked = evaluate_regime_local_gates({"activeStrategyCount": 0, "activeFamilyCount": 0, "winningScore": 0, "winningEdge": 0, "abstentionRate": 1}, classification(confidence=0.1), None, settings)
        self.assertIn("regime.local_gate.minimum_active_strategies", blocked)
        clear = evaluate_regime_local_gates({"activeStrategyCount": 3, "activeFamilyCount": 2, "winningScore": 0.8, "winningEdge": 0.3, "abstentionRate": 0}, classification(confidence=0.8), None, settings)
        self.assertEqual(clear, ())

