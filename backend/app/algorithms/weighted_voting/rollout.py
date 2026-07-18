"""Staged paper-trading rollout controls for Weighted Voting V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import os
from typing import Literal, Mapping, Protocol


WEIGHTED_VOTING_ROLLOUT_VERSION = "weighted_voting_rollout_v1"
WEIGHTED_VOTING_ROLLOUT_NAMESPACE = "data/algorithms/weighted_voting/rollout/"

WEIGHTED_VOTING_V2_ENABLED = "WEIGHTED_VOTING_V2_ENABLED"
WEIGHTED_VOTING_SHADOW_MODE = "WEIGHTED_VOTING_SHADOW_MODE"
WEIGHTED_VOTING_DYNAMIC_REDUCTION_ENABLED = "WEIGHTED_VOTING_DYNAMIC_REDUCTION_ENABLED"
WEIGHTED_VOTING_DYNAMIC_INCREASE_ENABLED = "WEIGHTED_VOTING_DYNAMIC_INCREASE_ENABLED"
WEIGHTED_VOTING_AUTO_SUBMIT_ENABLED = "WEIGHTED_VOTING_AUTO_SUBMIT_ENABLED"

ROLLOUT_STATE_KEY = "weighted_voting.rollout.active"
ROLLBACK_STATE_KEY = "weighted_voting.rollout.previous_valid"

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
) -> bool:
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
