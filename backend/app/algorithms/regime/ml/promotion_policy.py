"""Backend-authoritative Regime ML promotion policy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol


REGIME_ML_PROMOTION_POLICY_VERSION = "regime_ml_promotion_policy_v1"
REGIME_ML_MAX_AUTOMATIC_PROMOTION_MODE = "confirm_only"


@dataclass(frozen=True)
class RegimeMlCandidateArtifact:
    artifact_id: str
    artifact_hash: str
    model_version: str
    feature_schema_version: str
    label_version: str
    deterministic_baseline_version: str


@dataclass(frozen=True)
class RegimeMlPromotionEvidence:
    artifact_id: str
    artifact_hash: str
    model_version: str
    feature_schema_version: str
    label_version: str
    deterministic_baseline_version: str
    walk_forward_passed: bool
    untouched_holdout_passed: bool
    deterministic_baseline_comparison_passed: bool
    calibration_passed: bool
    leakage_tests_passed: bool
    paper_stability_passed: bool
    paper_shadow_decision_count: int
    paper_trading_day_count: int
    distinct_regimes_observed: int
    minimum_regime_coverage_passed: bool
    global_risk_violations: int
    unexpected_decision_mutations: int
    broker_reconciliation_failures: int
    operational_errors: int
    performance_review_passed: bool
    rollback_artifact_retained: bool
    tests_passed: bool
    evidence_generated_at: str
    evidence_expiration_at: str
    trusted_backend_record: bool = True

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RegimeMlPromotionDecision:
    promoted: bool
    target_mode: str
    maximum_automatic_promotion_mode: str
    reason_codes: tuple[str, ...]
    policy_version: str
    evidence_current: bool
    evidence_matches_candidate_artifact: bool
    frontend_supplied_evidence_rejected: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class RegimeMlPromotionEvidenceRepository(Protocol):
    def latest_regime_ml_promotion_evidence(self, artifact_id: str) -> dict | None:
        ...


def evaluate_regime_ml_promotion_policy(
    candidate: RegimeMlCandidateArtifact,
    repository: RegimeMlPromotionEvidenceRepository,
    *,
    now: datetime | None = None,
    frontend_supplied_evidence: dict | None = None,
) -> RegimeMlPromotionDecision:
    current_time = now or datetime.now(tz=UTC)
    evidence = _coerce_evidence(repository.latest_regime_ml_promotion_evidence(candidate.artifact_id))
    reasons: list[str] = []
    frontend_rejected = frontend_supplied_evidence is not None
    if frontend_rejected:
        reasons.append("regime.ml.promotion.frontend_supplied_evidence_rejected")
    if evidence is None:
        reasons.append("regime.ml.promotion.missing_backend_evidence")
        return _blocked(reasons, False, False, frontend_rejected)

    matches = evidence_matches_candidate_artifact(evidence, candidate)
    current = evidence_is_current(evidence, current_time)
    if not evidence.trusted_backend_record:
        reasons.append("regime.ml.promotion.untrusted_backend_evidence")
    if not current:
        reasons.append("regime.ml.promotion.stale_evidence")
    if not matches:
        reasons.extend(_artifact_mismatch_reasons(evidence, candidate))
    mandatory_checks = {
        "leakage_tests_passed": evidence.leakage_tests_passed,
        "deterministic_baseline_comparison_passed": evidence.deterministic_baseline_comparison_passed,
        "walk_forward_passed": evidence.walk_forward_passed,
        "untouched_holdout_passed": evidence.untouched_holdout_passed,
        "calibration_passed": evidence.calibration_passed,
        "paper_stability_passed": evidence.paper_stability_passed,
        "minimum_regime_coverage_passed": evidence.minimum_regime_coverage_passed,
        "performance_review_passed": evidence.performance_review_passed,
        "rollback_artifact_retained": evidence.rollback_artifact_retained,
        "tests_passed": evidence.tests_passed,
    }
    for field, passed in mandatory_checks.items():
        if not passed:
            reasons.append(f"regime.ml.promotion.{field}_required")
    if evidence.paper_trading_day_count <= 0:
        reasons.append("regime.ml.promotion.paper_days_required")
    if evidence.paper_shadow_decision_count <= 0:
        reasons.append("regime.ml.promotion.paper_shadow_decisions_required")
    if evidence.distinct_regimes_observed <= 1:
        reasons.append("regime.ml.promotion.multi_regime_paper_evidence_required")
    if evidence.global_risk_violations != 0:
        reasons.append("regime.ml.promotion.global_risk_violation_present")
    if evidence.unexpected_decision_mutations != 0:
        reasons.append("regime.ml.promotion.unexpected_decision_mutation_present")
    if evidence.broker_reconciliation_failures != 0:
        reasons.append("regime.ml.promotion.broker_reconciliation_failure_present")
    if evidence.operational_errors != 0:
        reasons.append("regime.ml.promotion.operational_error_present")
    promoted = not reasons and current and matches and evidence.trusted_backend_record
    return RegimeMlPromotionDecision(
        promoted=promoted,
        target_mode="confirm_only" if promoted else "shadow",
        maximum_automatic_promotion_mode=REGIME_ML_MAX_AUTOMATIC_PROMOTION_MODE,
        reason_codes=("regime.ml.promotion.confirm_only_allowed",) if promoted else tuple(dict.fromkeys(reasons)),
        policy_version=REGIME_ML_PROMOTION_POLICY_VERSION,
        evidence_current=current,
        evidence_matches_candidate_artifact=matches,
        frontend_supplied_evidence_rejected=frontend_rejected,
    )


def evidence_matches_candidate_artifact(evidence: RegimeMlPromotionEvidence, candidate: RegimeMlCandidateArtifact) -> bool:
    return (
        evidence.artifact_id == candidate.artifact_id
        and evidence.artifact_hash == candidate.artifact_hash
        and evidence.model_version == candidate.model_version
        and evidence.feature_schema_version == candidate.feature_schema_version
        and evidence.label_version == candidate.label_version
        and evidence.deterministic_baseline_version == candidate.deterministic_baseline_version
    )


def evidence_is_current(evidence: RegimeMlPromotionEvidence, now: datetime) -> bool:
    try:
        generated_at = _parse_time(evidence.evidence_generated_at)
        expiration_at = _parse_time(evidence.evidence_expiration_at)
    except ValueError:
        return False
    return generated_at <= now <= expiration_at


def _artifact_mismatch_reasons(evidence: RegimeMlPromotionEvidence, candidate: RegimeMlCandidateArtifact) -> tuple[str, ...]:
    reasons: list[str] = []
    if evidence.artifact_hash != candidate.artifact_hash:
        reasons.append("regime.ml.promotion.artifact_hash_mismatch")
    if evidence.model_version != candidate.model_version:
        reasons.append("regime.ml.promotion.model_version_mismatch")
    if evidence.feature_schema_version != candidate.feature_schema_version:
        reasons.append("regime.ml.promotion.feature_schema_version_mismatch")
    if evidence.label_version != candidate.label_version:
        reasons.append("regime.ml.promotion.label_version_mismatch")
    if evidence.deterministic_baseline_version != candidate.deterministic_baseline_version:
        reasons.append("regime.ml.promotion.deterministic_baseline_version_mismatch")
    if evidence.artifact_id != candidate.artifact_id:
        reasons.append("regime.ml.promotion.artifact_id_mismatch")
    return tuple(reasons)


def _coerce_evidence(raw: dict | RegimeMlPromotionEvidence | None) -> RegimeMlPromotionEvidence | None:
    if raw is None:
        return None
    if isinstance(raw, RegimeMlPromotionEvidence):
        return raw
    try:
        return RegimeMlPromotionEvidence(**raw)
    except TypeError:
        return None


def _blocked(
    reasons: list[str],
    current: bool,
    matches: bool,
    frontend_rejected: bool,
) -> RegimeMlPromotionDecision:
    return RegimeMlPromotionDecision(
        promoted=False,
        target_mode="shadow",
        maximum_automatic_promotion_mode=REGIME_ML_MAX_AUTOMATIC_PROMOTION_MODE,
        reason_codes=tuple(dict.fromkeys(reasons)),
        policy_version=REGIME_ML_PROMOTION_POLICY_VERSION,
        evidence_current=current,
        evidence_matches_candidate_artifact=matches,
        frontend_supplied_evidence_rejected=frontend_rejected,
    )


def _parse_time(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

