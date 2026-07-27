"""Regime application service boundary."""

from __future__ import annotations

import copy
from typing import Any

from backend.app.algorithms.regime.backtest.engine import run_regime_backtest
from backend.app.algorithms.regime.broker_adapter import regime_broker_adapter_inventory
from backend.app.algorithms.regime.configuration import REGIME_SETTINGS_AUTHORITATIVE_SOURCE, regime_settings_identity_from_payload
from backend.app.algorithms.regime.contracts import REGIME_ALLOWED_RUNTIME_MODE_VALUES, RegimeRuntimeMode, normalize_regime_runtime_mode
from backend.app.algorithms.regime.execution_pipeline import REGIME_EXECUTION_PIPELINE_MODULES, execute_regime_pipeline
from backend.app.algorithms.regime.global_risk_adapter import regime_global_risk_adapter_inventory
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.app.algorithms.regime.ml.promotion_policy import RegimeMlCandidateArtifact, evaluate_regime_ml_promotion_policy
from backend.app.algorithms.regime.repository import RegimeRepository, regime_repository_inventory
from backend.app.algorithms.regime.stateful_core import deterministic_data_manifest_hash, deterministic_regime_decision_id

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
    "position_manager.py",
    "trade_management.py",
    "exits.py",
    "order_intent.py",
    "order_validation.py",
    "execution_gateway.py",
    "runtime_state.py",
    "stateful_core.py",
    "execution_pipeline.py",
    "runtime.py",
    "runtime_supervisor.py",
    "runtime_events.py",
    "runtime_idempotency.py",
    "runtime_workers.py",
    "runtime_commands.py",
    "runtime_health.py",
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

    def record_stateful_bar_result(self, result: dict[str, Any]) -> dict[str, Any]:
        return self.repository.record_stateful_bar_result(result)

    def record_backtest_result(self, result: dict[str, Any]) -> dict[str, Any]:
        return self.repository.record_backtest_result(result)

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalize_regime_runtime_mode((payload or {}).get("runtimeMode") or (payload or {}).get("runtime_mode"), default=RegimeRuntimeMode.SHADOW)
        settings_context = self.repository.ensure_active_settings_snapshot(regime_settings_identity_from_payload(payload))
        snapshot = build_regime_market_snapshot(payload.get("marketData") or payload)
        inventory_snapshot = self._inventory_snapshot(payload, settings_context)
        previous_state = self.repository.read_runtime_checkpoint(settings_context["identity"])
        data_manifest_hash = deterministic_data_manifest_hash(snapshot, inventory_snapshot)
        decision_id = deterministic_regime_decision_id(
            algorithm_instance_id=settings_context["identity"]["algorithmInstanceId"],
            runtime_mode=settings_context["identity"]["runtimeMode"],
            symbol=snapshot.symbol,
            completed_bar_timestamp=snapshot.latest.timestamp,
            data_manifest_hash=data_manifest_hash,
            settings_version=str(settings_context["settingsVersion"]),
        )
        if _last_processed_bar(previous_state) == snapshot.latest.timestamp:
            existing = self.repository.read_decision_snapshot_by_id(settings_context["identity"], decision_id)
            if existing is not None:
                return existing
        safe_payload = self._payload_with_authoritative_settings(payload, settings_context)
        safe_payload["__regime_previous_state"] = previous_state
        safe_payload["__regime_inventory_snapshot"] = {**inventory_snapshot, "dataManifestHash": data_manifest_hash}
        result = execute_regime_pipeline(safe_payload)
        identity = settings_context["identity"]
        result["algorithmInstanceId"] = identity["algorithmInstanceId"]
        result["accountId"] = identity["accountId"]
        result["runtimeMode"] = identity["runtimeMode"]
        result["settingsSource"] = REGIME_SETTINGS_AUTHORITATIVE_SOURCE
        self.record_stateful_bar_result(result)
        return result

    def run_backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        if (payload or {}).get("runtimeMode") or (payload or {}).get("runtime_mode"):
            normalize_regime_runtime_mode((payload or {}).get("runtimeMode") or (payload or {}).get("runtime_mode"), default=RegimeRuntimeMode.BACKTEST)
        settings_context = self.repository.ensure_active_settings_snapshot(
            regime_settings_identity_from_payload({**payload, "runtimeMode": RegimeRuntimeMode.BACKTEST.value})
        )
        safe_payload = self._payload_with_authoritative_settings(payload, settings_context)
        result = run_regime_backtest(safe_payload)
        result["settingsSource"] = REGIME_SETTINGS_AUTHORITATIVE_SOURCE
        result["settingsVersion"] = settings_context["settingsVersion"]
        result["settingsSnapshot"] = settings_context["settingsSnapshot"]
        result["algorithmInstanceId"] = settings_context["identity"]["algorithmInstanceId"]
        result["accountId"] = settings_context["identity"]["accountId"]
        result["runtimeMode"] = "backtest"
        result["symbol"] = settings_context["identity"]["symbol"]
        self.record_backtest_result(result)
        return result

    def active_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.repository.ensure_active_settings_snapshot(regime_settings_identity_from_payload(payload or {}))

    def activate_settings(self, command: dict[str, Any]) -> dict[str, Any]:
        return self.repository.activate_settings_snapshot(command)

    def handle_settings_command(self, command: dict[str, Any]) -> dict[str, Any]:
        command_type = str(
            (command or {}).get("commandType")
            or (command or {}).get("command_type")
            or (command or {}).get("action")
            or "activate_version"
        )
        if command_type in {"validate", "validate_version", "settings_validate"}:
            return self.repository.validate_settings_snapshot_command(command)
        if command_type in {"create", "create_version", "settings_create"}:
            return self.repository.create_settings_version(command)
        if command_type in {"activate", "activate_version", "settings_activate"}:
            return self.repository.activate_settings_snapshot(command)
        if command_type in {"rollback", "rollback_version", "settings_rollback"}:
            return self.repository.rollback_settings_snapshot(command)
        raise ValueError(f"Unsupported Regime settings command: {command_type}")

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

    @staticmethod
    def _payload_with_authoritative_settings(payload: dict[str, Any], settings_context: dict[str, Any]) -> dict[str, Any]:
        safe_payload = copy.deepcopy(payload)
        safe_payload.pop("settings", None)
        safe_payload.pop("settingsSnapshot", None)
        safe_payload["__regime_authoritative_settings"] = settings_context["flatSettings"]
        safe_payload["__regime_settings_snapshot"] = settings_context["settingsSnapshot"]
        safe_payload["__regime_settings_source"] = REGIME_SETTINGS_AUTHORITATIVE_SOURCE
        identity = settings_context["identity"]
        safe_payload["algorithmInstanceId"] = identity["algorithmInstanceId"]
        safe_payload["accountId"] = identity["accountId"]
        safe_payload["runtimeMode"] = identity["runtimeMode"]
        return safe_payload

    @staticmethod
    def _inventory_snapshot(payload: dict[str, Any], settings_context: dict[str, Any]) -> dict[str, Any]:
        inventory = payload.get("inventorySnapshot") if isinstance(payload.get("inventorySnapshot"), dict) else {}
        return {
            **inventory,
            "algorithmInstanceId": settings_context["identity"]["algorithmInstanceId"],
            "accountId": settings_context["identity"]["accountId"],
            "runtimeMode": settings_context["identity"]["runtimeMode"],
            "symbol": settings_context["identity"]["symbol"],
        }


def _last_processed_bar(state: dict[str, Any] | None) -> str | None:
    if not isinstance(state, dict):
        return None
    value = state.get("lastProcessedBarTimestamp") or state.get("last_processed_bar_timestamp")
    return str(value) if value else None


def regime_backend_inventory() -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "version": REGIME_SERVICE_VERSION,
        "files": REGIME_BACKEND_FILE_INVENTORY,
        "productionDecisionCore": "backend.app.algorithms.regime.execution_pipeline.execute_regime_pipeline",
        "productionStateTransitionCore": "backend.app.algorithms.regime.stateful_core.process_regime_bar",
        "productionBacktestCore": "backend.app.algorithms.regime.backtest.engine.run_regime_backtest",
        "authoritativeRuntime": "backend.app.algorithms.regime.execution_pipeline",
        "authoritativeBacktestEngine": "backend.app.algorithms.regime.backtest.engine",
        "backgroundRuntime": "backend.app.algorithms.regime.runtime_supervisor.RegimeRuntimeSupervisor",
        "legacyJobManager": "backend.app.algorithms.regime.runtime.RegimeBackgroundJobManager",
        "backgroundWorkers": (
            "regime_market_processing_worker",
            "regime_strategy_evaluation_worker",
            "regime_backtest_worker",
            "regime_risk_processing_worker",
            "regime_execution_processing_worker",
            "regime_reconciliation_worker",
            "regime_position_management_worker",
            "regime_runtime_control_worker",
        ),
        "runtimeLocation": "backend/app/algorithms/regime",
        "frontendRole": "API client, settings editor, status display, diagnostics display, and backtest-job display",
        "frontendDecisionSubmissionAllowed": False,
        "allowedRuntimeModes": REGIME_ALLOWED_RUNTIME_MODE_VALUES,
        "apiResponsibilities": ("transport", "control", "status", "job_management"),
        "pipeline": REGIME_EXECUTION_PIPELINE_MODULES,
        "mlPromotionPolicy": "backend.app.algorithms.regime.ml.promotion_policy",
        "mlPromotionMaximumAutomaticMode": "confirm_only",
        "frontendMayPromoteMl": False,
        "service": "backend.app.algorithms.regime.service.RegimeApplicationService",
        "repository": regime_repository_inventory(),
        "regimeOwnedPersistence": "backend.app.algorithms.regime.persistence.RegimeSqliteRepository",
        "globalRiskAdapter": regime_global_risk_adapter_inventory(),
        "brokerAdapter": regime_broker_adapter_inventory(),
        "allowedSharedComponents": REGIME_ALLOWED_SHARED_COMPONENTS,
        "permittedSharedInfrastructure": REGIME_ALLOWED_SHARED_COMPONENTS,
        "neverSharedComponents": REGIME_NEVER_SHARED_COMPONENTS,
        "globalRiskLayerSharedServerSide": True,
        "localControlsRemainRegimeOwned": True,
        "sharedComponentsMayRewriteRegimeState": False,
        "otherAlgorithmsMayModifyPrivateRegimeComponents": False,
        "apiTransportOnly": True,
        "apiHandlersExecuteHeavyWorkInline": False,
        "settingsAuthoritativeSource": REGIME_SETTINGS_AUTHORITATIVE_SOURCE,
        "settingsChangePath": "API enqueues settings_activation commands for background processing.",
        "liveTradingEnabled": False,
    }


__all__ = [
    "REGIME_BACKEND_FILE_INVENTORY",
    "REGIME_ALLOWED_SHARED_COMPONENTS",
    "REGIME_NEVER_SHARED_COMPONENTS",
    "REGIME_SERVICE_VERSION",
    "RegimeApplicationService",
    "regime_backend_inventory",
]
