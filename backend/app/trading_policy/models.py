from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from typing import Any

from pydantic import Field, field_validator, model_validator

from backend.app.domain.models import (
    AccountRiskState,
    BaselineTradingSettings,
    ContextSignal,
    DomainModel,
    DynamicPolicyBounds,
    HardRiskLimits,
    MetaModelPrediction,
    OperatingMode,
    RegimeState,
    Signal,
    TradeCandidate,
    _require_utc,
)


POLICY_ENGINE_VERSION = "dynamic_trading_policy_engine_v1"


class DynamicTradingPolicyConfig(DomainModel):
    policyVersion: str = POLICY_ENGINE_VERSION
    mode: OperatingMode = OperatingMode.OFF
    useMetaModelRiskModifier: bool = True
    contextConflictRiskMultiplier: float = Field(default=0.75, ge=0.0, le=1.0)
    strongRegimeRiskMultiplier: float = Field(default=1.0, ge=0.0)
    weakRegimeRiskMultiplier: float = Field(default=0.75, ge=0.0)
    highVolatilityRiskMultiplier: float = Field(default=0.65, ge=0.0)
    extremeVolatilityRiskMultiplier: float = Field(default=0.0, ge=0.0)
    minimumMetaProbabilityForRisk: float = Field(default=0.55, ge=0.0, le=1.0)
    maximumMetaRiskMultiplier: float = Field(default=1.0, ge=0.0)
    missingMlFallbackCap: float = Field(default=0.75, ge=0.0, le=1.0)
    dataQualityFallbackCap: float = Field(default=0.5, ge=0.0, le=1.0)
    weakFamilyAgreementCap: float = Field(default=0.5, ge=0.0, le=1.0)
    eventRiskCap: float = Field(default=0.5, ge=0.0, le=1.0)
    nearCutoffRiskCap: float = Field(default=0.5, ge=0.0, le=1.0)
    nearCutoffMinutes: int = Field(default=15, ge=0)
    supportedOrderTypes: list[str] = Field(default_factory=lambda: ["LIMIT", "STOP_LIMIT", "BRACKET_OCO"])
    maxChaseDistanceBps: float = Field(default=8.0, ge=0.0)
    pullbackExpirationBars: int = Field(default=2, ge=1)
    breakoutTriggerBufferBps: float = Field(default=2.0, ge=0.0)
    breakoutLimitOffsetBps: float = Field(default=4.0, ge=0.0)
    breakoutExpirationBars: int = Field(default=3, ge=1)
    reversalExpirationBars: int = Field(default=2, ge=1)
    gapSessionExpirationBars: int = Field(default=3, ge=1)
    configurationHash: str = Field(default="dynamic_policy_config_v1", min_length=1)

    @model_validator(mode="after")
    def supported_mode(self) -> "DynamicTradingPolicyConfig":
        if str(self.mode) == OperatingMode.FILTER.value:
            raise ValueError("dynamic trading policy mode must be OFF, SHADOW, ACTIVE, or FALLBACK")
        return self


class DynamicRiskCap(DomainModel):
    capName: str = Field(min_length=1)
    multiplier: float = Field(ge=0.0, le=1.0)
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class EntryPlan(DomainModel):
    side: Signal
    strategyFamily: str = Field(min_length=1)
    setupSubtype: str = Field(min_length=1)
    entryPrice: float = Field(gt=0)
    entryOffsetBps: float = Field(ge=0)
    limitPrice: float = Field(gt=0)
    triggerPrice: float | None = Field(default=None, gt=0)
    orderType: str = Field(min_length=1)
    maxChaseDistance: float = Field(ge=0)
    expirationBars: int = Field(ge=1)
    cancelConditions: list[str] = Field(default_factory=list)
    brokerCapabilityAssumptions: list[str] = Field(default_factory=list)
    intent: str = Field(min_length=1)
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class ExitPlan(DomainModel):
    initialProtectiveStop: float = Field(gt=0)
    profitTarget: float = Field(gt=0)
    maximumHoldingMinutes: int = Field(ge=1)
    strategyInvalidationPrice: float | None = Field(default=None, gt=0)
    endOfDayExit: bool = True
    protectiveOrderQuantity: int = Field(ge=0)
    bracketOcoSupported: bool = True
    bracketOcoPlan: bool = True
    breakEvenStopEnabled: bool = False
    trailingStopEnabled: bool = False
    partialExitEnabled: bool = False
    pyramidingEnabled: bool = False
    timeStopReason: str = Field(min_length=1)
    invalidationExitReason: str = Field(min_length=1)
    exitAssumptions: list[str] = Field(default_factory=list)
    reasonCodes: list[str] = Field(default_factory=list)
    stopPrice: float = Field(gt=0)
    targetPrice: float = Field(gt=0)
    targetR: float = Field(gt=0)
    holdingPeriodMinutes: int = Field(ge=1)
    exitStyle: str = Field(min_length=1)
    explanation: str = Field(min_length=1)


class StopComponent(DomainModel):
    componentName: str = Field(min_length=1)
    distance: float = Field(ge=0)
    sourceValue: float | None = None
    explanation: str = Field(min_length=1)


class StopPlan(DomainModel):
    selectedStopDistance: float = Field(ge=0)
    selectedStopPrice: float = Field(gt=0)
    limitingComponent: str = Field(min_length=1)
    components: list[StopComponent] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class ShareCap(DomainModel):
    capName: str = Field(min_length=1)
    shares: int = Field(ge=0)
    sourceValue: float | None = None
    explanation: str = Field(min_length=1)


class PositionSizingResult(DomainModel):
    quantity: int = Field(ge=0)
    riskBasedShares: int = Field(ge=0)
    limitingShareCap: str = Field(min_length=1)
    shareCaps: list[ShareCap] = Field(default_factory=list)
    plannedRiskDollars: float = Field(ge=0)
    stopPlan: StopPlan
    explanation: str = Field(min_length=1)


class RiskCapBreakdown(DomainModel):
    baselineRiskDollars: float = Field(ge=0)
    signalRiskDollars: float = Field(ge=0)
    dynamicRiskDollars: float = Field(ge=0)
    hardRiskCapDollars: float = Field(ge=0)
    dailyLossRemainingDollars: float = Field(ge=0)
    openRiskCapDollars: float = Field(ge=0)
    orderNotionalCapDollars: float = Field(ge=0)
    positionNotionalCapDollars: float = Field(ge=0)
    dailyNotionalCapDollars: float = Field(ge=0)
    buyingPowerCapDollars: float = Field(ge=0)
    shareCap: int = Field(ge=0)
    volumeParticipationCapShares: int | None = Field(default=None, ge=0)
    dynamicRiskCaps: list[DynamicRiskCap] = Field(default_factory=list)
    limitingRiskCap: str | None = None
    stopPlan: StopPlan | None = None
    shareCaps: list[ShareCap] = Field(default_factory=list)
    limitingShareCap: str | None = None
    plannedRiskDollars: float = Field(default=0, ge=0)
    appliedCaps: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class DynamicPolicyInputs(DomainModel):
    candidate: TradeCandidate
    regimeState: RegimeState
    contextSignals: list[ContextSignal] = Field(default_factory=list)
    metaModelPrediction: MetaModelPrediction
    accountRiskState: AccountRiskState
    baselineSettings: BaselineTradingSettings
    hardRiskLimits: HardRiskLimits
    dynamicBounds: DynamicPolicyBounds
    evaluatedAt: datetime

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class DynamicTradingPolicyDecision(DomainModel):
    tradeAllowed: bool
    approvedRiskDollars: float = Field(ge=0)
    effectiveRiskMultiplier: float = Field(ge=0)
    maximumNotional: float = Field(ge=0)
    quantity: int = Field(ge=0)
    entryPlan: EntryPlan | None = None
    stop: float | None = Field(default=None, gt=0)
    target: float | None = Field(default=None, gt=0)
    holdingPeriodMinutes: int = Field(ge=0)
    exitPlan: ExitPlan | None = None
    capBreakdown: RiskCapBreakdown
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)
    policyVersion: str = Field(min_length=1)
    mode: OperatingMode
    decidedAt: datetime
    sessionDate: date
    configurationHash: str = Field(min_length=1)

    @field_validator("decidedAt")
    @classmethod
    def decided_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


def policy_configuration_hash(parts: dict[str, Any]) -> str:
    serialized = json.dumps(_jsonable(parts), sort_keys=True, separators=(",", ":"))
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
    return value
