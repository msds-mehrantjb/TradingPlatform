from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.wca.contracts import WcaOrderStatus, WcaOrderValidationContext, WcaRuntimeMode, WcaSide
from backend.app.algorithms.wca.paper_broker import (
    WcaDeterministicPaperBroker,
    WcaPaperBrokerFill,
    WcaPaperBrokerOutboxAdapter,
    build_wca_paper_broker_request,
    cancel_wca_paper_order,
)
from backend.app.algorithms.wca.position_management import WcaPositionManagementSettings, manage_wca_position
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.tests.test_wca_step6_inventory_persistence import decision_with_order


ACCOUNT_ID = "paper-phase9"
NOW = datetime(2026, 7, 15, 16, 0, tzinfo=UTC)


def test_partial_entry_fill_records_protection_and_duplicate_fill_is_idempotent() -> None:
    repository = phase9_repository()
    decision, request = reserve(repository, "partial-protection")
    fill = WcaPaperBrokerFill(
        fill_id="phase9-partial-fill",
        client_order_id=request.client_order_id,
        broker_order_id=f"alpaca-{request.client_order_id}",
        filled_quantity=2,
        remaining_quantity=3,
        average_fill_price=request.limit_price,
        filled_at=NOW,
    )

    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(
        repository,
        WcaDeterministicPaperBroker(fill=fill),
        owner_id="phase9-partial",
    )
    duplicate = repository.apply_fill_and_update_position(
        decision,
        fill_id=fill.fill_id,
        account_id=ACCOUNT_ID,
        quantity=fill.filled_quantity,
        broker_order_id=fill.broker_order_id,
        payload={
            "client_order_id": request.client_order_id,
            "entry_price": request.limit_price,
            "stop_price": decision.proposed_order.stop_price,
            "target_price": decision.proposed_order.target_price,
            "opened_at": NOW.isoformat(),
            "remaining_quantity": fill.remaining_quantity,
        },
    )

    events = ledger_events(repository)
    protective = [event for event in events if event["event_type"] == "PROTECTIVE_ORDER_CREATED"]
    partials = [event for event in events if event["event_type"] == "PARTIAL_FILL_RECEIVED"]
    assert result.state == WcaOrderStatus.PARTIALLY_FILLED
    assert duplicate is False
    assert len(partials) == 1
    assert len(protective) == 1
    assert protective[0]["payload"]["source_fill_id"] == fill.fill_id
    assert protective[0]["payload"]["protected_quantity"] == 2


def test_rejected_protection_opens_circuit_breaker_and_persists_critical_event() -> None:
    repository = phase9_repository()
    decision = phase9_decision("rejected-protection")
    assert repository.apply_fill_and_update_position(
        decision,
        fill_id="phase9-unprotected-fill",
        account_id=ACCOUNT_ID,
        quantity=3,
        broker_order_id="broker-unprotected",
        payload={
            "client_order_id": "wca-unprotected",
            "entry_price": 100,
            "stop_price": None,
            "target_price": 102,
            "opened_at": (NOW - timedelta(seconds=5)).isoformat(),
        },
    )

    position = manage_wca_position(repository=repository, account_id=ACCOUNT_ID, symbol="SPY", mark_price=100.5, evaluated_at=NOW)

    critical = [event for event in ledger_events(repository) if event["event_type"] == "RECONCILIATION_CORRECTION" and event["payload"].get("critical")]
    assert position.circuit_breaker_open is True
    assert position.pending_exit_orders
    assert "wca.position.circuit_breaker.unprotected_position" in position.reason_codes
    assert repository.wca_position_circuit_breaker_open(account_id=ACCOUNT_ID, symbol="SPY") is True
    assert critical
    assert critical[-1]["payload"]["protective_exit_required"] is True


def test_position_worker_recovers_authoritative_inventory_after_restart() -> None:
    repository = phase9_repository()
    decision = phase9_decision("restart")
    assert repository.apply_fill_and_update_position(
        decision,
        fill_id="phase9-restart-fill",
        account_id=ACCOUNT_ID,
        quantity=5,
        broker_order_id="broker-restart",
        payload=fill_payload(entry_price=100, stop_price=99, target_price=102),
    )

    restarted = WcaSqliteRepository(f"sqlite:///{repository.path}")
    position = manage_wca_position(repository=restarted, account_id=ACCOUNT_ID, symbol="SPY", mark_price=101, evaluated_at=NOW)

    assert position.open_quantity == 5
    assert position.average_entry_price == 100
    assert position.pending_exit_orders == ()
    assert any(event["event_type"] == "PROTECTIVE_ORDER_CREATED" for event in ledger_events(restarted))


def test_rapid_entry_to_stop_exit_and_partial_full_exit_updates_authoritative_lots() -> None:
    repository = phase9_repository()
    decision = phase9_decision("rapid-stop")
    assert repository.apply_fill_and_update_position(
        decision,
        fill_id="phase9-stop-fill",
        account_id=ACCOUNT_ID,
        quantity=5,
        broker_order_id="broker-stop",
        payload=fill_payload(entry_price=100, stop_price=99, target_price=102),
    )

    position = manage_wca_position(repository=repository, account_id=ACCOUNT_ID, symbol="SPY", mark_price=98.75, evaluated_at=NOW)
    partial_exit = repository.close_wca_attributed_position_quantity(account_id=ACCOUNT_ID, symbol="SPY", quantity=2, exit_price=99, exit_reason="stop_loss_exit", evaluated_at=NOW)
    remaining_after_partial = repository.open_wca_position_quantity(account_id=ACCOUNT_ID, symbol="SPY")
    full_exit = repository.close_wca_attributed_position_quantity(account_id=ACCOUNT_ID, symbol="SPY", quantity=3, exit_price=99, exit_reason="stop_loss_exit", evaluated_at=NOW)

    assert position.pending_exit_orders
    assert position.pending_exit_orders[0].side == WcaSide.SELL
    assert "wca.position.exit.stop_loss" in position.reason_codes
    assert partial_exit is True
    assert remaining_after_partial == 3
    assert full_exit is True
    assert repository.open_wca_position_quantity(account_id=ACCOUNT_ID, symbol="SPY") == 0


def test_short_position_requires_explicit_configuration() -> None:
    repository = phase9_repository()
    short_decision = phase9_decision("short").model_copy(
        update={"proposed_order": phase9_decision("short").proposed_order.model_copy(update={"side": WcaSide.SELL})}
    )
    assert repository.apply_fill_and_update_position(
        short_decision,
        fill_id="phase9-short-fill",
        account_id=ACCOUNT_ID,
        quantity=2,
        broker_order_id="broker-short",
        payload=fill_payload(entry_price=100, stop_price=101, target_price=98, side="SELL"),
    )

    blocked_short = manage_wca_position(repository=repository, account_id=ACCOUNT_ID, symbol="SPY", mark_price=100, evaluated_at=NOW)
    allowed_short = manage_wca_position(
        repository=repository,
        account_id=ACCOUNT_ID,
        symbol="SPY",
        mark_price=100,
        evaluated_at=NOW,
        settings=WcaPositionManagementSettings(short_positions_allowed=True),
    )

    assert "wca.position.circuit_breaker.short_not_allowed" in blocked_short.reason_codes
    assert blocked_short.pending_exit_orders[0].side == WcaSide.BUY
    assert "wca.position.circuit_breaker.short_not_allowed" not in allowed_short.reason_codes


def test_position_increase_requires_explicit_configuration() -> None:
    repository = phase9_repository()
    first = phase9_decision("increase-a")
    second = phase9_decision("increase-b")
    assert repository.apply_fill_and_update_position(
        first,
        fill_id="phase9-increase-fill-a",
        account_id=ACCOUNT_ID,
        quantity=2,
        broker_order_id="broker-increase-a",
        payload=fill_payload(entry_price=100, stop_price=99, target_price=102),
    )
    assert repository.apply_fill_and_update_position(
        second,
        fill_id="phase9-increase-fill-b",
        account_id=ACCOUNT_ID,
        quantity=2,
        broker_order_id="broker-increase-b",
        payload=fill_payload(entry_price=101, stop_price=100, target_price=103),
    )

    blocked_increase = manage_wca_position(repository=repository, account_id=ACCOUNT_ID, symbol="SPY", mark_price=101, evaluated_at=NOW)
    allowed_increase = manage_wca_position(
        repository=repository,
        account_id=ACCOUNT_ID,
        symbol="SPY",
        mark_price=101,
        evaluated_at=NOW,
        settings=WcaPositionManagementSettings(allow_position_increase=True),
    )

    assert "wca.position.circuit_breaker.position_increase_not_allowed" in blocked_increase.reason_codes
    assert blocked_increase.pending_exit_orders[0].side == WcaSide.SELL
    assert "wca.position.circuit_breaker.position_increase_not_allowed" not in allowed_increase.reason_codes


def test_rejected_and_cancelled_entries_release_reserved_risk_events() -> None:
    repository = phase9_repository()
    _, rejected_request = reserve(repository, "rejected")
    rejected = WcaPaperBrokerOutboxAdapter().process_next_outbox(
        repository,
        WcaDeterministicPaperBroker(ack_status="REJECTED"),
        owner_id="phase9-rejected",
    )
    _, cancelled_request = reserve(repository, "cancelled")
    cancelled = cancel_wca_paper_order(
        repository,
        outbox_id=f"wca-outbox-phase9-cancelled-intent",
        cancellation_idempotency_key="phase9-cancelled-cancel-key",
        original_idempotency_key=cancelled_request.idempotency_key,
    )

    events = ledger_events(repository)
    assert rejected.state == WcaOrderStatus.REJECTED
    assert cancelled is True
    assert any(event["event_type"] == "ORDER_REJECTED" and event["reserved_risk"] > 0 for event in events)
    assert any(event["event_type"] == "ORDER_CANCELLED" and event["reserved_risk"] > 0 for event in events)


def reserve(repository: WcaSqliteRepository, suffix: str):
    decision = phase9_decision(suffix)
    request = build_wca_paper_broker_request(decision.proposed_order)
    repository.reserve_decision_order_and_outbox(
        decision,
        run_id=f"phase9-{suffix}-run",
        account_id=ACCOUNT_ID,
        idempotency_key=request.idempotency_key,
        client_order_id=request.client_order_id,
        request_payload=request.model_dump(mode="json"),
        final_validation_context=validation_context(decision, request),
    )
    return decision, request


def validation_context(decision, request) -> WcaOrderValidationContext:
    return WcaOrderValidationContext(
        evaluation_timestamp=decision.decision_timestamp,
        account_id=ACCOUNT_ID,
        broker_endpoint="paper",
        runtime_mode=WcaRuntimeMode.AUTOMATIC_PAPER,
        requires_executable_paper_stage=True,
        automatic_paper_enabled=True,
        data_ready=decision.market_snapshot.data_ready,
        quote_freshness_seconds=15,
        candle_freshness_seconds=120,
        available_buying_power=100_000,
        account_equity=100_000,
        max_position_value=100_000,
        max_approved_quantity=1000,
        order_type=request.order_type,
        time_in_force=request.time_in_force,
        idempotency_required=True,
        protective_exit_plan_present=True,
    )


def phase9_decision(suffix: str):
    decision = decision_with_order(f"phase9-{suffix}-decision", f"phase9-{suffix}-intent", f"phase9-{suffix}-key")
    proposed = decision.proposed_order.model_copy(update={"account_id": ACCOUNT_ID, "quantity": 5, "limit_price": 100, "trigger_price": 100, "stop_price": 99, "target_price": 102})
    return decision.model_copy(update={"proposed_order": proposed})


def fill_payload(*, entry_price: float, stop_price: float | None, target_price: float | None, side: str = "BUY") -> dict[str, object]:
    return {
        "client_order_id": f"wca-phase9-{uuid4().hex[:8]}",
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
        "side": side,
        "opened_at": NOW.isoformat(),
        "position_effect": "entry",
    }


def ledger_events(repository: WcaSqliteRepository) -> list[dict[str, object]]:
    with sqlite3.connect(repository.path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT event_type, fill_id, reserved_risk, payload_json FROM wca_inventory_ledger ORDER BY event_timestamp, inventory_event_id").fetchall()
    return [
        {
            "event_type": row["event_type"],
            "fill_id": row["fill_id"],
            "reserved_risk": float(row["reserved_risk"]),
            "payload": json.loads(row["payload_json"] or "{}"),
        }
        for row in rows
    ]


def phase9_repository() -> WcaSqliteRepository:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return WcaSqliteRepository(f"sqlite:///{root / f'wca-phase9-{uuid4().hex}.sqlite'}")
