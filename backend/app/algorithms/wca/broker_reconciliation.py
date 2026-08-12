"""Periodic WCA paper broker reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from backend.app.algorithms.wca.local_paper_account import WCA_LOCAL_PAPER_SOURCE_AUTHORITY
from backend.app.algorithms.wca.contracts import (
    WCA_ALGORITHM_ID,
    WCA_BROKER_RECONCILIATION_SCHEMA_VERSION,
    ProposedOrder,
    WcaBrokerReconciliationDiscrepancy,
    WcaBrokerReconciliationResult,
    WcaOrderStatus,
    WcaSide,
    coerce_wca_order_status,
)
from backend.app.execution import BrokerFillUpdate
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState


WCA_BROKER_RECONCILIATION_VERSION = WCA_BROKER_RECONCILIATION_SCHEMA_VERSION


class WcaPaperBrokerReconciliationClient(Protocol):
    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        ...

    def refresh_order(self, client_order_id: str) -> object | None:
        ...

    def read_fills_and_activities(self, *, after: datetime | None = None) -> tuple[object, ...]:
        ...


class WcaBrokerReconciliationRepository(Protocol):
    def list_order_intents(self, *, account_id: str | None = None) -> tuple[ProposedOrder, ...]:
        ...

    def has_order_fill(self, order_intent_id: str) -> bool:
        ...

    def list_execution_outbox_records(self, *, account_id: str | None = None) -> tuple[object, ...]:
        ...

    def write_broker_reconciliation(self, result: WcaBrokerReconciliationResult) -> None:
        ...

    def open_wca_position_quantity(self, *, account_id: str, symbol: str) -> int:
        ...


def reconcile_wca_broker(
    *,
    repository: WcaBrokerReconciliationRepository,
    broker: WcaPaperBrokerReconciliationClient,
    account_id: str | None = None,
    evaluated_at: datetime | None = None,
    stale_after_seconds: int = 300,
    shared_global_attribution_ledger: dict[str, dict[str, int]] | None = None,
) -> WcaBrokerReconciliationResult:
    evaluated = (evaluated_at or datetime.now(UTC)).astimezone(UTC)
    snapshot = broker.refresh_account_snapshot()
    account = account_id or snapshot.accountId
    if account_id is not None and snapshot.accountId != account_id:
        discrepancies: list[WcaBrokerReconciliationDiscrepancy] = [
            _account_discrepancy(
                "broker_account_identity_mismatch",
                account_id,
                broker_status=snapshot.accountId,
                reason="wca.broker_reconciliation.account_identity_mismatch",
                explanation="WCA configured account id does not match the paper broker account id.",
            )
        ]
        result = _result(account_id, evaluated, snapshot, (), discrepancies)
        _persist_reconciliation(repository, result, snapshot)
        return result
    intents = repository.list_order_intents(account_id=account)
    outbox_rows = repository.list_execution_outbox_records(account_id=account) if hasattr(repository, "list_execution_outbox_records") else ()
    broker_orders = tuple(order for order in (*snapshot.pendingOrders, *snapshot.partiallyFilledOrders) if _is_wca(order))
    entry_broker_orders = tuple(order for order in broker_orders if not _is_protective_order(order))
    broker_positions = tuple(position for position in snapshot.positions if _is_wca(position))
    non_wca_spy_positions = tuple(position for position in snapshot.positions if position.symbol.upper() == "SPY" and not _is_wca(position))
    order_by_intent = {order.orderIntentId: order for order in entry_broker_orders if order.orderIntentId}
    order_by_client = {order.clientOrderId: order for order in entry_broker_orders if order.clientOrderId}
    position_by_intent = {position.orderIntentId: position for position in broker_positions if position.orderIntentId}
    known_intents = {intent.order_intent_id for intent in intents}
    known_clients = {client for client in (_client_id_for_intent(intent) for intent in intents) if client}
    known_clients.update(str(getattr(row, "client_order_id", "") or "") for row in outbox_rows)
    discrepancies = []

    if snapshot.sourceAuthority not in {"broker", WCA_LOCAL_PAPER_SOURCE_AUTHORITY}:
        discrepancies.append(_account_discrepancy("paper_account_not_active", account, broker_status=snapshot.sourceAuthority, reason="wca.broker_reconciliation.account_source_not_authoritative", explanation="WCA paper account snapshot was not sourced from an authoritative WCA account source."))
    if snapshot.equity <= 0 or snapshot.buyingPower <= 0:
        discrepancies.append(_account_discrepancy("broker_account_not_active", account, broker_status="equity_or_buying_power_unavailable", reason="wca.broker_reconciliation.account_not_tradeable", explanation="Broker account equity or buying power is unavailable for WCA reconciliation."))

    for intent in intents:
        client_id = _client_id_for_intent(intent, outbox_rows)
        broker_order = order_by_intent.get(intent.order_intent_id) or order_by_client.get(client_id)
        broker_position = position_by_intent.get(intent.order_intent_id)
        update = _refresh_broker_order(broker, client_id) if client_id else None
        update_status = _update_status(update)
        update_filled = _update_filled_quantity(update)

        if update and update_status == "REJECTED":
            discrepancies.append(_discrepancy("rejected_order", account, intent, broker_status=update_status, severity="hard", reason="wca.broker_reconciliation.rejected_order"))

        if update and update_filled > 0 and not repository.has_order_fill(intent.order_intent_id):
            discrepancies.append(
                _discrepancy(
                    "missing_backend_fill",
                    account,
                    intent,
                    broker_status=update_status,
                    broker_quantity=update_filled,
                    backend_quantity=0,
                    broker_filled_quantity=update_filled,
                    severity="hard",
                    reason="wca.broker_reconciliation.missing_backend_fill",
                )
            )
            discrepancies.append(
                _discrepancy(
                    "partial_fill_not_processed" if broker_order is not None and broker_order.remaining_quantity > 0 else "filled_order_still_pending",
                    account,
                    intent,
                    broker_status=update_status,
                    broker_quantity=update_filled,
                    backend_quantity=0,
                    broker_filled_quantity=update_filled,
                    severity="hard",
                    reason="wca.broker_reconciliation.fill_not_processed_locally",
                )
            )

        if update and update_status == "CANCELLED" and _local_status_for_intent(intent, outbox_rows) not in {WcaOrderStatus.CANCELLED.value, WcaOrderStatus.RECONCILED.value}:
            discrepancies.append(_discrepancy("cancelled_order_still_open", account, intent, broker_status=update_status, severity="hard", reason="wca.broker_reconciliation.cancelled_order_still_open_locally"))

        if update and update_status == "REJECTED" and _local_status_for_intent(intent, outbox_rows) != WcaOrderStatus.REJECTED.value:
            discrepancies.append(_discrepancy("rejection_not_processed", account, intent, broker_status=update_status, severity="hard", reason="wca.broker_reconciliation.rejection_not_processed_locally"))

        if broker_order is None and broker_position is None and (update is None or update_filled <= 0):
            discrepancies.append(_discrepancy("missing_broker_order", account, intent, backend_quantity=intent.quantity, severity="warning", reason="wca.broker_reconciliation.missing_broker_order"))

        if broker_order is not None:
            age_seconds = max(0, int((evaluated - broker_order.submittedAt.astimezone(UTC)).total_seconds()))
            if broker_order.clientOrderId and str(broker_order.clientOrderId).startswith("wca-") and broker_order.clientOrderId not in known_clients:
                discrepancies.append(_broker_order_discrepancy("unknown_wca_prefixed_broker_order", account, broker_order, reason="wca.broker_reconciliation.unknown_wca_prefixed_broker_order"))
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
            if broker_order.filledQuantity > 0 and not repository.has_order_fill(intent.order_intent_id):
                discrepancies.append(
                    _discrepancy(
                        "partial_fill_not_processed",
                        account,
                        intent,
                        broker_status=broker_order.status,
                        broker_quantity=broker_order.quantity,
                        backend_quantity=0,
                        broker_filled_quantity=broker_order.filledQuantity,
                        severity="hard",
                        reason="wca.broker_reconciliation.partial_fill_not_processed_locally",
                    )
                )

        if broker_position is not None and broker_position.quantity != intent.quantity:
            discrepancies.append(_quantity_mismatch(account, intent, broker_position.quantity, "wca.broker_reconciliation.position_quantity_mismatch"))

    for row in outbox_rows:
        local_status = coerce_wca_order_status(getattr(row, "status", ""))
        if local_status in {WcaOrderStatus.RESERVED.value, WcaOrderStatus.SUBMITTING.value, WcaOrderStatus.SUBMITTED.value, WcaOrderStatus.ACKNOWLEDGED.value, WcaOrderStatus.PARTIALLY_FILLED.value, WcaOrderStatus.UNKNOWN.value, WcaOrderStatus.RECONCILING.value}:
            client_id = str(getattr(row, "client_order_id", "") or "")
            broker_order = order_by_client.get(client_id)
            update = _refresh_broker_order(broker, client_id) if client_id else None
            if broker_order is None and update is None:
                discrepancies.append(_outbox_discrepancy("local_outbox_missing_broker_order", account, row, reason="wca.broker_reconciliation.local_outbox_missing_broker_order"))

    fills = broker.read_fills_and_activities(after=evaluated - timedelta(days=1)) if hasattr(broker, "read_fills_and_activities") else ()
    for fill in fills:
        client_id = _fill_client_order_id(fill)
        if client_id.startswith("wca-") and client_id not in known_clients:
            discrepancies.append(_fill_discrepancy("broker_order_missing_locally", account, fill, reason="wca.broker_reconciliation.broker_fill_missing_locally"))

    for order in broker_orders:
        if not _is_protective_order(order) and order.clientOrderId and str(order.clientOrderId).startswith("wca-") and order.clientOrderId not in known_clients:
            discrepancies.append(_broker_order_discrepancy("broker_order_missing_locally", account, order, reason="wca.broker_reconciliation.broker_order_missing_locally"))
        if _is_protective_order(order) and _parent_order_intent_id(order.orderIntentId) not in known_intents:
            discrepancies.append(_broker_order_discrepancy("orphan_protective_order", account, order, reason="wca.broker_reconciliation.orphan_protective_order"))

    for position in broker_positions:
        if position.orderIntentId not in known_intents:
            discrepancies.append(_orphan_position(account, position, "wca.broker_reconciliation.orphan_position"))
        if not position.orderIntentId or not position.decisionId:
            discrepancies.append(_orphan_position(account, position, "wca.broker_reconciliation.attribution_missing", discrepancy_type="attribution_missing"))
        if position.symbol.upper() == "SPY" and position.quantity > 0 and position.stopPrice is None and not any(_is_protective_order(order) for order in broker_orders):
            discrepancies.append(_position_discrepancy("position_without_protection", account, position, reason="wca.broker_reconciliation.position_without_protection"))

    for position in non_wca_spy_positions:
        discrepancies.append(_position_discrepancy("unexpected_account_spy_position", account, position, reason="wca.broker_reconciliation.unexpected_account_spy_position"))

    for symbol in sorted({position.symbol for position in broker_positions}):
        broker_wca_quantity = sum(_signed_quantity(position.side, position.quantity) for position in broker_positions if position.symbol == symbol)
        backend_wca_quantity = repository.open_wca_position_quantity(account_id=account, symbol=symbol) if hasattr(repository, "open_wca_position_quantity") else broker_wca_quantity
        if broker_wca_quantity != backend_wca_quantity:
            discrepancies.append(
                _inventory_mismatch(
                    account,
                    symbol,
                    broker_quantity=broker_wca_quantity,
                    backend_quantity=backend_wca_quantity,
                    reason="wca.broker_reconciliation.wca_inventory_broker_mismatch",
                )
            )

    if shared_global_attribution_ledger is not None:
        for symbol in sorted({position.symbol for position in snapshot.positions} | set(shared_global_attribution_ledger)):
            broker_net_quantity = sum(_signed_quantity(position.side, position.quantity) for position in snapshot.positions if position.symbol == symbol)
            ledger_net_quantity = sum(int(quantity) for quantity in shared_global_attribution_ledger.get(symbol, {}).values())
            if broker_net_quantity != ledger_net_quantity:
                discrepancies.append(
                    _net_attribution_mismatch(
                        account,
                        symbol,
                        broker_quantity=broker_net_quantity,
                        ledger_quantity=ledger_net_quantity,
                        ledger=shared_global_attribution_ledger.get(symbol, {}),
                    )
                )

    result = _result(account, evaluated, snapshot, intents, discrepancies)
    _persist_reconciliation(repository, result, snapshot)
    return result


def _result(account: str, evaluated: datetime, snapshot: BrokerAccountSnapshot, intents: tuple[ProposedOrder, ...], discrepancies: list[WcaBrokerReconciliationDiscrepancy]) -> WcaBrokerReconciliationResult:
    broker_orders = tuple(order for order in (*snapshot.pendingOrders, *snapshot.partiallyFilledOrders) if _is_wca(order))
    broker_positions = tuple(position for position in snapshot.positions if _is_wca(position))
    reason_codes = ("wca.broker_reconciliation.clean",) if not discrepancies else tuple(sorted({code for row in discrepancies for code in row.reason_codes}))
    return WcaBrokerReconciliationResult(
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
        explanation="WCA local paper account, orders, fills, positions, inventory, protection, and local state were reconciled without assigning sibling algorithm inventory to WCA.",
    )


def _persist_reconciliation(repository: WcaBrokerReconciliationRepository, result: WcaBrokerReconciliationResult, snapshot: BrokerAccountSnapshot) -> None:
    if hasattr(repository, "write_broker_account_snapshot"):
        repository.write_broker_account_snapshot(snapshot, symbol="SPY", configuration_version=result.reconciliation_version, decision_id=result.reconciliation_id, run_id=result.reconciliation_id)
    repository.write_broker_reconciliation(result)


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


def _account_discrepancy(discrepancy_type: str, account_id: str, *, broker_status: str, reason: str, explanation: str) -> WcaBrokerReconciliationDiscrepancy:
    return WcaBrokerReconciliationDiscrepancy(
        discrepancy_type=discrepancy_type,
        severity="hard",
        account_id=account_id,
        symbol="SPY",
        side=WcaSide.HOLD,
        broker_status=broker_status,
        preserves_wca_attribution=True,
        attribution={"algorithmId": WCA_ALGORITHM_ID, "accountId": account_id},
        reason_codes=(reason,),
        explanation=explanation,
    )


def _broker_order_discrepancy(discrepancy_type: str, account_id: str, order: BrokerOrderState, *, reason: str) -> WcaBrokerReconciliationDiscrepancy:
    return WcaBrokerReconciliationDiscrepancy(
        discrepancy_type=discrepancy_type,
        severity="hard",
        account_id=account_id,
        symbol=order.symbol,
        side=order.side,
        order_intent_id=order.orderIntentId,
        decision_id=order.decisionId,
        idempotency_key=order.clientOrderId,
        broker_status=order.status,
        broker_quantity=order.quantity,
        broker_filled_quantity=order.filledQuantity,
        preserves_wca_attribution=bool(order.orderIntentId and order.decisionId),
        attribution={
            "algorithmId": order.algorithmId,
            "clientOrderId": order.clientOrderId,
            "orderIntentId": order.orderIntentId,
            "decisionId": order.decisionId,
            "exitOwner": order.exitOwner,
        },
        reason_codes=(reason,),
        explanation=f"{discrepancy_type} detected for WCA broker order {order.clientOrderId}.",
    )


def _outbox_discrepancy(discrepancy_type: str, account_id: str, row: object, *, reason: str) -> WcaBrokerReconciliationDiscrepancy:
    proposed = getattr(row, "proposed_order")
    return WcaBrokerReconciliationDiscrepancy(
        discrepancy_type=discrepancy_type,
        severity="hard",
        account_id=account_id,
        symbol=getattr(row, "symbol", proposed.symbol),
        side=proposed.side,
        order_intent_id=getattr(row, "order_intent_id", proposed.order_intent_id),
        decision_id=getattr(row, "decision_id", proposed.decision_id),
        idempotency_key=getattr(row, "client_order_id", proposed.idempotency_key),
        backend_quantity=proposed.quantity,
        broker_quantity=0,
        preserves_wca_attribution=True,
        attribution={
            "algorithmId": WCA_ALGORITHM_ID,
            "outboxId": getattr(row, "outbox_id", None),
            "localStatus": getattr(row, "status", None),
            "clientOrderId": getattr(row, "client_order_id", None),
        },
        reason_codes=(reason,),
        explanation=f"{discrepancy_type} detected for local WCA outbox row.",
    )


def _fill_discrepancy(discrepancy_type: str, account_id: str, fill: object, *, reason: str) -> WcaBrokerReconciliationDiscrepancy:
    return WcaBrokerReconciliationDiscrepancy(
        discrepancy_type=discrepancy_type,
        severity="hard",
        account_id=account_id,
        symbol="SPY",
        side=WcaSide.HOLD,
        idempotency_key=_fill_client_order_id(fill),
        broker_quantity=_update_filled_quantity(fill),
        broker_filled_quantity=_update_filled_quantity(fill),
        preserves_wca_attribution=False,
        attribution={"algorithmId": WCA_ALGORITHM_ID, "clientOrderId": _fill_client_order_id(fill), "fillId": str(getattr(fill, "fill_id", "") or getattr(fill, "fillId", ""))},
        reason_codes=(reason,),
        explanation=f"{discrepancy_type} detected for broker fill/activity.",
    )


def _position_discrepancy(discrepancy_type: str, account_id: str, position: BrokerPositionState, *, reason: str) -> WcaBrokerReconciliationDiscrepancy:
    return WcaBrokerReconciliationDiscrepancy(
        discrepancy_type=discrepancy_type,
        severity="hard",
        account_id=account_id,
        symbol=position.symbol,
        side=position.side,
        order_intent_id=position.orderIntentId,
        decision_id=position.decisionId,
        broker_quantity=position.quantity,
        backend_quantity=0,
        preserves_wca_attribution=_is_wca(position),
        attribution={
            "algorithmId": position.algorithmId,
            "positionOwner": position.positionOwner,
            "orderIntentId": position.orderIntentId,
            "decisionId": position.decisionId,
            "stopPrice": str(position.stopPrice) if position.stopPrice is not None else None,
        },
        reason_codes=(reason,),
        explanation=f"{discrepancy_type} detected for broker position.",
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


def _inventory_mismatch(account_id: str, symbol: str, *, broker_quantity: int, backend_quantity: int, reason: str) -> WcaBrokerReconciliationDiscrepancy:
    side = WcaSide.BUY if broker_quantity >= 0 else WcaSide.SELL
    return WcaBrokerReconciliationDiscrepancy(
        discrepancy_type="wca_inventory_broker_mismatch",
        severity="hard",
        account_id=account_id,
        symbol=symbol,
        side=side,
        broker_quantity=abs(broker_quantity),
        backend_quantity=abs(backend_quantity),
        attribution={
            "algorithmId": WCA_ALGORITHM_ID,
            "brokerSignedQuantity": str(broker_quantity),
            "backendSignedQuantity": str(backend_quantity),
        },
        reason_codes=(reason,),
        explanation="WCA-owned inventory does not match WCA-attributed broker position; new WCA entries must remain blocked.",
    )


def _net_attribution_mismatch(account_id: str, symbol: str, *, broker_quantity: int, ledger_quantity: int, ledger: dict[str, int]) -> WcaBrokerReconciliationDiscrepancy:
    side = WcaSide.BUY if broker_quantity >= 0 else WcaSide.SELL
    return WcaBrokerReconciliationDiscrepancy(
        discrepancy_type="broker_net_attribution_mismatch",
        severity="hard",
        account_id=account_id,
        symbol=symbol,
        side=side,
        broker_quantity=abs(broker_quantity),
        backend_quantity=abs(ledger_quantity),
        preserves_wca_attribution=True,
        attribution={
            "brokerSignedQuantity": str(broker_quantity),
            "ledgerSignedQuantity": str(ledger_quantity),
            **{f"ledger.{algorithm_id}": str(quantity) for algorithm_id, quantity in ledger.items()},
        },
        reason_codes=("wca.broker_reconciliation.broker_net_attribution_mismatch",),
        explanation="Broker net position differs from the sum of attributed algorithm inventories; WCA will not absorb another algorithm's quantity.",
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


def _is_protective_order(order: BrokerOrderState) -> bool:
    client_order_id = str(getattr(order, "clientOrderId", "") or "").lower()
    return (
        order.exitOwner == WCA_ALGORITHM_ID
        or str(order.orderType).upper() in {"STOP", "STOP_LIMIT", "TRAILING_STOP"}
        or "-protection-" in client_order_id
        or client_order_id.startswith("wca-protection-")
    )


def _parent_order_intent_id(order_intent_id: str | None) -> str | None:
    if not order_intent_id:
        return order_intent_id
    return str(order_intent_id).split(":protection:", 1)[0]

def _refresh_broker_order(broker: WcaPaperBrokerReconciliationClient, client_id: str) -> object | None:
    if hasattr(broker, "poll_order_updates"):
        polled = broker.poll_order_updates(client_id)
        if polled is not None:
            return polled
    return broker.refresh_order(client_id)


def _client_id_for_intent(intent: ProposedOrder, outbox_rows: tuple[object, ...] = ()) -> str:
    for row in outbox_rows:
        if getattr(row, "order_intent_id", None) == intent.order_intent_id and getattr(row, "client_order_id", None):
            return str(getattr(row, "client_order_id"))
    return str(intent.idempotency_key or intent.order_intent_id)


def _local_status_for_intent(intent: ProposedOrder, outbox_rows: tuple[object, ...]) -> str:
    for row in outbox_rows:
        if getattr(row, "order_intent_id", None) == intent.order_intent_id:
            return coerce_wca_order_status(getattr(row, "status", ""))
    return coerce_wca_order_status(intent.status)


def _update_status(update: object | None) -> str:
    if update is None:
        return ""
    status = _field(update, "status") or _field(update, "order_status")
    if not status and _update_filled_quantity(update) > 0:
        return "FILLED"
    normalized = str(status or "").upper()
    if normalized == "CANCELED":
        return "CANCELLED"
    return normalized


def _update_filled_quantity(update: object | None) -> int:
    if update is None:
        return 0
    for field in ("filledQuantity", "filled_quantity"):
        value = _field(update, field)
        if value is not None:
            return int(float(value))
    if isinstance(update, dict):
        value = update.get("filled_qty") or update.get("qty")
        if value is not None:
            return int(float(value))
    return 0


def _fill_client_order_id(fill: object) -> str:
    return str(_field(fill, "client_order_id") or _field(fill, "clientOrderId") or "")


def _field(value: object, key: str) -> object | None:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _signed_quantity(side: WcaSide | str, quantity: int) -> int:
    return int(quantity) if _side_value(side) == WcaSide.BUY.value else -int(quantity)


def _side_value(side: WcaSide | str) -> str:
    return side.value if isinstance(side, WcaSide) else str(side)


__all__ = [
    "WCA_BROKER_RECONCILIATION_VERSION",
    "WcaBrokerReconciliationRepository",
    "WcaPaperBrokerReconciliationClient",
    "reconcile_wca_broker",
]
