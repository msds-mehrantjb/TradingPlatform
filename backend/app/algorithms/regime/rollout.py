"""Staged paper deployment controls for the Regime algorithm."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Mapping, Protocol


REGIME_ROLLOUT_VERSION = "regime_staged_paper_deployment_v1"

REGIME_V2_ENABLED = "REGIME_V2_ENABLED"
REGIME_DYNAMIC_PROFILE_ENABLED = "REGIME_DYNAMIC_PROFILE_ENABLED"
REGIME_ML_MODE = "REGIME_ML_MODE"
REGIME_GLOBAL_RISK_MANAGER_ENABLED = "REGIME_GLOBAL_RISK_MANAGER_ENABLED"
REGIME_SHORT_ENTRIES_ENABLED = "REGIME_SHORT_ENTRIES_ENABLED"

REGIME_ROLLOUT_STATE_KEY = "regime.rollout.active"
REGIME_ROLLBACK_STATE_KEY = "regime.rollout.previous_valid"

RegimeMlMode = Literal["off", "shadow", "confirm_only", "active"]
RegimeRolloutPhase = Literal[
    "historical_characterization",
    "dedicated_backtest",
    "untouched_oos",
    "ml_shadow",
    "paper_shadow_decisions",
    "shadow_comparison",
    "limited_paper_orders",
    "global_gate_monitoring",
    "multi_regime_trade_collection",
    "promotion_review",
]

REGIME_ROLLOUT_PHASES: tuple[RegimeRolloutPhase, ...] = (
    "historical_characterization",
    "dedicated_backtest",
    "untouched_oos",
    "ml_shadow",
    "paper_shadow_decisions",
    "shadow_comparison",
    "limited_paper_orders",
    "global_gate_monitoring",
    "multi_regime_trade_collection",
    "promotion_review",
)


class RegimeRolloutStore(Protocol):
    def read_snapshot(self, key: str) -> dict:
        ...

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        ...


class RegimeRolloutPermission(str, Enum):
    ENABLED = "enabled"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class RegimeRolloutFlags:
    v2_enabled: bool = True
    dynamic_profile_enabled: bool = True
    ml_mode: RegimeMlMode = "shadow"
    global_risk_manager_enabled: bool = True
    short_entries_enabled: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "RegimeRolloutFlags":
        source = environ or os.environ
        return cls(
            v2_enabled=_env_bool(source, REGIME_V2_ENABLED, True),
            dynamic_profile_enabled=_env_bool(source, REGIME_DYNAMIC_PROFILE_ENABLED, True),
            ml_mode=_env_ml_mode(source.get(REGIME_ML_MODE), "shadow"),
            global_risk_manager_enabled=_env_bool(source, REGIME_GLOBAL_RISK_MANAGER_ENABLED, True),
            short_entries_enabled=_env_bool(source, REGIME_SHORT_ENTRIES_ENABLED, False),
        )

    def model_dump(self) -> dict[str, bool | str]:
        return {
            "rollout_version": REGIME_ROLLOUT_VERSION,
            REGIME_V2_ENABLED: self.v2_enabled,
            REGIME_DYNAMIC_PROFILE_ENABLED: self.dynamic_profile_enabled,
            REGIME_ML_MODE: self.ml_mode,
            REGIME_GLOBAL_RISK_MANAGER_ENABLED: self.global_risk_manager_enabled,
            REGIME_SHORT_ENTRIES_ENABLED: self.short_entries_enabled,
            "paper_trading_only": True,
        }


@dataclass(frozen=True)
class RegimeRolloutValidation:
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

    def model_dump(self) -> dict[str, bool]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class RegimeRolloutPhaseStatus:
    phase: RegimeRolloutPhase
    permission: RegimeRolloutPermission | str
    reason_codes: tuple[str, ...]
    explanation: str

    @property
    def enabled(self) -> bool:
        return self.permission == RegimeRolloutPermission.ENABLED.value

    def model_dump(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "permission": self.permission,
            "enabled": self.enabled,
            "reason_codes": self.reason_codes,
            "explanation": self.explanation,
        }


def regime_rollout_feature_flags(environ: Mapping[str, str] | None = None) -> RegimeRolloutFlags:
    return RegimeRolloutFlags.from_env(environ)


def evaluate_regime_rollout_phase(
    phase: RegimeRolloutPhase,
    *,
    flags: RegimeRolloutFlags | None = None,
    validation: RegimeRolloutValidation | None = None,
) -> RegimeRolloutPhaseStatus:
    active_flags = flags or regime_rollout_feature_flags()
    active_validation = validation or RegimeRolloutValidation()
    if phase not in REGIME_ROLLOUT_PHASES:
        raise ValueError(f"unknown Regime rollout phase: {phase}")
    blockers = _phase_blockers(phase, active_flags, active_validation)
    if blockers:
        return RegimeRolloutPhaseStatus(
            phase=phase,
            permission=RegimeRolloutPermission.BLOCKED.value,
            reason_codes=tuple(blockers),
            explanation="Regime rollout phase is blocked until flags and prior paper-deployment evidence pass.",
        )
    return RegimeRolloutPhaseStatus(
        phase=phase,
        permission=RegimeRolloutPermission.ENABLED.value,
        reason_codes=(f"regime.rollout.{phase}.enabled",),
        explanation="Regime rollout phase is enabled for staged paper deployment only.",
    )


def regime_rollout_status(
    *,
    flags: RegimeRolloutFlags | None = None,
    validation: RegimeRolloutValidation | None = None,
) -> dict[str, object]:
    active_flags = flags or regime_rollout_feature_flags()
    active_validation = validation or RegimeRolloutValidation()
    phases = tuple(
        evaluate_regime_rollout_phase(phase, flags=active_flags, validation=active_validation).model_dump()
        for phase in REGIME_ROLLOUT_PHASES
    )
    return {
        "algorithm_id": "regime",
        "rollout_version": REGIME_ROLLOUT_VERSION,
        "feature_flags": active_flags.model_dump(),
        "validation": active_validation.model_dump(),
        "phases": phases,
        "paper_shadow_allowed": evaluate_regime_rollout_phase("paper_shadow_decisions", flags=active_flags, validation=active_validation).enabled,
        "limited_paper_orders_allowed": limited_paper_orders_allowed(flags=active_flags, validation=active_validation),
        "live_trading_allowed": False,
        "rollback_plan": rollback_configuration(),
        "deployment_sequence": REGIME_ROLLOUT_PHASES,
        "reason_codes": ("regime.rollout.paper_only", "regime.rollout.live_trading_never_allowed"),
    }


def limited_paper_orders_allowed(
    *,
    flags: RegimeRolloutFlags | None = None,
    validation: RegimeRolloutValidation | None = None,
) -> bool:
    return evaluate_regime_rollout_phase(
        "limited_paper_orders",
        flags=flags or regime_rollout_feature_flags(),
        validation=validation or RegimeRolloutValidation(),
    ).enabled


def rollback_configuration() -> dict[str, object]:
    return {
        REGIME_V2_ENABLED: False,
        REGIME_DYNAMIC_PROFILE_ENABLED: False,
        REGIME_ML_MODE: "off",
        REGIME_GLOBAL_RISK_MANAGER_ENABLED: True,
        REGIME_SHORT_ENTRIES_ENABLED: False,
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
    }


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


def _phase_blockers(phase: RegimeRolloutPhase, flags: RegimeRolloutFlags, validation: RegimeRolloutValidation) -> list[str]:
    blockers: list[str] = []
    if validation.live_trading_enabled:
        blockers.append("regime.rollout.live_trading_never_allowed")
    if not flags.v2_enabled:
        blockers.append("regime.rollout.v2_flag_disabled")
    if phase in {"dedicated_backtest", "untouched_oos", "ml_shadow", "paper_shadow_decisions", "shadow_comparison", "limited_paper_orders", "global_gate_monitoring", "multi_regime_trade_collection", "promotion_review"} and not validation.historical_characterization_passed:
        blockers.append("regime.rollout.historical_characterization_not_validated")
    if phase in {"untouched_oos", "ml_shadow", "paper_shadow_decisions", "shadow_comparison", "limited_paper_orders", "global_gate_monitoring", "multi_regime_trade_collection", "promotion_review"} and not validation.dedicated_backtest_passed:
        blockers.append("regime.rollout.dedicated_backtest_not_validated")
    if phase in {"paper_shadow_decisions", "shadow_comparison", "limited_paper_orders", "global_gate_monitoring", "multi_regime_trade_collection", "promotion_review"} and not validation.untouched_oos_passed:
        blockers.append("regime.rollout.untouched_oos_not_validated")
    if phase in {"shadow_comparison", "limited_paper_orders", "global_gate_monitoring", "multi_regime_trade_collection", "promotion_review"} and not validation.paper_shadow_decisions_passed:
        blockers.append("regime.rollout.paper_shadow_decisions_not_validated")
    if phase in {"limited_paper_orders", "global_gate_monitoring", "multi_regime_trade_collection", "promotion_review"} and not validation.old_new_decision_comparison_passed:
        blockers.append("regime.rollout.old_new_decision_comparison_not_validated")
    if phase in {"global_gate_monitoring", "multi_regime_trade_collection", "promotion_review"} and not validation.limited_paper_orders_approved:
        blockers.append("regime.rollout.limited_paper_orders_not_approved")
    if phase in {"multi_regime_trade_collection", "promotion_review"} and not validation.global_gate_monitoring_passed:
        blockers.append("regime.rollout.global_gate_monitoring_not_validated")
    if phase == "promotion_review":
        if not validation.enough_multi_regime_trades_collected:
            blockers.append("regime.rollout.multi_regime_trade_collection_not_sufficient")
        if not validation.performance_review_passed:
            blockers.append("regime.rollout.performance_review_not_passed")

    if phase == "ml_shadow" and flags.ml_mode != "shadow":
        blockers.append("regime.rollout.ml_shadow_mode_required")
    if phase in {"paper_shadow_decisions", "shadow_comparison"} and flags.short_entries_enabled:
        blockers.append("regime.rollout.short_entries_disabled_initially")
    if phase in {"limited_paper_orders", "global_gate_monitoring", "multi_regime_trade_collection", "promotion_review"}:
        if not validation.tests_passed:
            blockers.append("regime.rollout.tests_not_passed")
        if not flags.global_risk_manager_enabled:
            blockers.append("regime.rollout.global_risk_manager_required")
    return list(dict.fromkeys(blockers))


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
    "REGIME_DYNAMIC_PROFILE_ENABLED",
    "REGIME_GLOBAL_RISK_MANAGER_ENABLED",
    "REGIME_ML_MODE",
    "REGIME_ROLLBACK_STATE_KEY",
    "REGIME_ROLLOUT_PHASES",
    "REGIME_ROLLOUT_STATE_KEY",
    "REGIME_ROLLOUT_VERSION",
    "REGIME_SHORT_ENTRIES_ENABLED",
    "REGIME_V2_ENABLED",
    "RegimeRolloutFlags",
    "RegimeRolloutPermission",
    "RegimeRolloutPhaseStatus",
    "RegimeRolloutValidation",
    "evaluate_regime_rollout_phase",
    "limited_paper_orders_allowed",
    "record_valid_regime_rollout_state",
    "regime_rollout_feature_flags",
    "regime_rollout_status",
    "rollback_configuration",
    "rollback_regime_rollout",
]
