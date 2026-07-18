"""Weighted Voting-owned order, position, and trade lifecycle state."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.persistence import WeightedVotingStateStore


WEIGHTED_VOTING_POSITION_TRADE_STATE_VERSION = "weighted_voting_position_trade_state_v1"
WEIGHTED_VOTING_POSITION_TRADE_STATE_NAMESPACE = "weighted_voting.position_trade_state"


class WeightedVotingOrderLifecycle(str, Enum):
    PENDING_ORDER = "pending_order"
    OPEN_ORDER = "open_order"
    PARTIAL_FILL = "partial_fill"
    FILLED_ORDER = "filled_order"
    CANCELLED_ORDER = "cancelled_order"
    REJECTED_ORDER = "rejected_order"


class WeightedVotingPositionLifecycle(str, Enum):
    OPEN_POSITION = "open_position"
    CLOSED_POSITION = "closed_position"


@dataclass(frozen=True)
class WeightedVotingOrderState:
    algorithm_id: Literal["weighted_voting"]
    order_state_id: str
    order_lifecycle: WeightedVotingOrderLifecycle
    decision_id: str
    client_order_id: str
    symbol: str
    side: str
    requested_quantity: int
    filled_quantity: int
    remaining_quantity: int
    average_fill_price: float | None
    entry_time: datetime | None
    stop: float | None
    target: float | None
    owning_decision_id: str
    owning_strategy_ids: tuple[str, ...]
    weight_version: str
    settings_version: str
    created_at: datetime
    updated_at: datetime
    broker_order_id: str | None = None
    rejection_reason: str | None = None
    cancellation_reason: str | None = None
    reason_codes: tuple[str, ...] = ()
    state_version: str = WEIGHTED_VOTING_POSITION_TRADE_STATE_VERSION

    def __post_init__(self) -> None:
        _require_weighted_voting_owner(self.algorithm_id)
        if self.owning_decision_id != self.decision_id:
            raise ValueError("Weighted Voting order state owning decision must match the order decision")
        if self.requested_quantity < 0 or self.filled_quantity < 0 or self.remaining_quantity < 0:
            raise ValueError("Weighted Voting order quantities must be non-negative")
        if self.filled_quantity + self.remaining_quantity > self.requested_quantity:
            raise ValueError("Weighted Voting order state cannot exceed requested quantity")
        if self.order_lifecycle == WeightedVotingOrderLifecycle.PARTIAL_FILL and self.filled_quantity <= 0:
            raise ValueError("Weighted Voting partial fill state requires a positive filled quantity")
        if self.order_lifecycle == WeightedVotingOrderLifecycle.FILLED_ORDER and self.remaining_quantity != 0:
            raise ValueError("Weighted Voting filled order state cannot have remaining quantity")

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["order_lifecycle"] = self.order_lifecycle.value
        payload["entry_time"] = self.entry_time.isoformat() if self.entry_time else None
        payload["created_at"] = self.created_at.isoformat()
        payload["updated_at"] = self.updated_at.isoformat()
        return _camel_payload(payload)


@dataclass(frozen=True)
class WeightedVotingPositionTradeState:
    algorithm_id: Literal["weighted_voting"]
    position_state_id: str
    position_lifecycle: WeightedVotingPositionLifecycle
    symbol: str
    side: str
    quantity: int
    average_entry_price: float
    realized_pnl: float
    unrealized_pnl: float
    entry_time: datetime
    exit_time: datetime | None
    stop: float | None
    target: float | None
    maximum_favorable_excursion: float
    maximum_adverse_excursion: float
    exit_reason: str | None
    owning_decision_id: str
    owning_strategy_ids: tuple[str, ...]
    weight_version: str
    settings_version: str
    source_order_state_id: str
    client_order_id: str
    created_at: datetime
    updated_at: datetime
    closed_order_state_id: str | None = None
    reason_codes: tuple[str, ...] = ()
    state_version: str = WEIGHTED_VOTING_POSITION_TRADE_STATE_VERSION

    def __post_init__(self) -> None:
        _require_weighted_voting_owner(self.algorithm_id)
        if self.quantity == 0 and self.position_lifecycle == WeightedVotingPositionLifecycle.OPEN_POSITION:
            raise ValueError("Weighted Voting open position state requires non-zero quantity")
        if self.average_entry_price <= 0:
            raise ValueError("Weighted Voting position average entry price must be positive")
        if self.position_lifecycle == WeightedVotingPositionLifecycle.CLOSED_POSITION and self.exit_time is None:
            raise ValueError("Weighted Voting closed position state requires an exit time")
        if self.position_lifecycle == WeightedVotingPositionLifecycle.OPEN_POSITION and self.exit_time is not None:
            raise ValueError("Weighted Voting open position state cannot have an exit time")

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["position_lifecycle"] = self.position_lifecycle.value
        payload["entry_time"] = self.entry_time.isoformat()
        payload["exit_time"] = self.exit_time.isoformat() if self.exit_time else None
        payload["created_at"] = self.created_at.isoformat()
        payload["updated_at"] = self.updated_at.isoformat()
        return _camel_payload(payload)


def create_weighted_voting_order_state(
    *,
    decision_id: str,
    client_order_id: str,
    symbol: str,
    side: str,
    requested_quantity: int,
    stop: float | None,
    target: float | None,
    owning_strategy_ids: tuple[str, ...],
    weight_version: str,
    settings_version: str,
    created_at: datetime,
    broker_order_id: str | None = None,
    lifecycle: WeightedVotingOrderLifecycle = WeightedVotingOrderLifecycle.PENDING_ORDER,
) -> WeightedVotingOrderState:
    filled_quantity = requested_quantity if lifecycle == WeightedVotingOrderLifecycle.FILLED_ORDER else 0
    remaining_quantity = 0 if lifecycle == WeightedVotingOrderLifecycle.FILLED_ORDER else requested_quantity
    entry_time = created_at if lifecycle == WeightedVotingOrderLifecycle.FILLED_ORDER else None
    return WeightedVotingOrderState(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        order_state_id=f"{WEIGHTED_VOTING_POSITION_TRADE_STATE_NAMESPACE}.order.{client_order_id}",
        order_lifecycle=lifecycle,
        decision_id=decision_id,
        client_order_id=client_order_id,
        symbol=symbol.upper(),
        side=side.upper(),
        requested_quantity=requested_quantity,
        filled_quantity=filled_quantity,
        remaining_quantity=remaining_quantity,
        average_fill_price=None,
        entry_time=entry_time,
        stop=stop,
        target=target,
        owning_decision_id=decision_id,
        owning_strategy_ids=owning_strategy_ids,
        weight_version=weight_version,
        settings_version=settings_version,
        broker_order_id=broker_order_id,
        created_at=created_at,
        updated_at=created_at,
        reason_codes=("weighted_voting.state.order_created",),
    )


def mark_weighted_voting_order_open(
    order: WeightedVotingOrderState,
    *,
    broker_order_id: str | None,
    opened_at: datetime,
) -> WeightedVotingOrderState:
    _assert_order_owned_by_weighted_voting(order)
    return replace(
        order,
        order_lifecycle=WeightedVotingOrderLifecycle.OPEN_ORDER,
        broker_order_id=broker_order_id or order.broker_order_id,
        updated_at=opened_at,
        reason_codes=tuple(dict.fromkeys((*order.reason_codes, "weighted_voting.state.order_open"))),
    )


def mark_weighted_voting_order_partial_fill(
    order: WeightedVotingOrderState,
    *,
    filled_quantity: int,
    average_fill_price: float,
    filled_at: datetime,
) -> WeightedVotingOrderState:
    _assert_order_owned_by_weighted_voting(order)
    if filled_quantity <= 0 or filled_quantity >= order.requested_quantity:
        raise ValueError("Weighted Voting partial fill quantity must be between zero and requested quantity")
    return replace(
        order,
        order_lifecycle=WeightedVotingOrderLifecycle.PARTIAL_FILL,
        filled_quantity=filled_quantity,
        remaining_quantity=order.requested_quantity - filled_quantity,
        average_fill_price=average_fill_price,
        entry_time=filled_at,
        updated_at=filled_at,
        reason_codes=tuple(dict.fromkeys((*order.reason_codes, "weighted_voting.state.order_partial_fill"))),
    )


def mark_weighted_voting_order_filled(
    order: WeightedVotingOrderState,
    *,
    filled_quantity: int,
    average_fill_price: float,
    filled_at: datetime,
) -> WeightedVotingOrderState:
    _assert_order_owned_by_weighted_voting(order)
    if filled_quantity <= 0 or filled_quantity > order.requested_quantity:
        raise ValueError("Weighted Voting filled quantity must be positive and no larger than requested quantity")
    remaining = order.requested_quantity - filled_quantity
    lifecycle = WeightedVotingOrderLifecycle.FILLED_ORDER if remaining == 0 else WeightedVotingOrderLifecycle.PARTIAL_FILL
    return replace(
        order,
        order_lifecycle=lifecycle,
        filled_quantity=filled_quantity,
        remaining_quantity=remaining,
        average_fill_price=average_fill_price,
        entry_time=order.entry_time or filled_at,
        updated_at=filled_at,
        reason_codes=tuple(dict.fromkeys((*order.reason_codes, "weighted_voting.state.order_filled" if remaining == 0 else "weighted_voting.state.order_partial_fill"))),
    )


def mark_weighted_voting_order_cancelled(
    order: WeightedVotingOrderState,
    *,
    cancelled_at: datetime,
    cancellation_reason: str,
) -> WeightedVotingOrderState:
    _assert_order_owned_by_weighted_voting(order)
    return replace(
        order,
        order_lifecycle=WeightedVotingOrderLifecycle.CANCELLED_ORDER,
        cancellation_reason=cancellation_reason,
        remaining_quantity=0,
        updated_at=cancelled_at,
        reason_codes=tuple(dict.fromkeys((*order.reason_codes, "weighted_voting.state.order_cancelled"))),
    )


def mark_weighted_voting_order_rejected(
    order: WeightedVotingOrderState,
    *,
    rejected_at: datetime,
    rejection_reason: str,
) -> WeightedVotingOrderState:
    _assert_order_owned_by_weighted_voting(order)
    return replace(
        order,
        order_lifecycle=WeightedVotingOrderLifecycle.REJECTED_ORDER,
        rejection_reason=rejection_reason,
        remaining_quantity=0,
        updated_at=rejected_at,
        reason_codes=tuple(dict.fromkeys((*order.reason_codes, "weighted_voting.state.order_rejected"))),
    )


def open_weighted_voting_position_from_order(
    order: WeightedVotingOrderState,
    *,
    opened_at: datetime,
) -> WeightedVotingPositionTradeState:
    _assert_order_owned_by_weighted_voting(order)
    if order.filled_quantity <= 0 or order.average_fill_price is None:
        raise ValueError("Weighted Voting position can only open from a filled or partially filled owned order")
    signed_quantity = -order.filled_quantity if order.side.upper() == "SELL" else order.filled_quantity
    return WeightedVotingPositionTradeState(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        position_state_id=f"{WEIGHTED_VOTING_POSITION_TRADE_STATE_NAMESPACE}.position.{order.client_order_id}",
        position_lifecycle=WeightedVotingPositionLifecycle.OPEN_POSITION,
        symbol=order.symbol.upper(),
        side="SHORT" if signed_quantity < 0 else "LONG",
        quantity=signed_quantity,
        average_entry_price=order.average_fill_price,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        entry_time=order.entry_time or opened_at,
        exit_time=None,
        stop=order.stop,
        target=order.target,
        maximum_favorable_excursion=0.0,
        maximum_adverse_excursion=0.0,
        exit_reason=None,
        owning_decision_id=order.owning_decision_id,
        owning_strategy_ids=order.owning_strategy_ids,
        weight_version=order.weight_version,
        settings_version=order.settings_version,
        source_order_state_id=order.order_state_id,
        client_order_id=order.client_order_id,
        created_at=opened_at,
        updated_at=opened_at,
        reason_codes=("weighted_voting.state.position_opened_from_owned_order",),
    )


def update_weighted_voting_position_mark(
    position: WeightedVotingPositionTradeState,
    *,
    mark_price: float,
    marked_at: datetime,
) -> WeightedVotingPositionTradeState:
    _assert_position_owned_by_weighted_voting(position)
    if position.position_lifecycle != WeightedVotingPositionLifecycle.OPEN_POSITION:
        raise ValueError("Weighted Voting can only update mark-to-market for an open Weighted Voting position")
    raw_pnl = (mark_price - position.average_entry_price) * position.quantity
    favorable = max(position.maximum_favorable_excursion, raw_pnl)
    adverse = min(position.maximum_adverse_excursion, raw_pnl)
    return replace(
        position,
        unrealized_pnl=round(raw_pnl, 10),
        maximum_favorable_excursion=round(favorable, 10),
        maximum_adverse_excursion=round(adverse, 10),
        updated_at=marked_at,
        reason_codes=tuple(dict.fromkeys((*position.reason_codes, "weighted_voting.state.position_marked"))),
    )


def close_weighted_voting_position(
    position: WeightedVotingPositionTradeState,
    *,
    exit_price: float,
    exit_time: datetime,
    exit_reason: str,
    closed_order_state_id: str | None = None,
) -> WeightedVotingPositionTradeState:
    _assert_position_owned_by_weighted_voting(position)
    if position.position_lifecycle != WeightedVotingPositionLifecycle.OPEN_POSITION:
        raise ValueError("Weighted Voting can only close an open Weighted Voting position")
    realized = (exit_price - position.average_entry_price) * position.quantity
    return replace(
        position,
        position_lifecycle=WeightedVotingPositionLifecycle.CLOSED_POSITION,
        realized_pnl=round(realized, 10),
        unrealized_pnl=0.0,
        exit_time=exit_time,
        exit_reason=exit_reason,
        closed_order_state_id=closed_order_state_id,
        updated_at=exit_time,
        reason_codes=tuple(dict.fromkeys((*position.reason_codes, "weighted_voting.state.position_closed"))),
    )


def persist_weighted_voting_order_state(store: WeightedVotingStateStore, order: WeightedVotingOrderState) -> None:
    _assert_order_owned_by_weighted_voting(order)
    store.write_snapshot(_order_key(order.client_order_id), order.as_dict())


def persist_weighted_voting_position_state(store: WeightedVotingStateStore, position: WeightedVotingPositionTradeState) -> None:
    _assert_position_owned_by_weighted_voting(position)
    store.write_snapshot(_position_key(position.position_state_id), position.as_dict())


def assert_weighted_voting_position_ownership(position: WeightedVotingPositionTradeState | dict[str, Any]) -> None:
    algorithm_id = position.algorithm_id if isinstance(position, WeightedVotingPositionTradeState) else position.get("algorithmId") or position.get("algorithm_id")
    _require_weighted_voting_owner(str(algorithm_id))


def position_trade_state_status() -> dict[str, Any]:
    return {
        "stateVersion": WEIGHTED_VOTING_POSITION_TRADE_STATE_VERSION,
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "namespace": WEIGHTED_VOTING_POSITION_TRADE_STATE_NAMESPACE,
        "ownedOrderRecords": [status.value for status in WeightedVotingOrderLifecycle],
        "ownedPositionRecords": [status.value for status in WeightedVotingPositionLifecycle],
        "ownedFields": [
            "realized_pnl",
            "unrealized_pnl",
            "entry_time",
            "exit_time",
            "stop",
            "target",
            "maximum_favorable_excursion",
            "maximum_adverse_excursion",
            "exit_reason",
            "owning_decision",
            "owning_strategies",
            "weight_version",
            "settings_version",
        ],
        "ownershipRule": "positions_from_other_algorithms_are_rejected",
    }


def _assert_order_owned_by_weighted_voting(order: WeightedVotingOrderState) -> None:
    _require_weighted_voting_owner(order.algorithm_id)


def _assert_position_owned_by_weighted_voting(position: WeightedVotingPositionTradeState) -> None:
    _require_weighted_voting_owner(position.algorithm_id)


def _require_weighted_voting_owner(algorithm_id: str) -> None:
    if algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
        raise ValueError("position or trade state does not belong to Weighted Voting")


def _order_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_POSITION_TRADE_STATE_NAMESPACE}.order.{client_order_id}"


def _position_key(position_state_id: str) -> str:
    return position_state_id


def _camel_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {_camel(key): value for key, value in payload.items()}


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


__all__ = [
    "WEIGHTED_VOTING_POSITION_TRADE_STATE_NAMESPACE",
    "WEIGHTED_VOTING_POSITION_TRADE_STATE_VERSION",
    "WeightedVotingOrderLifecycle",
    "WeightedVotingOrderState",
    "WeightedVotingPositionLifecycle",
    "WeightedVotingPositionTradeState",
    "assert_weighted_voting_position_ownership",
    "close_weighted_voting_position",
    "create_weighted_voting_order_state",
    "mark_weighted_voting_order_cancelled",
    "mark_weighted_voting_order_filled",
    "mark_weighted_voting_order_open",
    "mark_weighted_voting_order_partial_fill",
    "mark_weighted_voting_order_rejected",
    "open_weighted_voting_position_from_order",
    "persist_weighted_voting_order_state",
    "persist_weighted_voting_position_state",
    "position_trade_state_status",
    "update_weighted_voting_position_mark",
]
