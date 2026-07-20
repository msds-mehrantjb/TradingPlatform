"""Backend-generated promotion evidence for Meta-Strategy model artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Mapping

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.models import artifact_hash


META_STRATEGY_PROMOTION_EVIDENCE_FIELDS = (
    "walk_forward_result",
    "holdout_result",
    "calibration_result",
    "economic_baseline_comparison",
    "drawdown_result",
    "side_coverage",
    "regime_coverage",
    "shadow_comparison",
    "paper_stability",
    "operational_failures",
    "risk_violations",
    "reconciliation_failures",
    "rollback_artifact",
)


class PromotionEvidenceSourceError(ValueError):
    pass


@dataclass(frozen=True)
class MetaStrategyPromotionEvidence:
    algorithm_id: str
    artifact_id: str
    artifact_hash: str
    walk_forward_result: Mapping[str, Any]
    holdout_result: Mapping[str, Any]
    calibration_result: Mapping[str, Any]
    economic_baseline_comparison: Mapping[str, Any]
    drawdown_result: Mapping[str, Any]
    side_coverage: Mapping[str, Any]
    regime_coverage: Mapping[str, Any]
    shadow_comparison: Mapping[str, Any]
    paper_stability: Mapping[str, Any]
    operational_failures: Mapping[str, Any]
    risk_violations: Mapping[str, Any]
    reconciliation_failures: Mapping[str, Any]
    rollback_artifact: Mapping[str, Any]
    evidence_timestamps: Mapping[str, datetime]
    generated_at: datetime
    evidence_source: str = "backend"
    reason_codes: tuple[str, ...] = field(default=("meta_strategy.promotion.evidence.backend_generated",))

    def __post_init__(self) -> None:
        if self.algorithm_id != ALGORITHM_ID:
            raise ValueError("promotion evidence must be attributed to meta_strategy")
        if self.evidence_source != "backend":
            raise PromotionEvidenceSourceError("frontend-provided promotion evidence is not trusted")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        normalized_timestamps = {}
        for key, timestamp in dict(self.evidence_timestamps).items():
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("evidence timestamps must be timezone-aware")
            normalized_timestamps[str(key)] = timestamp.astimezone(UTC)
        object.__setattr__(self, "evidence_timestamps", MappingProxyType(normalized_timestamps))
        for field_name in META_STRATEGY_PROMOTION_EVIDENCE_FIELDS:
            object.__setattr__(self, field_name, MappingProxyType(dict(getattr(self, field_name))))


def build_meta_strategy_promotion_evidence(
    *,
    candidate_artifact: Mapping[str, Any],
    walk_forward_result: Mapping[str, Any],
    holdout_result: Mapping[str, Any],
    calibration_result: Mapping[str, Any],
    economic_baseline_comparison: Mapping[str, Any],
    drawdown_result: Mapping[str, Any],
    side_coverage: Mapping[str, Any],
    regime_coverage: Mapping[str, Any],
    shadow_comparison: Mapping[str, Any],
    paper_stability: Mapping[str, Any],
    operational_failures: Mapping[str, Any],
    risk_violations: Mapping[str, Any],
    reconciliation_failures: Mapping[str, Any],
    rollback_artifact: Mapping[str, Any],
    evidence_timestamps: Mapping[str, datetime] | None = None,
    generated_at: datetime | None = None,
    evidence_source: str = "backend",
) -> MetaStrategyPromotionEvidence:
    if evidence_source != "backend":
        raise PromotionEvidenceSourceError("frontend-provided promotion evidence is not trusted")
    generated = (generated_at or datetime.now(tz=UTC)).astimezone(UTC)
    artifact_id = str(candidate_artifact.get("artifactId") or candidate_artifact.get("artifact_id") or "")
    if not artifact_id:
        raise ValueError("candidate artifact must include artifactId")
    expected_hash = artifact_hash(dict(candidate_artifact))
    supplied_hash = str(candidate_artifact.get("artifactHash") or expected_hash)
    if supplied_hash != expected_hash:
        raise ValueError("candidate artifact hash mismatch")
    timestamps = dict(evidence_timestamps or {field_name: generated for field_name in META_STRATEGY_PROMOTION_EVIDENCE_FIELDS})
    return MetaStrategyPromotionEvidence(
        algorithm_id=ALGORITHM_ID,
        artifact_id=artifact_id,
        artifact_hash=expected_hash,
        walk_forward_result=walk_forward_result,
        holdout_result=holdout_result,
        calibration_result=calibration_result,
        economic_baseline_comparison=economic_baseline_comparison,
        drawdown_result=drawdown_result,
        side_coverage=side_coverage,
        regime_coverage=regime_coverage,
        shadow_comparison=shadow_comparison,
        paper_stability=paper_stability,
        operational_failures=operational_failures,
        risk_violations=risk_violations,
        reconciliation_failures=reconciliation_failures,
        rollback_artifact=rollback_artifact,
        evidence_timestamps=timestamps,
        generated_at=generated,
        evidence_source=evidence_source,
    )


def evidence_matches_candidate_artifact(evidence: MetaStrategyPromotionEvidence, candidate_artifact: Mapping[str, Any]) -> bool:
    artifact_id = str(candidate_artifact.get("artifactId") or candidate_artifact.get("artifact_id") or "")
    return artifact_id == evidence.artifact_id and artifact_hash(dict(candidate_artifact)) == evidence.artifact_hash


__all__ = [
    "META_STRATEGY_PROMOTION_EVIDENCE_FIELDS",
    "MetaStrategyPromotionEvidence",
    "PromotionEvidenceSourceError",
    "build_meta_strategy_promotion_evidence",
    "evidence_matches_candidate_artifact",
]
