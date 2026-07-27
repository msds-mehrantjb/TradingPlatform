"""Immutable Meta-Strategy evaluation context."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.algorithms.meta_strategy.configuration import MetaStrategyBaselineSettings, meta_strategy_baseline_settings
from backend.app.algorithms.meta_strategy.contracts import MetaStrategyContractModel, MetaStrategyMarketSnapshot


class MetaStrategyInventorySnapshot(MetaStrategyContractModel):
    positions: tuple[dict[str, Any], ...] = ()
    reserved_risk_dollars: float = Field(default=0.0, ge=0)
    remaining_risk_dollars: float = Field(default=1_000.0, ge=0)
    daily_trade_count: int = Field(default=0, ge=0)
    daily_trade_limit: int = Field(default=5, ge=0)
    realized_daily_pnl: float = 0.0
    daily_loss_limit: float = -1_000.0
    duplicate_order_blocked: bool = False
    existing_position_policy_allows_entry: bool = True


class MetaStrategyAccountSnapshot(MetaStrategyContractModel):
    buying_power: float = Field(ge=0)
    cash_available: float = Field(ge=0)
    account_equity: float = Field(gt=0)
    captured_at: datetime
    read_only: bool = True

    @field_validator("captured_at")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        return value


class MetaStrategyGlobalRiskSnapshot(MetaStrategyContractModel):
    available_risk_dollars: float = Field(ge=0)
    max_quantity: int = Field(ge=0)
    trading_permission: bool = True
    captured_at: datetime
    read_only: bool = True

    @field_validator("captured_at")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        return value


class MetaStrategyOperationalHealthSnapshot(MetaStrategyContractModel):
    status: Literal["OK", "DEGRADED", "BLOCKED"]
    broker_connected: bool
    data_connected: bool
    trading_allowed: bool
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        return value


class MetaStrategyEconomicEventSnapshot(MetaStrategyContractModel):
    state: str = "none"
    severity: str = "none"
    minutes_to_event: int | None = Field(default=None, ge=0)
    active: bool = False
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        return value


class MetaStrategyEvaluationContext(MetaStrategyContractModel):
    market_snapshot: MetaStrategyMarketSnapshot
    algorithm_inventory_snapshot: MetaStrategyInventorySnapshot = Field(default_factory=MetaStrategyInventorySnapshot)
    account_snapshot: MetaStrategyAccountSnapshot
    global_risk_snapshot: MetaStrategyGlobalRiskSnapshot
    operational_health_snapshot: MetaStrategyOperationalHealthSnapshot
    economic_event_snapshot: MetaStrategyEconomicEventSnapshot
    active_settings: MetaStrategyBaselineSettings = Field(default_factory=meta_strategy_baseline_settings)
    execution_mode: Literal["SHADOW", "PAPER", "BACKTEST", "DAILY_REPLAY", "DIAGNOSTICS"]
    evaluation_timestamp: datetime

    @field_validator("evaluation_timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evaluation_timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def timestamps_must_be_point_in_time(self) -> MetaStrategyEvaluationContext:
        if self.market_snapshot.timestamp > self.evaluation_timestamp:
            raise ValueError("market snapshot cannot be after evaluation timestamp")
        for captured_at in (
            self.account_snapshot.captured_at,
            self.global_risk_snapshot.captured_at,
            self.operational_health_snapshot.captured_at,
            self.economic_event_snapshot.captured_at,
        ):
            if captured_at > self.evaluation_timestamp:
                raise ValueError("context snapshots cannot be after evaluation timestamp")
        return self


def context_market_snapshot(value: MetaStrategyMarketSnapshot | MetaStrategyEvaluationContext) -> MetaStrategyMarketSnapshot:
    return value.market_snapshot if isinstance(value, MetaStrategyEvaluationContext) else value


def local_diagnostic_context(snapshot: MetaStrategyMarketSnapshot) -> MetaStrategyEvaluationContext:
    timestamp = snapshot.timestamp
    return MetaStrategyEvaluationContext(
        market_snapshot=snapshot,
        account_snapshot=MetaStrategyAccountSnapshot(
            buying_power=100_000.0,
            cash_available=100_000.0,
            account_equity=100_000.0,
            captured_at=timestamp,
        ),
        global_risk_snapshot=MetaStrategyGlobalRiskSnapshot(
            available_risk_dollars=1_000.0,
            max_quantity=10_000,
            trading_permission=True,
            captured_at=timestamp,
        ),
        operational_health_snapshot=MetaStrategyOperationalHealthSnapshot(
            status="OK",
            broker_connected=True,
            data_connected=True,
            trading_allowed=True,
            captured_at=timestamp,
        ),
        economic_event_snapshot=MetaStrategyEconomicEventSnapshot(
            state=str(snapshot.economic_event_state.get("state") or "none"),
            severity=str(snapshot.economic_event_state.get("severity") or "none"),
            minutes_to_event=snapshot.economic_event_state.get("minutesToEvent"),
            active=bool(snapshot.economic_event_state.get("active") or False),
            captured_at=timestamp,
        ),
        execution_mode="DIAGNOSTICS",
        evaluation_timestamp=timestamp,
    )


__all__ = [
    "MetaStrategyAccountSnapshot",
    "MetaStrategyEconomicEventSnapshot",
    "MetaStrategyEvaluationContext",
    "MetaStrategyGlobalRiskSnapshot",
    "MetaStrategyInventorySnapshot",
    "MetaStrategyOperationalHealthSnapshot",
    "context_market_snapshot",
    "local_diagnostic_context",
]
