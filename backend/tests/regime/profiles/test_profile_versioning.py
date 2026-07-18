import unittest
from backend.app.algorithms.regime.configuration import validate_regime_settings
from backend.app.algorithms.regime.dynamic_profile import resolve_effective_regime_profile

class ProfileVersioningTest(unittest.TestCase):
    def test_profile_id_contains_backend_version(self):
        profile = resolve_effective_regime_profile(validate_regime_settings(), "strong_uptrend")
        self.assertEqual(profile["profileId"], "strong_uptrend:regime_profile_matrix_v2_backend")

