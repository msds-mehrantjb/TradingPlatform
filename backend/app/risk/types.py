from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


GlobalGateStatus = Literal["approved", "resized", "denied"]
GateSeverity = Literal["pass", "warning", "fail"]
OrderSide = Literal["Buy", "Sell"]
PositionEffect = Literal["enter_long", "exit_long", "enter_short", "cover_short", "none"]
OrderIntentType = Literal["new_entry", "protective_exit", "risk_reducing", "end_of_day_liquidation", "reconciliation"]


class RiskModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GateResult(RiskModel):
    gateId: str = Field(min_length=1)
    gateName: str = Field(min_length=1)
    status: GateSeverity
    reason: str = Field(min_length=1)
    blocksNewEntries: bool = False
    blocksProtectiveExits: bool = False
    evaluatedAt: datetime

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_utc(cls, value: datetime) -> datetime:
        return _as_utc(value)


class GlobalGateDecision(RiskModel):
    status: GlobalGateStatus
    approvedQuantity: int = Field(ge=0)
    approvedRiskDollars: float = Field(ge=0)
    passedGates: tuple[GateResult, ...] = ()
    failedGates: tuple[GateResult, ...] = ()
    warningGates: tuple[GateResult, ...] = ()
    accountSnapshotId: str = Field(min_length=1)
    reservationId: str | None = None
    evaluatedAt: datetime

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_utc(cls, value: datetime) -> datetime:
        return _as_utc(value)


class GlobalOrderIntent(RiskModel):
    decisionId: str = Field(min_length=1)
    clientOrderId: str | None = None
    algorithmId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: OrderSide
    positionEffect: PositionEffect
    intentType: OrderIntentType = "new_entry"
    requestedQuantity: int = Field(ge=0)
    expectedEntryPrice: float = Field(gt=0)
    protectiveStopPrice: float | None = Field(default=None, gt=0)
    targetPrice: float | None = Field(default=None, gt=0)
    requestedRiskDollars: float = Field(ge=0)
    orderType: str = "limit"
    marketDataTimestamp: datetime
    generatedAt: datetime
    expiresAt: datetime
    settingsVersion: str = Field(default="unknown_settings", min_length=1)
    profileVersion: str = Field(default="unknown_profile", min_length=1)
    fractionalQuantityAllowed: bool = False
    shortable: bool = True
    borrowAvailable: bool | None = None

    @field_validator("marketDataTimestamp", "generatedAt", "expiresAt")
    @classmethod
    def timestamps_utc(cls, value: datetime) -> datetime:
        return _as_utc(value)

    @model_validator(mode="after")
    def expiration_after_generation(self) -> "GlobalOrderIntent":
        if self.expiresAt < self.generatedAt:
            raise ValueError("expiresAt must be at or after generatedAt")
        return self

    @property
    def is_new_entry(self) -> bool:
        return self.intentType == "new_entry" and self.positionEffect in {"enter_long", "enter_short"}

    @property
    def is_protective_exit(self) -> bool:
        return self.intentType != "new_entry" or self.positionEffect in {"exit_long", "cover_short"}

    @property
    def requested_notional(self) -> float:
        return self.requestedQuantity * self.expectedEntryPrice


class AccountSnapshot(RiskModel):
    accountSnapshotId: str = Field(min_length=1)
    accountId: str = Field(default="paper-account", min_length=1)
    equity: float = Field(gt=0)
    highWaterEquity: float = Field(gt=0)
    availableBuyingPower: float = Field(ge=0)
    settledCash: float | None = Field(default=None, ge=0)
    realizedDailyPnl: float = 0
    unrealizedDailyPnl: float = 0
    brokerConnected: bool = True
    brokerAccountActive: bool = True
    tradingPermission: bool = True
    clockSynchronized: bool = True
    accountSnapshotFresh: bool = True
    localBrokerOrdersReconciled: bool = True
    localBrokerPositionsReconciled: bool = True
    unresolvedSubmissionFailure: bool = False
    brokerRateLimited: bool = False
    observedAt: datetime

    @field_validator("observedAt")
    @classmethod
    def observed_at_utc(cls, value: datetime) -> datetime:
        return _as_utc(value)


class MarketSnapshot(RiskModel):
    marketSnapshotId: str = Field(min_length=1)
    session: Literal["regular", "premarket", "after_hours", "closed"] = "regular"
    regularSessionAllowed: bool = True
    extendedHoursAllowed: bool = False
    marketHoliday: bool = False
    earlyClose: bool = False
    entryCutoffReached: bool = False
    tradingHalt: bool = False
    luld: bool = False
    marketWideCircuitBreaker: bool = False
    candleTimestamp: datetime
    quoteTimestamp: datetime
    spreadPercent: float | None = Field(default=None, ge=0)
    oneMinuteVolume: int | None = Field(default=None, ge=0)
    estimatedSlippagePercent: float | None = Field(default=None, ge=0)
    eventBlackout: bool = False
    unsupportedOrderType: bool = False
    evaluatedAt: datetime

    @field_validator("candleTimestamp", "quoteTimestamp", "evaluatedAt")
    @classmethod
    def timestamps_utc(cls, value: datetime) -> datetime:
        return _as_utc(value)


class PortfolioPosition(RiskModel):
    algorithmId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    sector: str | None = None
    quantity: int
    marketValue: float = Field(ge=0)
    openRiskDollars: float = Field(default=0, ge=0)
    side: Literal["long", "short"]

    @property
    def signed_value(self) -> float:
        return self.marketValue if self.side == "long" else -self.marketValue


class PendingOrder(RiskModel):
    algorithmId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: OrderSide
    quantity: int = Field(ge=0)
    notional: float = Field(ge=0)
    riskDollars: float = Field(default=0, ge=0)
    decisionId: str = Field(min_length=1)
    clientOrderId: str | None = None
    intentKey: str = Field(min_length=1)
    submittedAt: datetime

    @field_validator("submittedAt")
    @classmethod
    def submitted_at_utc(cls, value: datetime) -> datetime:
        return _as_utc(value)


class PortfolioSnapshot(RiskModel):
    positions: tuple[PortfolioPosition, ...] = ()
    pendingOrders: tuple[PendingOrder, ...] = ()
    tradesToday: int = 0
    algorithmTradesToday: dict[str, int] = Field(default_factory=dict)
    ordersSubmittedInLastMinute: int = 0


class GlobalRiskEvaluationRequest(RiskModel):
    intent: GlobalOrderIntent
    account: AccountSnapshot
    market: MarketSnapshot
    portfolio: PortfolioSnapshot = Field(default_factory=PortfolioSnapshot)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
