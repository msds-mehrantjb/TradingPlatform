from __future__ import annotations

from dataclasses import replace

from backend.app.algorithms.wca.contracts import WcaRuntimeMode
from backend.app.algorithms.wca.order_validation import validate_wca_final_order
from backend.app.algorithms.wca.rollout import WcaRolloutEvidence, WcaRolloutFlags, evaluate_wca_automatic_paper_rollout
from backend.tests.test_wca_phase5_final_order_validation import valid_context, valid_decision
from backend.tests.test_wca_step20_rollout import complete_evidence


def test_wca_paper_execution_env_flag_alone_does_not_permit_automatic_rollout() -> None:
    decision = evaluate_wca_automatic_paper_rollout(
        flags=WcaRolloutFlags(paper_execution_enabled=True),
        evidence=WcaRolloutEvidence(),
    )

    assert decision.permitted is False
    assert decision.stage == "DISABLED"
    assert "wca.rollout.automatic_paper.stage_not_permitted.disabled" in decision.reason_codes


def test_configured_limited_stage_caps_highest_evidenced_automatic_rollout() -> None:
    decision = evaluate_wca_automatic_paper_rollout(
        flags=WcaRolloutFlags(paper_execution_enabled=True),
        evidence=complete_evidence(),
        configured_stage="LIMITED_AUTOMATIC_PAPER",
    )

    assert decision.permitted is True
    assert decision.stage == "LIMITED_AUTOMATIC_PAPER"


def test_automatic_order_boundary_requires_rollout_stage_and_evidence() -> None:
    decision = valid_decision()
    context = replace(
        valid_context(decision, runtime_mode=WcaRuntimeMode.AUTOMATIC_PAPER),
        rollout_stage="MANUAL_PAPER",
        rollout_evidence_revision="",
        rollout_evidence_hash="",
    )

    result = validate_wca_final_order(decision, context)

    assert result.valid is False
    assert "wca.order_validation.rollout_stage_not_automatic_paper" in result.reason_codes
    assert "wca.order_validation.rollout_evidence_missing" in result.reason_codes


def test_limited_automatic_paper_enforces_quantity_and_symbol_caps() -> None:
    decision = valid_decision()
    context = replace(
        valid_context(decision, runtime_mode=WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER),
        rollout_stage="LIMITED_AUTOMATIC_PAPER",
        rollout_allowed_symbols=("QQQ",),
        rollout_max_quantity=1,
    )

    result = validate_wca_final_order(decision, context)

    assert result.valid is False
    assert "wca.order_validation.rollout_limited_symbol_not_allowed" in result.reason_codes
    assert "wca.order_validation.rollout_limited_quantity_exceeded" in result.reason_codes


def test_limited_automatic_paper_enforces_windows_daily_caps_and_strategy_set() -> None:
    decision = valid_decision()
    context = replace(
        valid_context(decision, runtime_mode=WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER),
        rollout_stage="LIMITED_AUTOMATIC_PAPER",
        rollout_allowed_entry_windows=("03:00-03:30 America/New_York",),
        rollout_allowed_strategy_ids=("NOT_A_WCA_STRATEGY",),
        rollout_max_daily_trades=1,
        trades_today=1,
        rollout_max_daily_loss=25.0,
        realized_daily_loss=25.0,
    )

    result = validate_wca_final_order(decision, context)

    assert result.valid is False
    assert "wca.order_validation.rollout_limited_entry_window_closed" in result.reason_codes
    assert "wca.order_validation.rollout_limited_strategy_set_not_allowed" in result.reason_codes
    assert "wca.order_validation.rollout_limited_daily_trade_cap_exceeded" in result.reason_codes
    assert "wca.order_validation.rollout_limited_daily_loss_cap_exceeded" in result.reason_codes
