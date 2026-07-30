from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.algorithms.wca.contracts import WcaOrderStatus, WcaOrderValidationContext, WcaRuntimeMode
from backend.app.algorithms.wca.paper_broker import (
    WcaPaperBrokerAck,
    WcaPaperBrokerFill,
    WcaPaperBrokerOutboxAdapter,
    WcaPaperBrokerTimeout,
    build_wca_paper_broker_request,
)
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.tests.test_wca_step6_inventory_persistence import decision_with_order


ACCOUNT_ID = "paper-phase7"


class CountingCrashBroker:
    def __init__(self, *, crash: str | None = None, partial_fill: bool = False) -> None:
        self.crash = crash
        self.partial_fill = partial_fill
        self.submit_count = 0
        self.economic_client_order_ids: set[str] = set()

    def submit_order(self, request):
        if self.crash == "before_broker_request":
            raise RuntimeError("phase7 crash before broker request")
        self.submit_count += 1
        self.economic_client_order_ids.add(request.client_order_id)
        if self.crash == "during_broker_request":
            raise WcaPaperBrokerTimeout("phase7 timeout after possible broker acceptance")
        fill = None
        if self.partial_fill:
            fill = WcaPaperBrokerFill(
                fill_id=f"fill-{request.client_order_id}",
                client_order_id=request.client_order_id,
                broker_order_id=f"alpaca-{request.client_order_id}",
                filled_quantity=max(1, request.quantity // 2),
                remaining_quantity=request.quantity - max(1, request.quantity // 2),
                average_fill_price=request.limit_price,
            )
        return WcaPaperBrokerAck(
            status="ACKNOWLEDGED",
            client_order_id=request.client_order_id,
            broker_order_id=f"alpaca-{request.client_order_id}",
            accepted_quantity=request.quantity,
            fill=fill,
        )

    def refresh_order(self, client_order_id: str):
        return None


def test_crash_before_transaction_commit_rolls_back_everything_and_no_submission_occurs(monkeypatch) -> None:
    repository = phase7_repository()
    decision, request = reserve_inputs("before-commit")
    original = repository._record_inventory_event_in_conn

    def crash_on_risk_reservation(conn, event):
        if event.event_type == "RISK_RESERVED":
            raise RuntimeError("phase7 crash before transaction commit")
        return original(conn, event)

    monkeypatch.setattr(repository, "_record_inventory_event_in_conn", crash_on_risk_reservation)

    with pytest.raises(RuntimeError, match="before transaction commit"):
        repository.reserve_decision_order_and_outbox(
            decision,
            run_id="phase7-before-commit-run",
            account_id=ACCOUNT_ID,
            idempotency_key=request.idempotency_key,
            client_order_id=request.client_order_id,
            request_payload=request.model_dump(mode="json"),
            final_validation_context=validation_context(decision, request),
        )

    assert table_count(repository, "wca_decisions") == 0
    assert table_count(repository, "wca_execution_outbox") == 0
    assert table_count(repository, "wca_inventory_ledger") == 0


def test_atomic_reservation_persists_decision_outbox_and_inventory_risk_reservation() -> None:
    repository = phase7_repository()
    _, request = reserve(repository, "atomic")

    with sqlite3.connect(repository.path) as conn:
        outbox = conn.execute("SELECT status, client_order_id FROM wca_execution_outbox WHERE idempotency_key = ?", (request.idempotency_key,)).fetchone()
        events = conn.execute(
            "SELECT event_type FROM wca_inventory_ledger WHERE client_order_id = ? ORDER BY event_type",
            (request.client_order_id,),
        ).fetchall()

    assert outbox == (WcaOrderStatus.RESERVED.value, request.client_order_id)
    assert [row[0] for row in events] == ["ORDER_INTENT_RESERVED", "RISK_RESERVED"]


def test_duplicate_reservation_retries_and_multiple_workers_create_one_broker_order() -> None:
    repository = phase7_repository()
    decision, request = reserve(repository, "duplicate")
    duplicate = repository.reserve_decision_order_and_outbox(
        decision,
        run_id="phase7-duplicate-run",
        account_id=ACCOUNT_ID,
        idempotency_key=request.idempotency_key,
        client_order_id=request.client_order_id,
        request_payload=request.model_dump(mode="json"),
        final_validation_context=validation_context(decision, request),
    )
    broker = CountingCrashBroker()

    first = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="worker-a")
    second = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="worker-b")

    assert duplicate.created is False
    assert first.submitted is True
    assert second.submitted is False
    assert broker.submit_count == 1
    assert len(broker.economic_client_order_ids) == 1
    assert broker_order_count(repository) == 1


@pytest.mark.parametrize(
    ("crash", "expected_status", "expected_economic_orders"),
    (
        ("before_broker_request", WcaOrderStatus.SUBMITTING.value, 0),
        ("during_broker_request", WcaOrderStatus.UNKNOWN.value, 1),
    ),
)
def test_crash_before_or_during_broker_request_never_resubmits_on_retry(crash: str, expected_status: str, expected_economic_orders: int) -> None:
    repository = phase7_repository()
    _, request = reserve(repository, crash)
    broker = CountingCrashBroker(crash=crash)

    if crash == "before_broker_request":
        with pytest.raises(RuntimeError):
            WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="phase7")
    else:
        result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="phase7")
        assert result.state == WcaOrderStatus.UNKNOWN.value

    retry = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="phase7-retry")

    assert retry.submitted is False
    assert state_for(repository, request.idempotency_key) == expected_status
    assert len(broker.economic_client_order_ids) == expected_economic_orders
    assert broker_order_count(repository) == 0


def test_timeout_recovery_requires_reconciliation_instead_of_immediate_resubmit() -> None:
    repository = phase7_repository()
    _, request = reserve(repository, "timeout")
    broker = CountingCrashBroker(crash="during_broker_request")

    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="phase7-timeout")
    retry = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="phase7-timeout-retry")

    assert result.state == WcaOrderStatus.UNKNOWN.value
    assert retry.submitted is False
    assert broker.submit_count == 1
    assert len(broker.economic_client_order_ids) == 1


def test_crash_after_broker_acceptance_before_local_acknowledgement_never_resubmits(monkeypatch) -> None:
    repository = phase7_repository()
    _, request = reserve(repository, "after-acceptance")
    broker = CountingCrashBroker()

    def crash_before_ack(*args, **kwargs):
        raise RuntimeError("phase7 crash before local acknowledgement")

    monkeypatch.setattr(repository, "record_broker_order", crash_before_ack)

    with pytest.raises(RuntimeError, match="before local acknowledgement"):
        WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="phase7-ack")
    retry = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="phase7-ack-retry")

    assert retry.submitted is False
    assert state_for(repository, request.idempotency_key) == WcaOrderStatus.SUBMITTING.value
    assert broker.submit_count == 1
    assert len(broker.economic_client_order_ids) == 1
    assert broker_order_count(repository) == 0


def test_crash_during_partial_fill_local_update_never_resubmits(monkeypatch) -> None:
    repository = phase7_repository()
    _, request = reserve(repository, "partial")
    broker = CountingCrashBroker(partial_fill=True)

    def crash_during_fill(*args, **kwargs):
        raise RuntimeError("phase7 crash during partial fill")

    monkeypatch.setattr(repository, "apply_fill_and_update_position", crash_during_fill)

    with pytest.raises(RuntimeError, match="during partial fill"):
        WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="phase7-fill")
    retry = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="phase7-fill-retry")

    assert retry.submitted is False
    assert state_for(repository, request.idempotency_key) == WcaOrderStatus.SUBMITTING.value
    assert broker.submit_count == 1
    assert len(broker.economic_client_order_ids) == 1
    assert broker_order_count(repository) == 1


def test_cancel_transition_and_worker_restart_do_not_create_duplicate_broker_orders() -> None:
    repository = phase7_repository()
    _, request = reserve(repository, "restart")
    broker = CountingCrashBroker()
    first = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="phase7-restart-a")
    restarted_repository = WcaSqliteRepository(f"sqlite:///{repository.path}")
    second = WcaPaperBrokerOutboxAdapter().process_next_outbox(restarted_repository, broker, owner_id="phase7-restart-b")
    cancelled = restarted_repository.update_execution_outbox_state(
        outbox_id=first.outbox_id,
        status=WcaOrderStatus.CANCELLED,
        response_payload={"phase7": "cancelled-after-ack"},
    )
    after_cancel = WcaPaperBrokerOutboxAdapter().process_next_outbox(restarted_repository, broker, owner_id="phase7-restart-c")

    assert first.state == WcaOrderStatus.ACKNOWLEDGED.value
    assert second.submitted is False
    assert cancelled is True
    assert after_cancel.submitted is False
    assert broker.submit_count == 1
    assert len(broker.economic_client_order_ids) == 1
    assert broker_order_count(restarted_repository) == 1


def test_invalid_order_state_transitions_are_rejected() -> None:
    repository = phase7_repository()
    _, request = reserve(repository, "invalid-transition")

    with pytest.raises(ValueError, match="invalid WCA order state transition"):
        repository.update_execution_outbox_state(
            outbox_id=f"wca-outbox-phase7-invalid-transition-intent",
            status=WcaOrderStatus.FILLED,
        )

    assert state_for(repository, request.idempotency_key) == WcaOrderStatus.RESERVED.value


def reserve(repository: WcaSqliteRepository, suffix: str):
    decision, request = reserve_inputs(suffix)
    repository.reserve_decision_order_and_outbox(
        decision,
        run_id=f"phase7-{suffix}-run",
        account_id=ACCOUNT_ID,
        idempotency_key=request.idempotency_key,
        client_order_id=request.client_order_id,
        request_payload=request.model_dump(mode="json"),
        final_validation_context=validation_context(decision, request),
    )
    return decision, request


def reserve_inputs(suffix: str):
    decision = decision_with_order(f"phase7-{suffix}-decision", f"phase7-{suffix}-intent", f"phase7-{suffix}-key")
    assert decision.proposed_order is not None
    proposed = decision.proposed_order.model_copy(update={"account_id": ACCOUNT_ID})
    decision = decision.model_copy(update={"proposed_order": proposed})
    return decision, build_wca_paper_broker_request(proposed)


def validation_context(decision, request) -> WcaOrderValidationContext:
    return WcaOrderValidationContext(
        evaluation_timestamp=decision.decision_timestamp,
        account_id=ACCOUNT_ID,
        broker_endpoint="paper",
        runtime_mode=WcaRuntimeMode.AUTOMATIC_PAPER,
        requires_executable_paper_stage=True,
        automatic_paper_enabled=True,
        market_is_open=True,
        allowed_session_window=True,
        data_ready=decision.market_snapshot.data_ready,
        quote_freshness_seconds=15,
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


def phase7_repository() -> WcaSqliteRepository:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return WcaSqliteRepository(f"sqlite:///{root / f'wca-phase7-{uuid4().hex}.sqlite'}")


def table_count(repository: WcaSqliteRepository, table: str) -> int:
    with sqlite3.connect(repository.path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def broker_order_count(repository: WcaSqliteRepository) -> int:
    return table_count(repository, "wca_broker_orders")


def state_for(repository: WcaSqliteRepository, idempotency_key: str) -> str:
    with sqlite3.connect(repository.path) as conn:
        return conn.execute("SELECT status FROM wca_execution_outbox WHERE idempotency_key = ?", (idempotency_key,)).fetchone()[0]
