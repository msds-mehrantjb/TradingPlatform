"""Evidence-derived staged paper rollout controls for Regime."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Mapping, Protocol


REGIME_ROLLOUT_VERSION = "regime_staged_paper_deployment_v2"

REGIME_V2_ENABLED = "REGIME_V2_ENABLED"
REGIME_DYNAMIC_PROFILE_ENABLED = "REGIME_DYNAMIC_PROFILE_ENABLED"
REGIME_ML_MODE = "REGIME_ML_MODE"
REGIME_GLOBAL_RISK_MANAGER_ENABLED = "REGIME_GLOBAL_RISK_MANAGER_ENABLED"
REGIME_SHORT_ENTRIES_ENABLED = "REGIME_SHORT_ENTRIES_ENABLED"
REGIME_PAPER_SUBMISSION_ENABLED = "REGIME_PAPER_SUBMISSION_ENABLED"
REGIME_AUTOMATIC_ORDER_SUBMISSION_ENABLED = "REGIME_AUTOMATIC_ORDER_SUBMISSION_ENABLED"

REGIME_ROLLOUT_STATE_KEY = "regime.rollout.active"
REGIME_ROLLBACK_STATE_KEY = "regime.rollout.previous_valid"

RegimeMlMode = Literal["off", "shadow", "confirm_only", "active"]
RegimeRolloutStage = Literal[
    "stage_a_offline_validation",
    "stage_b_shadow_runtime",
    "stage_c_paper_intent_validation",
    "stage_d_limited_spy_paper_submission",
    "stage_e_expanded_paper_validation",
]

REGIME_ROLLOUT_STAGES: tuple[RegimeRolloutStage, ...] = (
    "stage_a_offline_validation",
    "stage_b_shadow_runtime",
    "stage_c_paper_intent_validation",
    "stage_d_limited_spy_paper_submission",
    "stage_e_expanded_paper_validation",
)
REGIME_ROLLOUT_PHASES = REGIME_ROLLOUT_STAGES

LEGACY_STAGE_ALIASES: dict[str, RegimeRolloutStage] = {
    "historical_characterization": "stage_a_offline_validation",
    "dedicated_backtest": "stage_a_offline_validation",
    "untouched_oos": "stage_a_offline_validation",
    "ml_shadow": "stage_b_shadow_runtime",
    "paper_shadow_decisions": "stage_b_shadow_runtime",
    "shadow_comparison": "stage_b_shadow_runtime",
    "limited_paper_orders": "stage_d_limited_spy_paper_submission",
    "global_gate_monitoring": "stage_e_expanded_paper_validation",
    "multi_regime_trade_collection": "stage_e_expanded_paper_validation",
    "promotion_review": "stage_e_expanded_paper_validation",
}

STAGE_LABELS: dict[str, str] = {
    "stage_a_offline_validation": "Stage A - deterministic offline validation",
    "stage_b_shadow_runtime": "Stage B - background shadow runtime",
    "stage_c_paper_intent_validation": "Stage C - paper intent validation",
    "stage_d_limited_spy_paper_submission": "Stage D - limited SPY paper submission",
    "stage_e_expanded_paper_validation": "Stage E - expanded paper validation",
}

STAGE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "stage_a_offline_validation": (
        "focused_tests_passed",
        "full_backend_tests_passed",
        "realistic_backtesting_passed",
        "walk_forward_passed",
        "holdout_passed",
        "strategy_regime_occupancy_reasonable",
        "transaction_costs_included",
    ),
    "stage_b_shadow_runtime": (
        "completed_bar_reliability_passed",
        "decision_latency_passed",
        "stable_hysteresis_passed",
        "shadow_regime_occupancy_reasonable",
        "strategy_opportunity_frequency_reasonable",
        "blocker_frequency_reasonable",
        "restart_recovery_passed",
        "duplicate_prevention_passed",
        "paper_backtest_replay_parity_passed",
    ),
    "stage_c_paper_intent_validation": (
        "quantity_validated",
        "stops_validated",
        "targets_validated",
        "transaction_cost_gate_validated",
        "global_risk_reservation_validated",
        "outbox_state_validated",
        "idempotency_validated",
    ),
    "stage_d_limited_spy_paper_submission": (
        "spy_only_validated",
        "single_instance_validated",
        "long_only_validated",
        "low_quantity_cap_validated",
        "no_pyramiding_validated",
        "limited_trades_per_day_validated",
        "strict_daily_loss_validated",
        "end_of_day_flatten_validated",
    ),
    "stage_e_expanded_paper_validation": (
        "fill_quality_passed",
        "reconciliation_passed",
        "expanded_restart_recovery_passed",
        "slippage_passed",
        "daily_loss_protection_passed",
        "position_isolation_passed",
        "no_duplicate_orders_passed",
    ),
}

REQUIRED_ML_PROMOTION_EVIDENCE: tuple[str, ...] = (
    "ml_deterministic_baseline_stability",
    "ml_walk_forward_improvement",
    "ml_holdout_improvement",
    "ml_paper_shadow_stability",
    "ml_calibration_passed",
    "ml_drift_monitoring_passed",
    "ml_rollback_safety_passed",
)


class RegimeRolloutStore(Protocol):
    def read_snapshot(self, key: str) -> dict:
        ...

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        ...


class RegimeRolloutPermission(str, Enum):
    ENABLED = "enabled"
    BLOCKED = "blocked"


class RegimeReadinessStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


REGIME_PAPER_READINESS_VERSION = "regime_paper_readiness_v1"

REGIME_PAPER_READINESS_TEST_GROUPS: tuple[str, ...] = (
    "authority_boundary",
    "runtime_mode_no_live",
    "cross_algorithm_isolation",
    "settings_ownership",
    "settings_activation_rollback",
    "stateful_hysteresis",
    "strategy_semantics",
    "family_anti_duplication",
    "completed_bar_background_processing",
    "stale_out_of_order_events",
    "local_risk_blockers",
    "transaction_cost_gate",
    "global_risk_reservation",
    "outbox_idempotency",
    "fake_paper_broker_lifecycle",
    "position_trade_isolation",
    "restart_crash_recovery",
    "backtest_fill_model",
    "net_pnl_drawdown",
    "walk_forward_failure",
    "backtest_paper_shadow_parity",
    "api_responsiveness",
)

REGIME_PAPER_READINESS_TEST_PATHS: dict[str, tuple[str, ...]] = {
    "authority_boundary": ("backend/tests/regime/test_paper_only_authority_boundary.py", "frontend/tests/V2DecisionPanel.test.ts"),
    "runtime_mode_no_live": ("backend/tests/regime/test_paper_only_authority_boundary.py",),
    "cross_algorithm_isolation": ("backend/tests/regime/test_persistence_isolation_boundary.py",),
    "settings_ownership": ("backend/tests/regime/test_versioned_settings_boundary.py",),
    "settings_activation_rollback": ("backend/tests/regime/test_versioned_settings_boundary.py",),
    "stateful_hysteresis": ("backend/tests/regime/transitions", "backend/tests/regime/test_step5_background_runtime.py"),
    "strategy_semantics": ("backend/tests/regime/strategies/directional",),
    "family_anti_duplication": ("backend/tests/regime/decision/test_contribution_caps.py",),
    "completed_bar_background_processing": ("backend/tests/regime/test_step5_background_runtime.py",),
    "stale_out_of_order_events": ("backend/tests/regime/test_step5_background_runtime.py",),
    "local_risk_blockers": ("backend/tests/regime/test_step6_local_risk.py",),
    "transaction_cost_gate": ("backend/tests/regime/test_step6_local_risk.py",),
    "global_risk_reservation": ("backend/tests/regime/test_step7_paper_execution_positions.py",),
    "outbox_idempotency": ("backend/tests/regime/test_step7_paper_execution_positions.py",),
    "fake_paper_broker_lifecycle": ("backend/tests/regime/test_step7_paper_execution_positions.py",),
    "position_trade_isolation": ("backend/tests/regime/test_step7_paper_execution_positions.py",),
    "restart_crash_recovery": ("backend/tests/regime/test_step9_fail_closed_health.py",),
    "backtest_fill_model": ("backend/tests/regime/test_step8_background_backtest_parity.py",),
    "net_pnl_drawdown": ("backend/tests/regime/backtest/test_metrics.py", "backend/tests/regime/test_step8_background_backtest_parity.py"),
    "walk_forward_failure": ("backend/tests/regime/backtest/test_walk_forward.py",),
    "backtest_paper_shadow_parity": ("backend/tests/regime/test_step8_background_backtest_parity.py",),
    "api_responsiveness": ("backend/tests/regime/test_step5_background_runtime.py", "backend/tests/regime/test_step8_background_backtest_parity.py"),
}

REGIME_PAPER_READINESS_STAGES: tuple[str, ...] = (
    "stage_1_offline_only",
    "stage_2_background_shadow",
    "stage_3_paper_intent_only",
    "stage_4_limited_automated_paper_trading",
)

REGIME_PAPER_READINESS_STAGE_LABELS: dict[str, str] = {
    "stage_1_offline_only": "Stage 1 - Offline only",
    "stage_2_background_shadow": "Stage 2 - Background shadow",
    "stage_3_paper_intent_only": "Stage 3 - Paper intent only",
    "stage_4_limited_automated_paper_trading": "Stage 4 - Limited automated paper trading",
}

REGIME_PAPER_READINESS_STAGE_TEST_GROUPS: dict[str, tuple[str, ...]] = {
    "stage_1_offline_only": (
        "authority_boundary",
        "runtime_mode_no_live",
        "cross_algorithm_isolation",
        "settings_ownership",
        "settings_activation_rollback",
        "stateful_hysteresis",
        "strategy_semantics",
        "family_anti_duplication",
        "backtest_fill_model",
        "net_pnl_drawdown",
        "walk_forward_failure",
        "backtest_paper_shadow_parity",
    ),
    "stage_2_background_shadow": (
        "completed_bar_background_processing",
        "stale_out_of_order_events",
        "restart_crash_recovery",
        "api_responsiveness",
    ),
    "stage_3_paper_intent_only": (
        "local_risk_blockers",
        "transaction_cost_gate",
        "global_risk_reservation",
        "outbox_idempotency",
    ),
    "stage_4_limited_automated_paper_trading": (
        "fake_paper_broker_lifecycle",
        "position_trade_isolation",
    ),
}

REGIME_PAPER_READINESS_STAGE_ROLLOUT_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "stage_1_offline_only": STAGE_REQUIREMENTS["stage_a_offline_validation"],
    "stage_2_background_shadow": STAGE_REQUIREMENTS["stage_b_shadow_runtime"],
    "stage_3_paper_intent_only": STAGE_REQUIREMENTS["stage_c_paper_intent_validation"],
    "stage_4_limited_automated_paper_trading": STAGE_REQUIREMENTS["stage_d_limited_spy_paper_submission"],
}


@dataclass(frozen=True)
class RegimeRolloutFlags:
    v2_enabled: bool = True
    dynamic_profile_enabled: bool = True
    ml_mode: RegimeMlMode = "shadow"
    global_risk_manager_enabled: bool = True
    short_entries_enabled: bool = False
    paper_submission_enabled: bool = False
    automatic_order_submission_enabled: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "RegimeRolloutFlags":
        source = environ or os.environ
        return cls(
            v2_enabled=_env_bool(source, REGIME_V2_ENABLED, True),
            dynamic_profile_enabled=_env_bool(source, REGIME_DYNAMIC_PROFILE_ENABLED, True),
            ml_mode=_env_ml_mode(source.get(REGIME_ML_MODE), "shadow"),
            global_risk_manager_enabled=_env_bool(source, REGIME_GLOBAL_RISK_MANAGER_ENABLED, True),
            short_entries_enabled=_env_bool(source, REGIME_SHORT_ENTRIES_ENABLED, False),
            paper_submission_enabled=_env_bool(source, REGIME_PAPER_SUBMISSION_ENABLED, False),
            automatic_order_submission_enabled=_env_bool(source, REGIME_AUTOMATIC_ORDER_SUBMISSION_ENABLED, False),
        )

    def model_dump(self) -> dict[str, bool | str]:
        return {
            "rollout_version": REGIME_ROLLOUT_VERSION,
            REGIME_V2_ENABLED: self.v2_enabled,
            REGIME_DYNAMIC_PROFILE_ENABLED: self.dynamic_profile_enabled,
            REGIME_ML_MODE: self.ml_mode,
            REGIME_GLOBAL_RISK_MANAGER_ENABLED: self.global_risk_manager_enabled,
            REGIME_SHORT_ENTRIES_ENABLED: self.short_entries_enabled,
            REGIME_PAPER_SUBMISSION_ENABLED: self.paper_submission_enabled,
            REGIME_AUTOMATIC_ORDER_SUBMISSION_ENABLED: self.automatic_order_submission_enabled,
            "paper_trading_only": True,
            "live_trading_allowed": False,
        }


@dataclass(frozen=True)
class RegimeRolloutEvidence:
    persisted_evidence_ids: frozenset[str] = frozenset()
    focused_tests_passed: bool = False
    full_backend_tests_passed: bool = False
    realistic_backtesting_passed: bool = False
    walk_forward_passed: bool = False
    holdout_passed: bool = False
    strategy_regime_occupancy_reasonable: bool = False
    transaction_costs_included: bool = False
    completed_bar_reliability_passed: bool = False
    decision_latency_passed: bool = False
    stable_hysteresis_passed: bool = False
    shadow_regime_occupancy_reasonable: bool = False
    strategy_opportunity_frequency_reasonable: bool = False
    blocker_frequency_reasonable: bool = False
    restart_recovery_passed: bool = False
    duplicate_prevention_passed: bool = False
    paper_backtest_replay_parity_passed: bool = False
    quantity_validated: bool = False
    stops_validated: bool = False
    targets_validated: bool = False
    transaction_cost_gate_validated: bool = False
    global_risk_reservation_validated: bool = False
    outbox_state_validated: bool = False
    idempotency_validated: bool = False
    spy_only_validated: bool = False
    single_instance_validated: bool = False
    long_only_validated: bool = False
    low_quantity_cap_validated: bool = False
    no_pyramiding_validated: bool = False
    limited_trades_per_day_validated: bool = False
    strict_daily_loss_validated: bool = False
    end_of_day_flatten_validated: bool = False
    fill_quality_passed: bool = False
    reconciliation_passed: bool = False
    expanded_restart_recovery_passed: bool = False
    slippage_passed: bool = False
    daily_loss_protection_passed: bool = False
    position_isolation_passed: bool = False
    no_duplicate_orders_passed: bool = False
    paper_submission_attempted_before_stage_d: bool = False
    live_trading_enabled: bool = False
    broker_orders_created_in_shadow: int = 0
    broker_orders_created_in_intent_validation: int = 0
    automatic_order_submission_enabled: bool = False
    ml_mode: RegimeMlMode = "shadow"
    ml_shadow_only: bool = True

    def model_dump(self) -> dict[str, object]:
        data = self.__dict__.copy()
        data["persisted_evidence_ids"] = tuple(sorted(self.persisted_evidence_ids))
        return data


@dataclass(frozen=True)
class RegimePaperReadinessEvidence:
    rollout_evidence: RegimeRolloutEvidence = field(default_factory=RegimeRolloutEvidence)
    flags: RegimeRolloutFlags = field(default_factory=RegimeRolloutFlags)
    passed_test_groups: frozenset[str] = frozenset()
    failed_test_groups: frozenset[str] = frozenset()
    not_run_test_groups: frozenset[str] = frozenset()
    persisted_evidence_ids: frozenset[str] = frozenset()
    critical_defects: tuple[str, ...] = ()
    completed_bar_reliability_observed: bool = False
    runtime_latency_observed: bool = False
    persistent_hysteresis_observed: bool = False
    strategy_opportunity_frequency_observed: bool = False
    blocker_frequency_observed: bool = False
    restart_recovery_observed: bool = False
    duplicate_decision_prevention_observed: bool = False
    replay_parity_observed: bool = False
    sizing_observed: bool = False
    stops_targets_observed: bool = False
    cost_gate_observed: bool = False
    reservation_behaviour_observed: bool = False
    outbox_idempotency_observed: bool = False
    outbox_expiry_observed: bool = False

    def model_dump(self) -> dict[str, object]:
        return {
            "rolloutEvidence": self.rollout_evidence.model_dump(),
            "flags": self.flags.model_dump(),
            "passedTestGroups": tuple(sorted(self.passed_test_groups)),
            "failedTestGroups": tuple(sorted(self.failed_test_groups)),
            "notRunTestGroups": tuple(sorted(self.not_run_test_groups)),
            "persistedEvidenceIds": tuple(sorted(self.persisted_evidence_ids)),
            "criticalDefects": self.critical_defects,
            "completedBarReliabilityObserved": self.completed_bar_reliability_observed,
            "runtimeLatencyObserved": self.runtime_latency_observed,
            "persistentHysteresisObserved": self.persistent_hysteresis_observed,
            "strategyOpportunityFrequencyObserved": self.strategy_opportunity_frequency_observed,
            "blockerFrequencyObserved": self.blocker_frequency_observed,
            "restartRecoveryObserved": self.restart_recovery_observed,
            "duplicateDecisionPreventionObserved": self.duplicate_decision_prevention_observed,
            "replayParityObserved": self.replay_parity_observed,
            "sizingObserved": self.sizing_observed,
            "stopsTargetsObserved": self.stops_targets_observed,
            "costGateObserved": self.cost_gate_observed,
            "reservationBehaviourObserved": self.reservation_behaviour_observed,
            "outboxIdempotencyObserved": self.outbox_idempotency_observed,
            "outboxExpiryObserved": self.outbox_expiry_observed,
        }


@dataclass(frozen=True)
class RegimeRolloutValidation:
    rollout_evidence: RegimeRolloutEvidence | None = None
    historical_characterization_passed: bool = False
    dedicated_backtest_passed: bool = False
    untouched_oos_passed: bool = False
    ml_shadow_passed: bool = False
    paper_shadow_decisions_passed: bool = False
    old_new_decision_comparison_passed: bool = False
    limited_paper_orders_approved: bool = False
    global_gate_monitoring_passed: bool = False
    enough_multi_regime_trades_collected: bool = False
    performance_review_passed: bool = False
    tests_passed: bool = False
    live_trading_enabled: bool = False

    def model_dump(self) -> dict[str, object]:
        return {
            **self.__dict__,
            "rollout_evidence": (self.rollout_evidence or _legacy_validation_to_evidence(self)).model_dump(),
        }


@dataclass(frozen=True)
class RegimeRolloutStageStatus:
    phase: str
    stage: RegimeRolloutStage
    label: str
    permission: RegimeRolloutPermission | str
    reason_codes: tuple[str, ...]
    explanation: str
    requirement_status: dict[str, str]

    @property
    def enabled(self) -> bool:
        return self.permission == RegimeRolloutPermission.ENABLED.value

    def model_dump(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "stage": self.stage,
            "label": self.label,
            "permission": self.permission,
            "enabled": self.enabled,
            "reason_codes": self.reason_codes,
            "explanation": self.explanation,
            "requirementStatus": self.requirement_status,
        }


RegimeRolloutPhaseStatus = RegimeRolloutStageStatus


def regime_rollout_feature_flags(environ: Mapping[str, str] | None = None) -> RegimeRolloutFlags:
    return RegimeRolloutFlags.from_env(environ)


def evaluate_regime_rollout_phase(
    phase: str,
    *,
    flags: RegimeRolloutFlags | None = None,
    validation: RegimeRolloutValidation | RegimeRolloutEvidence | None = None,
    evidence: RegimeRolloutEvidence | None = None,
) -> RegimeRolloutStageStatus:
    return evaluate_regime_rollout_stage(phase, flags=flags, validation=validation, evidence=evidence)


def evaluate_regime_rollout_stage(
    stage: str,
    *,
    flags: RegimeRolloutFlags | None = None,
    validation: RegimeRolloutValidation | RegimeRolloutEvidence | None = None,
    evidence: RegimeRolloutEvidence | None = None,
) -> RegimeRolloutStageStatus:
    active_flags = flags or regime_rollout_feature_flags()
    active_evidence = evidence or _coerce_evidence(validation)
    canonical_stage = _canonical_stage(stage)
    blockers = _stage_blockers(canonical_stage, active_flags, active_evidence)
    permission = RegimeRolloutPermission.BLOCKED.value if blockers else RegimeRolloutPermission.ENABLED.value
    return RegimeRolloutStageStatus(
        phase=stage,
        stage=canonical_stage,
        label=STAGE_LABELS[canonical_stage],
        permission=permission,
        reason_codes=tuple(blockers) if blockers else (f"regime.rollout.{canonical_stage}.enabled",),
        explanation=(
            "Regime rollout stage is enabled for staged paper validation only."
            if not blockers
            else "Regime rollout stage is blocked until preceding stage gates and persisted evidence pass."
        ),
        requirement_status=_stage_requirement_status(canonical_stage, active_evidence),
    )


def regime_rollout_status(
    *,
    flags: RegimeRolloutFlags | None = None,
    validation: RegimeRolloutValidation | RegimeRolloutEvidence | None = None,
    evidence: RegimeRolloutEvidence | None = None,
) -> dict[str, object]:
    active_flags = flags or regime_rollout_feature_flags()
    active_evidence = evidence or _coerce_evidence(validation)
    readiness = build_regime_paper_readiness_report(RegimePaperReadinessEvidence(rollout_evidence=active_evidence, flags=active_flags))
    stages = tuple(
        evaluate_regime_rollout_stage(stage, flags=active_flags, evidence=active_evidence).model_dump()
        for stage in REGIME_ROLLOUT_STAGES
    )
    return {
        "algorithm_id": "regime",
        "rollout_version": REGIME_ROLLOUT_VERSION,
        "feature_flags": active_flags.model_dump(),
        "validation": active_evidence.model_dump(),
        "evidence": active_evidence.model_dump(),
        "phases": stages,
        "stages": stages,
        "paper_shadow_allowed": evaluate_regime_rollout_stage("stage_b_shadow_runtime", flags=active_flags, evidence=active_evidence).enabled,
        "paper_intent_generation_allowed": evaluate_regime_rollout_stage("stage_c_paper_intent_validation", flags=active_flags, evidence=active_evidence).enabled,
        "limited_paper_orders_allowed": limited_paper_orders_allowed(flags=active_flags, evidence=active_evidence),
        "paper_submission_allowed": paper_submission_allowed(flags=active_flags, evidence=active_evidence),
        "automatic_order_submission_allowed": False,
        "live_trading_allowed": False,
        "paperReadiness": readiness,
        "ml_shadow_only": active_flags.ml_mode == "shadow" and active_evidence.ml_shadow_only and active_evidence.ml_mode == "shadow",
        "rollback_plan": rollback_configuration(),
        "deployment_sequence": REGIME_ROLLOUT_STAGES,
        "reason_codes": (
            "regime.rollout.paper_only",
            "regime.rollout.automatic_order_submission_disabled_by_default",
            "regime.rollout.live_trading_never_allowed",
            "regime.rollout.ml_shadow_only",
        ),
    }


def build_regime_paper_readiness_report(
    evidence: RegimePaperReadinessEvidence | RegimeRolloutEvidence | None = None,
    *,
    flags: RegimeRolloutFlags | None = None,
) -> dict[str, object]:
    readiness_evidence = _coerce_readiness_evidence(evidence, flags=flags)
    test_groups = tuple(_paper_readiness_test_group_status(group, readiness_evidence) for group in REGIME_PAPER_READINESS_TEST_GROUPS)
    stages = tuple(_paper_readiness_stage_status(stage, readiness_evidence) for stage in REGIME_PAPER_READINESS_STAGES)
    counts = {
        status.value: sum(1 for item in (*test_groups, *stages) if item["status"] == status.value)
        for status in RegimeReadinessStatus
    }
    blocking = [
        item["id"]
        for item in (*test_groups, *stages)
        if item["status"] != RegimeReadinessStatus.PASS.value and item.get("requiredForPaperReadiness", True)
    ]
    stage_status = {stage["id"]: stage["status"] for stage in stages}
    return {
        "algorithmId": "regime",
        "version": REGIME_PAPER_READINESS_VERSION,
        "evidenceDerived": True,
        "allowedStatuses": tuple(status.value for status in RegimeReadinessStatus),
        "passingStatus": RegimeReadinessStatus.PASS.value,
        "nonPassingStatuses": (
            RegimeReadinessStatus.FAIL.value,
            RegimeReadinessStatus.NOT_RUN.value,
            RegimeReadinessStatus.INSUFFICIENT_EVIDENCE.value,
        ),
        "complete": not blocking,
        "counts": counts,
        "blockingItems": tuple(blocking),
        "testGroups": test_groups,
        "stages": stages,
        "stageStatus": stage_status,
        "paperSubmissionAllowed": (
            stage_status["stage_4_limited_automated_paper_trading"] == RegimeReadinessStatus.PASS.value
            and paper_submission_allowed(flags=readiness_evidence.flags, evidence=readiness_evidence.rollout_evidence)
        ),
        "automaticPaperTradingEnabled": False,
        "liveTradingAllowed": False,
        "rolloutEvidence": readiness_evidence.rollout_evidence.model_dump(),
        "sourceEvidence": readiness_evidence.model_dump(),
    }


def limited_paper_orders_allowed(
    *,
    flags: RegimeRolloutFlags | None = None,
    validation: RegimeRolloutValidation | RegimeRolloutEvidence | None = None,
    evidence: RegimeRolloutEvidence | None = None,
) -> bool:
    return evaluate_regime_rollout_stage(
        "stage_d_limited_spy_paper_submission",
        flags=flags or regime_rollout_feature_flags(),
        evidence=evidence or _coerce_evidence(validation),
    ).enabled


def paper_submission_allowed(
    *,
    flags: RegimeRolloutFlags | None = None,
    validation: RegimeRolloutValidation | RegimeRolloutEvidence | None = None,
    evidence: RegimeRolloutEvidence | None = None,
) -> bool:
    active_flags = flags or regime_rollout_feature_flags()
    return active_flags.paper_submission_enabled and limited_paper_orders_allowed(
        flags=active_flags,
        evidence=evidence or _coerce_evidence(validation),
    )


def rollback_configuration() -> dict[str, object]:
    return {
        REGIME_V2_ENABLED: False,
        REGIME_DYNAMIC_PROFILE_ENABLED: False,
        REGIME_ML_MODE: "off",
        REGIME_GLOBAL_RISK_MANAGER_ENABLED: True,
        REGIME_SHORT_ENTRIES_ENABLED: False,
        REGIME_PAPER_SUBMISSION_ENABLED: False,
        REGIME_AUTOMATIC_ORDER_SUBMISSION_ENABLED: False,
        "paper_trading_only": True,
        "regime_new_entries": "disabled",
        "protective_exits": "preserved",
        "restore_previous_settings": True,
        "restore_previous_model_artifact": True,
        "database_migration_rollback": "safe_only",
        "disable_dynamic_profiles_only": {REGIME_DYNAMIC_PROFILE_ENABLED: False},
        "disable_ml_only": {REGIME_ML_MODE: "off"},
        "disable_regime_entries_preserve_exits": {"regime_new_entries": "disabled", "protective_exits": "preserved"},
        "delete_historical_records": False,
        "live_orders": False,
        "paper_broker_submission": False,
        "automatic_order_submission": False,
    }


def _paper_readiness_test_group_status(group: str, evidence: RegimePaperReadinessEvidence) -> dict[str, object]:
    if group in evidence.failed_test_groups:
        status = RegimeReadinessStatus.FAIL
        reason_codes = (f"regime.readiness.test_group_failed:{group}",)
    elif group in evidence.not_run_test_groups:
        status = RegimeReadinessStatus.NOT_RUN
        reason_codes = (f"regime.readiness.test_group_not_run:{group}",)
    elif _readiness_group_passed(evidence, group):
        status = RegimeReadinessStatus.PASS
        reason_codes = (f"regime.readiness.test_group_passed:{group}",)
    else:
        status = RegimeReadinessStatus.INSUFFICIENT_EVIDENCE
        reason_codes = (f"regime.readiness.test_group_evidence_missing:{group}",)
    return {
        "id": group,
        "status": status.value,
        "requiredForPaperReadiness": True,
        "evidence": REGIME_PAPER_READINESS_TEST_PATHS[group],
        "reasonCodes": reason_codes,
    }


def _paper_readiness_stage_status(stage: str, evidence: RegimePaperReadinessEvidence) -> dict[str, object]:
    blockers: list[str] = []
    statuses: dict[str, str] = {}
    for prior_stage in _paper_readiness_stages_through(stage):
        for group in REGIME_PAPER_READINESS_STAGE_TEST_GROUPS[prior_stage]:
            group_status = _paper_readiness_test_group_status(group, evidence)["status"]
            statuses[group] = group_status  # type: ignore[assignment]
            if group_status != RegimeReadinessStatus.PASS.value:
                blockers.append(f"regime.readiness.test_group_{str(group_status).split('.')[-1].lower()}:{group}")
        for requirement in REGIME_PAPER_READINESS_STAGE_ROLLOUT_REQUIREMENTS[prior_stage]:
            requirement_status = (
                RegimeReadinessStatus.PASS.value
                if _requirement_passed(evidence.rollout_evidence, requirement)
                else RegimeReadinessStatus.INSUFFICIENT_EVIDENCE.value
            )
            statuses[requirement] = requirement_status
            if requirement_status != RegimeReadinessStatus.PASS.value:
                blockers.append(f"regime.readiness.rollout_evidence_missing:{requirement}")

    blockers.extend(_readiness_stage_operational_blockers(stage, evidence))
    if evidence.critical_defects:
        blockers.extend(f"regime.readiness.critical_defect:{defect}" for defect in evidence.critical_defects)

    status = RegimeReadinessStatus.PASS if not blockers else _stage_failure_status(blockers)
    return {
        "id": stage,
        "label": REGIME_PAPER_READINESS_STAGE_LABELS[stage],
        "status": status.value,
        "requiredForPaperReadiness": True,
        "requirementStatus": statuses,
        "reasonCodes": tuple(dict.fromkeys(blockers)) if blockers else (f"regime.readiness.{stage}.passed",),
        "paperSubmissionEnabled": bool(evidence.flags.paper_submission_enabled),
        "automaticPaperTradingEnabled": False,
        "liveTradingAllowed": False,
    }


def _readiness_stage_operational_blockers(stage: str, evidence: RegimePaperReadinessEvidence) -> list[str]:
    blockers: list[str] = []
    rollout = evidence.rollout_evidence
    flags = evidence.flags
    if rollout.live_trading_enabled:
        blockers.append("regime.readiness.live_trading_never_allowed")
    if flags.automatic_order_submission_enabled or rollout.automatic_order_submission_enabled:
        blockers.append("regime.readiness.automatic_order_submission_disabled")
    if flags.ml_mode != "shadow" or rollout.ml_mode != "shadow" or not rollout.ml_shadow_only:
        blockers.append("regime.readiness.ml_shadow_only_required")
    if flags.short_entries_enabled:
        blockers.append("regime.readiness.short_entries_disabled_initially")
    if not flags.global_risk_manager_enabled:
        blockers.append("regime.readiness.global_risk_manager_required")
    if rollout.paper_submission_attempted_before_stage_d:
        blockers.append("regime.readiness.paper_submission_attempted_before_stage_4")
    if rollout.broker_orders_created_in_shadow:
        blockers.append("regime.readiness.shadow_created_broker_orders")
    if rollout.broker_orders_created_in_intent_validation:
        blockers.append("regime.readiness.intent_validation_created_broker_orders")

    if stage in {"stage_2_background_shadow", "stage_3_paper_intent_only", "stage_4_limited_automated_paper_trading"}:
        for observed, reason in (
            (evidence.completed_bar_reliability_observed, "completed_bar_reliability"),
            (evidence.runtime_latency_observed, "runtime_latency"),
            (evidence.persistent_hysteresis_observed, "persistent_hysteresis"),
            (evidence.strategy_opportunity_frequency_observed, "strategy_opportunity_frequency"),
            (evidence.blocker_frequency_observed, "blocker_frequency"),
            (evidence.restart_recovery_observed, "restart_recovery"),
            (evidence.duplicate_decision_prevention_observed, "duplicate_decision_prevention"),
            (evidence.replay_parity_observed, "replay_parity"),
        ):
            if not observed:
                blockers.append(f"regime.readiness.observation_missing:{reason}")

    if stage in {"stage_3_paper_intent_only", "stage_4_limited_automated_paper_trading"}:
        for observed, reason in (
            (evidence.sizing_observed, "sizing"),
            (evidence.stops_targets_observed, "stops_targets"),
            (evidence.cost_gate_observed, "cost_gate"),
            (evidence.reservation_behaviour_observed, "reservation_behaviour"),
            (evidence.outbox_idempotency_observed, "outbox_idempotency"),
            (evidence.outbox_expiry_observed, "outbox_expiry"),
        ):
            if not observed:
                blockers.append(f"regime.readiness.observation_missing:{reason}")

    if stage == "stage_4_limited_automated_paper_trading" and not flags.paper_submission_enabled:
        blockers.append("regime.readiness.paper_submission_flag_disabled")
    return blockers


def _stage_failure_status(blockers: list[str]) -> RegimeReadinessStatus:
    if any(
        "failed" in blocker
        or "_fail" in blocker
        or "live_trading" in blocker
        or "automatic_order_submission" in blocker
        or "broker_orders" in blocker
        or "critical_defect" in blocker
        for blocker in blockers
    ):
        return RegimeReadinessStatus.FAIL
    if any("not_run" in blocker for blocker in blockers):
        return RegimeReadinessStatus.NOT_RUN
    return RegimeReadinessStatus.INSUFFICIENT_EVIDENCE


def _readiness_group_passed(evidence: RegimePaperReadinessEvidence, group: str) -> bool:
    return group in evidence.passed_test_groups and (
        group in evidence.persisted_evidence_ids
        or f"regime.readiness.test:{group}" in evidence.persisted_evidence_ids
    )


def _paper_readiness_stages_through(stage: str) -> tuple[str, ...]:
    if stage not in REGIME_PAPER_READINESS_STAGES:
        raise ValueError(f"unknown Regime paper readiness stage: {stage}")
    selected: list[str] = []
    for candidate in REGIME_PAPER_READINESS_STAGES:
        selected.append(candidate)
        if candidate == stage:
            break
    return tuple(selected)


def _coerce_readiness_evidence(
    evidence: RegimePaperReadinessEvidence | RegimeRolloutEvidence | None,
    *,
    flags: RegimeRolloutFlags | None,
) -> RegimePaperReadinessEvidence:
    if evidence is None:
        return RegimePaperReadinessEvidence(flags=flags or RegimeRolloutFlags())
    if isinstance(evidence, RegimeRolloutEvidence):
        return RegimePaperReadinessEvidence(rollout_evidence=evidence, flags=flags or RegimeRolloutFlags())
    if flags is None:
        return evidence
    return RegimePaperReadinessEvidence(
        **{
            **evidence.__dict__,
            "flags": flags,
        }
    )


def record_valid_regime_rollout_state(
    store: RegimeRolloutStore,
    candidate_state: dict,
    *,
    recorded_at: datetime | None = None,
) -> dict:
    current = _read_optional(store, REGIME_ROLLOUT_STATE_KEY)
    if current and current.get("status") == "valid":
        store.write_snapshot(REGIME_ROLLBACK_STATE_KEY, current)
    state = {
        **candidate_state,
        "algorithm_id": "regime",
        "rollout_version": REGIME_ROLLOUT_VERSION,
        "status": "valid",
        "recorded_at": (recorded_at or datetime.now(timezone.utc)).isoformat(),
        "rollback_configuration": rollback_configuration(),
        "reason_codes": tuple(dict.fromkeys([*(candidate_state.get("reason_codes") or ()), "regime.rollout.valid_state_recorded"])),
    }
    store.write_snapshot(REGIME_ROLLOUT_STATE_KEY, state)
    return state


def rollback_regime_rollout(store: RegimeRolloutStore, *, rolled_back_at: datetime | None = None) -> dict:
    previous = _read_optional(store, REGIME_ROLLBACK_STATE_KEY)
    if not previous:
        previous = {
            "algorithm_id": "regime",
            "rollout_version": REGIME_ROLLOUT_VERSION,
            "status": "rollback_baseline",
            "rollback_configuration": rollback_configuration(),
            "reason_codes": ("regime.rollout.rollback_baseline_restored",),
        }
    restored = {
        **previous,
        "restored_at": (rolled_back_at or datetime.now(timezone.utc)).isoformat(),
        "rollback_configuration": rollback_configuration(),
        "historical_records_deleted": False,
        "reason_codes": tuple(dict.fromkeys([*(previous.get("reason_codes") or ()), "regime.rollout.rollback_restored_safe_state"])),
    }
    store.write_snapshot(REGIME_ROLLOUT_STATE_KEY, restored)
    return restored


def _stage_blockers(stage: RegimeRolloutStage, flags: RegimeRolloutFlags, evidence: RegimeRolloutEvidence) -> list[str]:
    blockers: list[str] = []
    if evidence.live_trading_enabled:
        blockers.append("regime.rollout.live_trading_never_allowed")
    if evidence.automatic_order_submission_enabled or flags.automatic_order_submission_enabled:
        blockers.append("regime.rollout.automatic_order_submission_not_permitted")
    if not flags.v2_enabled:
        blockers.append("regime.rollout.v2_flag_disabled")
    if flags.ml_mode != "shadow" or evidence.ml_mode != "shadow" or not evidence.ml_shadow_only:
        blockers.append("regime.rollout.ml_shadow_mode_required")
    if flags.short_entries_enabled:
        blockers.append("regime.rollout.short_entries_disabled_initially")
    if not flags.global_risk_manager_enabled:
        blockers.append("regime.rollout.global_risk_manager_required")
    if evidence.paper_submission_attempted_before_stage_d:
        blockers.append("regime.rollout.paper_submission_attempted_before_stage_d")
    if evidence.broker_orders_created_in_shadow:
        blockers.append("regime.rollout.shadow_created_broker_orders")
    if evidence.broker_orders_created_in_intent_validation:
        blockers.append("regime.rollout.intent_validation_created_broker_orders")

    for requirement in _requirements_through(stage):
        if not _requirement_passed(evidence, requirement):
            blockers.append(f"regime.rollout.evidence_missing:{requirement}")

    if stage in {"stage_d_limited_spy_paper_submission", "stage_e_expanded_paper_validation"} and not flags.paper_submission_enabled:
        blockers.append("regime.rollout.paper_submission_flag_disabled")
    return list(dict.fromkeys(blockers))


def _stage_requirement_status(stage: RegimeRolloutStage, evidence: RegimeRolloutEvidence) -> dict[str, str]:
    return {
        requirement: "PASS" if _requirement_passed(evidence, requirement) else "INSUFFICIENT_EVIDENCE"
        for requirement in _requirements_through(stage)
    }


def _requirements_through(stage: RegimeRolloutStage) -> tuple[str, ...]:
    selected: list[str] = []
    for candidate in REGIME_ROLLOUT_STAGES:
        selected.extend(STAGE_REQUIREMENTS[candidate])
        if candidate == stage:
            break
    return tuple(selected)


def _requirement_passed(evidence: RegimeRolloutEvidence, requirement: str) -> bool:
    return bool(getattr(evidence, requirement)) and (
        requirement in evidence.persisted_evidence_ids
        or f"regime.rollout.evidence:{requirement}" in evidence.persisted_evidence_ids
    )


def _canonical_stage(stage: str) -> RegimeRolloutStage:
    canonical = LEGACY_STAGE_ALIASES.get(stage, stage)
    if canonical not in REGIME_ROLLOUT_STAGES:
        raise ValueError(f"unknown Regime rollout stage: {stage}")
    return canonical  # type: ignore[return-value]


def _coerce_evidence(validation: RegimeRolloutValidation | RegimeRolloutEvidence | None) -> RegimeRolloutEvidence:
    if validation is None:
        return RegimeRolloutEvidence()
    if isinstance(validation, RegimeRolloutEvidence):
        return validation
    if validation.rollout_evidence is not None:
        return validation.rollout_evidence
    return _legacy_validation_to_evidence(validation)


def _legacy_validation_to_evidence(validation: RegimeRolloutValidation) -> RegimeRolloutEvidence:
    payload = {
        "focused_tests_passed": validation.tests_passed,
        "full_backend_tests_passed": validation.tests_passed,
        "realistic_backtesting_passed": validation.dedicated_backtest_passed,
        "walk_forward_passed": validation.dedicated_backtest_passed,
        "holdout_passed": validation.untouched_oos_passed,
        "strategy_regime_occupancy_reasonable": validation.historical_characterization_passed,
        "transaction_costs_included": validation.dedicated_backtest_passed,
        "completed_bar_reliability_passed": validation.paper_shadow_decisions_passed,
        "decision_latency_passed": validation.paper_shadow_decisions_passed,
        "stable_hysteresis_passed": validation.paper_shadow_decisions_passed,
        "shadow_regime_occupancy_reasonable": validation.paper_shadow_decisions_passed,
        "strategy_opportunity_frequency_reasonable": validation.paper_shadow_decisions_passed,
        "blocker_frequency_reasonable": validation.paper_shadow_decisions_passed,
        "restart_recovery_passed": validation.paper_shadow_decisions_passed,
        "duplicate_prevention_passed": validation.old_new_decision_comparison_passed,
        "paper_backtest_replay_parity_passed": validation.old_new_decision_comparison_passed,
        "quantity_validated": validation.limited_paper_orders_approved,
        "stops_validated": validation.limited_paper_orders_approved,
        "targets_validated": validation.limited_paper_orders_approved,
        "transaction_cost_gate_validated": validation.limited_paper_orders_approved,
        "global_risk_reservation_validated": validation.global_gate_monitoring_passed,
        "outbox_state_validated": validation.limited_paper_orders_approved,
        "idempotency_validated": validation.old_new_decision_comparison_passed,
        "spy_only_validated": validation.limited_paper_orders_approved,
        "single_instance_validated": validation.limited_paper_orders_approved,
        "long_only_validated": not validation.live_trading_enabled,
        "low_quantity_cap_validated": validation.limited_paper_orders_approved,
        "no_pyramiding_validated": validation.limited_paper_orders_approved,
        "limited_trades_per_day_validated": validation.limited_paper_orders_approved,
        "strict_daily_loss_validated": validation.limited_paper_orders_approved,
        "end_of_day_flatten_validated": validation.limited_paper_orders_approved,
        "fill_quality_passed": validation.performance_review_passed,
        "reconciliation_passed": validation.global_gate_monitoring_passed,
        "expanded_restart_recovery_passed": validation.global_gate_monitoring_passed,
        "slippage_passed": validation.performance_review_passed,
        "daily_loss_protection_passed": validation.global_gate_monitoring_passed,
        "position_isolation_passed": validation.global_gate_monitoring_passed,
        "no_duplicate_orders_passed": validation.old_new_decision_comparison_passed,
        "live_trading_enabled": validation.live_trading_enabled,
    }
    payload["persisted_evidence_ids"] = frozenset(
        key for key, value in payload.items() if isinstance(value, bool) and value and key in _all_stage_requirements()
    )
    return RegimeRolloutEvidence(**payload)


def _all_stage_requirements() -> frozenset[str]:
    return frozenset(requirement for requirements in STAGE_REQUIREMENTS.values() for requirement in requirements)


def _env_bool(source: Mapping[str, str], key: str, default: bool) -> bool:
    raw = source.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_ml_mode(raw: str | None, default: RegimeMlMode) -> RegimeMlMode:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"off", "shadow", "confirm_only", "active"}:
        return value  # type: ignore[return-value]
    return default


def _read_optional(store: RegimeRolloutStore, key: str) -> dict | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


__all__ = [
    "REGIME_AUTOMATIC_ORDER_SUBMISSION_ENABLED",
    "REGIME_DYNAMIC_PROFILE_ENABLED",
    "REGIME_GLOBAL_RISK_MANAGER_ENABLED",
    "REGIME_ML_MODE",
    "REGIME_PAPER_READINESS_STAGE_LABELS",
    "REGIME_PAPER_READINESS_STAGE_ROLLOUT_REQUIREMENTS",
    "REGIME_PAPER_READINESS_STAGE_TEST_GROUPS",
    "REGIME_PAPER_READINESS_STAGES",
    "REGIME_PAPER_READINESS_TEST_GROUPS",
    "REGIME_PAPER_READINESS_TEST_PATHS",
    "REGIME_PAPER_READINESS_VERSION",
    "REGIME_PAPER_SUBMISSION_ENABLED",
    "REGIME_ROLLBACK_STATE_KEY",
    "REGIME_ROLLOUT_PHASES",
    "REGIME_ROLLOUT_STAGES",
    "REGIME_ROLLOUT_STATE_KEY",
    "REGIME_ROLLOUT_VERSION",
    "REGIME_SHORT_ENTRIES_ENABLED",
    "REGIME_V2_ENABLED",
    "REQUIRED_ML_PROMOTION_EVIDENCE",
    "STAGE_REQUIREMENTS",
    "RegimePaperReadinessEvidence",
    "RegimeReadinessStatus",
    "RegimeRolloutEvidence",
    "RegimeRolloutFlags",
    "RegimeRolloutPermission",
    "RegimeRolloutPhaseStatus",
    "RegimeRolloutStage",
    "RegimeRolloutStageStatus",
    "RegimeRolloutValidation",
    "build_regime_paper_readiness_report",
    "evaluate_regime_rollout_phase",
    "evaluate_regime_rollout_stage",
    "limited_paper_orders_allowed",
    "paper_submission_allowed",
    "record_valid_regime_rollout_state",
    "regime_rollout_feature_flags",
    "regime_rollout_status",
    "rollback_configuration",
    "rollback_regime_rollout",
]
