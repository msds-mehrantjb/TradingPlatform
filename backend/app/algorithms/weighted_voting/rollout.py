"""Staged paper-trading rollout controls for Weighted Voting V2."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from typing import Any, Literal, Mapping, Protocol


WEIGHTED_VOTING_ROLLOUT_VERSION = "weighted_voting_rollout_v2"
WEIGHTED_VOTING_ROLLOUT_NAMESPACE = "data/algorithms/weighted_voting/rollout/"

WEIGHTED_VOTING_V2_ENABLED = "WEIGHTED_VOTING_V2_ENABLED"
WEIGHTED_VOTING_SHADOW_MODE = "WEIGHTED_VOTING_SHADOW_MODE"
WEIGHTED_VOTING_DYNAMIC_REDUCTION_ENABLED = "WEIGHTED_VOTING_DYNAMIC_REDUCTION_ENABLED"
WEIGHTED_VOTING_DYNAMIC_INCREASE_ENABLED = "WEIGHTED_VOTING_DYNAMIC_INCREASE_ENABLED"
WEIGHTED_VOTING_AUTO_SUBMIT_ENABLED = "WEIGHTED_VOTING_AUTO_SUBMIT_ENABLED"

ROLLOUT_STATE_KEY = "weighted_voting.rollout.active"
ROLLBACK_STATE_KEY = "weighted_voting.rollout.previous_valid"
ROLLOUT_AUDIT_PREFIX = "weighted_voting.rollout.audit."
ROLLOUT_EVIDENCE_PREFIX = "weighted_voting.rollout.evidence."

WeightedVotingControlledRolloutStage = Literal[
    "disabled",
    "background_observation",
    "shadow_decisions",
    "manual_paper_submission",
    "automatic_paper_small_allocation",
    "automatic_paper_approved_allocation",
]

CONTROLLED_ROLLOUT_STAGES: tuple[WeightedVotingControlledRolloutStage, ...] = (
    "disabled",
    "background_observation",
    "shadow_decisions",
    "manual_paper_submission",
    "automatic_paper_small_allocation",
    "automatic_paper_approved_allocation",
)

WeightedVotingRolloutLifecycleState = Literal[
    "disabled",
    "backtest_only",
    "shadow",
    "paper_trading",
    "limited_paper",
    "production_ready",
    "paused",
    "emergency_disabled",
]

WEIGHTED_VOTING_ROLLOUT_STATES: tuple[WeightedVotingRolloutLifecycleState, ...] = (
    "disabled",
    "backtest_only",
    "shadow",
    "paper_trading",
    "limited_paper",
    "production_ready",
    "paused",
    "emergency_disabled",
)

WeightedVotingRolloutStage = Literal[
    "backend_shadow",
    "shadow_comparison",
    "static_equal_weights",
    "performance_weights",
    "dynamic_reduction",
    "dynamic_entry_exit",
    "dynamic_increase",
    "manual_paper_submission",
    "automatic_paper_submission",
]

ROLLOUT_STAGES: tuple[WeightedVotingRolloutStage, ...] = (
    "backend_shadow",
    "shadow_comparison",
    "static_equal_weights",
    "performance_weights",
    "dynamic_reduction",
    "dynamic_entry_exit",
    "dynamic_increase",
    "manual_paper_submission",
    "automatic_paper_submission",
)


class WeightedVotingRolloutStore(Protocol):
    def read_snapshot(self, key: str) -> dict:
        ...

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        ...


class RolloutPermission(str, Enum):
    ENABLED = "enabled"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class WeightedVotingRolloutControl:
    requested_state: WeightedVotingRolloutLifecycleState
    effective_state: WeightedVotingRolloutLifecycleState
    algorithm_id: Literal["weighted_voting"]
    namespace: str
    trading_allowed: bool
    paper_trading_allowed: bool
    automatic_submission_allowed: bool
    production_ready: bool
    account_wide_emergency_shutdown: bool
    ignored_external_algorithm_disables: tuple[str, ...]
    reason_codes: tuple[str, ...]
    explanation: str

    def model_dump(self) -> dict[str, object]:
        return {
            "requested_state": self.requested_state,
            "effective_state": self.effective_state,
            "algorithm_id": self.algorithm_id,
            "namespace": self.namespace,
            "trading_allowed": self.trading_allowed,
            "paper_trading_allowed": self.paper_trading_allowed,
            "automatic_submission_allowed": self.automatic_submission_allowed,
            "production_ready": self.production_ready,
            "account_wide_emergency_shutdown": self.account_wide_emergency_shutdown,
            "ignored_external_algorithm_disables": self.ignored_external_algorithm_disables,
            "reason_codes": self.reason_codes,
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class WeightedVotingRolloutFlags:
    v2_enabled: bool = True
    shadow_mode: bool = True
    dynamic_reduction_enabled: bool = False
    dynamic_increase_enabled: bool = False
    auto_submit_enabled: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> WeightedVotingRolloutFlags:
        source = environ or os.environ
        return cls(
            v2_enabled=_env_bool(source, WEIGHTED_VOTING_V2_ENABLED, True),
            shadow_mode=_env_bool(source, WEIGHTED_VOTING_SHADOW_MODE, True),
            dynamic_reduction_enabled=_env_bool(source, WEIGHTED_VOTING_DYNAMIC_REDUCTION_ENABLED, False),
            dynamic_increase_enabled=_env_bool(source, WEIGHTED_VOTING_DYNAMIC_INCREASE_ENABLED, False),
            auto_submit_enabled=_env_bool(source, WEIGHTED_VOTING_AUTO_SUBMIT_ENABLED, False),
        )

    def model_dump(self) -> dict[str, bool | str]:
        return {
            "rollout_version": WEIGHTED_VOTING_ROLLOUT_VERSION,
            WEIGHTED_VOTING_V2_ENABLED: self.v2_enabled,
            WEIGHTED_VOTING_SHADOW_MODE: self.shadow_mode,
            WEIGHTED_VOTING_DYNAMIC_REDUCTION_ENABLED: self.dynamic_reduction_enabled,
            WEIGHTED_VOTING_DYNAMIC_INCREASE_ENABLED: self.dynamic_increase_enabled,
            WEIGHTED_VOTING_AUTO_SUBMIT_ENABLED: self.auto_submit_enabled,
        }


@dataclass(frozen=True)
class WeightedVotingRolloutValidation:
    backend_shadow_passed: bool = False
    shadow_comparison_passed: bool = False
    static_equal_weights_passed: bool = False
    performance_weights_validated: bool = False
    dynamic_reduction_validated: bool = False
    dynamic_entry_exit_validated: bool = False
    dynamic_increase_validated: bool = False
    manual_paper_submission_validated: bool = False
    tests_passed: bool = False
    paper_validations_passed: bool = False
    live_trading_enabled: bool = False

    def model_dump(self) -> dict[str, bool]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class WeightedVotingControlledRolloutEvidence:
    algorithm_id: Literal["weighted_voting"] = "weighted_voting"
    no_unresolved_isolation_failures: bool = False
    inventory_reconciled: bool = False
    no_duplicate_order_incidents: bool = False
    worker_reliability_ok: bool = False
    decision_latency_ok: bool = False
    broker_latency_ok: bool = False
    data_freshness_stable: bool = False
    global_risk_fail_closed_tests_passing: bool = False
    restart_recovery_successful: bool = False
    shadow_opportunity_count: int = 0
    manual_paper_sample_count: int = 0
    transaction_cost_adjusted_paper_stability_ok: bool = False
    drawdown_within_limit: bool = False
    position_pnl_attribution_accurate: bool = False
    protective_order_reliability_ok: bool = False
    explicit_configuration_approval: bool = False
    evidence_id: str = ""
    evidence_version: str = "weighted_voting_rollout_evidence_v1"

    def __post_init__(self) -> None:
        if self.algorithm_id != "weighted_voting":
            raise ValueError("Weighted Voting rollout evidence cannot be supplied for another algorithm")

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)

    def evidence_hash(self) -> str:
        return _hash_payload(self.model_dump())


@dataclass(frozen=True)
class WeightedVotingSmallAllocationGuardrails:
    cap_quantity: int = 10
    cap_daily_risk_dollars: float = 250.0
    cap_daily_trades: int = 2
    approved_active_strategy_ids: tuple[str, ...] = ("S2", "S5", "S6", "S7")
    pyramiding_enabled: bool = False
    maximum_spread_percent: float = 0.0005
    maximum_data_freshness_seconds: int = 65
    stop_entries_after_reconciliation_discrepancy: bool = True
    reason_codes: tuple[str, ...] = ("weighted_voting.rollout.small_allocation_guardrails",)

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WeightedVotingControlledRolloutPromotion:
    algorithm_id: Literal["weighted_voting"]
    from_stage: WeightedVotingControlledRolloutStage
    to_stage: WeightedVotingControlledRolloutStage
    promoted: bool
    evidence_hash: str
    immutable_audit_id: str
    actor: str
    promoted_at: datetime
    blockers: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def model_dump(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["promoted_at"] = self.promoted_at.isoformat()
        return payload


@dataclass(frozen=True)
class WeightedVotingRolloutStageStatus:
    stage: WeightedVotingRolloutStage
    permission: RolloutPermission | str
    reason_codes: tuple[str, ...]
    explanation: str

    @property
    def enabled(self) -> bool:
        return self.permission == RolloutPermission.ENABLED.value

    def model_dump(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "permission": self.permission,
            "enabled": self.enabled,
            "reason_codes": self.reason_codes,
            "explanation": self.explanation,
        }


def rollout_feature_flags(environ: Mapping[str, str] | None = None) -> WeightedVotingRolloutFlags:
    return WeightedVotingRolloutFlags.from_env(environ)


def default_controlled_rollout_state(*, recorded_at: datetime | None = None) -> dict[str, Any]:
    timestamp = recorded_at or datetime.now(timezone.utc)
    return {
        "algorithm_id": "weighted_voting",
        "rollout_version": WEIGHTED_VOTING_ROLLOUT_VERSION,
        "stage": "background_observation",
        "automatic_paper_submission_allowed": False,
        "live_trading_allowed": False,
        "evidence_hash": None,
        "state_version": f"weighted_voting_rollout_state_{timestamp.strftime('%Y%m%dT%H%M%S')}",
        "recorded_at": timestamp.isoformat(),
        "reason_codes": (
            "weighted_voting.rollout.default_background_observation",
            "weighted_voting.rollout.default_auto_submit_disabled",
        ),
    }


def controlled_rollout_status(store: WeightedVotingRolloutStore | None = None) -> dict[str, Any]:
    state = _read_optional(store, ROLLOUT_STATE_KEY) if store is not None else None
    active_state = state or default_controlled_rollout_state()
    stage = str(active_state.get("stage") or "background_observation")
    return {
        "algorithm_id": "weighted_voting",
        "rollout_version": WEIGHTED_VOTING_ROLLOUT_VERSION,
        "stages": CONTROLLED_ROLLOUT_STAGES,
        "active_stage": stage,
        "state": active_state,
        "automatic_paper_submission_allowed": stage in {"automatic_paper_small_allocation", "automatic_paper_approved_allocation"} and bool(active_state.get("automatic_paper_submission_allowed")),
        "small_allocation_guardrails": small_allocation_guardrails().model_dump(),
        "live_trading_allowed": False,
        "reason_codes": (
            "weighted_voting.rollout.controlled_stages_explicit",
            "weighted_voting.rollout.successful_build_not_approval",
        ),
    }


def evaluate_controlled_rollout_promotion(
    *,
    current_stage: WeightedVotingControlledRolloutStage,
    target_stage: WeightedVotingControlledRolloutStage,
    evidence: WeightedVotingControlledRolloutEvidence,
) -> tuple[bool, tuple[str, ...]]:
    if current_stage not in CONTROLLED_ROLLOUT_STAGES:
        raise ValueError(f"unknown current Weighted Voting rollout stage: {current_stage}")
    if target_stage not in CONTROLLED_ROLLOUT_STAGES:
        raise ValueError(f"unknown target Weighted Voting rollout stage: {target_stage}")
    if evidence.algorithm_id != "weighted_voting":
        raise ValueError("Weighted Voting rollout promotion evidence must be attributed to weighted_voting")
    if CONTROLLED_ROLLOUT_STAGES.index(target_stage) > CONTROLLED_ROLLOUT_STAGES.index(current_stage) + 1:
        return False, ("weighted_voting.rollout.promotion_must_be_sequential",)
    blockers = list(_controlled_stage_blockers(target_stage, evidence))
    return not blockers, tuple(blockers)


def promote_controlled_rollout_stage(
    store: WeightedVotingRolloutStore,
    *,
    target_stage: WeightedVotingControlledRolloutStage,
    evidence: WeightedVotingControlledRolloutEvidence,
    actor: str,
    promoted_at: datetime | None = None,
) -> WeightedVotingControlledRolloutPromotion:
    timestamp = promoted_at or datetime.now(timezone.utc)
    current = _read_optional(store, ROLLOUT_STATE_KEY) or default_controlled_rollout_state(recorded_at=timestamp)
    current_stage = str(current.get("stage") or "background_observation")
    promoted, blockers = evaluate_controlled_rollout_promotion(
        current_stage=current_stage,  # type: ignore[arg-type]
        target_stage=target_stage,
        evidence=evidence,
    )
    evidence_hash = evidence.evidence_hash()
    audit_id = f"weighted_voting.rollout.audit.{_hash_payload({'from': current_stage, 'to': target_stage, 'evidence': evidence_hash, 'at': timestamp.isoformat(), 'actor': actor})}"
    promotion = WeightedVotingControlledRolloutPromotion(
        algorithm_id="weighted_voting",
        from_stage=current_stage,  # type: ignore[arg-type]
        to_stage=target_stage,
        promoted=promoted,
        evidence_hash=evidence_hash,
        immutable_audit_id=audit_id,
        actor=actor,
        promoted_at=timestamp,
        blockers=blockers,
        reason_codes=(
            "weighted_voting.rollout.promotion_approved"
            if promoted
            else "weighted_voting.rollout.promotion_blocked"
        ,),
    )
    store.write_snapshot(f"{ROLLOUT_EVIDENCE_PREFIX}{evidence_hash}", {**evidence.model_dump(), "evidence_hash": evidence_hash})
    store.write_snapshot(audit_id, promotion.model_dump())
    if promoted:
        if current and current.get("status") == "valid":
            store.write_snapshot(ROLLBACK_STATE_KEY, current)
        new_state = {
            "algorithm_id": "weighted_voting",
            "rollout_version": WEIGHTED_VOTING_ROLLOUT_VERSION,
            "stage": target_stage,
            "status": "valid",
            "automatic_paper_submission_allowed": target_stage in {"automatic_paper_small_allocation", "automatic_paper_approved_allocation"},
            "live_trading_allowed": False,
            "evidence_hash": evidence_hash,
            "state_version": f"weighted_voting_rollout_{target_stage}_{timestamp.strftime('%Y%m%dT%H%M%S')}",
            "recorded_at": timestamp.isoformat(),
            "actor": actor,
            "small_allocation_guardrails": small_allocation_guardrails().model_dump() if target_stage == "automatic_paper_small_allocation" else None,
            "reason_codes": ("weighted_voting.rollout.stage_persisted",),
        }
        store.write_snapshot(ROLLOUT_STATE_KEY, new_state)
    return promotion


def rollback_controlled_rollout_stage(
    store: WeightedVotingRolloutStore,
    *,
    actor: str,
    rolled_back_at: datetime | None = None,
) -> dict[str, Any]:
    timestamp = rolled_back_at or datetime.now(timezone.utc)
    previous = _read_optional(store, ROLLBACK_STATE_KEY)
    if previous is None:
        previous = {
            **default_controlled_rollout_state(recorded_at=timestamp),
            "stage": "manual_paper_submission",
            "automatic_paper_submission_allowed": False,
            "reason_codes": ("weighted_voting.rollout.rollback_default_manual_paper_safe_state",),
        }
    restored = {
        **previous,
        "automatic_paper_submission_allowed": False if previous.get("stage") != "automatic_paper_approved_allocation" else bool(previous.get("automatic_paper_submission_allowed", False)),
        "restored_at": timestamp.isoformat(),
        "restored_by": actor,
        "reason_codes": tuple(dict.fromkeys([*(previous.get("reason_codes") or ()), "weighted_voting.rollout.rollback_immediate_safe"])),
    }
    store.write_snapshot(ROLLOUT_STATE_KEY, restored)
    store.write_snapshot(
        f"{ROLLOUT_AUDIT_PREFIX}rollback.{_hash_payload(restored)}",
        {
            "algorithm_id": "weighted_voting",
            "rollout_version": WEIGHTED_VOTING_ROLLOUT_VERSION,
            "action": "rollback",
            "actor": actor,
            "restored_state": restored,
            "recorded_at": timestamp.isoformat(),
            "reason_codes": ("weighted_voting.rollout.rollback_audited",),
        },
    )
    return restored


def small_allocation_guardrails() -> WeightedVotingSmallAllocationGuardrails:
    return WeightedVotingSmallAllocationGuardrails()


def evaluate_weighted_voting_rollout_control(
    requested_state: WeightedVotingRolloutLifecycleState = "shadow",
    *,
    account_wide_emergency_shutdown: bool = False,
    disabled_algorithm_ids: tuple[str, ...] = (),
    flags: WeightedVotingRolloutFlags | None = None,
    validation: WeightedVotingRolloutValidation | None = None,
) -> WeightedVotingRolloutControl:
    if requested_state not in WEIGHTED_VOTING_ROLLOUT_STATES:
        raise ValueError(f"unknown Weighted Voting rollout state: {requested_state}")
    ignored_disables = tuple(sorted(algorithm_id for algorithm_id in disabled_algorithm_ids if algorithm_id != "weighted_voting"))
    reason_codes: list[str] = ["weighted_voting.rollout.control_evaluated"]
    effective_state = requested_state
    if ignored_disables:
        reason_codes.append("weighted_voting.rollout.external_algorithm_disable_ignored")
    if account_wide_emergency_shutdown:
        effective_state = "emergency_disabled"
        reason_codes.append("weighted_voting.rollout.account_wide_emergency_shutdown")
    elif "weighted_voting" in disabled_algorithm_ids:
        effective_state = "disabled"
        reason_codes.append("weighted_voting.rollout.weighted_voting_disabled")

    stage_auto_allowed = automatic_submission_allowed(flags=flags, validation=validation)
    paper_trading_allowed = effective_state in {"paper_trading", "limited_paper", "production_ready"}
    trading_allowed = paper_trading_allowed and effective_state != "paused"
    auto_allowed = stage_auto_allowed and effective_state in {"paper_trading", "production_ready"}
    if effective_state in {"disabled", "backtest_only", "shadow", "paused", "emergency_disabled"}:
        trading_allowed = False
        paper_trading_allowed = False
        auto_allowed = False
        reason_codes.append(f"weighted_voting.rollout.{effective_state}.blocks_order_submission")
    if effective_state == "limited_paper":
        auto_allowed = False
        reason_codes.append("weighted_voting.rollout.limited_paper_requires_manual_or_limited_submission")

    return WeightedVotingRolloutControl(
        requested_state=requested_state,
        effective_state=effective_state,
        algorithm_id="weighted_voting",
        namespace=WEIGHTED_VOTING_ROLLOUT_NAMESPACE,
        trading_allowed=trading_allowed,
        paper_trading_allowed=paper_trading_allowed,
        automatic_submission_allowed=auto_allowed,
        production_ready=effective_state == "production_ready",
        account_wide_emergency_shutdown=account_wide_emergency_shutdown,
        ignored_external_algorithm_disables=ignored_disables,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        explanation="Weighted Voting rollout control is evaluated only from Weighted Voting state and account-wide emergency shutdown state.",
    )


def evaluate_rollout_stage(
    stage: WeightedVotingRolloutStage,
    *,
    flags: WeightedVotingRolloutFlags | None = None,
    validation: WeightedVotingRolloutValidation | None = None,
) -> WeightedVotingRolloutStageStatus:
    active_flags = flags or rollout_feature_flags()
    active_validation = validation or WeightedVotingRolloutValidation()
    if stage not in ROLLOUT_STAGES:
        raise ValueError(f"unknown Weighted Voting rollout stage: {stage}")
    blockers = _stage_blockers(stage, active_flags, active_validation)
    if blockers:
        return WeightedVotingRolloutStageStatus(
            stage=stage,
            permission=RolloutPermission.BLOCKED.value,
            reason_codes=tuple(blockers),
            explanation="Weighted Voting rollout stage is blocked until prior acceptance metrics pass.",
        )
    return WeightedVotingRolloutStageStatus(
        stage=stage,
        permission=RolloutPermission.ENABLED.value,
        reason_codes=(f"weighted_voting.rollout.{stage}.enabled",),
        explanation="Weighted Voting rollout stage is enabled under the current flags and validation metrics.",
    )


def rollout_status(
    *,
    flags: WeightedVotingRolloutFlags | None = None,
    validation: WeightedVotingRolloutValidation | None = None,
    requested_state: WeightedVotingRolloutLifecycleState = "shadow",
    account_wide_emergency_shutdown: bool = False,
    disabled_algorithm_ids: tuple[str, ...] = (),
) -> dict[str, object]:
    active_flags = flags or rollout_feature_flags()
    active_validation = validation or WeightedVotingRolloutValidation()
    stages = tuple(evaluate_rollout_stage(stage, flags=active_flags, validation=active_validation).model_dump() for stage in ROLLOUT_STAGES)
    control = evaluate_weighted_voting_rollout_control(
        requested_state=requested_state,
        account_wide_emergency_shutdown=account_wide_emergency_shutdown,
        disabled_algorithm_ids=disabled_algorithm_ids,
        flags=active_flags,
        validation=active_validation,
    )
    return {
        "algorithm_id": "weighted_voting",
        "rollout_version": WEIGHTED_VOTING_ROLLOUT_VERSION,
        "namespace": WEIGHTED_VOTING_ROLLOUT_NAMESPACE,
        "allowed_states": WEIGHTED_VOTING_ROLLOUT_STATES,
        "controlled_stages": CONTROLLED_ROLLOUT_STAGES,
        "controlled_rollout": controlled_rollout_status(),
        "control": control.model_dump(),
        "effective_state": control.effective_state,
        "feature_flags": active_flags.model_dump(),
        "validation": active_validation.model_dump(),
        "stages": stages,
        "automatic_submission_allowed": automatic_submission_allowed(flags=active_flags, validation=active_validation),
        "live_trading_allowed": False,
        "reason_codes": tuple(
            dict.fromkeys(
                (
                    "weighted_voting.rollout.paper_only",
                    "weighted_voting.rollout.automatic_submission_guarded",
                    *control.reason_codes,
                )
            )
        ),
    }


def automatic_submission_allowed(
    *,
    flags: WeightedVotingRolloutFlags | None = None,
    validation: WeightedVotingRolloutValidation | None = None,
    store: WeightedVotingRolloutStore | None = None,
) -> bool:
    if store is not None:
        return bool(controlled_rollout_status(store)["automatic_paper_submission_allowed"])
    status = evaluate_rollout_stage(
        "automatic_paper_submission",
        flags=flags or rollout_feature_flags(),
        validation=validation or WeightedVotingRolloutValidation(),
    )
    return status.enabled


def record_valid_rollout_state(
    store: WeightedVotingRolloutStore,
    candidate_state: dict,
    *,
    recorded_at: datetime | None = None,
) -> dict:
    current = _read_optional(store, ROLLOUT_STATE_KEY)
    if current and current.get("status") == "valid":
        store.write_snapshot(ROLLBACK_STATE_KEY, current)
    timestamp = (recorded_at or datetime.now(timezone.utc)).isoformat()
    state = {
        **candidate_state,
        "algorithm_id": "weighted_voting",
        "rollout_version": WEIGHTED_VOTING_ROLLOUT_VERSION,
        "status": "valid",
        "recorded_at": timestamp,
        "reason_codes": tuple(dict.fromkeys([*(candidate_state.get("reason_codes") or ()), "weighted_voting.rollout.valid_state_recorded"])),
    }
    store.write_snapshot(ROLLOUT_STATE_KEY, state)
    return state


def rollback_weighted_voting_rollout(store: WeightedVotingRolloutStore, *, rolled_back_at: datetime | None = None) -> dict:
    previous = _read_optional(store, ROLLBACK_STATE_KEY)
    if not previous:
        raise ValueError("no previous valid Weighted Voting rollout state is available")
    restored = {
        **previous,
        "restored_at": (rolled_back_at or datetime.now(timezone.utc)).isoformat(),
        "reason_codes": tuple(dict.fromkeys([*(previous.get("reason_codes") or ()), "weighted_voting.rollout.rollback_restored_previous_valid_state"])),
    }
    store.write_snapshot(ROLLOUT_STATE_KEY, restored)
    return restored


def _stage_blockers(stage: WeightedVotingRolloutStage, flags: WeightedVotingRolloutFlags, validation: WeightedVotingRolloutValidation) -> list[str]:
    blockers: list[str] = []
    if validation.live_trading_enabled:
        blockers.append("weighted_voting.rollout.live_trading_never_allowed")
    if not flags.v2_enabled:
        blockers.append("weighted_voting.rollout.v2_disabled")
    if stage in {"backend_shadow", "shadow_comparison"} and not flags.shadow_mode:
        blockers.append("weighted_voting.rollout.shadow_mode_required")

    required_acceptance: dict[WeightedVotingRolloutStage, tuple[tuple[bool, str], ...]] = {
        "backend_shadow": (),
        "shadow_comparison": ((validation.backend_shadow_passed, "weighted_voting.rollout.backend_shadow_not_validated"),),
        "static_equal_weights": (
            (validation.backend_shadow_passed, "weighted_voting.rollout.backend_shadow_not_validated"),
            (validation.shadow_comparison_passed, "weighted_voting.rollout.shadow_comparison_not_validated"),
        ),
        "performance_weights": (
            (validation.static_equal_weights_passed, "weighted_voting.rollout.static_equal_weights_not_validated"),
        ),
        "dynamic_reduction": (
            (validation.performance_weights_validated, "weighted_voting.rollout.performance_weights_not_validated"),
        ),
        "dynamic_entry_exit": (
            (validation.dynamic_reduction_validated, "weighted_voting.rollout.dynamic_reduction_not_validated"),
        ),
        "dynamic_increase": (
            (validation.dynamic_entry_exit_validated, "weighted_voting.rollout.dynamic_entry_exit_not_validated"),
        ),
        "manual_paper_submission": (
            (validation.dynamic_increase_validated, "weighted_voting.rollout.dynamic_increase_not_validated"),
        ),
        "automatic_paper_submission": (
            (validation.manual_paper_submission_validated, "weighted_voting.rollout.manual_paper_submission_not_validated"),
            (validation.tests_passed, "weighted_voting.rollout.tests_not_passed"),
            (validation.paper_validations_passed, "weighted_voting.rollout.paper_validations_not_passed"),
        ),
    }
    for passed, reason_code in required_acceptance[stage]:
        if not passed:
            blockers.append(reason_code)

    if stage == "dynamic_reduction" and not flags.dynamic_reduction_enabled:
        blockers.append("weighted_voting.rollout.dynamic_reduction_flag_disabled")
    if stage == "dynamic_increase" and not flags.dynamic_increase_enabled:
        blockers.append("weighted_voting.rollout.dynamic_increase_flag_disabled")
    if stage == "automatic_paper_submission" and not flags.auto_submit_enabled:
        blockers.append("weighted_voting.rollout.auto_submit_flag_disabled")
    return list(dict.fromkeys(blockers))


def _controlled_stage_blockers(stage: WeightedVotingControlledRolloutStage, evidence: WeightedVotingControlledRolloutEvidence) -> tuple[str, ...]:
    if stage == "disabled":
        return ()
    checks: list[tuple[bool, str]] = []
    if stage in {"background_observation", "shadow_decisions", "manual_paper_submission", "automatic_paper_small_allocation", "automatic_paper_approved_allocation"}:
        checks.extend(
            (
                (evidence.no_unresolved_isolation_failures, "weighted_voting.rollout.isolation_failures_unresolved"),
                (evidence.global_risk_fail_closed_tests_passing, "weighted_voting.rollout.global_risk_fail_closed_tests_missing"),
            )
        )
    if stage in {"shadow_decisions", "manual_paper_submission", "automatic_paper_small_allocation", "automatic_paper_approved_allocation"}:
        checks.append((evidence.shadow_opportunity_count >= 50, "weighted_voting.rollout.shadow_opportunity_minimum_not_met"))
    if stage in {"manual_paper_submission", "automatic_paper_small_allocation", "automatic_paper_approved_allocation"}:
        checks.extend(
            (
                (evidence.inventory_reconciled, "weighted_voting.rollout.inventory_unreconciled"),
                (evidence.no_duplicate_order_incidents, "weighted_voting.rollout.duplicate_order_incidents"),
                (evidence.worker_reliability_ok, "weighted_voting.rollout.worker_reliability_unacceptable"),
                (evidence.decision_latency_ok, "weighted_voting.rollout.decision_latency_unacceptable"),
                (evidence.broker_latency_ok, "weighted_voting.rollout.broker_latency_unacceptable"),
                (evidence.data_freshness_stable, "weighted_voting.rollout.data_freshness_unstable"),
                (evidence.restart_recovery_successful, "weighted_voting.rollout.restart_recovery_missing"),
                (evidence.position_pnl_attribution_accurate, "weighted_voting.rollout.position_pnl_attribution_unverified"),
                (evidence.protective_order_reliability_ok, "weighted_voting.rollout.protective_order_reliability_unverified"),
                (evidence.explicit_configuration_approval, "weighted_voting.rollout.explicit_configuration_approval_missing"),
            )
        )
    if stage in {"automatic_paper_small_allocation", "automatic_paper_approved_allocation"}:
        checks.extend(
            (
                (evidence.manual_paper_sample_count >= 20, "weighted_voting.rollout.manual_paper_sample_minimum_not_met"),
                (evidence.transaction_cost_adjusted_paper_stability_ok, "weighted_voting.rollout.paper_stability_unacceptable"),
                (evidence.drawdown_within_limit, "weighted_voting.rollout.drawdown_limit_exceeded"),
            )
        )
    return tuple(reason for passed, reason in checks if not passed)


def _hash_payload(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _env_bool(source: Mapping[str, str], key: str, default: bool) -> bool:
    raw = source.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_optional(store: WeightedVotingRolloutStore, key: str) -> dict | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


__all__ = [
    "CONTROLLED_ROLLOUT_STAGES",
    "ROLLOUT_AUDIT_PREFIX",
    "ROLLOUT_EVIDENCE_PREFIX",
    "ROLLOUT_STATE_KEY",
    "ROLLBACK_STATE_KEY",
    "WEIGHTED_VOTING_ROLLOUT_STATES",
    "WEIGHTED_VOTING_ROLLOUT_VERSION",
    "WeightedVotingControlledRolloutEvidence",
    "WeightedVotingControlledRolloutPromotion",
    "WeightedVotingRolloutFlags",
    "WeightedVotingRolloutValidation",
    "WeightedVotingSmallAllocationGuardrails",
    "automatic_submission_allowed",
    "controlled_rollout_status",
    "default_controlled_rollout_state",
    "evaluate_controlled_rollout_promotion",
    "evaluate_rollout_stage",
    "evaluate_weighted_voting_rollout_control",
    "promote_controlled_rollout_stage",
    "record_valid_rollout_state",
    "rollback_controlled_rollout_stage",
    "rollback_weighted_voting_rollout",
    "rollout_feature_flags",
    "rollout_status",
    "small_allocation_guardrails",
]
