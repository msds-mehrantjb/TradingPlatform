from __future__ import annotations

import json
import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import uuid4

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaOrderStatus, WcaOrderValidationContext, WcaRuntimeMode, WcaSide
from backend.app.algorithms.wca.local_paper_account import WcaLocalPaperAccount, WcaLocalPaperLotSnapshot, WcaLocalPaperOrderSnapshot
from backend.app.algorithms.wca.local_paper_broker import WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED, WcaLocalPaperBroker, WcaLocalPaperFillModel
from backend.app.algorithms.wca.local_paper_risk import WcaLocalPaperRiskContext, WcaLocalPaperRiskManager, WcaLocalPaperRiskPolicy
from backend.app.algorithms.wca.paper_broker import (
    WCA_REAL_MONEY_ENDPOINTS_AVAILABLE,
    WcaDeterministicPaperBroker,
    WcaPaperBrokerAck,
    WcaPaperBrokerFill,
    WcaPaperBrokerOrderRequest,
    WcaPaperBrokerOutboxAdapter,
    build_wca_paper_broker_request,
    cancel_wca_paper_order,
    redact_secret_payload,
    replace_wca_paper_order_requires_new_intent,
)
from backend.app.algorithms.wca.paper_account import (
    WCA_ALPACA_PAPER_ACCOUNT_ID,
    WCA_ALPACA_PAPER_API_KEY_ID,
    WCA_ALPACA_PAPER_API_SECRET_KEY,
    WCA_ALPACA_PAPER_BASE_URL,
    WCA_AUTOMATIC_PAPER_ENABLED,
    WCA_LOCAL_PAPER_ACCOUNT_ID,
    WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
    WCA_LOCAL_PAPER_STARTING_BALANCE,
    WCA_REQUIRED_ALPACA_PAPER_BASE_URL,
    validate_wca_automatic_paper_account,
)
from backend.app.algorithms.wca.repository import WcaGlobalRiskApprovalRecord, WcaInventoryLedgerEvent, WcaSqliteRepository
from backend.app.algorithms.wca.service import WcaService
from backend.tests.test_wca_paper_execution_pipeline import decision_with_order


def test_atomic_decision_intent_and_outbox_reservation_does_not_submit() -> None:
    repository = repository_for_step10()
    decision, request = reserve(repository)

    with sqlite3.connect(repository.path) as conn:
        decision_count = conn.execute("SELECT COUNT(*) FROM wca_decisions WHERE decision_id = ?", (decision.decision_id,)).fetchone()[0]
        intent_count = conn.execute("SELECT COUNT(*) FROM wca_order_intents WHERE idempotency_key = ?", (request.idempotency_key,)).fetchone()[0]
        outbox = conn.execute("SELECT status, submitted_at, client_order_id FROM wca_execution_outbox WHERE idempotency_key = ?", (request.idempotency_key,)).fetchone()

    assert decision_count == 1
    assert intent_count == 1
    assert outbox == (WcaOrderStatus.OUTBOX_RESERVED.value, None, request.client_order_id)


def test_acknowledgement_is_persisted_separately_from_submission_request() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository)
    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, WcaDeterministicPaperBroker(), owner_id="step10")

    assert result.submitted is True
    assert result.state == WcaOrderStatus.BROKER_ACKNOWLEDGED.value
    with sqlite3.connect(repository.path) as conn:
        outbox = conn.execute("SELECT status, submitted_at, acknowledged_at, request_payload_json, response_payload_json FROM wca_execution_outbox WHERE idempotency_key = ?", (request.idempotency_key,)).fetchone()
        broker = conn.execute("SELECT status, client_order_id, request_payload_json, response_payload_json FROM wca_broker_orders WHERE idempotency_key = ?", (request.idempotency_key,)).fetchone()

    assert outbox[0] == WcaOrderStatus.BROKER_ACKNOWLEDGED.value
    assert outbox[1] is not None
    assert outbox[2] is not None
    assert request.client_order_id in broker[1]
    assert "client_order_id" in broker[2]
    assert "ACKNOWLEDGED" in broker[3]


def test_rejection_timeout_and_duplicate_transition_to_explicit_states() -> None:
    rejected_repo = repository_for_step10()
    _, rejected_request = reserve(rejected_repo, suffix="rejected")
    rejected = WcaPaperBrokerOutboxAdapter().process_next_outbox(rejected_repo, WcaDeterministicPaperBroker(ack_status="REJECTED"), owner_id="step10")

    timeout_repo = repository_for_step10()
    _, timeout_request = reserve(timeout_repo, suffix="timeout")
    timeout = WcaPaperBrokerOutboxAdapter().process_next_outbox(timeout_repo, WcaDeterministicPaperBroker(timeout=True), owner_id="step10")

    duplicate_repo = repository_for_step10()
    _, duplicate_request = reserve(duplicate_repo, suffix="duplicate")
    duplicate = WcaPaperBrokerOutboxAdapter().process_next_outbox(duplicate_repo, WcaDeterministicPaperBroker(ack_status="DUPLICATE"), owner_id="step10")

    assert rejected.state == WcaOrderStatus.REJECTED.value
    assert timeout.state == WcaOrderStatus.SUBMISSION_UNKNOWN.value
    assert duplicate.state == WcaOrderStatus.RECONCILIATION_REQUIRED.value
    assert state_for(rejected_repo, rejected_request.idempotency_key) == WcaOrderStatus.REJECTED.value
    assert state_for(timeout_repo, timeout_request.idempotency_key) == WcaOrderStatus.SUBMISSION_UNKNOWN.value
    assert state_for(duplicate_repo, duplicate_request.idempotency_key) == WcaOrderStatus.RECONCILIATION_REQUIRED.value


def test_partial_and_delayed_fills_update_wca_owned_inventory_once() -> None:
    partial_repo = repository_for_step10()
    _, partial_request = reserve(partial_repo, suffix="partial")
    partial_fill = WcaPaperBrokerFill(
        fill_id="partial-fill",
        client_order_id=partial_request.client_order_id,
        broker_order_id=f"paper-{partial_request.client_order_id}",
        filled_quantity=2,
        remaining_quantity=3,
        average_fill_price=partial_request.limit_price,
    )
    partial = WcaPaperBrokerOutboxAdapter().process_next_outbox(partial_repo, WcaDeterministicPaperBroker(fill=partial_fill), owner_id="step10")

    delayed_repo = repository_for_step10()
    _, delayed_request = reserve(delayed_repo, suffix="delayed")
    acknowledged = WcaPaperBrokerOutboxAdapter().process_next_outbox(delayed_repo, WcaDeterministicPaperBroker(), owner_id="step10")
    delayed_fill = WcaPaperBrokerFill(
        fill_id="delayed-fill",
        client_order_id=delayed_request.client_order_id,
        broker_order_id=f"paper-{delayed_request.client_order_id}",
        filled_quantity=delayed_request.quantity,
        remaining_quantity=0,
        average_fill_price=delayed_request.limit_price,
    )
    delayed = WcaPaperBrokerOutboxAdapter().apply_delayed_fill(delayed_repo, acknowledged.outbox_record, delayed_fill)

    assert partial.state == WcaOrderStatus.PARTIALLY_FILLED.value
    assert delayed.state == WcaOrderStatus.FILLED.value
    assert fill_count(partial_repo, "partial-fill") == 1
    assert fill_count(delayed_repo, "delayed-fill") == 1


def test_partial_fill_resizes_reserved_risk_for_remaining_quantity() -> None:
    repository = repository_for_step10()
    decision, request = reserve(repository, suffix="partial-risk")
    before = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=request.account_id, symbol=request.symbol)
    partial_fill = WcaPaperBrokerFill(
        fill_id="partial-risk-fill",
        client_order_id=request.client_order_id,
        broker_order_id=f"paper-{request.client_order_id}",
        filled_quantity=2,
        remaining_quantity=max(0, request.quantity - 2),
        average_fill_price=request.limit_price,
    )

    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, WcaDeterministicPaperBroker(fill=partial_fill), owner_id="step10")
    after = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=request.account_id, symbol=request.symbol)

    assert result.state == WcaOrderStatus.PARTIALLY_FILLED.value
    assert after.reserved_risk < before.reserved_risk
    assert after.reserved_risk > 0


def test_idempotent_retry_never_generates_second_economic_order() -> None:
    repository = repository_for_step10()
    decision, request = reserve(repository)
    duplicate = repository.reserve_decision_order_and_outbox(
        decision,
        run_id="step10-run",
        account_id=request.account_id,
        idempotency_key=request.idempotency_key,
        client_order_id=request.client_order_id,
        request_payload=request.model_dump(mode="json"),
        final_validation_context=validation_context(decision, request),
    )
    broker = WcaDeterministicPaperBroker()
    first = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10")
    second = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10")

    assert duplicate.created is False
    assert first.submitted is True
    assert second.submitted is False
    assert broker.submit_count == 1


def test_existing_client_order_is_reconciled_before_duplicate_submit() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="duplicate-existing")
    broker = LookupBroker(existing_order=alpaca_order_payload(request))

    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10")

    assert result.submitted is False
    assert broker.submit_count == 0
    assert result.state == WcaOrderStatus.RECONCILIATION_REQUIRED.value
    assert "wca.paper_broker.duplicate_client_order_reconciled" in result.reason_codes


def test_timeout_existing_client_order_is_reconciled_before_retry() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="timeout-existing")
    broker = LookupBroker(existing_order=alpaca_order_payload(request), timeout=True, found_after_submit=True)

    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10")

    assert result.submitted is False
    assert broker.submit_count == 1
    assert result.state == WcaOrderStatus.RECONCILIATION_REQUIRED.value
    assert "wca.paper_broker.timeout_existing_order_reconciled" in result.reason_codes


def test_stale_outbox_is_cancelled_without_submission() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="stale")
    broker = WcaDeterministicPaperBroker()

    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(
        repository,
        broker,
        owner_id="step10",
        pre_submit_check=lambda _record, _request: (False, ("wca.runtime.pre_submit.command_deadline_expired",)),
    )

    assert result.submitted is False
    assert broker.submit_count == 0
    assert result.state == WcaOrderStatus.CANCELLED.value
    assert state_for(repository, request.idempotency_key) == WcaOrderStatus.CANCELLED.value


def test_atomic_reservation_persists_phase8_control_global_risk_and_state_evidence() -> None:
    repository = repository_for_step10()
    decision = decision_with_order()
    assert decision.proposed_order is not None
    proposed = decision.proposed_order.model_copy(
        update={
            "idempotency_key": "step10-phase8-evidence",
            "account_id": "paper-step10",
            "runtime_control_revision": 7,
            "runtime_control_hash": "control-hash-7",
        }
    )
    decision = decision.model_copy(
        update={
            "proposed_order": proposed,
            "runtime_control_revision": 7,
            "runtime_control_hash": "control-hash-7",
        }
    )
    request = build_wca_paper_broker_request(proposed)
    approval = WcaGlobalRiskApprovalRecord(
        decision_id=decision.decision_id,
        account_id=proposed.account_id,
        symbol=proposed.symbol,
        status="PASS",
        global_risk_decision_id="global-risk-phase8",
        evaluated_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        entry_permitted=True,
        risk_reducing_exit_permitted=True,
        requested_quantity=proposed.quantity,
        allowed_quantity=proposed.quantity,
        approved_risk_dollars=12.34,
        reason_codes=("wca.global_risk.durable_approval_persisted",),
        global_state_hash="global-state-hash",
        global_state_revision="global-state-revision",
        payload={},
    )

    repository.reserve_decision_order_and_outbox(
        decision,
        run_id="step10-run",
        account_id=proposed.account_id,
        idempotency_key=proposed.idempotency_key,
        client_order_id=request.client_order_id,
        request_payload=request.model_dump(mode="json"),
        final_validation_context=validation_context(decision, request),
        global_risk_approval=approval,
        authoritative_state_hash="authoritative-state-hash",
    )
    record = repository.list_execution_outbox_records(account_id=proposed.account_id)[0]

    assert record.runtime_control_revision == 7
    assert record.runtime_control_hash == "control-hash-7"
    assert record.global_risk_decision_id == "global-risk-phase8"
    assert record.global_risk_state_hash == "global-state-hash"
    assert record.global_risk_state_revision == "global-state-revision"
    assert record.authoritative_state_hash == "authoritative-state-hash"


def test_cancel_replace_redaction_and_live_endpoint_guards() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository)
    cancelled = cancel_wca_paper_order(
        repository,
        outbox_id=f"wca-outbox-{request.order_intent_id}",
        cancellation_idempotency_key="cancel-step10",
        original_idempotency_key=request.idempotency_key,
    )
    redacted = redact_secret_payload({"api_key": "secret", "nested": {"access_token": "token"}, "client_order_id": request.client_order_id})

    assert cancelled is True
    assert state_for(repository, request.idempotency_key) == WcaOrderStatus.CANCELLED.value
    assert redacted["api_key"] == "***REDACTED***"
    assert redacted["nested"]["access_token"] == "***REDACTED***"
    assert redacted["client_order_id"] == request.client_order_id
    assert WCA_REAL_MONEY_ENDPOINTS_AVAILABLE is False
    try:
        replace_wca_paper_order_requires_new_intent(replacement_idempotency_key=request.idempotency_key, original_idempotency_key=request.idempotency_key)
    except ValueError as exc:
        assert "new idempotency" in str(exc)
    else:
        raise AssertionError("replacement with same idempotency key must fail")


def test_wca_local_paper_broker_fills_from_local_account_and_keeps_wca_inventory_isolated() -> None:
    repository = repository_for_step10()
    decision, request = reserve(repository, suffix="local-paper")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, starting_balance=123_456.0)

    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-paper")
    resting_snapshot = broker.refresh_account_snapshot()
    resting_projection = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=request.account_id, symbol=request.symbol)

    assert result.state == WcaOrderStatus.ACKNOWLEDGED
    assert result.submitted is True
    assert resting_projection.open_quantity == 0
    assert list(resting_snapshot.positions) == []

    fills = fill_local_entry(repository, broker, request)
    snapshot = broker.refresh_account_snapshot()
    projection = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=request.account_id, symbol=request.symbol)

    assert len(fills) == 1
    assert snapshot.sourceAuthority == WCA_LOCAL_PAPER_SOURCE_AUTHORITY
    assert snapshot.accountId == request.account_id
    assert snapshot.equity == 123_456.0
    assert snapshot.buyingPower >= 0
    assert len(snapshot.positions) == 1
    assert snapshot.positions[0].algorithmId == WCA_ALGORITHM_ID
    assert snapshot.positions[0].positionOwner == WCA_ALGORITHM_ID
    assert projection.open_quantity == request.quantity
    assert projection.reserved_risk >= 0
    assert all(order.algorithmId == WCA_ALGORITHM_ID for order in snapshot.pendingOrders)
    assert any(order.exitOwner == WCA_ALGORITHM_ID for order in snapshot.pendingOrders)

def test_wca_local_paper_broker_does_not_reset_starting_balance_on_restart() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-paper-restart-balance")
    broker = WcaLocalPaperBroker(
        repository=repository,
        account_id=request.account_id,
        symbol=request.symbol,
        starting_balance=123_456.0,
    )

    restarted = WcaLocalPaperBroker(
        repository=repository,
        account_id=request.account_id,
        symbol=request.symbol,
        starting_balance=999_999.0,
    )
    snapshot = restarted.refresh_account_snapshot()
    account_payload = restarted.refresh_account()

    assert snapshot.equity == 123_456.0
    assert account_payload["cash"] == 123_456.0
    with sqlite3.connect(repository.path) as conn:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM wca_inventory_ledger
            WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?
              AND event_type = 'DAILY_STATE_RESET'
            """,
            (WCA_ALGORITHM_ID, request.account_id, request.symbol),
        ).fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0])["starting_balance"] == 123_456.0
    broker.close()
    restarted.close()

def test_wca_local_paper_restart_restores_positions_pnl_and_trade_count() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-paper-restart-state")
    broker = WcaLocalPaperBroker(
        repository=repository,
        account_id=request.account_id,
        symbol=request.symbol,
        starting_balance=123_456.0,
    )
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-restart-state")
    fill_local_entry(repository, broker, request)
    before = WcaLocalPaperAccount.restore(
        repository,
        account_id=request.account_id,
        symbol=request.symbol,
        starting_balance=123_456.0,
    ).get_account_snapshot()

    restarted_account = WcaLocalPaperAccount.restore(
        repository,
        account_id=request.account_id,
        symbol=request.symbol,
        starting_balance=999_999.0,
    ).get_account_snapshot()

    assert restarted_account.starting_balance == before.starting_balance
    assert restarted_account.cash == before.cash
    assert restarted_account.equity == before.equity
    assert restarted_account.unrealized_pnl == before.unrealized_pnl
    assert restarted_account.trades_today == before.trades_today
    assert len(restarted_account.positions) == 1
    assert restarted_account.positions[0].quantity == request.quantity
    broker.close()


def test_wca_local_paper_reset_blocks_pending_entries_without_force() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-reset-pending-entry")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol)
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-reset-pending-entry")

    result = broker.reset_local_paper_account(force=False, reason="wca.local_paper.test_reset")

    assert result["status"] == "blocked"
    assert result["reset"] is False
    assert "wca.local_paper.reset_blocked.pending_entry_orders" in result["reasonCodes"]
    assert broker.get_open_orders(symbol=request.symbol)
    broker.close()


def test_wca_local_paper_reset_blocks_positions_exits_and_protective_orders_without_force() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-reset-active-state")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol)
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-reset-active-state")
    fill_local_entry(repository, broker, request)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            INSERT INTO wca_local_orders (
                local_order_id, algorithm_id, local_account_id, client_order_id,
                symbol, side, order_type, quantity, remaining_quantity,
                limit_price, stop_price, target_price, status, created_at,
                updated_at, decision_id, idempotency_key, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"wca-exit-order-{uuid4().hex}",
                WCA_ALGORITHM_ID,
                request.account_id,
                f"wca-exit-{uuid4().hex}",
                request.symbol,
                WcaSide.SELL.value,
                "LIMIT",
                1,
                1,
                request.limit_price + 1.0,
                None,
                None,
                WcaOrderStatus.ACKNOWLEDGED.value,
                now,
                now,
                request.decision_id,
                f"exit-{uuid4().hex}",
                json.dumps({"side": WcaSide.SELL.value}),
            ),
        )

    result = broker.reset_local_paper_account(force=False, reason="wca.local_paper.test_reset")

    assert result["status"] == "blocked"
    assert "wca.local_paper.reset_blocked.open_positions" in result["reasonCodes"]
    assert "wca.local_paper.reset_blocked.pending_exit_orders" in result["reasonCodes"]
    assert "wca.local_paper.reset_blocked.protective_orders" in result["reasonCodes"]
    assert broker.refresh_account_snapshot().positions
    broker.close()


def test_wca_local_paper_force_reset_clears_local_state_and_cancels_pending_orders() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-force-reset")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol, starting_balance=123_456.0)
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-force-reset")
    fill_local_entry(repository, broker, request)

    result = broker.reset_local_paper_account(
        starting_balance=50_000.0,
        force=True,
        reason="wca.local_paper.test_force_reset",
        command_id="wca-test-force-reset-command",
    )
    snapshot = broker.refresh_account_snapshot()
    projection = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=request.account_id, symbol=request.symbol)

    assert result["status"] == "completed"
    assert result["reset"] is True
    assert result["startingBalance"] == 50_000.0
    assert snapshot.equity == 50_000.0
    assert snapshot.buyingPower == 50_000.0
    assert list(snapshot.positions) == []
    assert broker.get_open_orders(symbol=request.symbol) == []
    assert projection.open_quantity == 0
    assert projection.realized_pnl == 0.0
    assert projection.reserved_risk == 0.0
    with sqlite3.connect(repository.path) as conn:
        local_counts = {
            table: conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ?",
                (WCA_ALGORITHM_ID, request.account_id, request.symbol),
            ).fetchone()[0]
            for table in ("wca_local_positions", "wca_local_lots", "wca_local_orders", "wca_local_fills")
        }
        reset_event = conn.execute(
            """
            SELECT payload_json
            FROM wca_inventory_ledger
            WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?
              AND event_type = 'DAILY_STATE_RESET'
            ORDER BY event_timestamp DESC, inventory_event_id DESC
            LIMIT 1
            """,
            (WCA_ALGORITHM_ID, request.account_id, request.symbol),
        ).fetchone()
    assert local_counts == {"wca_local_positions": 0, "wca_local_lots": 0, "wca_local_orders": 0, "wca_local_fills": 0}
    assert json.loads(reset_event[0])["explicit_reset_command"] == "reset_local_paper_account"
    broker.close()

def test_wca_daily_reset_preserves_account_balance_positions_lots_and_cumulative_pnl() -> None:
    account = WcaLocalPaperAccount(account_id="wca-paper-daily-reset", starting_balance=10_000.0, session_date="2026-01-05")
    account.reserve_risk(125.0)
    account.apply_fill(symbol="SPY", side="BUY", quantity=4, price=100.0, filled_at="2026-01-05T15:00:00+00:00")
    account.mark_to_market(symbol="SPY", mark_price=112.0, marked_at="2026-01-05T15:01:00+00:00")
    before = account.close_position(symbol="SPY", quantity=1, price=115.0, closed_at="2026-01-05T15:02:00+00:00")
    account._circuit_breaker_state = "open"
    account._cooldown_until = datetime(2026, 1, 6, 15, 0, tzinfo=timezone.utc)

    daily_reset = account.reset_daily_state(
        session_date="2026-01-06",
        reset_circuit_breaker=False,
        reset_cooldown=False,
    )

    assert daily_reset.session_date.isoformat() == "2026-01-06"
    assert daily_reset.trades_today == 0
    assert daily_reset.daily_realized_pnl == 0.0
    assert daily_reset.daily_loss == 0.0
    assert daily_reset.daily_unrealized_pnl == before.unrealized_pnl
    assert daily_reset.starting_balance == before.starting_balance
    assert daily_reset.cash == before.cash
    assert daily_reset.equity == before.equity
    assert daily_reset.realized_pnl == before.realized_pnl
    assert daily_reset.reserved_risk == before.reserved_risk
    assert daily_reset.positions == before.positions
    assert daily_reset.lots == before.lots
    assert daily_reset.circuit_breaker_state == "open"
    assert daily_reset.cooldown_until == datetime(2026, 1, 6, 15, 0, tzinfo=timezone.utc)

    policy_cleared = account.reset_daily_state(session_date="2026-01-07")
    assert policy_cleared.circuit_breaker_state == "closed"
    assert policy_cleared.cooldown_until is None

def test_wca_daily_state_reset_ledger_event_preserves_inventory_projection_and_balance_seed() -> None:
    repository = repository_for_step10()
    account_id = "paper-daily-ledger"
    symbol = "SPY"
    repository.record_inventory_event(
        WcaInventoryLedgerEvent(
            inventory_event_id="daily-ledger-bootstrap",
            event_type="DAILY_STATE_RESET",
            broker_account_id=account_id,
            symbol=symbol,
            event_timestamp="2026-01-05T14:30:00+00:00",
            trade_date="2026-01-05",
            source_authority=WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
            payload={"starting_balance": 10_000.0, "cash": 10_000.0, "equity": 10_000.0, "buying_power": 10_000.0},
        )
    )
    repository.record_inventory_event(
        WcaInventoryLedgerEvent(
            inventory_event_id="daily-ledger-entry-fill",
            event_type="FILL_RECEIVED",
            broker_account_id=account_id,
            symbol=symbol,
            event_timestamp="2026-01-05T15:00:00+00:00",
            trade_date="2026-01-05",
            side=WcaSide.BUY.value,
            quantity=4,
            filled_quantity=4,
            average_entry_price=100.0,
            fill_price=100.0,
            source_authority=WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
            payload={"position_effect": "entry"},
        )
    )
    repository.record_inventory_event(
        WcaInventoryLedgerEvent(
            inventory_event_id="daily-ledger-correction",
            event_type="RECONCILIATION_CORRECTION",
            broker_account_id=account_id,
            symbol=symbol,
            event_timestamp="2026-01-05T15:01:00+00:00",
            trade_date="2026-01-05",
            average_entry_price=100.0,
            source_authority=WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
            payload={"open_quantity": 4, "average_entry_price": 100.0, "reserved_risk": 50.0},
        )
    )
    before = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=account_id, symbol=symbol)

    repository.record_inventory_event(
        WcaInventoryLedgerEvent(
            inventory_event_id="daily-ledger-session-reset",
            event_type="DAILY_STATE_RESET",
            broker_account_id=account_id,
            symbol=symbol,
            event_timestamp="2026-01-06T14:30:00+00:00",
            trade_date="2026-01-06",
            source_authority=WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
            payload={"daily_reset": True, "account_reset": False},
        )
    )
    after = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=account_id, symbol=symbol)
    restored = WcaLocalPaperAccount.restore(
        repository,
        account_id=account_id,
        symbol=symbol,
        starting_balance=999_999.0,
        session_date="2026-01-06",
    ).get_account_snapshot()
    daily = repository.read_daily_state_projection(
        algorithm_id=WCA_ALGORITHM_ID,
        broker_account_id=account_id,
        symbol=symbol,
        session_date="2026-01-06",
    )

    assert after.open_quantity == before.open_quantity == 4
    assert after.average_entry_price == before.average_entry_price == 100.0
    assert after.realized_pnl == before.realized_pnl
    assert after.reserved_risk == before.reserved_risk == 50.0
    assert restored.starting_balance == 10_000.0
    assert restored.positions[0].quantity == 4
    assert daily.trades_completed_today == 0
    assert daily.realized_pnl_today == 0.0
    assert daily.daily_loss == 0.0
    assert daily.circuit_breaker_state == "closed"
    assert daily.cooldown_until is None

def test_wca_local_paper_account_owns_cash_positions_risk_and_immutable_snapshots() -> None:
    account = WcaLocalPaperAccount(account_id="wca-paper-unit", starting_balance=1_000.0, session_date="2026-01-05")
    initial = account.get_account_snapshot()

    assert initial.algorithm_id == WCA_ALGORITHM_ID
    assert initial.cash == 1_000.0
    assert initial.buying_power == 1_000.0
    with pytest.raises(FrozenInstanceError):
        initial.cash = 0  # type: ignore[misc]

    account.reserve_cash(100.0)
    account.release_cash(40.0)
    account.reserve_risk(25.0)
    reserved = account.get_account_snapshot()
    assert reserved.cash == 1_000.0
    assert reserved.reserved_risk == 25.0
    assert reserved.buying_power == 915.0

    opened = account.apply_fill(symbol="SPY", side="BUY", quantity=2, price=100.0, filled_at="2026-01-05T15:00:00+00:00")
    assert opened.cash == 800.0
    assert opened.gross_exposure == 200.0
    assert opened.equity == 1_000.0
    assert opened.positions[0].quantity == 2

    marked = account.mark_to_market(symbol="SPY", mark_price=110.0, marked_at="2026-01-05T15:01:00+00:00")
    assert marked.unrealized_pnl == 20.0
    assert marked.equity == 1_020.0
    assert opened.positions[0].mark_price == 100.0

    account.close_position(symbol="SPY", quantity=1, price=115.0, closed_at="2026-01-05T15:02:00+00:00")
    closed = account.close_position(symbol="SPY", price=90.0, closed_at="2026-01-05T15:03:00+00:00")
    assert closed.positions == ()
    assert closed.cash == 1_005.0
    assert closed.realized_pnl == 5.0
    assert closed.trades_today == 1
    assert closed.daily_loss == 0.0

    reset = account.reset_daily_state(session_date="2026-01-06")
    assert reset.session_date.isoformat() == "2026-01-06"
    assert reset.trades_today == 0
    assert reset.reserved_risk == 25.0

    repository = repository_for_step10()
    persisted_account = WcaLocalPaperAccount(account_id="wca-paper-persist", starting_balance=1_000.0, session_date="2026-01-05")
    persisted_account.apply_fill(symbol="SPY", side="BUY", quantity=3, price=50.0, filled_at="2026-01-05T15:04:00+00:00")
    persisted_account.reserve_risk(12.5)
    persisted_account.persist(repository, symbol="SPY", timestamp="2026-01-05T15:05:00+00:00")
    restored = WcaLocalPaperAccount.restore(repository, account_id="wca-paper-persist", symbol="SPY", starting_balance=1_000.0, session_date="2026-01-05").get_account_snapshot()
    assert restored.positions[0].quantity == 3
    assert restored.reserved_risk == 12.5


def test_wca_local_paper_account_rejects_non_wca_lots_and_restores_repository_state() -> None:
    with pytest.raises(ValueError, match="non-WCA lots"):
        WcaLocalPaperAccount(
            account_id="wca-paper-unit",
            starting_balance=1_000.0,
            lots=(
                WcaLocalPaperLotSnapshot(
                    lot_id="foreign-lot",
                    algorithm_id="weighted_voting",
                    account_id="wca-paper-unit",
                    symbol="SPY",
                    side="BUY",
                    quantity=1,
                    entry_price=100.0,
                ),
            ),
        )

    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-account-restore")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, starting_balance=123_456.0)
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-account-restore")
    fill_local_entry(repository, broker, request)

    restored = WcaLocalPaperAccount.restore(
        repository,
        account_id=request.account_id,
        symbol=request.symbol,
        starting_balance=123_456.0,
        session_date=datetime.now(timezone.utc).date(),
    )
    snapshot = restored.get_account_snapshot()
    broker_snapshot = restored.to_broker_account_snapshot(symbol=request.symbol)

    assert snapshot.account_id == request.account_id
    assert snapshot.positions[0].algorithm_id == WCA_ALGORITHM_ID
    assert snapshot.positions[0].quantity == request.quantity
    assert snapshot.lots[0].algorithm_id == WCA_ALGORITHM_ID
    assert broker_snapshot.sourceAuthority == WCA_LOCAL_PAPER_SOURCE_AUTHORITY
    assert broker_snapshot.positions[0].algorithmId == WCA_ALGORITHM_ID

def test_wca_repository_rejects_non_wca_execution_state_writes() -> None:
    repository = repository_for_step10()
    decision, request = reserve(repository, suffix="identity")
    assert decision.proposed_order is not None
    non_wca_decision = decision.model_copy(update={"algorithm_id": "weighted_voting"})
    non_wca_order = decision.proposed_order.model_copy(update={"algorithm_id": "voting_ensemble"})
    decision_with_non_wca_order = decision.model_copy(update={"proposed_order": non_wca_order})

    with pytest.raises(ValueError, match="algorithm_id='wca'"):
        repository.record_broker_order(non_wca_decision, broker_order_id="bad-broker", account_id=request.account_id, idempotency_key="bad", status="ACKNOWLEDGED")
    with pytest.raises(ValueError, match="algorithm_id='wca'"):
        repository.apply_fill_and_update_position(decision_with_non_wca_order, fill_id="bad-fill", account_id=request.account_id, quantity=1)
    with pytest.raises(ValueError, match="account_id does not match"):
        repository.apply_fill_and_update_position(decision, fill_id="wrong-account-fill", account_id="weighted-voting-paper", quantity=1)

    with sqlite3.connect(repository.path) as conn:
        sibling_lots = conn.execute("SELECT COUNT(*) FROM wca_owned_lots WHERE algorithm_id <> ?", (WCA_ALGORITHM_ID,)).fetchone()[0]
        sibling_fills = conn.execute("SELECT COUNT(*) FROM wca_attributed_fills WHERE algorithm_id <> ?", (WCA_ALGORITHM_ID,)).fetchone()[0]

    assert sibling_lots == 0
    assert sibling_fills == 0


def test_wca_local_paper_inventory_tables_are_dedicated_namespaced_and_restorable() -> None:
    repository = repository_for_step10()
    required_columns = {
        "wca_local_paper_account": {
            "algorithm_id", "local_account_id", "symbol", "starting_balance", "cash", "equity",
            "buying_power", "realized_pnl", "unrealized_pnl", "daily_realized_pnl",
            "daily_unrealized_pnl", "daily_loss", "gross_exposure", "net_exposure",
            "reserved_risk", "trades_today", "session_date", "circuit_breaker_state",
            "cooldown_until", "last_mark_timestamp", "state_version",
        },
        "wca_local_positions": {
            "position_id", "algorithm_id", "local_account_id", "symbol", "side", "quantity",
            "average_entry_price", "opened_at", "last_updated_at", "stop_price", "target_price",
            "realized_pnl", "unrealized_pnl",
        },
        "wca_local_lots": {
            "lot_id", "algorithm_id", "local_account_id", "symbol", "side", "quantity",
            "remaining_quantity", "entry_price", "entry_timestamp", "decision_id", "order_intent_id",
        },
        "wca_local_orders": {
            "local_order_id", "algorithm_id", "local_account_id", "client_order_id", "symbol",
            "side", "order_type", "quantity", "remaining_quantity", "limit_price", "stop_price",
            "target_price", "status", "created_at", "updated_at", "decision_id", "idempotency_key",
        },
        "wca_local_fills": {
            "fill_id", "algorithm_id", "local_account_id", "order_id", "symbol", "side",
            "quantity", "fill_price", "commissions", "fees", "slippage", "timestamp",
        },
    }
    with sqlite3.connect(repository.path) as conn:
        for table, expected in required_columns.items():
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            assert expected <= columns
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO wca_local_positions (
                    position_id, algorithm_id, local_account_id, symbol, side, quantity,
                    average_entry_price, last_updated_at, realized_pnl, unrealized_pnl
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("foreign-position", "weighted_voting", "wca-paper-local", "SPY", "BUY", 1, 100.0, "2026-01-05T15:00:00+00:00", 0.0, 0.0),
            )

    account = WcaLocalPaperAccount(
        account_id="wca-paper-local",
        starting_balance=1_000.0,
        session_date="2026-01-05",
        open_orders=(
            WcaLocalPaperOrderSnapshot(
                algorithm_id=WCA_ALGORITHM_ID,
                account_id="wca-paper-local",
                symbol="SPY",
                side="BUY",
                quantity=4,
                remaining_quantity=4,
                status="SUBMITTED",
                client_order_id="wca-client-local-1",
                local_order_id="wca-local-order-1",
                order_type="LIMIT",
                limit_price=49.5,
                stop_price=45.0,
                target_price=60.0,
                created_at=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
                decision_id="wca-decision-local-1",
                order_intent_id="wca-intent-local-1",
                idempotency_key="wca-idem-local-1",
            ),
        ),
    )
    account.apply_fill(
        symbol="SPY",
        side="BUY",
        quantity=3,
        price=50.0,
        filled_at="2026-01-05T15:01:00+00:00",
        decision_id="wca-decision-local-1",
        order_intent_id="wca-intent-local-1",
        local_order_id="wca-local-order-1",
        fill_id="wca-fill-local-1",
        commissions=0.25,
        fees=0.05,
        slippage=0.10,
    )
    account.mark_to_market(symbol="SPY", mark_price=52.0, marked_at="2026-01-05T15:02:00+00:00")
    account.reserve_risk(7.5)
    account.persist(repository, symbol="SPY", timestamp="2026-01-05T15:03:00+00:00")

    inventory = repository.read_wca_local_inventory_snapshot(local_account_id="wca-paper-local", symbol="SPY")
    assert inventory is not None
    assert inventory["algorithm_id"] == WCA_ALGORITHM_ID
    assert inventory["local_account_id"] == "wca-paper-local"
    assert inventory["account_snapshot"]["local_account_id"] == "wca-paper-local"
    with sqlite3.connect(repository.path) as conn:
        account_row = conn.execute("SELECT algorithm_id, local_account_id, symbol, starting_balance, cash, equity, buying_power, reserved_risk FROM wca_local_paper_account").fetchone()
        position = conn.execute("SELECT algorithm_id, local_account_id, symbol, position_id, quantity, average_entry_price, unrealized_pnl FROM wca_local_positions").fetchone()
        lot = conn.execute("SELECT algorithm_id, local_account_id, symbol, lot_id, remaining_quantity, entry_price, decision_id, order_intent_id FROM wca_local_lots").fetchone()
        order = conn.execute("SELECT algorithm_id, local_account_id, symbol, local_order_id, client_order_id, remaining_quantity, target_price, idempotency_key FROM wca_local_orders").fetchone()
        fill = conn.execute("SELECT algorithm_id, local_account_id, symbol, fill_id, order_id, quantity, fill_price, commissions, fees, slippage FROM wca_local_fills").fetchone()
    assert account_row == (WCA_ALGORITHM_ID, "wca-paper-local", "SPY", 1_000.0, 849.6, 1_005.6, 842.1, 7.5)
    assert position == (WCA_ALGORITHM_ID, "wca-paper-local", "SPY", "wca-local-position-wca-paper-local-SPY", 3, 50.0, 6.0)
    assert lot == (WCA_ALGORITHM_ID, "wca-paper-local", "SPY", account.get_account_snapshot().lots[0].lot_id, 3, 50.0, "wca-decision-local-1", "wca-intent-local-1")
    assert order == (WCA_ALGORITHM_ID, "wca-paper-local", "SPY", "wca-local-order-1", "wca-client-local-1", 4, 60.0, "wca-idem-local-1")
    assert fill == (WCA_ALGORITHM_ID, "wca-paper-local", "SPY", "wca-fill-local-1", "wca-local-order-1", 3, 50.0, 0.25, 0.05, 0.1)

    restored = WcaLocalPaperAccount.restore(
        repository,
        account_id="wca-paper-local",
        symbol="SPY",
        starting_balance=1_000.0,
        session_date="2026-01-05",
    ).get_account_snapshot()
    assert restored.cash == 849.6
    assert restored.positions[0].quantity == 3
    assert restored.positions[0].mark_price == 52.0
    assert restored.reserved_risk == 7.5
    assert restored.fills[0].fill_id == "wca-fill-local-1"
    assert restored.open_orders[0].local_order_id == "wca-local-order-1"

def test_wca_fill_account_inventory_mutation_rolls_back_atomically() -> None:
    repository = repository_for_step10()
    decision, request = reserve(repository, suffix="local-rollback")
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            CREATE TRIGGER fail_wca_local_paper_account_insert
            BEFORE INSERT ON wca_local_paper_account
            BEGIN
                SELECT RAISE(ABORT, 'forced local account rollback');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced local account rollback"):
        repository.apply_fill_and_update_position(
            decision,
            fill_id="rollback-fill",
            account_id=request.account_id,
            quantity=request.quantity,
            broker_order_id="rollback-broker-order",
            payload={
                "client_order_id": request.client_order_id,
                "entry_price": request.limit_price,
                "opened_at": "2026-01-05T15:04:00+00:00",
                "remaining_quantity": 0,
            },
        )

    with sqlite3.connect(repository.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wca_attributed_fills WHERE fill_id = 'rollback-fill'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM wca_local_fills WHERE fill_id = 'rollback-fill'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM wca_owned_lots WHERE lot_id = 'wca-lot-rollback-fill'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM wca_local_lots WHERE lot_id = 'wca-lot-rollback-fill'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM wca_inventory_ledger WHERE fill_id = 'rollback-fill'").fetchone()[0] == 0
        projection = conn.execute(
            "SELECT open_quantity FROM wca_inventory_projection WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?",
            (WCA_ALGORITHM_ID, request.account_id, request.symbol),
        ).fetchone()
    assert projection is not None
    assert projection[0] == 0

def test_wca_local_paper_broker_reduces_only_wca_owned_local_lots() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-reduce-guard")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol)

    with pytest.raises(Exception, match="wca_owned_quantity_required"):
        broker.close_or_reduce_wca_position(symbol=request.symbol, quantity=1, side="BUY", client_order_id="wca-eos-empty")

    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-reduce-guard")
    fill_local_entry(repository, broker, request)
    with pytest.raises(Exception, match=WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED):
        broker.close_or_reduce_wca_position(symbol="QQQ", quantity=1, side="BUY", client_order_id="wca-eos-wrong-symbol")
    with pytest.raises(Exception, match=WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED):
        broker.flatten_wca_positions(symbol="QQQ", client_order_id="wca-flatten-wrong-symbol")
    with pytest.raises(Exception, match="wca_owned_quantity_required"):
        broker.close_or_reduce_wca_position(symbol=request.symbol, quantity=request.quantity + 1, side="BUY", client_order_id="wca-eos-too-large")

    projection = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=request.account_id, symbol=request.symbol)
    assert projection.open_quantity == request.quantity

def test_wca_automatic_paper_account_uses_local_isolated_state_not_alpaca_execution() -> None:
    missing = validate_wca_automatic_paper_account(account_id="wca-paper-1", environ={})
    alpaca_execution_configured = validate_wca_automatic_paper_account(
        account_id="wca-paper-1",
        environ=valid_wca_paper_env()
        | {
            WCA_ALPACA_PAPER_API_KEY_ID: "wca-key",
            WCA_ALPACA_PAPER_API_SECRET_KEY: "wca-secret",
            WCA_ALPACA_PAPER_BASE_URL: WCA_REQUIRED_ALPACA_PAPER_BASE_URL,
            WCA_ALPACA_PAPER_ACCOUNT_ID: "wca-paper-1",
        },
    )
    shared_credentials = validate_wca_automatic_paper_account(
        account_id="wca-paper-1",
        environ=valid_wca_paper_env()
        | {
            WCA_ALPACA_PAPER_API_KEY_ID: "wca-key",
            "APCA_API_KEY_ID": "wca-key",
        },
    )
    account_mismatch = validate_wca_automatic_paper_account(
        account_id="other-paper",
        environ=valid_wca_paper_env(),
    )
    invalid_balance = validate_wca_automatic_paper_account(
        account_id="wca-paper-1",
        environ=valid_wca_paper_env() | {WCA_LOCAL_PAPER_STARTING_BALANCE: "0"},
    )
    valid = validate_wca_automatic_paper_account(
        account_id="wca-paper-1",
        environ=valid_wca_paper_env(),
    )

    assert missing.verified is False
    assert "wca.local_paper_account.automatic_paper_disabled" in missing.reason_codes
    assert alpaca_execution_configured.verified is False
    assert "wca.local_paper_account.alpaca_paper_execution_disabled" in alpaca_execution_configured.reason_codes
    assert shared_credentials.verified is False
    assert "wca.local_paper_account.shared_alpaca_credentials_rejected" in shared_credentials.reason_codes
    assert account_mismatch.verified is False
    assert "wca.local_paper_account.account_id_mismatch" in account_mismatch.reason_codes
    assert invalid_balance.verified is False
    assert "wca.local_paper_account.starting_balance_invalid" in invalid_balance.reason_codes
    assert valid.verified is True
    assert valid.account_id == "wca-paper-1"
    assert valid.starting_balance == 250_000.0
    assert valid.source_authority == WCA_LOCAL_PAPER_SOURCE_AUTHORITY
    assert valid.reason_codes[-1] == "wca.local_paper_account.verified"


def test_wca_local_paper_broker_replaces_and_fetches_resting_orders() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-replace")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol)
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-replace")
    fill_local_entry(repository, broker, request)

    open_orders = broker.get_open_orders(symbol=request.symbol)
    target = next(order for order in open_orders if order.exitOwner == WCA_ALGORITHM_ID and order.orderType == "LIMIT")
    replacement = WcaPaperBrokerOrderRequest(
        account_id=request.account_id,
        symbol=request.symbol,
        side="SELL",
        quantity=target.quantity,
        order_type="LIMIT",
        limit_price=target.entryPrice + 2.0,
        target_price=target.entryPrice + 2.0,
        client_order_id=f"wca-protection-replaced-{uuid4().hex[:12]}",
        idempotency_key=f"wca-protection-replaced:{uuid4().hex}",
        decision_id="wca-protection-replaced-decision",
        order_intent_id="wca-protection-replaced-intent",
        configuration_version="test_configuration",
        configuration_hash="test-hash",
    )

    ack = broker.replace_order(target.clientOrderId, replacement)
    fetched = broker.get_order(ack.broker_order_id or "")
    open_after = broker.get_open_orders(symbol=request.symbol)

    assert ack.status == "ACKNOWLEDGED"
    assert fetched is not None
    assert fetched["client_order_id"] == replacement.client_order_id
    assert float(fetched["limit_price"]) == replacement.limit_price
    assert any(order.clientOrderId == replacement.client_order_id for order in open_after)
    assert all(order.algorithmId == WCA_ALGORITHM_ID and order.positionOwner == WCA_ALGORITHM_ID for order in open_after)


def test_wca_local_paper_broker_rejects_cross_account_order_mutations_before_write() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-cross-account")
    owner = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol)
    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, owner, owner_id="step13-local-cross-account")
    assert result.broker_order_id is not None

    other_account = WcaLocalPaperBroker(repository=repository, account_id="paper-other-step13", symbol=request.symbol)
    replacement = WcaPaperBrokerOrderRequest(
        account_id="paper-other-step13",
        symbol=request.symbol,
        side=request.side,
        quantity=request.quantity,
        order_type=request.order_type,
        limit_price=request.limit_price,
        stop_price=request.stop_price,
        target_price=request.target_price,
        client_order_id=f"wca-cross-replace-{uuid4().hex[:12]}",
        idempotency_key=f"wca-cross-replace:{uuid4().hex}",
        decision_id="wca-cross-replace-decision",
        order_intent_id="wca-cross-replace-intent",
        configuration_version="test_configuration",
        configuration_hash="test-hash",
    )

    with pytest.raises(Exception, match=WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED):
        other_account.cancel_order(result.broker_order_id)
    with pytest.raises(Exception, match=WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED):
        other_account.replace_order(result.broker_order_id, replacement)
    with pytest.raises(Exception, match=WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED):
        other_account.simulate_fill(result.broker_order_id, fill_price=request.limit_price, quantity=1)

    with sqlite3.connect(repository.path) as conn:
        status = conn.execute(
            "SELECT status FROM wca_broker_orders WHERE algorithm_id = ? AND account_id = ? AND broker_order_id = ?",
            (WCA_ALGORITHM_ID, request.account_id, result.broker_order_id),
        ).fetchone()[0]
        blocked = conn.execute(
            "SELECT COUNT(*) FROM wca_inventory_ledger WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ? AND payload_json LIKE ?",
            (WCA_ALGORITHM_ID, "paper-other-step13", request.symbol, f"%{WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED}%"),
        ).fetchone()[0]

    assert status == WcaOrderStatus.ACKNOWLEDGED.value
    assert blocked == 3


def test_wca_local_paper_status_observability_uses_only_wca_local_inventory() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-observability")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol, starting_balance=123_456.0)
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step20-local-observability")
    fill_local_entry(repository, broker, request)
    insert_other_wca_local_account_order(repository, symbol=request.symbol)

    status = WcaService(repository=repository).local_paper_status(
        runtime_mode=WcaRuntimeMode.LOCAL_AUTOMATIC_PAPER,
        account_id=request.account_id,
        symbol=request.symbol,
        starting_balance=123_456.0,
    )

    assert status["runtime_mode"] == WcaRuntimeMode.LOCAL_AUTOMATIC_PAPER.value
    assert status["local_account_id"] == request.account_id
    assert status["starting_balance"] == 123_456.0
    assert status["cash"] < 123_456.0
    assert status["equity"] == 123_456.0
    assert status["buying_power"] < 123_456.0
    assert status["realized_pnl"] == 0.0
    assert status["unrealized_pnl"] == 0.0
    assert status["daily_pnl"] == 0.0
    assert status["position"]["algorithm_id"] == WCA_ALGORITHM_ID
    assert status["position"]["local_account_id"] == request.account_id
    assert status["position_quantity"] == request.quantity
    assert status["average_entry_price"] == request.limit_price
    assert status["open_order_count"] == 2
    assert status["reserved_risk"] == 0.0
    assert status["daily_loss"] == 0.0
    assert status["trades_today"] == 0
    assert status["circuit_breaker_state"] == "closed"

def test_wca_local_paper_broker_processes_market_update_for_protective_stop_fill() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-protection-fill")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol)
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-protection-fill")
    fill_local_entry(repository, broker, request)
    market_timestamp = datetime.now(timezone.utc) + timedelta(seconds=1)
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            UPDATE wca_broker_orders
            SET timestamp = ?
            WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND client_order_id LIKE 'wca-protection-%'
            """,
            (market_timestamp.isoformat(), WCA_ALGORITHM_ID, request.account_id, request.symbol),
        )
    stop_order = next(order for order in broker.get_open_orders(symbol=request.symbol) if order.orderType == "STOP_LIMIT")

    fills = broker.process_market_update(
        {
            "symbol": request.symbol,
            "bid": stop_order.stopPrice or stop_order.entryPrice,
            "ask": (stop_order.stopPrice or stop_order.entryPrice) + 0.02,
            "timestamp": market_timestamp.isoformat(),
        }
    )
    projection = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=request.account_id, symbol=request.symbol)
    snapshot = broker.refresh_account_snapshot()
    filled_order = broker.get_order(stop_order.clientOrderId)

    assert len(fills) == 1
    assert fills[0].client_order_id == stop_order.clientOrderId
    assert projection.open_quantity == 0
    assert list(snapshot.positions) == []
    assert filled_order is not None
    assert filled_order["status"] == "filled"
    assert broker.get_open_orders(symbol=request.symbol) == []
    with sqlite3.connect(repository.path) as conn:
        sibling_statuses = conn.execute(
            """
            SELECT status FROM wca_broker_orders
            WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND client_order_id LIKE 'wca-protection-%'
            """,
            (WCA_ALGORITHM_ID, request.account_id, request.symbol),
        ).fetchall()
    assert sorted(status[0] for status in sibling_statuses) == [WcaOrderStatus.CANCELLED.value, WcaOrderStatus.FILLED.value]


def test_wca_local_protection_failure_marks_unprotected_blocks_entries_and_flattens() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-protection-failure")
    broker = RejectingProtectiveLocalBroker(repository=repository, account_id=request.account_id, symbol=request.symbol)
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step12-local-protection-failure")

    fills = fill_local_entry(repository, broker, request)
    projection = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=request.account_id, symbol=request.symbol)
    blocked_request = WcaPaperBrokerOrderRequest(
        account_id=request.account_id,
        symbol=request.symbol,
        side="BUY",
        quantity=1,
        order_type="LIMIT",
        limit_price=request.limit_price,
        stop_price=request.limit_price - 1.0,
        target_price=request.limit_price + 2.0,
        client_order_id="wca-after-unprotected-entry",
        idempotency_key="wca-after-unprotected-entry",
        decision_id="wca-after-unprotected-entry",
        order_intent_id="wca-after-unprotected-entry",
        configuration_version="test_configuration",
    )
    rejected = broker.submit_order(blocked_request)

    assert len(fills) == 1
    assert projection.open_quantity == 0
    assert rejected.status == "REJECTED"
    assert "wca.local_risk.circuit_breaker_open" in rejected.response_payload["localRisk"]["reason_codes"]
    with sqlite3.connect(repository.path) as conn:
        account = conn.execute(
            "SELECT circuit_breaker_state FROM wca_local_paper_account WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ?",
            (WCA_ALGORITHM_ID, request.account_id, request.symbol),
        ).fetchone()
        rejected_protection_count = conn.execute(
            "SELECT COUNT(*) FROM wca_broker_orders WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND client_order_id LIKE 'wca-protection-%' AND status = ?",
            (WCA_ALGORITHM_ID, request.account_id, request.symbol, WcaOrderStatus.REJECTED.value),
        ).fetchone()[0]
        unprotected_events = conn.execute(
            "SELECT COUNT(*) FROM wca_inventory_ledger WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ? AND payload_json LIKE '%UNPROTECTED%'",
            (WCA_ALGORITHM_ID, request.account_id, request.symbol),
        ).fetchone()[0]
    assert account == ("unprotected_position",)
    assert rejected_protection_count == 2
    assert unprotected_events >= 2
    assert broker.get_open_orders(symbol=request.symbol) == []


def test_wca_local_protective_order_requires_wca_position_ownership_before_mutation() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-protection-ownership")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol)
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step12-local-protection-ownership")
    fill_local_entry(repository, broker, request)
    stop_order = next(order for order in broker.get_open_orders(symbol=request.symbol) if order.orderType == "STOP_LIMIT")
    with sqlite3.connect(repository.path) as conn:
        row = conn.execute(
            "SELECT broker_order_id, payload_json FROM wca_broker_orders WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND client_order_id = ?",
            (WCA_ALGORITHM_ID, request.account_id, request.symbol, stop_order.clientOrderId),
        ).fetchone()
        payload = json.loads(row[1])
        payload["ownership"]["protected_algorithm_id"] = "weighted_voting"
        conn.execute("UPDATE wca_broker_orders SET payload_json = ? WHERE broker_order_id = ?", (json.dumps(payload, sort_keys=True), row[0]))

    with pytest.raises(Exception, match=WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED):
        broker.simulate_fill(
            client_order_id=stop_order.clientOrderId,
            fill_price=stop_order.stopPrice or stop_order.entryPrice,
            quantity=stop_order.quantity,
            filled_at=datetime.now(timezone.utc) + timedelta(seconds=1),
        )
    projection = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=request.account_id, symbol=request.symbol)

    assert projection.open_quantity == request.quantity
    with sqlite3.connect(repository.path) as conn:
        ownership_rejections = conn.execute(
            "SELECT COUNT(*) FROM wca_inventory_ledger WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ? AND payload_json LIKE ? AND payload_json LIKE '%position.ownership%'",
            (WCA_ALGORITHM_ID, request.account_id, request.symbol, f"%{WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED}%"),
        ).fetchone()[0]
    assert ownership_rejections == 1


def test_wca_local_paper_broker_flatten_wca_positions_closes_only_local_inventory() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-flatten")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol)
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-flatten")
    fill_local_entry(repository, broker, request)

    ack = broker.flatten_wca_positions(
        symbol=request.symbol,
        client_order_id="wca-flatten-unit-test",
        price=request.limit_price + 1.0,
        evaluated_at=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    projection = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=request.account_id, symbol=request.symbol)
    snapshot = broker.refresh_account_snapshot()

    assert ack.fill is not None
    assert ack.fill.filled_quantity == request.quantity
    assert projection.open_quantity == 0
    assert list(snapshot.positions) == []
    assert broker.get_open_orders(symbol=request.symbol) == []
    with pytest.raises(Exception, match="wca_owned_quantity_required"):
        broker.flatten_wca_positions(symbol=request.symbol)


def test_wca_local_paper_broker_does_not_fill_limits_without_executable_market_data() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-no-fill")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol)
    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-no-fill")

    buy_fills = broker.process_market_update({"symbol": request.symbol, "bid": request.limit_price - 0.05, "ask": request.limit_price + 0.05})
    projection = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=request.account_id, symbol=request.symbol)

    assert result.state == WcaOrderStatus.ACKNOWLEDGED
    assert buy_fills == ()
    assert projection.open_quantity == 0
    assert len(broker.get_open_orders(symbol=request.symbol)) == 1


def test_wca_local_paper_broker_requires_explicit_completed_bar_fallback_for_stop_limit() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-bar-stop")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol, allow_bar_execution=True)
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-bar-stop")
    fill_local_entry(repository, broker, request)
    market_timestamp = datetime.now(timezone.utc) + timedelta(seconds=1)
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            UPDATE wca_broker_orders
            SET timestamp = ?
            WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND client_order_id LIKE 'wca-protection-%'
            """,
            (market_timestamp.isoformat(), WCA_ALGORITHM_ID, request.account_id, request.symbol),
        )
    stop_order = next(order for order in broker.get_open_orders(symbol=request.symbol) if order.orderType == "STOP_LIMIT")
    bar = {
        "symbol": request.symbol,
        "low": (stop_order.stopPrice or stop_order.entryPrice) - 0.01,
        "high": (stop_order.stopPrice or stop_order.entryPrice) + 0.01,
        "timestamp": market_timestamp.isoformat(),
    }

    assert broker.process_market_update(bar) == ()
    assert broker.get_order(stop_order.clientOrderId)["status"] == "accepted"
    first_completed = broker.process_market_update(bar | {"completed_bar": True, "allow_bar_execution": True})
    triggered = broker.get_order(stop_order.clientOrderId)
    second_completed = broker.process_market_update(bar | {"timestamp": (market_timestamp + timedelta(seconds=60)).isoformat(), "completed_bar": True, "allow_bar_execution": True})

    assert first_completed == ()
    assert triggered["stop_triggered"] is True
    assert len(second_completed) == 1


def test_wca_local_paper_broker_applies_minimum_commission() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-minimum-commission")
    broker = WcaLocalPaperBroker(
        repository=repository,
        account_id=request.account_id,
        symbol=request.symbol,
        fill_model=WcaLocalPaperFillModel(
            commission_per_share=0.0,
            minimum_commission=2.50,
        ),
    )
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-minimum-commission")

    fills = fill_local_entry(repository, broker, request)
    fill_payload = fills[0].response_payload["fill"]

    assert fill_payload["commissions"] == 2.50
    with sqlite3.connect(repository.path) as conn:
        commissions = conn.execute("SELECT commissions FROM wca_local_fills WHERE fill_id = ?", (fills[0].fill_id,)).fetchone()[0]
    assert commissions == 2.50

def test_wca_local_paper_broker_applies_partial_fill_limits_and_costs() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-partial-cost")
    broker = WcaLocalPaperBroker(
        repository=repository,
        account_id=request.account_id,
        symbol=request.symbol,
        fill_model=WcaLocalPaperFillModel(
            slippage_bps=10.0,
            spread_cost_bps=5.0,
            commission_per_order=1.0,
            commission_per_share=0.01,
            regulatory_fee_per_share=0.005,
            participation_limit=0.5,
            max_fill_quantity=3,
            allow_partial_fills=True,
        ),
    )
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-partial-cost")
    market_timestamp = datetime.now(timezone.utc) + timedelta(seconds=1)
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            UPDATE wca_broker_orders
            SET timestamp = ?
            WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND idempotency_key = ?
            """,
            (market_timestamp.isoformat(), WCA_ALGORITHM_ID, request.account_id, request.symbol, request.idempotency_key),
        )

    fills = broker.process_market_update({"symbol": request.symbol, "bid": request.limit_price - 0.01, "ask": request.limit_price, "volume": 4, "timestamp": market_timestamp.isoformat()})
    projection = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=request.account_id, symbol=request.symbol)
    fill_payload = fills[0].response_payload["fill"]

    assert len(fills) == 1
    assert fills[0].filled_quantity == 2
    assert fills[0].remaining_quantity == request.quantity - 2
    assert projection.open_quantity == 2
    assert fill_payload["commissions"] == 1.02
    assert fill_payload["fees"] == 0.01
    assert fill_payload["slippage"] > 0
    with sqlite3.connect(repository.path) as conn:
        charges = conn.execute("SELECT commissions, fees, slippage FROM wca_local_fills WHERE fill_id = ?", (fills[0].fill_id,)).fetchone()
    assert charges[0] == 1.02
    assert charges[1] == 0.01
    assert charges[2] > 0


def test_wca_local_paper_risk_uses_wca_local_equity_for_risk_budget() -> None:
    account = WcaLocalPaperAccount(account_id="risk-local-equity", starting_balance=10_000)
    request = WcaPaperBrokerOrderRequest(
        account_id="risk-local-equity",
        symbol="SPY",
        side=WcaSide.BUY,
        quantity=100,
        order_type="STOP_LIMIT",
        limit_price=100.0,
        stop_price=99.0,
        target_price=103.0,
        client_order_id="risk-local-equity-client",
        idempotency_key="risk-local-equity-key",
        decision_id="risk-local-equity-decision",
        order_intent_id="risk-local-equity-intent",
        configuration_version="risk-test",
    )

    decision = WcaLocalPaperRiskManager().evaluate_order(
        WcaLocalPaperRiskContext(
            account_snapshot=account.get_account_snapshot(),
            request=request,
            policy=WcaLocalPaperRiskPolicy(
                base_risk_percent=1.0,
                confidence_size_multiplier=0.5,
                edge_size_multiplier=0.5,
                max_position_percent=100.0,
            ),
        )
    )

    assert decision.risk_budget_dollars == 50.0
    assert decision.order_risk_dollars == 100.0
    assert decision.local_equity == 10_000.0
    assert decision.permitted is False
    assert "wca.local_risk.base_risk_percent_exceeded" in decision.reason_codes


def test_wca_local_paper_broker_rejects_local_risk_before_resting_order() -> None:
    repository = repository_for_step10()
    _, request = reserve(repository, suffix="local-risk-reject")
    broker = WcaLocalPaperBroker(repository=repository, account_id=request.account_id, symbol=request.symbol, starting_balance=1.0)

    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10-local-risk-reject")

    assert result.state == WcaOrderStatus.REJECTED
    with sqlite3.connect(repository.path) as conn:
        conn.row_factory = sqlite3.Row
        broker_row = conn.execute(
            "SELECT status, response_payload_json FROM wca_broker_orders WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND idempotency_key = ?",
            (WCA_ALGORITHM_ID, request.account_id, request.symbol, request.idempotency_key),
        ).fetchone()
    assert broker_row is not None
    assert broker_row["status"] == WcaOrderStatus.REJECTED.value
    payload = json.loads(broker_row["response_payload_json"])
    risk_reasons = tuple(payload["response_payload"]["localRisk"]["reason_codes"])
    assert "wca.local_risk.available_cash_exceeded" in risk_reasons
    assert "wca.local_risk.buying_power_exceeded" in risk_reasons
def reserve(repository: WcaSqliteRepository, *, suffix: str = "ack"):
    decision = decision_with_order()
    assert decision.proposed_order is not None
    proposed = decision.proposed_order.model_copy(
        update={
            "decision_id": f"{decision.decision_id}-{suffix}",
            "idempotency_key": f"step10-{suffix}",
            "account_id": "paper-step10",
            "configuration_version": decision.configuration_version,
            "configuration_hash": decision.configuration_hash,
        }
    )
    decision = decision.model_copy(update={"decision_id": f"{decision.decision_id}-{suffix}", "proposed_order": proposed})
    request = build_wca_paper_broker_request(proposed)
    reservation = repository.reserve_decision_order_and_outbox(
        decision,
        run_id="step10-run",
        account_id=proposed.account_id,
        idempotency_key=proposed.idempotency_key,
        client_order_id=request.client_order_id,
        request_payload=request.model_dump(mode="json"),
        final_validation_context=validation_context(decision, request),
    )
    assert reservation.created is True
    return decision, request


def insert_other_wca_local_account_order(repository: WcaSqliteRepository, *, symbol: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            INSERT INTO wca_local_orders (
                local_order_id, algorithm_id, local_account_id, client_order_id,
                symbol, side, order_type, quantity, remaining_quantity,
                limit_price, stop_price, target_price, status, created_at,
                updated_at, decision_id, idempotency_key, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "other-account-observability-order",
                WCA_ALGORITHM_ID,
                "other-wca-local-account",
                "other-account-observability-client-order",
                symbol,
                "BUY",
                "LIMIT",
                999,
                999,
                100.0,
                None,
                None,
                "ACCEPTED",
                now,
                now,
                "other-account-decision",
                "other-account-observability-key",
                '{"owner":"wca","account_id":"other-wca-local-account"}',
            ),
        )

def fill_local_entry(repository: WcaSqliteRepository, broker: WcaLocalPaperBroker, request):
    market_timestamp = datetime.now(timezone.utc) + timedelta(seconds=1)
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """
            UPDATE wca_broker_orders
            SET timestamp = ?
            WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND idempotency_key = ?
            """,
            (market_timestamp.isoformat(), WCA_ALGORITHM_ID, request.account_id, request.symbol, request.idempotency_key),
        )
    return broker.process_market_update(
        {
            "symbol": request.symbol,
            "bid": request.limit_price - 0.01,
            "ask": request.limit_price,
            "timestamp": market_timestamp.isoformat(),
            "volume": request.quantity * 10,
        }
    )


class RejectingProtectiveLocalBroker(WcaLocalPaperBroker):
    def submit_order(self, request: WcaPaperBrokerOrderRequest) -> WcaPaperBrokerAck:
        if request.client_order_id.startswith("wca-protection-"):
            broker_order_id = f"wca-rejected-protection-{uuid4().hex[:12]}"
            return WcaPaperBrokerAck(
                status="REJECTED",
                client_order_id=request.client_order_id,
                broker_order_id=broker_order_id,
                accepted_quantity=0,
                message="unit_test_protection_rejected",
                response_payload={
                    "id": broker_order_id,
                    "client_order_id": request.client_order_id,
                    "symbol": request.symbol,
                    "status": "rejected",
                    "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
                },
                fill=None,
            )
        return super().submit_order(request)

def validation_context(decision, request) -> WcaOrderValidationContext:
    return WcaOrderValidationContext(
        evaluation_timestamp=decision.decision_timestamp,
        account_id=request.account_id,
        broker_endpoint="paper",
        runtime_mode=WcaRuntimeMode.MANUAL_PAPER,
        requires_executable_paper_stage=True,
        data_ready=decision.market_snapshot.data_ready,
        quote_freshness_seconds=None,
        candle_freshness_seconds=120,
        available_buying_power=100_000,
        account_equity=100_000,
        max_position_value=100_000,
        max_approved_quantity=1000,
        order_type=request.order_type,
        time_in_force=request.time_in_force,
        protective_exit_plan_present=True,
        idempotency_required=True,
    )


def repository_for_step10() -> WcaSqliteRepository:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return WcaSqliteRepository(f"sqlite:///{root / f'wca-step10-{uuid4().hex}.sqlite'}")


def state_for(repository: WcaSqliteRepository, idempotency_key: str) -> str:
    with sqlite3.connect(repository.path) as conn:
        return conn.execute("SELECT status FROM wca_execution_outbox WHERE idempotency_key = ?", (idempotency_key,)).fetchone()[0]


def fill_count(repository: WcaSqliteRepository, fill_id: str) -> int:
    with sqlite3.connect(repository.path) as conn:
        return conn.execute("SELECT COUNT(*) FROM wca_attributed_fills WHERE fill_id = ?", (fill_id,)).fetchone()[0]


def valid_wca_paper_env() -> dict[str, str]:
    return {
        WCA_AUTOMATIC_PAPER_ENABLED: "true",
        WCA_LOCAL_PAPER_ACCOUNT_ID: "wca-paper-1",
        WCA_LOCAL_PAPER_STARTING_BALANCE: "250000",
    }


class LookupBroker:
    def __init__(self, *, existing_order: dict, timeout: bool = False, found_after_submit: bool = False) -> None:
        self.existing_order = existing_order
        self.timeout = timeout
        self.found_after_submit = found_after_submit
        self.submit_count = 0
        self.lookup_count = 0

    def find_order_by_client_order_id(self, client_order_id: str):
        self.lookup_count += 1
        if self.found_after_submit and self.submit_count == 0:
            return None
        return self.existing_order if self.existing_order["client_order_id"] == client_order_id else None

    def submit_order(self, request):
        self.submit_count += 1
        if self.timeout:
            from backend.app.algorithms.wca.paper_broker import WcaPaperBrokerTimeout

            raise WcaPaperBrokerTimeout("lookup after timeout")
        raise AssertionError("duplicate existing order should not submit")

    def refresh_order(self, client_order_id: str):
        return None


def alpaca_order_payload(request) -> dict:
    return {
        "id": f"alpaca-{request.client_order_id}",
        "client_order_id": request.client_order_id,
        "symbol": request.symbol,
        "qty": str(request.quantity),
        "filled_qty": "0",
        "limit_price": str(request.limit_price),
        "status": "new",
    }
