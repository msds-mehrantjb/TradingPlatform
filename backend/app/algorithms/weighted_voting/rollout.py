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
ROLLOUT_VALIDATION_KEY = "weighted_voting.rollout.validation.latest"
ROLLOUT_AUDIT_PREFIX = "weighted_voting.rollout.audit."
ROLLOUT_EVIDENCE_PREFIX = "weighted_voting.rollout.evidence."
ROLLOUT_VALIDATION_AUDIT_PREFIX = "weighted_voting.rollout.validation.audit."

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
    paper_broker_e2e_validated: bool = False
    reconciliation_validated: bool = False
    restart_recovery_validated: bool = False
    persisted_operator_approval: bool = False
    live_trading_enabled: bool = False
    validation_record_id: str = ""
    source_authority: str = ""
    approved_by: str = ""
    recorded_at: str = ""

    def model_dump(self) -> dict[str, Any]:
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
    automated_paper_readiness_detected: bool = False
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


def persist_rollout_validation_record(
    store: WeightedVotingRolloutStore,
    validation: WeightedVotingRolloutValidation,
    *,
    source_authority: str,
    approved_by: str,
    recorded_at: datetime | None = None,
    reason: str = "weighted_voting.rollout.validation.backend_record_persisted",
) -> dict[str, Any]:
    if _frontend_validation_source(source_authority):
        raise ValueError("Weighted Voting rollout validation cannot be marked passed by frontend, browser, React state, or client API state")
    timestamp = recorded_at or datetime.now(timezone.utc)
    validation_payload = validation.model_dump()
    persisted_operator_approval = bool(validation.persisted_operator_approval and approved_by)
    record = {
        **validation_payload,
        "algorithm_id": "weighted_voting",
        "rollout_version": WEIGHTED_VOTING_ROLLOUT_VERSION,
        "source_authority": source_authority,
        "approved_by": approved_by,
        "recorded_at": timestamp.isoformat(),
        "persisted_operator_approval": persisted_operator_approval,
        "validation_record_id": f"weighted_voting.rollout.validation.{_hash_payload({**validation_payload, 'source': source_authority, 'approved_by': approved_by, 'at': timestamp.isoformat()})}",
        "reason_codes": (reason, "weighted_voting.rollout.validation.frontend_cannot_mark_passed"),
    }
    store.write_snapshot(ROLLOUT_VALIDATION_KEY, record)
    store.write_snapshot(f"{ROLLOUT_VALIDATION_AUDIT_PREFIX}{record['validation_record_id']}", record)
    return record


def load_persisted_rollout_validation(store: WeightedVotingRolloutStore | None) -> WeightedVotingRolloutValidation | None:
    if store is None:
        return None
    record = _read_optional(store, ROLLOUT_VALIDATION_KEY)
    if not record:
        return None
    if str(record.get("algorithm_id") or record.get("algorithmId")) != "weighted_voting":
        return None
    if _frontend_validation_source(str(record.get("source_authority") or "")):
        return None
    return WeightedVotingRolloutValidation(
        backend_shadow_passed=bool(record.get("backend_shadow_passed")),
        shadow_comparison_passed=bool(record.get("shadow_comparison_passed")),
        static_equal_weights_passed=bool(record.get("static_equal_weights_passed")),
        performance_weights_validated=bool(record.get("performance_weights_validated")),
        dynamic_reduction_validated=bool(record.get("dynamic_reduction_validated")),
        dynamic_entry_exit_validated=bool(record.get("dynamic_entry_exit_validated")),
        dynamic_increase_validated=bool(record.get("dynamic_increase_validated")),
        manual_paper_submission_validated=bool(record.get("manual_paper_submission_validated")),
        tests_passed=bool(record.get("tests_passed")),
        paper_validations_passed=bool(record.get("paper_validations_passed")),
        paper_broker_e2e_validated=bool(record.get("paper_broker_e2e_validated")),
        reconciliation_validated=bool(record.get("reconciliation_validated")),
        restart_recovery_validated=bool(record.get("restart_recovery_validated")),
        persisted_operator_approval=bool(record.get("persisted_operator_approval") and record.get("approved_by")),
        live_trading_enabled=bool(record.get("live_trading_enabled")),
        validation_record_id=str(record.get("validation_record_id") or ""),
        source_authority=str(record.get("source_authority") or ""),
        approved_by=str(record.get("approved_by") or ""),
        recorded_at=str(record.get("recorded_at") or ""),
    )


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
    allowed_shadow_to_auto_small = (
        current_stage == "shadow_decisions"
        and target_stage == "automatic_paper_small_allocation"
        and evidence.automated_paper_readiness_detected
    )
    if CONTROLLED_ROLLOUT_STAGES.index(target_stage) > CONTROLLED_ROLLOUT_STAGES.index(current_stage) + 1 and not allowed_shadow_to_auto_small:
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


def controlled_rollout_evidence_from_shadow_report(
    report: Mapping[str, Any],
    *,
    explicit_configuration_approval: bool = False,
    manual_paper_sample_count: int = 0,
    restart_recovery_successful: bool = False,
    protective_order_reliability_ok: bool = False,
) -> WeightedVotingControlledRolloutEvidence:
    """Convert a Weighted Voting shadow evidence report into rollout evidence.

    Shadow evidence can prove runtime safety properties, but it cannot by
    itself grant explicit configuration approval or replace manual paper
    samples for automatic allocation promotion.
    """

    if str(report.get("algorithmId") or report.get("algorithm_id")) != "weighted_voting":
        raise ValueError("Weighted Voting rollout evidence must come from a weighted_voting shadow report")
    if bool(report.get("liveOrdersSubmitted")):
        raise ValueError("Weighted Voting shadow evidence cannot include live order submission")

    decisions = _nested_int(report, "decisions", "count")
    accepted = _nested_int(report, "acceptedProposals", "count")
    latency = _nested_mapping(report, "latency")
    latency_max_ms = _float_value(latency.get("maxLatencyMs", latency.get("maxMs")))
    reconciliation = _nested_mapping(report, "reconciliationHealth")
    runtime_health = _nested_mapping(reconciliation, "runtimeHealth")
    restart_recovery = _nested_mapping(report, "restartRecovery")
    protective_order_behavior = _nested_mapping(report, "protectiveOrderBehavior")
    duplicate_prevented = bool(_nested_value(report, "duplicatePrevention", "duplicateEventPrevented"))
    discrepancy_count = _int_value(reconciliation.get("discrepancyCount"))
    entries_paused = bool(reconciliation.get("entriesPaused"))
    inventory_reconciled = bool(reconciliation.get("inventoryReconciled")) and not entries_paused and discrepancy_count == 0
    recovery_required = bool(runtime_health.get("recoveryRequired"))
    restart_ok = bool(restart_recovery.get("passed")) or bool(restart_recovery_successful)
    protective_ok = bool(protective_order_behavior.get("passed")) or bool(protective_order_reliability_ok)
    latency_ok = latency_max_ms is not None and latency_max_ms <= 250.0
    transaction_cost_ok = _nested_value(report, "pnl", "netUnrealizedAfterFees") is not None
    drawdown_ok = _nested_float(report, "pnl", "netUnrealizedAfterFees", default=0.0) >= 0.0
    data_ok = _all_runtime_contexts_have_fresh_data(report)
    global_ok = accepted > 0 and _nested_int(report, "globalGateApplications", "count") >= decisions
    automated_ready = all(
        (
            decisions >= 50,
            not recovery_required,
            inventory_reconciled,
            duplicate_prevented,
            latency_ok,
            accepted > 0,
            data_ok,
            global_ok,
            restart_ok,
            protective_ok,
            transaction_cost_ok,
            drawdown_ok,
        )
    )

    return WeightedVotingControlledRolloutEvidence(
        no_unresolved_isolation_failures=not recovery_required and not bool(report.get("liveOrdersSubmitted")),
        inventory_reconciled=inventory_reconciled,
        no_duplicate_order_incidents=duplicate_prevented,
        worker_reliability_ok=not recovery_required and decisions > 0,
        decision_latency_ok=latency_ok,
        broker_latency_ok=accepted > 0 and report.get("simulatedFills") is not None,
        data_freshness_stable=data_ok,
        global_risk_fail_closed_tests_passing=global_ok,
        restart_recovery_successful=restart_ok,
        shadow_opportunity_count=decisions,
        manual_paper_sample_count=manual_paper_sample_count,
        transaction_cost_adjusted_paper_stability_ok=transaction_cost_ok,
        drawdown_within_limit=drawdown_ok,
        position_pnl_attribution_accurate=transaction_cost_ok,
        protective_order_reliability_ok=protective_ok,
        explicit_configuration_approval=explicit_configuration_approval,
        automated_paper_readiness_detected=automated_ready,
        evidence_id=str(report.get("runId") or report.get("run_id") or ""),
    )


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
    store: WeightedVotingRolloutStore | None = None,
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

    stage_auto_allowed = automatic_submission_allowed(flags=flags, validation=validation, store=store)
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
    active_validation = validation
    if stage not in ROLLOUT_STAGES:
        raise ValueError(f"unknown Weighted Voting rollout stage: {stage}")
    if active_validation is None:
        if stage == "automatic_paper_submission":
            return WeightedVotingRolloutStageStatus(
                stage=stage,
                permission=RolloutPermission.BLOCKED.value,
                reason_codes=("weighted_voting.rollout.persisted_validation_record_missing",),
                explanation="Weighted Voting automatic paper submission is blocked until backend-owned persisted validation evidence is available.",
            )
        active_validation = WeightedVotingRolloutValidation(source_authority="weighted_voting.rollout.missing_status_projection")
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
    store: WeightedVotingRolloutStore | None = None,
) -> dict[str, object]:
    active_flags = flags or rollout_feature_flags()
    persisted_validation = load_persisted_rollout_validation(store)
    active_validation = validation or persisted_validation
    stages = tuple(evaluate_rollout_stage(stage, flags=active_flags, validation=active_validation).model_dump() for stage in ROLLOUT_STAGES)
    control = evaluate_weighted_voting_rollout_control(
        requested_state=requested_state,
        account_wide_emergency_shutdown=account_wide_emergency_shutdown,
        disabled_algorithm_ids=disabled_algorithm_ids,
        flags=active_flags,
        validation=active_validation,
        store=store if validation is None else None,
    )
    return {
        "algorithm_id": "weighted_voting",
        "rollout_version": WEIGHTED_VOTING_ROLLOUT_VERSION,
        "namespace": WEIGHTED_VOTING_ROLLOUT_NAMESPACE,
        "allowed_states": WEIGHTED_VOTING_ROLLOUT_STATES,
        "controlled_stages": CONTROLLED_ROLLOUT_STAGES,
        "controlled_rollout": controlled_rollout_status(store),
        "control": control.model_dump(),
        "effective_state": control.effective_state,
        "feature_flags": active_flags.model_dump(),
        "validation": active_validation.model_dump() if active_validation else {"status": "missing", "reason_codes": ("weighted_voting.rollout.persisted_validation_record_missing",)},
        "validation_source": "explicit_argument" if validation else ("persisted_backend_record" if persisted_validation else "missing"),
        "stages": stages,
        "automatic_submission_allowed": automatic_submission_allowed(flags=active_flags, validation=active_validation, store=store if validation is None else None),
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
    active_flags = flags or rollout_feature_flags()
    active_validation = validation
    if store is not None:
        active_validation = active_validation or load_persisted_rollout_validation(store)
        if active_validation is None:
            return False
        if not bool(controlled_rollout_status(store)["automatic_paper_submission_allowed"]):
            return False
    if active_validation is None:
        return False
    status = evaluate_rollout_stage(
        "automatic_paper_submission",
        flags=active_flags,
        validation=active_validation,
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

    static_or_performance_validated = bool(validation.static_equal_weights_passed or validation.performance_weights_validated)
    dynamic_reduction_requirement = (
        (validation.dynamic_reduction_validated, "weighted_voting.rollout.dynamic_reduction_not_validated"),
    ) if flags.dynamic_reduction_enabled else ()
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
            *dynamic_reduction_requirement,
        ),
        "dynamic_increase": (
            (validation.dynamic_entry_exit_validated, "weighted_voting.rollout.dynamic_entry_exit_not_validated"),
        ),
        "manual_paper_submission": (
            (validation.dynamic_entry_exit_validated, "weighted_voting.rollout.dynamic_entry_exit_not_validated"),
        ),
        "automatic_paper_submission": (
            (validation.backend_shadow_passed, "weighted_voting.rollout.backend_shadow_not_validated"),
            (validation.shadow_comparison_passed, "weighted_voting.rollout.shadow_comparison_not_validated"),
            (static_or_performance_validated, "weighted_voting.rollout.static_or_performance_weights_not_validated"),
            *dynamic_reduction_requirement,
            (validation.dynamic_entry_exit_validated, "weighted_voting.rollout.dynamic_entry_exit_not_validated"),
            (validation.manual_paper_submission_validated, "weighted_voting.rollout.manual_paper_submission_not_validated"),
            (validation.tests_passed, "weighted_voting.rollout.tests_not_passed"),
            (validation.paper_broker_e2e_validated, "weighted_voting.rollout.paper_broker_e2e_not_validated"),
            (validation.reconciliation_validated, "weighted_voting.rollout.reconciliation_not_validated"),
            (validation.restart_recovery_validated, "weighted_voting.rollout.restart_recovery_not_validated"),
            (validation.persisted_operator_approval, "weighted_voting.rollout.persisted_operator_approval_missing"),
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
                (evidence.position_pnl_attribution_accurate, "weighted_voting.rollout.position_pnl_attribution_unverified"),
            )
        )
    if stage in {"automatic_paper_small_allocation", "automatic_paper_approved_allocation"}:
        checks.extend(
            (
                (evidence.automated_paper_readiness_detected, "weighted_voting.rollout.automated_paper_readiness_not_detected"),
                (evidence.restart_recovery_successful, "weighted_voting.rollout.restart_recovery_missing"),
                (evidence.protective_order_reliability_ok, "weighted_voting.rollout.protective_order_reliability_unverified"),
                (evidence.transaction_cost_adjusted_paper_stability_ok, "weighted_voting.rollout.paper_stability_unacceptable"),
                (evidence.drawdown_within_limit, "weighted_voting.rollout.drawdown_limit_exceeded"),
            )
        )
    if stage == "automatic_paper_approved_allocation":
        checks.extend(
            (
                (evidence.manual_paper_sample_count >= 20, "weighted_voting.rollout.manual_paper_sample_minimum_not_met"),
                (evidence.explicit_configuration_approval, "weighted_voting.rollout.explicit_configuration_approval_missing"),
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


def _frontend_validation_source(source: str) -> bool:
    normalized = source.strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in ("frontend", "browser", "react", "client_state", "local_storage", "session_storage", "window.", "ui_button"))


def _read_optional(store: WeightedVotingRolloutStore, key: str) -> dict | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


def _nested_mapping(source: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = source.get(key)
    return value if isinstance(value, Mapping) else {}


def _nested_value(source: Mapping[str, Any], first: str, second: str) -> Any:
    return _nested_mapping(source, first).get(second)


def _nested_int(source: Mapping[str, Any], first: str, second: str) -> int:
    return _int_value(_nested_value(source, first, second))


def _nested_float(source: Mapping[str, Any], first: str, second: str, default: float | None = None) -> float | None:
    value = _nested_value(source, first, second)
    return _float_value(value, default=default)


def _float_value(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _all_runtime_contexts_have_fresh_data(report: Mapping[str, Any]) -> bool:
    contexts = _nested_mapping(report, "runtimeContexts").get("items") or ()
    if not contexts:
        return False
    for context in contexts:
        if not isinstance(context, Mapping):
            return False
        if context.get("read_only_account_equity_available") is not True:
            return False
        if context.get("read_only_broker_buying_power_available") is not True:
            return False
        if context.get("global_risk_service_available") is not True:
            return False
    return True


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
    "controlled_rollout_evidence_from_shadow_report",
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
