import unittest
from backend.app.algorithms.regime.configuration import validate_regime_settings
from backend.app.algorithms.regime.dynamic_profile import resolve_effective_regime_profile

class DynamicProfileMatrixTest(unittest.TestCase):
    def test_every_canonical_family_has_safe_profile_result(self):
        settings = validate_regime_settings()
        for regime in ("strong_uptrend", "high_volatility_trend", "low_volatility_quiet", "event_risk", "liquidity_stress", "unknown"):
            with self.subTest(regime=regime):
                profile = resolve_effective_regime_profile(settings, regime)
                self.assertIn("profileId", profile)
                self.assertLessEqual(profile["baseRiskPercent"], settings["baseRiskPercent"])

