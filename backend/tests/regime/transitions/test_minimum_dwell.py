import unittest
from backend.app.algorithms.regime.configuration import validate_regime_settings

class MinimumDwellTest(unittest.TestCase):
    def test_minimum_dwell_setting_is_validated(self):
        self.assertEqual(validate_regime_settings({"minimumDwellBars": -1})["minimumDwellBars"], 0)

