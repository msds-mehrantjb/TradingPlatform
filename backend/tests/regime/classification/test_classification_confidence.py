import unittest
from backend.app.algorithms.regime.classifier import _confidence

class ClassificationConfidenceTest(unittest.TestCase):
    def test_confidence_bounds_missing_inputs_and_no_trade(self):
        self.assertGreater(_confidence(5, 0, (), ()), _confidence(2, 1, ("atr", "rsi"), ()))
        self.assertGreaterEqual(_confidence(0, 0, (), ("regime.safety.event_blackout",)), 0.7)
        self.assertLessEqual(_confidence(99, 0, (), ()), 1.0)

