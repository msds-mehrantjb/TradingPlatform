from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import Field, field_validator

from backend.app.domain.models import AccountRiskState, Direction, DomainModel, Signal, _require_utc


ACCOUNT_RISK_AGGREGATOR_VERSION = "global_account_risk_aggregator_v1"
AlgorithmId = Literal[
    "voting_ensemble",
    "weighted_voting",
    "confidence_aggregation",
    "wca",
    "regime_selector",
    "meta_strategy",
]


class BrokerPositionState(DomainModel):
    algorithmId: AlgorithmId
    capitalPartitionId: str | None = None
    decisionId: str | None = None
    orderIntentId: str | None = None
    riskReservationId: str | None = None
    positionOwner: AlgorithmId | None = None
    parentOrderId: str | None = None
    exitOwner: AlgorithmId | None = None
    symbol: str = Field(min_length=1)
    side: Signal
    quantity: int = Field(ge=0)
    averageEntryPrice: float = Field(gt=0)
    markPrice: float = Field(gt=0)
    stopPrice: float | None = Field(default=None, gt=0)
    realizedPnlToday: float = 0.0
    openedAt: datetime | None = None

    @field_validator("openedAt")
    @classmethod
    def opened_at_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None

    @field_validator("capitalPartitionId")
    @classmethod
    def partition_id_must_match_algorithm(cls, value: str | None, info) -> str | None:
        algorithm_id = info.data.get("algorithmId")
        if value is not None and algorithm_id is not None and not value.startswith(f"{algorithm_id}."):
            raise ValueError("capitalPartitionId must be namespaced by algorithmId")
        return value


class BrokerOrderState(DomainModel):
    algorithmId: AlgorithmId
    capitalPartitionId: str | None = None
    decisionId: str | None = None
    orderIntentId: str | None = None
    riskReservationId: str | None = None
    positionOwner: AlgorithmId | None = None
    parentOrderId: str | None = None
    exitOwner: AlgorithmId | None = None
    symbol: str = Field(min_length=1)
    side: Signal
    clientOrderId: str | None = None
    orderType: str = Field(min_length=1)
    status: Literal["PENDING", "PARTIALLY_FILLED", "ACCEPTED", "NEW"] = "PENDING"
    quantity: int = Field(ge=0)
    filledQuantity: int = Field(default=0, ge=0)
    entryPrice: float = Field(gt=0)
    stopPrice: float | None = Field(default=None, gt=0)
    submittedAt: datetime

    @field_validator("submittedAt")
    @classmethod
    def submitted_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @property
    def remaining_quantity(self) -> int:
        return max(0, self.quantity - self.filledQuantity)

    @field_validator("capitalPartitionId")
    @classmethod
    def partition_id_must_match_algorithm(cls, value: str | None, info) -> str | None:
        algorithm_id = info.data.get("algorithmId")
        if value is not None and algorithm_id is not None and not value.startswith(f"{algorithm_id}."):
            raise ValueError("capitalPartitionId must be namespaced by algorithmId")
        return value


class BrokerAccountSnapshot(DomainModel):
    accountId: str = Field(min_length=1)
    equity: float = Field(ge=0)
    buyingPower: float = Field(ge=0)
    realizedPnlToday: float = 0.0
    intradayEquityHigh: float | None = Field(default=None, ge=0)
    positions: list[BrokerPositionState] = Field(default_factory=list)
    pendingOrders: list[BrokerOrderState] = Field(default_factory=list)
    partiallyFilledOrders: list[BrokerOrderState] = Field(default_factory=list)
    exitCostPerShare: float = Field(default=0.02, ge=0.0)
    observedAt: datetime
    sessionDate: date
    sourceAuthority: Literal["broker", "local_ui_history", "unknown"] = "broker"
    positionsReconciled: bool = True
    openOrdersReconciled: bool = True

    @field_validator("observedAt")
    @classmethod
    def observed_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class GlobalAccountRiskSnapshot(DomainModel):
    aggregatorVersion: str
    accountRiskState: AccountRiskState
    brokerState: dict[str, Any]
    riskState: dict[str, Any]
    reasonCodes: list[str]
    explanation: str
    observedAt: datetime
    sessionDate: date
    configurationHash: str

    @field_validator("observedAt")
    @classmethod
    def observed_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


def aggregate_global_account_risk(
    snapshot: BrokerAccountSnapshot,
    *,
    candidateSymbol: str = "SPY",
    candidateSide: Signal | str | None = None,
    configurationSalt: str = "global_account_risk_v1",
) -> GlobalAccountRiskSnapshot:
    symbol = candidateSymbol.upper()
    side = _normalize_signal(candidateSide)
    position_notional = sum(_position_notional(position) for position in snapshot.positions)
    order_notional = sum(_order_notional(order, order.remaining_quantity) for order in [*snapshot.pendingOrders, *snapshot.partiallyFilledOrders])
    spy_notional = sum(
        _position_notional(position)
        for position in snapshot.positions
        if position.symbol.upper() == symbol
    ) + sum(
        _order_notional(order, order.remaining_quantity)
        for order in [*snapshot.pendingOrders, *snapshot.partiallyFilledOrders]
        if order.symbol.upper() == symbol
    )
    open_risk = sum(_position_open_risk(position) for position in snapshot.positions) + sum(
        _order_open_risk(order) for order in [*snapshot.pendingOrders, *snapshot.partiallyFilledOrders]
    )
    unrealized = sum(_position_unrealized_pnl(position) for position in snapshot.positions)
    estimated_exit_costs = sum(position.quantity * snapshot.exitCostPerShare for position in snapshot.positions)
    daily_net_after_costs = snapshot.realizedPnlToday + unrealized - estimated_exit_costs
    current_intraday_equity = snapshot.equity + unrealized - estimated_exit_costs
    intraday_high = max(snapshot.intradayEquityHigh or snapshot.equity, current_intraday_equity, 0.0)
    drawdown_percent = ((intraday_high - current_intraday_equity) / intraday_high) * 100.0 if intraday_high > 0 else 0.0
    same_direction_notional = _same_direction_notional(snapshot, symbol, side)
    duplicate_spy_exposure = _has_same_direction_spy_exposure(snapshot, symbol, side)
    conflicting_spy_exposure = _has_conflicting_spy_exposure(snapshot, symbol, side)
    risk_state = {
        "totalOpenRiskDollars": round(open_risk, 6),
        "totalOpenRiskPercent": _percent(open_risk, snapshot.equity),
        "globalSpyNotionalDollars": round(spy_notional, 6),
        "totalSpyNotionalPercent": _percent(spy_notional, snapshot.equity),
        "sameDirectionExposureDollars": round(same_direction_notional, 6),
        "sameDirectionExposurePercent": _percent(same_direction_notional, snapshot.equity),
        "globalDailyRealizedPnl": round(snapshot.realizedPnlToday, 6),
        "globalDailyUnrealizedPnl": round(unrealized, 6),
        "estimatedExitCosts": round(estimated_exit_costs, 6),
        "dailyNetPnlAfterExitCosts": round(daily_net_after_costs, 6),
        "drawdownFromIntradayHighPercent": round(drawdown_percent, 6),
        "duplicateSpyExposure": duplicate_spy_exposure,
        "conflictingSpyExposure": conflicting_spy_exposure,
        "positionNotionalDollars": round(position_notional, 6),
        "pendingOrderNotionalDollars": round(order_notional, 6),
        "algorithmExposure": _algorithm_exposure(snapshot),
        "capitalPartitionExposure": _capital_partition_exposure(snapshot),
        "authority": snapshot.sourceAuthority,
    }
    broker_authoritative = snapshot.sourceAuthority == "broker"
    broker_state = {
        "brokerConnected": broker_authoritative,
        "paperAccountActive": broker_authoritative,
        "accountNotRestricted": broker_authoritative and snapshot.equity > 0,
        "symbolTradable": True,
        "buyingPowerCurrent": broker_authoritative,
        "positionsReconciled": broker_authoritative and snapshot.positionsReconciled,
        "openOrdersReconciled": broker_authoritative and snapshot.openOrdersReconciled,
    }
    account = AccountRiskState(
        accountId=snapshot.accountId,
        equity=snapshot.equity,
        buyingPower=snapshot.buyingPower,
        openPositionNotional=round(position_notional + order_notional, 6),
        realizedPnlToday=snapshot.realizedPnlToday,
        unrealizedPnlToday=round(unrealized, 6),
        estimatedExitCosts=round(estimated_exit_costs, 6),
        dailyNetPnlAfterExitCosts=round(daily_net_after_costs, 6),
        intradayEquityHigh=round(intraday_high, 6),
        drawdownFromIntradayHighPercent=round(drawdown_percent, 6),
        totalOpenRiskPercent=risk_state["totalOpenRiskPercent"],
        totalSpyNotionalPercent=risk_state["totalSpyNotionalPercent"],
        sameDirectionExposurePercent=risk_state["sameDirectionExposurePercent"],
        tradesToday=len(snapshot.positions),
        observedAt=snapshot.observedAt,
        sessionDate=snapshot.sessionDate,
    )
    reason_codes = ["risk.authority.broker" if broker_authoritative else f"risk.authority.{snapshot.sourceAuthority}"]
    if duplicate_spy_exposure:
        reason_codes.append("risk.duplicate_spy_exposure_detected")
    if conflicting_spy_exposure:
        reason_codes.append("risk.conflicting_spy_exposure_detected")
    return GlobalAccountRiskSnapshot(
        aggregatorVersion=ACCOUNT_RISK_AGGREGATOR_VERSION,
        accountRiskState=account,
        brokerState=broker_state,
        riskState=risk_state,
        reasonCodes=reason_codes,
        explanation="Global account risk was aggregated from broker-authoritative positions, pending orders, and partial fills.",
        observedAt=snapshot.observedAt,
        sessionDate=snapshot.sessionDate,
        configurationHash=_configuration_hash(snapshot, side, configurationSalt),
    )


def _position_notional(position: BrokerPositionState) -> float:
    return abs(position.quantity * position.markPrice)


def _order_notional(order: BrokerOrderState, quantity: int) -> float:
    return abs(quantity * order.entryPrice)


def _position_open_risk(position: BrokerPositionState) -> float:
    if position.stopPrice is None:
        return _position_notional(position)
    if position.side == Signal.BUY.value:
        return max(0.0, position.markPrice - position.stopPrice) * position.quantity
    return max(0.0, position.stopPrice - position.markPrice) * position.quantity


def _order_open_risk(order: BrokerOrderState) -> float:
    quantity = order.remaining_quantity
    if quantity <= 0:
        return 0.0
    if order.stopPrice is None:
        return _order_notional(order, quantity)
    return abs(order.entryPrice - order.stopPrice) * quantity


def _position_unrealized_pnl(position: BrokerPositionState) -> float:
    if position.side == Signal.BUY.value:
        return (position.markPrice - position.averageEntryPrice) * position.quantity
    return (position.averageEntryPrice - position.markPrice) * position.quantity


def _same_direction_notional(snapshot: BrokerAccountSnapshot, symbol: str, side: Signal | None) -> float:
    if side is None or side == Signal.HOLD:
        return 0.0
    return sum(
        _position_notional(position)
        for position in snapshot.positions
        if position.symbol.upper() == symbol and position.side == side.value
    ) + sum(
        _order_notional(order, order.remaining_quantity)
        for order in [*snapshot.pendingOrders, *snapshot.partiallyFilledOrders]
        if order.symbol.upper() == symbol and order.side == side.value
    )


def _has_same_direction_spy_exposure(snapshot: BrokerAccountSnapshot, symbol: str, side: Signal | None) -> bool:
    return side is not None and _same_direction_notional(snapshot, symbol, side) > 0


def _has_conflicting_spy_exposure(snapshot: BrokerAccountSnapshot, symbol: str, side: Signal | None) -> bool:
    if side is None or side == Signal.HOLD:
        return False
    opposite = Signal.SELL if side == Signal.BUY else Signal.BUY
    return _same_direction_notional(snapshot, symbol, opposite) > 0


def _algorithm_exposure(snapshot: BrokerAccountSnapshot) -> dict[str, dict[str, float]]:
    exposure: dict[str, dict[str, float]] = {}
    for position in snapshot.positions:
        bucket = exposure.setdefault(position.algorithmId, {"positionNotionalDollars": 0.0, "pendingOrderNotionalDollars": 0.0})
        bucket["positionNotionalDollars"] += _position_notional(position)
    for order in [*snapshot.pendingOrders, *snapshot.partiallyFilledOrders]:
        bucket = exposure.setdefault(order.algorithmId, {"positionNotionalDollars": 0.0, "pendingOrderNotionalDollars": 0.0})
        bucket["pendingOrderNotionalDollars"] += _order_notional(order, order.remaining_quantity)
    return {
        algorithm_id: {key: round(value, 6) for key, value in values.items()}
        for algorithm_id, values in sorted(exposure.items())
    }


def _capital_partition_exposure(snapshot: BrokerAccountSnapshot) -> dict[str, dict[str, float]]:
    exposure: dict[str, dict[str, float]] = {}
    for position in snapshot.positions:
        partition_id = position.capitalPartitionId or f"{position.algorithmId}.paper.default"
        bucket = exposure.setdefault(partition_id, {"positionNotionalDollars": 0.0, "pendingOrderNotionalDollars": 0.0})
        bucket["positionNotionalDollars"] += _position_notional(position)
    for order in [*snapshot.pendingOrders, *snapshot.partiallyFilledOrders]:
        partition_id = order.capitalPartitionId or f"{order.algorithmId}.paper.default"
        bucket = exposure.setdefault(partition_id, {"positionNotionalDollars": 0.0, "pendingOrderNotionalDollars": 0.0})
        bucket["pendingOrderNotionalDollars"] += _order_notional(order, order.remaining_quantity)
    return {
        partition_id: {key: round(value, 6) for key, value in values.items()}
        for partition_id, values in sorted(exposure.items())
    }


def _normalize_signal(value: Signal | str | None) -> Signal | None:
    if value is None:
        return None
    if isinstance(value, Signal):
        return value
    try:
        return Signal(str(value))
    except ValueError:
        return None


def _percent(value: float, equity: float) -> float:
    return round((value / equity) * 100.0, 6) if equity > 0 else 0.0


def _configuration_hash(snapshot: BrokerAccountSnapshot, side: Signal | None, salt: str) -> str:
    payload = {
        "version": ACCOUNT_RISK_AGGREGATOR_VERSION,
        "salt": salt,
        "side": side.value if side else None,
        "snapshot": snapshot.model_dump(mode="json"),
    }
    serialized = json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Direction):
        return int(value)
    return value
