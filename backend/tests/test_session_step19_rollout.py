from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.app.algorithms.session import (
    SESSION_ROLLBACK_STATE_KEY,
    SESSION_ROLLOUT_STATE_KEY,
    SESSION_ROLLOUT_STAGES,
    SessionHealthStatus,
    SessionPromotionEvidence,
    SessionRolloutFlags,
    SessionRolloutStage,
    build_session_promotion_evidence,
    evaluate_session_rollout_control,
    evaluate_session_rollout_stage,
    order_authority_for_stage,
    record_approved_session_rollout_version,
    rollback_session_rollout,
    session_rollout_feature_flags,
    session_rollout_status,
    validation_from_evidence,
)


NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def test_session_step19_default_deployment_is_shadow_only_with_diagnostics_on() -> None:
    status = session_rollout_status()
    control = status["control"]
    authority = control["order_authority"]

    assert status["defaultDeployment"]["classificationEnabled"] is True
    assert status["defaultDeployment"]["visibleInUi"] is True
    assert status["defaultDeployment"]["persistedForDiagnostics"] is True
    assert control["effective_stage"] == SessionRolloutStage.PAPER_SHADOW.value
    assert control["classification_enabled"] is True
    assert control["ui_visible"] is True
    assert control["diagnostic_persistence_enabled"] is True
    assert control["shadow_only"] is True
    assert authority["order_affecting"] is False
    assert authority["may_increase_risk"] is False
    assert authority["may_bypass_global_gates"] is False
    assert authority["live_trading_allowed"] is False


def test_session_step19_feature_flags_parse_environment_independently() -> None:
    flags = session_rollout_feature_flags(
        {
            "SESSION_CLASSIFICATION_ENABLED": "false",
            "SESSION_UI_VISIBLE": "false",
            "SESSION_DIAGNOSTIC_PERSISTENCE_ENABLED": "true",
            "SESSION_HEALTH_AUTO_SHADOW_ENABLED": "false",
            "SESSION_LIVE_TRADING_ENABLED": "true",
        }
    )

    assert flags.classification_enabled is False
    assert flags.ui_visible is False
    assert flags.diagnostic_persistence_enabled is True
    assert flags.health_auto_shadow_enabled is False
    assert flags.live_trading_enabled is True


def test_session_step19_promotion_stages_require_immutable_evidence() -> None:
    blocked = evaluate_session_rollout_stage(SessionRolloutStage.PAPER_VETO_REDUCTION_ONLY)
    evidence = build_session_promotion_evidence(evidence_id="session-rollout-valid", created_at=NOW)
    validation = validation_from_evidence(evidence)
    enabled = evaluate_session_rollout_stage(SessionRolloutStage.PAPER_VETO_REDUCTION_ONLY, validation=validation)

    assert blocked.enabled is False
    assert "session.rollout.promotion_evidence_required" in blocked.reason_codes
    assert enabled.enabled is True
    assert enabled.permission == "enabled"


def test_session_step19_incomplete_evidence_blocks_promotion() -> None:
    evidence = build_session_promotion_evidence(
        evidence_id="session-rollout-incomplete",
        created_at=NOW,
        cost_stress_2_0x_passed=False,
    )
    validation = validation_from_evidence(evidence)
    status = evaluate_session_rollout_stage(SessionRolloutStage.LIMITED_PAPER_ROUTING, validation=validation)

    assert status.enabled is False
    assert "session.rollout.cost_stress_2_0x_failed" in status.reason_codes


def test_session_step19_initial_order_affecting_authority_is_veto_reduction_only() -> None:
    authority = order_authority_for_stage(SessionRolloutStage.PAPER_VETO_REDUCTION_ONLY)

    assert authority.order_affecting is True
    assert authority.may_block_candidate is True
    assert authority.may_reduce_size is True
    assert authority.may_shorten_validity_or_holding_time is True
    assert authority.may_tighten_cost_liquidity_eligibility is True
    assert authority.may_route_new_entries is False
    assert authority.may_create_trade_from_hold is False
    assert authority.may_reverse_direction is False
    assert authority.may_increase_risk is False
    assert authority.may_bypass_global_gates is False
    assert authority.live_trading_allowed is False


def test_session_step19_limited_paper_routing_still_cannot_increase_risk_or_bypass_gates() -> None:
    authority = order_authority_for_stage(SessionRolloutStage.LIMITED_PAPER_ROUTING)

    assert authority.may_route_new_entries is True
    assert authority.may_create_trade_from_hold is False
    assert authority.may_reverse_direction is False
    assert authority.may_increase_risk is False
    assert authority.may_bypass_global_gates is False
    assert authority.live_trading_allowed is False


def test_session_step19_record_approved_version_requires_complete_evidence_and_rollback_restores_previous() -> None:
    store = MemoryStore()
    evidence = build_session_promotion_evidence(evidence_id="session-rollout-valid", created_at=NOW)
    invalid = build_session_promotion_evidence(evidence_id="session-rollout-invalid", created_at=NOW, rollback_tested=False)

    with pytest.raises(ValueError, match="complete immutable evidence"):
        record_approved_session_rollout_version(store, {"stage": "paper_veto_reduction_only"}, evidence=invalid, recorded_at=NOW)

    first = record_approved_session_rollout_version(store, {"stage": "paper_veto_reduction_only", "stateVersion": "v1"}, evidence=evidence, recorded_at=NOW)
    second = record_approved_session_rollout_version(store, {"stage": "limited_paper_routing", "stateVersion": "v2"}, evidence=evidence, recorded_at=NOW)
    restored = rollback_session_rollout(store, rolled_back_at=NOW)

    assert first["stateVersion"] == "v1"
    assert second["stateVersion"] == "v2"
    assert store.snapshots[SESSION_ROLLBACK_STATE_KEY]["stateVersion"] == "v1"
    assert restored["stateVersion"] == "v1"
    assert store.snapshots[SESSION_ROLLOUT_STATE_KEY]["stateVersion"] == "v1"
    assert restored["historicalDiagnosticsPreserved"] is True


def test_session_step19_health_failure_returns_to_shadow_without_stopping_diagnostics() -> None:
    evidence = build_session_promotion_evidence(evidence_id="session-rollout-valid", created_at=NOW)
    validation = validation_from_evidence(evidence)
    health = SessionHealthStatus(healthy=False, reason_codes=("session.health.latency_drift",))

    control = evaluate_session_rollout_control(SessionRolloutStage.LIMITED_PAPER_ROUTING, validation=validation, health=health)

    assert control.effective_stage == SessionRolloutStage.PAPER_SHADOW.value
    assert control.health_returned_to_shadow is True
    assert control.order_authority.order_affecting is False
    assert control.classification_enabled is True
    assert control.ui_visible is True
    assert control.diagnostic_persistence_enabled is True
    assert "session.rollout.health_failure_returned_to_shadow" in control.reason_codes


def test_session_step19_live_flag_never_allows_live_trading() -> None:
    evidence = build_session_promotion_evidence(evidence_id="session-rollout-valid", created_at=NOW)
    validation = validation_from_evidence(evidence)
    control = evaluate_session_rollout_control(
        SessionRolloutStage.BROADER_PAPER_USE,
        flags=SessionRolloutFlags(live_trading_enabled=True),
        validation=validation,
    )

    assert control.live_trading_allowed is False
    assert control.order_authority.live_trading_allowed is False
    assert control.order_authority.order_affecting is False
    assert "session.rollout.live_trading_never_allowed" in control.reason_codes


def test_session_step19_status_reports_complete_rollout_sequence() -> None:
    status = session_rollout_status()

    assert status["deploymentSequence"] == SESSION_ROLLOUT_STAGES
    assert [stage["stage"] for stage in status["stages"]] == list(SESSION_ROLLOUT_STAGES)


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot
