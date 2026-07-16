"""Staged shadow migration and controlled rollout for WCA."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Mapping, Protocol


WCA_ROLLOUT_VERSION = "wca_shadow_migration_rollout_v1"

WCA_BACKEND_ENGINE_ENABLED = "WCA_BACKEND_ENGINE_ENABLED"
WCA_CORRECTED_STRATEGY_CATALOG_ENABLED = "WCA_CORRECTED_STRATEGY_CATALOG_ENABLED"
WCA_DYNAMIC_WEIGHTS_ENABLED = "WCA_DYNAMIC_WEIGHTS_ENABLED"
WCA_DYNAMIC_PROFILE_ENABLED = "WCA_DYNAMIC_PROFILE_ENABLED"
GLOBAL_GATE_ENGINE_ENABLED = "GLOBAL_GATE_ENGINE_ENABLED"
WCA_BACKEND_BACKTEST_ENABLED = "WCA_BACKEND_BACKTEST_ENABLED"
WCA_PAPER_EXECUTION_ENABLED = "WCA_PAPER_EXECUTION_ENABLED"

WCA_ROLLOUT_STATE_KEY = "wca.rollout.active"
WCA_ROLLBACK_STATE_KEY = "wca.rollout.previous_valid"

WcaRolloutPhase = Literal[
    "legacy_parity",
    "corrected_catalog_shadow",
    "backend_backtest",
    "paper_recommendation",
    "paper_execution",
    "extended_paper_validation",
    "legacy_removal",
]

WCA_ROLLOUT_PHASES: tuple[WcaRolloutPhase, ...] = (
    "legacy_parity",
    "corrected_catalog_shadow",
    "backend_backtest",
    "paper_recommendation",
    "paper_execution",
    "extended_paper_validation",
    "legacy_removal",
)

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
class WcaRolloutPhaseStatus:
    phase: WcaRolloutPhase
    permission: WcaRolloutPermission | str
    reason_codes: tuple[str, ...]
    explanation: str

    @property
    def enabled(self) -> bool:
        return self.permission == WcaRolloutPermission.ENABLED.value

    def model_dump(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "permission": self.permission,
            "enabled": self.enabled,
            "reason_codes": self.reason_codes,
            "explanation": self.explanation,
        }


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
    phase: WcaRolloutPhase,
    *,
    flags: WcaRolloutFlags | None = None,
    validation: WcaRolloutValidation | None = None,
) -> WcaRolloutPhaseStatus:
    active_flags = flags or wca_rollout_feature_flags()
    active_validation = validation or WcaRolloutValidation()
    if phase not in WCA_ROLLOUT_PHASES:
        raise ValueError(f"unknown WCA rollout phase: {phase}")
    blockers = _phase_blockers(phase, active_flags, active_validation)
    if blockers:
        return WcaRolloutPhaseStatus(
            phase=phase,
            permission=WcaRolloutPermission.BLOCKED.value,
            reason_codes=tuple(blockers),
            explanation="WCA rollout phase is blocked until flags and prior acceptance criteria pass.",
        )
    return WcaRolloutPhaseStatus(
        phase=phase,
        permission=WcaRolloutPermission.ENABLED.value,
        reason_codes=(f"wca.rollout.{phase}.enabled",),
        explanation="WCA rollout phase is enabled under the current flags and validation metrics.",
    )


def wca_rollout_status(
    *,
    flags: WcaRolloutFlags | None = None,
    validation: WcaRolloutValidation | None = None,
) -> dict[str, object]:
    active_flags = flags or wca_rollout_feature_flags()
    active_validation = validation or WcaRolloutValidation()
    phase_statuses = tuple(
        evaluate_wca_rollout_phase(phase, flags=active_flags, validation=active_validation).model_dump()
        for phase in WCA_ROLLOUT_PHASES
    )
    return {
        "algorithm_id": "wca",
        "rollout_version": WCA_ROLLOUT_VERSION,
        "feature_flags": active_flags.model_dump(),
        "validation": active_validation.model_dump(),
        "phases": phase_statuses,
        "paper_recommendation_allowed": paper_recommendation_allowed(flags=active_flags, validation=active_validation),
        "paper_execution_allowed": paper_execution_allowed(flags=active_flags, validation=active_validation),
        "live_trading_allowed": False,
        "rollback_plan": rollback_configuration(),
        "reason_codes": ("wca.rollout.paper_only", "wca.rollout.legacy_removal_guarded"),
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
) -> bool:
    return evaluate_wca_rollout_phase(
        "paper_recommendation",
        flags=flags or wca_rollout_feature_flags(),
        validation=validation or WcaRolloutValidation(),
    ).enabled


def paper_execution_allowed(
    *,
    flags: WcaRolloutFlags | None = None,
    validation: WcaRolloutValidation | None = None,
) -> bool:
    return evaluate_wca_rollout_phase(
        "paper_execution",
        flags=flags or wca_rollout_feature_flags(),
        validation=validation or WcaRolloutValidation(),
    ).enabled


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
        "delete_historical_records": False,
    }


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


def rollback_wca_rollout(store: WcaRolloutStore, *, rolled_back_at: datetime | None = None) -> dict:
    previous = _read_optional(store, WCA_ROLLBACK_STATE_KEY)
    if not previous:
        previous = {
            "algorithm_id": "wca",
            "rollout_version": WCA_ROLLOUT_VERSION,
            "status": "rollback_baseline",
            "rollback_configuration": rollback_configuration(),
            "reason_codes": ("wca.rollout.rollback_baseline_restored",),
        }
    restored = {
        **previous,
        "restored_at": (rolled_back_at or datetime.now(timezone.utc)).isoformat(),
        "rollback_configuration": rollback_configuration(),
        "historical_records_deleted": False,
        "reason_codes": tuple(dict.fromkeys([*(previous.get("reason_codes") or ()), "wca.rollout.rollback_restored_safe_state"])),
    }
    store.write_snapshot(WCA_ROLLOUT_STATE_KEY, restored)
    return restored


def _phase_blockers(phase: WcaRolloutPhase, flags: WcaRolloutFlags, validation: WcaRolloutValidation) -> list[str]:
    blockers: list[str] = []
    if validation.live_trading_enabled:
        blockers.append("wca.rollout.live_trading_never_allowed")
    if not flags.backend_engine_enabled:
        blockers.append("wca.rollout.backend_engine_disabled")

    required_acceptance: dict[WcaRolloutPhase, tuple[tuple[bool, str], ...]] = {
        "legacy_parity": (),
        "corrected_catalog_shadow": ((validation.legacy_parity_passed, "wca.rollout.legacy_parity_not_validated"),),
        "backend_backtest": (
            (validation.legacy_parity_passed, "wca.rollout.legacy_parity_not_validated"),
            (validation.corrected_catalog_shadow_passed, "wca.rollout.corrected_catalog_shadow_not_validated"),
        ),
        "paper_recommendation": (
            (validation.full_history_backtest_passed, "wca.rollout.full_history_backtest_not_validated"),
            (validation.walk_forward_passed, "wca.rollout.walk_forward_not_validated"),
            (validation.untouched_holdout_passed, "wca.rollout.untouched_holdout_not_validated"),
        ),
        "paper_execution": (
            (validation.paper_recommendation_passed, "wca.rollout.paper_recommendation_not_validated"),
            (validation.tests_passed, "wca.rollout.tests_not_passed"),
        ),
        "extended_paper_validation": (
            (validation.paper_execution_passed, "wca.rollout.paper_execution_not_validated"),
            (validation.paper_trading_stable, "wca.rollout.paper_trading_not_stable"),
        ),
        "legacy_removal": (
            (validation.multiple_market_conditions_passed, "wca.rollout.market_conditions_not_validated"),
            (validation.multi_week_paper_validation_passed, "wca.rollout.multi_week_validation_not_validated"),
            (validation.paper_trading_stable, "wca.rollout.paper_trading_not_stable"),
            (validation.legacy_removal_accepted, "wca.rollout.legacy_removal_not_accepted"),
        ),
    }
    for passed, reason_code in required_acceptance[phase]:
        if not passed:
            blockers.append(reason_code)

    if phase == "corrected_catalog_shadow" and not flags.corrected_strategy_catalog_enabled:
        blockers.append("wca.rollout.corrected_catalog_flag_disabled")
    if phase == "backend_backtest" and not flags.backend_backtest_enabled:
        blockers.append("wca.rollout.backend_backtest_flag_disabled")
    if phase == "paper_recommendation" and flags.paper_execution_enabled:
        blockers.append("wca.rollout.paper_execution_must_remain_disabled_for_recommendations")
    if phase == "paper_execution":
        if not flags.paper_execution_enabled:
            blockers.append("wca.rollout.paper_execution_flag_disabled")
        if not flags.global_gate_engine_enabled:
            blockers.append("wca.rollout.global_gate_engine_required")
    if phase == "legacy_removal" and flags.paper_execution_enabled and not validation.multi_week_paper_validation_passed:
        blockers.append("wca.rollout.legacy_removal_requires_extended_paper_validation")
    return list(dict.fromkeys(blockers))


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
    "WCA_ROLLOUT_STATE_KEY",
    "WCA_ROLLOUT_VERSION",
    "WCA_SHADOW_COMPARISON_FIELDS",
    "WcaRolloutFlags",
    "WcaRolloutPermission",
    "WcaRolloutPhaseStatus",
    "WcaRolloutValidation",
    "WcaShadowComparisonResult",
    "compare_shadow_results",
    "evaluate_wca_rollout_phase",
    "paper_execution_allowed",
    "paper_recommendation_allowed",
    "record_valid_wca_rollout_state",
    "rollback_configuration",
    "rollback_wca_rollout",
    "wca_rollout_feature_flags",
    "wca_rollout_status",
]
