import unittest
from backend.app.algorithms.regime.classifier import _structure_axis
from backend.tests.regime.fixtures.market_snapshots import snapshot

class StructureAxisTest(unittest.TestCase):
    def test_trend_range_and_breakout_states(self):
        self.assertEqual(_structure_axis(snapshot("up"), 5, 0), "breakout")
        self.assertEqual(_structure_axis(snapshot("flat"), 2, 2), "range")
        self.assertIn(_structure_axis(snapshot("down"), 0, 5), {"breakout", "trend"})

