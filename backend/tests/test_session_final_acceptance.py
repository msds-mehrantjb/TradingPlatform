from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from backend.app.algorithms.session import (
    SESSION_ROLLOUT_STAGES,
    SessionHealthStatus,
    SessionPromotionEvidence,
    SessionRolloutStage,
    build_session_promotion_evidence,
    evaluate_session_rollout_control,
    evaluate_session_rollout_stage,
    session_rollout_status,
    validation_from_evidence,
)


NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def test_session_final_acceptance_default_is_classification_visible_persisted_shadow_only() -> None:
    status = session_rollout_status()
    control = status["control"]

    assert control["classification_enabled"] is True
    assert control["ui_visible"] is True
    assert control["diagnostic_persistence_enabled"] is True
    assert control["shadow_only"] is True
    assert control["order_authority"]["order_affecting"] is False
    assert control["live_trading_allowed"] is False


def test_session_final_acceptance_every_promotion_requirement_is_enforced() -> None:
    base = build_session_promotion_evidence(evidence_id="session-final-valid", created_at=NOW)
    required_fields = (
        ("all_session_tests_passed", "session.rollout.tests_not_passed"),
        ("point_in_time_leakage_absent", "session.rollout.point_in_time_leakage_not_cleared"),
        ("replay_backtest_paper_parity_passed", "session.rollout.parity_not_validated"),
        ("dst_and_early_close_tests_passed", "session.rollout.calendar_tests_not_passed"),
        ("stale_missing_data_fail_closed", "session.rollout.fail_closed_not_validated"),
        ("transition_stability_passed", "session.rollout.transition_stability_not_validated"),
        ("unknown_state_occupancy_acceptable", "session.rollout.unknown_occupancy_unacceptable"),
        ("latency_within_budget", "session.rollout.latency_budget_failed"),
        ("paper_shadow_matches_replay", "session.rollout.paper_shadow_replay_mismatch"),
        ("cost_stress_1_5x_passed", "session.rollout.cost_stress_1_5x_failed"),
        ("cost_stress_2_0x_passed", "session.rollout.cost_stress_2_0x_failed"),
        ("improves_net_value_or_reduces_risk", "session.rollout.no_incremental_value"),
        ("rollback_tested", "session.rollout.rollback_not_tested"),
    )

    for field_name, reason in required_fields:
        evidence = replace(base, **{field_name: False})
        status = evaluate_session_rollout_stage(SessionRolloutStage.PAPER_VETO_REDUCTION_ONLY, validation=validation_from_evidence(evidence))
        assert status.enabled is False
        assert reason in status.reason_codes


def test_session_final_acceptance_valid_evidence_enables_paper_stages_but_not_live() -> None:
    evidence = build_session_promotion_evidence(evidence_id="session-final-valid", created_at=NOW)
    validation = validation_from_evidence(evidence)

    for stage in SESSION_ROLLOUT_STAGES:
        status = evaluate_session_rollout_stage(stage, validation=validation)
        assert status.enabled is True

    control = evaluate_session_rollout_control(SessionRolloutStage.BROADER_PAPER_USE, validation=validation)
    assert control.order_authority.may_route_new_entries is True
    assert control.order_authority.may_increase_risk is False
    assert control.order_authority.may_bypass_global_gates is False
    assert control.order_authority.live_trading_allowed is False
    assert control.live_trading_allowed is False


def test_session_final_acceptance_health_failure_removes_order_authority_only() -> None:
    evidence = build_session_promotion_evidence(evidence_id="session-final-valid", created_at=NOW)
    validation = validation_from_evidence(evidence)
    control = evaluate_session_rollout_control(
        SessionRolloutStage.PAPER_VETO_REDUCTION_ONLY,
        validation=validation,
        health=SessionHealthStatus(parity_ok=False, reason_codes=("session.health.parity_failed",)),
    )

    assert control.effective_stage == SessionRolloutStage.PAPER_SHADOW.value
    assert control.order_authority.order_affecting is False
    assert control.classification_enabled is True
    assert control.diagnostic_persistence_enabled is True


def test_session_final_acceptance_frontend_or_cross_algorithm_evidence_is_not_trusted() -> None:
    evidence = SessionPromotionEvidence(
        evidence_id="frontend-evidence",
        generated_by="frontend",
        created_at=NOW,
        all_session_tests_passed=True,
        point_in_time_leakage_absent=True,
        replay_backtest_paper_parity_passed=True,
        dst_and_early_close_tests_passed=True,
        stale_missing_data_fail_closed=True,
        transition_stability_passed=True,
        unknown_state_occupancy_acceptable=True,
        latency_within_budget=True,
        paper_shadow_matches_replay=True,
        cost_stress_1_5x_passed=True,
        cost_stress_2_0x_passed=True,
        improves_net_value_or_reduces_risk=True,
        rollback_tested=True,
        report_ids=("session-report",),
    )
    status = evaluate_session_rollout_stage(SessionRolloutStage.PAPER_VETO_REDUCTION_ONLY, validation=validation_from_evidence(evidence))

    assert status.enabled is False
    assert "session.rollout.untrusted_evidence_source" in status.reason_codes
