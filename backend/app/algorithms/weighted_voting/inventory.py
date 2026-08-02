"""Authoritative isolated inventory for Weighted Voting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal

from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.persistence import WeightedVotingStateStore


WEIGHTED_VOTING_INVENTORY_VERSION = "weighted_voting_inventory_v1"
WEIGHTED_VOTING_INVENTORY_NAMESPACE = "weighted_voting.inventory"
WEIGHTED_VOTING_DEFAULT_CAPITAL_PARTITION_ID = "weighted_voting.paper.default"
CURRENT_SNAPSHOT_KEY = f"{WEIGHTED_VOTING_INVENTORY_NAMESPACE}.snapshot.current"
EVENT_INDEX_KEY = f"{WEIGHTED_VOTING_INVENTORY_NAMESPACE}.events.index"
POSITION_INDEX_KEY = f"{WEIGHTED_VOTING_INVENTORY_NAMESPACE}.positions.index"
DAILY_LEDGER_PREFIX = f"{WEIGHTED_VOTING_INVENTORY_NAMESPACE}.daily_ledgers."


class WeightedVotingInventoryEventType(str, Enum):
    SESSION_STARTED = "session_started"
    ORDER_RESERVED = "order_reserved"
    ORDER_RELEASED = "order_released"
    FILL_RECORDED = "fill_recorded"
    POSITION_MARKED = "position_marked"
    POSITION_CLOSED = "position_closed"
    BROKER_RECONCILED = "broker_reconciled"
    LEGACY_POSITION_MIGRATED = "legacy_position_migrated"


@dataclass(frozen=True)
class WeightedVotingCapitalPartition:
    algorithm_id: Literal["weighted_voting"]
    capital_partition_id: str
    allocated_capital: float
    cash_available: float
    reserved_buying_power: float
    created_at: datetime
    updated_at: datetime
    inventory_version: str = WEIGHTED_VOTING_INVENTORY_VERSION

    def __post_init__(self) -> None:
        _require_weighted_voting(self.algorithm_id)
        if not self.capital_partition_id.startswith("weighted_voting."):
            raise ValueError("Weighted Voting inventory requires a weighted_voting capital partition")
        if min(self.allocated_capital, self.cash_available, self.reserved_buying_power) < 0:
            raise ValueError("Weighted Voting capital partition values must be non-negative")


@dataclass(frozen=True)
class WeightedVotingLot:
    algorithm_id: Literal["weighted_voting"]
    lot_id: str
    position_id: str
    symbol: str
    side: str
    quantity: int
    remaining_quantity: int
    entry_price: float
    opened_at: datetime
    decision_id: str
    order_intent_id: str
    client_order_id: str
    source: str = "weighted_voting.inventory"
    inventory_version: str = WEIGHTED_VOTING_INVENTORY_VERSION

    def __post_init__(self) -> None:
        _require_weighted_voting(self.algorithm_id)
        if not all((self.lot_id, self.position_id, self.symbol, self.side, self.decision_id, self.order_intent_id, self.client_order_id)):
            raise ValueError("Weighted Voting lot requires full algorithm attribution")
        if self.quantity == 0 or self.remaining_quantity == 0:
            raise ValueError("Weighted Voting lot quantity cannot be zero")
        if (self.quantity > 0) != (self.remaining_quantity > 0):
            raise ValueError("Weighted Voting lot remaining quantity must preserve side")
        if abs(self.remaining_quantity) > abs(self.quantity):
            raise ValueError("Weighted Voting lot remaining quantity cannot exceed original quantity")
        if self.entry_price <= 0:
            raise ValueError("Weighted Voting lot entry price must be positive")


@dataclass(frozen=True)
class WeightedVotingPosition:
    algorithm_id: Literal["weighted_voting"]
    position_id: str
    symbol: str
    side: str
    quantity: int
    average_entry_price: float
    opened_at: datetime
    decision_id: str
    order_intent_id: str
    client_order_id: str
    lots: tuple[WeightedVotingLot, ...] = ()
    owning_strategy_ids: tuple[str, ...] = ()
    realised_pnl: float = 0.0
    unrealised_pnl: float = 0.0
    mark_price: float | None = None
    closed_at: datetime | None = None
    exit_reason: str | None = None
    source: str = "weighted_voting.inventory"
    inventory_version: str = WEIGHTED_VOTING_INVENTORY_VERSION

    def __post_init__(self) -> None:
        _require_weighted_voting(self.algorithm_id)
        if not self.position_id:
            raise ValueError("Weighted Voting position requires a position_id")
        if not all((self.symbol, self.side, self.decision_id, self.order_intent_id, self.client_order_id)):
            raise ValueError("Weighted Voting position requires full algorithm attribution")
        if self.quantity == 0:
            raise ValueError("Weighted Voting position quantity cannot be zero")
        if self.average_entry_price <= 0:
            raise ValueError("Weighted Voting position average entry price must be positive")
        for lot in self.lots:
            _require_weighted_voting(lot.algorithm_id)
            if lot.position_id != self.position_id or lot.client_order_id != self.client_order_id:
                raise ValueError("Weighted Voting position lots must remain attributed to the same position")


@dataclass(frozen=True)
class WeightedVotingPendingOrder:
    algorithm_id: Literal["weighted_voting"]
    order_id: str
    symbol: str
    side: str
    quantity: int
    reserved_buying_power: float
    planned_risk_dollars: float
    decision_id: str
    order_intent_id: str
    client_order_id: str
    created_at: datetime
    filled_quantity: int = 0
    status: str = "WORKING"
    protective: bool = False
    inventory_version: str = WEIGHTED_VOTING_INVENTORY_VERSION

    def __post_init__(self) -> None:
        _require_weighted_voting(self.algorithm_id)
        if not all((self.order_id, self.symbol, self.side, self.decision_id, self.order_intent_id, self.client_order_id)):
            raise ValueError("Weighted Voting pending order requires full algorithm attribution")
        if self.quantity < 0 or self.filled_quantity < 0 or self.reserved_buying_power < 0 or self.planned_risk_dollars < 0:
            raise ValueError("Weighted Voting pending order values must be non-negative")


@dataclass(frozen=True)
class WeightedVotingRiskUsage:
    algorithm_id: Literal["weighted_voting"]
    daily_risk_used: float
    remaining_daily_risk: float
    remaining_capital_partition: float
    daily_loss_percent: float
    inventory_version: str = WEIGHTED_VOTING_INVENTORY_VERSION

    def __post_init__(self) -> None:
        _require_weighted_voting(self.algorithm_id)


@dataclass(frozen=True)
class WeightedVotingDailyLedger:
    algorithm_id: Literal["weighted_voting"]
    session_date: date
    daily_realised_pnl: float
    daily_unrealised_pnl: float
    daily_loss_percent: float
    daily_trade_count: int
    daily_risk_used: float
    remaining_daily_risk: float
    created_at: datetime
    updated_at: datetime
    inventory_version: str = WEIGHTED_VOTING_INVENTORY_VERSION

    def __post_init__(self) -> None:
        _require_weighted_voting(self.algorithm_id)
        if self.daily_trade_count < 0:
            raise ValueError("Weighted Voting daily trade count must be non-negative")

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class WeightedVotingInventorySnapshot:
    algorithm_id: Literal["weighted_voting"]
    inventory_version: str
    snapshot_version: int
    capital_partition_id: str
    symbol: str
    allocated_capital: float
    cash_available: float
    reserved_buying_power: float
    open_positions: tuple[WeightedVotingPosition, ...]
    pending_orders: tuple[WeightedVotingPendingOrder, ...]
    realised_pnl: float
    unrealised_pnl: float
    daily_realised_pnl: float
    daily_unrealised_pnl: float
    daily_loss_percent: float
    daily_trade_count: int
    daily_risk_used: float
    remaining_daily_risk: float
    remaining_capital_partition: float
    gross_exposure: float
    net_exposure: float
    last_broker_reconciliation_at: datetime | None
    last_event_sequence: int
    session_date: date
    created_at: datetime
    updated_at: datetime
    consumed_capital: float = 0.0
    individual_lots: tuple[WeightedVotingLot, ...] = ()
    working_orders: tuple[WeightedVotingPendingOrder, ...] = ()
    partially_filled_orders: tuple[WeightedVotingPendingOrder, ...] = ()
    protective_orders: tuple[WeightedVotingPendingOrder, ...] = ()
    daily_loss: float = 0.0
    last_broker_reconciliation_checkpoint: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_weighted_voting(self.algorithm_id)
        if not self.capital_partition_id.startswith("weighted_voting."):
            raise ValueError("Weighted Voting snapshot requires a weighted_voting capital partition")
        if self.snapshot_version < 0 or self.last_event_sequence < 0:
            raise ValueError("Weighted Voting inventory versions must be non-negative")
        for collection in (
            self.open_positions,
            self.pending_orders,
            self.individual_lots,
            self.working_orders,
            self.partially_filled_orders,
            self.protective_orders,
        ):
            for item in collection:
                _require_weighted_voting(item.algorithm_id)

    @staticmethod
    def empty(
        *,
        symbol: str,
        allocated_capital: float,
        session_date: date,
        created_at: datetime,
        capital_partition_id: str = WEIGHTED_VOTING_DEFAULT_CAPITAL_PARTITION_ID,
    ) -> "WeightedVotingInventorySnapshot":
        return WeightedVotingInventorySnapshot(
            algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
            inventory_version=WEIGHTED_VOTING_INVENTORY_VERSION,
            snapshot_version=0,
            capital_partition_id=capital_partition_id,
            symbol=symbol.upper(),
            allocated_capital=allocated_capital,
            cash_available=allocated_capital,
            reserved_buying_power=0.0,
            open_positions=(),
            pending_orders=(),
            realised_pnl=0.0,
            unrealised_pnl=0.0,
            daily_realised_pnl=0.0,
            daily_unrealised_pnl=0.0,
            daily_loss_percent=0.0,
            daily_trade_count=0,
            daily_risk_used=0.0,
            remaining_daily_risk=allocated_capital,
            remaining_capital_partition=allocated_capital,
            gross_exposure=0.0,
            net_exposure=0.0,
            last_broker_reconciliation_at=None,
            last_event_sequence=0,
            session_date=session_date,
            created_at=created_at,
            updated_at=created_at,
        )

    def as_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass(frozen=True)
class WeightedVotingInventoryEvent:
    algorithm_id: Literal["weighted_voting"]
    event_id: str
    event_type: WeightedVotingInventoryEventType | str
    event_sequence: int
    expected_snapshot_version: int
    occurred_at: datetime
    payload: dict[str, Any]
    reason_codes: tuple[str, ...]
    inventory_version: str = WEIGHTED_VOTING_INVENTORY_VERSION

    def __post_init__(self) -> None:
        _require_weighted_voting(self.algorithm_id)
        if not self.event_id:
            raise ValueError("Weighted Voting inventory event requires an event_id")
        _require_payload_attribution(self.payload)
        if self.event_sequence < 1 or self.expected_snapshot_version < 0:
            raise ValueError("Weighted Voting inventory event versions must be non-negative")

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["event_type"] = _event_type_value(self.event_type)
        return _json_ready(payload)


class WeightedVotingInventoryRepository:
    def __init__(
        self,
        store: WeightedVotingStateStore,
        *,
        symbol: str = "SPY",
        allocated_capital: float = 0.0,
        capital_partition_id: str = WEIGHTED_VOTING_DEFAULT_CAPITAL_PARTITION_ID,
    ) -> None:
        self.store = store
        self.symbol = symbol.upper()
        self.allocated_capital = float(allocated_capital)
        self.capital_partition_id = capital_partition_id

    def current_snapshot(self, *, now: datetime | None = None, session_date: date | None = None) -> WeightedVotingInventorySnapshot:
        payload = _read_optional(self.store, CURRENT_SNAPSHOT_KEY)
        if payload:
            return _snapshot_from_payload(payload)
        timestamp = now or datetime.now(timezone.utc)
        return WeightedVotingInventorySnapshot.empty(
            symbol=self.symbol,
            allocated_capital=self.allocated_capital,
            session_date=session_date or timestamp.date(),
            created_at=timestamp,
            capital_partition_id=self.capital_partition_id,
        )

    def append_event(
        self,
        *,
        event_id: str,
        event_type: WeightedVotingInventoryEventType | str,
        payload: dict[str, Any],
        occurred_at: datetime,
        expected_snapshot_version: int,
    ) -> WeightedVotingInventorySnapshot:
        _require_payload_attribution(payload)
        if self._event_exists(event_id):
            return self.current_snapshot(now=occurred_at)
        current = self.current_snapshot(now=occurred_at)
        if current.snapshot_version != expected_snapshot_version:
            raise RuntimeError("Weighted Voting inventory optimistic version check failed")
        event = WeightedVotingInventoryEvent(
            algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
            event_id=event_id,
            event_type=event_type,
            event_sequence=current.last_event_sequence + 1,
            expected_snapshot_version=expected_snapshot_version,
            occurred_at=occurred_at,
            payload=_json_ready(payload),
            reason_codes=(f"weighted_voting.inventory.event.{_event_type_value(event_type)}",),
        )
        updated = _apply_event(current, event)
        self._persist_event_and_snapshot(event, updated)
        return updated

    def initialize_session(
        self,
        *,
        session_date: date,
        allocated_capital: float,
        cash_available: float | None,
        occurred_at: datetime,
        expected_snapshot_version: int,
        event_id: str | None = None,
    ) -> WeightedVotingInventorySnapshot:
        return self.append_event(
            event_id=event_id or f"session-start-{session_date.isoformat()}",
            event_type=WeightedVotingInventoryEventType.SESSION_STARTED,
            payload={
                "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
                "session_date": session_date.isoformat(),
                "allocated_capital": allocated_capital,
                "cash_available": allocated_capital if cash_available is None else cash_available,
                "capital_partition_id": self.capital_partition_id,
                "symbol": self.symbol,
            },
            occurred_at=occurred_at,
            expected_snapshot_version=expected_snapshot_version,
        )

    def rebuild_snapshot_from_events(self) -> WeightedVotingInventorySnapshot:
        events = self._events()
        if not events:
            return self.current_snapshot()
        first = events[0]
        base = WeightedVotingInventorySnapshot.empty(
            symbol=str(first.payload.get("symbol") or self.symbol),
            allocated_capital=float(first.payload.get("allocated_capital") or self.allocated_capital),
            session_date=_date_value(first.payload.get("session_date"), first.occurred_at.date()),
            created_at=first.occurred_at,
            capital_partition_id=str(first.payload.get("capital_partition_id") or self.capital_partition_id),
        )
        snapshot = base
        for event in events:
            snapshot = _apply_event(snapshot, event)
        return snapshot

    def recover_current_snapshot(self) -> WeightedVotingInventorySnapshot:
        rebuilt = self.rebuild_snapshot_from_events()
        self._write_snapshot(rebuilt)
        return rebuilt

    def position_by_id(self, position_id: str) -> WeightedVotingPosition | None:
        snapshot = self.current_snapshot()
        for position in snapshot.open_positions:
            if position.position_id == position_id:
                return position
        payload = _read_optional(self.store, f"{WEIGHTED_VOTING_INVENTORY_NAMESPACE}.positions.{position_id}")
        return _position_from_payload(payload) if payload else None

    def migrate_legacy_positions(self, *, migrated_at: datetime, expected_snapshot_version: int) -> WeightedVotingInventorySnapshot:
        snapshot = self.current_snapshot(now=migrated_at)
        if snapshot.snapshot_version != expected_snapshot_version:
            raise RuntimeError("Weighted Voting inventory optimistic version check failed")
        for key, record in _snapshot_items(self.store):
            if not _legacy_position_key(key):
                continue
            payload = _canonical_position_payload(record, fallback_position_id=key, migrated_at=migrated_at)
            if payload is None:
                continue
            snapshot = self.append_event(
                event_id=f"migrate-{key}",
                event_type=WeightedVotingInventoryEventType.LEGACY_POSITION_MIGRATED,
                payload=payload,
                occurred_at=migrated_at,
                expected_snapshot_version=snapshot.snapshot_version,
            )
        return snapshot

    def _persist_event_and_snapshot(self, event: WeightedVotingInventoryEvent, snapshot: WeightedVotingInventorySnapshot) -> None:
        self.store.write_snapshot(_event_key(event), event.as_dict())
        index = _read_optional(self.store, EVENT_INDEX_KEY) or {"algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID, "event_ids": [], "event_keys": []}
        event_ids = list(index.get("event_ids", ()))
        event_keys = list(index.get("event_keys", ()))
        if event.event_id not in event_ids:
            event_ids.append(event.event_id)
            event_keys.append(_event_key(event))
        self.store.write_snapshot(EVENT_INDEX_KEY, {"algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID, "event_ids": event_ids, "event_keys": event_keys})
        self._write_snapshot(snapshot)

    def _write_snapshot(self, snapshot: WeightedVotingInventorySnapshot) -> None:
        self.store.write_snapshot(CURRENT_SNAPSHOT_KEY, snapshot.as_dict())
        position_ids = []
        for position in snapshot.open_positions:
            position_ids.append(position.position_id)
            self.store.write_snapshot(f"{WEIGHTED_VOTING_INVENTORY_NAMESPACE}.positions.{position.position_id}", _json_ready(asdict(position)))
        self.store.write_snapshot(POSITION_INDEX_KEY, {"algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID, "position_ids": position_ids})
        self.store.write_snapshot(f"{DAILY_LEDGER_PREFIX}{snapshot.session_date.isoformat()}", _daily_ledger(snapshot).as_dict())

    def _event_exists(self, event_id: str) -> bool:
        index = _read_optional(self.store, EVENT_INDEX_KEY) or {}
        if event_id in set(index.get("event_ids", ())):
            return True
        return any(payload.get("event_id") == event_id or payload.get("eventId") == event_id for _, payload in _snapshot_items(self.store) if ".inventory.events." in _)

    def _events(self) -> list[WeightedVotingInventoryEvent]:
        events: list[WeightedVotingInventoryEvent] = []
        index = _read_optional(self.store, EVENT_INDEX_KEY) or {}
        for key in index.get("event_keys", ()):
            payload = _read_optional(self.store, str(key))
            if payload:
                events.append(_event_from_payload(payload))
        if not events:
            for key, payload in _snapshot_items(self.store):
                if str(key).startswith(f"{WEIGHTED_VOTING_INVENTORY_NAMESPACE}.events."):
                    events.append(_event_from_payload(payload))
        return sorted({event.event_id: event for event in events}.values(), key=lambda event: event.event_sequence)


def _apply_event(snapshot: WeightedVotingInventorySnapshot, event: WeightedVotingInventoryEvent) -> WeightedVotingInventorySnapshot:
    event_type = _event_type_value(event.event_type)
    payload = event.payload
    if event_type == WeightedVotingInventoryEventType.SESSION_STARTED.value:
        session_date = _date_value(payload.get("session_date"), event.occurred_at.date())
        rollover = session_date != snapshot.session_date
        allocated = float(payload.get("allocated_capital", snapshot.allocated_capital))
        cash = float(payload.get("cash_available", allocated))
        return _recalculate(
            replace(
                snapshot,
                capital_partition_id=str(payload.get("capital_partition_id") or snapshot.capital_partition_id),
                symbol=str(payload.get("symbol") or snapshot.symbol).upper(),
                allocated_capital=allocated,
                cash_available=cash,
                reserved_buying_power=0.0 if rollover else snapshot.reserved_buying_power,
                pending_orders=() if rollover else snapshot.pending_orders,
                daily_realised_pnl=0.0,
                daily_unrealised_pnl=0.0,
                daily_loss_percent=0.0,
                daily_trade_count=0,
                daily_risk_used=0.0,
                remaining_daily_risk=allocated,
                remaining_capital_partition=allocated,
                session_date=session_date,
                snapshot_version=snapshot.snapshot_version + 1,
                last_event_sequence=event.event_sequence,
                updated_at=event.occurred_at,
            )
        )
    if event_type == WeightedVotingInventoryEventType.ORDER_RESERVED.value:
        order = _pending_order_from_payload(payload)
        pending = tuple(item for item in snapshot.pending_orders if item.order_id != order.order_id) + (order,)
        return _recalculate(replace(snapshot, pending_orders=pending, snapshot_version=snapshot.snapshot_version + 1, last_event_sequence=event.event_sequence, updated_at=event.occurred_at))
    if event_type == WeightedVotingInventoryEventType.ORDER_RELEASED.value:
        order_id = str(payload.get("order_id") or payload.get("orderId") or "")
        client_order_id = str(payload.get("client_order_id") or payload.get("clientOrderId") or "")
        pending = tuple(
            item
            for item in snapshot.pending_orders
            if not ((order_id and item.order_id == order_id) or (client_order_id and item.client_order_id == client_order_id))
        )
        return _recalculate(replace(snapshot, pending_orders=pending, snapshot_version=snapshot.snapshot_version + 1, last_event_sequence=event.event_sequence, updated_at=event.occurred_at))
    if event_type in {WeightedVotingInventoryEventType.FILL_RECORDED.value, WeightedVotingInventoryEventType.LEGACY_POSITION_MIGRATED.value}:
        position = _position_from_payload(payload)
        if event_type == WeightedVotingInventoryEventType.FILL_RECORDED.value and not (payload.get("fill_id") or payload.get("fillId")) and any(item.position_id == position.position_id or item.client_order_id == position.client_order_id for item in snapshot.open_positions):
            return replace(snapshot, last_event_sequence=event.event_sequence)
        positions = _merge_filled_position(snapshot.open_positions, position)
        pending = _pending_after_fill(snapshot.pending_orders, position.client_order_id, abs(position.quantity))
        return _recalculate(replace(snapshot, open_positions=positions, pending_orders=pending, snapshot_version=snapshot.snapshot_version + 1, last_event_sequence=event.event_sequence, updated_at=event.occurred_at))
    if event_type == WeightedVotingInventoryEventType.POSITION_MARKED.value:
        position_id = str(payload.get("position_id") or payload.get("positionId"))
        mark_price = float(payload["mark_price"] if "mark_price" in payload else payload["markPrice"])
        positions = tuple(_mark_position(position, position_id, mark_price) for position in snapshot.open_positions)
        return _recalculate(replace(snapshot, open_positions=positions, snapshot_version=snapshot.snapshot_version + 1, last_event_sequence=event.event_sequence, updated_at=event.occurred_at))
    if event_type == WeightedVotingInventoryEventType.POSITION_CLOSED.value:
        position_id = str(payload.get("position_id") or payload.get("positionId"))
        exit_price = float(payload["exit_price"] if "exit_price" in payload else payload["exitPrice"])
        open_positions = []
        realised_delta = 0.0
        trade_increment = 0
        for position in snapshot.open_positions:
            if position.position_id == position_id:
                realised_delta += _position_pnl(position, exit_price)
                trade_increment += 1
            else:
                open_positions.append(position)
        return _recalculate(
            replace(
                snapshot,
                open_positions=tuple(open_positions),
                realised_pnl=round(snapshot.realised_pnl + realised_delta, 10),
                daily_realised_pnl=round(snapshot.daily_realised_pnl + realised_delta, 10),
                daily_trade_count=snapshot.daily_trade_count + trade_increment,
                snapshot_version=snapshot.snapshot_version + 1,
                last_event_sequence=event.event_sequence,
                updated_at=event.occurred_at,
            )
        )
    if event_type == WeightedVotingInventoryEventType.BROKER_RECONCILED.value:
        checkpoint = payload.get("checkpoint") if isinstance(payload.get("checkpoint"), dict) else payload
        return _recalculate(
            replace(
                snapshot,
                last_broker_reconciliation_at=event.occurred_at,
                last_broker_reconciliation_checkpoint=_json_ready(dict(checkpoint)),
                snapshot_version=snapshot.snapshot_version + 1,
                last_event_sequence=event.event_sequence,
                updated_at=event.occurred_at,
            )
        )
    raise ValueError(f"unsupported Weighted Voting inventory event type: {event_type}")


def _recalculate(snapshot: WeightedVotingInventorySnapshot) -> WeightedVotingInventorySnapshot:
    reserved = round(sum(order.reserved_buying_power for order in snapshot.pending_orders), 10)
    unrealised = round(sum(position.unrealised_pnl for position in snapshot.open_positions), 10)
    gross = round(sum(abs(position.quantity * (position.mark_price or position.average_entry_price)) for position in snapshot.open_positions), 10)
    net = round(sum(position.quantity * (position.mark_price or position.average_entry_price) for position in snapshot.open_positions), 10)
    remaining_partition = round(max(0.0, snapshot.allocated_capital - reserved - gross), 10)
    daily_loss = max(0.0, -(snapshot.daily_realised_pnl + unrealised))
    daily_loss_percent = round(daily_loss / snapshot.allocated_capital * 100.0, 10) if snapshot.allocated_capital > 0 else 0.0
    remaining_daily_risk = round(max(0.0, snapshot.allocated_capital - daily_loss), 10)
    cash = round(max(0.0, snapshot.allocated_capital + snapshot.realised_pnl - reserved), 10)
    lots = tuple(lot for position in snapshot.open_positions for lot in position.lots)
    working_statuses = {"PENDING", "WORKING", "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED"}
    working_orders = tuple(order for order in snapshot.pending_orders if order.status.upper() in working_statuses)
    partially_filled_orders = tuple(order for order in snapshot.pending_orders if order.status.upper() == "PARTIALLY_FILLED" or order.filled_quantity > 0)
    protective_orders = tuple(order for order in snapshot.pending_orders if order.protective)
    return replace(
        snapshot,
        cash_available=cash,
        reserved_buying_power=reserved,
        consumed_capital=gross,
        unrealised_pnl=unrealised,
        daily_unrealised_pnl=unrealised,
        daily_loss=round(daily_loss, 10),
        daily_loss_percent=daily_loss_percent,
        daily_risk_used=round(daily_loss, 10),
        remaining_daily_risk=remaining_daily_risk,
        remaining_capital_partition=remaining_partition,
        gross_exposure=gross,
        net_exposure=net,
        individual_lots=lots,
        working_orders=working_orders,
        partially_filled_orders=partially_filled_orders,
        protective_orders=protective_orders,
    )


def _daily_ledger(snapshot: WeightedVotingInventorySnapshot) -> WeightedVotingDailyLedger:
    return WeightedVotingDailyLedger(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        session_date=snapshot.session_date,
        daily_realised_pnl=snapshot.daily_realised_pnl,
        daily_unrealised_pnl=snapshot.daily_unrealised_pnl,
        daily_loss_percent=snapshot.daily_loss_percent,
        daily_trade_count=snapshot.daily_trade_count,
        daily_risk_used=snapshot.daily_risk_used,
        remaining_daily_risk=snapshot.remaining_daily_risk,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


def _mark_position(position: WeightedVotingPosition, position_id: str, mark_price: float) -> WeightedVotingPosition:
    if position.position_id != position_id:
        return position
    return replace(position, mark_price=mark_price, unrealised_pnl=round(_position_pnl(position, mark_price), 10))


def _merge_filled_position(open_positions: tuple[WeightedVotingPosition, ...], fill_position: WeightedVotingPosition) -> tuple[WeightedVotingPosition, ...]:
    merged: list[WeightedVotingPosition] = []
    matched = False
    for position in open_positions:
        if position.position_id != fill_position.position_id and position.client_order_id != fill_position.client_order_id:
            merged.append(position)
            continue
        matched = True
        old_quantity = int(position.quantity)
        add_quantity = int(fill_position.quantity)
        new_quantity = old_quantity + add_quantity
        if old_quantity == 0 or (old_quantity > 0) != (add_quantity > 0):
            merged.append(fill_position)
            continue
        average = ((abs(old_quantity) * position.average_entry_price) + (abs(add_quantity) * fill_position.average_entry_price)) / abs(new_quantity)
        merged.append(
            replace(
                position,
                quantity=new_quantity,
                average_entry_price=round(average, 10),
                lots=position.lots + fill_position.lots,
                opened_at=min(position.opened_at, fill_position.opened_at),
                mark_price=fill_position.mark_price if fill_position.mark_price is not None else position.mark_price,
            )
        )
    if not matched:
        merged.append(fill_position)
    return tuple(merged)


def _pending_after_fill(pending_orders: tuple[WeightedVotingPendingOrder, ...], client_order_id: str, filled_quantity: int) -> tuple[WeightedVotingPendingOrder, ...]:
    updated: list[WeightedVotingPendingOrder] = []
    remaining_fill = max(0, int(filled_quantity))
    for order in pending_orders:
        if order.client_order_id != client_order_id:
            updated.append(order)
            continue
        if remaining_fill <= 0:
            updated.append(order)
            continue
        if remaining_fill >= order.quantity:
            remaining_fill -= order.quantity
            continue
        remaining_quantity = order.quantity - remaining_fill
        ratio = remaining_quantity / order.quantity if order.quantity > 0 else 0.0
        filled_delta = remaining_fill
        updated.append(
            replace(
                order,
                quantity=remaining_quantity,
                reserved_buying_power=round(order.reserved_buying_power * ratio, 10),
                planned_risk_dollars=round(order.planned_risk_dollars * ratio, 10),
                filled_quantity=order.filled_quantity + filled_delta,
                status="PARTIALLY_FILLED",
            )
        )
        remaining_fill = 0
    return tuple(updated)


def _position_pnl(position: WeightedVotingPosition, price: float) -> float:
    return (price - position.average_entry_price) * position.quantity


def _require_payload_attribution(payload: dict[str, Any]) -> None:
    algorithm_id = str(payload.get("algorithm_id") or payload.get("algorithmId") or WEIGHTED_VOTING_ALGORITHM_ID)
    _require_weighted_voting(algorithm_id)
    for nested_name in ("order", "fill", "position", "trade"):
        nested = payload.get(nested_name)
        if isinstance(nested, dict):
            _require_weighted_voting(str(nested.get("algorithm_id") or nested.get("algorithmId") or ""))


def _require_weighted_voting(algorithm_id: str) -> None:
    if algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
        raise ValueError("Weighted Voting inventory rejects cross-algorithm writes")


def _read_optional(store: WeightedVotingStateStore, key: str) -> dict | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


def _snapshot_items(store: WeightedVotingStateStore) -> tuple[tuple[str, dict], ...]:
    snapshots = getattr(store, "snapshots", None)
    if not isinstance(snapshots, dict):
        return ()
    return tuple((str(key), value) for key, value in snapshots.items() if isinstance(value, dict))


def _event_key(event: WeightedVotingInventoryEvent) -> str:
    return f"{WEIGHTED_VOTING_INVENTORY_NAMESPACE}.events.{event.event_sequence:012d}.{event.event_id}"


def _event_type_value(value: WeightedVotingInventoryEventType | str) -> str:
    return str(getattr(value, "value", value))


def _snapshot_from_payload(payload: dict[str, Any]) -> WeightedVotingInventorySnapshot:
    values = _snake_dict(payload)
    values["open_positions"] = tuple(_position_from_payload(item) for item in values.get("open_positions", ()))
    values["pending_orders"] = tuple(_pending_order_from_payload(item) for item in values.get("pending_orders", ()))
    values["individual_lots"] = tuple(_lot_from_payload(item) for item in values.get("individual_lots", ()))
    values["working_orders"] = tuple(_pending_order_from_payload(item) for item in values.get("working_orders", ()))
    values["partially_filled_orders"] = tuple(_pending_order_from_payload(item) for item in values.get("partially_filled_orders", ()))
    values["protective_orders"] = tuple(_pending_order_from_payload(item) for item in values.get("protective_orders", ()))
    values["created_at"] = _datetime_value(values["created_at"])
    values["updated_at"] = _datetime_value(values["updated_at"])
    values["last_broker_reconciliation_at"] = _datetime_optional(values.get("last_broker_reconciliation_at"))
    values["session_date"] = _date_value(values["session_date"], values["updated_at"].date())
    values["last_broker_reconciliation_checkpoint"] = dict(values.get("last_broker_reconciliation_checkpoint") or {})
    allowed = {field.name for field in WeightedVotingInventorySnapshot.__dataclass_fields__.values()}
    values = {key: value for key, value in values.items() if key in allowed}
    return WeightedVotingInventorySnapshot(**values)


def _event_from_payload(payload: dict[str, Any]) -> WeightedVotingInventoryEvent:
    values = _snake_dict(payload)
    values["event_type"] = values.get("event_type")
    values["occurred_at"] = _datetime_value(values["occurred_at"])
    values["reason_codes"] = tuple(values.get("reason_codes", ()))
    return WeightedVotingInventoryEvent(**values)


def _position_from_payload(payload: dict[str, Any]) -> WeightedVotingPosition:
    values = _snake_dict(payload)
    values["algorithm_id"] = str(values.get("algorithm_id") or WEIGHTED_VOTING_ALGORITHM_ID)
    values["position_id"] = str(values.get("position_id") or values.get("position_state_id") or f"weighted_voting.position.{values.get('client_order_id')}")
    values["symbol"] = str(values.get("symbol") or "").upper()
    values["side"] = str(values.get("side") or ("SHORT" if int(values.get("quantity", 0)) < 0 else "LONG")).upper()
    values["quantity"] = int(values.get("quantity") or 0)
    values["average_entry_price"] = float(values.get("average_entry_price") or values.get("average_fill_price") or 0.0)
    values["opened_at"] = _datetime_value(values.get("opened_at") or values.get("entry_time") or values.get("created_at"))
    values["decision_id"] = str(values.get("decision_id") or values.get("owning_decision_id") or "")
    values["order_intent_id"] = str(values.get("order_intent_id") or "")
    values["client_order_id"] = str(values.get("client_order_id") or "")
    values["lots"] = tuple(_lot_from_payload(item) for item in values.get("lots", ()))
    values["owning_strategy_ids"] = tuple(values.get("owning_strategy_ids", ()))
    values["realised_pnl"] = float(values.get("realised_pnl", values.get("realized_pnl", 0.0)) or 0.0)
    values["unrealised_pnl"] = float(values.get("unrealised_pnl", values.get("unrealized_pnl", 0.0)) or 0.0)
    values["mark_price"] = None if values.get("mark_price") is None else float(values.get("mark_price"))
    values["closed_at"] = _datetime_optional(values.get("closed_at") or values.get("exit_time"))
    values["exit_reason"] = values.get("exit_reason")
    if not values["lots"] and values["quantity"] != 0:
        values["lots"] = (_lot_from_position_values(values),)
    allowed = {field.name for field in WeightedVotingPosition.__dataclass_fields__.values()}
    return WeightedVotingPosition(**{key: value for key, value in values.items() if key in allowed})


def _lot_from_payload(payload: dict[str, Any]) -> WeightedVotingLot:
    values = _snake_dict(payload)
    values["algorithm_id"] = str(values.get("algorithm_id") or WEIGHTED_VOTING_ALGORITHM_ID)
    values["lot_id"] = str(values.get("lot_id") or values.get("fill_id") or f"weighted_voting.lot.{values.get('client_order_id')}")
    values["position_id"] = str(values.get("position_id") or f"weighted_voting.position.{values.get('client_order_id')}")
    values["symbol"] = str(values.get("symbol") or "").upper()
    values["side"] = str(values.get("side") or ("SHORT" if int(values.get("quantity", 0)) < 0 else "LONG")).upper()
    values["quantity"] = int(values.get("quantity") or 0)
    values["remaining_quantity"] = int(values.get("remaining_quantity") or values["quantity"])
    values["entry_price"] = float(values.get("entry_price") or values.get("average_entry_price") or values.get("average_fill_price") or 0.0)
    values["opened_at"] = _datetime_value(values.get("opened_at") or values.get("filled_at") or values.get("created_at"))
    values["decision_id"] = str(values.get("decision_id") or "")
    values["order_intent_id"] = str(values.get("order_intent_id") or "")
    values["client_order_id"] = str(values.get("client_order_id") or "")
    allowed = {field.name for field in WeightedVotingLot.__dataclass_fields__.values()}
    return WeightedVotingLot(**{key: value for key, value in values.items() if key in allowed})


def _lot_from_position_values(values: dict[str, Any]) -> WeightedVotingLot:
    lot_seed = {
        "algorithm_id": values["algorithm_id"],
        "lot_id": str(values.get("fill_id") or f"{values['position_id']}.lot.{values['opened_at'].isoformat()}.{abs(values['quantity'])}"),
        "position_id": values["position_id"],
        "symbol": values["symbol"],
        "side": values["side"],
        "quantity": values["quantity"],
        "remaining_quantity": values["quantity"],
        "entry_price": values["average_entry_price"],
        "opened_at": values["opened_at"],
        "decision_id": values["decision_id"],
        "order_intent_id": values["order_intent_id"],
        "client_order_id": values["client_order_id"],
        "source": values.get("source") or "weighted_voting.inventory.fill",
    }
    return WeightedVotingLot(**lot_seed)


def _pending_order_from_payload(payload: dict[str, Any]) -> WeightedVotingPendingOrder:
    values = _snake_dict(payload)
    values["algorithm_id"] = str(values.get("algorithm_id") or WEIGHTED_VOTING_ALGORITHM_ID)
    values["order_id"] = str(values.get("order_id") or values.get("order_intent_id") or values.get("client_order_id"))
    values["symbol"] = str(values.get("symbol") or "").upper()
    values["side"] = str(values.get("side") or "").upper()
    values["quantity"] = int(values.get("quantity") or values.get("requested_quantity") or 0)
    values["reserved_buying_power"] = float(values.get("reserved_buying_power") or 0.0)
    values["planned_risk_dollars"] = float(values.get("planned_risk_dollars") or 0.0)
    values["decision_id"] = str(values.get("decision_id") or "")
    values["order_intent_id"] = str(values.get("order_intent_id") or "")
    values["client_order_id"] = str(values.get("client_order_id") or "")
    values["created_at"] = _datetime_value(values.get("created_at"))
    values["filled_quantity"] = int(values.get("filled_quantity") or 0)
    values["status"] = str(values.get("status") or "WORKING").upper()
    values["protective"] = bool(values.get("protective") or values.get("is_protective") or False)
    allowed = {field.name for field in WeightedVotingPendingOrder.__dataclass_fields__.values()}
    return WeightedVotingPendingOrder(**{key: value for key, value in values.items() if key in allowed})


def _canonical_position_payload(record: dict[str, Any], *, fallback_position_id: str, migrated_at: datetime) -> dict[str, Any] | None:
    values = _snake_dict(record)
    if str(values.get("algorithm_id") or "") != WEIGHTED_VOTING_ALGORITHM_ID:
        return None
    quantity = int(values.get("quantity") or 0)
    average = values.get("average_entry_price") if values.get("average_entry_price") is not None else values.get("average_fill_price")
    if quantity == 0 or average is None:
        return None
    return {
        "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
        "position_id": str(values.get("position_id") or values.get("position_state_id") or fallback_position_id),
        "symbol": str(values.get("symbol") or "SPY").upper(),
        "side": str(values.get("side") or ("SHORT" if quantity < 0 else "LONG")).upper(),
        "quantity": quantity,
        "average_entry_price": float(average),
        "opened_at": (_datetime_optional(values.get("opened_at") or values.get("entry_time") or values.get("created_at")) or migrated_at).isoformat(),
        "decision_id": str(values.get("decision_id") or values.get("owning_decision_id") or "legacy-weighted-voting-decision"),
        "order_intent_id": str(values.get("order_intent_id") or "legacy-weighted-voting-order"),
        "client_order_id": str(values.get("client_order_id") or values.get("position_id") or fallback_position_id),
        "owning_strategy_ids": tuple(values.get("owning_strategy_ids", ())),
        "source": "weighted_voting.inventory.migration",
    }


def _legacy_position_key(key: str) -> bool:
    return (
        key.startswith("weighted_voting.execution_gateway.position.")
        or key.startswith("weighted_voting.position_trade_state.position.")
        or (key.startswith("weighted_voting.positions.") and not key.startswith(f"{WEIGHTED_VOTING_INVENTORY_NAMESPACE}.positions."))
    )


def _snake_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {_snake(str(key)): value for key, value in payload.items()}


def _snake(value: str) -> str:
    out = []
    for char in value:
        if char.isupper():
            out.append("_")
            out.append(char.lower())
        else:
            out.append(char)
    return "".join(out).lstrip("_")


def _datetime_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError("Weighted Voting inventory datetime is required")


def _datetime_optional(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return _datetime_value(value)


def _date_value(value: Any, fallback: date) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value)
    return fallback


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def inventory_status() -> dict[str, Any]:
    return {
        "inventoryVersion": WEIGHTED_VOTING_INVENTORY_VERSION,
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "namespace": WEIGHTED_VOTING_INVENTORY_NAMESPACE,
        "capitalPartitionId": WEIGHTED_VOTING_DEFAULT_CAPITAL_PARTITION_ID,
        "eventSourcing": "event_plus_snapshot_transaction",
        "optimisticVersionChecks": True,
        "idempotency": "event_id",
        "positionCollection": f"{WEIGHTED_VOTING_INVENTORY_NAMESPACE}.positions.*",
        "legacyPositionSources": (
            "weighted_voting.execution_gateway.position.*",
            "weighted_voting.position_trade_state.position.*",
            "weighted_voting.positions.*",
        ),
        "brokerAccountRole": "read_only_reconciliation_source",
        "authoritativeFields": (
            "allocated_capital",
            "available_algorithm_cash",
            "capital_reserved_by_pending_orders",
            "consumed_capital",
            "remaining_daily_risk",
            "open_positions",
            "individual_lots",
            "average_entry_price",
            "pending_orders",
            "working_orders",
            "partially_filled_orders",
            "protective_orders",
            "realized_pnl",
            "unrealized_pnl",
            "daily_loss",
            "daily_trade_count",
            "gross_exposure",
            "net_exposure",
            "last_broker_reconciliation_checkpoint",
            "inventory_version",
        ),
        "reasonCodes": ("weighted_voting.inventory.status.ready",),
    }


__all__ = [
    "WEIGHTED_VOTING_INVENTORY_NAMESPACE",
    "WEIGHTED_VOTING_INVENTORY_VERSION",
    "WeightedVotingCapitalPartition",
    "WeightedVotingDailyLedger",
    "WeightedVotingInventoryEvent",
    "WeightedVotingInventoryEventType",
    "WeightedVotingInventoryRepository",
    "WeightedVotingInventorySnapshot",
    "WeightedVotingLot",
    "WeightedVotingPendingOrder",
    "WeightedVotingPosition",
    "WeightedVotingRiskUsage",
    "inventory_status",
]
