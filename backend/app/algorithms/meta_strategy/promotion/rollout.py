"""Staged rollout governance for Meta-Strategy artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Mapping

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.models import artifact_hash
from backend.app.algorithms.meta_strategy.promotion.evidence import MetaStrategyPromotionEvidence, evidence_matches_candidate_artifact
from backend.app.algorithms.meta_strategy.promotion.policy import (
    MetaStrategyPromotionPolicy,
    evaluate_meta_strategy_promotion_policy,
)


MetaStrategyRolloutStage = Literal["OFF", "RESEARCH", "SHADOW", "PAPER_FILTER", "PAPER_RISK_REDUCTION", "LIMITED_LIVE_FILTER"]
MetaStrategyRolloutAction = Literal["ADVANCE", "REJECT", "ROLLBACK"]

META_STRATEGY_ROLLOUT_STAGES: tuple[MetaStrategyRolloutStage, ...] = (
    "OFF",
    "RESEARCH",
    "SHADOW",
    "PAPER_FILTER",
    "PAPER_RISK_REDUCTION",
    "LIMITED_LIVE_FILTER",
)
META_STRATEGY_LIVE_ROLLOUT_STAGES: tuple[MetaStrategyRolloutStage, ...] = ("LIMITED_LIVE_FILTER",)


@dataclass(frozen=True)
class MetaStrategyManualApproval:
    approval_id: str
    approved_by: str
    approved_at: datetime
    approved: bool = True

    def __post_init__(self) -> None:
        if self.approved_at.tzinfo is None or self.approved_at.utcoffset() is None:
            raise ValueError("manual approval timestamp must be timezone-aware")
        if not self.approval_id or not self.approved_by:
            raise ValueError("manual approval must include approval_id and approved_by")


@dataclass(frozen=True)
class MetaStrategyRolloutState:
    algorithm_id: str
    stage: MetaStrategyRolloutStage
    artifact_id: str
    artifact_hash: str
    previous_stage: MetaStrategyRolloutStage | None = None
    previous_artifact_id: str | None = None
    previous_artifact_hash: str | None = None
    updated_at: datetime | None = None
    manual_approval_id: str | None = None
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.algorithm_id != ALGORITHM_ID:
            raise ValueError("rollout state must be attributed to meta_strategy")
        if self.stage not in META_STRATEGY_ROLLOUT_STAGES:
            raise ValueError("unsupported Meta-Strategy rollout stage")
        if self.updated_at is not None and (self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None):
            raise ValueError("rollout updated_at must be timezone-aware")


@dataclass(frozen=True)
class MetaStrategyRolloutDecision:
    algorithm_id: str
    action: MetaStrategyRolloutAction
    approved: bool
    from_stage: str
    to_stage: str
    artifact_id: str
    artifact_hash: str
    state: MetaStrategyRolloutState
    reason_codes: tuple[str, ...]
    decided_at: datetime


def initial_meta_strategy_rollout_state(*, checked_at: datetime | None = None) -> MetaStrategyRolloutState:
    return MetaStrategyRolloutState(
        algorithm_id=ALGORITHM_ID,
        stage="OFF",
        artifact_id="",
        artifact_hash="",
        updated_at=(checked_at or datetime.now(tz=UTC)).astimezone(UTC),
        reason_codes=("meta_strategy.rollout.initial_off",),
    )


def advance_meta_strategy_rollout(
    state: MetaStrategyRolloutState,
    *,
    target_stage: str,
    candidate_artifact: Mapping[str, Any],
    evidence: MetaStrategyPromotionEvidence | None = None,
    manual_approval: MetaStrategyManualApproval | None = None,
    policy: MetaStrategyPromotionPolicy | None = None,
    checked_at: datetime | None = None,
) -> MetaStrategyRolloutDecision:
    checked = (checked_at or datetime.now(tz=UTC)).astimezone(UTC)
    reason_codes: list[str] = []
    target = str(target_stage).upper()
    if target not in META_STRATEGY_ROLLOUT_STAGES:
        reason_codes.append("meta_strategy.rollout.unrestricted_live_not_supported")
        return _rejected(state, target, candidate_artifact, checked, reason_codes)
    if not _is_next_stage(state.stage, target):
        reason_codes.append("meta_strategy.rollout.stage_skip_rejected")
    if target in META_STRATEGY_LIVE_ROLLOUT_STAGES:
        if manual_approval is None or not manual_approval.approved:
            reason_codes.append("meta_strategy.rollout.manual_approval_required_for_live")
    if _stage_index(target) > _stage_index("SHADOW"):
        if evidence is None:
            reason_codes.append("meta_strategy.rollout.promotion_evidence_required_beyond_shadow")
        else:
            reason_codes.extend(_required_beyond_shadow_failures(evidence))
            promotion = evaluate_meta_strategy_promotion_policy(
                evidence,
                candidate_artifact=candidate_artifact,
                policy=policy,
                checked_at=checked,
            )
            if not promotion.promoted:
                reason_codes.extend(promotion.reason_codes)
    elif evidence is not None and not evidence_matches_candidate_artifact(evidence, candidate_artifact):
        reason_codes.append("meta_strategy.rollout.candidate_artifact_mismatch")
    if reason_codes:
        return _rejected(state, target, candidate_artifact, checked, reason_codes)

    artifact_id = str(candidate_artifact.get("artifactId") or candidate_artifact.get("artifact_id") or "")
    candidate_hash = artifact_hash(dict(candidate_artifact))
    next_state = MetaStrategyRolloutState(
        algorithm_id=ALGORITHM_ID,
        stage=target,  # type: ignore[arg-type]
        artifact_id=artifact_id,
        artifact_hash=candidate_hash,
        previous_stage=state.stage,
        previous_artifact_id=state.artifact_id,
        previous_artifact_hash=state.artifact_hash,
        updated_at=checked,
        manual_approval_id=manual_approval.approval_id if manual_approval is not None else state.manual_approval_id,
        reason_codes=tuple(dict.fromkeys(("meta_strategy.rollout.stage_advanced", f"meta_strategy.rollout.stage_{target.lower()}"))),
    )
    return MetaStrategyRolloutDecision(
        algorithm_id=ALGORITHM_ID,
        action="ADVANCE",
        approved=True,
        from_stage=state.stage,
        to_stage=target,
        artifact_id=artifact_id,
        artifact_hash=candidate_hash,
        state=next_state,
        reason_codes=next_state.reason_codes,
        decided_at=checked,
    )


def rollback_meta_strategy_rollout(
    state: MetaStrategyRolloutState,
    *,
    checked_at: datetime | None = None,
) -> MetaStrategyRolloutDecision:
    checked = (checked_at or datetime.now(tz=UTC)).astimezone(UTC)
    if not state.previous_stage or state.previous_artifact_id is None or state.previous_artifact_hash is None:
        return MetaStrategyRolloutDecision(
            algorithm_id=ALGORITHM_ID,
            action="REJECT",
            approved=False,
            from_stage=state.stage,
            to_stage=state.stage,
            artifact_id=state.artifact_id,
            artifact_hash=state.artifact_hash,
            state=state,
            reason_codes=("meta_strategy.rollout.rollback_unavailable",),
            decided_at=checked,
        )
    restored = MetaStrategyRolloutState(
        algorithm_id=ALGORITHM_ID,
        stage=state.previous_stage,
        artifact_id=state.previous_artifact_id,
        artifact_hash=state.previous_artifact_hash,
        previous_stage=state.stage,
        previous_artifact_id=state.artifact_id,
        previous_artifact_hash=state.artifact_hash,
        updated_at=checked,
        reason_codes=("meta_strategy.rollout.rollback_restored_previous_artifact_and_mode",),
    )
    return MetaStrategyRolloutDecision(
        algorithm_id=ALGORITHM_ID,
        action="ROLLBACK",
        approved=True,
        from_stage=state.stage,
        to_stage=restored.stage,
        artifact_id=restored.artifact_id,
        artifact_hash=restored.artifact_hash,
        state=restored,
        reason_codes=restored.reason_codes,
        decided_at=checked,
    )


def _required_beyond_shadow_failures(evidence: MetaStrategyPromotionEvidence) -> tuple[str, ...]:
    failures = []
    if not bool(evidence.walk_forward_result.get("passed", False)):
        failures.append("meta_strategy.rollout.walk_forward_required_beyond_shadow")
    if not bool(evidence.holdout_result.get("passed", False)):
        failures.append("meta_strategy.rollout.holdout_required_beyond_shadow")
    if not bool(evidence.paper_stability.get("stable", evidence.paper_stability.get("passed", False))):
        failures.append("meta_strategy.rollout.paper_stability_required_beyond_shadow")
    return tuple(failures)


def _rejected(
    state: MetaStrategyRolloutState,
    target_stage: str,
    candidate_artifact: Mapping[str, Any],
    checked_at: datetime,
    reason_codes: list[str],
) -> MetaStrategyRolloutDecision:
    return MetaStrategyRolloutDecision(
        algorithm_id=ALGORITHM_ID,
        action="REJECT",
        approved=False,
        from_stage=state.stage,
        to_stage=target_stage,
        artifact_id=str(candidate_artifact.get("artifactId") or candidate_artifact.get("artifact_id") or state.artifact_id),
        artifact_hash=str(candidate_artifact.get("artifactHash") or state.artifact_hash),
        state=state,
        reason_codes=tuple(dict.fromkeys(("meta_strategy.rollout.fail_closed", *reason_codes))),
        decided_at=checked_at,
    )


def _is_next_stage(current: str, target: str) -> bool:
    return _stage_index(target) == _stage_index(current) + 1


def _stage_index(stage: str) -> int:
    try:
        return META_STRATEGY_ROLLOUT_STAGES.index(stage)  # type: ignore[arg-type]
    except ValueError:
        return 10_000


__all__ = [
    "META_STRATEGY_LIVE_ROLLOUT_STAGES",
    "META_STRATEGY_ROLLOUT_STAGES",
    "MetaStrategyManualApproval",
    "MetaStrategyRolloutAction",
    "MetaStrategyRolloutDecision",
    "MetaStrategyRolloutStage",
    "MetaStrategyRolloutState",
    "advance_meta_strategy_rollout",
    "initial_meta_strategy_rollout_state",
    "rollback_meta_strategy_rollout",
]
