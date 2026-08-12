from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.app.algorithms.wca.contracts import WcaOrderStatus, WcaOrderValidationContext, WcaRuntimeMode, WcaSide
from backend.app.algorithms.wca.local_paper_broker import WcaLocalPaperBroker
from backend.app.algorithms.wca.market_calendar import WcaMarketCalendar
from backend.app.algorithms.wca.paper_broker import WcaPaperBrokerAck, WcaPaperBrokerFill, WcaPaperBrokerOutboxAdapter, WcaPaperBrokerTimeout, build_wca_paper_broker_request
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.runtime_commands import WcaRuntimeCommandType, runtime_command
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.runtime_supervisor import WcaRuntimeSettings, WcaRuntimeSupervisor, process_wca_end_of_session
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState
from backend.tests.test_wca_step6_inventory_persistence import decision_with_order


ACCOUNT_ID = "paper-phase10"
NOW = datetime(2026, 7, 15, 19, 56, tzinfo=UTC)
EARLY_CLOSE_NOW = datetime(2026, 11, 27, 17, 56, tzinfo=UTC)


class FakePhase10Broker:
    def __init__(self, *, positions=(), orders=(), fills=(), close_status: str = "filled", account_id: str = ACCOUNT_ID) -> None:
        self.positions = list(positions)
        self.orders = list(orders)
        self.fills = tuple(fills)
        self.close_status = close_status
        self.account_id = account_id
        self.cancelled_entries = 0
        self.close_calls = 0
        self.closed = False

    def verify_account_and_endpoint_identity(self):
        return True, ("wca.alpaca_paper.account_verified",)

    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            accountId=self.account_id,
            equity=100_000,
            buyingPower=100_000,
            realizedPnlToday=0,
            positions=self.positions,
            pendingOrders=self.orders,
            partiallyFilledOrders=[],
            observedAt=NOW,
            sessionDate=date(2026, 7, 15),
            sourceAuthority="broker",
        )

    def cancel_all_wca_entry_orders(self):
        entry_orders = [order for order in self.orders if order.orderType != "STOP_LIMIT"]
        self.cancelled_entries += len(entry_orders)
        self.orders = [order for order in self.orders if order.orderType == "STOP_LIMIT"]
        return tuple({"client_order_id": order.clientOrderId} for order in entry_orders)

    def read_fills_and_activities(self, *, after: datetime | None = None):
        return self.fills

    def close_or_reduce_wca_position(self, *, symbol: str, quantity: int, side: WcaSide | str, client_order_id: str):
        self.close_calls += 1
        if self.close_status == "timeout":
            raise WcaPaperBrokerTimeout("phase10 timeout")
        if self.close_status == "rejected":
            return WcaPaperBrokerAck(status="REJECTED", client_order_id=client_order_id, broker_order_id="broker-eos-rejected", message="rejected")
        filled = quantity if self.close_status == "filled" else max(1, quantity // 2)
        remaining = max(0, quantity - filled)
        updated_positions = []
        wca_reduced = False
        for position in self.positions:
            owns_wca_position = position.symbol.upper() == "SPY" and (position.algorithmId == "wca" or position.positionOwner == "wca")
            if owns_wca_position and not wca_reduced:
                wca_reduced = True
                if remaining > 0:
                    updated_positions.append(position.model_copy(update={"quantity": remaining}))
                continue
            updated_positions.append(position)
        self.positions = updated_positions
        return WcaPaperBrokerAck(
            status="ACKNOWLEDGED",
            client_order_id=client_order_id,
            broker_order_id="broker-eos-close",
            accepted_quantity=quantity,
            fill=WcaPaperBrokerFill(
                fill_id=f"fill-{client_order_id}",
                client_order_id=client_order_id,
                broker_order_id="broker-eos-close",
                filled_quantity=filled,
                remaining_quantity=remaining,
                average_fill_price=101,
                filled_at=NOW,
            ),
        )

    def poll_order_updates(self, client_order_id: str):
        return None

    def refresh_order(self, client_order_id: str):
        return None

    def close(self) -> None:
        self.closed = True


def test_normal_end_of_session_cancels_entries_reconciles_flattens_and_finalizes_state() -> None:
    repository = phase10_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    seed_open_position(repository, "normal", quantity=5)
    reserve_entry(repository, "normal-pending")
    broker = FakePhase10Broker(positions=(broker_position(quantity=5),), orders=(broker_entry_order("normal-pending"),))
    command = eos_command(NOW)

    result = process_wca_end_of_session(repository=repository, runtime_repository=runtime_repository, broker=broker, command=command, max_queue_depth=100, now=NOW)

    assert result["verified"] is True
    assert broker.cancelled_entries == 1
    assert broker.close_calls == 1
    assert repository.open_wca_position_quantity(account_id=ACCOUNT_ID, symbol="SPY") == 0
    projection = repository.read_inventory_projection(algorithm_id="wca", broker_account_id=ACCOUNT_ID, symbol="SPY")
    assert projection.open_quantity == 0
    assert projection.reserved_risk == 0
    assert ledger_count(repository, "END_OF_SESSION_FLATTEN") == 1


def test_local_paper_end_of_session_closes_wca_only_cancels_protection_and_persists_final_snapshot() -> None:
    repository = phase10_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    broker = WcaLocalPaperBroker(repository=repository, account_id=ACCOUNT_ID, symbol="SPY", starting_balance=100_000)
    stale_request = reserve_entry(repository, "local-stale")
    stale_result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="phase10-local-stale")
    entry_request = reserve_entry(repository, "local-owned")
    broker.simulate_fill(
        client_order_id=stale_request.client_order_id,
        fill_price=100.0,
        quantity=5,
        filled_at=NOW - timedelta(minutes=10),
    )
    result = process_wca_end_of_session(repository=repository, runtime_repository=runtime_repository, broker=broker, command=eos_command(NOW, mark_price=101.0), max_queue_depth=100, now=NOW)

    assert stale_result.state == WcaOrderStatus.ACKNOWLEDGED
    assert result["verified"] is True
    assert repository.open_wca_position_quantity(account_id=ACCOUNT_ID, symbol="SPY") == 0
    assert result["evidence"]["final_local_account_snapshot"]["status"] == "persisted"
    assert result["evidence"]["final_local_account_snapshot"]["realized_pnl"] == 5.0
    assert ledger_count(repository, "END_OF_SESSION_FLATTEN") == 1
    assert local_open_order_count(repository) == 0
    assert broker_order_statuses(repository, "wca-protection-%") == [WcaOrderStatus.CANCELLED.value, WcaOrderStatus.CANCELLED.value]
    assert execution_outbox_status(repository, entry_request.client_order_id) == WcaOrderStatus.CANCELLED.value

def test_early_close_uses_authoritative_calendar() -> None:
    repository = phase10_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    seed_open_position(repository, "early", quantity=1)
    broker = FakePhase10Broker(positions=(broker_position(quantity=1),))

    result = process_wca_end_of_session(repository=repository, runtime_repository=runtime_repository, broker=broker, command=eos_command(EARLY_CLOSE_NOW), max_queue_depth=100, now=EARLY_CLOSE_NOW, calendar=WcaMarketCalendar())

    assert result["verified"] is True
    assert result["evidence"]["early_close"] is True
    assert result["evidence"]["entry_cutoff_reached"] is True


def test_partial_fill_near_close_is_processed_before_flatten() -> None:
    repository = phase10_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    request = reserve_entry(repository, "partial-near-close")
    fill = WcaPaperBrokerFill(
        fill_id="phase10-late-partial-fill",
        client_order_id=request.client_order_id,
        broker_order_id="broker-late-partial",
        filled_quantity=2,
        remaining_quantity=3,
        average_fill_price=100,
        filled_at=NOW - timedelta(seconds=10),
    )
    broker = FakePhase10Broker(positions=(broker_position(quantity=2),), fills=(fill,))

    result = process_wca_end_of_session(repository=repository, runtime_repository=runtime_repository, broker=broker, command=eos_command(NOW), max_queue_depth=100, now=NOW)

    assert result["verified"] is True
    assert result["evidence"]["processed_fills"] == 1
    assert repository.open_wca_position_quantity(account_id=ACCOUNT_ID, symbol="SPY") == 0
    assert ledger_count(repository, "PARTIAL_FILL_RECEIVED") == 1
    assert ledger_count(repository, "END_OF_SESSION_FLATTEN") == 1


def test_end_of_session_flatten_disabled_does_not_close_wca_position() -> None:
    repository = phase10_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    seed_open_position(repository, "flatten-disabled", quantity=3)
    command = eos_command(NOW)
    command.payload["end_of_session_flatten_enabled"] = False
    broker = FakePhase10Broker(positions=(broker_position(quantity=3),))

    result = process_wca_end_of_session(repository=repository, runtime_repository=runtime_repository, broker=broker, command=command, max_queue_depth=100, now=NOW)

    assert result["verified"] is False
    assert "wca.runtime.end_of_session.flatten_disabled" in result["reasonCodes"]
    assert "wca.runtime.end_of_session.position_not_flat" in result["reasonCodes"]
    assert broker.close_calls == 0
    assert repository.open_wca_position_quantity(account_id=ACCOUNT_ID, symbol="SPY") == 3

def test_end_of_session_does_not_flatten_foreign_spy_position() -> None:
    repository = phase10_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    seed_open_position(repository, "foreign-broker", quantity=5)
    foreign_position = broker_position(quantity=7).model_copy(
        update={
            "algorithmId": "weighted_voting",
            "capitalPartitionId": "weighted_voting.local_paper",
            "decisionId": "foreign-decision",
            "orderIntentId": "foreign-intent",
            "positionOwner": "weighted_voting",
        }
    )
    broker = FakePhase10Broker(positions=(broker_position(quantity=5), foreign_position))

    result = process_wca_end_of_session(repository=repository, runtime_repository=runtime_repository, broker=broker, command=eos_command(NOW), max_queue_depth=100, now=NOW)

    assert result["verified"] is True
    assert repository.open_wca_position_quantity(account_id=ACCOUNT_ID, symbol="SPY") == 0
    assert len(broker.positions) == 1
    assert broker.positions[0].positionOwner == "weighted_voting"
    assert broker.positions[0].quantity == 7

def test_broker_rejection_fails_session_close_and_keeps_critical_health_path() -> None:
    repository = phase10_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    seed_open_position(repository, "reject", quantity=3)
    broker = FakePhase10Broker(positions=(broker_position(quantity=3),), close_status="rejected")

    result = process_wca_end_of_session(repository=repository, runtime_repository=runtime_repository, broker=broker, command=eos_command(NOW), max_queue_depth=100, now=NOW)

    assert result["verified"] is False
    assert "wca.runtime.end_of_session.flatten_rejected" in result["reasonCodes"]
    assert "wca.runtime.end_of_session.position_not_flat" in result["reasonCodes"]
    assert repository.open_wca_position_quantity(account_id=ACCOUNT_ID, symbol="SPY") == 3


def test_broker_timeout_fails_session_close_and_preserves_position() -> None:
    repository = phase10_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    seed_open_position(repository, "timeout", quantity=4)
    broker = FakePhase10Broker(positions=(broker_position(quantity=4),), close_status="timeout")

    result = process_wca_end_of_session(repository=repository, runtime_repository=runtime_repository, broker=broker, command=eos_command(NOW), max_queue_depth=100, now=NOW)

    assert result["verified"] is False
    assert "wca.runtime.end_of_session.flatten_timeout" in result["reasonCodes"]
    assert repository.open_wca_position_quantity(account_id=ACCOUNT_ID, symbol="SPY") == 4


def test_worker_restart_processes_durable_end_of_session_command() -> None:
    repository = phase10_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    seed_open_position(repository, "restart", quantity=2)
    command = eos_command(NOW)
    runtime_repository.enqueue_command(command)
    broker = FakePhase10Broker(positions=(broker_position(quantity=2),))
    restarted_supervisor = WcaRuntimeSupervisor(
        repository=WcaSqliteRepository(f"sqlite:///{repository.path}"),
        runtime_repository=WcaRuntimeRepository(WcaSqliteRepository(f"sqlite:///{repository.path}")),
        settings=WcaRuntimeSettings(account_id=ACCOUNT_ID),
        owner_id="phase10-restart",
    )

    with patch("backend.app.algorithms.wca.runtime_supervisor.WcaLocalPaperBroker.from_env", return_value=broker):
        result = next(worker for worker in restarted_supervisor.workers if worker.worker_name == "end_of_session_worker").run_once()

    assert result["status"] == "completed"
    assert result["verified"] is True
    assert broker.closed is True
    assert command_status(repository, command.command_id) == "completed"


def test_already_flat_session_completes_without_flatten_order() -> None:
    repository = phase10_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    broker = FakePhase10Broker()

    result = process_wca_end_of_session(repository=repository, runtime_repository=runtime_repository, broker=broker, command=eos_command(NOW), max_queue_depth=100, now=NOW)

    assert result["verified"] is True
    assert "wca.runtime.end_of_session.already_flat" in result["reasonCodes"]
    assert broker.close_calls == 0
    assert ledger_count(repository, "END_OF_SESSION_FLATTEN") == 0


def seed_open_position(repository: WcaSqliteRepository, suffix: str, *, quantity: int) -> None:
    decision = phase10_decision(f"position-{suffix}")
    assert repository.apply_fill_and_update_position(
        decision,
        fill_id=f"phase10-fill-{suffix}",
        account_id=ACCOUNT_ID,
        quantity=quantity,
        broker_order_id=f"broker-fill-{suffix}",
        payload={
            "client_order_id": f"wca-position-{suffix}",
            "entry_price": 100,
            "stop_price": 99,
            "target_price": 102,
            "opened_at": (NOW - timedelta(minutes=30)).isoformat(),
            "position_effect": "entry",
        },
    )


def reserve_entry(repository: WcaSqliteRepository, suffix: str):
    decision = phase10_decision(f"entry-{suffix}")
    request = build_wca_paper_broker_request(decision.proposed_order)
    repository.reserve_decision_order_and_outbox(
        decision,
        run_id=f"phase10-{suffix}-run",
        account_id=ACCOUNT_ID,
        idempotency_key=request.idempotency_key,
        client_order_id=request.client_order_id,
        request_payload=request.model_dump(mode="json"),
        final_validation_context=validation_context(decision, request),
    )
    return request


def phase10_decision(suffix: str):
    decision = decision_with_order(f"phase10-{suffix}-decision", f"phase10-{suffix}-intent", f"phase10-{suffix}-key")
    proposed = decision.proposed_order.model_copy(update={"account_id": ACCOUNT_ID, "quantity": 5, "limit_price": 100, "trigger_price": 100, "stop_price": 99, "target_price": 102})
    return decision.model_copy(update={"proposed_order": proposed})


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


def eos_command(now: datetime, *, mark_price: float | None = None):
    payload = {"evaluated_at": now.isoformat()}
    if mark_price is not None:
        payload["mark_price"] = mark_price
    return runtime_command(
        WcaRuntimeCommandType.END_OF_SESSION,
        account_id=ACCOUNT_ID,
        symbol="SPY",
        run_id="phase10-eos",
        payload=payload,
        reason_codes=("wca.runtime.end_of_session.requested",),
    )


def broker_position(*, quantity: int) -> BrokerPositionState:
    return BrokerPositionState(
        algorithmId="wca",
        capitalPartitionId="wca.alpaca_paper",
        decisionId="phase10-position",
        orderIntentId="phase10-position-intent",
        positionOwner="wca",
        symbol="SPY",
        side=WcaSide.BUY,
        quantity=quantity,
        averageEntryPrice=100,
        markPrice=101,
        stopPrice=99,
        openedAt=NOW - timedelta(minutes=30),
    )


def broker_entry_order(suffix: str) -> BrokerOrderState:
    return BrokerOrderState(
        algorithmId="wca",
        capitalPartitionId="wca.alpaca_paper",
        decisionId=f"phase10-entry-{suffix}-decision",
        orderIntentId=f"phase10-entry-{suffix}-intent",
        positionOwner="wca",
        symbol="SPY",
        side=WcaSide.BUY,
        clientOrderId=f"wca-entry-{suffix}",
        orderType="LIMIT",
        status="ACCEPTED",
        quantity=5,
        entryPrice=100,
        stopPrice=99,
        submittedAt=NOW - timedelta(minutes=1),
    )


def phase10_repository() -> WcaSqliteRepository:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return WcaSqliteRepository(f"sqlite:///{root / f'wca-phase10-{uuid4().hex}.sqlite'}")


def local_open_order_count(repository: WcaSqliteRepository) -> int:
    with sqlite3.connect(repository.path) as conn:
        return int(
            conn.execute(
                """
                SELECT COUNT(*) FROM wca_local_orders
                WHERE algorithm_id = 'wca' AND local_account_id = ? AND symbol = ?
                  AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
                """,
                (ACCOUNT_ID, "SPY"),
            ).fetchone()[0]
        )


def execution_outbox_status(repository: WcaSqliteRepository, client_order_id: str) -> str:
    with sqlite3.connect(repository.path) as conn:
        row = conn.execute(
            "SELECT status FROM wca_execution_outbox WHERE algorithm_id = 'wca' AND account_id = ? AND symbol = ? AND client_order_id = ?",
            (ACCOUNT_ID, "SPY", client_order_id),
        ).fetchone()
    return str(row[0]) if row is not None else ""

def broker_order_statuses(repository: WcaSqliteRepository, client_order_match: str) -> list[str]:
    operator = "LIKE" if "%" in client_order_match else "="
    with sqlite3.connect(repository.path) as conn:
        rows = conn.execute(
            f"""
            SELECT status FROM wca_broker_orders
            WHERE algorithm_id = 'wca' AND account_id = ? AND symbol = ? AND client_order_id {operator} ?
            ORDER BY client_order_id
            """,
            (ACCOUNT_ID, "SPY", client_order_match),
        ).fetchall()
    return [str(row[0]) for row in rows]

def ledger_count(repository: WcaSqliteRepository, event_type: str) -> int:
    with sqlite3.connect(repository.path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM wca_inventory_ledger WHERE event_type = ?", (event_type,)).fetchone()[0])


def command_status(repository: WcaSqliteRepository, command_id: str) -> str:
    with sqlite3.connect(repository.path) as conn:
        return str(conn.execute("SELECT status FROM wca_runtime_command_queue WHERE command_id = ?", (command_id,)).fetchone()[0])
