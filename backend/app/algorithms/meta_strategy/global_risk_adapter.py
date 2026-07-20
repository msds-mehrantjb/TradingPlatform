"""Meta-Strategy adapter for shared global-risk controls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from backend.app.algorithms.meta_strategy.contracts import MetaOrderIntent
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID


class MetaStrategyGlobalRiskAdapter(Protocol):
    def apply(self, order_intent: MetaOrderIntent | None, *, requested_quantity: int) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class MetaStrategyGlobalRiskDecision:
    algorithm_id: str
    status: str
    requested_quantity: int
    original_quantity: int
    approved_quantity: int
    reserved_risk_dollars: float
    reason_codes: tuple[str, ...]
    candidate_rewritten: bool = False
    model_probability_changed: bool = False
    settings_changed: bool = False
    protective_exits_removed: bool = False

    def as_pipeline_result(self) -> dict[str, Any]:
        return {
            "algorithm_id": self.algorithm_id,
            "algorithmId": self.algorithm_id,
            "status": self.status,
            "requestedQuantity": self.requested_quantity,
            "originalQuantity": self.original_quantity,
            "approvedQuantity": self.approved_quantity,
            "reservedRiskDollars": self.reserved_risk_dollars,
            "candidateRewritten": self.candidate_rewritten,
            "modelProbabilityChanged": self.model_probability_changed,
            "settingsChanged": self.settings_changed,
            "protectiveExitsRemoved": self.protective_exits_removed,
            "reasonCodes": self.reason_codes,
        }


class ReadOnlyMetaStrategyGlobalRiskAdapter:
    def __init__(
        self,
        *,
        reject: bool = False,
        max_quantity: int | None = None,
        available_risk_dollars: float | None = None,
        stop_distance: float | None = None,
    ) -> None:
        self.reject = reject
        self.max_quantity = max_quantity
        self.available_risk_dollars = available_risk_dollars
        self.stop_distance = stop_distance

    def apply(self, order_intent: MetaOrderIntent | None, *, requested_quantity: int) -> dict[str, Any]:
        requested = max(0, int(requested_quantity))
        original = 0 if order_intent is None else max(0, int(order_intent.quantity))
        if order_intent is None:
            return MetaStrategyGlobalRiskDecision(
                algorithm_id=ALGORITHM_ID,
                status="NO_ORDER",
                requested_quantity=requested,
                original_quantity=0,
                approved_quantity=0,
                reserved_risk_dollars=0.0,
                reason_codes=("meta_strategy.global_risk.no_order",),
            ).as_pipeline_result()
        if self.reject:
            return MetaStrategyGlobalRiskDecision(
                algorithm_id=ALGORITHM_ID,
                status="REJECTED",
                requested_quantity=requested,
                original_quantity=original,
                approved_quantity=0,
                reserved_risk_dollars=0.0,
                reason_codes=("meta_strategy.global_risk.rejected",),
            ).as_pipeline_result()

        caps = [requested, original]
        if self.max_quantity is not None:
            caps.append(max(0, int(self.max_quantity)))
        if self.available_risk_dollars is not None and self.stop_distance is not None and self.stop_distance > 0:
            caps.append(max(0, int(float(self.available_risk_dollars) // float(self.stop_distance))))
        approved = min(caps)
        stop_distance = max(0.0, float(self.stop_distance or 0.0))
        reserved = approved * stop_distance
        return MetaStrategyGlobalRiskDecision(
            algorithm_id=ALGORITHM_ID,
            status="PASS" if approved > 0 else "REDUCED_TO_ZERO",
            requested_quantity=requested,
            original_quantity=original,
            approved_quantity=approved,
            reserved_risk_dollars=reserved,
            reason_codes=("meta_strategy.global_risk.quantity_capped",),
        ).as_pipeline_result()


__all__ = [
    "MetaStrategyGlobalRiskAdapter",
    "MetaStrategyGlobalRiskDecision",
    "ReadOnlyMetaStrategyGlobalRiskAdapter",
]
