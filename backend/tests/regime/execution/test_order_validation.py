import unittest
from dataclasses import replace

from backend.app.algorithms.regime.contracts import RegimeOrderIntent
from backend.app.algorithms.regime.order_validation import validate_regime_order_intent


def valid_intent(**overrides):
    fields = {
        "algorithm_id": "regime",
        "algorithm_version": "regime_algorithm_v3_backend_authoritative",
        "settings_version": "regime_base_settings_v2",
        "decision_id": "decision-1",
        "order_intent_id": "intent-1",
        "symbol": "SPY",
        "side": "Buy",
        "position_effect": "enter_long",
        "quantity": 10,
        "entry_price": 100.0,
        "stop_price": 99.0,
        "target_price": 102.0,
        "risk_dollars": 10.0,
        "regime": "strong_uptrend",
        "confidence": 0.8,
    }
    fields.update(overrides)
    return RegimeOrderIntent(**fields)


class OrderValidationTest(unittest.TestCase):
    def test_accepts_valid_regime_order_intent(self):
        valid, reasons = validate_regime_order_intent(valid_intent(), {"shortEntriesEnabled": False})
        self.assertTrue(valid)
        self.assertEqual(reasons, ())

    def test_rejects_missing_or_unprotected_intent(self):
        valid, reasons = validate_regime_order_intent(None, {})
        self.assertFalse(valid)
        self.assertIn("regime.order_validation.no_order_intent", reasons)

        valid, reasons = validate_regime_order_intent(valid_intent(stop_price=None), {"shortEntriesEnabled": True})
        self.assertFalse(valid)
        self.assertIn("regime.order_validation.missing_protection", reasons)

    def test_rejects_short_when_profile_disables_short_entries(self):
        sell_intent = replace(valid_intent(), side="Sell", position_effect="enter_short")
        valid, reasons = validate_regime_order_intent(sell_intent, {"shortEntriesEnabled": False})
        self.assertFalse(valid)
        self.assertIn("regime.order_validation.short_entries_disabled", reasons)

