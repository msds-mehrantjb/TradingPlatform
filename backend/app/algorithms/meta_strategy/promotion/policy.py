"""Fail-closed promotion policy for Meta-Strategy artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Mapping

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.promotion.evidence import (
    META_STRATEGY_PROMOTION_EVIDENCE_FIELDS,
    MetaStrategyPromotionEvidence,
    evidence_matches_candidate_artifact,
)


PromotionAction = Literal["PROMOTE", "REJECT"]


@dataclass(frozen=True)
class MetaStrategyPromotionPolicy:
    max_evidence_age_days: int = 7
    maximum_brier_score: float = 0.25
    maximum_drawdown: float = 10.0
    minimum_side_coverage: float = 0.10
    minimum_regime_coverage: float = 0.10
    maximum_operational_failures: int = 0
    maximum_risk_violations: int = 0
    maximum_reconciliation_failures: int = 0
    require_shadow_pass: bool = True
    require_paper_stability: bool = True


@dataclass(frozen=True)
class MetaStrategyPromotionDecision:
    algorithm_id: str
    artifact_id: str
    artifact_hash: str
    action: PromotionAction
    promoted: bool
    reason_codes: tuple[str, ...]
    checked_at: datetime


def evaluate_meta_strategy_promotion_policy(
    evidence: MetaStrategyPromotionEvidence,
    *,
    candidate_artifact: Mapping[str, Any],
    policy: MetaStrategyPromotionPolicy | None = None,
    checked_at: datetime | None = None,
) -> MetaStrategyPromotionDecision:
    policy = policy or MetaStrategyPromotionPolicy()
    checked = (checked_at or datetime.now(tz=UTC)).astimezone(UTC)
    reason_codes: list[str] = []
    if evidence.algorithm_id != ALGORITHM_ID:
        reason_codes.append("meta_strategy.promotion.cross_algorithm_evidence")
    if evidence.evidence_source != "backend":
        reason_codes.append("meta_strategy.promotion.frontend_evidence_rejected")
    if not evidence_matches_candidate_artifact(evidence, candidate_artifact):
        reason_codes.append("meta_strategy.promotion.candidate_artifact_mismatch")
    reason_codes.extend(_missing_evidence(evidence))
    reason_codes.extend(_stale_evidence(evidence, checked_at=checked, max_age_days=policy.max_evidence_age_days))
    reason_codes.extend(_threshold_failures(evidence, policy))
    if reason_codes:
        return MetaStrategyPromotionDecision(
            algorithm_id=ALGORITHM_ID,
            artifact_id=evidence.artifact_id,
            artifact_hash=evidence.artifact_hash,
            action="REJECT",
            promoted=False,
            reason_codes=tuple(dict.fromkeys(("meta_strategy.promotion.fail_closed", *reason_codes))),
            checked_at=checked,
        )
    return MetaStrategyPromotionDecision(
        algorithm_id=ALGORITHM_ID,
        artifact_id=evidence.artifact_id,
        artifact_hash=evidence.artifact_hash,
        action="PROMOTE",
        promoted=True,
        reason_codes=("meta_strategy.promotion.evidence_passed",),
        checked_at=checked,
    )


def _missing_evidence(evidence: MetaStrategyPromotionEvidence) -> tuple[str, ...]:
    missing = []
    for field_name in META_STRATEGY_PROMOTION_EVIDENCE_FIELDS:
        value = getattr(evidence, field_name)
        if not value:
            missing.append(f"meta_strategy.promotion.missing_{field_name}")
        if field_name not in evidence.evidence_timestamps:
            missing.append(f"meta_strategy.promotion.missing_timestamp_{field_name}")
    return tuple(missing)


def _stale_evidence(evidence: MetaStrategyPromotionEvidence, *, checked_at: datetime, max_age_days: int) -> tuple[str, ...]:
    stale = []
    for field_name, timestamp in evidence.evidence_timestamps.items():
        age_days = (checked_at - timestamp.astimezone(UTC)).total_seconds() / 86400.0
        if age_days > max_age_days:
            stale.append(f"meta_strategy.promotion.stale_{field_name}")
    return tuple(stale)


def _threshold_failures(evidence: MetaStrategyPromotionEvidence, policy: MetaStrategyPromotionPolicy) -> tuple[str, ...]:
    failures: list[str] = []
    if not _passed(evidence.walk_forward_result):
        failures.append("meta_strategy.promotion.walk_forward_failed")
    if not _passed(evidence.holdout_result):
        failures.append("meta_strategy.promotion.holdout_failed")
    if not _calibration_passed(evidence.calibration_result, policy):
        failures.append("meta_strategy.promotion.calibration_failed")
    if not _economic_passed(evidence.economic_baseline_comparison):
        failures.append("meta_strategy.promotion.economic_baseline_failed")
    if _number(evidence.drawdown_result, "maxDrawdown", "maximumDrawdown", "drawdown") > policy.maximum_drawdown:
        failures.append("meta_strategy.promotion.drawdown_failed")
    if min(_coverage_values(evidence.side_coverage) or [0.0]) < policy.minimum_side_coverage:
        failures.append("meta_strategy.promotion.side_coverage_failed")
    if min(_coverage_values(evidence.regime_coverage) or [0.0]) < policy.minimum_regime_coverage:
        failures.append("meta_strategy.promotion.regime_coverage_failed")
    if policy.require_shadow_pass and not _passed(evidence.shadow_comparison):
        failures.append("meta_strategy.promotion.shadow_comparison_failed")
    if policy.require_paper_stability and not _paper_stable(evidence.paper_stability):
        failures.append("meta_strategy.promotion.paper_stability_failed")
    if _failure_count(evidence.operational_failures) > policy.maximum_operational_failures:
        failures.append("meta_strategy.promotion.operational_failures")
    if _failure_count(evidence.risk_violations) > policy.maximum_risk_violations:
        failures.append("meta_strategy.promotion.risk_violations")
    if _failure_count(evidence.reconciliation_failures) > policy.maximum_reconciliation_failures:
        failures.append("meta_strategy.promotion.reconciliation_failures")
    if not evidence.rollback_artifact.get("artifactId") and not evidence.rollback_artifact.get("artifact_id"):
        failures.append("meta_strategy.promotion.rollback_artifact_missing")
    return tuple(failures)


def _passed(payload: Mapping[str, Any]) -> bool:
    return bool(payload.get("passed", payload.get("promoted", payload.get("approved", False))))


def _calibration_passed(payload: Mapping[str, Any], policy: MetaStrategyPromotionPolicy) -> bool:
    approved = bool(payload.get("probabilitySizingApproved", payload.get("approved", payload.get("passed", False))))
    brier = _number(payload, "brierScore", "brier_score")
    return approved and brier <= policy.maximum_brier_score


def _economic_passed(payload: Mapping[str, Any]) -> bool:
    if "outperformedBaseline" in payload:
        return bool(payload["outperformedBaseline"])
    return _number(payload, "netPnlDelta", "net_pnl_delta", "netExpectancyDelta") > 0


def _paper_stable(payload: Mapping[str, Any]) -> bool:
    return bool(payload.get("stable", payload.get("passed", False)))


def _failure_count(payload: Mapping[str, Any]) -> int:
    value = payload.get("count", payload.get("failureCount", payload.get("failures", 0)))
    if isinstance(value, list | tuple):
        return len(value)
    return int(value or 0)


def _coverage_values(payload: Mapping[str, Any]) -> list[float]:
    values = payload.get("coverage", payload)
    if isinstance(values, Mapping):
        return [float(value) for value in values.values()]
    return [float(values)]


def _number(payload: Mapping[str, Any], *keys: str) -> float:
    for key in keys:
        if key in payload and payload[key] is not None:
            return float(payload[key])
    return 0.0


__all__ = [
    "MetaStrategyPromotionDecision",
    "MetaStrategyPromotionPolicy",
    "PromotionAction",
    "evaluate_meta_strategy_promotion_policy",
]
