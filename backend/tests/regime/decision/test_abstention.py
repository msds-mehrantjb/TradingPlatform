import unittest
from backend.app.algorithms.regime.local_gates import evaluate_regime_local_gates
from backend.app.algorithms.regime.configuration import validate_regime_settings
from backend.tests.regime.fixtures.classification_cases import classification

class AbstentionTest(unittest.TestCase):
    def test_maximum_abstention_blocks(self):
        blockers = evaluate_regime_local_gates({"activeStrategyCount": 3, "activeFamilyCount": 2, "winningScore": 0.8, "winningEdge": 0.3, "abstentionRate": 0.99}, classification(), None, validate_regime_settings())
        self.assertIn("regime.local_gate.maximum_abstention_rate", blockers)

