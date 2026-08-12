from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from backend.app.algorithms.wca.broker_reconciliation import reconcile_wca_broker
from backend.app.algorithms.wca.contracts import WcaOrderStatus, WcaOrderValidationContext, WcaRuntimeMode, WcaSide
from backend.app.algorithms.wca.paper_account import (
    WCA_AUTOMATIC_PAPER_ENABLED,
    WCA_LOCAL_PAPER_ACCOUNT_ID,
)
from backend.app.algorithms.wca.paper_broker import build_wca_paper_broker_request
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.runtime_supervisor import WcaRuntimeSettings, WcaRuntimeSupervisor
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState
from backend.tests.test_wca_step6_inventory_persistence import decision_with_order


NOW = datetime(2026, 7, 16, 16, 0, tzinfo=UTC)
SESSION_DATE = date(2026, 7, 16)
ACCOUNT_ID = "paper-phase8"


class FakePhase8Broker:
    def __init__(self, *, orders=(), partial_orders=(), positions=(), updates=None, fills=(), account_id: str = ACCOUNT_ID, source: str = "broker") -> None:
        self.orders = list(orders)
        self.partial_orders = list(partial_orders)
        self.positions = list(positions)
        self.updates = updates or {}
        self.fills = tuple(fills)
        self.account_id = account_id
        self.source = source
        self.closed = False

    def verify_account_and_endpoint_identity(self):
        return (self.account_id == ACCOUNT_ID, ("wca.alpaca_paper.account_verified",) if self.account_id == ACCOUNT_ID else ("wca.alpaca_paper.account_id_mismatch",))

    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            accountId=self.account_id,
            equity=100_000,
            buyingPower=50_000,
            realizedPnlToday=0,
            positions=self.positions,
            pendingOrders=self.orders,
            partiallyFilledOrders=self.partial_orders,
            observedAt=NOW,
            sessionDate=SESSION_DATE,
            sourceAuthority=self.source,
        )

    def poll_order_updates(self, client_order_id: str):
        return self.updates.get(client_order_id)

    def refresh_order(self, client_order_id: str):
        return None

    def read_fills_and_activities(self, *, after: datetime | None = None):
        return self.fills

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("name", "broker_factory", "expected"),
    (
        ("local_order_missing_at_broker", lambda local: FakePhase8Broker(), "missing_broker_order"),
        ("broker_order_missing_locally", lambda local: FakePhase8Broker(orders=(broker_order("unknown-intent", "unknown-decision", "wca-unknown-client"),)), "broker_order_missing_locally"),
        ("partial_fill_not_processed", lambda local: FakePhase8Broker(partial_orders=(broker_order(local.order_intent_id, local.decision_id, local.client_order_id, filled=2),)), "partial_fill_not_processed"),
        ("rejection_not_processed", lambda local: FakePhase8Broker(updates={local.client_order_id: {"client_order_id": local.client_order_id, "status": "rejected", "filled_qty": "0"}}), "rejection_not_processed"),
        ("cancelled_order_still_open", lambda local: FakePhase8Broker(updates={local.client_order_id: {"client_order_id": local.client_order_id, "status": "canceled", "filled_qty": "0"}}), "cancelled_order_still_open"),
        ("filled_order_still_pending", lambda local: FakePhase8Broker(updates={local.client_order_id: {"client_order_id": local.client_order_id, "status": "filled", "filled_qty": "5"}}), "filled_order_still_pending"),
        ("orphan_protective_order", lambda local: FakePhase8Broker(orders=(broker_order("unknown-exit-intent", "unknown-exit-decision", "wca-exit-unknown", order_type="STOP_LIMIT", exit_owner="wca"),)), "orphan_protective_order"),
        ("position_without_protection", lambda local: FakePhase8Broker(positions=(broker_position(local.order_intent_id, local.decision_id, stop_price=None),)), "position_without_protection"),
        ("unknown_wca_prefixed_broker_order", lambda local: FakePhase8Broker(orders=(broker_order(local.order_intent_id, local.decision_id, "wca-unexpected-client"),)), "unknown_wca_prefixed_broker_order"),
        ("unexpected_account_spy_position", lambda local: FakePhase8Broker(positions=(BrokerPositionState(algorithmId="meta_strategy", capitalPartitionId="meta_strategy.paper", positionOwner="meta_strategy", symbol="SPY", side="BUY", quantity=3, averageEntryPrice=100, markPrice=101),)), "unexpected_account_spy_position"),
    ),
)
def test_phase8_discrepancy_scenarios_block_entries_and_open_circuit_breaker(name, broker_factory, expected) -> None:
    repository = phase8_repository()
    local = reserve(repository, name)

    result = reconcile_wca_broker(repository=repository, broker=broker_factory(local), account_id=ACCOUNT_ID, evaluated_at=NOW)

    discrepancy_types = {row.discrepancy_type for row in result.discrepancies}
    assert expected in discrepancy_types
    assert result.hard_operational_warning is True or expected == "missing_broker_order"
    assert repository.reconciliation_blocks_new_entries(account_id=ACCOUNT_ID, symbol="SPY") is True
    assert repository.wca_position_circuit_breaker_open(account_id=ACCOUNT_ID, symbol="SPY") is True


def test_startup_reconciliation_uses_local_paper_account_and_clears_startup_entry_gate() -> None:
    repository = phase8_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    broker = FakePhase8Broker()
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(account_id=ACCOUNT_ID),
        owner_id="phase8-startup",
    )

    assert repository.reconciliation_blocks_new_entries(account_id=ACCOUNT_ID, symbol="SPY") is True
    with patch.dict("os.environ", valid_env(), clear=True), patch("backend.app.algorithms.wca.runtime_supervisor.WcaLocalPaperBroker.from_env", return_value=broker):
        result = next(worker for worker in supervisor.workers if worker.worker_name == "broker_reconciliation_worker").run_once()

    assert result["status"] == "completed"
    assert "wca.runtime.broker_reconciliation.startup_completed" in result["reasonCodes"]
    assert repository.reconciliation_blocks_new_entries(account_id=ACCOUNT_ID, symbol="SPY") is False
    assert broker.closed is True
    with sqlite3.connect(repository.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM wca_broker_account_snapshots").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM wca_broker_reconciliations WHERE discrepancy_count = 0").fetchone()[0] == 1


@pytest.mark.parametrize(
    "status",
    (
        WcaOrderStatus.RESERVED.value,
        WcaOrderStatus.SUBMITTING.value,
        WcaOrderStatus.SUBMITTED.value,
        WcaOrderStatus.ACKNOWLEDGED.value,
        WcaOrderStatus.PARTIALLY_FILLED.value,
        WcaOrderStatus.FILLED.value,
        WcaOrderStatus.CANCEL_PENDING.value,
        WcaOrderStatus.CANCELLED.value,
        WcaOrderStatus.REJECTED.value,
        WcaOrderStatus.UNKNOWN.value,
        WcaOrderStatus.RECONCILING.value,
        WcaOrderStatus.RECONCILED.value,
        WcaOrderStatus.DEAD_LETTER.value,
    ),
)
def test_restart_reconciles_from_every_order_state_without_submission(status: str) -> None:
    repository = phase8_repository()
    local = reserve(repository, f"restart-{status.lower()}")
    with sqlite3.connect(repository.path) as conn:
        conn.execute("UPDATE wca_execution_outbox SET status = ? WHERE client_order_id = ?", (status, local.client_order_id))
        conn.commit()
    restarted = WcaSqliteRepository(f"sqlite:///{repository.path}")

    result = reconcile_wca_broker(
        repository=restarted,
        broker=FakePhase8Broker(orders=(broker_order(local.order_intent_id, local.decision_id, local.client_order_id),)),
        account_id=ACCOUNT_ID,
        evaluated_at=NOW,
    )

    assert result.reconciliation_id
    assert table_count(restarted, "wca_broker_orders") == 0
    assert table_count(restarted, "wca_broker_reconciliations") == 1


def test_account_mismatch_blocks_entries_without_assigning_broker_state_to_wca() -> None:
    repository = phase8_repository()
    result = reconcile_wca_broker(repository=repository, broker=FakePhase8Broker(account_id="other-paper"), account_id=ACCOUNT_ID, evaluated_at=NOW)

    assert {row.discrepancy_type for row in result.discrepancies} == {"broker_account_identity_mismatch"}
    assert repository.reconciliation_blocks_new_entries(account_id=ACCOUNT_ID, symbol="SPY") is True


def reserve(repository: WcaSqliteRepository, suffix: str):
    decision = decision_with_order(f"phase8-{suffix}-decision", f"phase8-{suffix}-intent", f"phase8-{suffix}-key")
    assert decision.proposed_order is not None
    proposed = decision.proposed_order.model_copy(update={"account_id": ACCOUNT_ID})
    decision = decision.model_copy(update={"proposed_order": proposed})
    request = build_wca_paper_broker_request(proposed)
    repository.reserve_decision_order_and_outbox(
        decision,
        run_id=f"phase8-{suffix}-run",
        account_id=ACCOUNT_ID,
        idempotency_key=request.idempotency_key,
        client_order_id=request.client_order_id,
        request_payload=request.model_dump(mode="json"),
        final_validation_context=validation_context(decision, request),
    )
    return request


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


def broker_order(order_intent_id: str, decision_id: str, client_order_id: str, *, filled: int = 0, order_type: str = "LIMIT", exit_owner: str | None = None) -> BrokerOrderState:
    return BrokerOrderState(
        algorithmId="wca",
        capitalPartitionId="wca.alpaca_paper",
        decisionId=decision_id,
        orderIntentId=order_intent_id,
        positionOwner="wca",
        exitOwner=exit_owner,
        symbol="SPY",
        side="BUY",
        clientOrderId=client_order_id,
        orderType=order_type,
        status="PARTIALLY_FILLED" if filled else "ACCEPTED",
        quantity=5,
        filledQuantity=filled,
        entryPrice=100,
        stopPrice=99 if order_type == "LIMIT" else None,
        submittedAt=NOW - timedelta(minutes=10),
    )


def broker_position(order_intent_id: str, decision_id: str, *, stop_price: float | None = 99) -> BrokerPositionState:
    return BrokerPositionState(
        algorithmId="wca",
        capitalPartitionId="wca.alpaca_paper",
        decisionId=decision_id,
        orderIntentId=order_intent_id,
        positionOwner="wca",
        symbol="SPY",
        side=WcaSide.BUY,
        quantity=5,
        averageEntryPrice=100,
        markPrice=101,
        stopPrice=stop_price,
        openedAt=NOW,
    )


def phase8_repository() -> WcaSqliteRepository:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return WcaSqliteRepository(f"sqlite:///{root / f'wca-phase8-{uuid4().hex}.sqlite'}")


def table_count(repository: WcaSqliteRepository, table: str) -> int:
    with sqlite3.connect(repository.path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def valid_env() -> dict[str, str]:
    return {
        WCA_AUTOMATIC_PAPER_ENABLED: "true",
        WCA_LOCAL_PAPER_ACCOUNT_ID: ACCOUNT_ID,
    }
