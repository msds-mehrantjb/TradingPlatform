"""Standalone WCA research worker for expensive non-latency-critical work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.app.algorithms.wca.confidence import ConfidenceCalibrationConfig, build_calibration_table
from backend.app.algorithms.wca.contracts import WcaBacktestRequest, WcaConfidenceCalibrationOutcome, WcaEvaluateRequest, WcaPaperStabilityValidationRequest, WcaSide, WcaStrategyPerformanceRecord
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.research_jobs import WCA_RESEARCH_WORKER_VERSION, WcaResearchJob, WcaResearchJobType
from backend.app.algorithms.wca.research_repository import WcaResearchRepository
from backend.app.algorithms.wca.service import WcaService
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY
from backend.app.algorithms.wca.weights import WcaWeightEngineConfig, performance_weight_snapshot


WCA_RESEARCH_WORKER_REQUIRES_OS_PROCESS = True
WCA_RESEARCH_JOB_TYPES = tuple(item.value for item in WcaResearchJobType)
WCA_RESEARCH_CANDIDATE_JOB_TYPES = {
    WcaResearchJobType.CONFIDENCE_CALIBRATION.value,
    WcaResearchJobType.WEIGHT_CANDIDATE_CALCULATION.value,
}


@dataclass(frozen=True)
class WcaResearchWorkerSettings:
    lease_seconds: int = 900
    poll_seconds: float = 2.0


class WcaResearchWorker:
    def __init__(
        self,
        *,
        repository: WcaSqliteRepository | None = None,
        research_repository: WcaResearchRepository | None = None,
        settings: WcaResearchWorkerSettings | None = None,
        owner_id: str | None = None,
    ) -> None:
        self.repository = repository or WcaSqliteRepository()
        self.research_repository = research_repository or WcaResearchRepository(self.repository)
        self._service: WcaService | None = None
        self.settings = settings or WcaResearchWorkerSettings()
        self.owner_id = owner_id or f"wca-research-worker-{uuid4().hex}"

    @property
    def service(self) -> WcaService:
        if self._service is None:
            self._service = WcaService(repository=self.repository, research_repository=self.research_repository)
        return self._service

    def run_once(self) -> dict[str, Any]:
        job = self.research_repository.claim_next_job(owner_id=self.owner_id, lease_seconds=self.settings.lease_seconds)
        if job is None:
            return {"status": "idle", "reasonCodes": ["wca.research.worker.idle"]}
        if self.research_repository.cancellation_requested(job.job_id):
            self.research_repository.cancel_job(job.job_id)
            return {"status": "cancelled", "jobId": job.job_id, "reasonCodes": ["wca.research.job.cancelled_before_running"]}
        if not self.research_repository.mark_running(job.job_id, owner_id=self.owner_id):
            return {"status": "lost_lease", "jobId": job.job_id, "reasonCodes": ["wca.research.job.lease_lost"]}
        try:
            result = self._execute_job(job)
            if self.research_repository.cancellation_requested(job.job_id):
                self.research_repository.cancel_job(job.job_id)
                return {"status": "cancelled", "jobId": job.job_id, "reasonCodes": ["wca.research.job.cancelled_after_running"]}
            self.research_repository.complete_job(job.job_id, result_reference=result)
            return {"status": "succeeded", "jobId": job.job_id, "resultReference": result}
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc), "workerVersion": WCA_RESEARCH_WORKER_VERSION}
            self.research_repository.fail_job(job.job_id, error=error)
            return {"status": "failed", "jobId": job.job_id, "error": error, "reasonCodes": ["wca.research.job.failed"]}

    def _execute_job(self, job: WcaResearchJob) -> dict[str, Any]:
        job_type = str(job.job_type)
        self.research_repository.update_progress(job.job_id, progress_percent=5, log="wca.research.job.started")
        if job_type == WcaResearchJobType.BACKTEST.value:
            result = self.service.run_backtest(WcaBacktestRequest.model_validate(job.payload["request"]))
            self.research_repository.update_progress(job.job_id, progress_percent=100, log="wca.research.backtest.completed")
            return {"kind": "backtest_result", "runId": result.run_configuration.run_id, "jobId": job.job_id}
        if job_type == WcaResearchJobType.BACKTEST_MODES.value:
            result = self.service.run_backtest_modes(WcaBacktestRequest.model_validate(job.payload["request"]))
            self.research_repository.update_progress(job.job_id, progress_percent=100, log="wca.research.backtest_modes.completed")
            return {"kind": "backtest_suite", "suiteId": result.suite_id, "jobId": job.job_id}
        if job_type in {WcaResearchJobType.WALK_FORWARD.value, WcaResearchJobType.HOLDOUT.value, WcaResearchJobType.HISTORICAL_REPLAY.value}:
            result = self.service.run_backtest(WcaBacktestRequest.model_validate(job.payload["request"]))
            self.research_repository.update_progress(job.job_id, progress_percent=100, log=f"wca.research.{job_type}.completed")
            return {"kind": job_type, "runId": result.run_configuration.run_id, "jobId": job.job_id}
        if job_type == WcaResearchJobType.SHADOW_COMPARISON.value:
            evidence = self.service.record_shadow_comparison_evidence(WcaEvaluateRequest.model_validate(job.payload["request"]))
            self.research_repository.update_progress(job.job_id, progress_percent=100, log="wca.research.shadow_comparison.completed")
            return {"kind": "shadow_comparison_evidence", "evidenceId": evidence.evidence_id, "jobId": job.job_id}
        if job_type == WcaResearchJobType.PAPER_STABILITY_REPORT.value:
            result = self.service.validate_paper_stability(WcaPaperStabilityValidationRequest.model_validate(job.payload["request"]))
            self.research_repository.update_progress(job.job_id, progress_percent=100, log="wca.research.paper_stability.completed")
            return {"kind": "paper_stability_report", "validationId": result.validation_id, "jobId": job.job_id}
        if job_type == WcaResearchJobType.CONFIDENCE_CALIBRATION.value:
            result = self._confidence_calibration_candidate(job)
            self.research_repository.update_progress(job.job_id, progress_percent=100, log="wca.research.confidence_calibration.candidate_created")
            return result
        if job_type == WcaResearchJobType.WEIGHT_CANDIDATE_CALCULATION.value:
            result = self._weight_candidate(job)
            self.research_repository.update_progress(job.job_id, progress_percent=100, log="wca.research.weight_candidate.candidate_created")
            return result
        self.research_repository.update_progress(job.job_id, progress_percent=100, log=f"wca.research.{job_type}.report_generated")
        return {"kind": job_type, "jobId": job.job_id, "reportReference": f"wca-research-report-{job.job_id}"}

    def _confidence_calibration_candidate(self, job: WcaResearchJob) -> dict[str, Any]:
        as_of = _parse_dt(job.payload.get("as_of") or job.payload.get("cutoff")) or datetime.now(timezone.utc)
        config = ConfidenceCalibrationConfig(**job.payload.get("config", {}))
        outcomes = tuple(WcaConfidenceCalibrationOutcome.model_validate(row) for row in job.payload.get("outcomes", ()))
        tables = []
        for definition in WCA_STRATEGY_REGISTRY:
            versions = sorted({outcome.strategy_version for outcome in outcomes if outcome.strategy_id == definition.strategy_id}) or [definition.strategy_version]
            for strategy_version in versions:
                base = tuple(outcome for outcome in outcomes if outcome.strategy_id == definition.strategy_id and outcome.strategy_version == strategy_version)
                tables.append(build_calibration_table(strategy_id=definition.strategy_id, strategy_version=strategy_version, outcomes=outcomes, as_of=as_of, config=config))
                for direction in (WcaSide.BUY, WcaSide.SELL):
                    if sum(1 for outcome in base if _side(outcome.direction) == direction.value and outcome.outcome_available_at < as_of) >= config.direction_minimum_samples:
                        tables.append(build_calibration_table(strategy_id=definition.strategy_id, strategy_version=strategy_version, outcomes=outcomes, as_of=as_of, direction=direction, config=config))
                regimes = sorted({outcome.regime for outcome in base if outcome.regime != "default"})
                for regime in regimes:
                    if sum(1 for outcome in base if outcome.regime == regime and outcome.outcome_available_at < as_of) >= config.regime_minimum_samples:
                        tables.append(build_calibration_table(strategy_id=definition.strategy_id, strategy_version=strategy_version, outcomes=outcomes, as_of=as_of, regime=regime, config=config))
        candidate_version = f"{job.job_id}.confidence_calibration.{as_of.strftime('%Y%m%d%H%M%S')}"
        candidate_id = self.research_repository.save_candidate_result(
            job_id=job.job_id,
            candidate_type=WcaResearchJobType.CONFIDENCE_CALIBRATION.value,
            candidate_version=candidate_version,
            payload={
                "job_id": job.job_id,
                "job_type": WcaResearchJobType.CONFIDENCE_CALIBRATION.value,
                "configuration_version": job.configuration_version,
                "activation_policy": "promotion_required",
                "active_runtime_state_modified": False,
                "calibration_tables": [table.model_dump(mode="json") for table in tables],
                "outcome_cutoff_timestamp": as_of.isoformat(),
                "reason_codes": ["wca.research.confidence_calibration.versioned_candidate", "wca.research.candidate.requires_separate_promotion"],
            },
        )
        return {"kind": "research_candidate", "candidateId": candidate_id, "candidateVersion": candidate_version, "tableCount": len(tables), "promotionRequired": True, "jobId": job.job_id}

    def _weight_candidate(self, job: WcaResearchJob) -> dict[str, Any]:
        cutoff = _parse_dt(job.payload.get("cutoff") or job.payload.get("as_of")) or datetime.now(timezone.utc)
        candidate_version = f"{job.job_id}.weight_candidate.{cutoff.strftime('%Y%m%d%H%M%S')}"
        config_payload = dict(job.payload.get("config", {}))
        config_payload["weight_version"] = candidate_version
        config = WcaWeightEngineConfig(**config_payload)
        records = tuple(WcaStrategyPerformanceRecord.model_validate(row) for row in job.payload.get("performance_records", ()))
        if not records:
            records = self.repository.load_strategy_performance_records(symbol=job.symbol, as_of=cutoff)
        snapshot = performance_weight_snapshot(records=records, cutoff=cutoff, config=config, regime=str(job.payload.get("regime") or "default"))
        candidate_id = self.research_repository.save_candidate_result(
            job_id=job.job_id,
            candidate_type=WcaResearchJobType.WEIGHT_CANDIDATE_CALCULATION.value,
            candidate_version=candidate_version,
            payload={
                "job_id": job.job_id,
                "job_type": WcaResearchJobType.WEIGHT_CANDIDATE_CALCULATION.value,
                "configuration_version": job.configuration_version,
                "activation_policy": "promotion_required",
                "active_runtime_state_modified": False,
                "weight_snapshot": snapshot.model_dump(mode="json"),
                "record_count": len(records),
                "metrics_cutoff_timestamp": cutoff.isoformat(),
                "reason_codes": ["wca.research.weight_candidate.versioned_candidate", "wca.research.candidate.requires_separate_promotion"],
            },
        )
        return {"kind": "research_candidate", "candidateId": candidate_id, "candidateVersion": candidate_version, "weightVersion": snapshot.weight_version, "recordCount": len(records), "promotionRequired": True, "jobId": job.job_id}


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _side(value: WcaSide | str) -> str:
    return value.value if isinstance(value, WcaSide) else str(value)


__all__ = [
    "WCA_RESEARCH_CANDIDATE_JOB_TYPES",
    "WCA_RESEARCH_JOB_TYPES",
    "WCA_RESEARCH_WORKER_REQUIRES_OS_PROCESS",
    "WcaResearchWorker",
    "WcaResearchWorkerSettings",
]
