import unittest
from backend.tests.regime.fixtures.classification_cases import classification
from backend.tests.regime.strategies.helpers import assert_safety_gate_contract

class ExcessiveSpreadTest(unittest.TestCase):
    def test_trigger_and_clear(self): assert_safety_gate_contract(self, "excessive_spread", classification(), {"quoteFreshness": {"status": "fresh", "spreadPercent": 0.031}})

