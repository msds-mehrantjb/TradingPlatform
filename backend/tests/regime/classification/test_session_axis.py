import unittest
from backend.app.algorithms.regime.classifier import _session_axis

class SessionAxisTest(unittest.TestCase):
    def test_session_states(self):
        self.assertEqual(_session_axis("2026-07-18T13:00:00Z"), "outside_regular")
        self.assertEqual(_session_axis("2026-07-18T14:45:00Z"), "opening")
        self.assertEqual(_session_axis("2026-07-18T16:00:00Z"), "midday")
        self.assertEqual(_session_axis("2026-07-18T19:00:00Z"), "afternoon")
        self.assertEqual(_session_axis("2026-07-18T20:45:00Z"), "closing")

