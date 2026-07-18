import unittest
from backend.app.algorithms.regime.configuration import validate_regime_settings

class UnknownStateRecoveryTest(unittest.TestCase):
    def test_maximum_unknown_bars_is_clamped(self):
        self.assertEqual(validate_regime_settings({"maximumUnknownBars": -10})["maximumUnknownBars"], 0)

