"""Regime application service boundary."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.regime.broker_adapter import regime_broker_adapter_inventory
from backend.app.algorithms.regime.execution_pipeline import REGIME_EXECUTION_PIPELINE_MODULES, execute_regime_pipeline
from backend.app.algorithms.regime.global_risk_adapter import regime_global_risk_adapter_inventory
from backend.app.algorithms.regime.ml.promotion_policy import RegimeMlCandidateArtifact, evaluate_regime_ml_promotion_policy
from backend.app.algorithms.regime.repository import RegimeRepository, regime_repository_inventory

REGIME_SERVICE_VERSION = "regime_service_v1"
REGIME_BACKEND_FILE_INVENTORY = (
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
REGIME_ALLOWED_SHARED_COMPONENTS = (
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
REGIME_NEVER_SHARED_COMPONENTS = (
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


class RegimeApplicationService:
    def __init__(self, repository: RegimeRepository | None = None) -> None:
        self.repository = repository or RegimeRepository()

    def record_decision_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return self.repository.record_decision_snapshot(snapshot)

    def record_backtest_result(self, result: dict[str, Any]) -> dict[str, Any]:
        return self.repository.record_backtest_result(result)

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = execute_regime_pipeline(payload)
        self.record_decision_snapshot(result)
        return result

    def record_ml_promotion_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        return self.repository.record_regime_ml_promotion_evidence(evidence)

    def evaluate_ml_promotion(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate_payload = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else payload
        candidate = RegimeMlCandidateArtifact(
            artifact_id=str(candidate_payload.get("artifact_id") or candidate_payload.get("artifactId") or ""),
            artifact_hash=str(candidate_payload.get("artifact_hash") or candidate_payload.get("artifactHash") or ""),
            model_version=str(candidate_payload.get("model_version") or candidate_payload.get("modelVersion") or ""),
            feature_schema_version=str(candidate_payload.get("feature_schema_version") or candidate_payload.get("featureSchemaVersion") or ""),
            label_version=str(candidate_payload.get("label_version") or candidate_payload.get("labelVersion") or ""),
            deterministic_baseline_version=str(candidate_payload.get("deterministic_baseline_version") or candidate_payload.get("deterministicBaselineVersion") or ""),
        )
        decision = evaluate_regime_ml_promotion_policy(
            candidate,
            self.repository,
            frontend_supplied_evidence=payload.get("evidence") if isinstance(payload.get("evidence"), dict) else None,
        )
        return decision.as_dict()

    def persistence_schema(self) -> dict[str, Any]:
        inventory = self.repository.persistence_inventory()
        return {
            "algorithmId": "regime",
            "ownedTables": inventory["ownedTables"],
            "sharedAttributedTables": inventory["sharedAttributedTables"],
            "requiredSharedAttributionColumns": inventory["requiredSharedAttributionColumns"],
            "ownedVersionColumns": inventory["ownedVersionColumns"],
            "inventoryPassed": inventory["passed"],
            "tables": {table: self.repository.table_columns(table) for table in inventory["ownedTables"] + inventory["sharedAttributedTables"]},
        }

    def backend_inventory(self) -> dict[str, Any]:
        return regime_backend_inventory()


def regime_backend_inventory() -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "version": REGIME_SERVICE_VERSION,
        "files": REGIME_BACKEND_FILE_INVENTORY,
        "authoritativeRuntime": "backend.app.algorithms.regime.execution_pipeline",
        "authoritativeBacktestEngine": "backend.app.algorithms.regime.backtest.engine",
        "runtimeLocation": "backend/app/algorithms/regime",
        "frontendRole": "API client and presentation only",
        "pipeline": REGIME_EXECUTION_PIPELINE_MODULES,
        "mlPromotionPolicy": "backend.app.algorithms.regime.ml.promotion_policy",
        "mlPromotionMaximumAutomaticMode": "confirm_only",
        "frontendMayPromoteMl": False,
        "service": "backend.app.algorithms.regime.service.RegimeApplicationService",
        "repository": regime_repository_inventory(),
        "globalRiskAdapter": regime_global_risk_adapter_inventory(),
        "brokerAdapter": regime_broker_adapter_inventory(),
        "allowedSharedComponents": REGIME_ALLOWED_SHARED_COMPONENTS,
        "neverSharedComponents": REGIME_NEVER_SHARED_COMPONENTS,
        "globalRiskLayerSharedServerSide": True,
        "localControlsRemainRegimeOwned": True,
        "sharedComponentsMayRewriteRegimeState": False,
        "otherAlgorithmsMayModifyPrivateRegimeComponents": False,
        "apiTransportOnly": True,
    }


__all__ = [
    "REGIME_BACKEND_FILE_INVENTORY",
    "REGIME_ALLOWED_SHARED_COMPONENTS",
    "REGIME_NEVER_SHARED_COMPONENTS",
    "REGIME_SERVICE_VERSION",
    "RegimeApplicationService",
    "regime_backend_inventory",
]
