"""Regime-owned broker reconciliation and restart recovery."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from backend.app.algorithms.regime.persistence import RegimeSqliteRepository
from backend.app.algorithms.regime.position_manager import RegimePositionManager


REGIME_BROKER_RECONCILIATION_VERSION = "regime_broker_reconciliation_v1"
RECOVERABLE_OUTBOX_STATES = {
    "created",
    "risk_approved",
    "queued",
    "retry_scheduled",
    "submitting",
    "acknowledged",
    "partially_filled",
    "reconciliation_required",
    "pending",
    "risk_reserved",
    "submitted",
}
AMBIGUOUS_SUBMISSION_STATES = {"submitting", "submitted", "reconciliation_required"}
BROKER_OPEN_ORDER_STATES = {"new", "accepted", "acknowledged", "open", "partially_filled", "pending", "submitted"}
BROKER_TERMINAL_ORDER_STATES = {"filled", "cancelled", "canceled", "rejected", "expired"}


def run_regime_broker_reconciliation(
    *,
    repository: RegimeSqliteRepository,
    identity: dict[str, Any],
    broker: Any | None = None,
    evaluated_at: datetime | None = None,
    trigger: str = "periodic",
    broker_positions: list[dict[str, Any]] | None = None,
    broker_open_orders: list[dict[str, Any]] | None = None,
    broker_fills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare Regime-owned execution state with broker observations.

    The recovery path is intentionally narrow: a broker object can only mutate
    Regime inventory when it carries attribution that matches an already-owned
    Regime intent, client order, broker order, position, or trade.
    """

    evaluated_at = _as_utc(evaluated_at or datetime.now(UTC))
    observed_at = _iso(evaluated_at)
    context = _reconciliation_context(repository, identity)
    broker_orders_available = broker_open_orders is not None or _broker_observation_available(broker, ("refresh_open_orders", "list_open_orders", "open_orders"))
    broker_fills_available = broker_fills is not None or _broker_observation_available(broker, ("refresh_fills", "list_fills", "fills"))
    broker_positions = broker_positions if broker_positions is not None else _optional_broker_call(broker, ("refresh_positions", "list_positions"))
    broker_open_orders = broker_open_orders if broker_open_orders is not None else _optional_broker_call(broker, ("refresh_open_orders", "list_open_orders", "open_orders"))
    broker_fills = broker_fills if broker_fills is not None else _optional_broker_call(broker, ("refresh_fills", "list_fills", "fills"))

    discrepancies: list[str] = []
    reason_codes: list[str] = [f"regime.reconciliation.trigger.{trigger}"]
    deterministic_recoveries: list[dict[str, Any]] = []
    manual_review_required = False

    for status, order_intent_id in _restart_state_matrix(context["latestOutbox"]):
        if status in AMBIGUOUS_SUBMISSION_STATES:
            reason_codes.append("regime.reconciliation.ambiguous_submission_state_detected")
        if status in RECOVERABLE_OUTBOX_STATES:
            deterministic_recoveries.append({"orderIntentId": order_intent_id, "fromStatus": status, "recoveryAction": "state_loaded_for_restart"})

    for order in broker_open_orders:
        normalized = _normalize_broker_order(order, observed_at)
        ownership = _prove_order_ownership(normalized, context)
        if not ownership["proven"]:
            discrepancies.append(f"regime.reconciliation.unattributed_broker_order:{_observation_id(normalized)}")
            manual_review_required = True
            continue
        order_intent_id = ownership["orderIntentId"]
        outbox = context["latestOutbox"].get(order_intent_id, {})
        status = str(normalized.get("status") or "").lower()
        if str(outbox.get("processingStatus") or "") in AMBIGUOUS_SUBMISSION_STATES | {"queued", "risk_approved"}:
            repository.update_execution_outbox_status(
                identity,
                order_intent_id,
                status="acknowledged" if status in BROKER_OPEN_ORDER_STATES else status or "reconciliation_required",
                payload={
                    **outbox,
                    "brokerReconciliation": normalized,
                    "reconciliationVersion": REGIME_BROKER_RECONCILIATION_VERSION,
                    "reasonCodes": ["regime.reconciliation.broker_order_recovered"],
                    "allowDuplicateStatusUpdate": True,
                },
            )
            deterministic_recoveries.append({"orderIntentId": order_intent_id, "recoveryAction": "broker_order_status_recovered", "brokerStatus": status})
        repository.copy_broker_observation(
            {
                **identity,
                **normalized,
                "algorithmId": "regime",
                "type": "order",
                "orderIntentId": order_intent_id,
                "processingStatus": "acknowledged" if status in BROKER_OPEN_ORDER_STATES else status or "observed",
                "timestamp": observed_at,
            }
        )

    for fill in broker_fills:
        normalized = _normalize_broker_fill(fill, observed_at)
        ownership = _prove_order_ownership(normalized, context)
        if not ownership["proven"]:
            discrepancies.append(f"regime.reconciliation.unattributed_broker_fill:{_observation_id(normalized)}")
            manual_review_required = True
            continue
        order_intent_id = ownership["orderIntentId"]
        outbox = context["latestOutbox"].get(order_intent_id, {})
        fill_payload = _fill_payload(identity, normalized, outbox, order_intent_id, observed_at)
        copied = repository.copy_broker_observation(fill_payload)
        update: dict[str, Any] = {}
        if bool(copied.get("copied")) or not _fill_already_in_inventory(context, fill_payload):
            try:
                update = RegimePositionManager(repository).apply_fill_observation(identity, fill_payload, settings_snapshot=dict(_order_intent_from_outbox(outbox).get("settingsSnapshot") or {}))
                deterministic_recoveries.append({"orderIntentId": order_intent_id, "recoveryAction": "broker_fill_applied", "updated": bool(update.get("updated"))})
            except ValueError as exc:
                discrepancies.append(f"regime.reconciliation.fill_recovery_rejected:{order_intent_id}:{exc}")
                manual_review_required = True
        broker_fill_status = str(fill_payload.get("status") or "").lower()
        cumulative_filled_quantity = _cumulative_filled_quantity_after_recovery(update, fill_payload)
        requested_quantity = int(_order_intent_from_outbox(outbox).get("quantity") or fill_payload.get("submittedQuantity") or fill_payload.get("filledQuantity") or 0)
        terminal_status = "filled" if broker_fill_status == "filled" or cumulative_filled_quantity >= requested_quantity else "partially_filled"
        repository.update_execution_outbox_status(
            identity,
            order_intent_id,
            status=terminal_status,
            payload={
                **outbox,
                "brokerReconciliation": normalized,
                "reconciliationVersion": REGIME_BROKER_RECONCILIATION_VERSION,
                "reasonCodes": ["regime.reconciliation.broker_fill_recovered"],
                "allowDuplicateStatusUpdate": True,
            },
        )

    context = _reconciliation_context(repository, identity)
    position_discrepancies = _position_discrepancies(context, broker_positions)
    discrepancies.extend(position_discrepancies)
    if position_discrepancies:
        manual_review_required = manual_review_required or any("unattributed" in item or "ownership_unproven" in item for item in position_discrepancies)

    order_gap_discrepancies = _order_gap_discrepancies(context, broker_open_orders, broker_fills, broker_orders_available=broker_orders_available, broker_fills_available=broker_fills_available)
    discrepancies.extend(order_gap_discrepancies)

    current_inventory = repository.current_inventory_snapshot(identity)
    unresolved = bool(discrepancies)
    block_new_entries = unresolved or manual_review_required
    result = {
        **identity,
        "algorithmId": "regime",
        "reconciliationVersion": REGIME_BROKER_RECONCILIATION_VERSION,
        "trigger": trigger,
        "timestamp": observed_at,
        "reconciled": not unresolved,
        "reconciliationRequired": unresolved,
        "blockNewEntries": block_new_entries,
        "newEntriesPaused": block_new_entries,
        "riskReducingExitsAllowed": True,
        "manualReviewRequired": manual_review_required,
        "deterministicRecoveries": deterministic_recoveries,
        "discrepancies": tuple(dict.fromkeys(discrepancies)),
        "counts": {
            "outbox": len(context["latestOutbox"]),
            "regimeOrders": len(context["regimeOrders"]),
            "regimeFills": len(context["regimeFills"]),
            "brokerOpenOrders": len(broker_open_orders),
            "brokerFills": len(broker_fills),
            "brokerPositions": len(broker_positions),
        },
        "inventorySnapshot": current_inventory,
        "reasonCodes": tuple(dict.fromkeys(reason_codes + (["regime.reconciliation.unresolved_discrepancy"] if unresolved else ["regime.reconciliation.completed"]))),
    }
    repository.record_reconciliation_run(result, status="unresolved_discrepancy" if unresolved else "reconciled")
    if unresolved:
        repository.record_runtime_alert(
            identity,
            {
                "alertType": "broker_reconciliation_mismatch",
                "trigger": trigger,
                "manualReviewRequired": manual_review_required,
                "discrepancies": result["discrepancies"],
                "newEntriesBlocked": True,
                "riskReducingExitsAllowed": True,
                "timestamp": observed_at,
                "reasonCodes": result["reasonCodes"],
            },
            status="active",
        )
    return result


def _reconciliation_context(repository: RegimeSqliteRepository, identity: dict[str, Any]) -> dict[str, Any]:
    latest_outbox = _latest_by_intent(repository.read_owned_records("regime_execution_outbox", identity))
    regime_orders = repository.read_owned_records("regime_orders", identity)
    regime_fills = repository.read_owned_records("regime_fills", identity)
    regime_positions = repository.latest_regime_positions(identity)
    inventory_events = repository.read_owned_records("regime_inventory_events", identity)
    known_intents = set(latest_outbox)
    known_client_ids = {str(item.get("brokerClientOrderId") or "") for item in latest_outbox.values()}
    known_client_ids.update(str(_record(item.get("gatewayResult")).get("clientOrderId") or "") for item in latest_outbox.values())
    known_client_ids.update(str(item.get("clientOrderId") or "") for item in regime_orders)
    known_broker_order_ids = {str(item.get("brokerOrderId") or item.get("broker_order_id") or "") for item in regime_orders}
    known_broker_order_ids.update(str(_record(item.get("brokerAck")).get("brokerOrderId") or "") for item in regime_orders)
    known_position_ids = {str(item.get("positionId") or item.get("position_id") or "") for item in regime_positions}
    known_trade_ids = {str(item.get("tradeId") or item.get("trade_id") or "") for item in regime_positions}
    return {
        "latestOutbox": latest_outbox,
        "regimeOrders": regime_orders,
        "regimeFills": regime_fills,
        "regimePositions": regime_positions,
        "inventoryEvents": inventory_events,
        "knownIntents": {item for item in known_intents if item},
        "knownClientIds": {item for item in known_client_ids if item},
        "knownBrokerOrderIds": {item for item in known_broker_order_ids if item},
        "knownPositionIds": {item for item in known_position_ids if item},
        "knownTradeIds": {item for item in known_trade_ids if item},
    }


def _latest_by_intent(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        order_intent_id = str(record.get("orderIntentId") or record.get("order_intent_id") or _record(record.get("orderIntent")).get("orderIntentId") or "")
        if order_intent_id:
            latest[order_intent_id] = record
    return latest


def _restart_state_matrix(latest_outbox: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    return [(str(record.get("processingStatus") or ""), order_intent_id) for order_intent_id, record in latest_outbox.items()]


def _optional_broker_call(broker: Any | None, names: tuple[str, ...]) -> list[dict[str, Any]]:
    if broker is None:
        return []
    for name in names:
        candidate = getattr(broker, name, None)
        if callable(candidate):
            observed = candidate()
            return [_record(item) for item in (observed or [])]
        if isinstance(candidate, list):
            return [_record(item) for item in candidate]
    return []


def _broker_observation_available(broker: Any | None, names: tuple[str, ...]) -> bool:
    if broker is None:
        return False
    return any(callable(getattr(broker, name, None)) or isinstance(getattr(broker, name, None), list) for name in names)


def _normalize_broker_order(order: dict[str, Any], observed_at: str) -> dict[str, Any]:
    return {
        **order,
        "clientOrderId": str(order.get("clientOrderId") or order.get("client_order_id") or order.get("id") or ""),
        "brokerOrderId": str(order.get("brokerOrderId") or order.get("broker_order_id") or order.get("id") or ""),
        "orderIntentId": str(order.get("orderIntentId") or order.get("order_intent_id") or ""),
        "positionId": str(order.get("positionId") or order.get("position_id") or ""),
        "tradeId": str(order.get("tradeId") or order.get("trade_id") or ""),
        "status": str(order.get("status") or order.get("orderStatus") or "open").lower(),
        "timestamp": str(order.get("timestamp") or order.get("updatedAt") or observed_at),
    }


def _normalize_broker_fill(fill: dict[str, Any], observed_at: str) -> dict[str, Any]:
    return {
        **fill,
        "clientOrderId": str(fill.get("clientOrderId") or fill.get("client_order_id") or ""),
        "brokerOrderId": str(fill.get("brokerOrderId") or fill.get("broker_order_id") or fill.get("orderId") or fill.get("order_id") or ""),
        "orderIntentId": str(fill.get("orderIntentId") or fill.get("order_intent_id") or ""),
        "positionId": str(fill.get("positionId") or fill.get("position_id") or ""),
        "tradeId": str(fill.get("tradeId") or fill.get("trade_id") or ""),
        "fillId": str(fill.get("fillId") or fill.get("fill_id") or fill.get("id") or ""),
        "filledQuantity": int(fill.get("filledQuantity") or fill.get("filled_quantity") or fill.get("quantity") or 0),
        "averageFillPrice": float(fill.get("averageFillPrice") or fill.get("average_fill_price") or fill.get("price") or 0.0),
        "side": _normal_side(fill.get("side") or fill.get("orderSide") or "Buy"),
        "filledAt": str(fill.get("filledAt") or fill.get("filled_at") or fill.get("timestamp") or observed_at),
        "status": str(fill.get("status") or "FILLED").upper(),
    }


def _prove_order_ownership(observation: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    algorithm_id = str(observation.get("algorithmId") or observation.get("algorithm_id") or "")
    order_intent_id = str(observation.get("orderIntentId") or "")
    client_order_id = str(observation.get("clientOrderId") or "")
    broker_order_id = str(observation.get("brokerOrderId") or "")
    position_id = str(observation.get("positionId") or "")
    trade_id = str(observation.get("tradeId") or "")
    if algorithm_id and algorithm_id != "regime":
        return {"proven": False, "orderIntentId": ""}
    if order_intent_id and order_intent_id in context["knownIntents"]:
        return {"proven": True, "orderIntentId": order_intent_id}
    if client_order_id and client_order_id in context["knownClientIds"]:
        return {"proven": True, "orderIntentId": _intent_for_client_id(client_order_id, context)}
    if broker_order_id and broker_order_id in context["knownBrokerOrderIds"]:
        return {"proven": True, "orderIntentId": _intent_for_broker_order_id(broker_order_id, context)}
    if position_id and position_id in context["knownPositionIds"]:
        return {"proven": True, "orderIntentId": _intent_for_position_id(position_id, context)}
    if trade_id and trade_id in context["knownTradeIds"]:
        return {"proven": True, "orderIntentId": _intent_for_trade_id(trade_id, context)}
    return {"proven": False, "orderIntentId": ""}


def _position_discrepancies(context: dict[str, Any], broker_positions: list[dict[str, Any]]) -> list[str]:
    discrepancies: list[str] = []
    own_open = {
        str(position.get("positionId") or ""): position
        for position in context["regimePositions"]
        if str(position.get("positionStatus") or "open").lower() not in {"closed", "flat", "cancelled", "canceled"}
        and int(position.get("filledQuantity") or position.get("quantity") or 0) != 0
    }
    matched_owned: set[str] = set()
    for broker_position in broker_positions:
        position = _record(broker_position)
        quantity = int(position.get("quantity") or position.get("filledQuantity") or 0)
        if quantity == 0:
            continue
        proof = _prove_order_ownership(position, context)
        if not proof["proven"]:
            discrepancies.append(f"regime.reconciliation.unattributed_broker_position:{_observation_id(position)}")
            continue
        position_id = str(position.get("positionId") or position.get("position_id") or "")
        if position_id:
            matched_owned.add(position_id)
        if position_id in own_open:
            owned_quantity = int(own_open[position_id].get("filledQuantity") or own_open[position_id].get("quantity") or 0)
            if owned_quantity != quantity:
                discrepancies.append(f"regime.reconciliation.position_quantity_mismatch:{position_id}")
    if broker_positions:
        for position_id in own_open:
            if position_id and position_id not in matched_owned:
                discrepancies.append(f"regime.reconciliation.broker_missing_regime_position:{position_id}")
    return discrepancies


def _order_gap_discrepancies(
    context: dict[str, Any],
    broker_open_orders: list[dict[str, Any]],
    broker_fills: list[dict[str, Any]],
    *,
    broker_orders_available: bool,
    broker_fills_available: bool,
) -> list[str]:
    if not broker_orders_available and not broker_fills_available:
        return []
    observed_intents = {
        proof["orderIntentId"]
        for item in [*broker_open_orders, *broker_fills]
        if (proof := _prove_order_ownership(_record(item), context)).get("proven")
    }
    discrepancies: list[str] = []
    for order_intent_id, outbox in context["latestOutbox"].items():
        status = str(outbox.get("processingStatus") or "")
        if status in {"acknowledged", "partially_filled", "submitting", "submitted", "reconciliation_required"} and order_intent_id not in observed_intents:
            discrepancies.append(f"regime.reconciliation.broker_order_update_gap:{order_intent_id}")
    return discrepancies


def _fill_payload(identity: dict[str, Any], normalized: dict[str, Any], outbox: dict[str, Any], order_intent_id: str, observed_at: str) -> dict[str, Any]:
    intent = _order_intent_from_outbox(outbox)
    return {
        **identity,
        "algorithmId": "regime",
        "type": "fill",
        "decisionId": str(intent.get("decisionId") or outbox.get("decisionId") or normalized.get("decisionId") or ""),
        "orderIntentId": order_intent_id,
        "brokerOrderId": normalized.get("brokerOrderId"),
        "clientOrderId": normalized.get("clientOrderId"),
        "positionId": normalized.get("positionId") or intent.get("positionId"),
        "tradeId": normalized.get("tradeId") or intent.get("tradeId"),
        "fillId": normalized.get("fillId") or f"{order_intent_id}:{normalized.get('filledAt')}",
        "symbol": str(normalized.get("symbol") or identity.get("symbol") or "SPY").upper(),
        "side": normalized.get("side"),
        "filledQuantity": normalized.get("filledQuantity"),
        "averageFillPrice": normalized.get("averageFillPrice"),
        "filledAt": normalized.get("filledAt"),
        "status": normalized.get("status") or "FILLED",
        "submittedQuantity": intent.get("quantity") or normalized.get("filledQuantity"),
        "stopPrice": intent.get("stopPrice"),
        "targetPrice": intent.get("targetPrice"),
        "positionEffect": intent.get("positionEffect") or intent.get("position_effect"),
        "settingsVersion": intent.get("settingsVersion") or _record(intent.get("settingsSnapshot")).get("settingsVersion"),
        "timestamp": observed_at,
        "processingStatus": str(normalized.get("status") or "filled").lower(),
    }


def _fill_already_in_inventory(context: dict[str, Any], fill_payload: dict[str, Any]) -> bool:
    fill_id = str(fill_payload.get("fillId") or "")
    if not fill_id:
        return False
    return any(fill_id in str(event.get("fillId") or event.get("inventoryEventId") or "") for event in context["inventoryEvents"])


def _cumulative_filled_quantity_after_recovery(update: dict[str, Any], fill_payload: dict[str, Any]) -> int:
    position = update.get("position") if isinstance(update.get("position"), dict) else {}
    for key in ("filledQuantity", "quantity"):
        try:
            quantity = int(position.get(key) or 0)
        except (TypeError, ValueError):
            quantity = 0
        if quantity > 0:
            return quantity
    try:
        return int(fill_payload.get("filledQuantity") or fill_payload.get("filled_quantity") or 0)
    except (TypeError, ValueError):
        return 0


def _order_intent_from_outbox(outbox: dict[str, Any]) -> dict[str, Any]:
    nested = outbox.get("orderIntent")
    return dict(nested) if isinstance(nested, dict) else dict(outbox)


def _intent_for_client_id(client_order_id: str, context: dict[str, Any]) -> str:
    for order_intent_id, outbox in context["latestOutbox"].items():
        if client_order_id in {str(outbox.get("brokerClientOrderId") or ""), str(_record(outbox.get("gatewayResult")).get("clientOrderId") or "")}:
            return order_intent_id
    for order in context["regimeOrders"]:
        if str(order.get("clientOrderId") or "") == client_order_id:
            return str(order.get("orderIntentId") or "")
    return ""


def _intent_for_broker_order_id(broker_order_id: str, context: dict[str, Any]) -> str:
    for order in context["regimeOrders"]:
        if broker_order_id in {str(order.get("brokerOrderId") or order.get("broker_order_id") or ""), str(_record(order.get("brokerAck")).get("brokerOrderId") or "")}:
            return str(order.get("orderIntentId") or "")
    return ""


def _intent_for_position_id(position_id: str, context: dict[str, Any]) -> str:
    for position in context["regimePositions"]:
        if str(position.get("positionId") or "") == position_id:
            return str(position.get("orderIntentId") or "")
    return ""


def _intent_for_trade_id(trade_id: str, context: dict[str, Any]) -> str:
    for position in context["regimePositions"]:
        if str(position.get("tradeId") or "") == trade_id:
            return str(position.get("orderIntentId") or "")
    return ""


def _observation_id(observation: dict[str, Any]) -> str:
    return str(observation.get("clientOrderId") or observation.get("brokerOrderId") or observation.get("positionId") or observation.get("tradeId") or observation.get("fillId") or "unknown")


def _record(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {}


def _normal_side(value: Any) -> str:
    text = str(getattr(value, "value", value)).upper()
    return "Sell" if text in {"SELL", "SHORT"} else "Buy"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


__all__ = [
    "REGIME_BROKER_RECONCILIATION_VERSION",
    "RECOVERABLE_OUTBOX_STATES",
    "run_regime_broker_reconciliation",
]
