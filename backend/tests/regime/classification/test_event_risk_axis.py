import unittest
from backend.app.algorithms.regime.classifier import _event_axis
from backend.tests.regime.fixtures.market_snapshots import snapshot

class EventRiskAxisTest(unittest.TestCase):
    def test_event_risk_states(self):
        self.assertEqual(_event_axis(snapshot("up")), "none")
        self.assertEqual(_event_axis(snapshot("up", context={"scheduledEconomicEvent": {"state": "elevated"}})), "elevated")
        self.assertEqual(_event_axis(snapshot("up", context={"scheduledEconomicEvent": {"state": "blackout"}})), "blackout")

