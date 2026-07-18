import unittest

from backend.app.algorithms.regime.broker_adapter import build_regime_broker_submission, regime_broker_adapter_inventory


class BrokerBoundaryTest(unittest.TestCase):
    def test_broker_submission_requires_global_approval_and_preserves_attribution(self):
        rejected = build_regime_broker_submission(
            decision_id="decision-1",
            order_intent_id="intent-1",
            symbol="spy",
            side="Buy",
            quantity=10,
            algorithm_version="regime_algorithm_v3_backend_authoritative",
            settings_version="regime_base_settings_v2",
            approved_by_global_risk=False,
        )

        self.assertEqual(rejected.algorithm_id, "regime")
        self.assertEqual(rejected.symbol, "SPY")
        self.assertFalse(rejected.submit_to_broker)

        approved = build_regime_broker_submission(
            decision_id="decision-1",
            order_intent_id="intent-1",
            symbol="SPY",
            side="Buy",
            quantity=10,
            algorithm_version="regime_algorithm_v3_backend_authoritative",
            settings_version="regime_base_settings_v2",
            approved_by_global_risk=True,
        )
        self.assertTrue(approved.submit_to_broker)

    def test_inventory_marks_broker_as_submission_only(self):
        inventory = regime_broker_adapter_inventory()
        self.assertEqual(inventory["algorithmId"], "regime")
        self.assertTrue(inventory["requiresGlobalApproval"])
        self.assertFalse(inventory["ownsSignals"])
        self.assertFalse(inventory["ownsSizing"])

