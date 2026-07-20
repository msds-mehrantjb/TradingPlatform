"""Meta-Strategy broker transport adapter with idempotency."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from backend.app.algorithms.meta_strategy.contracts import MetaOrderIntent
from backend.app.algorithms.meta_strategy.idempotency import (
    MetaStrategyIdempotencyRecord,
    MetaStrategyIdempotencyStore,
    meta_strategy_idempotency_key,
)
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID


class MetaStrategyBrokerTransport(Protocol):
    def submit(self, order_intent: MetaOrderIntent, *, idempotency_key: str, mode: str) -> dict[str, Any]:
        ...


class MetaStrategyBrokerAdapter(Protocol):
    def submit(self, order_intent: MetaOrderIntent | None, *, mode: str) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class MetaStrategyBrokerSubmissionResult:
    algorithm_id: str
    status: str
    submitted: bool
    filled_quantity: int
    idempotency_key: str | None
    broker_order_id: str | None
    reason_codes: tuple[str, ...]
    idempotency_record: MetaStrategyIdempotencyRecord | None = None

    def as_pipeline_result(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "algorithm_id": self.algorithm_id,
            "algorithmId": self.algorithm_id,
            "status": self.status,
            "submitted": self.submitted,
            "filledQuantity": self.filled_quantity,
            "idempotencyKey": self.idempotency_key,
            "brokerOrderId": self.broker_order_id,
            "reasonCodes": self.reason_codes,
        }
        if self.idempotency_record is not None:
            payload["idempotencyRecord"] = self.idempotency_record.as_dict()
        return payload


class NoopMetaStrategyBrokerAdapter:
    def __init__(
        self,
        *,
        idempotency_store: MetaStrategyIdempotencyStore | None = None,
        transport: MetaStrategyBrokerTransport | None = None,
        live_enabled: bool = False,
    ) -> None:
        self.idempotency_store = idempotency_store or MetaStrategyIdempotencyStore()
        self.transport = transport
        self.live_enabled = live_enabled

    def submit(self, order_intent: MetaOrderIntent | None, *, mode: str) -> dict[str, Any]:
        if not hasattr(self, "idempotency_store"):
            self.idempotency_store = MetaStrategyIdempotencyStore()
        if not hasattr(self, "transport"):
            self.transport = None
        if not hasattr(self, "live_enabled"):
            self.live_enabled = False
        if order_intent is None:
            return MetaStrategyBrokerSubmissionResult(
                algorithm_id=ALGORITHM_ID,
                status="NO_ORDER",
                submitted=False,
                filled_quantity=0,
                idempotency_key=None,
                broker_order_id=None,
                reason_codes=("meta_strategy.pipeline.no_order_intent",),
            ).as_pipeline_result()
        if mode == "LIVE" and not self.live_enabled:
            return MetaStrategyBrokerSubmissionResult(
                algorithm_id=ALGORITHM_ID,
                status="LIVE_DISABLED",
                submitted=False,
                filled_quantity=0,
                idempotency_key=None,
                broker_order_id=None,
                reason_codes=("meta_strategy.pipeline.live_requires_separate_enablement",),
            ).as_pipeline_result()

        key = meta_strategy_idempotency_key(order_intent)
        claimed, record = self.idempotency_store.claim(idempotency_key=key, order_intent_id=order_intent.order_intent_id)
        if not claimed:
            return MetaStrategyBrokerSubmissionResult(
                algorithm_id=ALGORITHM_ID,
                status="DUPLICATE_SUPPRESSED",
                submitted=False,
                filled_quantity=0,
                idempotency_key=key,
                broker_order_id=record.broker_order_id,
                reason_codes=("meta_strategy.broker.duplicate_submission_suppressed",),
                idempotency_record=record,
            ).as_pipeline_result()

        if mode not in {"PAPER", "LIVE"}:
            completed = self.idempotency_store.mark_completed(key)
            return MetaStrategyBrokerSubmissionResult(
                algorithm_id=ALGORITHM_ID,
                status="SIMULATED",
                submitted=False,
                filled_quantity=0,
                idempotency_key=key,
                broker_order_id=None,
                reason_codes=(f"meta_strategy.pipeline.mode_{mode.lower()}_no_broker_submit",),
                idempotency_record=completed,
            ).as_pipeline_result()

        try:
            if self.transport is not None:
                response = self.transport.submit(order_intent, idempotency_key=key, mode=mode)
                broker_order_id = str(response.get("brokerOrderId") or response.get("orderId") or f"meta_strategy.broker.{order_intent.order_intent_id}")
                filled = max(0, int(response.get("filledQuantity") or 0))
            else:
                broker_order_id = f"meta_strategy.paper.{order_intent.order_intent_id}"
                filled = int(order_intent.quantity) if mode == "PAPER" else 0
            submitted_record = self.idempotency_store.mark_submitted(key, broker_order_id=broker_order_id)
        except TimeoutError:
            timeout_record = self.idempotency_store.mark_timeout(key)
            return MetaStrategyBrokerSubmissionResult(
                algorithm_id=ALGORITHM_ID,
                status="TIMEOUT",
                submitted=False,
                filled_quantity=0,
                idempotency_key=key,
                broker_order_id=None,
                reason_codes=("meta_strategy.broker.timeout_recorded_without_retry",),
                idempotency_record=timeout_record,
            ).as_pipeline_result()

        return MetaStrategyBrokerSubmissionResult(
            algorithm_id=ALGORITHM_ID,
            status="PAPER_ACCEPTED" if mode == "PAPER" else "LIVE_SUBMITTED",
            submitted=True,
            filled_quantity=filled,
            idempotency_key=key,
            broker_order_id=broker_order_id,
            reason_codes=("meta_strategy.pipeline.paper_order_submitted",) if mode == "PAPER" else ("meta_strategy.pipeline.live_order_submitted",),
            idempotency_record=submitted_record,
        ).as_pipeline_result()


__all__ = [
    "MetaStrategyBrokerAdapter",
    "MetaStrategyBrokerSubmissionResult",
    "MetaStrategyBrokerTransport",
    "NoopMetaStrategyBrokerAdapter",
]
