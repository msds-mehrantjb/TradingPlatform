from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.wca.broker_reconciliation import reconcile_wca_broker
from backend.app.algorithms.wca.contracts import (
    ProposedOrder,
    WcaBrokerReconciliationDiscrepancy,
    WcaBrokerReconciliationResult,
    WcaOrderStatus,
    WcaSide,
)
from backend.app.algorithms.wca.market_calendar import WcaMarketCalendar
from backend.app.algorithms.wca.position_management import manage_wca_position
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.execution import BrokerFillUpdate
from backend.app.gates import BrokerAccountSnapshot, BrokerPositionState
from backend.tests.test_wca_paper_execution_pipeline import decision_with_order


NOW = datetime(2026, 7, 15, 16, 0, tzinfo=UTC)


def test_wca_position_manager_rebuilds_open_lots_and_pnl_from_durable_inventory() -> None:
    repository = repository_for_step11()
    decision = filled_decision("managed", quantity=5, stop_price=99, target_price=102)
    assert repository.apply_fill_and_update_position(
        decision,
        fill_id="fill-managed",
        account_id="paper-step11",
        quantity=5,
        broker_order_id="broker-managed",
        payload=fill_payload(entry_price=100, stop_price=99, target_price=102),
    )

    position = manage_wca_position(repository=repository, account_id="paper-step11", symbol="SPY", mark_price=101, evaluated_at=NOW)

    assert position.open_quantity == 5
    assert position.average_entry_price == 100
    assert position.unrealized_pnl == 5
    assert position.realized_pnl == 0
    assert position.stop_price == 99
    assert position.target_price == 102
    assert position.pending_exit_orders == ()
    assert position.circuit_breaker_open is False


def test_unprotected_wca_position_opens_circuit_breaker_and_keeps_risk_reducing_exit() -> None:
    repository = repository_for_step11()
    decision = filled_decision("unprotected", quantity=3, stop_price=99, target_price=102)
    assert repository.apply_fill_and_update_position(
        decision,
        fill_id="fill-unprotected",
        account_id="paper-step11",
        quantity=3,
        broker_order_id="broker-unprotected",
        payload=fill_payload(entry_price=100, stop_price=None, target_price=102),
    )

    position = manage_wca_position(repository=repository, account_id="paper-step11", symbol="SPY", mark_price=100.5, evaluated_at=NOW)

    assert position.circuit_breaker_open is True
    assert position.pending_exit_orders
    assert position.pending_exit_orders[0].side == WcaSide.SELL
    assert "wca.position.circuit_breaker.unprotected_position" in position.reason_codes
    assert repository.wca_position_circuit_breaker_open(account_id="paper-step11", symbol="SPY") is True


def test_wca_close_reduces_only_attributed_lots_and_records_realized_pnl() -> None:
    repository = repository_for_step11()
    decision = filled_decision("close", quantity=5, stop_price=99, target_price=102)
    assert repository.apply_fill_and_update_position(
        decision,
        fill_id="fill-close",
        account_id="paper-step11",
        quantity=5,
        broker_order_id="broker-close",
        payload=fill_payload(entry_price=100, stop_price=99, target_price=102),
    )

    assert repository.close_wca_attributed_position_quantity(account_id="paper-step11", symbol="SPY", quantity=3, exit_price=101, exit_reason="test_exit", evaluated_at=NOW)
    assert repository.open_wca_position_quantity(account_id="paper-step11", symbol="SPY") == 2
    assert repository.realized_pnl_for_wca_position(account_id="paper-step11", symbol="SPY") == 3
    assert not repository.close_wca_attributed_position_quantity(account_id="paper-step11", symbol="SPY", quantity=99, exit_price=101, exit_reason="over_close", evaluated_at=NOW)


def test_end_of_day_flatten_uses_early_close_calendar() -> None:
    black_friday_near_close = datetime(2026, 11, 27, 17, 56, tzinfo=UTC)

    assert WcaMarketCalendar().should_flatten(black_friday_near_close, buffer_minutes=5) is True


def test_reconciliation_blocks_entries_after_persisted_wca_discrepancy() -> None:
    repository = repository_for_step11()
    result = WcaBrokerReconciliationResult(
        reconciliation_id="step11-recon",
        account_id="paper-step11",
        evaluated_at=NOW,
        intents_checked=0,
        broker_open_orders_checked=0,
        broker_positions_checked=1,
        discrepancies=(
            WcaBrokerReconciliationDiscrepancy(
                discrepancy_type="wca_inventory_broker_mismatch",
                severity="hard",
                account_id="paper-step11",
                symbol="SPY",
                side=WcaSide.BUY,
                broker_quantity=5,
                backend_quantity=4,
                reason_codes=("wca.broker_reconciliation.wca_inventory_broker_mismatch",),
            ),
        ),
        hard_operational_warning=True,
    )

    repository.write_broker_reconciliation(result)

    assert repository.reconciliation_blocks_new_entries(account_id="paper-step11", symbol="SPY") is True


def test_reconciliation_detects_wca_inventory_and_global_net_attribution_mismatches() -> None:
    source = intent("intent-step11", "decision-step11", 5)
    repository = MemoryReconciliationRepository((source,), open_quantity=4)
    broker = FakeBroker(
        positions=[
            broker_position(source, quantity=5),
            sibling_position(quantity=7),
        ]
    )

    result = reconcile_wca_broker(
        repository=repository,
        broker=broker,
        account_id="paper-step11",
        evaluated_at=NOW,
        shared_global_attribution_ledger={"SPY": {"wca": 5, "weighted_voting": 6}},
    )

    discrepancy_types = {row.discrepancy_type for row in result.discrepancies}
    assert "wca_inventory_broker_mismatch" in discrepancy_types
    assert "broker_net_attribution_mismatch" in discrepancy_types
    assert result.hard_operational_warning is True
    net = next(row for row in result.discrepancies if row.discrepancy_type == "broker_net_attribution_mismatch")
    assert net.attribution["ledger.weighted_voting"] == "6"
    assert "another algorithm" in net.explanation


def test_step11_wca_position_modules_do_not_import_sibling_algorithm_inventory_or_settings() -> None:
    root = Path(__file__).parents[1] / "app" / "algorithms" / "wca"
    for module_name in ("position_management.py", "broker_reconciliation.py", "market_calendar.py"):
        text = (root / module_name).read_text(encoding="utf-8")
        assert "backend.app.algorithms.weighted" not in text
        assert "backend.app.algorithms.regime" not in text
        assert "backend.app.algorithms.meta" not in text
        assert "backend.app.algorithms.voting" not in text


class MemoryReconciliationRepository:
    def __init__(self, intents: tuple[ProposedOrder, ...], *, open_quantity: int) -> None:
        self.intents = intents
        self.open_quantity = open_quantity
        self.results: list[WcaBrokerReconciliationResult] = []

    def list_order_intents(self, *, account_id: str | None = None) -> tuple[ProposedOrder, ...]:
        if account_id is None:
            return self.intents
        return tuple(intent for intent in self.intents if intent.account_id == account_id)

    def has_order_fill(self, order_intent_id: str) -> bool:
        return True

    def open_wca_position_quantity(self, *, account_id: str, symbol: str) -> int:
        return self.open_quantity

    def write_broker_reconciliation(self, result: WcaBrokerReconciliationResult) -> None:
        self.results.append(result)


class FakeBroker:
    def __init__(self, *, positions: list[BrokerPositionState]) -> None:
        self.positions = positions

    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            accountId="paper-step11",
            equity=100_000,
            buyingPower=100_000,
            realizedPnlToday=0,
            positions=self.positions,
            pendingOrders=[],
            partiallyFilledOrders=[],
            observedAt=NOW,
            sessionDate=date(2026, 7, 15),
            sourceAuthority="broker",
        )

    def refresh_order(self, client_order_id: str) -> BrokerFillUpdate | None:
        return None


def repository_for_step11() -> WcaSqliteRepository:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return WcaSqliteRepository(f"sqlite:///{root / f'wca-step11-{uuid4().hex}.sqlite'}")


def filled_decision(suffix: str, *, quantity: int, stop_price: float | None, target_price: float | None):
    decision = decision_with_order()
    assert decision.proposed_order is not None
    proposed = decision.proposed_order.model_copy(
        update={
            "decision_id": f"decision-step11-{suffix}",
            "order_intent_id": f"intent-step11-{suffix}",
            "idempotency_key": f"step11-{suffix}",
            "account_id": "paper-step11",
            "quantity": quantity,
            "limit_price": 100,
            "trigger_price": 100,
            "stop_price": stop_price,
            "target_price": target_price,
        }
    )
    return decision.model_copy(update={"decision_id": proposed.decision_id, "proposed_order": proposed})


def fill_payload(*, entry_price: float, stop_price: float | None, target_price: float | None) -> dict[str, object]:
    return {
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
        "side": "BUY",
        "opened_at": NOW.isoformat(),
    }


def intent(order_intent_id: str, decision_id: str, quantity: int) -> ProposedOrder:
    return ProposedOrder(
        decision_id=decision_id,
        order_intent_id=order_intent_id,
        idempotency_key=f"client-{order_intent_id}",
        account_id="paper-step11",
        symbol="SPY",
        side=WcaSide.BUY,
        quantity=quantity,
        trigger_price=100,
        limit_price=100,
        stop_price=99,
        target_price=102,
        status=WcaOrderStatus.OUTBOX_RESERVED,
    )


def broker_position(source: ProposedOrder, *, quantity: int) -> BrokerPositionState:
    return BrokerPositionState(
        algorithmId="wca",
        capitalPartitionId="wca.paper.default",
        decisionId=source.decision_id,
        orderIntentId=source.order_intent_id,
        positionOwner="wca",
        parentOrderId=source.idempotency_key,
        symbol=source.symbol,
        side=source.side,
        quantity=quantity,
        averageEntryPrice=100,
        markPrice=101,
        stopPrice=99,
        openedAt=NOW,
    )


def sibling_position(*, quantity: int) -> BrokerPositionState:
    return BrokerPositionState(
        algorithmId="weighted_voting",
        capitalPartitionId="weighted_voting.paper.default",
        decisionId="weighted-decision",
        orderIntentId="weighted-intent",
        positionOwner="weighted_voting",
        parentOrderId="weighted-order",
        symbol="SPY",
        side="BUY",
        quantity=quantity,
        averageEntryPrice=100,
        markPrice=101,
        stopPrice=99,
        openedAt=NOW,
    )
