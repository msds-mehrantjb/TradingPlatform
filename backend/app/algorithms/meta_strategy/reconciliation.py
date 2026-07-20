"""Meta-Strategy broker reconciliation records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.trade_management import MetaStrategyFillReconciliation, reconcile_meta_strategy_fill


@dataclass(frozen=True)
class MetaStrategyReconciliationRecord:
    algorithm_id: str
    status: str
    planned_quantity: int
    filled_quantity: int
    protective_order_quantity: int
    fill_reconciliation: MetaStrategyFillReconciliation
    reason_codes: tuple[str, ...]

    def as_pipeline_result(self) -> dict[str, object]:
        return {
            "algorithm_id": self.algorithm_id,
            "algorithmId": self.algorithm_id,
            "status": self.status,
            "plannedQuantity": self.planned_quantity,
            "filledQuantity": self.filled_quantity,
            "protectiveOrderQuantity": self.protective_order_quantity,
            "reasonCodes": self.reason_codes,
        }


def reconcile_meta_strategy_broker_fill(
    *,
    planned_quantity: int,
    filled_quantity: int,
    position_id: str,
    symbol: str,
    side: Literal["BUY", "SELL"],
    average_fill_price: float,
    filled_at: datetime,
    protective_stop: float,
    profit_target: float,
    maximum_holding_minutes: int,
) -> MetaStrategyReconciliationRecord:
    fill = reconcile_meta_strategy_fill(
        planned_quantity=planned_quantity,
        filled_quantity=filled_quantity,
        position_id=position_id,
        symbol=symbol,
        side=side,
        average_fill_price=average_fill_price,
        filled_at=filled_at,
        protective_stop=protective_stop,
        profit_target=profit_target,
        maximum_holding_minutes=maximum_holding_minutes,
    )
    return MetaStrategyReconciliationRecord(
        algorithm_id=ALGORITHM_ID,
        status=fill.status,
        planned_quantity=fill.planned_quantity,
        filled_quantity=fill.filled_quantity,
        protective_order_quantity=fill.protective_order_quantity,
        fill_reconciliation=fill,
        reason_codes=tuple(dict.fromkeys(("meta_strategy.reconciliation.recorded", *fill.reason_codes))),
    )


__all__ = [
    "MetaStrategyReconciliationRecord",
    "reconcile_meta_strategy_broker_fill",
]
