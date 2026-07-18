import { defaultRegimeSizingDefaults, defaultRegimeTradingSettings } from "../config.ts";
import type { RegimeDynamicProfile } from "../dynamic-profile.ts";
import type { RegimeAlgoSignal, RegimeMarketContext, RegimePositionSnapshot, RegimeSizingDefaults, RegimeTradingSettings } from "../types.ts";
import { regimePositionAndBuyingPowerCaps } from "./exposure-cap.ts";
import { regimeLiquidityCap } from "./liquidity-cap.ts";
import { regimeRiskBudget, regimeSizingBlockers, signalStrengthMultiplierForWinningStrength } from "./risk-budget.ts";
import { regimeStopDistance } from "./stop-calculation.ts";
import { regimeTargetDistance } from "./target-calculation.ts";

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
  const riskDollars = regimeRiskBudget(accountEquity, effectiveRiskPercent, sizeMultiplier);
  const primaryAtr = market.atr.atr1m ?? (market.atr.atr5m !== null ? market.atr.atr5m / 5 : null);
  const effectiveAtrStopMultiplier = Math.max(defaults.atrStopMultiplier, effective?.effectiveAtrStopMultiplier ?? defaults.atrStopMultiplier);
  const stopDistance = regimeStopDistance(entryPrice, primaryAtr, effectiveAtrStopMultiplier, defaults);
  const maxPositionPercent = Math.min(defaults.maxPositionPercent, effective?.effectiveMaxPositionPercent ?? defaults.maxPositionPercent);
  const orderAllocationPercent = Math.min(settings.orderAllocationPercent, effective?.effectiveOrderAllocationPercent ?? settings.orderAllocationPercent);
  const maxParticipationPercent = Math.min(defaults.maxParticipationPercent, effective?.effectiveMaximumParticipationPercent ?? defaults.maxParticipationPercent);
  const exposureCaps = regimePositionAndBuyingPowerCaps({
    accountEquity,
    entryPrice,
    maxPositionPercent,
    orderAllocationPercent,
    dailyAllocationPercent: settings.dailyAllocationPercent,
    currentPosition,
  });
  const maxPositionDollars = exposureCaps.maxPositionDollars;
  const availableBuyingPower = exposureCaps.availableBuyingPower;
  const riskBasedQuantity = stopDistance > 0 ? riskDollars / stopDistance : 0;
  const allocationBasedQuantity = exposureCaps.allocationBasedQuantity;
  const positionBasedQuantity = exposureCaps.positionBasedQuantity;
  const buyingPowerQuantity = exposureCaps.buyingPowerQuantity;
  const liquidityBasedQuantity = regimeLiquidityCap(market.latest.volume, maxParticipationPercent);
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
  const effectiveTargetR = settings.takeProfitR * (profile.targetMultiplier || 1);
  const targetDistance = regimeTargetDistance(stopDistance, effectiveTargetR);
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

function positiveFinite(value: number): number {
  return Number.isFinite(value) && value > 0 ? value : 0;
}
