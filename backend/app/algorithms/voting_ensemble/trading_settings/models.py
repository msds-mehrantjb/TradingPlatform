"""Typed one-minute trading settings for Voting Ensemble."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


VOTING_ENSEMBLE_ALGORITHM_ID = "voting_ensemble"
VOTING_ENSEMBLE_ONE_MINUTE_SETTINGS_VERSION = "voting_ensemble_one_minute_settings_v1"
VOTING_ENSEMBLE_ONE_MINUTE_PROFILE_VERSION = "voting_ensemble_trading_profile_v1"


class ImmutableSettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StrategyEnablementSettings(ImmutableSettingsModel):
    deterministicVotingEnabled: bool = True
    mlShadowOnly: bool = True
    enabledDirectionalStrategies: tuple[str, ...]
    enabledContextModules: tuple[str, ...]
    enabledSafetyFilters: tuple[str, ...]


class AggregationThresholdSettings(ImmutableSettingsModel):
    minEligibleDirectionalVotes: int = Field(ge=1)
    minWinningVotes: int = Field(ge=1)
    minVoteEdge: float = Field(ge=0.0, le=1.0)
    holdBand: float = Field(ge=0.0, le=1.0)
    reliabilityWeightingMode: Literal["shadow", "active", "fallback"] = "shadow"
    reliabilitySampleWindow: Literal["rolling_20_trades", "rolling_60_trades", "rolling_120_trades"] = "rolling_60_trades"


class MinimumFamilySupportSettings(ImmutableSettingsModel):
    minimumFamiliesForTrade: int = Field(ge=1)
    familyWeights: dict[str, float]

    @model_validator(mode="after")
    def weights_must_be_non_negative(self) -> "MinimumFamilySupportSettings":
        if any(weight < 0 for weight in self.familyWeights.values()):
            raise ValueError("family weights must be non-negative")
        return self


class ContextBoundsSettings(ImmutableSettingsModel):
    maxContextBoost: float = Field(ge=0.0, le=1.0)
    maxContextPenalty: float = Field(ge=0.0, le=1.0)
    requireContextDataReady: bool = False


class SessionWindowSettings(ImmutableSettingsModel):
    sessionStart: str
    newTradesUntil: str
    forceClose: str
    allowedEntryHours: tuple[str, ...]


class EventBlackoutSettings(ImmutableSettingsModel):
    blockDuringMarketHalts: bool = True
    blockDuringLuld: bool = True
    blockHighImpactNews: bool = False
    blackoutMinutesBefore: int = Field(ge=0)
    blackoutMinutesAfter: int = Field(ge=0)


class DataFreshnessSettings(ImmutableSettingsModel):
    requiredTimeframe: Literal["1Min"] = "1Min"
    warmupBars: int = Field(ge=1)
    entryConfirmationBars: int = Field(ge=1)
    maxPrimaryFeedAgeSeconds: int = Field(ge=1)
    maxAuxiliaryFeedAgeSeconds: int = Field(ge=1)
    rejectPartialBars: bool = True


class LatencyLimitSettings(ImmutableSettingsModel):
    maxDecisionLatencyMs: int = Field(ge=1)
    maxQueueLatencyMs: int = Field(ge=1)
    commandDeadlineSeconds: int = Field(ge=1)


class SpreadLimitSettings(ImmutableSettingsModel):
    maximumSpreadBps: float = Field(ge=0.0)
    maximumSpreadDollars: float = Field(ge=0.0)


class SlippageLimitSettings(ImmutableSettingsModel):
    slippagePerShare: float = Field(ge=0.0)
    maxSlippagePerShare: float = Field(ge=0.0)
    slippageReserveMultiplier: float = Field(ge=0.0)


class NetEdgeRequirementSettings(ImmutableSettingsModel):
    minimumNetEdgeR: float = Field(ge=0.0)
    includeEstimatedFees: bool = True
    includeSlippageReserve: bool = True


class RiskPerTradeSettings(ImmutableSettingsModel):
    startingCapital: float = Field(gt=0.0)
    riskPerTradePercent: float = Field(ge=0.0)
    riskBudgetPercentOfOrder: float = Field(ge=0.0)


class DailyLossCapSettings(ImmutableSettingsModel):
    maxDailyLossPercent: float = Field(gt=0.0)


class PositionNotionalCapSettings(ImmutableSettingsModel):
    orderAllocationPercent: float = Field(ge=0.0)
    dailyAllocationPercent: float = Field(ge=0.0)
    maximumPositionPercent: float = Field(ge=0.0)
    maxShareQuantity: int = Field(ge=0)


class MaximumTradesSettings(ImmutableSettingsModel):
    maxTradesPerDay: int = Field(ge=0)
    maxConcurrentPositions: int = Field(ge=0)


class StopPolicySettings(ImmutableSettingsModel):
    stopLossPercent: float = Field(gt=0.0)
    fixedStopDistanceDollars: float = Field(ge=0.0)
    minimumStopDistanceDollars: float = Field(ge=0.0)


class TargetPolicySettings(ImmutableSettingsModel):
    takeProfitR: float = Field(gt=0.0)


class HoldingTimePolicySettings(ImmutableSettingsModel):
    maximumHoldingMinutes: int = Field(ge=1)
    signalFadeExit: Literal["disabled", "shadow", "active"] = "disabled"


class OrderTypeLimitPolicySettings(ImmutableSettingsModel):
    orderType: Literal["LIMIT"] = "LIMIT"
    limitPriceMode: Literal["entry_price", "midpoint_with_spread_cap"] = "entry_price"
    timeInForce: Literal["DAY"] = "DAY"


class CancellationReplacementPolicySettings(ImmutableSettingsModel):
    cancelUnfilledAfterSeconds: int = Field(ge=1)
    allowReplacement: bool = True
    maxReplacementAttempts: int = Field(ge=0)


class ProfileOverlayLimitSettings(ImmutableSettingsModel):
    minimumRiskMultiplier: float = Field(ge=0.0, le=1.0)
    minimumAllocationMultiplier: float = Field(ge=0.0, le=1.0)
    maximumSlippageMultiplier: float = Field(ge=1.0)


class PaperExecutionModeSettings(ImmutableSettingsModel):
    paperOnly: bool = True
    liveTradingEnabled: bool = False
    executionAdapter: str = "paper"

    @model_validator(mode="after")
    def live_trading_must_remain_disabled(self) -> "PaperExecutionModeSettings":
        if not self.paperOnly or self.liveTradingEnabled:
            raise ValueError("Voting Ensemble one-minute trading settings are paper-only")
        return self


class ExpenseModelSettings(ImmutableSettingsModel):
    description: str
    additionalLiquidityCostPerSharePerSide: float = Field(ge=0.0)
    commissionPerSharePerSide: float = Field(ge=0.0)
    secFeeRateOnSellNotional: float = Field(ge=0.0)
    finraTafPerSellShare: float = Field(ge=0.0)
    finraTafMaxPerTrade: float = Field(ge=0.0)


class ResolvedTradingProfileSettings(ImmutableSettingsModel):
    profileId: str
    activeOverlays: tuple[str, ...]
    overlayReasons: tuple[str, ...]
    entryPermission: Literal["allow_new_entries", "block_new_entries"]
    entriesBlocked: bool
    exitManagementEnabled: bool = True
    riskMultiplier: float = Field(ge=0.0, le=1.0)
    allocationMultiplier: float = Field(ge=0.0, le=1.0)
    dailyAllocationMultiplier: float = Field(ge=0.0, le=1.0)
    maxTradesMultiplier: float = Field(ge=0.0, le=1.0)
    estimatedCostMultiplier: float = Field(ge=1.0)
    riskPerTradePercent: float = Field(ge=0.0)
    orderAllocationPercent: float = Field(ge=0.0)
    dailyAllocationPercent: float = Field(ge=0.0)
    maximumPositionPercent: float = Field(ge=0.0)
    maxShareQuantity: int = Field(ge=0)
    maxTradesPerDay: int = Field(ge=0)
    minimumFinalScore: float = Field(ge=0.0, le=1.0)
    minimumIndependentFamilySupport: int = Field(ge=1)
    minimumNetEdgeR: float = Field(ge=0.0)
    minimumEdgeToCostRatio: float = Field(ge=0.0)
    maximumSpreadBps: float = Field(ge=0.0)
    maximumSpreadDollars: float = Field(ge=0.0)
    maximumSlippagePerShare: float = Field(ge=0.0)
    stopMultiplier: float = Field(gt=0.0)
    targetMultiplier: float = Field(gt=0.0)
    maximumHoldingMinutes: int = Field(ge=1)
    limitOrderOffsetBps: float = Field(ge=0.0)
    cancelReplaceTimeoutSeconds: int = Field(ge=1)
    cooldownSeconds: int = Field(ge=0)
    sourceInputs: dict[str, str | int | float | bool]


class VotingEnsembleOneMinuteSettings(ImmutableSettingsModel):
    algorithmId: Literal["voting_ensemble"] = VOTING_ENSEMBLE_ALGORITHM_ID
    settingsVersion: str = VOTING_ENSEMBLE_ONE_MINUTE_SETTINGS_VERSION
    profileVersion: str = VOTING_ENSEMBLE_ONE_MINUTE_PROFILE_VERSION
    configurationHash: str
    sourceBaselineVersion: str
    appliedOverlays: tuple[str, ...]
    resolutionTimestamp: datetime
    reasonCodes: tuple[str, ...]
    strategyEnablement: StrategyEnablementSettings
    aggregationThresholds: AggregationThresholdSettings
    minimumFamilySupport: MinimumFamilySupportSettings
    contextBounds: ContextBoundsSettings
    sessionWindows: SessionWindowSettings
    eventBlackouts: EventBlackoutSettings
    dataFreshness: DataFreshnessSettings
    latencyLimits: LatencyLimitSettings
    spreadLimits: SpreadLimitSettings
    slippageLimits: SlippageLimitSettings
    netEdgeRequirements: NetEdgeRequirementSettings
    riskPerTrade: RiskPerTradeSettings
    dailyLossCap: DailyLossCapSettings
    positionNotionalCap: PositionNotionalCapSettings
    maximumTrades: MaximumTradesSettings
    stopPolicy: StopPolicySettings
    targetPolicy: TargetPolicySettings
    holdingTimePolicy: HoldingTimePolicySettings
    orderTypeAndLimitPolicy: OrderTypeLimitPolicySettings
    cancellationAndReplacementPolicy: CancellationReplacementPolicySettings
    profileOverlayLimits: ProfileOverlayLimitSettings
    paperExecutionMode: PaperExecutionModeSettings
    expenseModel: ExpenseModelSettings
    resolvedTradingProfile: ResolvedTradingProfileSettings
    entriesBlocked: bool = False
    positionSizingMode: Literal["allocation", "risk"] = "allocation"
    positionSizing: str
