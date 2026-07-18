from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.algorithms.regime.broker_adapter import build_regime_broker_submission, regime_broker_adapter_inventory
from backend.app.algorithms.regime.global_risk_adapter import (
    RegimeGlobalRiskRequest,
    evaluate_regime_global_risk_request,
    regime_global_risk_adapter_inventory,
)
from backend.app.algorithms.regime.repository import RegimeRepository, regime_repository_inventory
from backend.app.algorithms.regime.service import REGIME_BACKEND_FILE_INVENTORY, RegimeApplicationService, regime_backend_inventory
from backend.app.main import app


ROOT = Path(__file__).resolve().parents[2]


class RegimeBackendBoundaryTest(unittest.TestCase):
    def test_backend_inventory_declares_dedicated_boundary_files(self) -> None:
        expected = (
            "__init__.py",
            "api.py",
            "service.py",
            "repository.py",
            "global_risk_adapter.py",
            "broker_adapter.py",
            "rollout.py",
            "final_acceptance.py",
        )
        inventory = regime_backend_inventory()

        self.assertEqual(REGIME_BACKEND_FILE_INVENTORY, expected)
        self.assertEqual(inventory["files"], expected)
        self.assertTrue(inventory["apiTransportOnly"])
        for file_name in expected:
            self.assertTrue((ROOT / "backend" / "app" / "algorithms" / "regime" / file_name).exists(), file_name)

    def test_repository_service_and_api_expose_same_regime_owned_schema(self) -> None:
        path = ROOT / "backend" / "tests" / "tmp" / "regime_backend_boundary" / f"{uuid4().hex}.sqlite"
        repository = RegimeRepository(f"sqlite:///{path}")
        service = RegimeApplicationService(repository)
        schema = service.persistence_schema()

        self.assertEqual(regime_repository_inventory()["algorithmId"], "regime")
        self.assertIn("regime_decisions", schema["ownedTables"])
        self.assertIn("global_gate_evaluations", schema["sharedAttributedTables"])
        self.assertTrue(schema["inventoryPassed"])

        response = TestClient(app).get("/api/regime/backend/inventory")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["algorithmId"], "regime")
        self.assertEqual(body["files"], list(REGIME_BACKEND_FILE_INVENTORY))
        self.assertEqual(body["repository"]["implementation"], "backend.app.algorithms.regime.persistence.RegimeSqliteRepository")

    def test_global_risk_adapter_can_only_reduce_or_reject_regime_quantity(self) -> None:
        approval = evaluate_regime_global_risk_request(
            RegimeGlobalRiskRequest(
                decision_id="regime-decision-1",
                order_intent_id="regime-intent-1",
                symbol="SPY",
                requested_quantity=100,
                requested_risk_dollars=500.0,
                algorithm_version="regime_algorithm_v2",
                settings_version="regime_base_settings_v1",
                global_quantity_cap=25,
            )
        )
        inventory = regime_global_risk_adapter_inventory()

        self.assertEqual(approval.algorithm_id, "regime")
        self.assertEqual(approval.approved_quantity, 25)
        self.assertFalse(approval.signal_rewritten)
        self.assertFalse(approval.settings_rewritten)
        self.assertFalse(approval.stops_rewritten)
        self.assertFalse(inventory["mayRewriteSignals"])
        self.assertFalse(inventory["mayRewriteSettings"])
        self.assertFalse(inventory["mayRewriteStops"])

    def test_broker_adapter_preserves_attribution_and_requires_global_approval(self) -> None:
        blocked = build_regime_broker_submission(
            decision_id="regime-decision-1",
            order_intent_id="regime-intent-1",
            symbol="spy",
            side="Buy",
            quantity=10,
            algorithm_version="regime_algorithm_v2",
            settings_version="regime_base_settings_v1",
            approved_by_global_risk=False,
        )
        approved = build_regime_broker_submission(
            decision_id="regime-decision-1",
            order_intent_id="regime-intent-1",
            symbol="spy",
            side="Buy",
            quantity=10,
            algorithm_version="regime_algorithm_v2",
            settings_version="regime_base_settings_v1",
            approved_by_global_risk=True,
        )
        inventory = regime_broker_adapter_inventory()

        self.assertEqual(blocked.algorithm_id, "regime")
        self.assertEqual(blocked.symbol, "SPY")
        self.assertFalse(blocked.submit_to_broker)
        self.assertTrue(approved.submit_to_broker)
        self.assertTrue(inventory["requiresGlobalApproval"])
        self.assertFalse(inventory["ownsSignals"])
        self.assertFalse(inventory["ownsSizing"])


if __name__ == "__main__":
    unittest.main()
