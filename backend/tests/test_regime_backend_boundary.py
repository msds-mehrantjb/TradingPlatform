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
from backend.app.algorithms.regime.service import (
    REGIME_ALLOWED_SHARED_COMPONENTS,
    REGIME_BACKEND_FILE_INVENTORY,
    REGIME_NEVER_SHARED_COMPONENTS,
    RegimeApplicationService,
    regime_backend_inventory,
)
from backend.app.main import app


ROOT = Path(__file__).resolve().parents[2]


class RegimeBackendBoundaryTest(unittest.TestCase):
    def test_backend_inventory_declares_dedicated_boundary_files(self) -> None:
        expected = (
            "__init__.py",
            "api.py",
            "contracts.py",
            "configuration.py",
            "market_snapshot.py",
            "indicators.py",
            "classification_axes.py",
            "classifier.py",
            "hysteresis.py",
            "transitions.py",
            "strategy_registry.py",
            "router.py",
            "family_aggregation.py",
            "decision_engine.py",
            "local_gates.py",
            "dynamic_profile.py",
            "sizing.py",
            "trade_management.py",
            "exits.py",
            "order_intent.py",
            "order_validation.py",
            "execution_pipeline.py",
            "service.py",
            "repository.py",
            "global_risk_adapter.py",
            "broker_adapter.py",
            "ml/paper_stability.py",
            "ml/promotion_policy.py",
            "rollout.py",
            "final_acceptance.py",
        )
        inventory = regime_backend_inventory()

        self.assertEqual(REGIME_BACKEND_FILE_INVENTORY, expected)
        self.assertEqual(inventory["files"], expected)
        self.assertEqual(inventory["authoritativeRuntime"], "backend.app.algorithms.regime.execution_pipeline")
        self.assertEqual(inventory["authoritativeBacktestEngine"], "backend.app.algorithms.regime.backtest.engine")
        self.assertEqual(inventory["frontendRole"], "API client and presentation only")
        self.assertIn("classifier", inventory["pipeline"])
        self.assertTrue(inventory["apiTransportOnly"])
        for file_name in expected:
            self.assertTrue((ROOT / "backend" / "app" / "algorithms" / "regime" / file_name).exists(), file_name)

    def test_backend_inventory_declares_allowed_shared_components_only(self) -> None:
        expected = (
            {"component": "Raw market-data service", "allowedUse": "Read-only input"},
            {"component": "Quote and candle cache", "allowedUse": "Read-only input"},
            {"component": "Market clock and calendar", "allowedUse": "Read-only input"},
            {"component": "Economic-event feed", "allowedUse": "Read-only input"},
            {"component": "Account equity and buying power", "allowedUse": "Read-only snapshot"},
            {"component": "Broker client", "allowedUse": "Submit approved Regime intents"},
            {"component": "Global account-risk engine", "allowedUse": "Reduce or reject Regime proposals"},
            {"component": "Global risk reservations", "allowedUse": "Account-wide exposure control"},
            {"component": "Database connection utilities", "allowedUse": "Infrastructure only"},
            {"component": "Logging and telemetry", "allowedUse": "Must include algorithm_id=regime"},
            {"component": "Order-side contract types", "allowedUse": "Type definitions only"},
            {"component": "Authentication and API framework", "allowedUse": "Transport only"},
        )
        inventory = regime_backend_inventory()

        self.assertEqual(REGIME_ALLOWED_SHARED_COMPONENTS, expected)
        self.assertEqual(inventory["allowedSharedComponents"], expected)
        self.assertTrue(inventory["globalRiskLayerSharedServerSide"])
        self.assertTrue(inventory["localControlsRemainRegimeOwned"])
        self.assertFalse(inventory["sharedComponentsMayRewriteRegimeState"])
        self.assertFalse(inventory["otherAlgorithmsMayModifyPrivateRegimeComponents"])

    def test_backend_inventory_declares_never_shared_regime_private_components(self) -> None:
        expected = (
            "Regime classification formulas",
            "Regime classification thresholds",
            "Regime axes and composite-state mapping",
            "Regime hysteresis state",
            "Regime transition history",
            "Regime strategy implementations",
            "Regime strategy compatibility matrix",
            "Regime strategy aliases",
            "Regime strategy health",
            "Regime strategy outputs",
            "Regime context outputs",
            "Regime family scores",
            "Regime aggregation",
            "Regime local gates",
            "Regime baseline settings",
            "Regime dynamic profiles",
            "Regime position sizing",
            "Regime entry and exit policy",
            "Regime decisions",
            "Regime order intents",
            "Regime positions and trades",
            "Regime backtest state",
            "Regime backtest results",
            "Regime ML features and artifacts",
            "Regime rollout state",
        )
        inventory = regime_backend_inventory()

        self.assertEqual(REGIME_NEVER_SHARED_COMPONENTS, expected)
        self.assertEqual(inventory["neverSharedComponents"], expected)
        self.assertEqual(len(set(REGIME_NEVER_SHARED_COMPONENTS)), len(REGIME_NEVER_SHARED_COMPONENTS))
        self.assertTrue(all(component.startswith("Regime ") for component in REGIME_NEVER_SHARED_COMPONENTS))

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
        self.assertEqual(body["authoritativeRuntime"], "backend.app.algorithms.regime.execution_pipeline")

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
