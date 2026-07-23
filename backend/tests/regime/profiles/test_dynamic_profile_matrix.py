import unittest

from backend.app.algorithms.regime.configuration import validate_regime_settings
from backend.app.algorithms.regime.dynamic_profile import resolve_effective_regime_profile


class DynamicProfileMatrixTest(unittest.TestCase):
    def test_every_representative_regime_has_safe_profile_result(self):
        settings = validate_regime_settings()
        regimes = (
            "strong_uptrend",
            "weak_uptrend",
            "range_bound",
            "opening_breakout",
            "intraday_expansion",
            "choppy_mixed",
            "event_risk",
            "low_volatility_quiet",
            "unknown",
        )
        for regime in regimes:
            with self.subTest(regime=regime):
                profile = resolve_effective_regime_profile(settings, regime)
                self.assertIn("profileId", profile)
                self.assertIn("profilePolicy", profile)
                self.assertLessEqual(profile["baseRiskPercent"], settings["baseRiskPercent"])
                self.assertIn("orderType", profile)
                self.assertIn("entryTimeoutSeconds", profile)
                self.assertIn("stopGeometry", profile)
                self.assertIn("targetGeometry", profile)
                self.assertIn("maximumHoldingMinutes", profile)

    def test_strong_trend_prefers_pullbacks_longer_hold_and_trailing_exits(self):
        profile = resolve_effective_regime_profile(validate_regime_settings(), "strong_uptrend")

        self.assertEqual(profile["entryStyle"], "pullback_continuation")
        self.assertIn("trend", profile["preferredStrategyFamilies"])
        self.assertTrue(profile["trailingExitsEnabled"])
        self.assertTrue(profile["pyramidingEnabled"])
        self.assertGreaterEqual(profile["maximumHoldingMinutes"], 45)

    def test_weak_trend_reduces_size_and_requires_stricter_confirmation(self):
        profile = resolve_effective_regime_profile(validate_regime_settings(), "weak_uptrend")

        self.assertLessEqual(profile["baseRiskPercent"], 0.12)
        self.assertLessEqual(profile["maxPositionPercent"], 25.0)
        self.assertGreaterEqual(profile["minimumWinningScore"], 0.70)
        self.assertGreaterEqual(profile["minimumIndependentFamilies"], 3)
        self.assertFalse(profile["pyramidingEnabled"])

    def test_range_profile_disables_breakout_chasing_and_uses_smaller_targets(self):
        profile = resolve_effective_regime_profile(validate_regime_settings(), "range_bound")

        self.assertIn("mean_reversion", profile["preferredStrategyFamilies"])
        self.assertIn("breakout", profile["disabledStrategyFamilies"])
        self.assertLessEqual(profile["takeProfitR"], 1.10)
        self.assertEqual(profile["orderType"], "limit")

    def test_opening_breakout_has_short_validity_and_strict_execution_caps(self):
        profile = resolve_effective_regime_profile(validate_regime_settings(), "opening_breakout")

        self.assertEqual(profile["orderType"], "stop_limit")
        self.assertLessEqual(profile["entryTimeoutSeconds"], 30)
        self.assertLessEqual(profile["validityWindowSeconds"], 300)
        self.assertLessEqual(profile["maxSpreadPercent"], 0.0015)
        self.assertLessEqual(profile["maximumSlippageBps"], 8.0)

    def test_intraday_expansion_reduces_size_widens_stop_and_requires_edge(self):
        profile = resolve_effective_regime_profile(validate_regime_settings(), "intraday_expansion")

        self.assertLessEqual(profile["baseRiskPercent"], 0.15)
        self.assertGreaterEqual(profile["atrStopMultiplier"], 2.5)
        self.assertGreaterEqual(profile["minimumNetExpectedEdge"], 0.35)

    def test_choppy_and_event_profiles_block_new_entries(self):
        for regime in ("choppy_mixed", "event_risk"):
            with self.subTest(regime=regime):
                profile = resolve_effective_regime_profile(validate_regime_settings(), regime)

                self.assertTrue(profile["noNewEntries"])
                self.assertEqual(profile["baseRiskPercent"], 0.0)
                self.assertEqual(profile["maxPositionPercent"], 0.0)
                self.assertFalse(profile["pyramidingEnabled"])

    def test_low_volatility_quiet_requires_costs_to_be_small_share_of_edge(self):
        profile = resolve_effective_regime_profile(validate_regime_settings(), "low_volatility_quiet")

        self.assertGreaterEqual(profile["minimumNetExpectedEdge"], 0.35)
        self.assertLessEqual(profile["maxExecutionCostToEdgeRatio"], 0.20)
        self.assertLessEqual(profile["baseRiskPercent"], 0.12)


if __name__ == "__main__":
    unittest.main()
