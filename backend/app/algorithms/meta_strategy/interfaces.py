"""Approved shared-service interfaces for Meta-Strategy.

The Meta-Strategy package may depend on these protocols, not on another
algorithm's private repositories, settings, strategies, or mutable state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from backend.app.algorithms.meta_strategy.identity import META_STRATEGY_ALLOWED_SHARED_SERVICES


@dataclass(frozen=True)
class MetaStrategySharedInterfaceContract:
    service_id: str
    access: str
    authority: str


class MetaStrategyMarketDataReader(Protocol):
    def read_market_snapshot(self, *, symbol: str, at: datetime) -> dict[str, Any]:
        ...


class MetaStrategyAccountDataReader(Protocol):
    def read_account_snapshot(self, *, at: datetime) -> dict[str, Any]:
        ...


class MetaStrategyGlobalRiskClient(Protocol):
    def evaluate_order_intent(self, order_intent: Any, *, correlation_id: str) -> dict[str, Any]:
        ...


class MetaStrategyBrokerGateway(Protocol):
    def submit_paper_order(self, order_intent: Any, *, idempotency_key: str) -> dict[str, Any]:
        ...


class MetaStrategyLogger(Protocol):
    def info(self, message: str, *, extra: dict[str, Any] | None = None) -> None:
        ...


class MetaStrategyMetrics(Protocol):
    def increment(self, metric_name: str, *, tags: dict[str, str] | None = None) -> None:
        ...


class MetaStrategyClock(Protocol):
    def now(self) -> datetime:
        ...


class MetaStrategyMarketCalendar(Protocol):
    def is_market_open(self, *, at: datetime) -> bool:
        ...

    def next_session(self, *, after: datetime) -> date:
        ...


META_STRATEGY_APPROVED_SHARED_INTERFACES: tuple[MetaStrategySharedInterfaceContract, ...] = (
    MetaStrategySharedInterfaceContract("market_data_reader", "read_only", "shared_market_data"),
    MetaStrategySharedInterfaceContract("account_data_reader", "read_only", "shared_account_data"),
    MetaStrategySharedInterfaceContract("global_risk_client", "reject_or_reduce_only", "shared_global_risk"),
    MetaStrategySharedInterfaceContract("broker_gateway", "paper_and_shadow_transport", "shared_broker_connectivity"),
    MetaStrategySharedInterfaceContract("logger", "append_only", "shared_logging"),
    MetaStrategySharedInterfaceContract("metrics", "append_only", "shared_metrics"),
    MetaStrategySharedInterfaceContract("clock", "read_only", "shared_time"),
    MetaStrategySharedInterfaceContract("market_calendar", "read_only", "shared_market_calendar"),
)
META_STRATEGY_APPROVED_SHARED_INTERFACE_IDS = tuple(contract.service_id for contract in META_STRATEGY_APPROVED_SHARED_INTERFACES)

if META_STRATEGY_APPROVED_SHARED_INTERFACE_IDS != META_STRATEGY_ALLOWED_SHARED_SERVICES:
    raise RuntimeError("Meta-Strategy shared-interface contracts must match the identity boundary.")


__all__ = [
    "META_STRATEGY_APPROVED_SHARED_INTERFACE_IDS",
    "META_STRATEGY_APPROVED_SHARED_INTERFACES",
    "MetaStrategyAccountDataReader",
    "MetaStrategyBrokerGateway",
    "MetaStrategyClock",
    "MetaStrategyGlobalRiskClient",
    "MetaStrategyLogger",
    "MetaStrategyMarketCalendar",
    "MetaStrategyMarketDataReader",
    "MetaStrategyMetrics",
    "MetaStrategySharedInterfaceContract",
]
