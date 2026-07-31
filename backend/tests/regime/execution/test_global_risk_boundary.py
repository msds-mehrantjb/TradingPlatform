import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.regime.global_risk_adapter import (
    RegimeGlobalRiskRequest,
    evaluate_regime_global_risk_request,
    regime_global_risk_adapter_inventory,
    release_regime_global_risk_reservation,
)
from backend.app.risk.manager import GLOBAL_PORTFOLIO_RISK_MANAGER_VERSION, GlobalPortfolioRiskManager


NOW = datetime(2026, 1, 5, 15, 30, tzinfo=UTC)


class GlobalRiskBoundaryTest(unittest.TestCase):
    def test_global_risk_can_reduce_but_not_rewrite_regime_state(self):
        manager = GlobalPortfolioRiskManager()
        approval = evaluate_regime_global_risk_request(_request(), manager=manager)

        self.assertEqual(approval.algorithm_id, "regime")
        self.assertEqual(approval.approved_quantity, 25)
        self.assertEqual(approval.reservation_id, manager.reservations.all()[0].reservationId)
        self.assertEqual(approval.account_risk_snapshot_version, GLOBAL_PORTFOLIO_RISK_MANAGER_VERSION)
        self.assertIn("regime.global_risk_adapter.quantity_reduced", approval.reason_codes)
        self.assertFalse(approval.signal_rewritten)
        self.assertFalse(approval.settings_rewritten)
        self.assertFalse(approval.stops_rewritten)

    def test_global_risk_can_reject_and_reservation_can_be_released(self):
        manager = GlobalPortfolioRiskManager()
        approval = evaluate_regime_global_risk_request(_request(market_snapshot={"tradingHalt": True}), manager=manager)

        self.assertTrue(approval.rejected)
        self.assertEqual(approval.approved_quantity, 0)
        self.assertIsNone(approval.reservation_id)
        self.assertIn("global_risk.failed.trading_halt", approval.reason_codes)
        self.assertFalse(release_regime_global_risk_reservation(approval.reservation_id, manager=manager))

        reserved = evaluate_regime_global_risk_request(_request(account_snapshot={"equity": 100_000, "availableBuyingPower": 10_000}), manager=manager)
        self.assertTrue(release_regime_global_risk_reservation(reserved.reservation_id, manager=manager))
        self.assertEqual(manager.reservations.all()[-1].status, "released")

    def test_missing_trusted_request_fields_fail_closed(self):
        approval = evaluate_regime_global_risk_request(_request(account_snapshot={}), manager=GlobalPortfolioRiskManager())

        self.assertTrue(approval.rejected)
        self.assertEqual(approval.approved_quantity, 0)
        self.assertIn("regime.global_risk_adapter.request_invalid", approval.reason_codes)

    def test_inventory_declares_shared_boundary(self):
        inventory = regime_global_risk_adapter_inventory()
        self.assertEqual(inventory["algorithmId"], "regime")
        self.assertFalse(inventory["mayRewriteSignals"])
        self.assertIn("decision_id", inventory["requiresAttribution"])
        self.assertIn("release_on_cancellation", inventory["reservationLifecycle"])


def _request(**overrides) -> RegimeGlobalRiskRequest:
    account_snapshot = overrides.pop("account_snapshot", {"accountSnapshotId": "acct-1", "equity": 100_000, "availableBuyingPower": 2_500, "observedAt": NOW})
    market_snapshot = overrides.pop("market_snapshot", {})
    payload = {
        "algorithm_id": "regime",
        "decision_id": "decision-1",
        "order_intent_id": "intent-1",
        "symbol": "SPY",
        "side": "Buy",
        "requested_quantity": 100,
        "requested_risk_dollars": 250,
        "stop_price": 99.0,
        "target_price": 102.0,
        "estimated_notional": 10_000,
        "existing_regime_exposure": {"quantity": 0, "marketValue": 0.0},
        "existing_account_exposure": {"positions": (), "pendingOrders": ()},
        "algorithm_version": "regime_algorithm_v3_backend_authoritative",
        "settings_version": "regime_base_settings_v2",
        "expiration_timestamp": NOW + timedelta(minutes=5),
        "idempotency_key": "regime:paper:SPY:2026-01-05T15:30:00Z:regime_algorithm_v3_backend_authoritative:regime_base_settings_v2",
        "entry_price": 100.0,
        "account_snapshot": account_snapshot,
        "market_snapshot": {"candleTimestamp": NOW, "quoteTimestamp": NOW, "spreadPercent": 0.01, "oneMinuteVolume": 100_000, "estimatedSlippagePercent": 0.01, **market_snapshot},
        "generated_at": NOW,
        "market_data_timestamp": NOW,
    }
    payload.update(overrides)
    return RegimeGlobalRiskRequest(**payload)
