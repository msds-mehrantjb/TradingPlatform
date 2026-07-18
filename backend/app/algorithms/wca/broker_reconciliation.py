"""Periodic WCA paper broker reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from backend.app.algorithms.wca.contracts import (
    WCA_ALGORITHM_ID,
    WCA_BROKER_RECONCILIATION_SCHEMA_VERSION,
    ProposedOrder,
    WcaBrokerReconciliationDiscrepancy,
    WcaBrokerReconciliationResult,
    WcaSide,
)
from backend.app.execution import BrokerFillUpdate
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState


WCA_BROKER_RECONCILIATION_VERSION = WCA_BROKER_RECONCILIATION_SCHEMA_VERSION


class WcaPaperBrokerReconciliationClient(Protocol):
    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        ...

    def refresh_order(self, client_order_id: str) -> BrokerFillUpdate | None:
        ...


class WcaBrokerReconciliationRepository(Protocol):
    def list_order_intents(self, *, account_id: str | None = None) -> tuple[ProposedOrder, ...]:
        ...

    def has_order_fill(self, order_intent_id: str) -> bool:
        ...

    def write_broker_reconciliation(self, result: WcaBrokerReconciliationResult) -> None:
        ...


def reconcile_wca_broker(
    *,
    repository: WcaBrokerReconciliationRepository,
    broker: WcaPaperBrokerReconciliationClient,
    account_id: str | None = None,
    evaluated_at: datetime | None = None,
    stale_after_seconds: int = 300,
) -> WcaBrokerReconciliationResult:
    evaluated = (evaluated_at or datetime.now(UTC)).astimezone(UTC)
    snapshot = broker.refresh_account_snapshot()
    account = account_id or snapshot.accountId
    intents = repository.list_order_intents(account_id=account)
    broker_orders = tuple(order for order in (*snapshot.pendingOrders, *snapshot.partiallyFilledOrders) if _is_wca(order))
    broker_positions = tuple(position for position in snapshot.positions if _is_wca(position))
    order_by_intent = {order.orderIntentId: order for order in broker_orders if order.orderIntentId}
    order_by_client = {order.clientOrderId: order for order in broker_orders if order.clientOrderId}
    position_by_intent = {position.orderIntentId: position for position in broker_positions if position.orderIntentId}
    known_intents = {intent.order_intent_id for intent in intents}
    discrepancies: list[WcaBrokerReconciliationDiscrepancy] = []

    for intent in intents:
        client_id = intent.idempotency_key or intent.order_intent_id
        broker_order = order_by_intent.get(intent.order_intent_id) or order_by_client.get(client_id)
        broker_position = position_by_intent.get(intent.order_intent_id)
        update = broker.refresh_order(client_id) if client_id else None

        if update and update.status == "REJECTED":
            discrepancies.append(_discrepancy("rejected_order", account, intent, broker_status=update.status, severity="hard", reason="wca.broker_reconciliation.rejected_order"))

        if update and update.filledQuantity > 0 and not repository.has_order_fill(intent.order_intent_id):
            discrepancies.append(
                _discrepancy(
                    "missing_backend_fill",
                    account,
                    intent,
                    broker_status=update.status,
                    broker_quantity=update.filledQuantity,
                    backend_quantity=0,
                    broker_filled_quantity=update.filledQuantity,
                    severity="hard",
                    reason="wca.broker_reconciliation.missing_backend_fill",
                )
            )

        if broker_order is None and broker_position is None and (update is None or update.filledQuantity <= 0):
            discrepancies.append(_discrepancy("missing_broker_order", account, intent, backend_quantity=intent.quantity, severity="warning", reason="wca.broker_reconciliation.missing_broker_order"))

        if broker_order is not None:
            age_seconds = max(0, int((evaluated - broker_order.submittedAt.astimezone(UTC)).total_seconds()))
            if broker_order.remaining_quantity > 0 and age_seconds > stale_after_seconds:
                discrepancies.append(
                    _discrepancy(
                        "stale_open_order",
                        account,
                        intent,
                        broker_status=broker_order.status,
                        broker_quantity=broker_order.remaining_quantity,
                        backend_quantity=intent.quantity,
                        broker_filled_quantity=broker_order.filledQuantity,
                        age_seconds=age_seconds,
                        severity="warning",
                        reason="wca.broker_reconciliation.stale_open_order",
                    )
                )
            if broker_order.quantity != intent.quantity:
                discrepancies.append(_quantity_mismatch(account, intent, broker_order.quantity, "wca.broker_reconciliation.order_quantity_mismatch"))

        if broker_position is not None and broker_position.quantity != intent.quantity:
            discrepancies.append(_quantity_mismatch(account, intent, broker_position.quantity, "wca.broker_reconciliation.position_quantity_mismatch"))

    for position in broker_positions:
        if position.orderIntentId not in known_intents:
            discrepancies.append(_orphan_position(account, position, "wca.broker_reconciliation.orphan_position"))
        if not position.orderIntentId or not position.decisionId:
            discrepancies.append(_orphan_position(account, position, "wca.broker_reconciliation.attribution_missing", discrepancy_type="attribution_missing"))

    reason_codes = ("wca.broker_reconciliation.clean",) if not discrepancies else tuple(sorted({code for row in discrepancies for code in row.reason_codes}))
    result = WcaBrokerReconciliationResult(
        reconciliation_id=f"wca-broker-reconciliation-{uuid4().hex}",
        reconciliation_version=WCA_BROKER_RECONCILIATION_VERSION,
        account_id=account,
        evaluated_at=evaluated,
        intents_checked=len(intents),
        broker_open_orders_checked=len(broker_orders),
        broker_positions_checked=len(broker_positions),
        discrepancies=tuple(discrepancies),
        hard_operational_warning=any(row.severity == "hard" for row in discrepancies),
        reason_codes=reason_codes,
        explanation="WCA paper intents were reconciled against broker paper orders and positions without netting away WCA attribution.",
    )
    repository.write_broker_reconciliation(result)
    return result


def _discrepancy(
    discrepancy_type: str,
    account_id: str,
    intent: ProposedOrder,
    *,
    broker_status: str | None = None,
    broker_quantity: int | None = None,
    backend_quantity: int | None = None,
    broker_filled_quantity: int | None = None,
    age_seconds: int | None = None,
    severity: str,
    reason: str,
) -> WcaBrokerReconciliationDiscrepancy:
    return WcaBrokerReconciliationDiscrepancy(
        discrepancy_type=discrepancy_type,
        severity=severity,
        account_id=account_id,
        symbol=intent.symbol,
        side=intent.side,
        order_intent_id=intent.order_intent_id,
        decision_id=intent.decision_id,
        idempotency_key=intent.idempotency_key,
        broker_status=broker_status,
        broker_quantity=broker_quantity,
        backend_quantity=backend_quantity,
        broker_filled_quantity=broker_filled_quantity,
        age_seconds=age_seconds,
        attribution=_attribution(intent),
        reason_codes=(reason,),
        explanation=f"{discrepancy_type} detected for WCA order intent {intent.order_intent_id}.",
    )


def _quantity_mismatch(account_id: str, intent: ProposedOrder, broker_quantity: int, reason: str) -> WcaBrokerReconciliationDiscrepancy:
    return _discrepancy(
        "mismatched_quantity",
        account_id,
        intent,
        broker_quantity=broker_quantity,
        backend_quantity=intent.quantity,
        severity="hard",
        reason=reason,
    )


def _orphan_position(
    account_id: str,
    position: BrokerPositionState,
    reason: str,
    *,
    discrepancy_type: str = "orphan_position",
) -> WcaBrokerReconciliationDiscrepancy:
    severity = "hard" if discrepancy_type == "orphan_position" else "warning"
    return WcaBrokerReconciliationDiscrepancy(
        discrepancy_type=discrepancy_type,
        severity=severity,
        account_id=account_id,
        symbol=position.symbol,
        side=position.side,
        order_intent_id=position.orderIntentId,
        decision_id=position.decisionId,
        broker_quantity=position.quantity,
        backend_quantity=0,
        preserves_wca_attribution=bool(position.orderIntentId and position.decisionId),
        attribution={
            "algorithmId": position.algorithmId,
            "capitalPartitionId": position.capitalPartitionId,
            "decisionId": position.decisionId,
            "orderIntentId": position.orderIntentId,
            "positionOwner": position.positionOwner,
            "parentOrderId": position.parentOrderId,
        },
        reason_codes=(reason,),
        explanation="Broker WCA position has no matching backend WCA intent or is missing attribution fields.",
    )


def _attribution(intent: ProposedOrder) -> dict[str, str | None]:
    return {
        "algorithmId": intent.algorithm_id,
        "accountId": intent.account_id,
        "decisionId": intent.decision_id,
        "orderIntentId": intent.order_intent_id,
        "idempotencyKey": intent.idempotency_key,
    }


def _is_wca(value: BrokerOrderState | BrokerPositionState) -> bool:
    return getattr(value, "algorithmId", None) == WCA_ALGORITHM_ID


__all__ = [
    "WCA_BROKER_RECONCILIATION_VERSION",
    "WcaBrokerReconciliationRepository",
    "WcaPaperBrokerReconciliationClient",
    "reconcile_wca_broker",
]
