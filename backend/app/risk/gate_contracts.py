"""Neutral global gate contracts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.execution.idempotency import idempotency_key


class GlobalGateDecision(str, Enum):
    ALLOW = "ALLOW"
    REDUCE_QUANTITY = "REDUCE_QUANTITY"
    REJECT_NEW_ENTRY = "REJECT_NEW_ENTRY"
    EXIT_ONLY = "EXIT_ONLY"
    EMERGENCY_LIQUIDATE = "EMERGENCY_LIQUIDATE"


class GlobalGateOrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class GlobalGateProposedOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    account_id: str = Field(min_length=1)
    algorithm_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: GlobalGateOrderSide
    quantity: int = Field(ge=0)
    order_intent_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    decision_timestamp: datetime
    configuration_version: str = Field(min_length=1)
    limit_price: float = Field(gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    planned_risk: float = Field(default=0, ge=0)
    is_position_increase: bool = False
    is_risk_reducing_exit: bool = False

    @property
    def order_value(self) -> float:
        return self.quantity * self.limit_price

    @property
    def signed_value(self) -> float:
        return self.order_value if self.side == GlobalGateOrderSide.BUY.value else -self.order_value


class GlobalGateAccountState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str = Field(min_length=1)
    account_snapshot_id: str = Field(min_length=1)
    status: str = "ACTIVE"
    broker_connected: bool = True
    broker_market_clock_open: bool = True
    new_entry_cutoff_reached: bool = False
    realized_pl: float = 0
    unrealized_pl: float = 0
    estimated_exit_costs: float = Field(default=0, ge=0)
    equity: float = Field(gt=0)
    high_water_equity: float = Field(gt=0)
    available_buying_power: float = Field(ge=0)
    daily_loss_limit: float = Field(default=0, ge=0)
    drawdown_limit: float = Field(default=0, ge=0)


class GlobalGateMarketState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    market_snapshot_id: str = Field(min_length=1)
    authoritative_broker_market_clock_open: bool = True
    market_data_fresh: bool = True
    market_data_complete: bool = True
    symbol_halted: bool = False
    luld_active: bool = False
    market_wide_circuit_breaker: bool = False
    broker_position_reconciled: bool = True
    broker_open_orders_reconciled: bool = True
    spread: float = Field(ge=0)
    liquidity: float = Field(ge=0)
    estimated_slippage: float = Field(default=0, ge=0)
    high_impact_event_blackout: bool = False


class GlobalGatePositionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    algorithm_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: GlobalGateOrderSide
    quantity: int = Field(ge=0)
    market_value: float = Field(ge=0)
    unrealized_pl: float = 0
    open_stop_risk: float = Field(default=0, ge=0)

    @property
    def signed_market_value(self) -> float:
        return self.market_value if self.side == GlobalGateOrderSide.BUY.value else -self.market_value


class GlobalGatePendingOrderState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    algorithm_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: GlobalGateOrderSide
    quantity: int = Field(ge=0)
    reserved_buying_power: float = Field(default=0, ge=0)
    pending_risk: float = Field(default=0, ge=0)
    order_intent_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


class GlobalGateLedgerState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    positions: tuple[GlobalGatePositionState, ...] = ()
    pending_orders: tuple[GlobalGatePendingOrderState, ...] = ()
    completed_idempotency_keys: tuple[str, ...] = ()


class GlobalGatePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    master_entry_enabled: bool = True
    allow_position_increases: bool = True
    emergency_flatten: bool = False
    max_symbol_exposure: float = Field(default=0, ge=0)
    max_gross_exposure: float = Field(default=0, ge=0)
    max_net_exposure: float = Field(default=0, ge=0)
    max_open_stop_risk: float = Field(default=0, ge=0)
    buying_power_reserve: float = Field(default=0, ge=0)
    max_open_orders: int = Field(default=0, ge=0)
    absolute_spread_ceiling: float = Field(default=0, ge=0)
    absolute_liquidity_floor: float = Field(default=0, ge=0)
    slippage_ceiling: float = Field(default=0, ge=0)
    high_impact_event_blackout_enabled: bool = False


class AccountWideLedgerSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    realized_pl: float = 0
    unrealized_pl: float = 0
    estimated_exit_costs: float = Field(default=0, ge=0)
    gross_exposure: float = Field(ge=0)
    net_exposure: float
    symbol_exposure: dict[str, float]
    open_stop_risk: float = Field(ge=0)
    pending_order_risk: float = Field(ge=0)
    reserved_buying_power: float = Field(ge=0)
    open_order_count: int = Field(ge=0)

    def deterministic_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class GlobalGateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposed_order: GlobalGateProposedOrder
    account_state: GlobalGateAccountState
    market_state: GlobalGateMarketState
    ledger_state: GlobalGateLedgerState = Field(default_factory=GlobalGateLedgerState)
    policy: GlobalGatePolicy = Field(default_factory=GlobalGatePolicy)
    evaluation_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GlobalGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    decision: GlobalGateDecision
    algorithm_id: str
    proposed_quantity: int = Field(ge=0)
    allowed_quantity: int = Field(ge=0)
    max_additional_risk: float = Field(default=0, ge=0)
    reason_codes: tuple[str, ...] = ()
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    allow_new_entries: bool = False
    allow_position_increases: bool = False
    allow_risk_reducing_exits: bool = True
    emergency_flatten: bool = False
    requested_quantity: int = Field(default=0, ge=0)
    approved_quantity: int = Field(default=0, ge=0)
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    account_snapshot_id: str = ""
    market_snapshot_id: str = ""
    evaluation_timestamp: datetime | None = None
    idempotency_key: str = ""
    account_ledger: AccountWideLedgerSnapshot | None = None
    source: str = "backend_global_gate_engine"

    @model_validator(mode="after")
    def global_gate_is_one_way(self) -> "GlobalGateResult":
        if self.allowed_quantity > self.proposed_quantity:
            raise ValueError("global gates cannot increase quantity")
        if self.approved_quantity > self.requested_quantity:
            raise ValueError("global gates cannot increase requested quantity")
        return self


def build_global_gate_idempotency_key(order: GlobalGateProposedOrder) -> str:
    return idempotency_key(
        order.account_id,
        order.algorithm_id,
        order.symbol,
        order.side,
        order.order_intent_id,
        order.decision_timestamp.isoformat(),
        order.decision_id,
        order.configuration_version,
    )
