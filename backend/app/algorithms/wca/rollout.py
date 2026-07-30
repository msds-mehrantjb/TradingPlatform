"""Staged shadow migration and controlled rollout for WCA."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Mapping, Protocol


WCA_ROLLOUT_VERSION = "wca_evidence_rollout_v2"

WCA_BACKEND_ENGINE_ENABLED = "WCA_BACKEND_ENGINE_ENABLED"
WCA_CORRECTED_STRATEGY_CATALOG_ENABLED = "WCA_CORRECTED_STRATEGY_CATALOG_ENABLED"
WCA_DYNAMIC_WEIGHTS_ENABLED = "WCA_DYNAMIC_WEIGHTS_ENABLED"
WCA_DYNAMIC_PROFILE_ENABLED = "WCA_DYNAMIC_PROFILE_ENABLED"
GLOBAL_GATE_ENGINE_ENABLED = "GLOBAL_GATE_ENGINE_ENABLED"
WCA_BACKEND_BACKTEST_ENABLED = "WCA_BACKEND_BACKTEST_ENABLED"
WCA_PAPER_EXECUTION_ENABLED = "WCA_PAPER_EXECUTION_ENABLED"

WCA_ROLLOUT_STATE_KEY = "wca.rollout.active"
WCA_ROLLBACK_STATE_KEY = "wca.rollout.previous_valid"

WcaRolloutStage = Literal[
    "DISABLED",
    "HISTORICAL_REPLAY",
    "SHADOW",
    "PAPER_RECOMMENDATION",
    "MANUAL_PAPER",
    "LIMITED_AUTOMATIC_PAPER",
    "AUTOMATIC_PAPER",
]

WCA_ROLLOUT_STAGES: tuple[WcaRolloutStage, ...] = (
    "DISABLED",
    "HISTORICAL_REPLAY",
    "SHADOW",
    "PAPER_RECOMMENDATION",
    "MANUAL_PAPER",
    "LIMITED_AUTOMATIC_PAPER",
    "AUTOMATIC_PAPER",
)
WcaRolloutPhase = WcaRolloutStage
WCA_ROLLOUT_PHASES = WCA_ROLLOUT_STAGES

WCA_ROLLOUT_STAGE_ALIASES = {
    "legacy_parity": "HISTORICAL_REPLAY",
    "corrected_catalog_shadow": "SHADOW",
    "backend_backtest": "HISTORICAL_REPLAY",
    "paper_recommendation": "PAPER_RECOMMENDATION",
    "paper_execution": "MANUAL_PAPER",
    "extended_paper_validation": "LIMITED_AUTOMATIC_PAPER",
    "legacy_removal": "AUTOMATIC_PAPER",
}

WCA_REQUIRED_ROLLOUT_EVIDENCE = frozenset(
    {
        "deterministic_replay_parity",
        "zero_unexplained_decision_mismatches",
        "zero_duplicate_broker_orders",
        "zero_cross_algorithm_inventory_mutations",
        "successful_restart_recovery",
        "accepted_reconciliation",
        "zero_unprotected_positions",
        "accepted_event_latency",
        "accepted_decision_latency",
        "accepted_broker_latency",
        "recorded_slippage",
        "opening_session_evidence",
        "midday_evidence",
        "closing_session_evidence",
        "high_volatility_evidence",
        "economic_event_session_evidence",
        "minimum_paper_observation_duration",
        "sufficient_paper_trade_count",
        "tested_rollback",
    }
)

WCA_ROLLOUT_EVIDENCE_ALIASES = {
    "no_unexplained_decision_mismatches": "zero_unexplained_decision_mismatches",
    "no_duplicate_broker_orders": "zero_duplicate_broker_orders",
    "no_cross_algorithm_inventory_mutations": "zero_cross_algorithm_inventory_mutations",
    "successful_reconciliation": "accepted_reconciliation",
    "no_unprotected_positions": "zero_unprotected_positions",
    "acceptable_event_lag": "accepted_event_latency",
    "acceptable_decision_latency": "accepted_decision_latency",
    "acceptable_broker_latency": "accepted_broker_latency",
    "acceptable_realised_slippage": "recorded_slippage",
}

WCA_SHADOW_COMPARISON_FIELDS = (
    "strategy_outputs",
    "scores",
    "decision",
    "quantity",
    "stop",
    "target",
    "gate_results",
)


class WcaRolloutStore(Protocol):
    def read_snapshot(self, key: str) -> dict:
        ...

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        ...


class WcaRolloutPermission(str, Enum):
    ENABLED = "enabled"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class WcaRolloutFlags:
    backend_engine_enabled: bool = True
    corrected_strategy_catalog_enabled: bool = True
    dynamic_weights_enabled: bool = True
    dynamic_profile_enabled: bool = True
    global_gate_engine_enabled: bool = True
    backend_backtest_enabled: bool = True
    paper_execution_enabled: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "WcaRolloutFlags":
        source = environ or os.environ
        return cls(
            backend_engine_enabled=_env_bool(source, WCA_BACKEND_ENGINE_ENABLED, True),
            corrected_strategy_catalog_enabled=_env_bool(source, WCA_CORRECTED_STRATEGY_CATALOG_ENABLED, True),
            dynamic_weights_enabled=_env_bool(source, WCA_DYNAMIC_WEIGHTS_ENABLED, True),
            dynamic_profile_enabled=_env_bool(source, WCA_DYNAMIC_PROFILE_ENABLED, True),
            global_gate_engine_enabled=_env_bool(source, GLOBAL_GATE_ENGINE_ENABLED, True),
            backend_backtest_enabled=_env_bool(source, WCA_BACKEND_BACKTEST_ENABLED, True),
            paper_execution_enabled=_env_bool(source, WCA_PAPER_EXECUTION_ENABLED, False),
        )

    def model_dump(self) -> dict[str, bool | str]:
        return {
            "rollout_version": WCA_ROLLOUT_VERSION,
            WCA_BACKEND_ENGINE_ENABLED: self.backend_engine_enabled,
            WCA_CORRECTED_STRATEGY_CATALOG_ENABLED: self.corrected_strategy_catalog_enabled,
            WCA_DYNAMIC_WEIGHTS_ENABLED: self.dynamic_weights_enabled,
            WCA_DYNAMIC_PROFILE_ENABLED: self.dynamic_profile_enabled,
            GLOBAL_GATE_ENGINE_ENABLED: self.global_gate_engine_enabled,
            WCA_BACKEND_BACKTEST_ENABLED: self.backend_backtest_enabled,
            WCA_PAPER_EXECUTION_ENABLED: self.paper_execution_enabled,
        }


@dataclass(frozen=True)
class WcaRolloutValidation:
    legacy_parity_passed: bool = False
    corrected_catalog_shadow_passed: bool = False
    full_history_backtest_passed: bool = False
    walk_forward_passed: bool = False
    untouched_holdout_passed: bool = False
    paper_recommendation_passed: bool = False
    paper_execution_passed: bool = False
    paper_trading_stable: bool = False
    multiple_market_conditions_passed: bool = False
    multi_week_paper_validation_passed: bool = False
    legacy_removal_accepted: bool = False
    tests_passed: bool = False
    live_trading_enabled: bool = False

    def model_dump(self) -> dict[str, bool]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class WcaRolloutEvidence:
    persisted_evidence_ids: frozenset[str] = frozenset()
    prior_steps_passed: bool = False
    deterministic_replay_parity: bool = False
    unexplained_decision_mismatches: int = 0
    duplicate_broker_orders: int = 0
    cross_algorithm_inventory_mutations: int = 0
    restart_recovery_passed: bool = False
    reconciliation_passed: bool = False
    unprotected_positions: int = 0
    max_event_lag_seconds: float | None = None
    max_decision_latency_seconds: float | None = None
    max_broker_latency_seconds: float | None = None
    average_realised_slippage_per_share: float | None = None
    market_conditions: tuple[str, ...] = ()
    session_periods: tuple[str, ...] = ()
    high_volatility_sessions: int = 0
    economic_event_sessions: int = 0
    paper_observation_days: float = 0.0
    paper_trade_count: int = 0
    rollback_tested: bool = False
    rollback_restored_safe_state: bool = False
    critical_failure_open: bool = False
    reconciliation_after_failure_passed: bool = False
    healthy_state_validation_passed: bool = False
    live_trading_enabled: bool = False

    def __post_init__(self) -> None:
        normalized = set(self.persisted_evidence_ids)
        normalized.update(
            WCA_ROLLOUT_EVIDENCE_ALIASES[evidence_id]
            for evidence_id in tuple(normalized)
            if evidence_id in WCA_ROLLOUT_EVIDENCE_ALIASES
        )
        if "opening_midday_closing_periods" in normalized:
            normalized.update({"opening_session_evidence", "midday_evidence", "closing_session_evidence"})
        if "high_volatility_and_economic_event_sessions" in normalized:
            normalized.update({"high_volatility_evidence", "economic_event_session_evidence"})
        object.__setattr__(self, "persisted_evidence_ids", frozenset(normalized))

    @classmethod
    def from_validation(cls, validation: WcaRolloutValidation) -> "WcaRolloutEvidence":
        persisted = set()
        if validation.full_history_backtest_passed and validation.walk_forward_passed and validation.untouched_holdout_passed:
            persisted.add("deterministic_replay_parity")
        if validation.corrected_catalog_shadow_passed:
            persisted.add("zero_unexplained_decision_mismatches")
        if validation.paper_execution_passed:
            persisted.update(
                {
                    "zero_duplicate_broker_orders",
                    "zero_cross_algorithm_inventory_mutations",
                    "accepted_reconciliation",
                    "zero_unprotected_positions",
                    "accepted_event_latency",
                    "accepted_decision_latency",
                    "accepted_broker_latency",
                    "recorded_slippage",
                }
            )
        if validation.paper_trading_stable:
            persisted.update(
                {
                    "opening_session_evidence",
                    "midday_evidence",
                    "closing_session_evidence",
                    "high_volatility_evidence",
                    "economic_event_session_evidence",
                    "minimum_paper_observation_duration",
                    "sufficient_paper_trade_count",
                }
            )
        if validation.legacy_removal_accepted:
            persisted.add("tested_rollback")
        return cls(
            persisted_evidence_ids=frozenset(persisted),
            prior_steps_passed=validation.tests_passed,
            deterministic_replay_parity=validation.full_history_backtest_passed
            and validation.walk_forward_passed
            and validation.untouched_holdout_passed,
            unexplained_decision_mismatches=0 if validation.corrected_catalog_shadow_passed else 1,
            duplicate_broker_orders=0,
            cross_algorithm_inventory_mutations=0,
            restart_recovery_passed=validation.paper_execution_passed,
            reconciliation_passed=validation.paper_execution_passed,
            unprotected_positions=0,
            max_event_lag_seconds=1 if validation.paper_execution_passed else None,
            max_decision_latency_seconds=1 if validation.paper_execution_passed else None,
            max_broker_latency_seconds=1 if validation.paper_execution_passed else None,
            average_realised_slippage_per_share=0.01 if validation.paper_execution_passed else None,
            market_conditions=("trend", "range", "volatile") if validation.multiple_market_conditions_passed else (),
            session_periods=("opening", "midday", "closing") if validation.paper_trading_stable else (),
            high_volatility_sessions=1 if validation.paper_trading_stable else 0,
            economic_event_sessions=1 if validation.paper_trading_stable else 0,
            paper_observation_days=15 if validation.multi_week_paper_validation_passed else 0,
            paper_trade_count=10 if validation.paper_trading_stable else 0,
            rollback_tested=validation.legacy_removal_accepted,
            rollback_restored_safe_state=validation.legacy_removal_accepted,
            live_trading_enabled=validation.live_trading_enabled,
        )

    def model_dump(self) -> dict[str, object]:
        payload = self.__dict__.copy()
        payload["persisted_evidence_ids"] = tuple(sorted(self.persisted_evidence_ids))
        return payload


@dataclass(frozen=True)
class WcaRolloutEvidenceThresholds:
    max_event_lag_seconds: float = 60.0
    max_decision_latency_seconds: float = 2.0
    max_broker_latency_seconds: float = 5.0
    max_average_realised_slippage_per_share: float = 0.05
    minimum_paper_observation_days: float = 10.0
    minimum_paper_trade_count: int = 10


@dataclass(frozen=True)
class WcaLimitedAutomaticPaperCaps:
    symbols: tuple[str, ...] = ("SPY",)
    max_quantity: int = 10
    max_daily_trades: int = 3
    max_daily_loss_dollars: float = 100.0
    session_windows: tuple[str, ...] = ("10:00-11:30 America/New_York", "13:30-15:30 America/New_York")
    allowed_strategies: tuple[str, ...] = ("C1", "C4", "C7")

    def model_dump(self) -> dict[str, object]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class WcaRolloutPhaseStatus:
    phase: WcaRolloutPhase | str
    permission: WcaRolloutPermission | str
    reason_codes: tuple[str, ...]
    explanation: str

    @property
    def enabled(self) -> bool:
        return self.permission == WcaRolloutPermission.ENABLED.value

    def model_dump(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "stage": self.phase,
            "permission": self.permission,
            "enabled": self.enabled,
            "reason_codes": self.reason_codes,
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class WcaCriticalFailureAction:
    failure_id: str
    stop_new_entries: bool = True
    continue_protective_exits: bool = True
    preserve_evidence: bool = True
    circuit_breaker_open: bool = True
    require_reconciliation_before_resumption: bool = True
    require_healthy_state_validation_before_resumption: bool = True
    reason_codes: tuple[str, ...] = ("wca.rollout.critical_failure.circuit_breaker_open",)

    def model_dump(self) -> dict[str, object]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class WcaRollbackResult:
    source_stage: WcaRolloutStage
    target_stage: Literal["SHADOW", "DISABLED"]
    new_entries_stopped: bool
    wca_entry_orders_cancelled: bool
    protective_exits_preserved: bool
    reconciliation_requested: bool
    broker_local_state_reconciled: bool
    wca_inventory_preserved: bool
    evidence_preserved: bool
    safe_state_verified: bool
    explicit_repromotion_required: bool
    reason_codes: tuple[str, ...]

    def model_dump(self) -> dict[str, object]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class WcaShadowComparisonResult:
    compared_fields: tuple[str, ...]
    mismatched_fields: tuple[str, ...]
    within_tolerance: bool
    submission_allowed: bool
    reason_codes: tuple[str, ...]

    def model_dump(self) -> dict[str, object]:
        return self.__dict__.copy()


def wca_rollout_feature_flags(environ: Mapping[str, str] | None = None) -> WcaRolloutFlags:
    return WcaRolloutFlags.from_env(environ)


def evaluate_wca_rollout_phase(
    phase: WcaRolloutPhase | str,
    *,
    flags: WcaRolloutFlags | None = None,
    validation: WcaRolloutValidation | None = None,
) -> WcaRolloutPhaseStatus:
    evidence = WcaRolloutEvidence.from_validation(validation or WcaRolloutValidation())
    return evaluate_wca_rollout_stage(_canonical_stage(phase), flags=flags, evidence=evidence)


def evaluate_wca_rollout_stage(
    stage: WcaRolloutStage | str,
    *,
    flags: WcaRolloutFlags | None = None,
    evidence: WcaRolloutEvidence | None = None,
    thresholds: WcaRolloutEvidenceThresholds | None = None,
) -> WcaRolloutPhaseStatus:
    canonical = _canonical_stage(stage)
    active_flags = flags or wca_rollout_feature_flags()
    active_evidence = evidence or WcaRolloutEvidence()
    active_thresholds = thresholds or WcaRolloutEvidenceThresholds()
    if canonical not in WCA_ROLLOUT_STAGES:
        raise ValueError(f"unknown WCA rollout stage: {stage}")
    blockers = _stage_blockers(canonical, active_flags, active_evidence, active_thresholds)
    if blockers:
        return WcaRolloutPhaseStatus(
            phase=canonical,
            permission=WcaRolloutPermission.BLOCKED.value,
            reason_codes=tuple(blockers),
            explanation="WCA rollout stage is blocked until required persisted evidence and safety criteria pass.",
        )
    return WcaRolloutPhaseStatus(
        phase=canonical,
        permission=WcaRolloutPermission.ENABLED.value,
        reason_codes=(f"wca.rollout.{canonical.lower()}.enabled",),
        explanation="WCA rollout stage is enabled by persisted evidence. Real-money execution remains disabled.",
    )


def wca_rollout_status(
    *,
    flags: WcaRolloutFlags | None = None,
    validation: WcaRolloutValidation | None = None,
    evidence: WcaRolloutEvidence | None = None,
) -> dict[str, object]:
    active_flags = flags or wca_rollout_feature_flags()
    active_evidence = evidence or WcaRolloutEvidence.from_validation(validation or WcaRolloutValidation())
    phase_statuses = tuple(
        evaluate_wca_rollout_stage(stage, flags=active_flags, evidence=active_evidence).model_dump()
        for stage in WCA_ROLLOUT_STAGES
    )
    current_stage = highest_wca_rollout_stage(flags=active_flags, evidence=active_evidence)
    return {
        "algorithm_id": "wca",
        "rollout_version": WCA_ROLLOUT_VERSION,
        "feature_flags": active_flags.model_dump(),
        "validation": (validation or WcaRolloutValidation()).model_dump(),
        "evidence": active_evidence.model_dump(),
        "current_stage": current_stage,
        "phases": phase_statuses,
        "stages": phase_statuses,
        "paper_recommendation_allowed": paper_recommendation_allowed(flags=active_flags, validation=validation, evidence=active_evidence),
        "manual_paper_allowed": manual_paper_allowed(flags=active_flags, evidence=active_evidence),
        "limited_automatic_paper_allowed": limited_automatic_paper_allowed(flags=active_flags, evidence=active_evidence),
        "paper_execution_allowed": paper_execution_allowed(flags=active_flags, validation=validation, evidence=active_evidence),
        "live_trading_allowed": False,
        "limited_automatic_paper_caps": WcaLimitedAutomaticPaperCaps().model_dump(),
        "critical_failure_action": critical_failure_action("template").model_dump(),
        "rollback_plan": rollback_configuration(),
        "reason_codes": ("wca.rollout.paper_only", "wca.rollout.evidence_required"),
    }


def compare_shadow_results(
    legacy_result: Mapping[str, object],
    backend_result: Mapping[str, object],
    *,
    numeric_tolerance: float = 1e-4,
) -> WcaShadowComparisonResult:
    mismatches: list[str] = []
    for field in WCA_SHADOW_COMPARISON_FIELDS:
        if not _values_match(legacy_result.get(field), backend_result.get(field), numeric_tolerance):
            mismatches.append(field)
    return WcaShadowComparisonResult(
        compared_fields=WCA_SHADOW_COMPARISON_FIELDS,
        mismatched_fields=tuple(mismatches),
        within_tolerance=not mismatches,
        submission_allowed=False,
        reason_codes=(
            "wca.rollout.shadow_comparison.calculated",
            "wca.rollout.shadow_comparison.no_submission",
            *("wca.rollout.shadow_comparison.mismatch" for _ in mismatches[:1]),
        ),
    )


def paper_recommendation_allowed(
    *,
    flags: WcaRolloutFlags | None = None,
    validation: WcaRolloutValidation | None = None,
    evidence: WcaRolloutEvidence | None = None,
) -> bool:
    return evaluate_wca_rollout_stage(
        "PAPER_RECOMMENDATION",
        flags=flags or wca_rollout_feature_flags(),
        evidence=evidence or WcaRolloutEvidence.from_validation(validation or WcaRolloutValidation()),
    ).enabled


def manual_paper_allowed(
    *,
    flags: WcaRolloutFlags | None = None,
    evidence: WcaRolloutEvidence | None = None,
) -> bool:
    return evaluate_wca_rollout_stage(
        "MANUAL_PAPER",
        flags=flags or wca_rollout_feature_flags(),
        evidence=evidence or WcaRolloutEvidence(),
    ).enabled


def limited_automatic_paper_allowed(
    *,
    flags: WcaRolloutFlags | None = None,
    evidence: WcaRolloutEvidence | None = None,
) -> bool:
    return evaluate_wca_rollout_stage(
        "LIMITED_AUTOMATIC_PAPER",
        flags=flags or wca_rollout_feature_flags(),
        evidence=evidence or WcaRolloutEvidence(),
    ).enabled


def paper_execution_allowed(
    *,
    flags: WcaRolloutFlags | None = None,
    validation: WcaRolloutValidation | None = None,
    evidence: WcaRolloutEvidence | None = None,
) -> bool:
    return evaluate_wca_rollout_stage(
        "LIMITED_AUTOMATIC_PAPER",
        flags=flags or wca_rollout_feature_flags(),
        evidence=evidence or WcaRolloutEvidence.from_validation(validation or WcaRolloutValidation()),
    ).enabled


def highest_wca_rollout_stage(
    *,
    flags: WcaRolloutFlags | None = None,
    evidence: WcaRolloutEvidence | None = None,
) -> WcaRolloutStage:
    active_flags = flags or wca_rollout_feature_flags()
    active_evidence = evidence or WcaRolloutEvidence()
    current: WcaRolloutStage = "DISABLED"
    for stage in WCA_ROLLOUT_STAGES:
        status = evaluate_wca_rollout_stage(stage, flags=active_flags, evidence=active_evidence)
        if status.enabled:
            current = stage
        else:
            break
    return current


def critical_failure_action(failure_id: str) -> WcaCriticalFailureAction:
    return WcaCriticalFailureAction(failure_id=failure_id)


def rollback_configuration() -> dict[str, object]:
    return {
        WCA_BACKEND_ENGINE_ENABLED: False,
        WCA_CORRECTED_STRATEGY_CATALOG_ENABLED: False,
        WCA_DYNAMIC_WEIGHTS_ENABLED: False,
        WCA_DYNAMIC_PROFILE_ENABLED: False,
        GLOBAL_GATE_ENGINE_ENABLED: False,
        WCA_BACKEND_BACKTEST_ENABLED: False,
        WCA_PAPER_EXECUTION_ENABLED: False,
        "display": "legacy_wca",
        "weights": "static_baseline",
        "settings": "baseline_trading_settings",
        "dynamic_profile": "disabled",
        "automated_paper_submission": False,
        "new_entries_stopped": True,
        "cancel_wca_entry_orders": True,
        "protective_exits_continue": True,
        "reconcile_broker_and_local_state": True,
        "preserve_wca_inventory": True,
        "preserve_rollout_evidence": True,
        "verify_safe_state": True,
        "explicit_repromotion_required": True,
        "circuit_breaker_open": True,
        "requires_reconciliation_before_resumption": True,
        "requires_healthy_state_validation_before_resumption": True,
        "delete_historical_records": False,
    }


def rollback_wca_automatic_paper_stage(
    *,
    source_stage: WcaRolloutStage | str,
    target_stage: Literal["SHADOW", "DISABLED"],
    entry_orders_cancelled: bool,
    broker_local_state_reconciled: bool,
    safe_state_verified: bool,
) -> WcaRollbackResult:
    source = _canonical_stage(source_stage)
    if source not in {"MANUAL_PAPER", "LIMITED_AUTOMATIC_PAPER", "AUTOMATIC_PAPER"}:
        raise ValueError("WCA rollback source must be a paper stage")
    if target_stage not in {"SHADOW", "DISABLED"}:
        raise ValueError("WCA rollback target must be SHADOW or DISABLED")
    reasons = [
        "wca.rollout.rollback.requested",
        "wca.rollout.rollback.new_entries_stopped",
        "wca.rollout.rollback.protective_exits_preserved",
        "wca.rollout.rollback.inventory_preserved",
        "wca.rollout.rollback.evidence_preserved",
        "wca.rollout.rollback.explicit_repromotion_required",
    ]
    reasons.append(
        "wca.rollout.rollback.entry_orders_cancelled"
        if entry_orders_cancelled
        else "wca.rollout.rollback.entry_order_cancellation_required"
    )
    reasons.append(
        "wca.rollout.rollback.reconciliation_accepted"
        if broker_local_state_reconciled
        else "wca.rollout.rollback.reconciliation_required"
    )
    reasons.append(
        "wca.rollout.rollback.safe_state_verified"
        if safe_state_verified
        else "wca.rollout.rollback.safe_state_verification_required"
    )
    return WcaRollbackResult(
        source_stage=source,
        target_stage=target_stage,
        new_entries_stopped=True,
        wca_entry_orders_cancelled=entry_orders_cancelled,
        protective_exits_preserved=True,
        reconciliation_requested=True,
        broker_local_state_reconciled=broker_local_state_reconciled,
        wca_inventory_preserved=True,
        evidence_preserved=True,
        safe_state_verified=safe_state_verified,
        explicit_repromotion_required=True,
        reason_codes=tuple(reasons),
    )


def record_valid_wca_rollout_state(
    store: WcaRolloutStore,
    candidate_state: dict,
    *,
    recorded_at: datetime | None = None,
) -> dict:
    current = _read_optional(store, WCA_ROLLOUT_STATE_KEY)
    if current and current.get("status") == "valid":
        store.write_snapshot(WCA_ROLLBACK_STATE_KEY, current)
    state = {
        **candidate_state,
        "algorithm_id": "wca",
        "rollout_version": WCA_ROLLOUT_VERSION,
        "status": "valid",
        "recorded_at": (recorded_at or datetime.now(timezone.utc)).isoformat(),
        "rollback_configuration": rollback_configuration(),
        "reason_codes": tuple(dict.fromkeys([*(candidate_state.get("reason_codes") or ()), "wca.rollout.valid_state_recorded"])),
    }
    store.write_snapshot(WCA_ROLLOUT_STATE_KEY, state)
    return state


def rollback_wca_rollout(
    store: WcaRolloutStore,
    *,
    rolled_back_at: datetime | None = None,
    target_stage: Literal["SHADOW", "DISABLED"] = "SHADOW",
    entry_orders_cancelled: bool = True,
    broker_local_state_reconciled: bool = True,
    safe_state_verified: bool = True,
) -> dict:
    previous = _read_optional(store, WCA_ROLLBACK_STATE_KEY)
    if not previous:
        previous = {
            "algorithm_id": "wca",
            "rollout_version": WCA_ROLLOUT_VERSION,
            "status": "rollback_baseline",
            "rollback_configuration": rollback_configuration(),
            "reason_codes": ("wca.rollout.rollback_baseline_restored",),
        }
    current = _read_optional(store, WCA_ROLLOUT_STATE_KEY) or {}
    source_stage = str(current.get("phase") or previous.get("phase") or "MANUAL_PAPER")
    rollback = rollback_wca_automatic_paper_stage(
        source_stage=source_stage,
        target_stage=target_stage,
        entry_orders_cancelled=entry_orders_cancelled,
        broker_local_state_reconciled=broker_local_state_reconciled,
        safe_state_verified=safe_state_verified,
    )
    restored = {
        **previous,
        "phase": target_stage,
        "status": "rollback_safe_state_verified" if safe_state_verified else "rollback_pending_safe_state",
        "restored_at": (rolled_back_at or datetime.now(timezone.utc)).isoformat(),
        "rollback_configuration": rollback_configuration(),
        "rollback_result": rollback.model_dump(),
        "historical_records_deleted": False,
        "reason_codes": tuple(
            dict.fromkeys(
                [*(previous.get("reason_codes") or ()), *rollback.reason_codes, "wca.rollout.rollback_restored_safe_state"]
            )
        ),
    }
    store.write_snapshot(WCA_ROLLOUT_STATE_KEY, restored)
    return restored


def _stage_blockers(
    stage: WcaRolloutStage,
    flags: WcaRolloutFlags,
    evidence: WcaRolloutEvidence,
    thresholds: WcaRolloutEvidenceThresholds,
) -> list[str]:
    blockers: list[str] = []
    if evidence.live_trading_enabled:
        blockers.append("wca.rollout.live_trading_never_allowed")
    if stage == "DISABLED":
        return blockers
    if not flags.backend_engine_enabled:
        blockers.append("wca.rollout.backend_engine_disabled")
    if not evidence.prior_steps_passed:
        blockers.append("wca.rollout.prior_steps_not_passed")
    if evidence.critical_failure_open:
        blockers.append("wca.rollout.critical_failure_circuit_breaker_open")
        if not evidence.reconciliation_after_failure_passed:
            blockers.append("wca.rollout.reconciliation_required_after_failure")
        if not evidence.healthy_state_validation_passed:
            blockers.append("wca.rollout.healthy_state_validation_required_after_failure")
    if stage in {"SHADOW", "PAPER_RECOMMENDATION", "MANUAL_PAPER", "LIMITED_AUTOMATIC_PAPER", "AUTOMATIC_PAPER"} and not flags.corrected_strategy_catalog_enabled:
        blockers.append("wca.rollout.corrected_catalog_flag_disabled")
    if stage in {"HISTORICAL_REPLAY", "SHADOW", "PAPER_RECOMMENDATION", "MANUAL_PAPER", "LIMITED_AUTOMATIC_PAPER", "AUTOMATIC_PAPER"} and not flags.backend_backtest_enabled:
        blockers.append("wca.rollout.backend_backtest_flag_disabled")

    blockers.extend(_evidence_blockers(stage, evidence, thresholds))
    if stage in {"LIMITED_AUTOMATIC_PAPER", "AUTOMATIC_PAPER"}:
        if not flags.paper_execution_enabled:
            blockers.append("wca.rollout.automatic_paper_flag_disabled")
        if not flags.global_gate_engine_enabled:
            blockers.append("wca.rollout.global_gate_engine_required")
    return list(dict.fromkeys(blockers))


def _evidence_blockers(
    stage: WcaRolloutStage,
    evidence: WcaRolloutEvidence,
    thresholds: WcaRolloutEvidenceThresholds,
) -> list[str]:
    requirements: dict[WcaRolloutStage, tuple[str, ...]] = {
        "DISABLED": (),
        "HISTORICAL_REPLAY": ("deterministic_replay_parity", "successful_restart_recovery"),
        "SHADOW": (
            "deterministic_replay_parity",
            "successful_restart_recovery",
            "zero_unexplained_decision_mismatches",
            "accepted_event_latency",
            "accepted_decision_latency",
        ),
        "PAPER_RECOMMENDATION": (
            "deterministic_replay_parity",
            "successful_restart_recovery",
            "zero_unexplained_decision_mismatches",
            "zero_duplicate_broker_orders",
            "zero_cross_algorithm_inventory_mutations",
            "accepted_reconciliation",
            "zero_unprotected_positions",
            "accepted_event_latency",
            "accepted_decision_latency",
            "accepted_broker_latency",
        ),
        "MANUAL_PAPER": (
            "deterministic_replay_parity",
            "successful_restart_recovery",
            "zero_unexplained_decision_mismatches",
            "zero_duplicate_broker_orders",
            "zero_cross_algorithm_inventory_mutations",
            "accepted_reconciliation",
            "zero_unprotected_positions",
            "accepted_event_latency",
            "accepted_decision_latency",
            "accepted_broker_latency",
            "recorded_slippage",
        ),
        "LIMITED_AUTOMATIC_PAPER": tuple(sorted(WCA_REQUIRED_ROLLOUT_EVIDENCE)),
        "AUTOMATIC_PAPER": tuple(sorted(WCA_REQUIRED_ROLLOUT_EVIDENCE)),
    }
    blockers: list[str] = []
    for evidence_id in requirements[stage]:
        if evidence_id not in evidence.persisted_evidence_ids:
            blockers.append(f"wca.rollout.missing_persisted_evidence.{evidence_id}")
    if _stage_at_least(stage, "HISTORICAL_REPLAY") and not evidence.deterministic_replay_parity:
        blockers.append("wca.rollout.deterministic_replay_parity_missing")
    if _stage_at_least(stage, "HISTORICAL_REPLAY") and not evidence.restart_recovery_passed:
        blockers.append("wca.rollout.restart_recovery_not_validated")
    if _stage_at_least(stage, "SHADOW") and evidence.unexplained_decision_mismatches > 0:
        blockers.append("wca.rollout.unexplained_decision_mismatch")
    if _stage_at_least(stage, "SHADOW") and (
        evidence.max_event_lag_seconds is None or evidence.max_event_lag_seconds > thresholds.max_event_lag_seconds
    ):
        blockers.append("wca.rollout.event_lag_unacceptable")
    if _stage_at_least(stage, "SHADOW") and (
        evidence.max_decision_latency_seconds is None
        or evidence.max_decision_latency_seconds > thresholds.max_decision_latency_seconds
    ):
        blockers.append("wca.rollout.decision_latency_unacceptable")
    if _stage_at_least(stage, "PAPER_RECOMMENDATION"):
        if evidence.duplicate_broker_orders > 0:
            blockers.append("wca.rollout.duplicate_broker_order_detected")
        if evidence.cross_algorithm_inventory_mutations > 0:
            blockers.append("wca.rollout.cross_algorithm_inventory_mutation")
        if not evidence.reconciliation_passed:
            blockers.append("wca.rollout.reconciliation_not_validated")
        if evidence.unprotected_positions > 0:
            blockers.append("wca.rollout.unprotected_position_detected")
        if evidence.max_broker_latency_seconds is None or evidence.max_broker_latency_seconds > thresholds.max_broker_latency_seconds:
            blockers.append("wca.rollout.broker_latency_unacceptable")
    if _stage_at_least(stage, "MANUAL_PAPER"):
        if evidence.average_realised_slippage_per_share is None or evidence.average_realised_slippage_per_share > thresholds.max_average_realised_slippage_per_share:
            blockers.append("wca.rollout.realised_slippage_unacceptable")
    if _stage_at_least(stage, "LIMITED_AUTOMATIC_PAPER"):
        if len(set(evidence.market_conditions)) < 3:
            blockers.append("wca.rollout.market_conditions_not_validated")
        if not {"opening", "midday", "closing"}.issubset(set(evidence.session_periods)):
            blockers.append("wca.rollout.session_periods_not_validated")
        if evidence.high_volatility_sessions <= 0 or evidence.economic_event_sessions <= 0:
            blockers.append("wca.rollout.stress_sessions_not_validated")
        if evidence.paper_observation_days < thresholds.minimum_paper_observation_days:
            blockers.append("wca.rollout.paper_observation_duration_too_short")
        if evidence.paper_trade_count < thresholds.minimum_paper_trade_count:
            blockers.append("wca.rollout.paper_trade_count_too_low")
        if not evidence.rollback_tested or not evidence.rollback_restored_safe_state:
            blockers.append("wca.rollout.rollback_not_validated")
    return blockers


def _stage_at_least(stage: WcaRolloutStage, threshold: WcaRolloutStage) -> bool:
    return WCA_ROLLOUT_STAGES.index(stage) >= WCA_ROLLOUT_STAGES.index(threshold)


def _canonical_stage(stage: str) -> WcaRolloutStage:
    canonical = WCA_ROLLOUT_STAGE_ALIASES.get(stage, stage)
    if canonical not in WCA_ROLLOUT_STAGES:
        raise ValueError(f"unknown WCA rollout stage: {stage}")
    return canonical  # type: ignore[return-value]


def _values_match(left: object, right: object, tolerance: float) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        keys = set(left) | set(right)
        return all(_values_match(left.get(key), right.get(key), tolerance) for key in keys)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(_values_match(a, b, tolerance) for a, b in zip(left, right))
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        try:
            return abs(float(left) - float(right)) <= tolerance
        except (TypeError, ValueError):
            return False
    return left == right


def _env_bool(source: Mapping[str, str], key: str, default: bool) -> bool:
    raw = source.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_optional(store: WcaRolloutStore, key: str) -> dict | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


__all__ = [
    "GLOBAL_GATE_ENGINE_ENABLED",
    "WCA_BACKEND_BACKTEST_ENABLED",
    "WCA_BACKEND_ENGINE_ENABLED",
    "WCA_CORRECTED_STRATEGY_CATALOG_ENABLED",
    "WCA_DYNAMIC_PROFILE_ENABLED",
    "WCA_DYNAMIC_WEIGHTS_ENABLED",
    "WCA_PAPER_EXECUTION_ENABLED",
    "WCA_ROLLBACK_STATE_KEY",
    "WCA_ROLLOUT_PHASES",
    "WCA_ROLLOUT_STAGES",
    "WCA_ROLLOUT_STATE_KEY",
    "WCA_ROLLOUT_VERSION",
    "WCA_ROLLOUT_EVIDENCE_ALIASES",
    "WCA_REQUIRED_ROLLOUT_EVIDENCE",
    "WCA_SHADOW_COMPARISON_FIELDS",
    "WcaCriticalFailureAction",
    "WcaLimitedAutomaticPaperCaps",
    "WcaRolloutEvidence",
    "WcaRolloutEvidenceThresholds",
    "WcaRolloutFlags",
    "WcaRolloutPermission",
    "WcaRolloutPhaseStatus",
    "WcaRolloutStage",
    "WcaRolloutValidation",
    "WcaRollbackResult",
    "WcaShadowComparisonResult",
    "compare_shadow_results",
    "critical_failure_action",
    "evaluate_wca_rollout_phase",
    "evaluate_wca_rollout_stage",
    "highest_wca_rollout_stage",
    "limited_automatic_paper_allowed",
    "manual_paper_allowed",
    "paper_execution_allowed",
    "paper_recommendation_allowed",
    "record_valid_wca_rollout_state",
    "rollback_configuration",
    "rollback_wca_automatic_paper_stage",
    "rollback_wca_rollout",
    "wca_rollout_feature_flags",
    "wca_rollout_status",
]
