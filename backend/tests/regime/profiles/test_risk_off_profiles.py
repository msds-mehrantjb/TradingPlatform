import unittest
from backend.app.algorithms.regime.configuration import validate_regime_settings
from backend.app.algorithms.regime.dynamic_profile import resolve_effective_regime_profile

class RiskOffProfilesTest(unittest.TestCase):
    def test_risk_off_regimes_zero_new_entry_risk(self):
        for regime in ("event_risk", "liquidity_stress", "extreme_volatility_no_trade"):
            profile = resolve_effective_regime_profile(validate_regime_settings(), regime)
            self.assertEqual(profile["baseRiskPercent"], 0.0)
            self.assertEqual(profile["maxPositionPercent"], 0.0)

