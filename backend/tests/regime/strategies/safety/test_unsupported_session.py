import unittest
from backend.tests.regime.fixtures.classification_cases import classification
from backend.tests.regime.strategies.helpers import assert_safety_gate_contract

class UnsupportedSessionTest(unittest.TestCase):
    def test_trigger_and_clear(self): assert_safety_gate_contract(self, "unsupported_session", classification(session="outside_regular"))

