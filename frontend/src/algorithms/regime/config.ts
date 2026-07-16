import type { RegimeHysteresisSettings, RegimeSizingDefaults, RegimeTradingSettings } from "./types.ts";

export const REGIME_MIN_WINNING_SCORE = 0.6;
export const REGIME_MIN_WINNING_EDGE = 0.2;
export const REGIME_MIN_CONDITION_CONFIDENCE = 0.65;
export const REGIME_MIN_INDEPENDENT_FAMILIES = 2;
export const REGIME_MAX_ABSTENTION_RATE = 0.6;
export const REGIME_MAX_INDIVIDUAL_STRATEGY_CONTRIBUTION = 0.15;
export const REGIME_MAX_FAMILY_CONTRIBUTION = 0.35;

export const DEFAULT_REGIME_HYSTERESIS_SETTINGS: RegimeHysteresisSettings = {
  confirmationBars: 3,
  immediateConfidenceThreshold: 0.65,
  minimumDwellBars: 0,
  transitionConfidenceGap: 0,
  maximumUnknownBars: 3,
};

export function resolveRegimeHysteresisSettings(settings?: Partial<RegimeHysteresisSettings>): RegimeHysteresisSettings {
  return {
    confirmationBars: Math.max(1, Math.floor(settings?.confirmationBars ?? DEFAULT_REGIME_HYSTERESIS_SETTINGS.confirmationBars)),
    immediateConfidenceThreshold: clampSetting(settings?.immediateConfidenceThreshold, DEFAULT_REGIME_HYSTERESIS_SETTINGS.immediateConfidenceThreshold),
    minimumDwellBars: Math.max(0, Math.floor(settings?.minimumDwellBars ?? DEFAULT_REGIME_HYSTERESIS_SETTINGS.minimumDwellBars)),
    transitionConfidenceGap: Math.max(0, settings?.transitionConfidenceGap ?? DEFAULT_REGIME_HYSTERESIS_SETTINGS.transitionConfidenceGap),
    maximumUnknownBars: Math.max(0, Math.floor(settings?.maximumUnknownBars ?? DEFAULT_REGIME_HYSTERESIS_SETTINGS.maximumUnknownBars)),
  };
}

export function defaultRegimeTradingSettings(): RegimeTradingSettings {
  return {
    startingCapital: 25000,
    orderAllocationPercent: 10,
    dailyAllocationPercent: 50,
    riskBudgetPercentOfOrder: 50,
    maxTradesPerDay: 10,
    stopLossPercent: 0.35,
    fixedStopDistanceDollars: 1,
    takeProfitR: 1.5,
    maximumHoldingMinutes: 120,
    slippagePerShare: 0.02,
    useDefaultSizingSettings: true,
    minimumBuyScore: REGIME_MIN_WINNING_SCORE,
    minimumWinningScore: REGIME_MIN_WINNING_SCORE,
    minimumSignalEdge: REGIME_MIN_WINNING_EDGE,
    minimumDirectionalEdge: REGIME_MIN_WINNING_EDGE,
    minimumRegimeConfidence: REGIME_MIN_CONDITION_CONFIDENCE,
    baseRiskPercent: 0.25,
    maxPositionPercent: 50,
    atrStopMultiplier: 2,
    minimumStopDistancePercent: 0.05,
    maxParticipationPercent: 0.3,
    maximumVolumeParticipationPercent: 0.3,
    maxAllowedShares: 0,
    maximumAllowedShares: 0,
    maxDailyLossPercent: 1,
    algorithmDailyLossPercent: 1,
    minimumActiveStrategies: 3,
    minimumIndependentFamilies: REGIME_MIN_INDEPENDENT_FAMILIES,
    maximumAbstentionRate: REGIME_MAX_ABSTENTION_RATE,
    minimumOneMinuteVolume: 0,
    maxSpreadPercent: 0.03,
    pyramidingEnabled: true,
    shortEntriesEnabled: false,
    mlMode: "shadow",
  };
}

export function defaultRegimeSizingDefaults(settings: RegimeTradingSettings = defaultRegimeTradingSettings()): RegimeSizingDefaults {
  if (!settings.useDefaultSizingSettings) {
    return {
      baseRiskPercent: Math.max(0, settings.orderAllocationPercent * (settings.riskBudgetPercentOfOrder / 100)),
      maxPositionPercent: Math.max(0, settings.orderAllocationPercent),
      fixedStopDistanceDollars: fixedStopDistanceDollars(settings.fixedStopDistanceDollars),
      atrStopMultiplier: Math.max(0.01, settings.stopLossPercent / 0.05),
      minimumStopDistancePercent: Math.max(0.0001, settings.stopLossPercent),
      maxParticipationPercent: Math.max(0, settings.maximumVolumeParticipationPercent ?? 1),
      maxAllowedShares: Math.max(0, Math.floor(settings.maximumAllowedShares ?? 0)),
    };
  }
  return {
    baseRiskPercent: Math.max(0, settings.baseRiskPercent),
    maxPositionPercent: Math.max(0, settings.maxPositionPercent),
    fixedStopDistanceDollars: fixedStopDistanceDollars(settings.fixedStopDistanceDollars),
    atrStopMultiplier: Math.max(0.01, settings.atrStopMultiplier),
    minimumStopDistancePercent: Math.max(0, settings.minimumStopDistancePercent),
    maxParticipationPercent: Math.max(0, settings.maximumVolumeParticipationPercent ?? settings.maxParticipationPercent),
    maxAllowedShares: Math.max(0, Math.floor(settings.maximumAllowedShares ?? settings.maxAllowedShares)),
  };
}

export function fixedStopDistanceDollars(value: number): number {
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function clampSetting(value: number | undefined, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fallback;
  }
  return Math.max(0, Math.min(1, value));
}
