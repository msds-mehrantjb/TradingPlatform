import unittest
from backend.app.algorithms.regime.classifier import _direction_axis

class DirectionAxisTest(unittest.TestCase):
    def test_all_direction_states(self):
        self.assertEqual(_direction_axis(5, 0), "strong_up")
        self.assertEqual(_direction_axis(3, 1), "weak_up")
        self.assertEqual(_direction_axis(2, 2), "neutral")
        self.assertEqual(_direction_axis(1, 3), "weak_down")
        self.assertEqual(_direction_axis(0, 5), "strong_down")

