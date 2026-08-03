from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from backend.app.algorithms.wca.contracts import WcaOrderStatus, WcaOrderValidationContext, WcaRuntimeMode
from backend.app.algorithms.wca.paper_broker import WcaPaperBrokerAck, WcaPaperBrokerFill, WcaPaperBrokerOutboxAdapter, build_wca_paper_broker_request
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.runtime_commands import WcaRuntimeCommandType, runtime_command
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.runtime_supervisor import WcaRuntimeSettings, WcaRuntimeSupervisor, _process_observed_fills
from backend.app.gates import BrokerAccountSnapshot, BrokerPositionState
from backend.tests.test_wca_phase9_position_protection import ProtectiveBroker, reserve as phase9_reserve
from backend.tests.test_wca_step6_inventory_persistence import decision_with_order


NOW = datetime(2026, 7, 20, 16, 0, tzinfo=UTC)
SESSION_DATE = date(2026, 7, 20)
ACCOUNT_ID = "paper-phase10"


def test_broker_reconciliation_worker_polls_orders_fills_updates_inventory_and_reconciles_clean() -> None:
    repository = phase10_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    decision, request = reserve(repository, "worker-poll")
    fill = WcaPaperBrokerFill(
        fill_id="phase10-entry-fill",
        client_order_id=request.client_order_id,
        broker_order_id=f"alpaca-{request.client_order_id}",
        filled_quantity=request.quantity,
        remaining_quantity=0,
        average_fill_price=request.limit_price,
        filled_at=NOW,
    )
    broker = Phase10PollingBroker(decision_id=decision.decision_id, order_intent_id=request.order_intent_id, client_order_id=request.client_order_id, fill=fill)
    runtime_repository.enqueue_command(
        runtime_command(
            WcaRuntimeCommandType.BROKER_RECONCILIATION,
            account_id=ACCOUNT_ID,
            symbol="SPY",
            decision_id=decision.decision_id,
            run_id="phase10-worker-run",
            reason_codes=("wca.test.phase10.reconciliation_requested",),
        )
    )
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(account_id=ACCOUNT_ID, symbol="SPY"),
        owner_id="phase10-worker",
    )

    with patch("backend.app.algorithms.wca.runtime_supervisor.WcaAlpacaPaperBroker.from_env", return_value=broker):
        result = next(worker for worker in supervisor.workers if worker.worker_name == "broker_reconciliation_worker").run_once()

    projection = repository.read_inventory_projection(algorithm_id="wca", broker_account_id=ACCOUNT_ID, symbol="SPY")
    daily = repository.read_daily_state_projection(algorithm_id="wca", broker_account_id=ACCOUNT_ID, symbol="SPY", session_date=NOW.date().isoformat())
    with sqlite3.connect(repository.path) as conn:
        outbox_status = conn.execute("SELECT status FROM wca_execution_outbox WHERE client_order_id = ?", (request.client_order_id,)).fetchone()[0]
        reconciliation = conn.execute("SELECT discrepancy_count FROM wca_broker_reconciliations ORDER BY created_at DESC LIMIT 1").fetchone()[0]

    assert result["status"] == "completed"
    assert result["ordersPolled"] == 1
    assert result["fillsProcessed"] == 1
    assert "wca.runtime.broker_polling.reconciled_after_updates" in result["reasonCodes"]
    assert outbox_status == WcaOrderStatus.FILLED.value
    assert projection.open_quantity == request.quantity
    assert projection.reserved_risk == 0
    assert daily.last_entry_timestamp is not None
    assert len([order for order in broker.submitted_requests if order.client_order_id.startswith("wca-protection-")]) == 2
    assert reconciliation == 0
    assert repository.reconciliation_blocks_new_entries(account_id=ACCOUNT_ID, symbol="SPY") is False


def test_duplicate_protective_fill_activity_is_deduped_and_updates_projection_daily_pnl() -> None:
    repository = phase10_repository()
    _, request = phase9_reserve(repository, "phase10-duplicate-protection")
    entry_fill = WcaPaperBrokerFill(
        fill_id="phase10-protective-entry",
        client_order_id=request.client_order_id,
        broker_order_id=f"alpaca-{request.client_order_id}",
        filled_quantity=request.quantity,
        remaining_quantity=0,
        average_fill_price=request.limit_price,
        filled_at=NOW,
    )
    broker = ProtectiveBroker(entry_fill=entry_fill)
    WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker, owner_id="phase10-protection-entry")
    target_client_id = next(item.client_order_id for item in broker.submitted_requests if item.client_order_id.startswith("wca-protection-") and item.order_type == "LIMIT")
    target_fill = WcaPaperBrokerFill(
        fill_id="phase10-target-fill-duplicate",
        client_order_id=target_client_id,
        broker_order_id=f"alpaca-{target_client_id}",
        filled_quantity=request.quantity,
        remaining_quantity=0,
        average_fill_price=102,
        filled_at=NOW + timedelta(seconds=30),
    )
    broker.activity_fills = (target_fill, target_fill)

    first = _process_observed_fills(repository, broker)
    second = _process_observed_fills(repository, broker)

    projection = repository.read_inventory_projection(algorithm_id="wca", broker_account_id=request.account_id, symbol="SPY")
    daily = repository.read_daily_state_projection(algorithm_id="wca", broker_account_id=request.account_id, symbol="SPY", session_date=NOW.date().isoformat())
    with sqlite3.connect(repository.path) as conn:
        trade_count = conn.execute("SELECT COUNT(*) FROM wca_trade_ledger WHERE payload_json LIKE '%phase10-target-fill-duplicate%'").fetchone()[0]
        fill_count = conn.execute("SELECT COUNT(*) FROM wca_attributed_fills WHERE fill_id = ?", (target_fill.fill_id,)).fetchone()[0]

    assert first == 1
    assert second == 0
    assert projection.open_quantity == 0
    assert projection.realized_pnl == 10
    assert daily.trades_completed_today == 1
    assert daily.realized_pnl_today == 10
    assert trade_count == 1
    assert fill_count == 1


def reserve(repository: WcaSqliteRepository, suffix: str):
    decision = decision_with_order(f"phase10-{suffix}-decision", f"phase10-{suffix}-intent", f"phase10-{suffix}-key")
    assert decision.proposed_order is not None
    proposed = decision.proposed_order.model_copy(update={"account_id": ACCOUNT_ID, "quantity": 5, "limit_price": 100, "trigger_price": 100, "stop_price": 99, "target_price": 102})
    decision = decision.model_copy(update={"proposed_order": proposed})
    request = build_wca_paper_broker_request(proposed)
    repository.reserve_decision_order_and_outbox(
        decision,
        run_id=f"phase10-{suffix}-run",
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


def phase10_repository() -> WcaSqliteRepository:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return WcaSqliteRepository(f"sqlite:///{root / f'wca-phase10-{uuid4().hex}.sqlite'}")


class Phase10PollingBroker:
    def __init__(self, *, decision_id: str, order_intent_id: str, client_order_id: str, fill: WcaPaperBrokerFill) -> None:
        self.decision_id = decision_id
        self.order_intent_id = order_intent_id
        self.client_order_id = client_order_id
        self.fill = fill
        self.entry_processed = False
        self.submitted_requests = []
        self.orders: dict[str, dict[str, object]] = {
            client_order_id: {
                "id": fill.broker_order_id,
                "client_order_id": client_order_id,
                "status": "filled",
                "qty": str(fill.filled_quantity),
                "filled_qty": str(fill.filled_quantity),
                "filled_avg_price": str(fill.average_fill_price),
            }
        }
        self.closed = False

    def verify_account_and_endpoint_identity(self):
        return True, ("wca.alpaca_paper.account_verified",)

    def poll_order_updates(self, client_order_id: str):
        return self.orders.get(client_order_id)

    def refresh_order(self, client_order_id: str):
        return self.orders.get(client_order_id)

    def read_fills_and_activities(self, *, after=None):
        if self.entry_processed:
            return ()
        self.entry_processed = True
        return (self.fill,)

    def submit_order(self, request):
        self.submitted_requests.append(request)
        broker_order_id = f"alpaca-{request.client_order_id}"
        self.orders[request.client_order_id] = {
            "id": broker_order_id,
            "client_order_id": request.client_order_id,
            "status": "accepted",
            "qty": str(request.quantity),
            "filled_qty": "0",
            "limit_price": str(request.limit_price),
        }
        return WcaPaperBrokerAck(
            status="ACKNOWLEDGED",
            client_order_id=request.client_order_id,
            broker_order_id=broker_order_id,
            accepted_quantity=request.quantity,
            response_payload=self.orders[request.client_order_id],
        )

    def read_open_orders(self):
        return tuple(SimpleNamespace(clientOrderId=order["client_order_id"]) for order in self.orders.values() if order["status"] == "accepted")

    def find_order_by_client_order_id(self, client_order_id: str):
        order = self.orders.get(client_order_id)
        return None if order is None else dict(order)

    def cancel_order(self, broker_order_id: str):
        for order in self.orders.values():
            if order["id"] == broker_order_id:
                order["status"] = "canceled"
                return {"id": broker_order_id, "status": "canceled"}
        return {"id": broker_order_id, "status": "missing"}

    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        positions = []
        if self.entry_processed:
            positions.append(
                BrokerPositionState(
                    algorithmId="wca",
                    capitalPartitionId="wca.alpaca_paper",
                    decisionId=self.decision_id,
                    orderIntentId=self.order_intent_id,
                    positionOwner="wca",
                    symbol="SPY",
                    side="BUY",
                    quantity=self.fill.filled_quantity,
                    averageEntryPrice=float(self.fill.average_fill_price or 100),
                    markPrice=101,
                    stopPrice=99,
                    openedAt=self.fill.filled_at,
                )
            )
        return BrokerAccountSnapshot(
            accountId=ACCOUNT_ID,
            equity=100_000,
            buyingPower=50_000,
            realizedPnlToday=0,
            positions=positions,
            pendingOrders=[],
            partiallyFilledOrders=[],
            observedAt=NOW,
            sessionDate=SESSION_DATE,
            sourceAuthority="broker",
        )

    def close(self) -> None:
        self.closed = True
