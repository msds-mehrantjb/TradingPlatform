export const TRADING_SETTINGS_SCHEMA_VERSION = "canonical_trading_settings_v2" as const;

export const TRADING_SETTINGS_FIELD_GROUPS = {
  baselineSettings: [
    "baseRiskPercent",
    "basePositionPercent",
    "baseOrderAllocationPercent",
    "baseDailyAllocationPercent",
    "baseAtrStopMultiplier",
    "baseMinimumStopPercent",
    "baseTargetR",
    "baseMaximumHoldingMinutes",
    "baseParticipationPercent",
    "baseEntryOffsetBps",
    "baseSlippagePerShare",
    "minimumExpectedValue",
    "minimumModelProbability",
  ],
  hardLimits: [
    "maximumRiskPerTradePercent",
    "maximumDailyLossPercent",
    "maximumOpenRiskPercent",
    "maximumPositionPercent",
    "maximumOrderNotionalPercent",
    "maximumDailyNotionalPercent",
    "maximumShares",
    "maximumVolumeParticipationPercent",
    "maximumTradesPerDay",
    "maximumConsecutiveLosses",
    "maximumSpreadBps",
    "allowPyramiding",
    "newEntryCutoff",
  ],
  dynamicBounds: [
    "minimumRiskMultiplier",
    "maximumRiskMultiplier",
    "minimumTargetR",
    "maximumTargetR",
    "minimumHoldingMinutes",
    "maximumHoldingMinutes",
    "minimumAtrStopMultiplier",
    "maximumAtrStopMultiplier",
  ],
} as const;

export type CanonicalBaselineTradingSettings = {
  baseRiskPercent: number;
  basePositionPercent: number;
  baseOrderAllocationPercent: number;
  baseDailyAllocationPercent: number;
  baseAtrStopMultiplier: number;
  baseMinimumStopPercent: number;
  baseTargetR: number;
  baseMaximumHoldingMinutes: number;
  baseParticipationPercent: number;
  baseEntryOffsetBps: number;
  baseSlippagePerShare: number;
  minimumExpectedValue: number;
  minimumModelProbability: number;
  settingsVersion: string;
  configurationHash: string;
};

export type CanonicalHardRiskLimits = {
  maximumRiskPerTradePercent: number;
  maximumDailyLossPercent: number;
  maximumOpenRiskPercent: number;
  maximumPositionPercent: number;
  maximumOrderNotionalPercent: number;
  maximumDailyNotionalPercent: number;
  maximumShares: number;
  maximumVolumeParticipationPercent: number;
  maximumTradesPerDay: number;
  maximumConsecutiveLosses: number;
  maximumSpreadBps: number;
  allowPyramiding: boolean;
  newEntryCutoff: string;
  configurationHash: string;
};

export type CanonicalDynamicPolicyBounds = {
  minimumRiskMultiplier: number;
  maximumRiskMultiplier: number;
  minimumTargetR: number;
  maximumTargetR: number;
  minimumHoldingMinutes: number;
  maximumHoldingMinutes: number;
  minimumAtrStopMultiplier: number;
  maximumAtrStopMultiplier: number;
  configurationHash: string;
};
