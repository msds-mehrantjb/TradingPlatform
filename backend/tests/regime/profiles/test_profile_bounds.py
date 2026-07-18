import unittest
from backend.app.algorithms.regime.configuration import validate_regime_settings

class ProfileBoundsTest(unittest.TestCase):
    def test_invalid_settings_are_clamped(self):
        settings = validate_regime_settings({"baseRiskPercent": 99, "maxPositionPercent": 999, "minimumWinningScore": -1})
        self.assertEqual(settings["baseRiskPercent"], 5.0)
        self.assertEqual(settings["maxPositionPercent"], 100.0)
        self.assertEqual(settings["minimumWinningScore"], 0.0)

