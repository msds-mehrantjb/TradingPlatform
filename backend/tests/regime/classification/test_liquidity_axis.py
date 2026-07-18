import unittest
from backend.app.algorithms.regime.classifier import _liquidity_axis
from backend.tests.regime.fixtures.market_snapshots import snapshot

class LiquidityAxisTest(unittest.TestCase):
    def test_liquidity_states(self):
        self.assertEqual(_liquidity_axis(snapshot("up"), 1.0), "good")
        self.assertEqual(_liquidity_axis(snapshot("up"), 0.6), "acceptable")
        self.assertEqual(_liquidity_axis(snapshot("up"), 0.44), "poor")
        self.assertEqual(_liquidity_axis(snapshot("up", context={"quoteFreshness": {"status": "stale"}}), 1.0), "poor")

