from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRecord, MetaStrategyJobRepository, MetaStrategyJobStatus, MetaStrategyWorker
from backend.app.algorithms.meta_strategy.settings import META_STRATEGY_DEFAULT_SETTINGS_VERSION
from backend.app.algorithms.meta_strategy.strategy_registry import META_STRATEGY_STRATEGY_VERSION
from backend.app.algorithms.meta_strategy.versions import META_STRATEGY_FEATURE_SCHEMA_VERSION, META_STRATEGY_MODEL_VERSION


META_STRATEGY_RESEARCH_WORKFLOW_VERSION = "meta_strategy_research_workflows_v1"
META_STRATEGY_MODEL_PROMOTION_EVIDENCE_REQUIREMENTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("minimumSampleSize", "meta_strategy.promotion.minimum_sample_size_required", ("minimum_sample_size", "sampleSize", "sample_size")),
    ("chronologicalHoldout", "meta_strategy.promotion.chronological_holdout_required", ("chronological_holdout",)),
    ("walkForward", "meta_strategy.promotion.walk_forward_required", ("walk_forward", "walkForwardEvaluation", "walk_forward_evaluation")),
    ("probabilityCalibration", "meta_strategy.promotion.probability_calibration_required", ("probability_calibration",)),
    ("costAdjustedPerformance", "meta_strategy.promotion.cost_adjusted_performance_required", ("cost_adjusted_performance",)),
    ("regimeStability", "meta_strategy.promotion.regime_stability_required", ("regime_stability",)),
    ("missingDataBehavior", "meta_strategy.promotion.missing_data_behavior_required", ("missing_data_behavior",)),
    ("outOfDistributionHandling", "meta_strategy.promotion.ood_handling_required", ("out_of_distribution_handling", "oodHandling", "ood_handling")),
    ("deterministicReplay", "meta_strategy.promotion.deterministic_replay_required", ("deterministic_replay",)),
    ("paperStability", "meta_strategy.promotion.paper_stability_required", ("paper_stability",)),
    ("rollbackTesting", "meta_strategy.promotion.rollback_testing_required", ("rollback_testing",)),
)
META_STRATEGY_PAPER_EXECUTION_PROHIBITED_MODEL_MARKERS = (
    "reinforcement_learning",
    "reinforcement learning",
    "rl_policy",
    "q_learning",
    "policy_gradient",
    "actor_critic",
)
META_STRATEGY_RESEARCH_WORKFLOW_JOB_TYPES = (
    "deterministic_replay",
    "backtesting",
    "walk_forward_evaluation",
    "holdout_evaluation",
    "cost_slippage_analysis",
    "training",
    "model_inference_validation",
    "paper_stability_evaluation",
    "model_promotion",
    "settings_promotion",
    "report_generation",
)
RUNTIME_PARITY_ENTRYPOINT = "backend.app.algorithms.meta_strategy.execution_pipeline.run_meta_strategy_execution_pipeline"
WorkflowRunner = Callable[[str, dict[str, Any]], Mapping[str, Any]]


class MetaStrategyResearchWorkflowWorker(MetaStrategyWorker):
    workflow_job_types: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        repository: MetaStrategyJobRepository,
        queue_name: str,
        worker_id: str,
        runner: WorkflowRunner | None = None,
        lease_seconds: int = 300,
    ) -> None:
        super().__init__(repository=repository, queue_name=queue_name, worker_id=worker_id, lease_seconds=lease_seconds)
        self.runner = runner or default_research_workflow_runner

    def run_once(self, *, now: datetime | None = None, handler=None) -> dict[str, Any] | None:  # type: ignore[override]
        current = now or datetime.now(UTC)
        job = self.repository.claim_next_job(queue_name=self.queue_name, worker_id=self.worker_id, lease_seconds=self.lease_seconds, now=current)
        if job is None:
            self.repository.record_worker_heartbeat(worker_id=self.worker_id, queue_name=self.queue_name, now=current)
            return None
        if job.job_type not in self.workflow_job_types:
            self.repository.fail_job(job.job_id, worker_id=self.worker_id, error_category="UnsupportedWorkflow", error_details=f"unsupported workflow {job.job_type}", now=current)
            return {"status": "FAILED", "reasonCodes": ("meta_strategy.research.unsupported_workflow",)}
        if job.cancel_requested:
            self.repository.record_job_progress(job.job_id, worker_id=self.worker_id, status="CANCELLED", progress_percent=0.0, now=current)
            self.repository.cancel_job(job.job_id, now=current)
            return None
        self.repository.record_job_progress(job.job_id, worker_id=self.worker_id, status="RUNNING", progress_percent=0.0, payload={"workflowType": job.job_type}, now=current)
        try:
            payload = _job_payload(self.repository, job)
            guardrail_failure = _promotion_guardrail_failure(job, payload)
            if guardrail_failure:
                self.repository.record_job_progress(job.job_id, worker_id=self.worker_id, status="FAILED", progress_percent=100.0, payload={"reasonCode": guardrail_failure}, now=current)
                self.repository.dead_letter_job(job.job_id, worker_id=self.worker_id, error_category="PromotionGuardrailError", error_details=guardrail_failure, now=current)
                return {"status": "FAILED", "reasonCodes": (guardrail_failure,)}
            enriched_payload = _enrich_payload(job.job_type, payload)
            result = dict(self.runner(job.job_type, enriched_payload))
            promotion = None
            if job.job_type == "model_promotion":
                candidate = dict(payload.get("candidateArtifact") or payload.get("candidate_artifact") or {})
                promotion = self.repository.promote_model_atomically(
                    job=job,
                    model_artifact_id=str(candidate.get("artifactId") or candidate.get("artifact_id") or "candidate-unknown"),
                    evidence=dict(payload.get("evidence") or {}),
                    now=current,
                )
                result["promotion"] = promotion
            artifact = self.repository.persist_workflow_artifact(
                job=job,
                workflow_type=job.job_type,
                metadata=_workflow_metadata(job.job_type, enriched_payload),
                payload=result,
                now=current,
            )
            self.repository.record_job_progress(job.job_id, worker_id=self.worker_id, status="SUCCEEDED", progress_percent=100.0, payload={"artifactId": artifact["artifactId"]}, now=current)
            self.repository.complete_job(job.job_id, worker_id=self.worker_id, result={"artifactId": artifact["artifactId"], "workflowType": job.job_type, "promotion": promotion or {}}, now=current)
            return {"status": "SUCCEEDED", "artifactId": artifact["artifactId"], "workflowType": job.job_type}
        except Exception as exc:
            self.repository.record_job_progress(job.job_id, worker_id=self.worker_id, status="FAILED", progress_percent=100.0, payload={"errorCategory": type(exc).__name__}, now=current)
            self.repository.fail_job(job.job_id, worker_id=self.worker_id, error_category=type(exc).__name__, error_details=str(exc), now=current)
            return {"status": "FAILED", "reasonCodes": ("meta_strategy.research.workflow_failed",)}


class MetaStrategyTrainingWorker(MetaStrategyResearchWorkflowWorker):
    workflow_job_types = ("training",)

    def __init__(self, *, repository: MetaStrategyJobRepository, worker_id: str = "meta_strategy.training_worker", runner: WorkflowRunner | None = None) -> None:
        super().__init__(repository=repository, queue_name="training", worker_id=worker_id, runner=runner, lease_seconds=3600)


class MetaStrategyBacktestingWorker(MetaStrategyResearchWorkflowWorker):
    workflow_job_types = ("backtesting", "walk_forward_evaluation", "holdout_evaluation")

    def __init__(self, *, repository: MetaStrategyJobRepository, worker_id: str = "meta_strategy.backtesting_worker", runner: WorkflowRunner | None = None) -> None:
        super().__init__(repository=repository, queue_name="backtesting", worker_id=worker_id, runner=runner, lease_seconds=1800)


class MetaStrategyReplayWorker(MetaStrategyResearchWorkflowWorker):
    workflow_job_types = ("deterministic_replay",)

    def __init__(self, *, repository: MetaStrategyJobRepository, worker_id: str = "meta_strategy.replay_worker", runner: WorkflowRunner | None = None) -> None:
        super().__init__(repository=repository, queue_name="replay", worker_id=worker_id, runner=runner, lease_seconds=1800)


class MetaStrategyModelEvaluationWorker(MetaStrategyResearchWorkflowWorker):
    workflow_job_types = ("model_inference_validation", "paper_stability_evaluation", "cost_slippage_analysis")

    def __init__(self, *, repository: MetaStrategyJobRepository, worker_id: str = "meta_strategy.model_evaluation_worker", runner: WorkflowRunner | None = None) -> None:
        super().__init__(repository=repository, queue_name="model_evaluation", worker_id=worker_id, runner=runner, lease_seconds=1800)


class MetaStrategyPromotionWorker(MetaStrategyResearchWorkflowWorker):
    workflow_job_types = ("model_promotion", "settings_promotion")

    def __init__(self, *, repository: MetaStrategyJobRepository, worker_id: str = "meta_strategy.promotion_worker", runner: WorkflowRunner | None = None) -> None:
        super().__init__(repository=repository, queue_name="promotion", worker_id=worker_id, runner=runner, lease_seconds=600)


class MetaStrategyReportingWorker(MetaStrategyResearchWorkflowWorker):
    workflow_job_types = ("report_generation", "reporting")

    def __init__(self, *, repository: MetaStrategyJobRepository, worker_id: str = "meta_strategy.reporting_worker", runner: WorkflowRunner | None = None) -> None:
        super().__init__(repository=repository, queue_name="reporting", worker_id=worker_id, runner=runner, lease_seconds=900)


def default_research_workflow_runner(workflow_type: str, payload: dict[str, Any]) -> Mapping[str, Any]:
    return {
        "workflowType": workflow_type,
        "status": "COMPLETED",
        "runtimeParityEntrypoint": payload["runtimeParityEntrypoint"],
        "inputDigest": _stable_hash(payload),
        "mlShadowOnly": workflow_type in {"training", "model_inference_validation", "paper_stability_evaluation"},
        "reasonCodes": ("meta_strategy.research.workflow_completed_in_worker",),
    }


def _job_payload(repository: MetaStrategyJobRepository, job: MetaStrategyJobRecord) -> dict[str, Any]:
    stored = repository.read_payload(job.payload_reference)
    payload = stored.get("payload")
    return dict(payload) if isinstance(payload, Mapping) else {}


def _enrich_payload(workflow_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "workflowVersion": META_STRATEGY_RESEARCH_WORKFLOW_VERSION,
        "workflowType": workflow_type,
        "runtimeParityEntrypoint": RUNTIME_PARITY_ENTRYPOINT,
        "codeBuildIdentifier": _code_build_identifier(),
    }


def _workflow_metadata(workflow_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    training_args = payload.get("trainingArguments") if isinstance(payload.get("trainingArguments"), Mapping) else {}
    return {
        "workflowVersion": META_STRATEGY_RESEARCH_WORKFLOW_VERSION,
        "workflowType": workflow_type,
        "dataVersion": str(payload.get("datasetVersion") or payload.get("snapshotSetVersion") or payload.get("executionDatasetVersion") or training_args.get("datasetVersion") or "inline-payload"),
        "featureVersion": str(payload.get("featureVersion") or META_STRATEGY_FEATURE_SCHEMA_VERSION),
        "settingsVersion": str(payload.get("settingsVersion") or payload.get("settings_version") or META_STRATEGY_DEFAULT_SETTINGS_VERSION),
        "strategyVersions": payload.get("strategyVersions") or {"catalog": META_STRATEGY_STRATEGY_VERSION},
        "modelVersion": str(payload.get("modelVersion") or payload.get("model_version") or META_STRATEGY_MODEL_VERSION),
        "randomSeed": int(payload.get("randomSeed") or payload.get("random_seed") or 0),
        "transactionCostAssumptions": payload.get("transactionCostAssumptions") or payload.get("transaction_cost_assumptions") or {"source": "settings_or_payload_default"},
        "codeBuildIdentifier": str(payload.get("codeBuildIdentifier") or _code_build_identifier()),
        "runtimeParityEntrypoint": RUNTIME_PARITY_ENTRYPOINT,
    }


def _promotion_guardrail_failure(job: MetaStrategyJobRecord, payload: Mapping[str, Any]) -> str | None:
    if job.job_type != "model_promotion":
        return None
    candidate = dict(payload.get("candidateArtifact") or payload.get("candidate_artifact") or {})
    evidence = dict(payload.get("evidence") or {})
    if _candidate_uses_reinforcement_learning(candidate):
        return "meta_strategy.promotion.reinforcement_learning_not_allowed_for_paper_execution"
    for canonical, reason_code, aliases in META_STRATEGY_MODEL_PROMOTION_EVIDENCE_REQUIREMENTS:
        if not _evidence_passed(evidence, canonical, aliases):
            return reason_code
    if str(candidate.get("generatedByJobId") or candidate.get("generated_by_job_id") or "") in {job.job_id, "self"}:
        return "meta_strategy.promotion.model_cannot_promote_itself"
    return None


def _evidence_passed(evidence: Mapping[str, Any], canonical: str, aliases: tuple[str, ...]) -> bool:
    value = _first_present(evidence, canonical, *aliases)
    if isinstance(value, Mapping):
        truthy_keys = ("passed", "validated", "stable", "complete", "approved")
        if any(value.get(key) is True for key in truthy_keys):
            return True
        if canonical == "minimumSampleSize":
            rows = value.get("rows") or value.get("sampleSize") or value.get("sample_size") or value.get("n")
            return _positive_number(rows)
        return False
    return value is True


def _candidate_uses_reinforcement_learning(candidate: Mapping[str, Any]) -> bool:
    fields = (
        "kind",
        "modelKind",
        "model_kind",
        "trainingMethod",
        "training_method",
        "learningMethod",
        "learning_method",
        "algorithm",
        "championModel",
    )
    if any(_contains_prohibited_model_marker(candidate.get(field)) for field in fields):
        return True
    models = candidate.get("models")
    if isinstance(models, Mapping):
        return any(isinstance(model, Mapping) and _candidate_uses_reinforcement_learning(model) for model in models.values())
    return False


def _contains_prohibited_model_marker(value: Any) -> bool:
    text = str(value or "").replace("-", "_").lower()
    return any(marker in text for marker in META_STRATEGY_PAPER_EXECUTION_PROHIBITED_MODEL_MARKERS)


def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _positive_number(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _code_build_identifier() -> str:
    explicit = os.getenv("BUILD_SHA") or os.getenv("GIT_COMMIT")
    if explicit:
        return str(explicit)
    return f"local-{_stable_hash(META_STRATEGY_RESEARCH_WORKFLOW_VERSION)}"


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


__all__ = [
    "META_STRATEGY_RESEARCH_WORKFLOW_JOB_TYPES",
    "META_STRATEGY_RESEARCH_WORKFLOW_VERSION",
    "MetaStrategyBacktestingWorker",
    "MetaStrategyModelEvaluationWorker",
    "MetaStrategyPromotionWorker",
    "MetaStrategyReplayWorker",
    "MetaStrategyReportingWorker",
    "MetaStrategyResearchWorkflowWorker",
    "MetaStrategyTrainingWorker",
    "default_research_workflow_runner",
]
