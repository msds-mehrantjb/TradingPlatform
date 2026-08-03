from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from datetime import datetime, timedelta, timezone

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaOrderStatus, WcaOrderValidationContext, WcaRuntimeMode
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
from backend.app.algorithms.wca.paper_account import (
    WCA_ALPACA_PAPER_ACCOUNT_ID,
    WCA_ALPACA_PAPER_API_KEY_ID,
    WCA_ALPACA_PAPER_API_SECRET_KEY,
    WCA_ALPACA_PAPER_BASE_URL,
    WCA_AUTOMATIC_PAPER_ENABLED,
    WCA_REQUIRED_ALPACA_PAPER_BASE_URL,
    validate_wca_automatic_paper_account,
)
from backend.app.algorithms.wca.repository import WcaGlobalRiskApprovalRecord, WcaSqliteRepository
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


def test_wca_automatic_paper_account_requires_dedicated_valid_env() -> None:
    missing = validate_wca_automatic_paper_account(account_id="wca-paper-1", environ={})
    live_endpoint = validate_wca_automatic_paper_account(
        account_id="wca-paper-1",
        environ=valid_wca_paper_env()
        | {
            WCA_ALPACA_PAPER_BASE_URL: "https://api.alpaca.markets",
        },
    )
    shared_credentials = validate_wca_automatic_paper_account(
        account_id="wca-paper-1",
        environ=valid_wca_paper_env()
        | {
            "APCA_API_KEY_ID": "wca-key",
            "APCA_API_SECRET_KEY": "wca-secret",
        },
    )
    account_mismatch = validate_wca_automatic_paper_account(
        account_id="other-paper",
        environ=valid_wca_paper_env(),
    )
    default_account = validate_wca_automatic_paper_account(
        account_id="paper",
        environ=valid_wca_paper_env() | {WCA_ALPACA_PAPER_ACCOUNT_ID: "paper"},
    )
    shared_account_without_allocator = validate_wca_automatic_paper_account(
        account_id="wca-paper-1",
        environ=valid_wca_paper_env() | {"WCA_ALPACA_PAPER_ACCOUNT_SHARED": "true"},
    )
    valid = validate_wca_automatic_paper_account(
        account_id="wca-paper-1",
        environ=valid_wca_paper_env(),
    )

    assert missing.verified is False
    assert "wca.paper_account.automatic_paper_disabled" in missing.reason_codes
    assert "wca.paper_account.api_key_missing" in missing.reason_codes
    assert live_endpoint.verified is False
    assert "wca.paper_account.paper_base_url_invalid" in live_endpoint.reason_codes
    assert shared_credentials.verified is False
    assert "wca.paper_account.shared_alpaca_credentials_rejected" in shared_credentials.reason_codes
    assert account_mismatch.verified is False
    assert "wca.paper_account.account_id_mismatch" in account_mismatch.reason_codes
    assert default_account.verified is False
    assert "wca.paper_account.dedicated_account_id_required" in default_account.reason_codes
    assert shared_account_without_allocator.verified is False
    assert "wca.paper_account.shared_physical_account_requires_allocator" in shared_account_without_allocator.reason_codes
    assert valid.verified is True
    assert valid.reason_codes[-1] == "wca.paper_account.verified"


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
        WCA_ALPACA_PAPER_API_KEY_ID: "wca-key",
        WCA_ALPACA_PAPER_API_SECRET_KEY: "wca-secret",
        WCA_ALPACA_PAPER_BASE_URL: WCA_REQUIRED_ALPACA_PAPER_BASE_URL,
        WCA_ALPACA_PAPER_ACCOUNT_ID: "wca-paper-1",
        WCA_AUTOMATIC_PAPER_ENABLED: "true",
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
