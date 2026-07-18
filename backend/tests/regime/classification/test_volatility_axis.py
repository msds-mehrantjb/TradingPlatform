import unittest
from backend.app.algorithms.regime.classifier import _volatility_axis

class VolatilityAxisTest(unittest.TestCase):
    def test_all_volatility_states_and_boundaries(self):
        self.assertEqual(_volatility_axis(0.0039, None), "compressed")
        self.assertEqual(_volatility_axis(0.005, None), "normal")
        self.assertEqual(_volatility_axis(0.018, None), "expanded")
        self.assertEqual(_volatility_axis(0.035, None), "extreme")

