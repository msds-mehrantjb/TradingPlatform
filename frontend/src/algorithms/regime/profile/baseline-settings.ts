import { defaultRegimeTradingSettings } from "../config.ts";
import type { RegimeBaseSettings, RegimeTradingSettings } from "../types.ts";

export function baseRegimeSettingsFromTradingSettings(settings: RegimeTradingSettings = defaultRegimeTradingSettings()): RegimeBaseSettings {
  return {
    startingCapital: settings.startingCapital,
    orderAllocationPercent: settings.orderAllocationPercent,
    dailyAllocationPercent: settings.dailyAllocationPercent,
    baseRiskPercent: settings.baseRiskPercent,
    maxPositionPercent: settings.maxPositionPercent,
    maxTradesPerDay: settings.maxTradesPerDay,
    minimumWinningScore: settings.minimumWinningScore ?? settings.minimumBuyScore,
    minimumDirectionalEdge: settings.minimumDirectionalEdge ?? settings.minimumSignalEdge,
    minimumRegimeConfidence: settings.minimumRegimeConfidence ?? 0.65,
    minimumActiveStrategies: settings.minimumActiveStrategies,
    minimumIndependentFamilies: settings.minimumIndependentFamilies ?? 2,
    fixedStopDistanceDollars: settings.fixedStopDistanceDollars,
    atrStopMultiplier: settings.atrStopMultiplier,
    minimumStopDistancePercent: settings.minimumStopDistancePercent,
    takeProfitR: settings.takeProfitR,
    maximumHoldingMinutes: settings.maximumHoldingMinutes ?? 120,
    maximumVolumeParticipationPercent: settings.maximumVolumeParticipationPercent ?? settings.maxParticipationPercent,
    minimumOneMinuteVolume: settings.minimumOneMinuteVolume,
    maximumAllowedShares: settings.maximumAllowedShares ?? settings.maxAllowedShares,
    algorithmDailyLossPercent: settings.algorithmDailyLossPercent ?? settings.maxDailyLossPercent,
    pyramidingEnabled: settings.pyramidingEnabled,
    shortEntriesEnabled: settings.shortEntriesEnabled ?? false,
    slippagePerShare: settings.slippagePerShare,
  };
}
