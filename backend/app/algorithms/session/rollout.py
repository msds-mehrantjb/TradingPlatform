"""Staged rollout, promotion, rollback, and health fail-closed policy for Session."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
import hashlib
import json
import os
from typing import Any, Mapping, Protocol


SESSION_ROLLOUT_VERSION = "session_rollout_v1"
SESSION_ROLLOUT_NAMESPACE = "data/algorithms/session/rollout/"

SESSION_CLASSIFICATION_ENABLED = "SESSION_CLASSIFICATION_ENABLED"
SESSION_UI_VISIBLE = "SESSION_UI_VISIBLE"
SESSION_DIAGNOSTIC_PERSISTENCE_ENABLED = "SESSION_DIAGNOSTIC_PERSISTENCE_ENABLED"
SESSION_HEALTH_AUTO_SHADOW_ENABLED = "SESSION_HEALTH_AUTO_SHADOW_ENABLED"
SESSION_LIVE_TRADING_ENABLED = "SESSION_LIVE_TRADING_ENABLED"

SESSION_ROLLOUT_STATE_KEY = "session.rollout.active"
SESSION_ROLLBACK_STATE_KEY = "session.rollout.previous_approved"


class SessionRolloutStage(str, Enum):
    HISTORICAL_CHARACTERIZATION = "historical_characterization"
    EVENT_REPLAY_VALIDATION = "event_replay_validation"
    UNTOUCHED_OOS_VALIDATION = "untouched_out_of_sample_validation"
    PAPER_SHADOW = "paper_shadow"
    PAPER_VETO_REDUCTION_ONLY = "paper_veto_reduction_only"
    LIMITED_PAPER_ROUTING = "limited_paper_routing"
    BROADER_PAPER_USE = "broader_paper_use"


SESSION_ROLLOUT_STAGES: tuple[str, ...] = tuple(stage.value for stage in SessionRolloutStage)


class SessionRolloutPermission(str, Enum):
    ENABLED = "enabled"
    BLOCKED = "blocked"


class SessionRolloutStore(Protocol):
    def read_snapshot(self, key: str) -> dict[str, Any]:
        ...

    def write_snapshot(self, key: str, snapshot: dict[str, Any]) -> None:
        ...


@dataclass(frozen=True)
class SessionRolloutFlags:
    classification_enabled: bool = True
    ui_visible: bool = True
    diagnostic_persistence_enabled: bool = True
    health_auto_shadow_enabled: bool = True
    live_trading_enabled: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SessionRolloutFlags:
        source = environ or os.environ
        return cls(
            classification_enabled=_env_bool(source, SESSION_CLASSIFICATION_ENABLED, True),
            ui_visible=_env_bool(source, SESSION_UI_VISIBLE, True),
            diagnostic_persistence_enabled=_env_bool(source, SESSION_DIAGNOSTIC_PERSISTENCE_ENABLED, True),
            health_auto_shadow_enabled=_env_bool(source, SESSION_HEALTH_AUTO_SHADOW_ENABLED, True),
            live_trading_enabled=_env_bool(source, SESSION_LIVE_TRADING_ENABLED, False),
        )

    def model_dump(self) -> dict[str, Any]:
        return {
            "rolloutVersion": SESSION_ROLLOUT_VERSION,
            SESSION_CLASSIFICATION_ENABLED: self.classification_enabled,
            SESSION_UI_VISIBLE: self.ui_visible,
            SESSION_DIAGNOSTIC_PERSISTENCE_ENABLED: self.diagnostic_persistence_enabled,
            SESSION_HEALTH_AUTO_SHADOW_ENABLED: self.health_auto_shadow_enabled,
            SESSION_LIVE_TRADING_ENABLED: self.live_trading_enabled,
        }


@dataclass(frozen=True)
class SessionPromotionEvidence:
    evidence_id: str
    generated_by: str = "backend_session_research"
    created_at: datetime | None = None
    all_session_tests_passed: bool = False
    point_in_time_leakage_absent: bool = False
    replay_backtest_paper_parity_passed: bool = False
    dst_and_early_close_tests_passed: bool = False
    stale_missing_data_fail_closed: bool = False
    transition_stability_passed: bool = False
    unknown_state_occupancy_acceptable: bool = False
    latency_within_budget: bool = False
    paper_shadow_matches_replay: bool = False
    cost_stress_1_5x_passed: bool = False
    cost_stress_2_0x_passed: bool = False
    improves_net_value_or_reduces_risk: bool = False
    rollback_tested: bool = False
    report_ids: tuple[str, ...] = ()
    metrics: Mapping[str, Any] | None = None
    reason_codes: tuple[str, ...] = ("session.rollout.evidence.immutable",)

    def model_dump(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = (self.created_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        payload["evidence_hash"] = self.evidence_hash()
        return _jsonable(payload)

    def evidence_hash(self) -> str:
        payload = asdict(self)
        payload["created_at"] = (self.created_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        return _hash_json(payload)


@dataclass(frozen=True)
class SessionRolloutValidation:
    evidence: SessionPromotionEvidence | None = None
    historical_characterization_passed: bool = False
    event_replay_validation_passed: bool = False
    untouched_oos_validation_passed: bool = False
    paper_shadow_passed: bool = False
    paper_veto_reduction_approved: bool = False
    limited_paper_routing_approved: bool = False
    broader_paper_use_approved: bool = False

    def model_dump(self) -> dict[str, Any]:
        return {
            "evidence": self.evidence.model_dump() if self.evidence else None,
            "historical_characterization_passed": self.historical_characterization_passed,
            "event_replay_validation_passed": self.event_replay_validation_passed,
            "untouched_oos_validation_passed": self.untouched_oos_validation_passed,
            "paper_shadow_passed": self.paper_shadow_passed,
            "paper_veto_reduction_approved": self.paper_veto_reduction_approved,
            "limited_paper_routing_approved": self.limited_paper_routing_approved,
            "broader_paper_use_approved": self.broader_paper_use_approved,
        }


@dataclass(frozen=True)
class SessionHealthStatus:
    healthy: bool = True
    latency_within_budget: bool = True
    unknown_occupancy_acceptable: bool = True
    cost_stress_acceptable: bool = True
    parity_ok: bool = True
    data_fail_closed: bool = True
    reason_codes: tuple[str, ...] = ()

    @property
    def failed(self) -> bool:
        return not (
            self.healthy
            and self.latency_within_budget
            and self.unknown_occupancy_acceptable
            and self.cost_stress_acceptable
            and self.parity_ok
            and self.data_fail_closed
        )

    def model_dump(self) -> dict[str, Any]:
        return {**asdict(self), "failed": self.failed}


@dataclass(frozen=True)
class SessionOrderAuthority:
    order_affecting: bool
    may_block_candidate: bool
    may_reduce_size: bool
    may_shorten_validity_or_holding_time: bool
    may_tighten_cost_liquidity_eligibility: bool
    may_route_new_entries: bool
    may_create_trade_from_hold: bool
    may_reverse_direction: bool
    may_increase_risk: bool
    may_bypass_global_gates: bool
    live_trading_allowed: bool
    reason_codes: tuple[str, ...]

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SessionRolloutStageStatus:
    stage: str
    permission: str
    enabled: bool
    reason_codes: tuple[str, ...]
    explanation: str

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SessionRolloutControl:
    requested_stage: str
    effective_stage: str
    algorithm_id: str
    rollout_version: str
    namespace: str
    classification_enabled: bool
    ui_visible: bool
    diagnostic_persistence_enabled: bool
    shadow_only: bool
    health_returned_to_shadow: bool
    order_authority: SessionOrderAuthority
    live_trading_allowed: bool
    reason_codes: tuple[str, ...]

    def model_dump(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "order_authority": self.order_authority.model_dump(),
        }


def session_rollout_feature_flags(environ: Mapping[str, str] | None = None) -> SessionRolloutFlags:
    return SessionRolloutFlags.from_env(environ)


def initial_session_rollout_validation() -> SessionRolloutValidation:
    return SessionRolloutValidation()


def evaluate_session_rollout_stage(
    stage: str | SessionRolloutStage,
    *,
    flags: SessionRolloutFlags | None = None,
    validation: SessionRolloutValidation | None = None,
) -> SessionRolloutStageStatus:
    requested = _stage_value(stage)
    active_flags = flags or session_rollout_feature_flags()
    active_validation = validation or SessionRolloutValidation()
    blockers = _stage_blockers(requested, active_flags, active_validation)
    if blockers:
        return SessionRolloutStageStatus(
            stage=requested,
            permission=SessionRolloutPermission.BLOCKED.value,
            enabled=False,
            reason_codes=tuple(blockers),
            explanation="Session rollout stage is blocked until immutable promotion evidence and prior validations pass.",
        )
    return SessionRolloutStageStatus(
        stage=requested,
        permission=SessionRolloutPermission.ENABLED.value,
        enabled=True,
        reason_codes=(f"session.rollout.{requested}.enabled",),
        explanation="Session rollout stage is enabled under paper-only guarded permissions.",
    )


def evaluate_session_rollout_control(
    requested_stage: str | SessionRolloutStage = SessionRolloutStage.PAPER_SHADOW,
    *,
    flags: SessionRolloutFlags | None = None,
    validation: SessionRolloutValidation | None = None,
    health: SessionHealthStatus | None = None,
) -> SessionRolloutControl:
    active_flags = flags or session_rollout_feature_flags()
    active_validation = validation or SessionRolloutValidation()
    active_health = health or SessionHealthStatus()
    requested = _stage_value(requested_stage)
    stage_status = evaluate_session_rollout_stage(requested, flags=active_flags, validation=active_validation)
    effective = requested if stage_status.enabled else SessionRolloutStage.PAPER_SHADOW.value
    health_shadow = False
    reasons = ["session.rollout.control_evaluated", *stage_status.reason_codes]
    if active_health.failed and active_flags.health_auto_shadow_enabled:
        effective = SessionRolloutStage.PAPER_SHADOW.value
        health_shadow = True
        reasons.extend(("session.rollout.health_failure_returned_to_shadow", *active_health.reason_codes))
    if active_flags.live_trading_enabled:
        reasons.append("session.rollout.live_trading_never_allowed")

    authority = order_authority_for_stage(effective, flags=active_flags)
    return SessionRolloutControl(
        requested_stage=requested,
        effective_stage=effective,
        algorithm_id="session",
        rollout_version=SESSION_ROLLOUT_VERSION,
        namespace=SESSION_ROLLOUT_NAMESPACE,
        classification_enabled=active_flags.classification_enabled,
        ui_visible=active_flags.ui_visible,
        diagnostic_persistence_enabled=active_flags.diagnostic_persistence_enabled,
        shadow_only=not authority.order_affecting,
        health_returned_to_shadow=health_shadow,
        order_authority=authority,
        live_trading_allowed=False,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


def order_authority_for_stage(stage: str | SessionRolloutStage, *, flags: SessionRolloutFlags | None = None) -> SessionOrderAuthority:
    active_flags = flags or session_rollout_feature_flags()
    value = _stage_value(stage)
    order_affecting = value in {
        SessionRolloutStage.PAPER_VETO_REDUCTION_ONLY.value,
        SessionRolloutStage.LIMITED_PAPER_ROUTING.value,
        SessionRolloutStage.BROADER_PAPER_USE.value,
    }
    routing = value in {SessionRolloutStage.LIMITED_PAPER_ROUTING.value, SessionRolloutStage.BROADER_PAPER_USE.value}
    if active_flags.live_trading_enabled:
        order_affecting = False
        routing = False
    return SessionOrderAuthority(
        order_affecting=order_affecting,
        may_block_candidate=order_affecting,
        may_reduce_size=order_affecting,
        may_shorten_validity_or_holding_time=order_affecting,
        may_tighten_cost_liquidity_eligibility=order_affecting,
        may_route_new_entries=routing,
        may_create_trade_from_hold=False,
        may_reverse_direction=False,
        may_increase_risk=False,
        may_bypass_global_gates=False,
        live_trading_allowed=False,
        reason_codes=(
            "session.rollout.veto_reduction_only" if order_affecting and not routing else
            "session.rollout.paper_routing_limited" if routing else
            "session.rollout.shadow_only"
        , "session.rollout.live_trading_never_allowed", "session.rollout.global_gates_required"),
    )


def session_rollout_status(
    *,
    flags: SessionRolloutFlags | None = None,
    validation: SessionRolloutValidation | None = None,
    requested_stage: str | SessionRolloutStage = SessionRolloutStage.PAPER_SHADOW,
    health: SessionHealthStatus | None = None,
) -> dict[str, Any]:
    active_flags = flags or session_rollout_feature_flags()
    active_validation = validation or SessionRolloutValidation()
    control = evaluate_session_rollout_control(requested_stage, flags=active_flags, validation=active_validation, health=health)
    return {
        "algorithmId": "session",
        "rolloutVersion": SESSION_ROLLOUT_VERSION,
        "namespace": SESSION_ROLLOUT_NAMESPACE,
        "featureFlags": active_flags.model_dump(),
        "validation": active_validation.model_dump(),
        "control": control.model_dump(),
        "stages": tuple(evaluate_session_rollout_stage(stage, flags=active_flags, validation=active_validation).model_dump() for stage in SESSION_ROLLOUT_STAGES),
        "deploymentSequence": SESSION_ROLLOUT_STAGES,
        "defaultDeployment": {
            "classificationEnabled": True,
            "visibleInUi": True,
            "persistedForDiagnostics": True,
            "strategyRouting": "shadow_only",
            "mayIncreaseRisk": False,
            "mayBypassExistingGate": False,
            "maySubmitLiveOrders": False,
        },
        "reasonCodes": ("session.rollout.default_shadow_only", "session.rollout.live_trading_never_allowed"),
    }


def build_session_promotion_evidence(**overrides: Any) -> SessionPromotionEvidence:
    defaults: dict[str, Any] = {
        "evidence_id": "session-promotion-evidence",
        "created_at": datetime.now(UTC),
        "all_session_tests_passed": True,
        "point_in_time_leakage_absent": True,
        "replay_backtest_paper_parity_passed": True,
        "dst_and_early_close_tests_passed": True,
        "stale_missing_data_fail_closed": True,
        "transition_stability_passed": True,
        "unknown_state_occupancy_acceptable": True,
        "latency_within_budget": True,
        "paper_shadow_matches_replay": True,
        "cost_stress_1_5x_passed": True,
        "cost_stress_2_0x_passed": True,
        "improves_net_value_or_reduces_risk": True,
        "rollback_tested": True,
        "report_ids": ("session-research-report",),
        "metrics": {
            "unknownStateOccupancy": 0.05,
            "latencyP95Ms": 250,
            "costStress1_5xNetAcceptable": True,
            "costStress2_0xNetAcceptable": True,
            "incrementalValueDemonstrated": True,
        },
    }
    defaults.update(overrides)
    return SessionPromotionEvidence(**defaults)


def validation_from_evidence(evidence: SessionPromotionEvidence) -> SessionRolloutValidation:
    evidence_ok = _evidence_passed(evidence)
    return SessionRolloutValidation(
        evidence=evidence,
        historical_characterization_passed=evidence_ok,
        event_replay_validation_passed=evidence_ok,
        untouched_oos_validation_passed=evidence_ok,
        paper_shadow_passed=evidence_ok,
        paper_veto_reduction_approved=evidence_ok,
        limited_paper_routing_approved=evidence_ok,
        broader_paper_use_approved=evidence_ok,
    )


def record_approved_session_rollout_version(
    store: SessionRolloutStore,
    candidate_state: dict[str, Any],
    *,
    evidence: SessionPromotionEvidence,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    if not _evidence_passed(evidence):
        raise ValueError("Session promotion cannot be recorded without complete immutable evidence")
    current = _read_optional(store, SESSION_ROLLOUT_STATE_KEY)
    if current and current.get("status") == "approved":
        store.write_snapshot(SESSION_ROLLBACK_STATE_KEY, current)
    state = {
        **candidate_state,
        "algorithmId": "session",
        "rolloutVersion": SESSION_ROLLOUT_VERSION,
        "status": "approved",
        "recordedAt": (recorded_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "promotionEvidence": evidence.model_dump(),
        "promotionEvidenceHash": evidence.evidence_hash(),
        "liveTradingAllowed": False,
        "historicalDiagnosticsPreserved": True,
        "reasonCodes": tuple(dict.fromkeys((*(candidate_state.get("reasonCodes") or ()), "session.rollout.approved_version_recorded"))),
    }
    store.write_snapshot(SESSION_ROLLOUT_STATE_KEY, state)
    return state


def rollback_session_rollout(store: SessionRolloutStore, *, rolled_back_at: datetime | None = None) -> dict[str, Any]:
    previous = _read_optional(store, SESSION_ROLLBACK_STATE_KEY)
    if not previous:
        previous = {
            "algorithmId": "session",
            "rolloutVersion": SESSION_ROLLOUT_VERSION,
            "stage": SessionRolloutStage.PAPER_SHADOW.value,
            "status": "rollback_shadow_baseline",
            "liveTradingAllowed": False,
            "reasonCodes": ("session.rollout.rollback_shadow_baseline",),
        }
    restored = {
        **previous,
        "restoredAt": (rolled_back_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "historicalDiagnosticsPreserved": True,
        "liveTradingAllowed": False,
        "reasonCodes": tuple(dict.fromkeys((*(previous.get("reasonCodes") or ()), "session.rollout.rollback_restored_previous_approved_version"))),
    }
    store.write_snapshot(SESSION_ROLLOUT_STATE_KEY, restored)
    return restored


def _stage_blockers(stage: str, flags: SessionRolloutFlags, validation: SessionRolloutValidation) -> list[str]:
    if stage not in SESSION_ROLLOUT_STAGES:
        raise ValueError(f"unknown Session rollout stage: {stage}")
    blockers: list[str] = []
    if not flags.classification_enabled:
        blockers.append("session.rollout.classification_flag_disabled")
    if flags.live_trading_enabled:
        blockers.append("session.rollout.live_trading_never_allowed")
    if stage == SessionRolloutStage.HISTORICAL_CHARACTERIZATION.value:
        return blockers
    if validation.evidence is None:
        blockers.append("session.rollout.promotion_evidence_required")
    elif not _evidence_passed(validation.evidence):
        blockers.extend(_evidence_failures(validation.evidence))
    if stage in _stages_at_or_after(SessionRolloutStage.EVENT_REPLAY_VALIDATION) and not validation.historical_characterization_passed:
        blockers.append("session.rollout.historical_characterization_not_validated")
    if stage in _stages_at_or_after(SessionRolloutStage.UNTOUCHED_OOS_VALIDATION) and not validation.event_replay_validation_passed:
        blockers.append("session.rollout.event_replay_not_validated")
    if stage in _stages_at_or_after(SessionRolloutStage.PAPER_SHADOW) and not validation.untouched_oos_validation_passed:
        blockers.append("session.rollout.untouched_oos_not_validated")
    if stage in _stages_at_or_after(SessionRolloutStage.PAPER_VETO_REDUCTION_ONLY) and not validation.paper_shadow_passed:
        blockers.append("session.rollout.paper_shadow_not_validated")
    if stage in _stages_at_or_after(SessionRolloutStage.LIMITED_PAPER_ROUTING) and not validation.paper_veto_reduction_approved:
        blockers.append("session.rollout.paper_veto_reduction_not_approved")
    if stage in _stages_at_or_after(SessionRolloutStage.BROADER_PAPER_USE) and not validation.limited_paper_routing_approved:
        blockers.append("session.rollout.limited_paper_routing_not_approved")
    if stage == SessionRolloutStage.BROADER_PAPER_USE.value and not validation.broader_paper_use_approved:
        blockers.append("session.rollout.broader_paper_use_not_approved")
    return list(dict.fromkeys(blockers))


def _stages_at_or_after(stage: SessionRolloutStage) -> set[str]:
    values = SESSION_ROLLOUT_STAGES
    return set(values[values.index(stage.value) :])


def _evidence_passed(evidence: SessionPromotionEvidence) -> bool:
    return not _evidence_failures(evidence)


def _evidence_failures(evidence: SessionPromotionEvidence) -> tuple[str, ...]:
    checks = {
        "all_session_tests_passed": "session.rollout.tests_not_passed",
        "point_in_time_leakage_absent": "session.rollout.point_in_time_leakage_not_cleared",
        "replay_backtest_paper_parity_passed": "session.rollout.parity_not_validated",
        "dst_and_early_close_tests_passed": "session.rollout.calendar_tests_not_passed",
        "stale_missing_data_fail_closed": "session.rollout.fail_closed_not_validated",
        "transition_stability_passed": "session.rollout.transition_stability_not_validated",
        "unknown_state_occupancy_acceptable": "session.rollout.unknown_occupancy_unacceptable",
        "latency_within_budget": "session.rollout.latency_budget_failed",
        "paper_shadow_matches_replay": "session.rollout.paper_shadow_replay_mismatch",
        "cost_stress_1_5x_passed": "session.rollout.cost_stress_1_5x_failed",
        "cost_stress_2_0x_passed": "session.rollout.cost_stress_2_0x_failed",
        "improves_net_value_or_reduces_risk": "session.rollout.no_incremental_value",
        "rollback_tested": "session.rollout.rollback_not_tested",
    }
    failures = [reason for field_name, reason in checks.items() if not bool(getattr(evidence, field_name))]
    if evidence.generated_by != "backend_session_research":
        failures.append("session.rollout.untrusted_evidence_source")
    if not evidence.report_ids:
        failures.append("session.rollout.research_report_required")
    return tuple(failures)


def _stage_value(stage: str | SessionRolloutStage) -> str:
    value = stage.value if isinstance(stage, SessionRolloutStage) else str(stage)
    if value not in SESSION_ROLLOUT_STAGES:
        raise ValueError(f"unknown Session rollout stage: {value}")
    return value


def _read_optional(store: SessionRolloutStore, key: str) -> dict[str, Any] | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


def _env_bool(source: Mapping[str, str], key: str, default: bool) -> bool:
    raw = source.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _hash_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()[:16]


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


__all__ = [
    "SESSION_CLASSIFICATION_ENABLED",
    "SESSION_DIAGNOSTIC_PERSISTENCE_ENABLED",
    "SESSION_HEALTH_AUTO_SHADOW_ENABLED",
    "SESSION_LIVE_TRADING_ENABLED",
    "SESSION_ROLLBACK_STATE_KEY",
    "SESSION_ROLLOUT_NAMESPACE",
    "SESSION_ROLLOUT_STAGES",
    "SESSION_ROLLOUT_STATE_KEY",
    "SESSION_ROLLOUT_VERSION",
    "SESSION_UI_VISIBLE",
    "SessionHealthStatus",
    "SessionOrderAuthority",
    "SessionPromotionEvidence",
    "SessionRolloutControl",
    "SessionRolloutFlags",
    "SessionRolloutPermission",
    "SessionRolloutStage",
    "SessionRolloutStageStatus",
    "SessionRolloutValidation",
    "build_session_promotion_evidence",
    "evaluate_session_rollout_control",
    "evaluate_session_rollout_stage",
    "initial_session_rollout_validation",
    "order_authority_for_stage",
    "record_approved_session_rollout_version",
    "rollback_session_rollout",
    "session_rollout_feature_flags",
    "session_rollout_status",
    "validation_from_evidence",
]
