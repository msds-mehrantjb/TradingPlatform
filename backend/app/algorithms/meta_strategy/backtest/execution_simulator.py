"""Simulation replacements for broker transport and account snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.algorithms.meta_strategy.contracts import MetaOrderIntent
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.idempotency import MetaStrategyIdempotencyStore, meta_strategy_idempotency_key


@dataclass(frozen=True)
class MetaStrategySimulationConfig:
    spread_bps: float = 1.0
    slippage_bps: float = 2.0
    fee_per_share: float = 0.005
    regulatory_fee_per_share: float = 0.0
    order_delay_seconds: float = 1.0
    limit_order_fill_probability: float = 1.0
    partial_fill_ratio: float = 1.0

    def __post_init__(self) -> None:
        if self.spread_bps < 0 or self.slippage_bps < 0 or self.fee_per_share < 0 or self.regulatory_fee_per_share < 0 or self.order_delay_seconds < 0:
            raise ValueError("spread, slippage, fees, and delay must be non-negative")
        if not 0.0 <= self.limit_order_fill_probability <= 1.0:
            raise ValueError("limit_order_fill_probability must be between 0 and 1")
        if not 0.0 <= self.partial_fill_ratio <= 1.0:
            raise ValueError("partial_fill_ratio must be between 0 and 1")


@dataclass(frozen=True)
class MetaStrategySimulatedAccountSnapshot:
    algorithm_id: str = ALGORITHM_ID
    account_equity: float = 100_000.0
    buying_power: float = 100_000.0
    remaining_algorithm_risk: float = 1_000.0
    global_available_risk: float = 1_000.0
    global_quantity_cap: int = 10_000


class MetaStrategySimulatedBrokerAdapter:
    def __init__(self, config: MetaStrategySimulationConfig | None = None, *, idempotency_store: MetaStrategyIdempotencyStore | None = None) -> None:
        self.config = config or MetaStrategySimulationConfig()
        self.idempotency_store = idempotency_store or MetaStrategyIdempotencyStore()
        self.submissions: list[dict[str, Any]] = []

    def submit(self, order_intent: MetaOrderIntent | None, *, mode: str) -> dict[str, Any]:
        if order_intent is None:
            return {
                "algorithmId": ALGORITHM_ID,
                "algorithm_id": ALGORITHM_ID,
                "status": "NO_ORDER",
                "submitted": False,
                "filledQuantity": 0,
                "reasonCodes": ("meta_strategy.backtest.no_order",),
            }
        if mode != "BACKTEST":
            return {
                "algorithmId": ALGORITHM_ID,
                "algorithm_id": ALGORITHM_ID,
                "status": "SIMULATOR_MODE_MISMATCH",
                "submitted": False,
                "filledQuantity": 0,
                "reasonCodes": ("meta_strategy.backtest.simulator_requires_backtest_mode",),
            }
        key = meta_strategy_idempotency_key(order_intent)
        claimed, record = self.idempotency_store.claim(idempotency_key=key, order_intent_id=order_intent.order_intent_id)
        if not claimed:
            return {
                "algorithmId": ALGORITHM_ID,
                "algorithm_id": ALGORITHM_ID,
                "status": "DUPLICATE_SUPPRESSED",
                "submitted": False,
                "filledQuantity": 0,
                "idempotencyKey": key,
                "idempotencyRecord": record.as_dict(),
                "reasonCodes": ("meta_strategy.backtest.duplicate_suppressed",),
            }
        fillable = self.config.limit_order_fill_probability > 0.0
        filled = 0 if not fillable else max(0, min(int(order_intent.quantity), int(int(order_intent.quantity) * self.config.partial_fill_ratio)))
        submitted = self.idempotency_store.mark_submitted(key, broker_order_id=f"meta_strategy.backtest.order.{order_intent.order_intent_id}")
        payload = {
            "algorithmId": ALGORITHM_ID,
            "algorithm_id": ALGORITHM_ID,
            "status": "SIMULATED_PARTIAL_FILL" if 0 < filled < int(order_intent.quantity) else "SIMULATED_FILL" if filled else "SIMULATED_UNFILLED",
            "submitted": True,
            "filledQuantity": filled,
            "requestedQuantity": int(order_intent.quantity),
            "spreadBps": self.config.spread_bps,
            "slippageBps": self.config.slippage_bps,
            "feePerShare": self.config.fee_per_share,
            "regulatoryFeePerShare": self.config.regulatory_fee_per_share,
            "orderDelaySeconds": self.config.order_delay_seconds,
            "limitOrderFillProbability": self.config.limit_order_fill_probability,
            "idempotencyKey": key,
            "idempotencyRecord": submitted.as_dict(),
            "reasonCodes": (
                "meta_strategy.backtest.simulated_execution",
                "meta_strategy.backtest.limit_order_non_fill_modeled" if filled == 0 else "meta_strategy.backtest.fill_modeled",
                "meta_strategy.backtest.order_delay_modeled",
            ),
        }
        self.submissions.append(payload)
        return payload


__all__ = [
    "MetaStrategySimulatedAccountSnapshot",
    "MetaStrategySimulatedBrokerAdapter",
    "MetaStrategySimulationConfig",
]
