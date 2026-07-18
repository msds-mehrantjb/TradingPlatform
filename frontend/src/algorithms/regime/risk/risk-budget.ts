import type { RegimeAlgoSignal, RegimeMarketContext, RegimePositionSnapshot, RegimeTradingSettings } from "../types.ts";

export function regimeRiskBudget(accountEquity: number, effectiveRiskPercent: number, sizeMultiplier: number): number {
  return Math.max(0, accountEquity) * (Math.max(0, effectiveRiskPercent) / 100) * Math.max(0, sizeMultiplier);
}

export function signalStrengthMultiplierForWinningStrength(winningStrength: number): number {
  if (!Number.isFinite(winningStrength) || winningStrength < 0.5) return 0;
  if (winningStrength < 0.6) return 0.25;
  if (winningStrength < 0.7) return 0.5;
  if (winningStrength < 0.8) return 0.75;
  return 1;
}

export function regimeSizingBlockers(input: {
  signal: RegimeAlgoSignal;
  sizeMultiplier: number;
  requestedQuantityBeforeGlobalCapacity: number;
  stopDistance: number;
  entryPrice: number;
  primaryAtr: number | null;
  effectiveAtrStopMultiplier: number;
  market: RegimeMarketContext;
  settings: RegimeTradingSettings;
  currentPosition: RegimePositionSnapshot;
  riskDollars: number;
  globalRiskCapacityQuantity: number | null;
}): string[] {
  const blockers: string[] = [];
  if (input.signal === "Hold") blockers.push("regime.sizing.hold_signal");
  if (input.sizeMultiplier <= 0) blockers.push("regime.sizing.signal_strength_too_low");
  if (!Number.isFinite(input.requestedQuantityBeforeGlobalCapacity) || input.requestedQuantityBeforeGlobalCapacity <= 0) blockers.push("regime.sizing.quantity_zero_or_invalid");
  if (!Number.isFinite(input.stopDistance) || input.stopDistance <= 0) blockers.push("regime.sizing.invalid_stop_distance");
  if (!Number.isFinite(input.entryPrice) || input.entryPrice <= 0) blockers.push("regime.sizing.invalid_entry_price");
  if (input.effectiveAtrStopMultiplier > 0 && (input.primaryAtr === null || input.primaryAtr <= 0)) blockers.push("regime.sizing.atr_unavailable");
  if (input.market.latest.volume < (input.settings.minimumOneMinuteVolume ?? 0)) blockers.push("regime.sizing.volume_below_minimum");
  if (input.currentPosition.requireSpreadEstimate && !Number.isFinite(input.market.spreadLiquidity.spreadPercent)) blockers.push("regime.sizing.spread_estimate_unavailable");
  if (input.currentPosition.remainingAlgorithmRiskDollars !== undefined && input.riskDollars > input.currentPosition.remainingAlgorithmRiskDollars) blockers.push("regime.sizing.algorithm_risk_capacity_exceeded");
  if (input.globalRiskCapacityQuantity === null) blockers.push("regime.sizing.global_capacity_unavailable");
  return blockers;
}
