from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from enum import Enum, IntEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Direction(IntEnum):
    SHORT = -1
    FLAT = 0
    LONG = 1


class StrategyRole(str, Enum):
    DIRECTIONAL = "DIRECTIONAL"
    CONTEXT = "CONTEXT"
    REGIME = "REGIME"
    SAFETY = "SAFETY"
    AGGREGATOR = "AGGREGATOR"


class StrategyFamily(str, Enum):
    TREND = "TREND"
    BREAKOUT = "BREAKOUT"
    REVERSAL = "REVERSAL"
    MEAN_REVERSION = "MEAN_REVERSION"
    GAP_SESSION = "GAP_SESSION"
    MARKET_CONTEXT = "MARKET_CONTEXT"
    SAFETY = "SAFETY"


class GateStatus(str, Enum):
    PASS = "PASS"
    CAUTION = "CAUTION"
    FAIL = "FAIL"
    INFO = "INFO"


class OperatingMode(str, Enum):
    OFF = "OFF"
    SHADOW = "SHADOW"
    FILTER = "FILTER"
    ACTIVE = "ACTIVE"
    FALLBACK = "FALLBACK"


Score01 = Field(
    ge=0.0,
    le=1.0,
    description="Normalized score from 0.0 to 1.0 inclusive; higher means stronger support.",
)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value.astimezone(UTC)


def _validate_geometry(side: Signal | str, entry: float, stop: float | None, target: float | None) -> None:
    normalized = side.value if isinstance(side, Signal) else str(side)
    if normalized == Signal.BUY.value:
        if stop is None or target is None or not (stop < entry < target):
            raise ValueError("BUY geometry requires stopPrice < entryPrice < targetPrice")
    elif normalized == Signal.SELL.value:
        if stop is None or target is None or not (target < entry < stop):
            raise ValueError("SELL geometry requires targetPrice < entryPrice < stopPrice")


class StrategySignal(DomainModel):
    strategyId: str = Field(min_length=1)
    strategyName: str = Field(min_length=1)
    strategyVersion: str = Field(min_length=1)
    family: StrategyFamily
    role: StrategyRole
    signal: Signal
    direction: Direction
    confidence: float = Score01
    active: bool
    eligible: bool
    dataReady: bool
    setupDetected: bool
    regimeFit: float = Score01
    reliability: float = Score01
    reliabilityVersion: str = "strategy_signal_static_v1"
    reliabilitySourceWindow: dict[str, Any] = Field(default_factory=dict)
    structuralInvalidationPrice: float | None = Field(default=None, gt=0)
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)
    features: dict[str, Any] = Field(default_factory=dict)
    requiredInputs: list[str] = Field(default_factory=list)
    inputTimestamps: dict[str, datetime] = Field(default_factory=dict)
    evaluatedAt: datetime
    sessionDate: date
    configurationHash: str = Field(min_length=1)

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator("inputTimestamps")
    @classmethod
    def input_timestamps_must_be_utc(cls, value: dict[str, datetime]) -> dict[str, datetime]:
        return {key: _require_utc(timestamp) for key, timestamp in value.items()}

    @model_validator(mode="after")
    def direction_must_match_signal(self) -> StrategySignal:
        expected = {
            Signal.BUY.value: Direction.LONG,
            Signal.SELL.value: Direction.SHORT,
            Signal.HOLD.value: Direction.FLAT,
        }[str(self.signal)]
        if self.direction != expected:
            raise ValueError("direction must be derived from signal, not from strategy-fit score")
        return self


class ContextSignal(DomainModel):
    contextId: str = Field(min_length=1)
    signal: Signal
    direction: Direction
    confidence: float = Score01
    dataReady: bool
    explanation: str = Field(min_length=1)
    features: dict[str, Any] = Field(default_factory=dict)
    evaluatedAt: datetime
    sessionDate: date
    configurationHash: str = Field(min_length=1)

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class RegimeState(DomainModel):
    regimeId: str = Field(min_length=1)
    label: str = Field(min_length=1)
    direction: Direction
    volatility: Literal["LOW", "NORMAL", "HIGH", "EXTREME"]
    confidence: float = Score01
    features: dict[str, Any] = Field(default_factory=dict)
    evaluatedAt: datetime
    sessionDate: date
    configurationHash: str = Field(min_length=1)

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class FamilyScore(DomainModel):
    family: StrategyFamily
    buyScore: float = Score01
    sellScore: float = Score01
    holdScore: float = Score01
    confidence: float = Score01
    reliability: float = Score01
    explanation: str = Field(min_length=1)


class EnsembleDecision(DomainModel):
    decisionId: str = Field(min_length=1)
    signal: Signal
    direction: Direction
    confidence: float = Score01
    rawScore: float = Field(default=0.0, ge=-1.0, le=1.0)
    finalScore: float = Field(default=0.0, ge=-1.0, le=1.0)
    buyConfidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sellConfidence: float = Field(default=0.0, ge=0.0, le=1.0)
    holdConfidence: float = Field(default=1.0, ge=0.0, le=1.0)
    supportingFamilies: list[StrategyFamily] = Field(default_factory=list)
    opposingFamilies: list[StrategyFamily] = Field(default_factory=list)
    eligibleStrategyCount: int = Field(default=0, ge=0)
    familyScores: list[FamilyScore] = Field(default_factory=list)
    strategySignals: list[StrategySignal] = Field(default_factory=list)
    contextAdjustments: list[dict[str, Any]] = Field(default_factory=list)
    safetyStatus: GateStatus = GateStatus.INFO
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)
    dataReady: bool
    eligible: bool
    decidedAt: datetime
    sessionDate: date
    configurationHash: str = Field(min_length=1)
    engineVersion: str = Field(min_length=1)

    @field_validator("decidedAt")
    @classmethod
    def decided_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class MetaModelPrediction(DomainModel):
    modelId: str = Field(min_length=1)
    modelVersion: str = Field(min_length=1)
    objective: Literal["candidate_success_probability"] = "candidate_success_probability"
    candidateSide: Signal | None = None
    probabilityCandidateSuccess: float | None = Field(default=None, ge=0.0, le=1.0)
    probabilityTargetBeforeStop: float | None = Field(default=None, ge=0.0, le=1.0)
    probabilityProfitableAfterCosts: float | None = Field(default=None, ge=0.0, le=1.0)
    signal: Signal
    probabilityBuy: float = Score01
    probabilitySell: float = Score01
    probabilityHold: float = Score01
    confidence: float = Score01
    reliability: float = Score01
    features: dict[str, Any] = Field(default_factory=dict)
    predictedAt: datetime
    sessionDate: date
    configurationHash: str = Field(min_length=1)

    @field_validator("predictedAt")
    @classmethod
    def predicted_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class GateResult(DomainModel):
    gateId: str = Field(min_length=1)
    gateName: str = Field(min_length=1)
    status: GateStatus
    blocksTrading: bool
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)
    checkedAt: datetime
    configurationHash: str = Field(min_length=1)

    @field_validator("checkedAt")
    @classmethod
    def checked_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class GlobalGateDecision(DomainModel):
    status: GateStatus
    eligible: bool
    dataReady: bool
    gateResults: list[GateResult] = Field(default_factory=list)
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)
    checkedAt: datetime
    sessionDate: date
    configurationHash: str = Field(min_length=1)

    @field_validator("checkedAt")
    @classmethod
    def checked_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class BaselineTradingSettings(DomainModel):
    baseRiskPercent: float = Field(default=0.25, ge=0, le=100)
    basePositionPercent: float = Field(default=50.0, ge=0, le=100)
    baseOrderAllocationPercent: float = Field(default=10.0, ge=0, le=100)
    baseDailyAllocationPercent: float = Field(default=50.0, ge=0, le=100)
    baseAtrStopMultiplier: float = Field(default=2.0, gt=0)
    baseMinimumStopPercent: float = Field(default=0.05, ge=0, le=100)
    baseTargetR: float = Field(default=1.5, gt=0)
    baseMaximumHoldingMinutes: int = Field(default=30, ge=1)
    baseParticipationPercent: float = Field(default=0.3, ge=0, le=100)
    baseEntryOffsetBps: float = Field(default=0.0, ge=0)
    baseSlippagePerShare: float = Field(default=0.02, ge=0)
    minimumExpectedValue: float = 0.0
    minimumModelProbability: float = Field(default=0.55, ge=0.0, le=1.0)
    settingsVersion: str = Field(default="baseline_trading_settings_v2", min_length=1)
    configurationHash: str = Field(min_length=1)
    startingCapital: float = Field(default=25000.0, gt=0)
    orderAllocationPercent: float = Field(default=10.0, ge=0, le=100)
    dailyAllocationPercent: float = Field(default=50.0, ge=0, le=100)
    riskBudgetPercentOfOrder: float = Field(default=50.0, ge=0, le=100)
    maxTradesPerDay: int = Field(default=10, ge=0)
    stopLossPercent: float = Field(default=0.35, gt=0, le=100)
    fixedStopDistanceDollars: float = Field(default=1.0, ge=0)
    takeProfitR: float = Field(default=1.5, gt=0)
    slippagePerShare: float = Field(default=0.02, ge=0)
    positionSizingMode: Literal["allocation", "risk"] = "risk"


class HardRiskLimits(DomainModel):
    maximumRiskPerTradePercent: float = Field(default=1.0, ge=0, le=100)
    maximumDailyLossPercent: float = Field(default=3.0, ge=0, le=100)
    maximumOpenRiskPercent: float = Field(default=3.0, ge=0, le=100)
    maximumPositionPercent: float = Field(default=50.0, ge=0, le=100)
    maximumOrderNotionalPercent: float = Field(default=10.0, ge=0, le=100)
    maximumDailyNotionalPercent: float = Field(default=50.0, ge=0, le=100)
    maximumShares: int = Field(default=1000, ge=0)
    maximumVolumeParticipationPercent: float = Field(default=1.0, ge=0, le=100)
    maximumTradesPerDay: int = Field(default=10, ge=0)
    maximumConsecutiveLosses: int = Field(default=3, ge=0)
    maximumSpreadBps: float = Field(default=25.0, ge=0)
    allowPyramiding: bool = False
    newEntryCutoff: time = time(20, 45)
    configurationHash: str = Field(min_length=1)
    maxDailyLossPercent: float = Field(default=3.0, ge=0, le=100)
    maxOrderNotional: float = Field(default=2500.0, gt=0)
    maxPositionNotional: float = Field(default=12500.0, gt=0)
    maxShareQuantity: int = Field(default=1000, ge=0)
    minStopDistanceDollars: float = Field(default=0.01, ge=0)
    maxSlippagePerShare: float = Field(default=1.0, ge=0)


class DynamicPolicyBounds(DomainModel):
    minimumRiskMultiplier: float = Field(default=0.0, ge=0)
    maximumRiskMultiplier: float = Field(default=1.0, ge=0)
    minimumTargetR: float = Field(default=1.0, gt=0)
    maximumTargetR: float = Field(default=3.0, gt=0)
    minimumHoldingMinutes: int = Field(default=1, ge=1)
    maximumHoldingMinutes: int = Field(default=120, ge=1)
    minimumAtrStopMultiplier: float = Field(default=0.5, gt=0)
    maximumAtrStopMultiplier: float = Field(default=4.0, gt=0)
    minConfidence: float = Score01
    minReliability: float = Score01
    minRegimeFit: float = Score01
    maxSpreadPercent: float = Field(ge=0, le=100)
    maxParticipationPercent: float = Field(ge=0, le=100)
    minLiquidityShares: int = Field(ge=0)
    configurationHash: str = Field(min_length=1)

    @model_validator(mode="after")
    def bounds_must_be_ordered(self) -> DynamicPolicyBounds:
        if self.maximumRiskMultiplier < self.minimumRiskMultiplier:
            raise ValueError("maximumRiskMultiplier must be >= minimumRiskMultiplier")
        if self.maximumTargetR < self.minimumTargetR:
            raise ValueError("maximumTargetR must be >= minimumTargetR")
        if self.maximumHoldingMinutes < self.minimumHoldingMinutes:
            raise ValueError("maximumHoldingMinutes must be >= minimumHoldingMinutes")
        if self.maximumAtrStopMultiplier < self.minimumAtrStopMultiplier:
            raise ValueError("maximumAtrStopMultiplier must be >= minimumAtrStopMultiplier")
        return self


class TradeCandidate(DomainModel):
    candidateId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    signal: Signal
    direction: Direction
    entryPrice: float = Field(gt=0)
    stopPrice: float | None = Field(default=None, gt=0)
    targetPrice: float | None = Field(default=None, gt=0)
    quantity: int = Field(ge=0)
    confidence: float = Score01
    expectedValue: float | None = None
    features: dict[str, Any] = Field(default_factory=dict)
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)
    generatedAt: datetime
    sessionDate: date
    configurationHash: str = Field(min_length=1)

    @field_validator("generatedAt")
    @classmethod
    def generated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def prices_must_have_valid_geometry(self) -> TradeCandidate:
        _validate_geometry(self.signal, self.entryPrice, self.stopPrice, self.targetPrice)
        return self


class AccountRiskState(DomainModel):
    accountId: str = Field(min_length=1)
    equity: float = Field(ge=0)
    buyingPower: float = Field(ge=0)
    openPositionNotional: float = Field(ge=0)
    realizedPnlToday: float
    unrealizedPnlToday: float = 0.0
    estimatedExitCosts: float = Field(default=0.0, ge=0.0)
    dailyNetPnlAfterExitCosts: float | None = None
    intradayEquityHigh: float | None = Field(default=None, ge=0.0)
    drawdownFromIntradayHighPercent: float = Field(default=0.0, ge=0.0)
    totalOpenRiskPercent: float = Field(default=0.0, ge=0.0)
    totalSpyNotionalPercent: float = Field(default=0.0, ge=0.0)
    sameDirectionExposurePercent: float = Field(default=0.0, ge=0.0)
    tradesToday: int = Field(ge=0)
    observedAt: datetime
    sessionDate: date

    @field_validator("observedAt")
    @classmethod
    def observed_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class EffectiveTradePolicy(DomainModel):
    mode: OperatingMode
    baselineSettings: BaselineTradingSettings
    hardRiskLimits: HardRiskLimits
    dynamicBounds: DynamicPolicyBounds
    accountRiskState: AccountRiskState
    maxQuantity: int = Field(ge=0)
    maxNotional: float = Field(ge=0)
    riskDollars: float = Field(ge=0)
    explanation: str = Field(min_length=1)
    effectiveAt: datetime
    sessionDate: date
    configurationHash: str = Field(min_length=1)

    @field_validator("effectiveAt")
    @classmethod
    def effective_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class OrderPlan(DomainModel):
    orderPlanId: str = Field(min_length=1)
    candidateId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: Signal
    orderType: Literal["STOP_LIMIT", "LIMIT", "MARKET", "NO_ORDER"]
    quantity: int = Field(ge=0)
    entryPrice: float = Field(gt=0)
    stopPrice: float | None = Field(default=None, gt=0)
    targetPrice: float | None = Field(default=None, gt=0)
    limitPrice: float | None = Field(default=None, gt=0)
    maximumHoldingMinutes: int | None = Field(default=None, ge=1)
    strategyInvalidationPrice: float | None = Field(default=None, gt=0)
    endOfDayExit: bool = True
    timeInForce: Literal["DAY", "GTC"]
    eligible: bool
    validationErrors: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)
    generatedAt: datetime
    sessionDate: date
    configurationHash: str = Field(min_length=1)

    @field_validator("generatedAt")
    @classmethod
    def generated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def prices_must_have_valid_geometry(self) -> OrderPlan:
        if self.orderType != "NO_ORDER":
            _validate_geometry(self.side, self.entryPrice, self.stopPrice, self.targetPrice)
        return self


class FillResult(DomainModel):
    fillId: str = Field(min_length=1)
    orderPlanId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: Signal
    quantity: int = Field(ge=0)
    averageFillPrice: float = Field(gt=0)
    fees: float = Field(ge=0)
    slippagePerShare: float = Field(ge=0)
    filledAt: datetime
    sessionDate: date
    explanation: str = Field(min_length=1)

    @field_validator("filledAt")
    @classmethod
    def filled_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class CandidateMetaLabel(DomainModel):
    labelSchemaVersion: Literal["candidate_meta_label_v1"] = "candidate_meta_label_v1"
    labelVersion: str = Field(default="candidate_triple_barrier_v1", min_length=1)
    labelId: str = Field(min_length=1)
    snapshotId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    candidateId: str | None = None
    candidateSide: Signal
    decisionTimestampUtc: datetime
    sessionDateNewYork: date
    entryTimestampUtc: datetime | None = None
    entryPrice: float | None = Field(default=None, gt=0)
    profitTargetPrice: float | None = Field(default=None, gt=0)
    protectiveStopPrice: float | None = Field(default=None, gt=0)
    upperBarrierPrice: float | None = Field(
        default=None,
        gt=0,
        description="Side-normalized profit-target barrier. For SELL candidates this raw price is below entry.",
    )
    lowerBarrierPrice: float | None = Field(
        default=None,
        gt=0,
        description="Side-normalized protective-stop barrier. For SELL candidates this raw price is above entry.",
    )
    verticalBarrierTimestampUtc: datetime | None = None
    firstBarrierHit: Literal["TARGET", "STOP", "VERTICAL", "NO_CANDIDATE", "NO_ENTRY", "INVALID_GEOMETRY"]
    firstBarrierTimestampUtc: datetime | None = None
    exitPrice: float | None = Field(default=None, gt=0)
    strictOutcomeLabel: Literal[0, 1] | None = None
    costAdjustedTrainingLabel: Literal[0, 1] | None = None
    grossPnlPerShare: float | None = None
    netPnlAfterCosts: float | None = None
    quantity: int = Field(default=0, ge=0)
    spreadDollars: float = Field(default=0.0, ge=0)
    slippagePerShare: float = Field(default=0.0, ge=0)
    fees: float = Field(default=0.0, ge=0)
    latencyMilliseconds: int = Field(default=0, ge=0)
    orderFillBehavior: str = Field(min_length=1)
    barrierExplanation: str = Field(min_length=1)
    eligibleForTraining: bool
    reasonCodes: list[str] = Field(default_factory=list)
    createdAt: datetime
    configurationHash: str = Field(min_length=1)

    @field_validator("decisionTimestampUtc", "entryTimestampUtc", "verticalBarrierTimestampUtc", "firstBarrierTimestampUtc", "createdAt")
    @classmethod
    def label_timestamps_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None

    @model_validator(mode="after")
    def binary_labels_only_exist_for_trainable_candidates(self) -> CandidateMetaLabel:
        if self.entryTimestampUtc and self.entryTimestampUtc <= self.decisionTimestampUtc:
            raise ValueError("label entry timestamp must be after the decision timestamp")
        if self.eligibleForTraining and (self.strictOutcomeLabel is None or self.costAdjustedTrainingLabel is None):
            raise ValueError("eligible candidate meta-labels require strict and cost-adjusted binary labels")
        if not self.eligibleForTraining and (self.strictOutcomeLabel is not None or self.costAdjustedTrainingLabel is not None):
            raise ValueError("ineligible diagnostic labels must not masquerade as failed candidate trades")
        if self.candidateSide == Signal.HOLD.value and self.eligibleForTraining:
            raise ValueError("Hold snapshots are diagnostics only and cannot be candidate-training labels")
        if self.eligibleForTraining and self.entryPrice and self.profitTargetPrice and self.protectiveStopPrice:
            _validate_geometry(self.candidateSide, self.entryPrice, self.protectiveStopPrice, self.profitTargetPrice)
        return self


def decision_snapshot_configuration_hash(parts: dict[str, Any]) -> str:
    serialized = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


class V1SnapshotArchiveRecord(DomainModel):
    archiveSchemaVersion: Literal["v1_snapshot_archive_v1"] = "v1_snapshot_archive_v1"
    archiveId: str = Field(min_length=1)
    sourceSnapshotId: str = Field(min_length=1)
    sourceSchemaVersion: str = Field(min_length=1)
    archivedAt: datetime
    preservedFor: Literal["historical_comparison"] = "historical_comparison"
    trainingCompatibleWithV2: bool = False
    containsDuplicatedVoteSignals: bool = True
    migrationMetadata: dict[str, Any] = Field(default_factory=dict)
    explanation: str = Field(min_length=1)

    @field_validator("archivedAt")
    @classmethod
    def archived_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def v1_archive_must_not_be_v2_training_compatible(self) -> V1SnapshotArchiveRecord:
        if self.sourceSchemaVersion != "decision_snapshot_v2" and self.trainingCompatibleWithV2:
            raise ValueError("V1 snapshots are incompatible with V2 training and must remain archived separately")
        return self


class DecisionSnapshotV2(DomainModel):
    snapshotSchemaVersion: Literal["decision_snapshot_v2"] = "decision_snapshot_v2"
    snapshotVersion: Literal["decision_snapshot_v2"] = "decision_snapshot_v2"
    strategySchemaVersion: str = Field(default="strategy_signal_schema_v2", min_length=1)
    featureSchemaVersion: str = Field(default="point_in_time_feature_engine_v1", min_length=1)
    labelVersion: str = Field(default="unlabeled_v1", min_length=1)
    executionModelVersion: str = Field(default="paper_execution_model_v1", min_length=1)
    gateVersion: str = Field(default="global_gate_schema_v2", min_length=1)
    policyVersion: str = Field(default="effective_trade_policy_v2", min_length=1)
    modelVersion: str = Field(default="none", min_length=1)
    snapshotId: str = Field(min_length=1)
    codeVersion: str = Field(default="unknown", min_length=1)
    symbol: str = Field(min_length=1)
    marketDataFeed: str = Field(default="unknown", min_length=1)
    decisionTimestampUtc: datetime | None = None
    sessionDateNewYork: date | None = None
    sessionDate: date
    decisionTimestamp: datetime
    operatingMode: OperatingMode
    dataQuality: dict[str, Any] = Field(default_factory=dict)
    rawMarketReferences: dict[str, Any] = Field(default_factory=dict)
    featureSnapshot: dict[str, Any] = Field(default_factory=dict)
    strategySignals: list[StrategySignal] = Field(default_factory=list)
    directionalStrategyOutputs: list[StrategySignal] = Field(default_factory=list)
    contextSignals: list[ContextSignal] = Field(default_factory=list)
    contextOutputs: list[ContextSignal] = Field(default_factory=list)
    regimeState: RegimeState
    safetyOutput: GlobalGateDecision | None = None
    ensembleDecision: EnsembleDecision
    metaModelPrediction: MetaModelPrediction | None = None
    globalGateDecision: GlobalGateDecision
    globalGateResults: list[GateResult] = Field(default_factory=list)
    effectiveTradePolicy: EffectiveTradePolicy
    tradeCandidate: TradeCandidate | None = None
    orderPlan: OrderPlan | None = None
    brokerSubmissionResult: dict[str, Any] | None = None
    fillResult: FillResult | None = None
    fills: list[FillResult] = Field(default_factory=list)
    positionState: dict[str, Any] = Field(default_factory=dict)
    finalOutcome: dict[str, Any] | None = None
    eligibleForTraining: bool = False
    trainingIncompatibilityReasons: list[str] = Field(default_factory=list)
    samplingProbability: float = Field(default=1.0, gt=0.0, le=1.0)
    sampleWeight: float = Field(default=1.0, gt=0.0)
    samplingReason: str = "record_all_eligible_decision_timestamps"
    explanation: str = Field(min_length=1)
    engineVersion: str = Field(min_length=1)
    strategyConfigurationHash: str = Field(default="unknown", min_length=1)
    tradingSettingsHash: str = Field(default="unknown", min_length=1)
    configurationHash: str = Field(min_length=1)

    @field_validator("decisionTimestamp", "decisionTimestampUtc")
    @classmethod
    def decision_timestamp_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None

    @model_validator(mode="after")
    def snapshot_v2_reproducibility_fields_must_be_consistent(self) -> DecisionSnapshotV2:
        if self.decisionTimestampUtc is None:
            self.decisionTimestampUtc = self.decisionTimestamp
        if self.decisionTimestampUtc != self.decisionTimestamp:
            raise ValueError("decisionTimestampUtc must match decisionTimestamp")
        if self.sessionDateNewYork is None:
            self.sessionDateNewYork = self.sessionDate
        if self.sessionDateNewYork != self.sessionDate:
            raise ValueError("sessionDateNewYork must match sessionDate")
        if not self.directionalStrategyOutputs:
            self.directionalStrategyOutputs = list(self.strategySignals)
        if not self.contextOutputs:
            self.contextOutputs = list(self.contextSignals)
        if self.safetyOutput is None:
            self.safetyOutput = self.globalGateDecision
        if not self.globalGateResults:
            self.globalGateResults = list(self.globalGateDecision.gateResults)
        if self.fillResult and not self.fills:
            self.fills = [self.fillResult]
        incompatibilities = set(self.trainingIncompatibilityReasons)
        if _contains_demo_or_fallback_market_data(self.rawMarketReferences) or _contains_demo_or_fallback_market_data(self.featureSnapshot):
            incompatibilities.add("demo_or_fallback_market_data")
        if self.snapshotSchemaVersion != "decision_snapshot_v2" or self.snapshotVersion != "decision_snapshot_v2":
            incompatibilities.add("non_v2_snapshot_schema")
        if any(signal.strategyId == "ensemble_strategy_voting" or signal.role == StrategyRole.AGGREGATOR.value for signal in self.strategySignals):
            incompatibilities.add("duplicated_or_aggregator_signal_present")
        self.trainingIncompatibilityReasons = sorted(incompatibilities)
        if self.trainingIncompatibilityReasons and self.eligibleForTraining:
            raise ValueError("snapshots with incompatible data cannot be eligible for V2 training")
        return self


def _contains_demo_or_fallback_market_data(value: Any) -> bool:
    if isinstance(value, dict):
        provider = value.get("provider")
        if isinstance(provider, str) and provider.lower() in {"demo", "fallback"}:
            return True
        return any(_contains_demo_or_fallback_market_data(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_demo_or_fallback_market_data(item) for item in value)
    return False
