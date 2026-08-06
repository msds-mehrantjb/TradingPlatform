"""Meta-Strategy local ledger PAPER broker adapter."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderIntentRecord


META_STRATEGY_LOCAL_LEDGER_PAPER_BROKER_VERSION = "meta_strategy_local_ledger_paper_broker_v1"
_ORDER_PREFIX = "meta_strategy.local_ledger.order."
_FILL_PREFIX = "meta_strategy.local_ledger.fill."
_EVENT_PREFIX = "meta_strategy.local_ledger.event."
_POSITION_PREFIX = "meta_strategy.local_ledger.position."


class MetaStrategyLocalLedgerPaperBroker:
    """Durable PAPER-only broker facade backed by the Meta-Strategy gateway ledger."""

    broker_kind = "local_paper_ledger"
    paper_endpoint = True

    def __init__(self, store: Any, *, immediate_fills: bool | None = None) -> None:
        self.store = store
        self.immediate_fills = (
            _env_bool("META_STRATEGY_LOCAL_LEDGER_IMMEDIATE_FILLS", False)
            if immediate_fills is None
            else bool(immediate_fills)
        )

    @property
    def configured(self) -> bool:
        return True

    def verify_paper_account(self) -> bool:
        now = datetime.now(UTC).isoformat()
        probe = {
            "brokerVersion": META_STRATEGY_LOCAL_LEDGER_PAPER_BROKER_VERSION,
            "brokerKind": self.broker_kind,
            "status": "VERIFIED",
            "configured": True,
            "verified": True,
            "paperOnly": True,
            "liveTradingEnabled": False,
            "verifiedAt": now,
            "reasonCodes": ("meta_strategy.local_ledger.paper_account_verified",),
        }
        try:
            self.store.write_snapshot("meta_strategy.local_ledger.paper_account", probe)
            self.store.write_snapshot("paper_broker_connectivity", probe)
            loaded = self.store.read_snapshot("meta_strategy.local_ledger.paper_account")
        except Exception:
            return False
        return loaded.get("paperOnly") is True and loaded.get("liveTradingEnabled") is False

    def get_clock(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        is_open = _env_bool("META_STRATEGY_LOCAL_LEDGER_MARKET_OPEN", False)
        return {
            "source": "meta_strategy.paper_ledger_clock",
            "capturedAt": now.isoformat(),
            "dataSourceTimestamp": now.isoformat(),
            "isOpen": is_open,
            "status": "open" if is_open else "closed",
            "authoritativeReadOnly": True,
            "fresh": True,
            "canAuthorizeNewEntries": is_open,
            "paperOnly": True,
            "liveTradingEnabled": False,
            "reasonCodes": (
                "meta_strategy.local_ledger.market_clock_open"
                if is_open
                else "meta_strategy.local_ledger.market_clock_closed"
            ),
        }

    def submit_bracket_order(self, intent: PaperOrderIntentRecord) -> PaperGatewayBrokerAck:
        submitted_at = datetime.now(UTC)
        broker_order_id = _broker_order_id(intent)
        order = {
            "brokerVersion": META_STRATEGY_LOCAL_LEDGER_PAPER_BROKER_VERSION,
            "algorithmId": "meta_strategy",
            "capitalPartitionId": intent.capitalPartitionId,
            "decisionId": intent.decisionId,
            "orderIntentId": intent.orderIntentId,
            "clientOrderId": intent.clientOrderId,
            "brokerOrderId": broker_order_id,
            "symbol": intent.symbol.upper(),
            "side": "BUY" if intent.side == Signal.BUY else "SELL",
            "quantity": int(intent.submittedQuantity),
            "remainingQuantity": int(intent.submittedQuantity),
            "limitPrice": intent.limitPrice,
            "stopPrice": intent.stopPrice,
            "targetPrice": intent.targetPrice,
            "status": "ACCEPTED",
            "paperOnly": True,
            "liveTradingEnabled": False,
            "createdAt": submitted_at.isoformat(),
            "updatedAt": submitted_at.isoformat(),
            "reasonCodes": ("meta_strategy.local_ledger.order_accepted",),
        }
        self.store.write_snapshot(_ORDER_PREFIX + intent.clientOrderId, order)
        self._write_event(order, status="ACCEPTED", timestamp=submitted_at)
        if self.immediate_fills and intent.submittedQuantity > 0:
            self._fill_order(order, timestamp=submitted_at)
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=broker_order_id,
            status="ACCEPTED",
            acceptedAt=submitted_at,
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        fill = _read_optional(self.store, _FILL_PREFIX + client_order_id)
        if not fill:
            return None
        return PaperGatewayFill(
            clientOrderId=str(fill["clientOrderId"]),
            algorithmId="meta_strategy",
            orderIntentId=str(fill["orderIntentId"]),
            symbol=str(fill["symbol"]).upper(),
            side=Signal.SELL if str(fill["side"]).upper() == "SELL" else Signal.BUY,
            filledQuantity=int(fill["filledQuantity"]),
            averageFillPrice=float(fill["averageFillPrice"]),
            status=str(fill.get("status") or "FILLED"),
            filledAt=_parse_time(fill.get("filledAt")) or datetime.now(UTC),
        )

    def cancel_order(self, client_order_id: str) -> bool:
        order = _read_optional(self.store, _ORDER_PREFIX + client_order_id)
        if not order:
            return False
        if str(order.get("status") or "").upper() in {"FILLED", "CANCELED", "CANCELLED", "REJECTED"}:
            return False
        now = datetime.now(UTC)
        updated = {**order, "status": "CANCELED", "updatedAt": now.isoformat(), "reasonCodes": ("meta_strategy.local_ledger.order_cancelled",)}
        self.store.write_snapshot(_ORDER_PREFIX + client_order_id, updated)
        self._write_event(updated, status="CANCELED", timestamp=now)
        return True

    def refresh_positions(self) -> list[dict[str, Any]]:
        positions = [
            dict(value)
            for key, value in self.store.snapshots.items()
            if str(key).startswith(_POSITION_PREFIX) and isinstance(value, Mapping)
        ]
        return positions

    def list_order_events(self) -> list[dict[str, Any]]:
        events = [
            dict(value)
            for key, value in self.store.snapshots.items()
            if str(key).startswith(_EVENT_PREFIX) and isinstance(value, Mapping)
        ]
        return sorted(events, key=lambda item: str(item.get("timestamp") or ""))

    def replace_order(self, broker_order_id: str, **updates: Any) -> dict[str, Any] | None:
        for key, value in self.store.snapshots.items():
            if not str(key).startswith(_ORDER_PREFIX) or not isinstance(value, Mapping):
                continue
            if str(value.get("brokerOrderId") or "") != broker_order_id:
                continue
            now = datetime.now(UTC)
            updated = {**dict(value), **dict(updates), "status": "REPLACED", "updatedAt": now.isoformat()}
            self.store.write_snapshot(str(key), updated)
            return self._write_event(updated, status="REPLACED", timestamp=now)
        return None

    def _fill_order(self, order: Mapping[str, Any], *, timestamp: datetime) -> None:
        client_order_id = str(order["clientOrderId"])
        price = float(order.get("limitPrice") or 0.01)
        fill = {
            **dict(order),
            "brokerFillId": _fill_id(order),
            "brokerEventId": _fill_id(order),
            "filledQuantity": int(order.get("quantity") or 0),
            "averageFillPrice": price,
            "fillPrice": price,
            "status": "FILLED",
            "filledAt": timestamp.isoformat(),
            "timestamp": timestamp.isoformat(),
            "reasonCodes": ("meta_strategy.local_ledger.order_filled",),
        }
        self.store.write_snapshot(_FILL_PREFIX + client_order_id, fill)
        self.store.write_snapshot(
            _POSITION_PREFIX + str(order["symbol"]).upper(),
            {
                "algorithmId": "meta_strategy",
                "capitalPartitionId": order["capitalPartitionId"],
                "clientOrderId": client_order_id,
                "brokerOrderId": order["brokerOrderId"],
                "symbol": str(order["symbol"]).upper(),
                "quantity": int(order.get("quantity") or 0),
                "side": str(order["side"]).upper(),
                "averagePrice": price,
                "paperOnly": True,
                "updatedAt": timestamp.isoformat(),
            },
        )
        updated_order = {**dict(order), "status": "FILLED", "remainingQuantity": 0, "updatedAt": timestamp.isoformat()}
        self.store.write_snapshot(_ORDER_PREFIX + client_order_id, updated_order)
        self._write_event(fill, status="FILLED", timestamp=timestamp)

    def _write_event(self, payload: Mapping[str, Any], *, status: str, timestamp: datetime) -> dict[str, Any]:
        event_id = _event_id(payload, status)
        event = {
            **dict(payload),
            "brokerEventId": event_id,
            "status": status,
            "timestamp": timestamp.isoformat(),
            "algorithmId": "meta_strategy",
            "paperOnly": True,
            "liveTradingEnabled": False,
        }
        self.store.write_snapshot(_EVENT_PREFIX + event_id, event)
        return event


def _read_optional(store: Any, key: str) -> dict[str, Any] | None:
    try:
        value = store.read_snapshot(key)
    except KeyError:
        return None
    return dict(value) if isinstance(value, Mapping) else None


def _broker_order_id(intent: PaperOrderIntentRecord) -> str:
    return "meta-ledger-broker-" + hashlib.sha256(intent.clientOrderId.encode("utf-8")).hexdigest()[:18]


def _fill_id(order: Mapping[str, Any]) -> str:
    return "meta-ledger-fill-" + hashlib.sha256(str(order.get("clientOrderId") or "").encode("utf-8")).hexdigest()[:18]


def _event_id(payload: Mapping[str, Any], status: str) -> str:
    stable = {
        "clientOrderId": payload.get("clientOrderId"),
        "brokerOrderId": payload.get("brokerOrderId"),
        "brokerFillId": payload.get("brokerFillId"),
        "status": status,
    }
    return "meta-ledger-event-" + hashlib.sha256(json.dumps(stable, sort_keys=True).encode("utf-8")).hexdigest()[:18]


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["META_STRATEGY_LOCAL_LEDGER_PAPER_BROKER_VERSION", "MetaStrategyLocalLedgerPaperBroker"]
