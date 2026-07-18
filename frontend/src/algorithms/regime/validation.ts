import { defaultRegimeTradingSettings, resolveRegimeHysteresisSettings } from "./config.ts";
import { REGIME_ALGORITHM_ID, REGIME_IDENTITY_CONTRACT_FILES } from "./versions.ts";
import type { RegimeHysteresisSettings, RegimeTradingSettings } from "./types.ts";
import type { RegimeMlMode } from "./ml/types.ts";

export type RegimeContractValidationResult = {
  readonly algorithmId: typeof REGIME_ALGORITHM_ID;
  readonly files: typeof REGIME_IDENTITY_CONTRACT_FILES;
  readonly valid: boolean;
};

export function validateRegimeIdentityContracts(): RegimeContractValidationResult {
  return Object.freeze({
    algorithmId: REGIME_ALGORITHM_ID,
    files: REGIME_IDENTITY_CONTRACT_FILES,
    valid: REGIME_IDENTITY_CONTRACT_FILES.length === 5,
  });
}

export function validateRegimeHysteresisSettings(settings?: Partial<RegimeHysteresisSettings>): RegimeHysteresisSettings {
  return resolveRegimeHysteresisSettings(settings);
}

export function validateRegimeTradingSettings(settings: Partial<RegimeTradingSettings> = {}): RegimeTradingSettings {
  const defaults = defaultRegimeTradingSettings();
  const merged: RegimeTradingSettings = { ...defaults, ...settings };
  return {
    ...merged,
    startingCapital: nonNegative(merged.startingCapital, defaults.startingCapital),
    orderAllocationPercent: nonNegative(merged.orderAllocationPercent, defaults.orderAllocationPercent),
    dailyAllocationPercent: nonNegative(merged.dailyAllocationPercent, defaults.dailyAllocationPercent),
    riskBudgetPercentOfOrder: bounded(merged.riskBudgetPercentOfOrder, 0, 100, defaults.riskBudgetPercentOfOrder),
    maxTradesPerDay: wholeNonNegative(merged.maxTradesPerDay, defaults.maxTradesPerDay),
    maximumHoldingMinutes: wholePositive(merged.maximumHoldingMinutes, defaults.maximumHoldingMinutes ?? 120),
    stopLossPercent: nonNegative(merged.stopLossPercent, defaults.stopLossPercent),
    fixedStopDistanceDollars: nonNegative(merged.fixedStopDistanceDollars, defaults.fixedStopDistanceDollars),
    takeProfitR: positive(merged.takeProfitR, defaults.takeProfitR),
    slippagePerShare: nonNegative(merged.slippagePerShare, defaults.slippagePerShare),
    minimumBuyScore: bounded(merged.minimumBuyScore, 0, 1, defaults.minimumBuyScore),
    minimumWinningScore: bounded(merged.minimumWinningScore, 0, 1, defaults.minimumWinningScore ?? defaults.minimumBuyScore),
    minimumSignalEdge: bounded(merged.minimumSignalEdge, 0, 1, defaults.minimumSignalEdge),
    minimumDirectionalEdge: bounded(merged.minimumDirectionalEdge, 0, 1, defaults.minimumDirectionalEdge ?? defaults.minimumSignalEdge),
    minimumRegimeConfidence: bounded(merged.minimumRegimeConfidence, 0, 1, defaults.minimumRegimeConfidence ?? 0.65),
    baseRiskPercent: nonNegative(merged.baseRiskPercent, defaults.baseRiskPercent),
    maxPositionPercent: nonNegative(merged.maxPositionPercent, defaults.maxPositionPercent),
    atrStopMultiplier: positive(merged.atrStopMultiplier, defaults.atrStopMultiplier),
    minimumStopDistancePercent: nonNegative(merged.minimumStopDistancePercent, defaults.minimumStopDistancePercent),
    maxParticipationPercent: nonNegative(merged.maxParticipationPercent, defaults.maxParticipationPercent),
    maximumVolumeParticipationPercent: nonNegative(merged.maximumVolumeParticipationPercent, defaults.maximumVolumeParticipationPercent ?? defaults.maxParticipationPercent),
    maxAllowedShares: wholeNonNegative(merged.maxAllowedShares, defaults.maxAllowedShares),
    maximumAllowedShares: wholeNonNegative(merged.maximumAllowedShares, defaults.maximumAllowedShares ?? defaults.maxAllowedShares),
    maxDailyLossPercent: nonNegative(merged.maxDailyLossPercent, defaults.maxDailyLossPercent),
    algorithmDailyLossPercent: nonNegative(merged.algorithmDailyLossPercent, defaults.algorithmDailyLossPercent ?? defaults.maxDailyLossPercent),
    minimumActiveStrategies: Math.max(1, wholeNonNegative(merged.minimumActiveStrategies, defaults.minimumActiveStrategies)),
    minimumIndependentFamilies: Math.max(1, wholeNonNegative(merged.minimumIndependentFamilies, defaults.minimumIndependentFamilies ?? 2)),
    maximumAbstentionRate: bounded(merged.maximumAbstentionRate, 0, 1, defaults.maximumAbstentionRate ?? 0.6),
    minimumOneMinuteVolume: nonNegative(merged.minimumOneMinuteVolume, defaults.minimumOneMinuteVolume),
    maxSpreadPercent: nonNegative(merged.maxSpreadPercent, defaults.maxSpreadPercent),
    mlMode: validMlMode(merged.mlMode) ? merged.mlMode : defaults.mlMode,
  };
}

function bounded(value: number | undefined, min: number, max: number, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, value));
}

function nonNegative(value: number | undefined, fallback: number): number {
  return bounded(value, 0, Number.POSITIVE_INFINITY, fallback);
}

function positive(value: number | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : fallback;
}

function wholeNonNegative(value: number | undefined, fallback: number): number {
  return Math.max(0, Math.floor(nonNegative(value, fallback)));
}

function wholePositive(value: number | undefined, fallback: number): number {
  return Math.max(1, Math.floor(positive(value, fallback)));
}

function validMlMode(value: RegimeMlMode | undefined): value is RegimeMlMode {
  return value === "off" || value === "shadow" || value === "confirm_only" || value === "active";
}

