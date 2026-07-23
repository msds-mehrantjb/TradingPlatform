import unittest
from backend.app.algorithms.regime.classifier import _event_axis, _event_evidence
from backend.tests.regime.fixtures.market_snapshots import snapshot

class EventRiskAxisTest(unittest.TestCase):
    def test_event_risk_states(self):
        self.assertEqual(_event_axis(snapshot("up")), "none")
        self.assertEqual(_event_axis(snapshot("up", context={"scheduledEconomicEvent": {"state": "elevated"}})), "elevated")
        self.assertEqual(_event_axis(snapshot("up", context={"scheduledEconomicEvent": {"state": "blackout"}})), "blackout")
        self.assertEqual(_event_axis(snapshot("up", context={"haltLuldCircuitBreaker": {"newEntriesBlocked": True}})), "blackout")

    def test_event_evidence_covers_macro_and_unscheduled_halt_luld(self):
        market = snapshot(
            "up",
            context={
                "scheduledEconomicEvent": {"state": "soon", "eventType": "CPI"},
                "haltLuldCircuitBreaker": {"newEntriesBlocked": True, "haltState": "paused"},
            },
        )

        evidence = _event_evidence(market)

        self.assertEqual(evidence["eventType"], "cpi")
        self.assertTrue(evidence["isScheduledMacroEvent"])
        self.assertTrue(evidence["newEntriesBlocked"])
        self.assertIn("regime.event.unscheduled_halt_luld", evidence["reasonCodes"])
