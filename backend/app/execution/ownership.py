from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.domain.models import Direction, DomainModel, Signal, _require_utc


ALGORITHM_OWNERSHIP_LEDGER_VERSION = "algorithm_ownership_ledger_v1"
AlgorithmId = Literal["voting_ensemble", "weighted_voting", "confidence_aggregation", "regime_selector", "meta_strategy"]
OwnershipIntent = Literal["new_entry", "protective_exit", "risk_reducing", "end_of_day_liquidation", "reconciliation"]
OwnershipAction = Literal["ACCEPTED", "REJECTED", "QUEUED", "RECORDED"]
SameSymbolConflictPolicy = Literal["reject_conflicting_entries", "queue_conflicting_entries", "simulate_separately", "require_separate_paper_accounts"]


class CapitalPartition(DomainModel):
    algorithmId: AlgorithmId
    capitalPartitionId: str = Field(min_length=1)
    maxCapitalDollars: float = Field(ge=0.0)
    allocatedRiskDollars: float = Field(default=0.0, ge=0.0)
    realizedPnl: float = 0.0
    tradesToday: int = Field(default=0, ge=0)
    sessionDate: date

    @model_validator(mode="after")
    def partition_id_must_belong_to_algorithm(self) -> CapitalPartition:
        if not self.capitalPartitionId.startswith(f"{self.algorithmId}."):
            raise ValueError("capitalPartitionId must be namespaced by algorithmId")
        return self


class OwnedRiskReservation(DomainModel):
    algorithmId: AlgorithmId
    capitalPartitionId: str = Field(min_length=1)
    decisionId: str = Field(min_length=1)
    orderIntentId: str = Field(min_length=1)
    riskReservationId: str = Field(min_length=1)
    positionOwner: AlgorithmId
    parentOrderId: str = Field(min_length=1)
    exitOwner: AlgorithmId
    symbol: str = Field(min_length=1)
    side: Signal
    reservedRiskDollars: float = Field(ge=0.0)
    reservedNotionalDollars: float = Field(ge=0.0)
    createdAt: datetime
    sessionDate: date

    @field_validator("createdAt")
    @classmethod
    def created_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def ownership_must_be_consistent(self) -> OwnedRiskReservation:
        _validate_ownership_fields(self.algorithmId, self.capitalPartitionId, self.positionOwner, self.exitOwner)
        return self


class OwnedOrderIntent(DomainModel):
    algorithmId: AlgorithmId
    capitalPartitionId: str = Field(min_length=1)
    decisionId: str = Field(min_length=1)
    orderIntentId: str = Field(min_length=1)
    riskReservationId: str = Field(min_length=1)
    positionOwner: AlgorithmId
    parentOrderId: str = Field(min_length=1)
    exitOwner: AlgorithmId
    symbol: str = Field(min_length=1)
    side: Signal
    intent: OwnershipIntent
    quantity: int = Field(gt=0)
    entryPrice: float = Field(gt=0)
    createdAt: datetime
    sessionDate: date

    @field_validator("createdAt")
    @classmethod
    def created_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def ownership_must_be_consistent(self) -> OwnedOrderIntent:
        _validate_ownership_fields(self.algorithmId, self.capitalPartitionId, self.positionOwner, self.exitOwner)
        return self


class OwnedPosition(DomainModel):
    algorithmId: AlgorithmId
    capitalPartitionId: str = Field(min_length=1)
    decisionId: str = Field(min_length=1)
    orderIntentId: str = Field(min_length=1)
    riskReservationId: str = Field(min_length=1)
    positionOwner: AlgorithmId
    parentOrderId: str = Field(min_length=1)
    exitOwner: AlgorithmId
    symbol: str = Field(min_length=1)
    side: Signal
    quantity: int = Field(gt=0)
    averageEntryPrice: float = Field(gt=0)
    openedAt: datetime
    sessionDate: date

    @field_validator("openedAt")
    @classmethod
    def opened_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def ownership_must_be_consistent(self) -> OwnedPosition:
        _validate_ownership_fields(self.algorithmId, self.capitalPartitionId, self.positionOwner, self.exitOwner)
        return self


class OwnedTradeRecord(DomainModel):
    algorithmId: AlgorithmId
    capitalPartitionId: str = Field(min_length=1)
    decisionId: str = Field(min_length=1)
    orderIntentId: str = Field(min_length=1)
    riskReservationId: str = Field(min_length=1)
    positionOwner: AlgorithmId
    parentOrderId: str = Field(min_length=1)
    exitOwner: AlgorithmId
    tradeId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: Signal
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    realizedPnl: float = 0.0
    tradedAt: datetime
    sessionDate: date

    @field_validator("tradedAt")
    @classmethod
    def traded_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def ownership_must_be_consistent(self) -> OwnedTradeRecord:
        _validate_ownership_fields(self.algorithmId, self.capitalPartitionId, self.positionOwner, self.exitOwner)
        return self


class OwnershipDecision(DomainModel):
    ledgerVersion: str = ALGORITHM_OWNERSHIP_LEDGER_VERSION
    action: OwnershipAction
    accepted: bool
    algorithmId: AlgorithmId
    capitalPartitionId: str
    orderIntentId: str
    reasonCodes: tuple[str, ...]
    explanation: str
    evaluatedAt: datetime
    configurationHash: str

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class VirtualSubLedgerSnapshot(DomainModel):
    ledgerVersion: str = ALGORITHM_OWNERSHIP_LEDGER_VERSION
    algorithmId: AlgorithmId
    capitalPartitionId: str
    realizedPnl: float
    reservedRiskDollars: float = Field(ge=0.0)
    tradeCount: int = Field(ge=0)
    openQuantityBySymbol: dict[str, int]
    positionIds: tuple[str, ...]
    tradeIds: tuple[str, ...]


@dataclass
class AlgorithmOwnershipLedger:
    same_symbol_policy: SameSymbolConflictPolicy = "reject_conflicting_entries"
    partitions: dict[str, CapitalPartition] = field(default_factory=dict)
    reservations: dict[str, OwnedRiskReservation] = field(default_factory=dict)
    orders: dict[str, OwnedOrderIntent] = field(default_factory=dict)
    queued_orders: dict[str, OwnedOrderIntent] = field(default_factory=dict)
    positions: dict[str, OwnedPosition] = field(default_factory=dict)
    trades: dict[str, OwnedTradeRecord] = field(default_factory=dict)

    def register_partition(self, partition: CapitalPartition) -> None:
        self.partitions[partition.capitalPartitionId] = partition

    def reserve_risk(self, reservation: OwnedRiskReservation) -> OwnershipDecision:
        partition = self._partition_for(reservation.algorithmId, reservation.capitalPartitionId)
        if partition.allocatedRiskDollars + reservation.reservedRiskDollars > partition.maxCapitalDollars:
            return _decision(
                "REJECTED",
                False,
                reservation.algorithmId,
                reservation.capitalPartitionId,
                reservation.orderIntentId,
                ("ownership.capital_partition_limit",),
                "Risk reservation exceeds the algorithm capital partition.",
                reservation.createdAt,
            )
        self.reservations[reservation.riskReservationId] = reservation
        self.partitions[partition.capitalPartitionId] = partition.model_copy(
            update={"allocatedRiskDollars": partition.allocatedRiskDollars + reservation.reservedRiskDollars}
        )
        return _decision(
            "ACCEPTED",
            True,
            reservation.algorithmId,
            reservation.capitalPartitionId,
            reservation.orderIntentId,
            ("ownership.risk_reserved",),
            "Risk is reserved inside the algorithm capital partition.",
            reservation.createdAt,
        )

    def register_order_intent(self, order: OwnedOrderIntent) -> OwnershipDecision:
        self._partition_for(order.algorithmId, order.capitalPartitionId)
        if order.riskReservationId not in self.reservations:
            return _decision("REJECTED", False, order.algorithmId, order.capitalPartitionId, order.orderIntentId, ("ownership.risk_reservation_missing",), "Order intent is missing its risk reservation.", order.createdAt)
        conflict = self._same_symbol_conflict(order)
        if conflict and self.same_symbol_policy == "reject_conflicting_entries":
            return _decision("REJECTED", False, order.algorithmId, order.capitalPartitionId, order.orderIntentId, ("ownership.spy_conflicting_entry_rejected",), "Opposing same-symbol SPY entry is rejected by explicit conflict policy.", order.createdAt)
        if conflict and self.same_symbol_policy == "queue_conflicting_entries":
            self.queued_orders[order.orderIntentId] = order
            return _decision("QUEUED", False, order.algorithmId, order.capitalPartitionId, order.orderIntentId, ("ownership.spy_conflicting_entry_queued",), "Opposing same-symbol SPY entry is queued by explicit conflict policy.", order.createdAt)
        if conflict and self.same_symbol_policy == "require_separate_paper_accounts":
            return _decision("REJECTED", False, order.algorithmId, order.capitalPartitionId, order.orderIntentId, ("ownership.spy_requires_separate_paper_account",), "Opposing same-symbol SPY entry requires separate paper accounts by explicit conflict policy.", order.createdAt)
        self.orders[order.orderIntentId] = order
        code = "ownership.spy_conflicting_entry_simulated_separately" if conflict else "ownership.order_intent_registered"
        return _decision("ACCEPTED", True, order.algorithmId, order.capitalPartitionId, order.orderIntentId, (code,), "Order intent is attributed to its algorithm owner.", order.createdAt)

    def open_position_from_order(self, order_intent_id: str, *, quantity: int, fill_price: float, opened_at: datetime) -> OwnedPosition:
        order = self.orders[order_intent_id]
        position_id = _position_id(order)
        position = OwnedPosition(
            algorithmId=order.algorithmId,
            capitalPartitionId=order.capitalPartitionId,
            decisionId=order.decisionId,
            orderIntentId=order.orderIntentId,
            riskReservationId=order.riskReservationId,
            positionOwner=order.positionOwner,
            parentOrderId=order.parentOrderId,
            exitOwner=order.exitOwner,
            symbol=order.symbol,
            side=order.side,
            quantity=quantity,
            averageEntryPrice=fill_price,
            openedAt=opened_at,
            sessionDate=order.sessionDate,
        )
        self.positions[position_id] = position
        return position

    def close_owned_position(
        self,
        *,
        algorithm_id: AlgorithmId,
        position_id: str,
        quantity: int,
        exit_price: float,
        order_intent_id: str,
        decision_id: str,
        risk_reservation_id: str,
        closed_at: datetime,
    ) -> OwnershipDecision:
        position = self.positions.get(position_id)
        if position is None:
            return _decision("REJECTED", False, algorithm_id, f"{algorithm_id}.unknown", order_intent_id, ("ownership.position_missing",), "Position does not exist in the virtual sub-ledger.", closed_at)
        if position.positionOwner != algorithm_id or position.exitOwner != algorithm_id:
            return _decision("REJECTED", False, algorithm_id, position.capitalPartitionId, order_intent_id, ("ownership.cross_algorithm_close_rejected",), "Algorithm cannot close shares owned by another algorithm.", closed_at)
        if quantity > position.quantity:
            return _decision("REJECTED", False, algorithm_id, position.capitalPartitionId, order_intent_id, ("ownership.close_quantity_exceeds_owned_quantity",), "Close quantity exceeds the algorithm-owned virtual position.", closed_at)
        pnl = _realized_pnl(position.side, position.averageEntryPrice, exit_price, quantity)
        trade = OwnedTradeRecord(
            algorithmId=algorithm_id,
            capitalPartitionId=position.capitalPartitionId,
            decisionId=decision_id,
            orderIntentId=order_intent_id,
            riskReservationId=risk_reservation_id,
            positionOwner=position.positionOwner,
            parentOrderId=position.parentOrderId,
            exitOwner=position.exitOwner,
            tradeId=f"trade-{order_intent_id}",
            symbol=position.symbol,
            side=position.side,
            quantity=quantity,
            price=exit_price,
            realizedPnl=pnl,
            tradedAt=closed_at,
            sessionDate=position.sessionDate,
        )
        self.trades[trade.tradeId] = trade
        remaining = position.quantity - quantity
        if remaining == 0:
            del self.positions[position_id]
        else:
            self.positions[position_id] = position.model_copy(update={"quantity": remaining})
        partition = self.partitions[position.capitalPartitionId]
        self.partitions[position.capitalPartitionId] = partition.model_copy(
            update={"realizedPnl": partition.realizedPnl + pnl, "tradesToday": partition.tradesToday + 1}
        )
        return _decision("RECORDED", True, algorithm_id, position.capitalPartitionId, order_intent_id, ("ownership.owned_position_closed",), "Owned quantity was closed and P/L remains algorithm-attributed.", closed_at)

    def subledger_for(self, algorithm_id: AlgorithmId, capital_partition_id: str) -> VirtualSubLedgerSnapshot:
        positions = {key: position for key, position in self.positions.items() if position.algorithmId == algorithm_id and position.capitalPartitionId == capital_partition_id}
        trades = {key: trade for key, trade in self.trades.items() if trade.algorithmId == algorithm_id and trade.capitalPartitionId == capital_partition_id}
        reserved_risk = sum(reservation.reservedRiskDollars for reservation in self.reservations.values() if reservation.algorithmId == algorithm_id and reservation.capitalPartitionId == capital_partition_id)
        by_symbol: dict[str, int] = {}
        for position in positions.values():
            direction = 1 if position.side == Signal.BUY.value else -1
            by_symbol[position.symbol.upper()] = by_symbol.get(position.symbol.upper(), 0) + (position.quantity * direction)
        return VirtualSubLedgerSnapshot(
            algorithmId=algorithm_id,
            capitalPartitionId=capital_partition_id,
            realizedPnl=sum(trade.realizedPnl for trade in trades.values()),
            reservedRiskDollars=reserved_risk,
            tradeCount=len(trades),
            openQuantityBySymbol=by_symbol,
            positionIds=tuple(sorted(positions)),
            tradeIds=tuple(sorted(trades)),
        )

    def global_exposure(self) -> dict[str, Any]:
        gross_quantity = sum(position.quantity for position in self.positions.values())
        net_by_symbol: dict[str, int] = {}
        for position in self.positions.values():
            direction = 1 if position.side == Signal.BUY.value else -1
            net_by_symbol[position.symbol.upper()] = net_by_symbol.get(position.symbol.upper(), 0) + (direction * position.quantity)
        return {
            "grossQuantity": gross_quantity,
            "netQuantityBySymbol": net_by_symbol,
            "algorithmPositionCounts": {
                algorithm_id: sum(1 for position in self.positions.values() if position.algorithmId == algorithm_id)
                for algorithm_id in sorted({position.algorithmId for position in self.positions.values()})
            },
        }

    def _partition_for(self, algorithm_id: AlgorithmId, capital_partition_id: str) -> CapitalPartition:
        partition = self.partitions.get(capital_partition_id)
        if partition is None:
            raise ValueError(f"capital partition is not registered: {capital_partition_id}")
        if partition.algorithmId != algorithm_id:
            raise ValueError("capital partition owner does not match algorithmId")
        return partition

    def _same_symbol_conflict(self, order: OwnedOrderIntent) -> bool:
        if order.intent != "new_entry" or order.symbol.upper() != "SPY":
            return False
        opposite = Signal.SELL if order.side == Signal.BUY.value else Signal.BUY
        return any(
            position.symbol.upper() == "SPY" and position.side == opposite.value
            for position in self.positions.values()
        ) or any(
            pending.symbol.upper() == "SPY" and pending.side == opposite.value
            for pending in self.orders.values()
            if pending.intent == "new_entry"
        )


def default_capital_partition(algorithm_id: AlgorithmId, *, session_date: date, max_capital_dollars: float) -> CapitalPartition:
    return CapitalPartition(
        algorithmId=algorithm_id,
        capitalPartitionId=f"{algorithm_id}.paper.default",
        maxCapitalDollars=max_capital_dollars,
        sessionDate=session_date,
    )


def _validate_ownership_fields(algorithm_id: AlgorithmId, capital_partition_id: str, position_owner: AlgorithmId, exit_owner: AlgorithmId) -> None:
    if position_owner != algorithm_id:
        raise ValueError("positionOwner must match algorithmId")
    if exit_owner != algorithm_id:
        raise ValueError("exitOwner must match algorithmId")
    if not capital_partition_id.startswith(f"{algorithm_id}."):
        raise ValueError("capitalPartitionId must be namespaced by algorithmId")


def _realized_pnl(side: Signal | str, entry: float, exit_price: float, quantity: int) -> float:
    if side == Signal.BUY.value:
        return round((exit_price - entry) * quantity, 6)
    return round((entry - exit_price) * quantity, 6)


def _position_id(order: OwnedOrderIntent) -> str:
    payload = {
        "algorithmId": order.algorithmId,
        "capitalPartitionId": order.capitalPartitionId,
        "orderIntentId": order.orderIntentId,
        "symbol": order.symbol.upper(),
        "side": order.side.value if isinstance(order.side, Signal) else order.side,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    return f"position-{digest}"


def _decision(
    action: OwnershipAction,
    accepted: bool,
    algorithm_id: AlgorithmId,
    capital_partition_id: str,
    order_intent_id: str,
    reason_codes: tuple[str, ...],
    explanation: str,
    evaluated_at: datetime,
) -> OwnershipDecision:
    payload = {
        "version": ALGORITHM_OWNERSHIP_LEDGER_VERSION,
        "action": action,
        "accepted": accepted,
        "algorithmId": algorithm_id,
        "capitalPartitionId": capital_partition_id,
        "orderIntentId": order_intent_id,
        "reasonCodes": reason_codes,
    }
    digest = hashlib.sha256(json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    return OwnershipDecision(
        action=action,
        accepted=accepted,
        algorithmId=algorithm_id,
        capitalPartitionId=capital_partition_id,
        orderIntentId=order_intent_id,
        reasonCodes=reason_codes,
        explanation=explanation,
        evaluatedAt=evaluated_at,
        configurationHash=digest,
    )


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Direction):
        return int(value)
    if isinstance(value, Enum):
        return value.value
    return value
