from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.wca.contracts import WcaOrderStatus
from backend.app.algorithms.wca.paper_broker import (
    WCA_REAL_MONEY_ENDPOINTS_AVAILABLE,
    WcaDeterministicPaperBroker,
    WcaPaperBrokerFill,
    WcaPaperBrokerOutboxAdapter,
    build_wca_paper_broker_request,
    cancel_wca_paper_order,
    redact_secret_payload,
    replace_wca_paper_order_requires_new_intent,
)
from backend.app.algorithms.wca.repository import WcaSqliteRepository
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
    )
    broker = WcaDeterministicPaperBroker()
    first = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10")
    second = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="step10")

    assert duplicate.created is False
    assert first.submitted is True
    assert second.submitted is False
    assert broker.submit_count == 1


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


def reserve(repository: WcaSqliteRepository, *, suffix: str = "ack"):
    decision = decision_with_order()
    assert decision.proposed_order is not None
    proposed = decision.proposed_order.model_copy(
        update={
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
    )
    assert reservation.created is True
    return decision, request


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
