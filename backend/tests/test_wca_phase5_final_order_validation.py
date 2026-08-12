from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.algorithms.wca.contracts import ProposedOrder, WcaOrderStatus, WcaOrderValidationContext, WcaRuntimeMode, WcaSide
from backend.app.algorithms.wca.order_validation import (
    WCA_FINAL_PRE_OUTBOX_VALIDATION_PASSED,
    WCA_ORDER_VALIDATION_EXIT_CRITICAL_ALERT,
    assert_wca_final_pre_outbox_validation,
    validate_wca_final_order,
)
from backend.app.algorithms.wca.paper_broker import WcaDeterministicPaperBroker, WcaPaperBrokerOutboxAdapter, build_wca_paper_broker_request
from backend.app.algorithms.wca.repository import WcaExecutionOutboxRecord, WcaSqliteRepository
from backend.tests.test_wca_step6_inventory_persistence import decision_with_order


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (
        ("account_id", "shared", "wca.order_validation.account_mismatch"),
        ("broker_endpoint", "live", "wca.order_validation.paper_endpoint_required"),
        ("runtime_mode", WcaRuntimeMode.SHADOW, "wca.order_validation.runtime_stage_not_executable_paper"),
        ("automatic_paper_enabled", False, "wca.order_validation.automatic_paper_feature_flag_disabled"),
        ("market_is_open", False, "wca.order_validation.market_closed"),
        ("allowed_session_window", False, "wca.order_validation.entry_session_window_closed"),
        ("candle_freshness_seconds", 0, "wca.order_validation.stale_finalized_candle"),
        ("data_ready", False, "wca.order_validation.data_not_ready"),
        ("inventory_consistent", False, "wca.order_validation.inventory_inconsistent"),
        ("conflicting_wca_position", True, "wca.order_validation.conflicting_wca_position"),
        ("pending_wca_entry", True, "wca.order_validation.pending_wca_entry"),
        ("cooldown_active", True, "wca.order_validation.cooldown_active"),
        ("circuit_breaker_open", True, "wca.order_validation.circuit_breaker_open"),
        ("max_approved_quantity", 1, "wca.order_validation.maximum_approved_quantity_exceeded"),
        ("available_buying_power", 1.0, "wca.order_validation.buying_power_exceeded"),
        ("max_position_value", 1.0, "wca.order_validation.max_position_exceeded"),
        ("trades_today", 999, "wca.order_validation.max_daily_trades_exceeded"),
        ("realized_daily_loss", 1000.0, "wca.order_validation.max_daily_loss_exceeded"),
        ("max_spread_percent", 0.000001, "wca.order_validation.spread_limit_exceeded"),
        ("average_one_minute_volume", 1.0, "wca.order_validation.participation_limit_exceeded"),
        ("expected_net_edge", -1.0, "wca.order_validation.expected_net_edge_not_met"),
        ("order_type", "MARKET", "wca.order_validation.invalid_order_type"),
        ("time_in_force", "GTC", "wca.order_validation.invalid_time_in_force"),
        ("protective_exit_plan_present", False, "wca.order_validation.missing_protective_exit_plan"),
    ),
)
def test_entry_final_validation_matrix(field: str, value, reason: str) -> None:
    decision = valid_decision()
    context = replace(valid_context(decision, runtime_mode=WcaRuntimeMode.AUTOMATIC_PAPER), **{field: value})
    if field == "realized_daily_loss":
        context = replace(context, max_daily_loss=1000.0)
    if field == "trades_today":
        context = replace(context, max_daily_trades=999)
    if field == "candle_freshness_seconds":
        context = replace(context, evaluation_timestamp=decision.decision_timestamp + timedelta(seconds=1))

    result = validate_wca_final_order(decision, context)

    assert result.valid is False
    assert reason in result.reason_codes


def test_risk_reducing_exit_ignores_entry_only_blocks_and_optional_context() -> None:
    decision = exit_decision()
    context = valid_context(decision, runtime_mode=WcaRuntimeMode.AUTOMATIC_PAPER)
    context = replace(
        context,
        is_risk_reducing_exit=True,
        quote_freshness_seconds=None,
        market_is_open=False,
        allowed_session_window=False,
        data_ready=False,
        trades_today=999,
        max_daily_trades=999,
        realized_daily_loss=1000,
        max_daily_loss=1000,
        cooldown_active=True,
        circuit_breaker_open=True,
        expected_net_edge=-1,
        minimum_net_edge=0,
    )

    result = validate_wca_final_order(decision, context)

    assert result.valid is True


def test_technically_impossible_exit_opens_critical_alert() -> None:
    decision = exit_decision()
    context = replace(valid_context(decision), is_risk_reducing_exit=True, broker_endpoint="live", quote_freshness_seconds=None)

    result = validate_wca_final_order(decision, context)

    assert result.valid is False
    assert "wca.order_validation.paper_endpoint_required" in result.reason_codes
    assert WCA_ORDER_VALIDATION_EXIT_CRITICAL_ALERT in result.reason_codes


def test_final_pre_outbox_validation_runs_after_overrides_and_blocks_reservation() -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    decision = valid_decision()
    assert decision.proposed_order is not None
    adjusted_price = decision.market_snapshot.candles[-1].close * 1.25
    adjusted = decision.proposed_order.model_copy(update={"trigger_price": adjusted_price, "limit_price": adjusted_price, "stop_price": adjusted_price - 1, "target_price": adjusted_price + 2})
    decision = decision.model_copy(update={"proposed_order": adjusted})
    request = build_wca_paper_broker_request(adjusted)

    with pytest.raises(ValueError, match="wca.order_validation.unreasonable_price"):
        repository.reserve_decision_order_and_outbox(
            decision,
            run_id="phase5",
            account_id=adjusted.account_id,
            idempotency_key=adjusted.idempotency_key or "phase5-key",
            client_order_id=request.client_order_id,
            request_payload=request.model_dump(mode="json"),
            final_validation_context=valid_context(decision),
        )

    with sqlite3.connect(repository.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wca_execution_outbox").fetchone()[0] == 0


def test_successful_reservation_persists_final_validation_marker_and_broker_submits() -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    decision = valid_decision()
    assert decision.proposed_order is not None
    request = build_wca_paper_broker_request(decision.proposed_order)

    reservation = repository.reserve_decision_order_and_outbox(
        decision,
        run_id="phase5",
        account_id=decision.proposed_order.account_id,
        idempotency_key=decision.proposed_order.idempotency_key or "phase5-key",
        client_order_id=request.client_order_id,
        request_payload=request.model_dump(mode="json"),
        final_validation_context=valid_context(decision),
    )
    submission = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, WcaDeterministicPaperBroker(), owner_id="phase5")

    assert reservation.created is True
    assert WCA_FINAL_PRE_OUTBOX_VALIDATION_PASSED in reservation.proposed_order.reason_codes
    assert submission.submitted is True


def test_broker_refuses_legacy_outbox_without_final_validation_marker() -> None:
    decision = valid_decision()
    assert decision.proposed_order is not None
    record = WcaExecutionOutboxRecord(
        outbox_id="legacy-outbox",
        account_id="paper",
        symbol="SPY",
        decision_id="legacy-decision",
        run_id="legacy-run",
        order_intent_id="legacy-intent",
        idempotency_key="legacy-key",
        client_order_id="legacy-client",
        status=WcaOrderStatus.OUTBOX_RESERVED,
        version=1,
        decision=decision,
        proposed_order=decision.proposed_order,
        request_payload=build_wca_paper_broker_request(decision.proposed_order).model_dump(mode="json"),
    )
    repository = LegacyOutboxRepository(record)
    broker = WcaDeterministicPaperBroker()

    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="phase5")

    assert result.submitted is False
    assert broker.submit_count == 0
    assert "wca.paper_broker.final_validation_missing" in result.reason_codes


def valid_decision():
    return decision_with_order("phase5-decision", "phase5-intent", "phase5-key")


def exit_decision():
    decision = valid_decision()
    assert decision.proposed_order is not None
    exit_order = decision.proposed_order.model_copy(
        update={
            "side": WcaSide.SELL,
            "trigger_price": decision.market_snapshot.candles[-1].close,
            "limit_price": decision.market_snapshot.candles[-1].close,
            "stop_price": decision.market_snapshot.candles[-1].close + 1,
            "target_price": decision.market_snapshot.candles[-1].close - 2,
        }
    )
    sizing = decision.sizing.model_copy(update={"side": WcaSide.SELL, "stop_price": exit_order.stop_price, "target_price": exit_order.target_price})
    return decision.model_copy(update={"proposed_order": exit_order, "sizing": sizing})


def valid_context(decision, *, runtime_mode: WcaRuntimeMode = WcaRuntimeMode.MANUAL_PAPER) -> WcaOrderValidationContext:
    rollout_fields = (
        {
            "rollout_stage": "AUTOMATIC_PAPER",
            "rollout_evidence_revision": "wca_evidence_rollout_v2:test",
            "rollout_evidence_hash": "test-rollout-evidence-hash",
        }
        if runtime_mode in {WcaRuntimeMode.LOCAL_AUTOMATIC_PAPER, WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER, WcaRuntimeMode.AUTOMATIC_PAPER}
        else {}
    )
    return WcaOrderValidationContext(
        evaluation_timestamp=decision.decision_timestamp,
        account_id=decision.proposed_order.account_id,
        broker_endpoint="paper",
        runtime_mode=runtime_mode,
        **rollout_fields,
        requires_executable_paper_stage=True,
        automatic_paper_enabled=True,
        market_is_open=True,
        allowed_session_window=True,
        candle_freshness_seconds=120,
        data_ready=True,
        inventory_consistent=True,
        max_approved_quantity=1000,
        order_type="LIMIT",
        time_in_force="DAY",
        protective_exit_plan_present=True,
        quote_freshness_seconds=15,
        available_buying_power=100_000,
        account_equity=100_000,
        max_position_value=100_000,
        realized_daily_loss=0,
        max_daily_loss=1000,
        trades_today=0,
        max_daily_trades=10,
        max_spread_percent=10,
        average_one_minute_volume=100_000,
        max_participation_percent=10,
        expected_net_edge=1,
        minimum_net_edge=0,
        idempotency_required=True,
        new_entry_permitted=True,
        risk_reducing_exit_permitted=True,
    )


class LegacyOutboxRepository:
    def __init__(self, record: WcaExecutionOutboxRecord) -> None:
        self.record = record
        self.updated = None

    def claim_next_execution_outbox(self, *, owner_id: str):
        return self.record

    def update_execution_outbox_state(self, **kwargs):
        self.updated = kwargs
        return True


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"wca-phase5-{uuid4().hex}.sqlite"
