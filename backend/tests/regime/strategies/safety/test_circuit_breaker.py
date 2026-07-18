import unittest
from backend.tests.regime.fixtures.classification_cases import classification
from backend.tests.regime.strategies.helpers import assert_safety_gate_contract

class CircuitBreakerTest(unittest.TestCase):
    def test_trigger_and_clear(self): assert_safety_gate_contract(self, "circuit_breaker", classification(), {"haltLuldCircuitBreaker": {"newEntriesBlocked": True, "haltState": "none", "circuitBreakerState": "active"}})

