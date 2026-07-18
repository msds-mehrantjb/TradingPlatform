import unittest

from backend.app.algorithms.regime.global_risk_adapter import RegimeGlobalRiskRequest, evaluate_regime_global_risk_request, regime_global_risk_adapter_inventory


class GlobalRiskBoundaryTest(unittest.TestCase):
    def test_global_risk_can_reduce_or_reject_but_not_rewrite_regime_state(self):
        approval = evaluate_regime_global_risk_request(
            RegimeGlobalRiskRequest(
                decision_id="decision-1",
                order_intent_id="intent-1",
                symbol="SPY",
                requested_quantity=100,
                requested_risk_dollars=250,
                algorithm_version="regime_algorithm_v3_backend_authoritative",
                settings_version="regime_base_settings_v2",
                global_quantity_cap=25,
            )
        )

        self.assertEqual(approval.algorithm_id, "regime")
        self.assertEqual(approval.approved_quantity, 25)
        self.assertFalse(approval.signal_rewritten)
        self.assertFalse(approval.settings_rewritten)
        self.assertFalse(approval.stops_rewritten)

    def test_inventory_declares_shared_boundary(self):
        inventory = regime_global_risk_adapter_inventory()
        self.assertEqual(inventory["algorithmId"], "regime")
        self.assertFalse(inventory["mayRewriteSignals"])
        self.assertIn("decision_id", inventory["requiresAttribution"])

