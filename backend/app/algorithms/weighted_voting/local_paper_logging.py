"""Durable lifecycle logging for Weighted Voting local paper execution."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID


WEIGHTED_VOTING_LOCAL_PAPER_LIFECYCLE_VERSION = "weighted_voting_local_paper_lifecycle_v1"
WEIGHTED_VOTING_LOCAL_PAPER_LIFECYCLE_PREFIX = "weighted_voting.local_paper.lifecycle"
WEIGHTED_VOTING_LOCAL_PAPER_LIFECYCLE_INDEX_KEY = f"{WEIGHTED_VOTING_LOCAL_PAPER_LIFECYCLE_PREFIX}.index"

LOGGER = logging.getLogger(__name__)


def record_weighted_voting_local_paper_lifecycle_event(
    store: Any,
    *,
    event_name: str,
    source: Any,
    occurred_at: datetime,
    inventory_snapshot_version: int | None,
    position_id: str | None = None,
    status: str | None = None,
    reason_codes: tuple[str, ...] | list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist and emit an idempotent Weighted Voting local-paper lifecycle event."""

    algorithm_id = _value(source, "algorithmId", "algorithm_id") or WEIGHTED_VOTING_ALGORITHM_ID
    if str(algorithm_id) != WEIGHTED_VOTING_ALGORITHM_ID:
        raise ValueError("Weighted Voting local paper lifecycle event rejected foreign algorithm ownership")

    occurred_at = _utc(occurred_at)
    position = position_id or _value(source, "positionId", "position_id", "parentPositionId", "parent_position_id")
    event = {
        "version": WEIGHTED_VOTING_LOCAL_PAPER_LIFECYCLE_VERSION,
        "eventName": event_name,
        "eventType": event_name.rsplit(".", 1)[-1],
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "decisionId": str(_value(source, "decisionId", "decision_id") or ""),
        "orderIntentId": str(_value(source, "orderIntentId", "order_intent_id", "parentOrderIntentId") or ""),
        "clientOrderId": str(_value(source, "clientOrderId", "client_order_id") or ""),
        "inventorySnapshotVersion": inventory_snapshot_version,
        "executionMode": "LOCAL_PAPER",
        "brokerKind": "weighted_voting_local_paper",
        "occurredAt": occurred_at.isoformat(),
    }
    if position:
        event["positionId"] = str(position)
    symbol = _value(source, "symbol")
    if symbol:
        event["symbol"] = str(symbol).upper()
    side = _value(source, "side")
    if side:
        event["side"] = str(side).upper()
    if status:
        event["status"] = str(status).upper()
    elif _value(source, "status"):
        event["status"] = str(_value(source, "status")).upper()
    quantity = _value(source, "quantity")
    filled_quantity = _value(source, "filledQuantity", "filled_quantity")
    if quantity is not None:
        event["quantity"] = quantity
    if filled_quantity is not None:
        event["filledQuantity"] = filled_quantity
    reasons = tuple(str(code) for code in (reason_codes if reason_codes is not None else (_value(source, "reasonCodes", "reason_codes") or ())) if code)
    if reasons:
        event["reasonCodes"] = list(dict.fromkeys(reasons))
    if extra:
        event.update(extra)

    lifecycle_id = _lifecycle_event_id(event)
    event["lifecycleEventId"] = lifecycle_id
    key = f"{WEIGHTED_VOTING_LOCAL_PAPER_LIFECYCLE_PREFIX}.{lifecycle_id}"
    try:
        existing = store.read_snapshot(key)
    except KeyError:
        existing = None
    if isinstance(existing, dict):
        return existing

    store.write_snapshot(key, event)
    _append_lifecycle_index(store, lifecycle_id, key)
    LOGGER.info(event_name, extra={"weighted_voting_local_paper_lifecycle": event})
    return event


def _append_lifecycle_index(store: Any, lifecycle_id: str, key: str) -> None:
    try:
        index = dict(store.read_snapshot(WEIGHTED_VOTING_LOCAL_PAPER_LIFECYCLE_INDEX_KEY))
    except KeyError:
        index = {
            "version": WEIGHTED_VOTING_LOCAL_PAPER_LIFECYCLE_VERSION,
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "executionMode": "LOCAL_PAPER",
            "eventIds": [],
            "eventKeys": [],
        }
    event_ids = list(index.get("eventIds") or ())
    event_keys = list(index.get("eventKeys") or ())
    if lifecycle_id not in event_ids:
        event_ids.append(lifecycle_id)
    if key not in event_keys:
        event_keys.append(key)
    index["eventIds"] = event_ids
    index["eventKeys"] = event_keys
    index["count"] = len(event_ids)
    store.write_snapshot(WEIGHTED_VOTING_LOCAL_PAPER_LIFECYCLE_INDEX_KEY, index)


def _lifecycle_event_id(event: dict[str, Any]) -> str:
    identity = {
        key: event.get(key)
        for key in (
            "eventName",
            "decisionId",
            "orderIntentId",
            "clientOrderId",
            "positionId",
            "status",
            "inventorySnapshotVersion",
            "occurredAt",
            "filledQuantity",
        )
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]
    return f"{event['eventType']}.{digest}"


def _value(source: Any, *names: str) -> Any:
    for name in names:
        if isinstance(source, dict) and name in source:
            return source.get(name)
        if hasattr(source, name):
            return getattr(source, name)
    return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "WEIGHTED_VOTING_LOCAL_PAPER_LIFECYCLE_INDEX_KEY",
    "WEIGHTED_VOTING_LOCAL_PAPER_LIFECYCLE_PREFIX",
    "WEIGHTED_VOTING_LOCAL_PAPER_LIFECYCLE_VERSION",
    "record_weighted_voting_local_paper_lifecycle_event",
]
