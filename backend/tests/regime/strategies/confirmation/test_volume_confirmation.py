import unittest
from backend.tests.regime.strategies.helpers import assert_non_directional_contract

class VolumeConfirmationTest(unittest.TestCase):
    def test_contract(self): assert_non_directional_contract(self, "volume_confirmation", "confirmation")

