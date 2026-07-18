import unittest
from backend.app.algorithms.regime.classifier import _composite_regime
from backend.app.algorithms.regime.contracts import CANONICAL_MARKET_REGIMES, RegimeAxes

class CompositeRegimesTest(unittest.TestCase):
    def test_authoritative_inventory_has_16_canonical_ids(self):
        self.assertEqual(len(CANONICAL_MARKET_REGIMES), 16)
        self.assertIn("strong_uptrend", CANONICAL_MARKET_REGIMES)
        self.assertIn("extreme_volatility_no_trade", CANONICAL_MARKET_REGIMES)

    def test_composite_mapping_prioritizes_risk_conditions(self):
        self.assertEqual(_composite_regime(RegimeAxes("strong_up", "extreme", "trend", "good", "midday", "none")), "extreme_volatility_no_trade")
        self.assertEqual(_composite_regime(RegimeAxes("strong_up", "normal", "trend", "good", "midday", "blackout")), "event_risk")
        self.assertEqual(_composite_regime(RegimeAxes("strong_up", "normal", "trend", "poor", "midday", "none")), "liquidity_stress")
        self.assertEqual(_composite_regime(RegimeAxes("strong_up", "normal", "trend", "good", "midday", "none")), "strong_uptrend")
        self.assertEqual(_composite_regime(RegimeAxes("neutral", "compressed", "range", "good", "midday", "none")), "low_volatility_quiet")

