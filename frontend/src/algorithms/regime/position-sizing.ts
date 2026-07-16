import { defaultRegimeSizingDefaults, defaultRegimeTradingSettings } from "./config.ts";
import type { RegimeDynamicProfile } from "./dynamic-profile.ts";
import type { RegimeAlgoSignal, RegimeMarketContext, RegimePositionSnapshot, RegimeSizingDefaults, RegimeTradingSettings } from "./types.ts";

export type RegimeQuantityCap = {
  label:
    | "risk"
    | "allocation"
    | "position"
    | "buying_power"
    | "liquidity"
    | "share_limit"
    | "global_risk_capacity";
  quantity: number | null;
};

export type RegimePositionSizingResult = {
  signalStrength: number;
  signalStrengthMultiplier: number;
  sizeMultiplier: number;
  finalQuantity: number;
  requestedQuantityBeforeGlobalCapacity: number;
  riskDollars: number;
  stopDistance: number;
  effectiveTargetR: number;
  targetDistance: number;
  riskBasedQuantity: number;
  allocationBasedQuantity: number;
  positionBasedQuantity: number;
  buyingPowerQuantity: number;
  liquidityBasedQuantity: number;
  shareLimitQuantity: number;
  globalRiskCapacityQuantity: number | null;
  sharesByRisk: number;
  sharesByOrder: number;
  sharesByCapital: number;
  sharesByBuyingPower: number;
  sharesByLiquidity: number;
  availableBuyingPower: number;
  accountEquity: number;
  maxPositionDollars: number;
  currentPositionValue: number;
  limitingFactor: string;
  quantityCaps: RegimeQuantityCap[];
  blockedReason: string;
  blockerCodes: string[];
};

export function calculateRegimePositionSize(
  market: RegimeMarketContext,
  signal: RegimeAlgoSignal,
  winningStrength: number,
  settings: RegimeTradingSettings = defaultRegimeTradingSettings(),
  defaults: RegimeSizingDefaults = defaultRegimeSizingDefaults(settings),
  currentPosition: RegimePositionSnapshot = { marketValue: 0 },
  profile: RegimeDynamicProfile = { riskMultiplier: 1, targetMultiplier: 1, reasonCodes: ["regime.profile.default"] },
): RegimePositionSizingResult {
  const effective = profile.effectiveSettings;
  const accountEquity = positiveFinite(settings.startingCapital);
  const entryPrice = market.latest.close;
  const signalStrength = Math.abs(winningStrength);
  const signalStrengthMultiplier = signalStrengthMultiplierForWinningStrength(signalStrength);
  const newEntriesAllowedMultiplier = effective?.newEntriesAllowed === false ? 0 : 1;
  const sizeMultiplier = signal === "Hold" ? 0 : signalStrengthMultiplier * newEntriesAllowedMultiplier;
  const effectiveRiskPercent = Math.min(defaults.baseRiskPercent, effective?.effectiveRiskPercent ?? defaults.baseRiskPercent);
  const riskDollars = accountEquity * (effectiveRiskPercent / 100) * sizeMultiplier;
  const primaryAtr = market.atr.atr1m ?? (market.atr.atr5m !== null ? market.atr.atr5m / 5 : null);
  const effectiveAtrStopMultiplier = Math.max(defaults.atrStopMultiplier, effective?.effectiveAtrStopMultiplier ?? defaults.atrStopMultiplier);
  const fixedStopFloor = Math.max(0, defaults.fixedStopDistanceDollars);
  const atrStopDistance = primaryAtr !== null && primaryAtr > 0 ? primaryAtr * effectiveAtrStopMultiplier : Number.NaN;
  const priceStopDistance = entryPrice > 0 ? entryPrice * (defaults.minimumStopDistancePercent / 100) : Number.NaN;
  const stopDistance = Math.max(fixedStopFloor, finiteOrZero(atrStopDistance), finiteOrZero(priceStopDistance));
  const maxPositionPercent = Math.min(defaults.maxPositionPercent, effective?.effectiveMaxPositionPercent ?? defaults.maxPositionPercent);
  const orderAllocationPercent = Math.min(settings.orderAllocationPercent, effective?.effectiveOrderAllocationPercent ?? settings.orderAllocationPercent);
  const maxParticipationPercent = Math.min(defaults.maxParticipationPercent, effective?.effectiveMaximumParticipationPercent ?? defaults.maxParticipationPercent);
  const maxPositionDollars = accountEquity * (maxPositionPercent / 100);
  const maxOrderDollars = accountEquity * (orderAllocationPercent / 100);
  const dailyBuyingPowerDollars = accountEquity * (settings.dailyAllocationPercent / 100);
  const availableBuyingPower =
    currentPosition.availableBuyingPower !== undefined
      ? Math.max(0, currentPosition.availableBuyingPower)
      : Math.max(0, Math.min(maxPositionDollars, dailyBuyingPowerDollars) - currentPosition.marketValue);
  const riskBasedQuantity = stopDistance > 0 ? riskDollars / stopDistance : 0;
  const allocationBasedQuantity = entryPrice > 0 ? maxOrderDollars / entryPrice : 0;
  const positionBasedQuantity = entryPrice > 0 ? Math.max(0, maxPositionDollars - currentPosition.marketValue) / entryPrice : 0;
  const buyingPowerQuantity = entryPrice > 0 ? availableBuyingPower / entryPrice : 0;
  const liquidityBasedQuantity = maxParticipationPercent > 0 ? market.latest.volume * (maxParticipationPercent / 100) : Number.POSITIVE_INFINITY;
  const shareLimitQuantity = defaults.maxAllowedShares > 0 ? defaults.maxAllowedShares : Number.POSITIVE_INFINITY;
  const globalRiskCapacityQuantity = currentPosition.globalRiskCapacityQuantity ?? null;
  const quantityCaps: RegimeQuantityCap[] = [
    { label: "risk", quantity: riskBasedQuantity },
    { label: "allocation", quantity: allocationBasedQuantity },
    { label: "position", quantity: positionBasedQuantity },
    { label: "buying_power", quantity: buyingPowerQuantity },
    { label: "liquidity", quantity: liquidityBasedQuantity },
    { label: "share_limit", quantity: shareLimitQuantity },
    { label: "global_risk_capacity", quantity: globalRiskCapacityQuantity },
  ];
  const localCaps = quantityCaps
    .filter((cap) => cap.label !== "global_risk_capacity")
    .map((cap) => ({ ...cap, quantity: cap.quantity === null ? 0 : cap.quantity }))
    .sort((left, right) => (left.quantity ?? 0) - (right.quantity ?? 0));
  const requestedQuantityBeforeGlobalCapacity = Math.floor(Math.max(0, localCaps[0]?.quantity ?? 0));
  const blockerCodes = regimeSizingBlockers({
    signal,
    sizeMultiplier,
    requestedQuantityBeforeGlobalCapacity,
    stopDistance,
    entryPrice,
    primaryAtr,
    effectiveAtrStopMultiplier,
    market,
    settings,
    currentPosition,
    riskDollars,
    globalRiskCapacityQuantity,
  });
  const finalQuantity = blockerCodes.length ? 0 : requestedQuantityBeforeGlobalCapacity;
  const effectiveTargetR = (settings.takeProfitR * (profile.targetMultiplier || 1));
  const targetDistance = stopDistance * Math.max(0, effectiveTargetR);
  const limitingFactor = localCaps[0]?.label ?? "risk";
  return {
    signalStrength,
    signalStrengthMultiplier,
    sizeMultiplier,
    finalQuantity,
    requestedQuantityBeforeGlobalCapacity,
    riskDollars,
    stopDistance,
    effectiveTargetR,
    targetDistance,
    riskBasedQuantity,
    allocationBasedQuantity,
    positionBasedQuantity,
    buyingPowerQuantity,
    liquidityBasedQuantity,
    shareLimitQuantity,
    globalRiskCapacityQuantity,
    sharesByRisk: riskBasedQuantity,
    sharesByOrder: allocationBasedQuantity,
    sharesByCapital: positionBasedQuantity,
    sharesByBuyingPower: buyingPowerQuantity,
    sharesByLiquidity: liquidityBasedQuantity,
    availableBuyingPower,
    accountEquity,
    maxPositionDollars,
    currentPositionValue: currentPosition.marketValue,
    limitingFactor,
    quantityCaps,
    blockedReason: blockerCodes.join(", "),
    blockerCodes,
  };
}

export function signalStrengthMultiplierForWinningStrength(winningStrength: number): number {
  if (!Number.isFinite(winningStrength) || winningStrength < 0.5) return 0;
  if (winningStrength < 0.6) return 0.25;
  if (winningStrength < 0.7) return 0.5;
  if (winningStrength < 0.8) return 0.75;
  return 1;
}

export function calculateRegimePositionSizing(
  market: RegimeMarketContext,
  signal: RegimeAlgoSignal,
  normalizedNetScore: number,
  settings: RegimeTradingSettings = defaultRegimeTradingSettings(),
  defaults: RegimeSizingDefaults = defaultRegimeSizingDefaults(settings),
  currentPosition: RegimePositionSnapshot = { marketValue: 0 },
): RegimePositionSizingResult {
  return calculateRegimePositionSize(market, signal, normalizedNetScore, settings, defaults, currentPosition);
}

function regimeSizingBlockers(input: {
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

function positiveFinite(value: number): number {
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function finiteOrZero(value: number): number {
  return Number.isFinite(value) ? value : 0;
}
