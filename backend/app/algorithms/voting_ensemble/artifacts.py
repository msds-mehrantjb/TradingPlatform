from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from backend.app.domain.models import DomainModel, _require_utc


VOTING_ENSEMBLE_ARTIFACTS_VERSION = "voting_ensemble_artifacts_v1"
VOTING_ENSEMBLE_REPORTS_VERSION = "voting_ensemble_reports_v1"

VotingEnsembleArtifactKind = Literal[
    "backtest_result",
    "walk_forward_result",
    "parameter_tuning_report",
    "ml_model_artifact",
    "performance_snapshot",
    "reliability_history",
    "family_performance_history",
]


def artifacts_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_ARTIFACTS_VERSION,
        VOTING_ENSEMBLE_REPORTS_VERSION,
        "voting_ensemble.artifacts.versioned_envelope",
        "voting_ensemble.artifacts.content_hash",
        "voting_ensemble.reports.algorithm_owned",
    )


class VotingEnsembleArtifactEnvelope(DomainModel):
    artifactVersion: str = VOTING_ENSEMBLE_ARTIFACTS_VERSION
    artifactId: str = Field(min_length=1)
    artifactKind: VotingEnsembleArtifactKind
    createdAt: datetime
    contentHash: str = Field(min_length=1)
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    reasonCodes: list[str] = Field(default_factory=lambda: list(artifacts_reason_codes()))
    explanation: str = Field(min_length=1)

    @field_validator("createdAt")
    @classmethod
    def created_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class VotingEnsembleReportEnvelope(DomainModel):
    reportVersion: str = VOTING_ENSEMBLE_REPORTS_VERSION
    reportId: str = Field(min_length=1)
    reportKind: VotingEnsembleArtifactKind
    generatedAt: datetime
    artifact: VotingEnsembleArtifactEnvelope
    summary: dict[str, Any] = Field(default_factory=dict)
    reasonCodes: list[str] = Field(default_factory=lambda: list(artifacts_reason_codes()))
    explanation: str = Field(min_length=1)

    @field_validator("generatedAt")
    @classmethod
    def generated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


def build_voting_ensemble_artifact(
    *,
    artifact_kind: VotingEnsembleArtifactKind,
    payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> VotingEnsembleArtifactEnvelope:
    created = (created_at or datetime.now(UTC)).astimezone(UTC)
    content_hash = voting_ensemble_artifact_hash(payload)
    artifact_id = f"ve-{artifact_kind}-{content_hash}"
    return VotingEnsembleArtifactEnvelope(
        artifactId=artifact_id,
        artifactKind=artifact_kind,
        createdAt=created,
        contentHash=content_hash,
        payload=payload,
        metadata=metadata or {},
        explanation=f"Voting Ensemble {artifact_kind} artifact is wrapped in an algorithm-owned versioned envelope.",
    )


def build_voting_ensemble_report(
    *,
    report_kind: VotingEnsembleArtifactKind,
    payload: dict[str, Any],
    summary: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> VotingEnsembleReportEnvelope:
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    artifact = build_voting_ensemble_artifact(
        artifact_kind=report_kind,
        payload=payload,
        metadata=metadata,
        created_at=generated,
    )
    return VotingEnsembleReportEnvelope(
        reportId=f"ve-report-{report_kind}-{artifact.contentHash}",
        reportKind=report_kind,
        generatedAt=generated,
        artifact=artifact,
        summary=summary or {},
        explanation=f"Voting Ensemble {report_kind} report references a dedicated artifact envelope.",
    )


def voting_ensemble_artifact_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

