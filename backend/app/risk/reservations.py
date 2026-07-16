from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class RiskReservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reservationId: str = Field(min_length=1)
    decisionId: str = Field(min_length=1)
    algorithmId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    quantity: int = Field(ge=0)
    reservedBuyingPower: float = Field(ge=0)
    reservedRiskDollars: float = Field(ge=0)
    status: str
    createdAt: datetime
    brokerOrderId: str | None = None


class InMemoryRiskReservationStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._reservations: dict[str, RiskReservation] = {}

    def reserve(self, *, decision_id: str, algorithm_id: str, symbol: str, quantity: int, buying_power: float, risk_dollars: float) -> RiskReservation:
        with self._lock:
            duplicate = next((reservation for reservation in self._reservations.values() if reservation.decisionId == decision_id and reservation.status == "reserved"), None)
            if duplicate:
                return duplicate
            reservation = RiskReservation(
                reservationId=f"risk-res-{uuid4().hex}",
                decisionId=decision_id,
                algorithmId=algorithm_id,
                symbol=symbol,
                quantity=quantity,
                reservedBuyingPower=buying_power,
                reservedRiskDollars=risk_dollars,
                status="reserved",
                createdAt=datetime.now(UTC),
            )
            self._reservations[reservation.reservationId] = reservation
            return reservation

    def commit(self, reservation_id: str, *, broker_order_id: str | None = None) -> RiskReservation | None:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if not reservation:
                return None
            committed = reservation.model_copy(update={"status": "committed", "brokerOrderId": broker_order_id})
            self._reservations[reservation_id] = committed
            return committed

    def release(self, reservation_id: str) -> RiskReservation | None:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if not reservation:
                return None
            released = reservation.model_copy(update={"status": "released"})
            self._reservations[reservation_id] = released
            return released

    def all(self) -> tuple[RiskReservation, ...]:
        with self._lock:
            return tuple(self._reservations.values())


__all__ = ["InMemoryRiskReservationStore", "RiskReservation"]
