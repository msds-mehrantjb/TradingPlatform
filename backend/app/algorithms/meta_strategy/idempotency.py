"""Meta-Strategy order idempotency controls."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import RLock

from backend.app.algorithms.meta_strategy.contracts import MetaOrderIntent
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID


@dataclass(frozen=True)
class MetaStrategyIdempotencyRecord:
    algorithm_id: str
    idempotency_key: str
    order_intent_id: str
    status: str
    broker_order_id: str | None
    created_at: datetime
    updated_at: datetime
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "algorithm_id": self.algorithm_id,
            "algorithmId": self.algorithm_id,
            "idempotencyKey": self.idempotency_key,
            "orderIntentId": self.order_intent_id,
            "status": self.status,
            "brokerOrderId": self.broker_order_id,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
            "reasonCodes": self.reason_codes,
        }


def meta_strategy_idempotency_key(order_intent: MetaOrderIntent) -> str:
    return ":".join(
        (
            ALGORITHM_ID,
            order_intent.order_intent_id,
            order_intent.symbol.upper(),
            order_intent.side,
            str(int(order_intent.quantity)),
        )
    )


class MetaStrategyIdempotencyStore:
    def __init__(self) -> None:
        self._records: dict[str, MetaStrategyIdempotencyRecord] = {}
        self._lock = RLock()

    def claim(
        self,
        *,
        idempotency_key: str,
        order_intent_id: str,
        created_at: datetime | None = None,
    ) -> tuple[bool, MetaStrategyIdempotencyRecord]:
        timestamp = created_at or datetime.now(tz=UTC)
        with self._lock:
            existing = self._records.get(idempotency_key)
            if existing is not None:
                return False, existing
            record = MetaStrategyIdempotencyRecord(
                algorithm_id=ALGORITHM_ID,
                idempotency_key=idempotency_key,
                order_intent_id=order_intent_id,
                status="CLAIMED",
                broker_order_id=None,
                created_at=timestamp,
                updated_at=timestamp,
                reason_codes=("meta_strategy.idempotency.claimed",),
            )
            self._records[idempotency_key] = record
            return True, record

    def mark_submitted(self, idempotency_key: str, *, broker_order_id: str | None = None) -> MetaStrategyIdempotencyRecord:
        return self._replace(
            idempotency_key,
            status="SUBMITTED",
            broker_order_id=broker_order_id,
            reason_codes=("meta_strategy.idempotency.submitted",),
        )

    def mark_timeout(self, idempotency_key: str) -> MetaStrategyIdempotencyRecord:
        return self._replace(
            idempotency_key,
            status="TIMEOUT",
            reason_codes=("meta_strategy.idempotency.timeout_recorded",),
        )

    def mark_completed(self, idempotency_key: str, *, broker_order_id: str | None = None) -> MetaStrategyIdempotencyRecord:
        return self._replace(
            idempotency_key,
            status="COMPLETED",
            broker_order_id=broker_order_id,
            reason_codes=("meta_strategy.idempotency.completed",),
        )

    def get(self, idempotency_key: str) -> MetaStrategyIdempotencyRecord | None:
        with self._lock:
            return self._records.get(idempotency_key)

    def _replace(
        self,
        idempotency_key: str,
        *,
        status: str,
        broker_order_id: str | None = None,
        reason_codes: tuple[str, ...],
    ) -> MetaStrategyIdempotencyRecord:
        with self._lock:
            record = self._records[idempotency_key]
            updated = replace(
                record,
                status=status,
                broker_order_id=broker_order_id if broker_order_id is not None else record.broker_order_id,
                updated_at=datetime.now(tz=UTC),
                reason_codes=reason_codes,
            )
            self._records[idempotency_key] = updated
            return updated


__all__ = [
    "MetaStrategyIdempotencyRecord",
    "MetaStrategyIdempotencyStore",
    "meta_strategy_idempotency_key",
]
