"""Durable broker reconciliation for Weighted Voting inventory."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any, Literal

from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.inventory import (
    WeightedVotingInventoryEventType,
    WeightedVotingInventoryRepository,
    WeightedVotingInventorySnapshot,
)
from backend.app.algorithms.weighted_voting.persistence import WeightedVotingStateStore


WEIGHTED_VOTING_BROKER_RECONCILIATION_VERSION = "weighted_voting_broker_reconciliation_v1"
WEIGHTED_VOTING_BROKER_RECONCILIATION_NAMESPACE = "weighted_voting.broker_reconciliation"
CHECKPOINT_KEY = f"{WEIGHTED_VOTING_BROKER_RECONCILIATION_NAMESPACE}.checkpoint.latest"
DISCREPANCY_PREFIX = f"{WEIGHTED_VOTING_BROKER_RECONCILIATION_NAMESPACE}.discrepancies."


class WeightedVotingReconciliationDiscrepancySeverity(str, Enum):
    INFO = "info"
    PAUSE_ENTRIES = "pause_entries"
    CENTRAL_REVIEW = "central_review"


@dataclass(frozen=True)
class WeightedVotingBrokerOrderObservation:
    client_order_id: str
    algorithm_id: str | None
    symbol: str
    side: str
    status: str
    quantity: int
    filled_quantity: int
    average_fill_price: float | None
    observed_at: datetime
    broker_order_id: str | None = None
    replaced_by_client_order_id: str | None = None


@dataclass(frozen=True)
class WeightedVotingBrokerFillObservation:
    fill_id: str
    client_order_id: str
    algorithm_id: str | None
    symbol: str
    side: str
    quantity: int
    average_fill_price: float
    filled_at: datetime
    broker_order_id: str | None = None


@dataclass(frozen=True)
class WeightedVotingBrokerPositionObservation:
    client_order_id: str | None
    algorithm_id: str | None
    symbol: str
    quantity: int
    average_entry_price: float
    observed_at: datetime
    broker_position_id: str | None = None


@dataclass(frozen=True)
class WeightedVotingBrokerReconciliationDiscrepancy:
    discrepancy_id: str
    severity: WeightedVotingReconciliationDiscrepancySeverity | str
    reason_code: str
    client_order_id: str | None
    observed_at: datetime
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(
            {
                "reconciliationVersion": WEIGHTED_VOTING_BROKER_RECONCILIATION_VERSION,
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "discrepancyId": self.discrepancy_id,
                "severity": str(getattr(self.severity, "value", self.severity)),
                "reasonCode": self.reason_code,
                "clientOrderId": self.client_order_id,
                "observedAt": self.observed_at,
                "details": self.details,
            }
        )


@dataclass(frozen=True)
class WeightedVotingBrokerReconciliationResult:
    algorithm_id: Literal["weighted_voting"]
    reconciled_at: datetime
    inventory_reconciled: bool
    entries_paused: bool
    risk_reducing_exits_allowed: bool
    applied_fill_ids: tuple[str, ...]
    duplicate_fill_ids: tuple[str, ...]
    excluded_broker_position_ids: tuple[str, ...]
    discrepancies: tuple[WeightedVotingBrokerReconciliationDiscrepancy, ...]
    snapshot: WeightedVotingInventorySnapshot
    reason_codes: tuple[str, ...]
    reconciliation_version: str = WEIGHTED_VOTING_BROKER_RECONCILIATION_VERSION

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(
            {
                "reconciliationVersion": self.reconciliation_version,
                "algorithmId": self.algorithm_id,
                "reconciledAt": self.reconciled_at,
                "inventoryReconciled": self.inventory_reconciled,
                "entriesPaused": self.entries_paused,
                "riskReducingExitsAllowed": self.risk_reducing_exits_allowed,
                "appliedFillIds": self.applied_fill_ids,
                "duplicateFillIds": self.duplicate_fill_ids,
                "excludedBrokerPositionIds": self.excluded_broker_position_ids,
                "discrepancies": [item.as_dict() for item in self.discrepancies],
                "snapshotVersion": self.snapshot.snapshot_version,
                "reasonCodes": self.reason_codes,
            }
        )


def reconcile_weighted_voting_broker_observations(
    *,
    store: WeightedVotingStateStore,
    inventory_repository: WeightedVotingInventoryRepository,
    orders: tuple[WeightedVotingBrokerOrderObservation, ...] = (),
    fills: tuple[WeightedVotingBrokerFillObservation, ...] = (),
    positions: tuple[WeightedVotingBrokerPositionObservation, ...] = (),
    reconciled_at: datetime,
) -> WeightedVotingBrokerReconciliationResult:
    known_commands = _known_weighted_voting_client_orders(store)
    applied_fill_ids: list[str] = []
    duplicate_fill_ids: list[str] = []
    excluded_positions: list[str] = []
    discrepancies: list[WeightedVotingBrokerReconciliationDiscrepancy] = []

    for order in orders:
        if not _is_weighted_voting_attributed(order.algorithm_id):
            discrepancies.append(_discrepancy("broker_order_unattributed_or_foreign", order.client_order_id, order.observed_at, _json_ready(asdict(order)), severity=WeightedVotingReconciliationDiscrepancySeverity.CENTRAL_REVIEW))
            continue
        if order.client_order_id not in known_commands:
            discrepancies.append(_discrepancy("broker_order_missing_locally", order.client_order_id, order.observed_at, _json_ready(asdict(order)), severity=WeightedVotingReconciliationDiscrepancySeverity.PAUSE_ENTRIES))
            continue
        _persist_broker_order_lifecycle(store, order)

    for fill in sorted(fills, key=lambda item: item.filled_at):
        if not _is_weighted_voting_attributed(fill.algorithm_id):
            discrepancies.append(_discrepancy("broker_fill_unattributed_or_foreign", fill.client_order_id, fill.filled_at, _json_ready(asdict(fill)), severity=WeightedVotingReconciliationDiscrepancySeverity.CENTRAL_REVIEW))
            continue
        if fill.client_order_id not in known_commands:
            discrepancies.append(_discrepancy("broker_fill_missing_local_command", fill.client_order_id, fill.filled_at, _json_ready(asdict(fill)), severity=WeightedVotingReconciliationDiscrepancySeverity.PAUSE_ENTRIES))
            continue
        event_id = f"broker-fill-{fill.fill_id}"
        before = inventory_repository.current_snapshot(now=fill.filled_at)
        after = inventory_repository.append_event(
            event_id=event_id,
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload={
                "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                "fill_id": fill.fill_id,
                "position_id": f"weighted_voting.position.{fill.symbol.upper()}.{fill.client_order_id}",
                "symbol": fill.symbol.upper(),
                "side": "SHORT" if fill.side.upper() == "SELL" else "LONG",
                "quantity": -abs(fill.quantity) if fill.side.upper() == "SELL" else abs(fill.quantity),
                "average_entry_price": fill.average_fill_price,
                "opened_at": fill.filled_at.isoformat(),
                "decision_id": str(known_commands[fill.client_order_id].get("decisionId") or known_commands[fill.client_order_id].get("decision_id") or "weighted_voting.reconciliation.unknown_decision"),
                "order_intent_id": str(known_commands[fill.client_order_id].get("orderIntentId") or known_commands[fill.client_order_id].get("order_intent_id") or "weighted_voting.reconciliation.unknown_intent"),
                "client_order_id": fill.client_order_id,
                "source": "weighted_voting.broker_reconciliation.fill",
            },
            occurred_at=fill.filled_at,
            expected_snapshot_version=before.snapshot_version,
        )
        if after.snapshot_version == before.snapshot_version:
            duplicate_fill_ids.append(fill.fill_id)
        else:
            applied_fill_ids.append(fill.fill_id)
        store.write_snapshot(_fill_checkpoint_key(fill.fill_id), _json_ready(asdict(fill)))

    snapshot = inventory_repository.current_snapshot(now=reconciled_at)
    weighted_positions = tuple(position for position in positions if _is_weighted_voting_attributed(position.algorithm_id))
    for position in positions:
        if not _is_weighted_voting_attributed(position.algorithm_id):
            excluded_positions.append(str(position.broker_position_id or position.client_order_id or position.symbol))
            if position.algorithm_id is None:
                discrepancies.append(_discrepancy("broker_position_unattributed", position.client_order_id, position.observed_at, _json_ready(asdict(position)), severity=WeightedVotingReconciliationDiscrepancySeverity.CENTRAL_REVIEW))

    broker_quantity_by_client = {str(position.client_order_id): int(position.quantity) for position in weighted_positions if position.client_order_id}
    local_quantity_by_client = {position.client_order_id: int(position.quantity) for position in snapshot.open_positions}
    for client_order_id, quantity in broker_quantity_by_client.items():
        if client_order_id not in known_commands:
            discrepancies.append(_discrepancy("broker_position_missing_local_command", client_order_id, reconciled_at, {"brokerQuantity": quantity}, severity=WeightedVotingReconciliationDiscrepancySeverity.PAUSE_ENTRIES))
        elif local_quantity_by_client.get(client_order_id, 0) != quantity:
            discrepancies.append(_discrepancy("broker_position_quantity_mismatch", client_order_id, reconciled_at, {"brokerQuantity": quantity, "localQuantity": local_quantity_by_client.get(client_order_id, 0)}, severity=WeightedVotingReconciliationDiscrepancySeverity.PAUSE_ENTRIES))
    for pending in snapshot.pending_orders:
        if pending.client_order_id not in {order.client_order_id for order in orders} and pending.client_order_id not in broker_quantity_by_client:
            discrepancies.append(_discrepancy("local_pending_order_missing_at_broker", pending.client_order_id, reconciled_at, {"pendingQuantity": pending.quantity}, severity=WeightedVotingReconciliationDiscrepancySeverity.PAUSE_ENTRIES))

    for discrepancy in discrepancies:
        store.write_snapshot(f"{DISCREPANCY_PREFIX}{discrepancy.discrepancy_id}", discrepancy.as_dict())

    snapshot = inventory_repository.append_event(
        event_id=f"broker-reconciled-{_hash_payload({'at': reconciled_at, 'fills': applied_fill_ids, 'discrepancies': [item.discrepancy_id for item in discrepancies]})}",
        event_type=WeightedVotingInventoryEventType.BROKER_RECONCILED,
        payload={"algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID, "discrepancy_count": len(discrepancies)},
        occurred_at=reconciled_at,
        expected_snapshot_version=inventory_repository.current_snapshot(now=reconciled_at).snapshot_version,
    )
    entries_paused = any(_severity_value(item.severity) == WeightedVotingReconciliationDiscrepancySeverity.PAUSE_ENTRIES.value for item in discrepancies)
    result = WeightedVotingBrokerReconciliationResult(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        reconciled_at=reconciled_at,
        inventory_reconciled=not entries_paused,
        entries_paused=entries_paused,
        risk_reducing_exits_allowed=True,
        applied_fill_ids=tuple(applied_fill_ids),
        duplicate_fill_ids=tuple(duplicate_fill_ids),
        excluded_broker_position_ids=tuple(excluded_positions),
        discrepancies=tuple(discrepancies),
        snapshot=snapshot,
        reason_codes=tuple(dict.fromkeys(("weighted_voting.broker_reconciliation.completed", "weighted_voting.broker_reconciliation.entries_paused" if entries_paused else "weighted_voting.broker_reconciliation.inventory_reconciled"))),
    )
    store.write_snapshot(CHECKPOINT_KEY, result.as_dict())
    return result


def reconciliation_status() -> dict[str, Any]:
    return {
        "reconciliationVersion": WEIGHTED_VOTING_BROKER_RECONCILIATION_VERSION,
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "namespace": WEIGHTED_VOTING_BROKER_RECONCILIATION_NAMESPACE,
        "matchesBy": ("client_order_id", "algorithm_id"),
        "dailyTradeCountDefinition": "Weighted Voting daily trade count increments when a Weighted Voting position is closed, not when entry partial fills arrive.",
        "unattributedBrokerActivityPolicy": "central_operations_review_not_weighted_voting_assignment",
        "reasonCodes": ("weighted_voting.broker_reconciliation.status.ready",),
    }


def _known_weighted_voting_client_orders(store: WeightedVotingStateStore) -> dict[str, dict[str, Any]]:
    known: dict[str, dict[str, Any]] = {}
    for key, payload in _store_items(store):
        if not key.startswith("weighted_voting.execution_gateway.command."):
            continue
        if str(payload.get("algorithmId") or payload.get("algorithm_id")) != WEIGHTED_VOTING_ALGORITHM_ID:
            continue
        client_order_id = str(payload.get("clientOrderId") or payload.get("client_order_id") or "")
        if client_order_id:
            known[client_order_id] = payload
    return known


def _persist_broker_order_lifecycle(store: WeightedVotingStateStore, order: WeightedVotingBrokerOrderObservation) -> None:
    store.write_snapshot(
        f"{WEIGHTED_VOTING_BROKER_RECONCILIATION_NAMESPACE}.orders.{order.client_order_id}.{order.status.lower()}",
        {
            "reconciliationVersion": WEIGHTED_VOTING_BROKER_RECONCILIATION_VERSION,
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "clientOrderId": order.client_order_id,
            "brokerOrderId": order.broker_order_id,
            "status": order.status.upper(),
            "filledQuantity": order.filled_quantity,
            "averageFillPrice": order.average_fill_price,
            "replacedByClientOrderId": order.replaced_by_client_order_id,
            "observedAt": order.observed_at.isoformat(),
            "reasonCodes": ("weighted_voting.broker_reconciliation.order_lifecycle_observed",),
        },
    )


def _discrepancy(
    reason: str,
    client_order_id: str | None,
    observed_at: datetime,
    details: dict[str, Any],
    *,
    severity: WeightedVotingReconciliationDiscrepancySeverity,
) -> WeightedVotingBrokerReconciliationDiscrepancy:
    payload = {"reason": reason, "clientOrderId": client_order_id, "observedAt": observed_at.isoformat(), "details": details}
    return WeightedVotingBrokerReconciliationDiscrepancy(
        discrepancy_id=f"{reason}.{_hash_payload(payload)}",
        severity=severity,
        reason_code=f"weighted_voting.broker_reconciliation.{reason}",
        client_order_id=client_order_id,
        observed_at=observed_at,
        details=details,
    )


def _is_weighted_voting_attributed(algorithm_id: str | None) -> bool:
    return algorithm_id == WEIGHTED_VOTING_ALGORITHM_ID


def _severity_value(value: WeightedVotingReconciliationDiscrepancySeverity | str) -> str:
    return str(getattr(value, "value", value))


def _store_items(store: WeightedVotingStateStore) -> tuple[tuple[str, dict[str, Any]], ...]:
    snapshots = getattr(store, "snapshots", None)
    if not isinstance(snapshots, dict):
        return ()
    return tuple((str(key), value) for key, value in snapshots.items() if isinstance(value, dict))


def _fill_checkpoint_key(fill_id: str) -> str:
    return f"{WEIGHTED_VOTING_BROKER_RECONCILIATION_NAMESPACE}.fills.{fill_id}"


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(json.dumps(_json_ready(value), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _json_ready(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


__all__ = [
    "CHECKPOINT_KEY",
    "WEIGHTED_VOTING_BROKER_RECONCILIATION_NAMESPACE",
    "WEIGHTED_VOTING_BROKER_RECONCILIATION_VERSION",
    "WeightedVotingBrokerFillObservation",
    "WeightedVotingBrokerOrderObservation",
    "WeightedVotingBrokerPositionObservation",
    "WeightedVotingBrokerReconciliationDiscrepancy",
    "WeightedVotingBrokerReconciliationResult",
    "WeightedVotingReconciliationDiscrepancySeverity",
    "reconcile_weighted_voting_broker_observations",
    "reconciliation_status",
]
