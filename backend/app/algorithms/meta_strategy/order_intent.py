"""Meta-Strategy order-intent construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.app.algorithms.meta_strategy.contracts import MetaOrderIntent, MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.versions import (
    META_STRATEGY_ALGORITHM_VERSION,
    META_STRATEGY_CONFIGURATION_VERSION,
    META_STRATEGY_STRATEGY_CATALOG_VERSION,
)


OrderIntentStatus = Literal["CREATED", "NO_ORDER"]


@dataclass(frozen=True)
class MetaStrategyOrderIntentResult:
    status: OrderIntentStatus
    intent: MetaOrderIntent | None
    reason_codes: tuple[str, ...]


def build_meta_strategy_order_intent(
    *,
    snapshot: MetaStrategyMarketSnapshot,
    side: str,
    quantity: int,
    stop_price: float | None,
    limit_price: float | None = None,
    time_in_force: str = "DAY",
) -> MetaStrategyOrderIntentResult:
    normalized_side = str(side).upper()
    if quantity <= 0 or normalized_side not in {"BUY", "SELL"}:
        return MetaStrategyOrderIntentResult(
            status="NO_ORDER",
            intent=None,
            reason_codes=("meta_strategy.order_intent.no_order",),
        )
    intent = MetaOrderIntent(
        algorithm_id="meta_strategy",
        algorithm_version=META_STRATEGY_ALGORITHM_VERSION,
        configuration_version=META_STRATEGY_CONFIGURATION_VERSION,
        strategy_catalog_version=META_STRATEGY_STRATEGY_CATALOG_VERSION,
        decision_id=snapshot.decision_id,
        snapshot_id=snapshot.snapshot_id,
        timestamp=snapshot.timestamp,
        order_intent_id=f"meta_strategy.order_intent.{snapshot.decision_id}",
        symbol=snapshot.symbol,
        side=normalized_side,
        quantity=float(quantity),
        order_type="LIMIT" if limit_price is not None else "MARKET",
        limit_price=limit_price,
        stop_price=stop_price,
        time_in_force=time_in_force,
    )
    return MetaStrategyOrderIntentResult(
        status="CREATED",
        intent=intent,
        reason_codes=("meta_strategy.order_intent.created",),
    )


__all__ = [
    "MetaStrategyOrderIntentResult",
    "OrderIntentStatus",
    "build_meta_strategy_order_intent",
]
