"""Application-service boundary for the Meta-Strategy algorithm."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel

from backend.app.algorithms.meta_strategy.execution_pipeline import (
    META_STRATEGY_EXECUTION_PIPELINE_STAGES,
    MetaStrategyExecutionPipelineConfig,
    MetaStrategyExecutionPipelineRequest,
    pipeline_modes_using_authoritative_sequence,
    run_meta_strategy_execution_pipeline,
)
from backend.app.algorithms.meta_strategy.feature_schema import meta_strategy_feature_schema_hash
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID, ALGORITHM_NAME
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRecord, MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.market_snapshot import MetaStrategyMarketSnapshotRequest
from backend.app.algorithms.meta_strategy.models import load_runtime_model_artifact, load_runtime_model_artifact_data
from backend.app.algorithms.meta_strategy.ownership import META_STRATEGY_DEFAULT_CAPITAL_PARTITION
from backend.app.algorithms.meta_strategy.observability import (
    apply_meta_strategy_operational_control,
    build_meta_strategy_evidence_acceptance_report,
    build_meta_strategy_observability_snapshot,
    record_meta_strategy_test_evidence,
)
from backend.app.algorithms.meta_strategy.paper_readiness import (
    build_meta_strategy_paper_entry_readiness_prerequisites,
    build_meta_strategy_paper_readiness_acceptance_report,
)
from backend.app.algorithms.meta_strategy.repository import MetaStrategyRepositoryPersistenceAdapter, MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import (
    MetaStrategyCandidateAggregationSettings,
    MetaStrategyEntryExitSettings,
    MetaStrategyLocalRiskSettings,
    MetaStrategyMLInferenceSettings,
    MetaStrategyOrderConstructionSettings,
    MetaStrategyPositionManagementSettings,
    MetaStrategyPositionSizingSettings,
    MetaStrategySettings,
    MetaStrategySettingsStore,
    resolve_meta_strategy_effective_settings,
)
from backend.app.algorithms.meta_strategy.versions import meta_strategy_version_identifiers


ServiceStatus = Literal["OK", "REQUIRES_INPUT", "REJECTED"]
MetaStrategyRuntimeReadinessProvider = Callable[[], Mapping[str, Any]]

_CALLER_SUPPLIED_TRADING_STATE_KEYS: frozenset[str] = frozenset(
    {
        "accountEquity",
        "account_equity",
        "availableBuyingPower",
        "available_buying_power",
        "buyingPower",
        "buying_power",
        "accountSnapshot",
        "account_snapshot",
        "remainingAlgorithmRisk",
        "remaining_algorithm_risk",
        "remainingRiskDollars",
        "remaining_risk_dollars",
        "globalAvailableRisk",
        "global_available_risk",
        "availableRiskDollars",
        "available_risk_dollars",
        "globalRiskSnapshot",
        "global_risk_snapshot",
        "globalQuantityCap",
        "global_quantity_cap",
        "maxQuantity",
        "max_quantity",
        "inventory",
        "inventorySnapshot",
        "inventory_snapshot",
        "positions",
        "positionState",
        "position_state",
        "openPositions",
        "open_positions",
        "openOrders",
        "open_orders",
        "orders",
        "reservedRiskDollars",
        "reserved_risk_dollars",
        "reservedRiskLedger",
        "reserved_risk_ledger",
        "realizedDailyPnl",
        "realized_daily_pnl",
        "dailyTradeCount",
        "daily_trade_count",
        "paperTradingPermission",
        "paper_trading_permission",
        "liveTradingPermission",
        "live_trading_permission",
        "eventBlackout",
        "event_blackout",
        "sessionAllowed",
        "session_allowed",
        "brokerQuantity",
        "broker_quantity",
        "duplicateOrderIntentIds",
        "duplicate_order_intent_ids",
        "existingPositionSymbols",
        "existing_position_symbols",
        "cashAvailability",
        "cash_availability",
        "operationalHealthStatus",
        "operational_health_status",
    }
)


@dataclass(frozen=True)
class MetaStrategyServiceResult:
    algorithmId: str
    operation: str
    status: ServiceStatus
    payload: Mapping[str, Any]
    reasonCodes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithmId,
            "operation": self.operation,
            "status": self.status,
            "payload": _plain(self.payload),
            "reasonCodes": list(self.reasonCodes),
        }


class MetaStrategyApplicationService:
    """Thin orchestration layer over the authoritative Meta-Strategy package."""

    def __init__(
        self,
        *,
        settings_store: MetaStrategySettingsStore | None = None,
        job_repository: MetaStrategyJobRepository | None = None,
        repository: MetaStrategySqliteRepository | None = None,
        runtime_readiness_provider: MetaStrategyRuntimeReadinessProvider | None = None,
    ) -> None:
        self.settings_store = settings_store or MetaStrategySettingsStore(Path("./data/meta_strategy_settings.db"))
        self.job_repository = job_repository or MetaStrategyJobRepository()
        self.repository = repository or MetaStrategySqliteRepository(f"sqlite:///{self.job_repository.path}")
        self.persistence_adapter = MetaStrategyRepositoryPersistenceAdapter(self.repository)
        self.runtime_readiness_provider = runtime_readiness_provider

    def set_runtime_readiness_provider(self, provider: MetaStrategyRuntimeReadinessProvider | None) -> None:
        self.runtime_readiness_provider = provider

    def status(self) -> dict[str, Any]:
        diagnostics = self.diagnostics()
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="status",
            status="OK",
            payload={
                "algorithmName": ALGORITHM_NAME,
                "router": "backend.app.algorithms.meta_strategy.api",
                "packageBoundary": "dedicated",
                "modelStatus": {
                    "mode": "OFF",
                    "status": "not_loaded",
                    "reasonCodes": ("meta_strategy.model.off_by_default",),
                },
                "diagnostics": diagnostics["payload"],
            },
            reasonCodes=("meta_strategy.service.status_ready",),
        ).to_dict()

    def configuration(self) -> dict[str, Any]:
        active = self.settings_store.get_active_settings()
        effective = resolve_meta_strategy_effective_settings(active)
        self.settings_store.persist_effective_settings(effective)
        active_payload = active.model_dump(mode="json")
        active_payload["settingsVersion"] = active.settings_version
        active_payload["settingsHash"] = active.settings_hash
        effective_payload = effective.model_dump(mode="json")
        effective_payload["settingsVersion"] = effective.settings_version
        effective_payload["effectiveSettingsHash"] = effective.effective_settings_hash
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="configuration",
            status="OK",
            payload={
                "versions": meta_strategy_version_identifiers(),
                "baselineImmutable": True,
                "effectiveProfileDoesNotOverwriteDefaults": True,
                "activeSettings": active_payload,
                "baselineSettingsHash": active.settings_hash,
                "effectiveSettings": effective_payload,
                "effectiveSettingsHash": effective.effective_settings_hash,
            },
            reasonCodes=("meta_strategy.service.configuration_ready",),
        ).to_dict()

    def create_settings_draft(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        actor = str(data.get("actor") or "unknown")
        settings_payload = data.get("settings")
        if not isinstance(settings_payload, Mapping):
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="settings_draft",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "settings"},
                reasonCodes=("meta_strategy.service.settings_payload_required",),
            ).to_dict()
        settings = MetaStrategySettings.model_validate(settings_payload)
        draft = self.settings_store.create_draft(settings, actor=actor)
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="settings_draft",
            status="OK",
            payload={"draftSettingsVersion": draft.settings_version, "settingsHash": draft.settings_hash},
            reasonCodes=("meta_strategy.service.settings_draft_created",),
        ).to_dict()

    def promote_settings_draft(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        version = str(data.get("settingsVersion") or data.get("settings_version") or "")
        if not version:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="settings_promotion",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "settingsVersion"},
                reasonCodes=("meta_strategy.service.settings_version_required",),
            ).to_dict()
        job = self._enqueue_job(
            operation="settings_promotion",
            job_type="settings_promotion",
            payload={
                "settingsVersion": version,
                "actor": str(data.get("actor") or "unknown"),
                "validationEvidence": dict(data.get("validationEvidence") or data.get("validation_evidence") or {}),
            },
            request_payload=data,
            reason_code="meta_strategy.service.settings_promotion_job_queued",
            max_attempts=1,
            cancellable=False,
        )
        return job.to_dict()

    def rollback_settings(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        version = str(data.get("settingsVersion") or data.get("settings_version") or "")
        if not version:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="settings_rollback",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "settingsVersion"},
                reasonCodes=("meta_strategy.service.settings_version_required",),
            ).to_dict()
        record = self.settings_store.rollback_to(
            version,
            actor=str(data.get("actor") or "unknown"),
            reason=str(data.get("reason") or "operator_rollback"),
        )
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="settings_rollback",
            status="OK",
            payload={"rollback": record},
            reasonCodes=record.reason_codes,
        ).to_dict()

    def evaluate(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._enqueue_evaluation_command("evaluation_command", "EVALUATION", payload).to_dict()

    def predict(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        result = self._enqueue_evaluation_command("prediction_command", "EVALUATION", payload).to_dict()
        result["payload"]["orderSubmissionAllowed"] = False
        result["payload"]["approvedSubmissionEndpointRequired"] = True
        result["reasonCodes"] = [*result["reasonCodes"], "meta_strategy.prediction.no_order_submission"]
        return result

    def shadow_evaluate(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._enqueue_evaluation_command("shadow_evaluation_command", "SHADOW", payload).to_dict()

    def paper_evaluate(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        runtime = self._runtime_readiness()
        if runtime and runtime.get("paperOrdersBlocked") is True:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="paper_evaluation_command",
                status="REJECTED",
                payload={"runtimeSupervisor": _plain(runtime), "orderSubmissionAllowed": False, "liveTradingEnabled": False},
                reasonCodes=tuple(runtime.get("reasonCodes") or ("meta_strategy.runtime.paper_orders_blocked",)),
            ).to_dict()
        return self._enqueue_evaluation_command("paper_evaluation_command", "PAPER", payload).to_dict()

    def deterministic_activation(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.shadow_evaluate(payload)

    def ml_filter_rollout(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.shadow_evaluate(payload)

    def dynamic_policy_shadow(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.shadow_evaluate(payload)

    def dynamic_policy_activation(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.paper_evaluate(payload)

    def ml_risk_modifier_experiment(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        response = self.shadow_evaluate(payload)
        response["payload"]["riskModifierAppliedToOrders"] = False
        response["reasonCodes"] = [*response["reasonCodes"], "meta_strategy.ml_risk_modifier.experiment_no_order_submission"]
        return response

    def enqueue_finalised_bar(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        rejected = self._reject_command_payload(data, operation="finalised_bar_event")
        if rejected is not None:
            return rejected.to_dict()
        mode = str(data.get("mode") or "PAPER")
        if mode.upper() == "LIVE":
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="finalised_bar_event",
                status="REJECTED",
                payload={"rejectedFields": ("mode",), "liveTradingEnabled": False},
                reasonCodes=("meta_strategy.api.live_mode_rejected",),
            ).to_dict()
        symbol = str(data.get("symbol") or "")
        timeframe = str(data.get("timeframe") or "1m")
        bar_end_value = data.get("barEnd") or data.get("bar_end")
        if not symbol or bar_end_value is None:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="finalised_bar_event",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "symbol and barEnd"},
                reasonCodes=("meta_strategy.service.finalised_bar_required",),
            ).to_dict()
        active_settings = self.settings_store.get_active_settings()
        bar_end = datetime.fromisoformat(str(bar_end_value))
        job = self.job_repository.enqueue_finalised_bar_decision(
            mode=mode,
            symbol=symbol,
            timeframe=timeframe,
            bar_end=bar_end,
            settings_version=active_settings.settings_version,
            payload={"source": "api_command"},
        )
        event_id = _job_event_id(self.job_repository, job)
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="finalised_bar_event",
            status="OK",
            payload={
                "job": _job_summary(job),
                "event": {"eventId": event_id, "algorithmId": ALGORITHM_ID, "jobId": job.job_id},
                "queued": not job.duplicate,
                "durableQueue": True,
                "backgroundWorkerRequired": True,
                "correlationIds": _correlation_ids(job),
            },
            reasonCodes=("meta_strategy.service.finalised_bar_job_queued" if not job.duplicate else "meta_strategy.service.duplicate_job_suppressed",),
        ).to_dict()

    def reconciliation(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._enqueue_research_job("reconciliation_command", "inventory_reconciliation", payload, "meta_strategy.service.reconciliation_job_queued", queue_max_attempts=2)

    def train(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        training_arguments = data.get("trainingArguments") or data.get("training_arguments")
        if not isinstance(training_arguments, Mapping):
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="training",
                status="REQUIRES_INPUT",
                payload={
                    "authoritativeEntrypoint": "backend.app.algorithms.meta_strategy.training.train_and_validate_meta_model_v2",
                    "requiredInput": "trainingArguments",
                },
                reasonCodes=("meta_strategy.service.training_arguments_required",),
            ).to_dict()
        job = self._enqueue_job(
            operation="training",
            job_type="training",
            payload={"trainingArguments": dict(training_arguments)},
            request_payload=data,
            reason_code="meta_strategy.service.training_job_queued",
            max_attempts=int(data.get("maxAttempts", data.get("max_attempts", 3)) or 3),
            cancellable=True,
        )
        return job.to_dict()

    def load_artifact(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        artifact = data.get("artifact") or data.get("modelArtifact")
        artifact_path = data.get("artifactPath") or data.get("path")
        expected_hash = str(data.get("expectedFeatureSchemaHash") or data.get("expected_feature_schema_hash") or "")
        if isinstance(artifact, Mapping):
            expected_hash = expected_hash or str(artifact.get("featureSchemaHash") or meta_strategy_feature_schema_hash())
            loaded = load_runtime_model_artifact_data(dict(artifact), expected_feature_schema_hash=expected_hash)
        elif artifact_path:
            loaded = load_runtime_model_artifact(Path(str(artifact_path)), expected_feature_schema_hash=expected_hash or meta_strategy_feature_schema_hash())
        else:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="artifact_loading",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "artifact or artifactPath"},
                reasonCodes=("meta_strategy.service.artifact_required",),
            ).to_dict()
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="artifact_loading",
            status="OK",
            payload={
                "artifactId": loaded.artifactId,
                "artifactHash": loaded.artifactHash,
                "modelVersion": loaded.modelVersion,
                "featureSchemaHash": loaded.featureSchemaHash,
                "promotionStatus": loaded.promotionStatus,
            },
            reasonCodes=("meta_strategy.service.artifact_loaded",),
        ).to_dict()

    def backtest(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        snapshot_payloads = data.get("decisionRequests") or data.get("decision_requests")
        if not isinstance(snapshot_payloads, Sequence) or isinstance(snapshot_payloads, str | bytes):
            single = data.get("snapshotRequest") or data.get("snapshot_request")
            snapshot_payloads = (single,) if single is not None else ()
        if not snapshot_payloads:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="backtesting",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "decisionRequests"},
                reasonCodes=("meta_strategy.service.backtest_decision_requests_required",),
            ).to_dict()
        for row in snapshot_payloads:
            _snapshot_request(row)
        job = self._enqueue_job(
            operation="backtesting",
            job_type="backtesting",
            payload={"decisionRequests": _plain(tuple(snapshot_payloads)), "modelArtifacts": _plain(data.get("modelArtifacts", ()))},
            request_payload=data,
            reason_code="meta_strategy.service.backtest_job_queued",
            max_attempts=int(data.get("maxAttempts", data.get("max_attempts", 2)) or 2),
            cancellable=True,
        )
        return job.to_dict()

    def deterministic_replay(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        snapshot_payloads = data.get("decisionRequests") or data.get("decision_requests") or ()
        if not isinstance(snapshot_payloads, Sequence) or isinstance(snapshot_payloads, str | bytes):
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="deterministic_replay",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "decisionRequests"},
                reasonCodes=("meta_strategy.service.replay_decision_requests_required",),
            ).to_dict()
        for row in snapshot_payloads:
            _snapshot_request(row)
        return self._enqueue_job(
            operation="deterministic_replay",
            job_type="deterministic_replay",
            payload={"decisionRequests": _plain(tuple(snapshot_payloads)), "snapshotSetVersion": data.get("snapshotSetVersion") or data.get("snapshot_set_version") or "inline-payload"},
            request_payload=data,
            reason_code="meta_strategy.service.replay_job_queued",
            max_attempts=int(data.get("maxAttempts", data.get("max_attempts", 2)) or 2),
            cancellable=True,
        ).to_dict()

    def walk_forward_evaluation(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._enqueue_research_job("walk_forward_evaluation", "walk_forward_evaluation", payload, "meta_strategy.service.walk_forward_job_queued")

    def holdout_evaluation(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._enqueue_research_job("holdout_evaluation", "holdout_evaluation", payload, "meta_strategy.service.holdout_job_queued")

    def cost_slippage_analysis(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._enqueue_research_job("cost_slippage_analysis", "cost_slippage_analysis", payload, "meta_strategy.service.cost_slippage_job_queued")

    def model_inference_validation(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._enqueue_research_job("model_inference_validation", "model_inference_validation", payload, "meta_strategy.service.model_inference_validation_job_queued")

    def generate_report(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._enqueue_research_job("report_generation", "report_generation", payload, "meta_strategy.service.report_generation_job_queued", queue_max_attempts=1)

    def promote(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        candidate_artifact = data.get("candidateArtifact") or data.get("candidate_artifact") or data.get("artifact")
        evidence_payload = data.get("evidence") or {}
        if not isinstance(candidate_artifact, Mapping):
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="promotion",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "candidateArtifact"},
                reasonCodes=("meta_strategy.service.candidate_artifact_required",),
            ).to_dict()
        job = self._enqueue_job(
            operation="promotion",
            job_type="model_promotion",
            payload={"candidateArtifact": dict(candidate_artifact), "evidence": dict(evidence_payload)},
            request_payload=data,
            reason_code="meta_strategy.service.promotion_job_queued",
            max_attempts=1,
            cancellable=False,
        )
        return job.to_dict()

    def validate_paper_stability(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        candidate_artifact = data.get("candidateArtifact") or data.get("candidate_artifact") or data.get("artifact")
        observations = data.get("observations") or ()
        if not isinstance(candidate_artifact, Mapping):
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="paper_stability",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "candidateArtifact"},
                reasonCodes=("meta_strategy.service.candidate_artifact_required",),
            ).to_dict()
        job = self._enqueue_job(
            operation="paper_stability",
            job_type="paper_stability_evaluation",
            payload={"candidateArtifact": dict(candidate_artifact), "observations": tuple(dict(row) for row in observations)},
            request_payload=data,
            reason_code="meta_strategy.service.paper_stability_job_queued",
            max_attempts=2,
            cancellable=True,
        )
        return job.to_dict()

    def _enqueue_research_job(
        self,
        operation: str,
        job_type: str,
        payload: Mapping[str, Any] | None,
        reason_code: str,
        *,
        queue_max_attempts: int = 2,
    ) -> dict[str, Any]:
        data = dict(payload or {})
        job = self._enqueue_job(
            operation=operation,
            job_type=job_type,
            payload=data,
            request_payload=data,
            reason_code=reason_code,
            max_attempts=int(data.get("maxAttempts", data.get("max_attempts", queue_max_attempts)) or queue_max_attempts),
            cancellable=bool(data.get("cancellable", True)),
        )
        return job.to_dict()

    def job_status(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        queue_name = data.get("queueName") or data.get("queue_name")
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="job_status",
            status="OK",
            payload=self.job_repository.queue_status(queue_name=str(queue_name) if queue_name else None),
            reasonCodes=("meta_strategy.service.job_status_ready",),
        ).to_dict()

    def query_job(self, job_id: str) -> dict[str, Any]:
        try:
            job = self.job_repository.read_job(job_id)
        except KeyError:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="job_query",
                status="REJECTED",
                payload={"jobId": job_id},
                reasonCodes=("meta_strategy.service.job_not_found",),
            ).to_dict()
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="job_query",
            status="OK",
            payload={"job": _job_summary(job), "progress": self.job_repository.job_progress(job_id), "artifacts": self.job_repository.workflow_artifacts(job_id)},
            reasonCodes=("meta_strategy.service.job_query_ready",),
        ).to_dict()

    def query_job_progress(self, job_id: str) -> dict[str, Any]:
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="job_progress_query",
            status="OK",
            payload={"jobId": job_id, "progress": self.job_repository.job_progress(job_id)},
            reasonCodes=("meta_strategy.service.job_progress_ready",),
        ).to_dict()

    def query_job_results(self, job_id: str) -> dict[str, Any]:
        try:
            job = self.job_repository.read_job(job_id)
        except KeyError:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="job_results_query",
                status="REJECTED",
                payload={"jobId": job_id},
                reasonCodes=("meta_strategy.service.job_not_found",),
            ).to_dict()
        result = self.job_repository.read_payload(job.result_reference)["result"] if job.result_reference else None
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="job_results_query",
            status="OK",
            payload={"jobId": job_id, "status": job.status.value, "result": result, "artifacts": self.job_repository.workflow_artifacts(job_id)},
            reasonCodes=("meta_strategy.service.job_results_ready",),
        ).to_dict()

    def query_settings_active(self) -> dict[str, Any]:
        active = self.settings_store.get_active_settings()
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="active_settings_query",
            status="OK",
            payload={"activeSettings": _plain(active), "settingsVersion": active.settings_version, "settingsHash": active.settings_hash},
            reasonCodes=("meta_strategy.service.active_settings_ready",),
        ).to_dict()

    def query_settings_history(self) -> dict[str, Any]:
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="settings_history_query",
            status="OK",
            payload={
                "settings": self.settings_store.settings_history(include_drafts=True),
                "promotionHistory": tuple(_plain(item) for item in self.settings_store.promotion_history()),
                "rollbackHistory": tuple(_plain(item) for item in self.settings_store.rollback_history()),
            },
            reasonCodes=("meta_strategy.service.settings_history_ready",),
        ).to_dict()

    def query_effective_profile(self) -> dict[str, Any]:
        active = self.settings_store.get_active_settings()
        effective = resolve_meta_strategy_effective_settings(active)
        self.settings_store.persist_effective_settings(effective)
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="effective_profile_query",
            status="OK",
            payload={"effectiveProfile": _plain(effective), "history": self.settings_store.effective_profiles(limit=20)},
            reasonCodes=("meta_strategy.service.effective_profile_ready",),
        ).to_dict()

    def query_trading_settings(self) -> dict[str, Any]:
        active = self.settings_store.get_active_settings()
        snapshot = self.repository.current_inventory_snapshot()
        payload = _meta_strategy_trading_settings_view(active, snapshot=_plain(snapshot))
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="trading_settings_query",
            status="OK",
            payload=payload,
            reasonCodes=("meta_strategy.service.trading_settings_ready",),
        ).to_dict()

    def update_trading_settings(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        algorithm_id = str(data.get("algorithmId") or data.get("algorithm_id") or ALGORITHM_ID)
        if algorithm_id != ALGORITHM_ID:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="trading_settings_update",
                status="REJECTED",
                payload={"rejectedAlgorithmId": algorithm_id},
                reasonCodes=("meta_strategy.service.foreign_algorithm_rejected",),
            ).to_dict()
        forbidden = sorted(key for key in data if key in _CALLER_SUPPLIED_TRADING_STATE_KEYS)
        nested_settings = data.get("tradingSettings") if isinstance(data.get("tradingSettings"), Mapping) else data.get("settings")
        settings_payload = dict(nested_settings) if isinstance(nested_settings, Mapping) else {}
        forbidden.extend(sorted(key for key in settings_payload if key in _CALLER_SUPPLIED_TRADING_STATE_KEYS))
        if forbidden:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="trading_settings_update",
                status="REJECTED",
                payload={"rejectedFields": tuple(dict.fromkeys(forbidden))},
                reasonCodes=("meta_strategy.service.caller_authoritative_state_rejected",),
            ).to_dict()
        if not settings_payload:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="trading_settings_update",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "tradingSettings"},
                reasonCodes=("meta_strategy.service.trading_settings_payload_required",),
            ).to_dict()

        active = self.settings_store.get_active_settings()
        snapshot = self.repository.current_inventory_snapshot()
        try:
            updated = _apply_meta_strategy_trading_settings(active, settings_payload, allocated_capital=_settings_allocated_capital(settings_payload, snapshot.allocated_capital))
        except ValueError as exc:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="trading_settings_update",
                status="REJECTED",
                payload={"error": str(exc)},
                reasonCodes=("meta_strategy.service.trading_settings_invalid",),
            ).to_dict()
        actor = str(data.get("updatedBy") or data.get("actor") or "operator")
        stored = self.settings_store.create_baseline(updated, actor=actor)
        activated = self.settings_store.activate_settings(stored.settings_version, actor=actor)
        if "startingCapital" in settings_payload:
            now = datetime.now(UTC)
            self.repository.record_allocated_capital(
                {
                    "algorithmId": ALGORITHM_ID,
                    "capitalPartitionId": META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
                    "settingsVersion": activated.settings_version,
                    "allocatedCapital": _required_non_negative(settings_payload["startingCapital"], "startingCapital"),
                    "timestamp": now.isoformat(),
                    "createdAt": now.isoformat(),
                    "updatedBy": actor,
                    "reason": "meta_strategy.trading_settings.paper_capital_updated",
                }
            )
        snapshot = self.repository.current_inventory_snapshot()
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="trading_settings_update",
            status="OK",
            payload=_meta_strategy_trading_settings_view(activated, snapshot=_plain(snapshot)),
            reasonCodes=("meta_strategy.service.trading_settings_updated",),
        ).to_dict()

    def query_model_active(self) -> dict[str, Any]:
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="active_model_query",
            status="OK",
            payload={"activeModel": self.job_repository.active_model_pointer(), "mlShadowOnly": True},
            reasonCodes=("meta_strategy.service.active_model_ready",),
        ).to_dict()

    def query_model_history(self) -> dict[str, Any]:
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="model_history_query",
            status="OK",
            payload={"activeModel": self.job_repository.active_model_pointer(), "promotionHistory": self.job_repository.model_promotion_history()},
            reasonCodes=("meta_strategy.service.model_history_ready",),
        ).to_dict()

    def query_inventory(self) -> dict[str, Any]:
        snapshot = self.repository.current_inventory_snapshot()
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="inventory_query",
            status="OK",
            payload={"inventory": _plain(snapshot), "consistency": self.repository.check_inventory_consistency()},
            reasonCodes=("meta_strategy.service.inventory_ready",),
        ).to_dict()

    def query_inventory_records(self, record_type: str) -> dict[str, Any]:
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation=f"{record_type}_query",
            status="OK",
            payload={"recordType": record_type, "records": self.repository.inventory_records(record_type)},
            reasonCodes=("meta_strategy.service.inventory_records_ready",),
        ).to_dict()

    def query_pnl(self) -> dict[str, Any]:
        snapshot = self.repository.current_inventory_snapshot()
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="pnl_query",
            status="OK",
            payload={"realisedPnl": snapshot.realised_pnl, "unrealisedPnl": snapshot.unrealised_pnl, "dailyTradeCount": snapshot.daily_trade_count},
            reasonCodes=("meta_strategy.service.pnl_ready",),
        ).to_dict()

    def worker_health(self) -> dict[str, Any]:
        status = self.job_repository.queue_status()
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="worker_health_query",
            status="OK",
            payload={"workers": status["workers"], "queues": status["queues"], "runtimeSupervisor": _plain(self._runtime_readiness())},
            reasonCodes=("meta_strategy.service.worker_health_ready",),
        ).to_dict()

    def queue_lag(self) -> dict[str, Any]:
        status = self.job_repository.queue_status()
        lag = {queue: data["lagSeconds"] for queue, data in status["queues"].items()}
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="queue_lag_query",
            status="OK",
            payload={"asOf": status["asOf"], "lagSeconds": lag, "queues": status["queues"]},
            reasonCodes=("meta_strategy.service.queue_lag_ready",),
        ).to_dict()

    def blocked_decisions(self) -> dict[str, Any]:
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="blocked_decisions_query",
            status="OK",
            payload={"decisions": self.job_repository.blocked_decisions()},
            reasonCodes=("meta_strategy.service.blocked_decisions_ready",),
        ).to_dict()

    def latest_decision(self) -> dict[str, Any]:
        record = self.job_repository.latest_worker_decision() or self.repository.latest("decisions")
        if record is None:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="latest_decision_query",
                status="REJECTED",
                payload={
                    "available": False,
                    "signal": "Hold",
                    "decisionLabel": "Hold",
                    "reasonCodes": ("meta_strategy.decision.latest_unavailable",),
                    "summary": "No persisted Meta-Strategy decision is available yet.",
                },
                reasonCodes=("meta_strategy.service.latest_decision_unavailable",),
            ).to_dict()
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="latest_decision_query",
            status="OK",
            payload=_latest_decision_view(record),
            reasonCodes=("meta_strategy.service.latest_decision_ready",),
        ).to_dict()

    def api_documentation(self) -> dict[str, Any]:
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="api_documentation",
            status="OK",
            payload={
                "apiStyle": "command_query",
                "commandsReturn": "202 Accepted with durable job and correlation IDs",
                "liveTradingEnabled": False,
                "commandEndpoints": (
                    "/api/meta-strategy/evaluate",
                    "/api/meta-strategy/backtests/run",
                    "/api/meta-strategy/replay/run",
                    "/api/meta-strategy/training/run",
                    "/api/meta-strategy/model-inference/validate",
                    "/api/meta-strategy/promotion/evaluate",
                    "/api/meta-strategy/reconciliation/run",
                    "/api/meta-strategy/reports/generate",
                ),
                "queryEndpoints": (
                    "/api/meta-strategy/jobs/{job_id}",
                    "/api/meta-strategy/jobs/{job_id}/progress",
                    "/api/meta-strategy/jobs/{job_id}/results",
                    "/api/meta-strategy/settings/active",
                    "/api/meta-strategy/models/active",
                    "/api/meta-strategy/inventory",
                    "/api/meta-strategy/workers/health",
                    "/api/meta-strategy/queues/lag",
                ),
            },
            reasonCodes=("meta_strategy.service.api_documentation_ready",),
        ).to_dict()

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        cancelled = self.job_repository.cancel_job(job_id)
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="job_cancel",
            status="OK" if cancelled else "REJECTED",
            payload={"jobId": job_id, "cancelled": cancelled},
            reasonCodes=("meta_strategy.service.job_cancelled" if cancelled else "meta_strategy.service.job_not_cancellable"),
        ).to_dict()

    def final_acceptance(self) -> dict[str, Any]:
        report = build_meta_strategy_evidence_acceptance_report(
            build_meta_strategy_observability_snapshot(
                job_repository=self.job_repository,
                inventory_repository=self.repository,
                settings_store=self.settings_store,
            )
        )
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="final_acceptance",
            status="OK" if report["complete"] else "REJECTED",
            payload=report,
            reasonCodes=("meta_strategy.service.final_acceptance_ready",),
        ).to_dict()

    def observability(self) -> dict[str, Any]:
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="observability",
            status="OK",
            payload=build_meta_strategy_observability_snapshot(
                job_repository=self.job_repository,
                inventory_repository=self.repository,
                settings_store=self.settings_store,
            ),
            reasonCodes=("meta_strategy.service.observability_ready",),
        ).to_dict()

    def readiness_report(self) -> dict[str, Any]:
        snapshot = build_meta_strategy_observability_snapshot(
            job_repository=self.job_repository,
            inventory_repository=self.repository,
            settings_store=self.settings_store,
        )
        report = build_meta_strategy_evidence_acceptance_report(snapshot)
        runtime = self._runtime_readiness()
        paper_readiness = build_meta_strategy_paper_readiness_acceptance_report(snapshot, runtime)
        entry_prerequisites = build_meta_strategy_paper_entry_readiness_prerequisites(snapshot, runtime, paper_readiness)
        ready = bool(report["complete"] and paper_readiness["paperReady"] and entry_prerequisites["ready"] and not _runtime_blocks_paper(runtime))
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="readiness_report",
            status="OK" if ready else "REJECTED",
            payload={
                **report,
                "complete": ready,
                "paperReady": bool(paper_readiness["paperReady"]),
                "paperReadinessAcceptance": paper_readiness,
                "paperEntryReadinessPrerequisites": entry_prerequisites,
                "operationalPrerequisites": entry_prerequisites,
                "algorithmSpecificReadiness": snapshot.get("algorithmReadiness"),
                "apiHealth": {"status": "OK", "healthy": True},
                "metaStrategyRuntimeHealth": {
                    "healthy": bool(dict(runtime or {}).get("ready") is True),
                    "status": dict(runtime or {}).get("status"),
                    "reasonCodes": tuple(dict(runtime or {}).get("reasonCodes") or ()),
                },
                "paperReadiness": {"ready": bool(paper_readiness["paperReady"]), "entryReady": bool(entry_prerequisites["ready"])},
                "paperToggleState": self.query_paper_control({}).get("payload"),
                "marketOpenState": {"healthy": bool(entry_prerequisites.get("marketClockHealthy"))},
                "newEntryPermission": {
                    "allowed": ready,
                    "reasonCodes": tuple(_readiness_blocking_reason_codes(entry_prerequisites, runtime)),
                },
                "exitManagementHealth": {
                    "healthy": _position_management_healthy(runtime),
                    "riskReducingActivityContinuesWhenNewEntriesBlocked": True,
                },
                "apiProcessHealthyDoesNotImplyMetaStrategyReadiness": True,
                "runtimeSupervisor": _plain(runtime),
                "currentShadowPaperStatus": {
                    "shadow": report["shadowStatus"],
                    "paper": "blocked" if not ready else "READY",
                    "liveExecutionEnabled": False,
                    "paperOrdersBlocked": not ready,
                },
            },
            reasonCodes=("meta_strategy.service.readiness_report_ready",),
        ).to_dict()

    def _runtime_readiness(self) -> Mapping[str, Any] | None:
        if self.runtime_readiness_provider is None:
            return None
        try:
            return self.runtime_readiness_provider()
        except Exception as exc:
            return {
                "algorithmId": ALGORITHM_ID,
                "ready": False,
                "status": "unavailable",
                "paperOrdersBlocked": True,
                "reasonCodes": ("meta_strategy.runtime.readiness_provider_failed",),
                "error": str(exc),
            }

    def apply_control(self, control: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        result = apply_meta_strategy_operational_control(
            job_repository=self.job_repository,
            control=control,
            actor=str(data.get("actor") or "unknown"),
            reason=str(data.get("reason") or f"meta_strategy.control.{control}"),
            payload=data,
        )
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="operational_control",
            status="OK" if result.status == "RECORDED" else "REJECTED",
            payload=result.to_dict(),
            reasonCodes=result.reason_codes,
        ).to_dict()

    def query_paper_control(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        capital_partition_id = str(data.get("capitalPartitionId") or data.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION)
        try:
            record = self.job_repository.read_paper_trading_control(capital_partition_id=capital_partition_id)
        except ValueError as exc:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="paper_control_query",
                status="REJECTED",
                payload={"capitalPartitionId": capital_partition_id, "available": False},
                reasonCodes=(str(exc),),
            ).to_dict()
        if record is None:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="paper_control_query",
                status="REJECTED",
                payload={
                    "algorithmId": ALGORITHM_ID,
                    "capitalPartitionId": capital_partition_id,
                    "newPaperEntriesEnabled": False,
                    "automaticPaperTradingEnabled": False,
                    "paperEntriesAllowed": False,
                    "paperOnly": True,
                    "liveExecutionEnabled": False,
                    "available": False,
                    "version": 0,
                },
                reasonCodes=("meta_strategy.paper_control.state_unavailable",),
            ).to_dict()
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="paper_control_query",
            status="OK",
            payload={**record.to_dict(), "available": True},
            reasonCodes=("meta_strategy.paper_control.state_loaded",),
        ).to_dict()

    def update_paper_control(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        algorithm_id = str(data.get("algorithmId") or data.get("algorithm_id") or ALGORITHM_ID)
        capital_partition_id = str(data.get("capitalPartitionId") or data.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION)
        enabled_value = data.get("newPaperEntriesEnabled")
        if enabled_value is None:
            enabled_value = data.get("enabled")
        if enabled_value is None:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="paper_control_update",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "newPaperEntriesEnabled"},
                reasonCodes=("meta_strategy.paper_control.enabled_value_required",),
            ).to_dict()
        expected = data.get("expectedVersion") if data.get("expectedVersion") is not None else data.get("expected_version")
        control = "ENABLE_AUTOMATIC_PAPER_TRADING" if bool(enabled_value) else "DISABLE_AUTOMATIC_PAPER_TRADING"
        if algorithm_id != ALGORITHM_ID:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="paper_control_update",
                status="REJECTED",
                payload={"algorithmId": algorithm_id, "capitalPartitionId": capital_partition_id, "updated": False},
                reasonCodes=("meta_strategy.paper_control.foreign_algorithm_rejected",),
            ).to_dict()
        result = apply_meta_strategy_operational_control(
            job_repository=self.job_repository,
            control=control,
            actor=str(data.get("updatedBy") or data.get("actor") or "unknown"),
            reason=str(data.get("reason") or "meta_strategy.paper_control.operator_update"),
            payload={
                "capitalPartitionId": capital_partition_id,
                **({"expectedVersion": int(expected)} if expected is not None else {}),
            },
        )
        if result.status != "RECORDED":
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="paper_control_update",
                status="REJECTED",
                payload=result.to_dict(),
                reasonCodes=result.reason_codes,
            ).to_dict()
        record = self.job_repository.read_paper_trading_control(capital_partition_id=capital_partition_id)
        if record is None:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="paper_control_update",
                status="REJECTED",
                payload={"algorithmId": algorithm_id, "capitalPartitionId": capital_partition_id, "updated": False},
                reasonCodes=("meta_strategy.paper_control.state_unavailable",),
            ).to_dict()
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="paper_control_update",
            status="OK",
            payload={**record.to_dict(), "available": True},
            reasonCodes=record.to_dict()["reasonCodes"],
        ).to_dict()

    def record_test_evidence(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        test_id = str(data.get("testId") or data.get("test_id") or "")
        if not test_id:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation="test_evidence",
                status="REQUIRES_INPUT",
                payload={"requiredInput": "testId"},
                reasonCodes=("meta_strategy.service.test_id_required",),
            ).to_dict()
        evidence = record_meta_strategy_test_evidence(
            job_repository=self.job_repository,
            test_id=test_id,
            passed=bool(data.get("passed")),
            command=str(data.get("command") or ""),
            evidence=str(data.get("evidence") or ""),
        )
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="test_evidence",
            status="OK",
            payload=evidence,
            reasonCodes=("meta_strategy.service.test_evidence_recorded",),
        ).to_dict()

    def _enqueue_job(
        self,
        *,
        operation: str,
        job_type: str,
        payload: Mapping[str, Any],
        request_payload: Mapping[str, Any],
        reason_code: str,
        max_attempts: int,
        cancellable: bool,
    ) -> MetaStrategyServiceResult:
        rejected = self._reject_command_payload(request_payload, operation=operation)
        if rejected is not None:
            return rejected
        job = self.job_repository.enqueue_job(
            job_type=job_type,
            idempotency_key=_request_idempotency_key(job_type, request_payload),
            payload=payload,
            max_attempts=max_attempts,
            cancellable=cancellable,
        )
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation=operation,
            status="OK",
            payload={
                "job": _job_summary(job),
                "queued": not job.duplicate,
                "durableQueue": True,
                "backgroundWorkerRequired": True,
                "correlationIds": _correlation_ids(job),
            },
            reasonCodes=(reason_code if not job.duplicate else "meta_strategy.service.duplicate_job_suppressed",),
        )

    def _enqueue_evaluation_command(
        self,
        operation: str,
        mode: Literal["EVALUATION", "SHADOW", "PAPER"],
        payload: Mapping[str, Any] | None,
    ) -> MetaStrategyServiceResult:
        data = dict(payload or {})
        rejected = self._reject_command_payload(data, operation=operation)
        if rejected is not None:
            return rejected
        settings_override_keys = tuple(key for key in ("settings", "settingsVersion", "settings_version", "effectiveSettings", "effective_settings") if key in data)
        if settings_override_keys:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation=operation,
                status="REJECTED",
                payload={"rejectedFields": settings_override_keys, "migration": "Use persisted Meta-Strategy settings promotion commands instead of per-request overrides."},
                reasonCodes=("meta_strategy.api.request_settings_override_rejected",),
            )
        snapshot_payload = data.get("snapshotRequest") or data.get("snapshot_request")
        if snapshot_payload is None:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation=operation,
                status="REQUIRES_INPUT",
                payload={"requiredInput": "snapshotRequest"},
                reasonCodes=("meta_strategy.service.snapshot_request_required",),
            )
        snapshot = _snapshot_request(snapshot_payload)
        last_bar = snapshot.one_minute_candles[-1] if snapshot.one_minute_candles else None
        bar_end = last_bar.timestamp if last_bar is not None else snapshot.decision_timestamp
        active_settings = self.settings_store.get_active_settings()
        job = self.job_repository.enqueue_finalised_bar_decision(
            mode=mode,
            symbol=snapshot.symbol,
            timeframe=last_bar.timeframe if last_bar is not None else "1m",
            bar_end=bar_end,
            settings_version=active_settings.settings_version,
            payload={
                "source": "api_command",
                "operation": operation,
                "requestedDecisionId": snapshot.decision_id,
                "requestedSnapshotId": snapshot.snapshot_id,
            },
        )
        event_id = _job_event_id(self.job_repository, job)
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation=operation,
            status="OK",
            payload={
                "job": _job_summary(job),
                "event": {"eventId": event_id, "algorithmId": ALGORITHM_ID, "jobId": job.job_id},
                "queued": not job.duplicate,
                "durableQueue": True,
                "backgroundWorkerRequired": True,
                "correlationIds": _correlation_ids(job),
                "deprecatedSynchronousRoute": True,
            },
            reasonCodes=("meta_strategy.service.evaluation_job_queued" if not job.duplicate else "meta_strategy.service.duplicate_job_suppressed",),
        )

    def _reject_command_payload(self, data: Mapping[str, Any], *, operation: str) -> MetaStrategyServiceResult | None:
        algorithm_id = data.get("algorithmId") or data.get("algorithm_id")
        if algorithm_id is not None and str(algorithm_id) != ALGORITHM_ID:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation=operation,
                status="REJECTED",
                payload={"rejectedFields": ("algorithmId",), "requestedAlgorithmId": str(algorithm_id)},
                reasonCodes=("meta_strategy.api.algorithm_impersonation_rejected",),
            )
        mode = str(data.get("mode") or "").upper()
        if mode == "LIVE":
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation=operation,
                status="REJECTED",
                payload={"rejectedFields": ("mode",), "liveTradingEnabled": False},
                reasonCodes=("meta_strategy.api.live_mode_rejected",),
            )
        caller_trading_state_keys = tuple(sorted(key for key in data if key in _CALLER_SUPPLIED_TRADING_STATE_KEYS))
        if caller_trading_state_keys:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation=operation,
                status="REJECTED",
                payload={
                    "rejectedFields": caller_trading_state_keys,
                    "boundary": "trading_state_must_come_from_meta_strategy_repositories_and_read_only_shared_views",
                    "migration": "Workers load authoritative runtime state from Meta-Strategy repositories and read-only shared services.",
                },
                reasonCodes=("meta_strategy.api.authoritative_fields_rejected", "meta_strategy.service.caller_supplied_trading_state_rejected"),
            )
        return None

    def diagnostics(self) -> dict[str, Any]:
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="diagnostics",
            status="OK",
            payload={
                "algorithmName": ALGORITHM_NAME,
                "versions": meta_strategy_version_identifiers(),
                "authoritativePipelineStages": META_STRATEGY_EXECUTION_PIPELINE_STAGES,
                "pipelineModes": pipeline_modes_using_authoritative_sequence(),
                "serviceOperations": (
                    "evaluation",
                    "training",
                    "artifact_loading",
                    "backtesting",
                    "shadow_evaluation",
                    "paper_evaluation",
                    "promotion",
                    "paper_stability",
                    "diagnostics",
                ),
            },
            reasonCodes=("meta_strategy.service.diagnostics_ready",),
        ).to_dict()

    def _run_pipeline(
        self,
        operation: str,
        mode: Literal["EVALUATION", "SHADOW", "PAPER"],
        payload: Mapping[str, Any] | None,
    ) -> MetaStrategyServiceResult:
        data = dict(payload or {})
        request_settings_override = any(key in data for key in ("settings", "settingsVersion", "settings_version", "effectiveSettings", "effective_settings"))
        caller_trading_state_keys = sorted(key for key in data if key in _CALLER_SUPPLIED_TRADING_STATE_KEYS)
        if caller_trading_state_keys:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation=operation,
                status="REJECTED",
                payload={
                    "rejectedFields": caller_trading_state_keys,
                    "boundary": "trading_state_must_come_from_meta_strategy_repositories_and_read_only_shared_views",
                },
                reasonCodes=("meta_strategy.service.caller_supplied_trading_state_rejected",),
            )
        snapshot_payload = data.get("snapshotRequest") or data.get("snapshot_request")
        if snapshot_payload is None:
            return MetaStrategyServiceResult(
                algorithmId=ALGORITHM_ID,
                operation=operation,
                status="REQUIRES_INPUT",
                payload={"requiredInput": "snapshotRequest"},
                reasonCodes=("meta_strategy.service.snapshot_request_required",),
            )
        result = run_meta_strategy_execution_pipeline(
            MetaStrategyExecutionPipelineRequest(
                mode=mode,
                snapshot_request=_snapshot_request(snapshot_payload),
                model_artifact=_optional_mapping(data.get("modelArtifact") or data.get("model_artifact")),
                account_equity=_optional_float(data, "accountEquity", "account_equity"),
                available_buying_power=_optional_float(data, "availableBuyingPower", "available_buying_power"),
                remaining_algorithm_risk=_optional_float(data, "remainingAlgorithmRisk", "remaining_algorithm_risk"),
                global_available_risk=_optional_float(data, "globalAvailableRisk", "global_available_risk"),
                global_quantity_cap=_optional_int(data, "globalQuantityCap", "global_quantity_cap"),
                realized_daily_pnl=float(
                    data.get("realizedDailyPnl", data.get("realized_daily_pnl"))
                    if data.get("realizedDailyPnl", data.get("realized_daily_pnl")) is not None
                    else 0.0
                ),
                daily_trade_count=int(
                    data.get("dailyTradeCount", data.get("daily_trade_count"))
                    if data.get("dailyTradeCount", data.get("daily_trade_count")) is not None
                    else 0
                ),
                paper_trading_permission=bool(data.get("paperTradingPermission", data.get("paper_trading_permission", True))),
                live_trading_permission=bool(data.get("liveTradingPermission", data.get("live_trading_permission", False))),
                event_blackout=bool(data.get("eventBlackout", data.get("event_blackout", False))),
                session_allowed=bool(data.get("sessionAllowed", data.get("session_allowed", True))),
                broker_quantity=int(
                    data.get("brokerQuantity", data.get("broker_quantity"))
                    if data.get("brokerQuantity", data.get("broker_quantity")) is not None
                    else 0
                ),
                duplicate_order_intent_ids=tuple(data.get("duplicateOrderIntentIds", data.get("duplicate_order_intent_ids", ())) or ()),
                existing_position_symbols=tuple(data.get("existingPositionSymbols", data.get("existing_position_symbols", ())) or ()),
                max_quote_age_seconds=int(
                    data.get("maxQuoteAgeSeconds", data.get("max_quote_age_seconds"))
                    if data.get("maxQuoteAgeSeconds", data.get("max_quote_age_seconds")) is not None
                    else 60
                ),
            ),
            config=MetaStrategyExecutionPipelineConfig(submit_to_broker=False),
            persistence_adapter=self.persistence_adapter,
            config_settings=self.settings_store.get_active_settings(),
        )
        reason_codes = result.reason_codes
        if request_settings_override:
            reason_codes = (*reason_codes, "meta_strategy.service.request_settings_override_rejected")
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation=operation,
            status="OK",
            payload=_pipeline_summary(result),
            reasonCodes=reason_codes,
        )


def _snapshot_request(value: Any) -> MetaStrategyMarketSnapshotRequest:
    if isinstance(value, MetaStrategyMarketSnapshotRequest):
        return value
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    return MetaStrategyMarketSnapshotRequest.model_validate(value)


def _optional_mapping(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _optional_float(payload: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in payload and payload[key] is not None:
            return float(payload[key])
    return None


def _optional_int(payload: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key in payload and payload[key] is not None:
            return int(payload[key])
    return None


def _request_idempotency_key(job_type: str, payload: Mapping[str, Any]) -> str:
    explicit = payload.get("idempotencyKey") or payload.get("idempotency_key")
    if explicit:
        return f"meta_strategy.{job_type}.{explicit}"
    stable_payload = json.dumps(_plain(payload), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{job_type}:{stable_payload}".encode("utf-8")).hexdigest()[:24]
    return f"meta_strategy.{job_type}.{digest}"


def _job_summary(job: MetaStrategyJobRecord) -> dict[str, Any]:
    return {
        "jobId": job.job_id,
        "algorithmId": job.algorithm_id,
        "jobType": job.job_type,
        "queueName": job.queue_name,
        "idempotencyKey": job.idempotency_key,
        "status": job.status.value,
        "priority": job.priority,
        "attemptCount": job.attempt_count,
        "maxAttempts": job.max_attempts,
        "nextAttemptAt": job.next_attempt_at,
        "payloadReference": job.payload_reference,
        "duplicate": job.duplicate,
        "correlationIds": _correlation_ids(job),
    }


def _correlation_ids(job: MetaStrategyJobRecord) -> dict[str, Any]:
    return {
        "algorithmId": job.algorithm_id,
        "jobId": job.job_id,
        "queueName": job.queue_name,
        "idempotencyKey": job.idempotency_key,
        "payloadReference": job.payload_reference,
        "resultReference": job.result_reference,
    }


def _job_event_id(repository: MetaStrategyJobRepository, job: MetaStrategyJobRecord) -> str:
    try:
        payload = repository.read_payload(job.payload_reference)
    except KeyError:
        return ""
    nested = payload.get("payload") if isinstance(payload.get("payload"), Mapping) else payload
    return str(nested.get("eventId") or nested.get("event_id") or "")


def _runtime_blocks_paper(runtime: Mapping[str, Any] | None) -> bool:
    if runtime is None:
        return True
    return runtime.get("ready") is not True or runtime.get("paperOrdersBlocked") is True


def _readiness_blocking_reason_codes(prerequisites: Mapping[str, Any], runtime: Mapping[str, Any] | None) -> tuple[str, ...]:
    reason_codes: list[str] = []
    if dict(runtime or {}).get("ready") is not True:
        reason_codes.append("meta_strategy.readiness.runtime_supervisor_not_ready")
    mapping = {
        "durableDatabaseAvailable": "meta_strategy.readiness.database_unavailable",
        "activeSettingsPromotedForPaper": "meta_strategy.readiness.settings_not_promoted_for_paper",
        "paperBrokerVerified": "meta_strategy.readiness.paper_broker_unverified",
        "authoritativeMarketDataHealthy": "meta_strategy.readiness.market_data_unhealthy",
        "marketClockHealthy": "meta_strategy.readiness.market_clock_unhealthy",
        "requiredWorkersHealthy": "meta_strategy.readiness.worker_unhealthy",
        "queueLagBelowThreshold": "meta_strategy.readiness.queue_lag_exceeded",
        "deadLetterWithinThreshold": "meta_strategy.readiness.dead_letter_threshold_exceeded",
        "restartReconstructionSucceeded": "meta_strategy.readiness.restart_reconstruction_failed",
        "inventoryReconciliationCurrent": "meta_strategy.readiness.inventory_reconciliation_stale",
        "globalRiskSourceCurrent": "meta_strategy.readiness.global_risk_stale",
        "requiredAcceptanceTestsPassed": "meta_strategy.readiness.acceptance_evidence_missing_or_failed",
    }
    for field, reason in mapping.items():
        if prerequisites.get(field) is not True:
            reason_codes.append(reason)
    return tuple(dict.fromkeys(reason_codes))


def _position_management_healthy(runtime: Mapping[str, Any] | None) -> bool:
    workers = dict(dict(runtime or {}).get("workers") or {})
    status = workers.get("position_management")
    return status == "healthy" or dict(runtime or {}).get("positionManagementHealthy") is True


def _pipeline_summary(result: Any) -> dict[str, Any]:
    return {
        "mode": result.mode,
        "decisionId": result.snapshot.decision_id,
        "snapshotId": result.snapshot.snapshot_id,
        "symbol": result.snapshot.symbol,
        "stageSequence": result.stage_sequence,
        "settingsVersion": result.settings_version,
        "effectiveSettingsHash": result.effective_settings_hash,
        "deterministicCandidate": {
            "direction": result.deterministic_candidate.direction,
            "confidence": result.deterministic_candidate.deterministic_confidence,
            "winningScore": result.deterministic_candidate.winning_score,
            "opposingScore": result.deterministic_candidate.opposing_score,
            "edge": result.deterministic_candidate.edge,
        },
        "geometry": result.geometry,
        "inference": result.inference,
        "localGates": result.local_gates,
        "dynamicProfile": result.dynamic_profile,
        "sizing": result.sizing,
        "orderIntent": result.order_intent,
        "globalRisk": result.global_risk,
        "orderValidation": result.order_validation,
        "brokerResult": result.broker_result,
        "persistenceResult": result.persistence_result,
        "reconciliation": result.reconciliation,
        "finalValid": result.final_valid,
    }


def _latest_decision_view(record: Any) -> dict[str, Any]:
    payload = _latest_decision_payload(record)
    stage_results = dict(payload.get("stageResults") or payload.get("stage_results") or {})
    candidate = dict(stage_results.get("deterministic_candidate") or payload.get("deterministicCandidate") or {})
    direction = str(candidate.get("direction") or payload.get("signal") or "HOLD").upper()
    signal = "Buy" if direction == "BUY" else "Sell" if direction == "SELL" else "Hold"
    confidence = _finite_number(candidate.get("confidence"), 0.0)
    edge = _finite_number(candidate.get("edge"), 0.0)
    reason_codes = tuple(str(code) for code in payload.get("reasonCodes") or payload.get("reason_codes") or ())
    family_scores = _latest_decision_family_scores(stage_results, signal)
    safety_gates = _latest_decision_safety_gates(reason_codes)
    return {
        "available": True,
        "decisionId": payload.get("decisionId") or _latest_decision_field(record, "decision_id", "decisionId"),
        "recordId": _latest_decision_field(record, "record_id", "recordId") or _latest_decision_field(record, "decision_id", "decisionId"),
        "status": payload.get("status") or payload.get("finalStatus") or _latest_decision_field(record, "status") or "PERSISTED",
        "mode": payload.get("mode"),
        "symbol": payload.get("symbol") or _latest_decision_field(record, "symbol") or "SPY",
        "barEnd": payload.get("barEnd") or payload.get("bar_end") or _latest_decision_field(record, "barEnd", "bar_end"),
        "settingsVersion": payload.get("settingsVersion") or _latest_decision_field(record, "settings_version", "settingsVersion"),
        "effectiveSettingsHash": payload.get("effectiveSettingsHash"),
        "signal": signal,
        "decisionLabel": signal,
        "buyScore": confidence if signal == "Buy" else 0.0,
        "sellScore": confidence if signal == "Sell" else 0.0,
        "holdScore": 1.0 if signal == "Hold" else 0.0,
        "edge": edge,
        "familyScores": family_scores,
        "familyAggregation": _latest_decision_family_aggregation(stage_results, family_scores, edge),
        "familyDisplayScores": _latest_decision_family_display_scores(reason_codes),
        "safetyGates": safety_gates,
        "strategies": _latest_decision_strategies(stage_results),
        "reasonCodes": reason_codes,
        "summary": _latest_decision_summary(signal, reason_codes),
        "stageResults": stage_results,
        "authoritativeSource": "meta_strategy_worker_decisions" if isinstance(record, dict) else "meta_strategy_decisions",
        "inventoryIsolation": {
            "algorithmId": ALGORITHM_ID,
            "capitalPartitionId": _latest_decision_field(record, "capital_partition_id", "capitalPartitionId") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
            "source": "Meta-Strategy-owned decision repository",
        },
    }


def _latest_decision_payload(record: Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return dict(record.get("payload") or {})
    return dict(getattr(record, "payload", None) or {})


def _latest_decision_field(record: Any, *names: str) -> Any:
    for name in names:
        if isinstance(record, Mapping):
            value = record.get(name)
        else:
            value = getattr(record, name, None)
        if value is not None:
            return value
    return None


def _latest_decision_family_scores(stage_results: Mapping[str, Any], signal: str) -> dict[str, dict[str, Any]]:
    families = ("trend", "breakout", "mean_reversion", "reversal", "market_regime", "safety")
    scores = {family: {"buy": 0.0, "sell": 0.0, "hold": 0.0, "capped": False} for family in families}
    family_stage = dict(stage_results.get("family_aggregation") or {})
    raw_scores = family_stage.get("familyScores") or family_stage.get("family_scores")
    if isinstance(raw_scores, Mapping):
        for family in families:
            raw = raw_scores.get(family)
            if isinstance(raw, Mapping):
                scores[family] = {
                    "buy": _finite_number(raw.get("buy") or raw.get("buyScore"), 0.0),
                    "sell": _finite_number(raw.get("sell") or raw.get("sellScore"), 0.0),
                    "hold": _finite_number(raw.get("hold") or raw.get("holdScore"), 0.0),
                    "capped": bool(raw.get("capped")),
                }
    if not any(values["buy"] or values["sell"] or values["hold"] for values in scores.values()):
        for family in families:
            scores[family]["hold"] = 1.0
        if signal == "Buy":
            scores["trend"] = {"buy": 1.0, "sell": 0.0, "hold": 0.0, "capped": False}
        elif signal == "Sell":
            scores["trend"] = {"buy": 0.0, "sell": 1.0, "hold": 0.0, "capped": False}
    return scores


def _latest_decision_family_aggregation(stage_results: Mapping[str, Any], family_scores: Mapping[str, Mapping[str, Any]], edge: float) -> dict[str, float]:
    family_stage = dict(stage_results.get("family_aggregation") or {})
    return {
        "trend_buy_score": _finite_number(family_stage.get("trendBuyScore") or family_scores["trend"].get("buy"), 0.0),
        "trend_sell_score": _finite_number(family_stage.get("trendSellScore") or family_scores["trend"].get("sell"), 0.0),
        "breakout_buy_score": _finite_number(family_stage.get("breakoutBuyScore") or family_scores["breakout"].get("buy"), 0.0),
        "breakout_sell_score": _finite_number(family_stage.get("breakoutSellScore") or family_scores["breakout"].get("sell"), 0.0),
        "mean_reversion_buy_score": _finite_number(family_stage.get("meanReversionBuyScore") or family_scores["mean_reversion"].get("buy"), 0.0),
        "mean_reversion_sell_score": _finite_number(family_stage.get("meanReversionSellScore") or family_scores["mean_reversion"].get("sell"), 0.0),
        "reversal_buy_score": _finite_number(family_stage.get("reversalBuyScore") or family_scores["reversal"].get("buy"), 0.0),
        "reversal_sell_score": _finite_number(family_stage.get("reversalSellScore") or family_scores["reversal"].get("sell"), 0.0),
        "confirmation_score": edge,
        "regime_score": _finite_number(family_stage.get("regimeScore"), 0.0),
    }


def _latest_decision_family_display_scores(reason_codes: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    blocked = any(".safety." in code or ".local_gate." in code or ".sizing." in code for code in reason_codes)
    return {"safety": {"label": "Blocked", "value": -1.0}} if blocked else {}


def _latest_decision_safety_gates(reason_codes: tuple[str, ...]) -> tuple[dict[str, str], ...]:
    relevant = [
        code
        for code in reason_codes
        if ".safety." in code or ".local_gate." in code or ".sizing." in code or ".order_intent." in code or ".inference." in code
    ]
    if not relevant:
        return ({"label": "Meta-Strategy decision", "status": "info", "detail": "Latest decision did not report hard safety blockers."},)
    return tuple(
        {
            "label": _reason_code_label(code),
            "status": "fail" if ".safety." in code or ".local_gate." in code or ".sizing." in code else "info",
            "detail": code,
        }
        for code in relevant[:12]
    )


def _latest_decision_strategies(stage_results: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    strategies = dict(stage_results.get("strategies") or {})
    outputs = strategies.get("outputs") or strategies.get("strategyResults") or strategies.get("strategy_results")
    if not isinstance(outputs, (list, tuple)):
        return ()
    rows: list[dict[str, Any]] = []
    for output in outputs:
        if not isinstance(output, Mapping):
            continue
        signal = str(output.get("signal") or "HOLD").lower()
        rows.append(
            {
                "name": str(output.get("strategyId") or output.get("strategy_id") or "Meta strategy"),
                "role": str(output.get("role") or "directional"),
                "family": str(output.get("familyId") or output.get("family_id") or "trend"),
                "moduleStatus": "active" if output.get("eligible", False) else "not_data_ready",
                "signal": "buy" if signal == "buy" else "sell" if signal == "sell" else "hold",
                "confidence": _finite_number(output.get("confidence"), 0.0),
                "direction": 1 if signal == "buy" else -1 if signal == "sell" else 0,
                "contribution": _finite_number(output.get("confidence"), 0.0),
                "effectiveContribution": _finite_number(output.get("confidence"), 0.0),
                "source": "backend",
                "reason": ", ".join(str(code) for code in output.get("reasonCodes") or output.get("reason_codes") or ()) or "Backend strategy evidence",
            }
        )
    return tuple(rows)


def _latest_decision_summary(signal: str, reason_codes: tuple[str, ...]) -> str:
    if signal != "Hold":
        return f"Latest persisted Meta-Strategy decision selected {signal}."
    if reason_codes:
        return f"Latest persisted Meta-Strategy decision is Hold: {', '.join(reason_codes[:4])}."
    return "Latest persisted Meta-Strategy decision is Hold."


def _reason_code_label(code: str) -> str:
    parts = [part for part in code.split(".") if part and part != "meta_strategy"]
    return " ".join(parts[:3]).replace("_", " ").title() or "Meta-Strategy"


def _meta_strategy_trading_settings_view(settings: MetaStrategySettings, *, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    allocated_capital = _finite_number(snapshot.get("allocated_capital"), 0.0)
    reserved_risk = _finite_number(snapshot.get("reserved_risk_dollars"), 0.0)
    buying_power_display = max(0.0, allocated_capital - reserved_risk)
    daily_loss_percent = (settings.local_risk.maximum_daily_loss / allocated_capital * 100.0) if allocated_capital > 0 else 0.0
    no_new_entry_minutes = settings.position_management.no_new_entry_minutes_before_close
    return {
        "algorithmId": ALGORITHM_ID,
        "capitalPartitionId": str(snapshot.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION),
        "settingsVersion": settings.settings_version,
        "settingsHash": settings.settings_hash,
        "status": settings.status,
        "tradingSettings": {
            "startingCapital": allocated_capital,
            "orderAllocationPercent": settings.local_risk.paper_order_allocation_percent * 100.0,
            "dailyAllocationPercent": settings.local_risk.paper_daily_allocation_percent * 100.0,
            "riskBudgetPercentOfOrder": settings.local_risk.paper_risk_budget_percent_of_order * 100.0,
            "maxTradesPerDay": settings.local_risk.trade_count_limit,
            "stopLossPercent": settings.local_risk.paper_stop_loss_percent * 100.0,
            "fixedStopDistanceDollars": settings.entry_exit_management.stop_multiplier,
            "takeProfitR": settings.entry_exit_management.target_multiplier,
            "slippagePerShare": settings.order_construction.limit_offset_bps / 10000.0,
            "useDefaultSizingSettings": True,
            "minimumBuyScore": settings.entry_exit_management.entry_threshold,
            "minimumSignalEdge": settings.candidate_aggregation.minimum_conflict_edge,
            "baseRiskPercent": settings.local_risk.risk_percentage * 100.0,
            "maxPositionPercent": settings.position_sizing.position_cap * 100.0,
            "atrStopMultiplier": settings.stop_target_construction.stop_multiplier,
            "minimumStopDistancePercent": settings.stop_target_construction.minimum_stop_percent * 100.0,
            "maxParticipationPercent": settings.position_sizing.liquidity_participation_rate * 100.0,
            "maxAllowedShares": settings.position_sizing.maximum_share_quantity,
            "maxDailyLossPercent": daily_loss_percent,
            "minimumActiveStrategies": settings.candidate_aggregation.minimum_active_strategies,
            "minimumBuyStrategyCount": settings.candidate_aggregation.minimum_independent_families,
            "maxSpreadPercent": settings.local_risk.spread_limit_bps / 100.0,
            "minimumOneMinuteVolume": settings.local_risk.liquidity_requirement,
            "pyramidingEnabled": not settings.position_management.one_position_per_symbol,
            "positionSizingMode": "allocation",
            "mlInferenceMode": settings.ml_inference.mode,
        },
        "targetOrder": {
            "accountBalance": allocated_capital,
            "dailyLimitDollars": min(buying_power_display, allocated_capital * settings.local_risk.paper_daily_allocation_percent),
            "orderLimitDollars": allocated_capital * settings.local_risk.paper_order_allocation_percent,
            "symbol": "SPY",
            "side": "Hold",
            "orderType": "No order",
            "quantity": 0,
            "riskDollars": allocated_capital * settings.local_risk.risk_percentage,
            "plannedStopRiskDollars": 0,
            "estimatedSlippage": 0,
            "timeInForce": settings.order_construction.time_in_force,
            "cutoff": f"Backend: no new entries {no_new_entry_minutes}m before close",
            "submitMode": "Automatic",
            "failedGates": ("Meta-Strategy backend workers own entry decisions and paper submission.",),
        },
        "ownership": {
            "inventorySource": "authoritative_meta_strategy_inventory_repository",
            "algorithmScoped": True,
            "inventoryAlgorithmId": str(snapshot.get("algorithm_id") or ALGORITHM_ID),
            "inventoryCapitalPartitionId": str(snapshot.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION),
            "openPositionCount": len(tuple(snapshot.get("open_positions") or ())),
            "reservedRiskDollars": reserved_risk,
            "dailyTradeCount": int(_finite_number(snapshot.get("daily_trade_count"), 0.0)),
        },
        "readOnlyFinancialFields": (),
        "reasonCodes": ("meta_strategy.trading_settings.meta_owned", "meta_strategy.inventory.own_partition_only"),
    }


def _apply_meta_strategy_trading_settings(settings: MetaStrategySettings, payload: Mapping[str, Any], *, allocated_capital: float) -> MetaStrategySettings:
    unknown = sorted(key for key in payload if key not in _META_STRATEGY_TRADING_SETTING_KEYS)
    if unknown:
        raise ValueError(f"meta_strategy.trading_settings.unknown_fields.{','.join(unknown)}")
    local_risk_updates: dict[str, Any] = {}
    position_sizing_updates: dict[str, Any] = {}
    entry_exit_updates: dict[str, Any] = {}
    stop_target_updates: dict[str, Any] = {}
    order_updates: dict[str, Any] = {}
    position_management_updates: dict[str, Any] = {}
    aggregation_updates: dict[str, Any] = {}
    ml_updates: dict[str, Any] = {}

    if "riskBudgetPercentOfOrder" in payload:
        local_risk_updates["paper_risk_budget_percent_of_order"] = _required_non_negative(payload["riskBudgetPercentOfOrder"], "riskBudgetPercentOfOrder") / 100.0
    if "baseRiskPercent" in payload:
        local_risk_updates["risk_percentage"] = _required_non_negative(payload["baseRiskPercent"], "baseRiskPercent") / 100.0
    if "maxTradesPerDay" in payload:
        local_risk_updates["trade_count_limit"] = int(_required_non_negative(payload["maxTradesPerDay"], "maxTradesPerDay"))
    if "dailyAllocationPercent" in payload:
        local_risk_updates["paper_daily_allocation_percent"] = _required_non_negative(payload["dailyAllocationPercent"], "dailyAllocationPercent") / 100.0
    if "maxDailyLossPercent" in payload:
        local_risk_updates["maximum_daily_loss"] = allocated_capital * _required_non_negative(payload["maxDailyLossPercent"], "maxDailyLossPercent") / 100.0
    if "minimumOneMinuteVolume" in payload:
        local_risk_updates["liquidity_requirement"] = _required_non_negative(payload["minimumOneMinuteVolume"], "minimumOneMinuteVolume")
    if "maxSpreadPercent" in payload:
        local_risk_updates["spread_limit_bps"] = _required_non_negative(payload["maxSpreadPercent"], "maxSpreadPercent") * 100.0
    if "maximumOpenRiskDollars" in payload:
        local_risk_updates["maximum_open_risk"] = _required_non_negative(payload["maximumOpenRiskDollars"], "maximumOpenRiskDollars")

    if "orderAllocationPercent" in payload:
        local_risk_updates["paper_order_allocation_percent"] = _required_non_negative(payload["orderAllocationPercent"], "orderAllocationPercent") / 100.0
    if "maxPositionPercent" in payload:
        position_sizing_updates["position_cap"] = _required_non_negative(payload["maxPositionPercent"], "maxPositionPercent") / 100.0
    if "maxParticipationPercent" in payload:
        position_sizing_updates["liquidity_participation_rate"] = _required_non_negative(payload["maxParticipationPercent"], "maxParticipationPercent") / 100.0
    if "maxAllowedShares" in payload:
        position_sizing_updates["maximum_share_quantity"] = int(_required_non_negative(payload["maxAllowedShares"], "maxAllowedShares"))

    if "minimumBuyScore" in payload:
        entry_exit_updates["entry_threshold"] = _required_non_negative(payload["minimumBuyScore"], "minimumBuyScore")
    if "fixedStopDistanceDollars" in payload:
        entry_exit_updates["stop_multiplier"] = max(_required_non_negative(payload["fixedStopDistanceDollars"], "fixedStopDistanceDollars"), 0.000001)
    if "takeProfitR" in payload:
        value = max(_required_non_negative(payload["takeProfitR"], "takeProfitR"), 0.000001)
        entry_exit_updates["target_multiplier"] = value
        stop_target_updates["target_multiplier"] = value
    if "atrStopMultiplier" in payload:
        stop_target_updates["stop_multiplier"] = max(_required_non_negative(payload["atrStopMultiplier"], "atrStopMultiplier"), 0.000001)
    if "minimumStopDistancePercent" in payload:
        stop_target_updates["minimum_stop_percent"] = _required_non_negative(payload["minimumStopDistancePercent"], "minimumStopDistancePercent") / 100.0
    if "stopLossPercent" in payload:
        local_risk_updates["paper_stop_loss_percent"] = _required_non_negative(payload["stopLossPercent"], "stopLossPercent") / 100.0

    if "slippagePerShare" in payload:
        order_updates["limit_offset_bps"] = _required_non_negative(payload["slippagePerShare"], "slippagePerShare") * 10000.0
    if "pyramidingEnabled" in payload:
        position_management_updates["one_position_per_symbol"] = not bool(payload["pyramidingEnabled"])
    if "minimumSignalEdge" in payload:
        aggregation_updates["minimum_conflict_edge"] = _required_non_negative(payload["minimumSignalEdge"], "minimumSignalEdge")
    if "minimumActiveStrategies" in payload:
        aggregation_updates["minimum_active_strategies"] = int(_required_non_negative(payload["minimumActiveStrategies"], "minimumActiveStrategies"))
    if "minimumBuyStrategyCount" in payload:
        aggregation_updates["minimum_independent_families"] = int(_required_non_negative(payload["minimumBuyStrategyCount"], "minimumBuyStrategyCount"))
    if "mlInferenceMode" in payload:
        ml_updates["mode"] = _safe_paper_ml_mode(payload["mlInferenceMode"])

    now = datetime.now(UTC)
    return settings.model_copy(
        update={
            "settings_version": f"meta_strategy_settings_operator_{now.strftime('%Y%m%dT%H%M%S%f')}",
            "created_at": now,
            "status": "BASELINE",
            "local_risk": MetaStrategyLocalRiskSettings.model_validate({**settings.local_risk.model_dump(), **local_risk_updates}),
            "position_sizing": MetaStrategyPositionSizingSettings.model_validate({**settings.position_sizing.model_dump(), **position_sizing_updates}),
            "entry_exit_management": MetaStrategyEntryExitSettings.model_validate({**settings.entry_exit_management.model_dump(), **entry_exit_updates}),
            "stop_target_construction": settings.stop_target_construction.model_copy(update=stop_target_updates),
            "order_construction": MetaStrategyOrderConstructionSettings.model_validate({**settings.order_construction.model_dump(), **order_updates}),
            "position_management": MetaStrategyPositionManagementSettings.model_validate({**settings.position_management.model_dump(), **position_management_updates}),
            "candidate_aggregation": MetaStrategyCandidateAggregationSettings.model_validate({**settings.candidate_aggregation.model_dump(), **aggregation_updates}),
            "ml_inference": MetaStrategyMLInferenceSettings.model_validate({**settings.ml_inference.model_dump(), **ml_updates}),
            "reason_codes": ("meta_strategy.settings.operator_trading_settings_update",),
        }
    )


_META_STRATEGY_TRADING_SETTING_KEYS = frozenset(
    {
        "orderAllocationPercent",
        "startingCapital",
        "dailyAllocationPercent",
        "riskBudgetPercentOfOrder",
        "maxTradesPerDay",
        "stopLossPercent",
        "fixedStopDistanceDollars",
        "takeProfitR",
        "slippagePerShare",
        "minimumBuyScore",
        "minimumSignalEdge",
        "baseRiskPercent",
        "maxPositionPercent",
        "atrStopMultiplier",
        "minimumStopDistancePercent",
        "maxParticipationPercent",
        "maxAllowedShares",
        "minimumActiveStrategies",
        "minimumBuyStrategyCount",
        "maxSpreadPercent",
        "maxDailyLossPercent",
        "minimumOneMinuteVolume",
        "pyramidingEnabled",
        "maximumOpenRiskDollars",
        "mlInferenceMode",
    }
)


def _safe_paper_ml_mode(value: Any) -> str:
    mode = str(value or "").strip().upper()
    if mode not in {"DISABLED", "SHADOW"}:
        raise ValueError("meta_strategy.trading_settings.ml_inference_mode_requires_disabled_or_shadow")
    return mode


def _required_non_negative(value: Any, field_name: str) -> float:
    number = float(value)
    if not (number == number and number not in (float("inf"), float("-inf"))):
        raise ValueError(f"meta_strategy.trading_settings.non_finite.{field_name}")
    if number < 0:
        raise ValueError(f"meta_strategy.trading_settings.negative.{field_name}")
    return number


def _settings_allocated_capital(payload: Mapping[str, Any], current_allocated_capital: float) -> float:
    if "startingCapital" in payload:
        return _required_non_negative(payload["startingCapital"], "startingCapital")
    return max(0.0, float(current_allocated_capital))


def _finite_number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not (number == number and number not in (float("inf"), float("-inf"))):
        return default
    return number


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _plain(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted(_plain(item) for item in value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


__all__ = [
    "MetaStrategyApplicationService",
    "MetaStrategyServiceResult",
]
