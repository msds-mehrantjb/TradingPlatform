from __future__ import annotations

from pathlib import Path

from backend.app.algorithms.wca.rollout import (
    WCA_REQUIRED_ROLLOUT_EVIDENCE,
    WcaRolloutEvidence,
    WcaRolloutFlags,
    WcaRolloutValidation,
    evaluate_wca_rollout_stage,
    rollback_configuration,
    rollback_wca_automatic_paper_stage,
    rollback_wca_rollout,
    wca_rollout_status,
)
from backend.tests.test_wca_step20_rollout import MemoryStore, complete_evidence, fully_validated_rollout


ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "wca"


def test_phase15_required_promotion_evidence_is_exact_and_not_fabricated_from_code_completion() -> None:
    assert WCA_REQUIRED_ROLLOUT_EVIDENCE == {
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

    validation_only = WcaRolloutValidation(
        legacy_parity_passed=True,
        corrected_catalog_shadow_passed=True,
        full_history_backtest_passed=True,
        walk_forward_passed=True,
        untouched_holdout_passed=True,
        paper_recommendation_passed=True,
        tests_passed=True,
        paper_execution_passed=True,
        paper_trading_stable=False,
        multiple_market_conditions_passed=False,
        multi_week_paper_validation_passed=False,
        legacy_removal_accepted=True,
    )
    blocked = evaluate_wca_rollout_stage(
        "AUTOMATIC_PAPER",
        flags=WcaRolloutFlags(paper_execution_enabled=True),
        evidence=WcaRolloutEvidence.from_validation(validation_only),
    )

    assert blocked.enabled is False
    assert "wca.rollout.missing_persisted_evidence.minimum_paper_observation_duration" in blocked.reason_codes
    assert "wca.rollout.paper_observation_duration_too_short" in blocked.reason_codes


def test_user_or_api_status_cannot_bypass_rollout_evidence_or_feature_flag() -> None:
    status = wca_rollout_status(flags=WcaRolloutFlags(paper_execution_enabled=True), validation=fully_validated_rollout())

    assert status["current_stage"] != "AUTOMATIC_PAPER"
    assert status["limited_automatic_paper_allowed"] is False
    assert status["paper_execution_allowed"] is False
    assert any(row["permission"] == "blocked" for row in status["stages"] if row["stage"] == "AUTOMATIC_PAPER")

    enabled = evaluate_wca_rollout_stage(
        "AUTOMATIC_PAPER",
        flags=WcaRolloutFlags(paper_execution_enabled=True),
        evidence=complete_evidence(),
    )
    assert enabled.enabled is True

    flag_blocked = evaluate_wca_rollout_stage(
        "AUTOMATIC_PAPER",
        flags=WcaRolloutFlags(paper_execution_enabled=False),
        evidence=complete_evidence(),
    )
    assert flag_blocked.enabled is False
    assert "wca.rollout.automatic_paper_flag_disabled" in flag_blocked.reason_codes


def test_rollback_from_automatic_stages_to_shadow_or_disabled_requires_safe_state_and_repromotion() -> None:
    to_shadow = rollback_wca_automatic_paper_stage(
        source_stage="AUTOMATIC_PAPER",
        target_stage="SHADOW",
        entry_orders_cancelled=True,
        broker_local_state_reconciled=True,
        safe_state_verified=True,
    )
    to_disabled = rollback_wca_automatic_paper_stage(
        source_stage="LIMITED_AUTOMATIC_PAPER",
        target_stage="DISABLED",
        entry_orders_cancelled=False,
        broker_local_state_reconciled=False,
        safe_state_verified=False,
    )

    assert to_shadow.new_entries_stopped is True
    assert to_shadow.wca_entry_orders_cancelled is True
    assert to_shadow.protective_exits_preserved is True
    assert to_shadow.broker_local_state_reconciled is True
    assert to_shadow.wca_inventory_preserved is True
    assert to_shadow.evidence_preserved is True
    assert to_shadow.safe_state_verified is True
    assert to_shadow.explicit_repromotion_required is True
    assert "wca.rollout.rollback.safe_state_verified" in to_shadow.reason_codes

    assert to_disabled.target_stage == "DISABLED"
    assert "wca.rollout.rollback.entry_order_cancellation_required" in to_disabled.reason_codes
    assert "wca.rollout.rollback.reconciliation_required" in to_disabled.reason_codes
    assert "wca.rollout.rollback.safe_state_verification_required" in to_disabled.reason_codes


def test_persisted_rollback_preserves_inventory_evidence_and_requires_repromotion() -> None:
    store = MemoryStore()
    store.write_snapshot(
        "wca.rollout.active",
        {"phase": "AUTOMATIC_PAPER", "state_version": "automatic-v1", "evidence_ids": tuple(sorted(WCA_REQUIRED_ROLLOUT_EVIDENCE))},
    )
    store.write_snapshot(
        "wca.rollout.previous_valid",
        {"phase": "MANUAL_PAPER", "state_version": "manual-v1", "evidence_ids": tuple(sorted(WCA_REQUIRED_ROLLOUT_EVIDENCE))},
    )

    restored = rollback_wca_rollout(store, target_stage="DISABLED")

    assert restored["phase"] == "DISABLED"
    assert restored["historical_records_deleted"] is False
    assert restored["rollback_result"]["wca_inventory_preserved"] is True
    assert restored["rollback_result"]["evidence_preserved"] is True
    assert restored["rollback_result"]["explicit_repromotion_required"] is True
    assert restored["rollback_configuration"] == rollback_configuration()


def test_phase15_documentation_files_cover_operations_without_secrets() -> None:
    required_docs = {
        "final_acceptance_checklist.md",
        "background_runtime.md",
        "authoritative_inventory_persistence.md",
        "paper_broker_runbook.md",
        "reconciliation_runbook.md",
        "incident_recovery_runbook.md",
        "rollout_rollback_runbook.md",
        "environment_variables.md",
    }
    required_phrases = (
        "dedicated WCA paper account",
        "Start WCA workers",
        "Stop WCA workers",
        "Run replay",
        "Run shadow mode",
        "Run manual paper",
        "Enable limited automatic paper",
        "Inspect inventory",
        "Inspect orders and fills",
        "Inspect reconciliation",
        "Diagnose a blocked entry",
        "Recover after a crash",
        "Perform rollback",
        "Verify end-of-session flatness",
    )
    combined = ""
    for doc_name in required_docs:
        path = DOCS / doc_name
        assert path.exists(), doc_name
        text = path.read_text(encoding="utf-8")
        combined += "\n" + text
        lowered = text.lower()
        assert "secret-key-value" not in lowered
        assert "real-secret" not in lowered
        assert "sk_live" not in lowered
        assert "pk_live" not in lowered

    for phrase in required_phrases:
        assert phrase in combined
