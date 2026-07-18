import unittest
from copy import deepcopy
from backend.app.algorithms.regime.configuration import validate_regime_settings
from backend.app.algorithms.regime.dynamic_profile import resolve_effective_regime_profile

class ImmutableDefaultsTest(unittest.TestCase):
    def test_baseline_settings_are_not_mutated(self):
        settings = validate_regime_settings()
        before = deepcopy(settings)
        resolve_effective_regime_profile(settings, "event_risk")
        self.assertEqual(settings, before)

