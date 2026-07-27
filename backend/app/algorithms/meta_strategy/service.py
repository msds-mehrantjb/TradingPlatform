"""Application-service boundary for the Meta-Strategy algorithm."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

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
from backend.app.algorithms.meta_strategy.observability import (
    apply_meta_strategy_operational_control,
    build_meta_strategy_evidence_acceptance_report,
    build_meta_strategy_observability_snapshot,
    record_meta_strategy_test_evidence,
)
from backend.app.algorithms.meta_strategy.repository import MetaStrategyRepositoryPersistenceAdapter, MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import (
    MetaStrategySettings,
    MetaStrategySettingsStore,
    resolve_meta_strategy_effective_settings,
)
from backend.app.algorithms.meta_strategy.versions import meta_strategy_version_identifiers


ServiceStatus = Literal["OK", "REQUIRES_INPUT", "REJECTED"]

_CALLER_SUPPLIED_TRADING_STATE_KEYS: frozenset[str] = frozenset(
    {
        "accountEquity",
        "account_equity",
        "availableBuyingPower",
        "available_buying_power",
        "remainingAlgorithmRisk",
        "remaining_algorithm_risk",
        "globalAvailableRisk",
        "global_available_risk",
        "globalQuantityCap",
        "global_quantity_cap",
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
    ) -> None:
        self.settings_store = settings_store or MetaStrategySettingsStore(Path("./data/meta_strategy_settings.db"))
        self.job_repository = job_repository or MetaStrategyJobRepository()
        self.repository = repository or MetaStrategySqliteRepository(f"sqlite:///{self.job_repository.path}")
        self.persistence_adapter = MetaStrategyRepositoryPersistenceAdapter(self.repository)

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
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="finalised_bar_event",
            status="OK",
            payload={
                "job": _job_summary(job),
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
            payload={"workers": status["workers"], "queues": status["queues"]},
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
        return MetaStrategyServiceResult(
            algorithmId=ALGORITHM_ID,
            operation="readiness_report",
            status="OK" if report["complete"] else "REJECTED",
            payload={
                **report,
                "currentShadowPaperStatus": {
                    "shadow": report["shadowStatus"],
                    "paper": report["paperStatus"],
                    "liveExecutionEnabled": False,
                },
            },
            reasonCodes=("meta_strategy.service.readiness_report_ready",),
        ).to_dict()

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
            status="OK",
            payload=result.to_dict(),
            reasonCodes=result.reason_codes,
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
                realized_daily_pnl=float(data.get("realizedDailyPnl", data.get("realized_daily_pnl", 0.0)) or 0.0),
                daily_trade_count=int(data.get("dailyTradeCount", data.get("daily_trade_count", 0)) or 0),
                paper_trading_permission=bool(data.get("paperTradingPermission", data.get("paper_trading_permission", True))),
                live_trading_permission=bool(data.get("liveTradingPermission", data.get("live_trading_permission", False))),
                event_blackout=bool(data.get("eventBlackout", data.get("event_blackout", False))),
                session_allowed=bool(data.get("sessionAllowed", data.get("session_allowed", True))),
                broker_quantity=int(data.get("brokerQuantity", data.get("broker_quantity", 0)) or 0),
                duplicate_order_intent_ids=tuple(data.get("duplicateOrderIntentIds", data.get("duplicate_order_intent_ids", ())) or ()),
                existing_position_symbols=tuple(data.get("existingPositionSymbols", data.get("existing_position_symbols", ())) or ()),
                max_quote_age_seconds=int(data.get("maxQuoteAgeSeconds", data.get("max_quote_age_seconds", 60)) or 60),
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
