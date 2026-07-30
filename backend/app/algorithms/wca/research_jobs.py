"""WCA research worker job contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import Field, model_validator

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaContractModel


WCA_RESEARCH_JOB_SCHEMA_VERSION = "wca_research_job_v1"
WCA_RESEARCH_WORKER_VERSION = "wca_research_worker_v1"


class WcaResearchJobType(str, Enum):
    BACKTEST = "backtest"
    BACKTEST_MODES = "backtest_modes"
    WALK_FORWARD = "walk_forward"
    HOLDOUT = "holdout"
    HISTORICAL_REPLAY = "historical_replay"
    CONFIDENCE_CALIBRATION = "confidence_calibration"
    PERFORMANCE_STATISTICS_UPDATE = "performance_statistics_update"
    WEIGHT_CANDIDATE_CALCULATION = "weight_candidate_calculation"
    COMPUTE_STRATEGY_WEIGHT_CANDIDATE = "compute_strategy_weight_candidate"
    VALIDATE_STRATEGY_WEIGHT_CANDIDATE = "validate_strategy_weight_candidate"
    PROMOTE_STRATEGY_WEIGHT_VERSION = "promote_strategy_weight_version"
    ROLLBACK_STRATEGY_WEIGHT_VERSION = "rollback_strategy_weight_version"
    CORRELATION_ANALYSIS = "correlation_analysis"
    STRATEGY_HEALTH_ANALYSIS = "strategy_health_analysis"
    SHADOW_COMPARISON = "shadow_comparison"
    PAPER_STABILITY_REPORT = "paper_stability_report"
    EXPORT_REPORT_GENERATION = "export_report_generation"


class WcaResearchJobStatus(str, Enum):
    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    QUARANTINED = "QUARANTINED"


TERMINAL_RESEARCH_JOB_STATUSES = {
    WcaResearchJobStatus.SUCCEEDED.value,
    WcaResearchJobStatus.FAILED.value,
    WcaResearchJobStatus.CANCELLED.value,
    WcaResearchJobStatus.EXPIRED.value,
    WcaResearchJobStatus.QUARANTINED.value,
}


class WcaResearchJob(WcaContractModel):
    algorithm_id: str = WCA_ALGORITHM_ID
    schema_version: str = WCA_RESEARCH_JOB_SCHEMA_VERSION
    job_id: str = Field(default_factory=lambda: f"wca-research-{uuid4().hex}", min_length=1)
    job_type: WcaResearchJobType
    account_id: str = Field(default="paper", min_length=1)
    symbol: str = Field(default="SPY", min_length=1)
    configuration_version: str = "wca_research_job"
    run_id: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    max_attempts: int = Field(default=3, ge=1)
    priority: int = Field(default=50, ge=0, le=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_research_job(self) -> "WcaResearchJob":
        if self.algorithm_id != WCA_ALGORITHM_ID:
            raise ValueError("WCA research jobs must be WCA scoped")
        if self.symbol.upper() != "SPY":
            raise ValueError("WCA research worker currently accepts SPY jobs only")
        return self


class WcaResearchJobReceipt(WcaContractModel):
    job_id: str
    job_type: WcaResearchJobType | str
    status: WcaResearchJobStatus | str = WcaResearchJobStatus.QUEUED
    queued: bool = True
    reason_codes: tuple[str, ...] = ("wca.research.job.queued",)


class WcaResearchJobSnapshot(WcaContractModel):
    job_id: str
    job_type: str
    status: str
    progress_percent: float = Field(ge=0, le=100)
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    cancel_requested: bool = False
    result_reference: dict[str, Any] = Field(default_factory=dict)
    logs: tuple[str, ...] = ()
    error: dict[str, Any] = Field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()


def research_job(
    job_type: WcaResearchJobType,
    *,
    payload: dict[str, Any],
    run_id: str = "",
    configuration_version: str = "wca_research_job",
    priority: int = 50,
    expires_in_seconds: int | None = 3600,
    reason_codes: tuple[str, ...] = (),
) -> WcaResearchJob:
    now = datetime.now(timezone.utc)
    return WcaResearchJob(
        job_type=job_type,
        run_id=run_id,
        configuration_version=configuration_version,
        created_at=now,
        expires_at=now + timedelta(seconds=expires_in_seconds) if expires_in_seconds is not None else None,
        priority=priority,
        payload=payload,
        reason_codes=reason_codes,
    )


__all__ = [
    "TERMINAL_RESEARCH_JOB_STATUSES",
    "WCA_RESEARCH_JOB_SCHEMA_VERSION",
    "WCA_RESEARCH_WORKER_VERSION",
    "WcaResearchJob",
    "WcaResearchJobReceipt",
    "WcaResearchJobSnapshot",
    "WcaResearchJobStatus",
    "WcaResearchJobType",
    "research_job",
]
