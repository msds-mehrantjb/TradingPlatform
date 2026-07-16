from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from backend.app.algorithms.wca.broker_reconciliation import reconcile_wca_broker
from backend.app.algorithms.wca.contracts import ProposedOrder, WcaOrderStatus, WcaSide
from backend.app.execution import BrokerFillUpdate
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState


NOW = datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
SESSION_DATE = date(2026, 7, 15)


def test_wca_broker_reconciliation_detects_broker_discrepancies_without_netting_attribution() -> None:
    first = intent("intent-one", "decision-one", 10, "client-one")
    second = intent("intent-two", "decision-two", 5, "client-two")
    repository = MemoryWcaReconciliationRepository((first, second))
    broker = FakeWcaBroker(
        orders=[
            broker_order(first, quantity=8, filled=0, submitted_at=NOW - timedelta(minutes=10)),
        ],
        positions=[
            broker_position(first, quantity=8),
            broker_position(second, quantity=5),
            broker_position(intent("unknown-intent", "unknown-decision", 3, "unknown-client"), quantity=3),
            unattributed_wca_position(quantity=2),
        ],
        updates={
            "client-one": BrokerFillUpdate(clientOrderId="client-one", filledQuantity=4, averageFillPrice=100.1, status="PARTIALLY_FILLED", updatedAt=NOW),
            "client-two": BrokerFillUpdate(clientOrderId="client-two", filledQuantity=0, status="REJECTED", updatedAt=NOW),
        },
    )

    result = reconcile_wca_broker(repository=repository, broker=broker, account_id="paper-account", evaluated_at=NOW, stale_after_seconds=300)

    discrepancy_types = {row.discrepancy_type for row in result.discrepancies}
    assert "missing_backend_fill" in discrepancy_types
    assert "rejected_order" in discrepancy_types
    assert "stale_open_order" in discrepancy_types
    assert "mismatched_quantity" in discrepancy_types
    assert "orphan_position" in discrepancy_types
    assert "attribution_missing" in discrepancy_types
    assert result.hard_operational_warning is True
    assert repository.reconciliations[-1] == result

    first_mismatches = [row for row in result.discrepancies if row.discrepancy_type == "mismatched_quantity" and row.order_intent_id == first.order_intent_id]
    assert first_mismatches
    assert all(row.attribution["orderIntentId"] == first.order_intent_id for row in first_mismatches)

    missing_for_second = [row for row in result.discrepancies if row.discrepancy_type == "missing_broker_order" and row.order_intent_id == second.order_intent_id]
    assert not missing_for_second
    unattributed = [row for row in result.discrepancies if row.discrepancy_type == "attribution_missing"]
    assert unattributed
    assert unattributed[0].preserves_wca_attribution is False


def test_wca_reconciliation_reports_missing_broker_order_per_intent_not_by_symbol_netting() -> None:
    first = intent("intent-one", "decision-one", 10, "client-one")
    second = intent("intent-two", "decision-two", 5, "client-two")
    repository = MemoryWcaReconciliationRepository((first, second))
    broker = FakeWcaBroker(positions=[broker_position(second, quantity=5)])

    result = reconcile_wca_broker(repository=repository, broker=broker, account_id="paper-account", evaluated_at=NOW)

    missing = [row for row in result.discrepancies if row.discrepancy_type == "missing_broker_order"]
    assert len(missing) == 1
    assert missing[0].order_intent_id == first.order_intent_id
    assert missing[0].attribution["decisionId"] == first.decision_id


class MemoryWcaReconciliationRepository:
    def __init__(self, intents: tuple[ProposedOrder, ...], filled: set[str] | None = None) -> None:
        self.intents = intents
        self.filled = filled or set()
        self.reconciliations = []

    def list_order_intents(self, *, account_id: str | None = None) -> tuple[ProposedOrder, ...]:
        if account_id is None:
            return self.intents
        return tuple(intent for intent in self.intents if intent.account_id == account_id)

    def has_order_fill(self, order_intent_id: str) -> bool:
        return order_intent_id in self.filled

    def write_broker_reconciliation(self, result) -> None:
        self.reconciliations.append(result)


class FakeWcaBroker:
    def __init__(
        self,
        *,
        orders: list[BrokerOrderState] | None = None,
        positions: list[BrokerPositionState] | None = None,
        updates: dict[str, BrokerFillUpdate] | None = None,
    ) -> None:
        self.orders = orders or []
        self.positions = positions or []
        self.updates = updates or {}

    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        return BrokerAccountSnapshot(
            accountId="paper-account",
            equity=100_000,
            buyingPower=100_000,
            realizedPnlToday=0,
            positions=self.positions,
            pendingOrders=self.orders,
            partiallyFilledOrders=[],
            observedAt=NOW,
            sessionDate=SESSION_DATE,
            sourceAuthority="broker",
        )

    def refresh_order(self, client_order_id: str) -> BrokerFillUpdate | None:
        return self.updates.get(client_order_id)


def intent(order_intent_id: str, decision_id: str, quantity: int, client_id: str) -> ProposedOrder:
    return ProposedOrder(
        decision_id=decision_id,
        order_intent_id=order_intent_id,
        idempotency_key=client_id,
        account_id="paper-account",
        symbol="SPY",
        side=WcaSide.BUY,
        quantity=quantity,
        trigger_price=100,
        limit_price=100,
        stop_price=99,
        target_price=102,
        status=WcaOrderStatus.ACCEPTED_FOR_PAPER,
    )


def broker_order(source: ProposedOrder, *, quantity: int, filled: int, submitted_at: datetime) -> BrokerOrderState:
    return BrokerOrderState(
        algorithmId="wca",
        capitalPartitionId="wca.paper.default",
        decisionId=source.decision_id,
        orderIntentId=source.order_intent_id,
        positionOwner="wca",
        symbol=source.symbol,
        side=source.side,
        clientOrderId=source.idempotency_key,
        orderType="LIMIT",
        status="ACCEPTED",
        quantity=quantity,
        filledQuantity=filled,
        entryPrice=source.limit_price or source.trigger_price or 100,
        stopPrice=source.stop_price,
        submittedAt=submitted_at,
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
        averageEntryPrice=source.limit_price or source.trigger_price or 100,
        markPrice=101,
        stopPrice=source.stop_price,
        openedAt=NOW,
    )


def unattributed_wca_position(*, quantity: int) -> BrokerPositionState:
    return BrokerPositionState(
        algorithmId="wca",
        capitalPartitionId="wca.paper.default",
        symbol="SPY",
        side="BUY",
        quantity=quantity,
        averageEntryPrice=100,
        markPrice=101,
        stopPrice=99,
        openedAt=NOW,
    )
