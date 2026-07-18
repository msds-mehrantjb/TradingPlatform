import type { OrderIntentSide, PositionEffect } from "../../trading/shared/order-intent-types.ts";
import { defaultRegimeSizingDefaults, defaultRegimeTradingSettings } from "./config.ts";
import { resolveRegimeDecision } from "./decision-engine.ts";
import { resolveRegimeDynamicProfile } from "./dynamic-profile.ts";
import { calculateRegimePositionSize, type RegimePositionSizingResult } from "./position-sizing.ts";
import type { MarketRegimeId, RegimeMarketContext, RegimePositionSnapshot, RegimeSelectionResult, RegimeSizingDefaults, RegimeTradingSettings } from "./types.ts";
import { REGIME_ALGORITHM_ID, REGIME_ALGORITHM_VERSION, REGIME_PROFILE_VERSION, REGIME_SETTINGS_VERSION } from "./versions.ts";

export { REGIME_ALGORITHM_VERSION };
export const REGIME_ORDER_INTENT_VERSION = "regime_v2_order_intent_v1";
const DEFAULT_REGIME_BASE_SETTINGS_VERSION = REGIME_SETTINGS_VERSION;
const DEFAULT_REGIME_PROFILE_VERSION = REGIME_PROFILE_VERSION;

export type RegimeOrderIntentOptions = {
  currentPosition?: number;
  shortTradingEnabled?: boolean;
  accountShortPermission?: boolean;
  assetShortable?: boolean;
  borrowAvailable?: boolean;
  buyingPowerAvailable?: boolean;
  shortSaleRestrictionActive?: boolean;
  algorithmVersion?: string;
  expectedEntryPrice?: number;
  protectiveStopPrice?: number;
  targetPrice?: number;
  requestedRiskDollars?: number;
  baseSettingsVersion?: string;
  effectiveProfileId?: string;
  profileVersion?: string;
  strategyIds?: string[];
  familyScores?: Record<string, number>;
  marketDataTimestamp?: string;
  generatedAt?: string;
  expiresAt?: string;
  reasons?: string[];
};

export interface RegimeOrderIntent {
  readonly decisionId: string;
  readonly algorithmId: "regime";
  readonly algorithmVersion: string;
  readonly symbol: string;
  readonly signal: "Buy" | "Sell";
  readonly positionEffect: PositionEffect;
  readonly requestedQuantity: number;
  readonly expectedEntryPrice: number;
  readonly protectiveStopPrice: number;
  readonly targetPrice: number;
  readonly requestedRiskDollars: number;
  readonly confirmedRegime: MarketRegimeId;
  readonly regimeConfidence: number;
  readonly winningScore: number;
  readonly directionalEdge: number;
  readonly baseSettingsVersion: string;
  readonly effectiveProfileId: string;
  readonly strategyIds: readonly string[];
  readonly familyScores: Readonly<Record<string, number>>;
  readonly marketDataTimestamp: string;
  readonly generatedAt: string;
  readonly expiresAt: string;
  readonly reasons: readonly string[];
}

export type RegimeTargetOrder = {
  eligible: boolean;
  side: OrderIntentSide;
  signalDirection: OrderIntentSide;
  positionEffect: PositionEffect;
  currentPosition: number;
  requestedResultingPosition: number;
  orderType: string;
  symbol: string;
  quantity: number;
  triggerPrice: number | null;
  limitPrice: number | null;
  stopPrice: number | null;
  targetPrice: number | null;
  accountBalance: number;
  orderLimitDollars: number;
  dailyLimitDollars: number;
  riskDollars: number;
  orderNotional: number;
  plannedStopRiskDollars: number;
  orderIntent: RegimeOrderIntent | null;
  decisionId: string | null;
  estimatedSlippage: number;
  timeInForce: "Day";
  cutoff: string;
  profileId: string | null;
  failedGates: string[];
  sizing: RegimePositionSizingResult;
  summary: string;
};

export function buildRegimeOrderIntent(
  result: RegimeSelectionResult,
  symbol: string,
  quantity: number,
  options: RegimeOrderIntentOptions = {},
): RegimeOrderIntent | null {
  const signalDirection: OrderIntentSide = result.signal === "Buy" || result.signal === "Sell" ? result.signal : "Hold";
  const currentPosition = Math.trunc(options.currentPosition ?? 0);
  const positionEffect = resolveRegimePositionEffect(signalDirection, currentPosition, options.shortTradingEnabled === true);
  const requestedQuantity = Math.max(0, Math.floor(quantity));
  const shortGateReasons = positionEffect === "enter_short" ? shortSaleGateReasons(options) : [];
  const expectedEntryPrice = finitePositive(options.expectedEntryPrice);
  const protectiveStopPrice = finitePositive(options.protectiveStopPrice);
  const targetPrice = finitePositive(options.targetPrice);
  const requestedRiskDollars = finiteNonNegative(options.requestedRiskDollars);
  const blockers = [
    ...(result.tradeAllowed ? [] : ["regime.order_intent.trade_not_allowed", ...result.tradeBlockers]),
    ...(signalDirection === "Hold" ? ["regime.order_intent.no_direction"] : []),
    ...(positionEffect === "none" ? ["regime.order_intent.no_position_effect"] : []),
    ...(requestedQuantity > 0 ? [] : ["regime.order_intent.quantity_zero"]),
    ...(expectedEntryPrice > 0 ? [] : ["regime.order_intent.invalid_entry_price"]),
    ...(protectiveStopPrice > 0 ? [] : ["regime.order_intent.invalid_protective_stop"]),
    ...(targetPrice > 0 ? [] : ["regime.order_intent.invalid_target"]),
    ...shortGateReasons,
  ];
  if (blockers.length > 0 || signalDirection === "Hold") {
    return null;
  }

  const marketDataTimestamp = options.marketDataTimestamp ?? result.confirmedState?.timestamp ?? result.rawClassification?.timestamp ?? options.generatedAt ?? new Date(0).toISOString();
  const generatedAt = options.generatedAt ?? marketDataTimestamp;
  const expiresAt = options.expiresAt ?? addMinutesIso(generatedAt, 5);
  const baseSettingsVersion = options.baseSettingsVersion ?? result.effectiveSettings?.baseSettingsVersion ?? DEFAULT_REGIME_BASE_SETTINGS_VERSION;
  const profileVersion = options.profileVersion ?? result.effectiveSettings?.profileVersion ?? DEFAULT_REGIME_PROFILE_VERSION;
  const effectiveProfileId = options.effectiveProfileId ?? result.effectiveSettings?.profileId ?? `${confirmedRegimeId(result)}:${profileVersion}`;
  const decisionId = generateRegimeOrderIntentIdempotencyKey({
    algorithmId: REGIME_ALGORITHM_ID,
    symbol,
    decisionCandle: marketDataTimestamp,
    positionEffect,
    settingsVersion: baseSettingsVersion,
    profileVersion,
  });
  const strategyIds = options.strategyIds ?? result.selectedStrategies.map((strategy) => strategy.strategy);
  const familyScores = options.familyScores ?? familyScoreRecord(result);
  const intent: RegimeOrderIntent = {
    decisionId,
    algorithmId: REGIME_ALGORITHM_ID,
    algorithmVersion: options.algorithmVersion ?? REGIME_ALGORITHM_VERSION,
    symbol,
    signal: signalDirection,
    positionEffect,
    requestedQuantity,
    expectedEntryPrice,
    protectiveStopPrice,
    targetPrice,
    requestedRiskDollars,
    confirmedRegime: confirmedRegimeId(result),
    regimeConfidence: result.confirmedState?.confirmedConfidence ?? result.confidence,
    winningScore: result.winningScore,
    directionalEdge: result.directionalEdge ?? result.winningDirectionEdge ?? result.scoreEdge,
    baseSettingsVersion,
    effectiveProfileId,
    strategyIds: Object.freeze(strategyIds.slice()),
    familyScores: Object.freeze({ ...familyScores }),
    marketDataTimestamp,
    generatedAt,
    expiresAt,
    reasons: Object.freeze([`regime.order_intent.${positionEffect}`, ...result.reasons, ...(options.reasons ?? [])]),
  };
  return Object.freeze(intent);
}

export function generateRegimeOrderIntentIdempotencyKey(input: {
  algorithmId?: "regime";
  symbol: string;
  decisionCandle: string;
  positionEffect: PositionEffect;
  settingsVersion: string;
  profileVersion: string;
}): string {
  const parts = [
    input.algorithmId ?? "regime",
    input.symbol.toUpperCase(),
    input.decisionCandle,
    input.positionEffect,
    input.settingsVersion,
    input.profileVersion,
  ];
  return parts.map(stableKeyPart).join(":");
}

export function buildRegimeTargetOrder(
  result: RegimeSelectionResult,
  market: RegimeMarketContext | null,
  symbol: string,
  settings: RegimeTradingSettings = defaultRegimeTradingSettings(),
  defaults: RegimeSizingDefaults = defaultRegimeSizingDefaults(settings),
  currentPosition: RegimePositionSnapshot = { marketValue: 0 },
  options: RegimeOrderIntentOptions = {},
): RegimeTargetOrder {
  const resolved = resolveRegimeDecision(result);
  const profile = resolveRegimeDynamicProfile(result, market, settings);
  const sizing = market
    ? calculateRegimePositionSize(market, resolved.signal, resolved.signedNetScore, settings, defaults, currentPosition, profile)
    : emptyRegimePositionSizing(settings, "Waiting for session candles");
  const latestPrice = market?.latest.close ?? 0;
  const currentShares = currentPosition.shares ?? 0;
  const signalDirection: OrderIntentSide = resolved.signal === "Buy" || resolved.signal === "Sell" ? resolved.signal : "Hold";
  const positionEffect = resolveRegimePositionEffect(signalDirection, currentShares, options.shortTradingEnabled === true);
  const pricingSide = signalDirection === "Sell" ? "Sell" : "Buy";
  const triggerPrice =
    signalDirection === "Hold" || latestPrice <= 0 || sizing.finalQuantity <= 0
      ? null
      : pricingSide === "Buy"
        ? roundCurrency(latestPrice + settings.slippagePerShare)
        : roundCurrency(Math.max(0, latestPrice - settings.slippagePerShare));
  const limitPrice =
    triggerPrice === null
      ? null
      : pricingSide === "Buy"
        ? roundCurrency(triggerPrice + settings.slippagePerShare)
        : roundCurrency(Math.max(0, triggerPrice - settings.slippagePerShare));
  const stopPrice =
    triggerPrice === null
      ? null
      : pricingSide === "Buy"
        ? roundCurrency(Math.max(0, triggerPrice - sizing.stopDistance))
        : roundCurrency(triggerPrice + sizing.stopDistance);
  const effective = profile.effectiveSettings;
  const targetR = effective?.effectiveTakeProfitR ?? settings.takeProfitR;
  const targetDistance = sizing.finalQuantity > 0 ? sizing.targetDistance || sizing.stopDistance * targetR : 0;
  const targetPrice =
    triggerPrice === null
      ? null
      : pricingSide === "Buy"
        ? roundCurrency(triggerPrice + targetDistance)
        : roundCurrency(Math.max(0, triggerPrice - targetDistance));
  const intent = buildRegimeOrderIntent(result, symbol, sizing.finalQuantity, {
    ...options,
    currentPosition: currentShares,
    expectedEntryPrice: triggerPrice ?? 0,
    protectiveStopPrice: stopPrice ?? 0,
    targetPrice: targetPrice ?? 0,
    requestedRiskDollars: sizing.riskDollars,
    marketDataTimestamp: market?.latest.timestamp,
    baseSettingsVersion: effective?.baseSettingsVersion,
    effectiveProfileId: effective?.profileId,
    profileVersion: effective?.profileVersion,
    reasons: sizing.blockerCodes,
  });
  const signedQuantity = signedQuantityForEffect(positionEffect, sizing.finalQuantity, currentShares);
  const requestedResultingPosition = currentShares + (intent ? signedQuantity : 0);
  const orderNotional = triggerPrice !== null ? roundCurrency((intent?.requestedQuantity ?? 0) * triggerPrice) : 0;
  const plannedStopRiskDollars = triggerPrice !== null && stopPrice !== null ? roundCurrency((intent?.requestedQuantity ?? 0) * Math.abs(triggerPrice - stopPrice)) : 0;
  const estimatedSlippage = roundCurrency((intent?.requestedQuantity ?? 0) * settings.slippagePerShare * 2);
  const failedGates = intent ? [] : regimeOrderIntentFailureReasons(result, signalDirection, positionEffect, sizing.finalQuantity, triggerPrice, stopPrice, targetPrice, options);
  const orderType = intent ? `${intent.signal} stop-limit` : "No order";
  return {
    eligible: intent !== null,
    side: intent?.signal ?? "Hold",
    signalDirection,
    positionEffect,
    currentPosition: currentShares,
    requestedResultingPosition,
    orderType,
    symbol,
    quantity: intent?.requestedQuantity ?? 0,
    triggerPrice,
    limitPrice,
    stopPrice,
    targetPrice,
    accountBalance: sizing.accountEquity,
    orderLimitDollars: sizing.accountEquity * ((effective?.effectiveOrderAllocationPercent ?? settings.orderAllocationPercent) / 100),
    dailyLimitDollars: sizing.availableBuyingPower + sizing.currentPositionValue,
    riskDollars: sizing.riskDollars,
    orderNotional,
    plannedStopRiskDollars,
    orderIntent: intent,
    decisionId: intent?.decisionId ?? null,
    estimatedSlippage,
    timeInForce: "Day",
    cutoff: "No new Regime entries after 15:30 ET",
    profileId: effective?.profileId ?? null,
    failedGates,
    sizing,
    summary: intent
      ? `${orderType} ${symbol}, ${intent.requestedQuantity} shares, trigger ${formatCurrency(triggerPrice)}, limit ${formatCurrency(limitPrice)}, stop ${formatCurrency(stopPrice)}, target ${formatCurrency(targetPrice)}.`
      : `No order: ${failedGates.join(", ") || "Regime final signal is Hold"}.`,
  };
}

export function resolveRegimePositionEffect(
  signalDirection: OrderIntentSide,
  currentPosition: number,
  shortTradingEnabled: boolean,
): PositionEffect {
  if (signalDirection === "Buy") {
    return currentPosition < 0 ? "cover_short" : "enter_long";
  }
  if (signalDirection === "Sell") {
    if (currentPosition > 0) {
      return "exit_long";
    }
    return shortTradingEnabled ? "enter_short" : "none";
  }
  return "none";
}

function shortSaleGateReasons(options: RegimeOrderIntentOptions): string[] {
  const reasons: string[] = [];
  if (!options.shortTradingEnabled) {
    reasons.push("regime.short.disabled");
  }
  if (!options.accountShortPermission) {
    reasons.push("regime.short.account_permission_missing");
  }
  if (!options.assetShortable) {
    reasons.push("regime.short.asset_not_shortable");
  }
  if (options.borrowAvailable === false) {
    reasons.push("regime.short.borrow_unavailable");
  }
  if (options.buyingPowerAvailable === false) {
    reasons.push("regime.short.buying_power_unavailable");
  }
  if (options.shortSaleRestrictionActive) {
    reasons.push("regime.short.short_sale_restriction_active");
  }
  return reasons;
}

function regimeOrderIntentFailureReasons(
  result: RegimeSelectionResult,
  signalDirection: OrderIntentSide,
  positionEffect: PositionEffect,
  requestedQuantity: number,
  expectedEntryPrice: number | null,
  protectiveStopPrice: number | null,
  targetPrice: number | null,
  options: RegimeOrderIntentOptions,
): string[] {
  return [
    "regime.order_intent.blocked",
    ...(result.tradeAllowed ? [] : ["regime.order_intent.trade_not_allowed", ...result.tradeBlockers]),
    ...(signalDirection === "Hold" ? ["regime.order_intent.no_direction"] : []),
    ...(positionEffect === "none" ? ["regime.order_intent.no_position_effect"] : []),
    ...(requestedQuantity > 0 ? [] : ["regime.order_intent.quantity_zero"]),
    ...(finitePositive(expectedEntryPrice ?? 0) > 0 ? [] : ["regime.order_intent.invalid_entry_price"]),
    ...(finitePositive(protectiveStopPrice ?? 0) > 0 ? [] : ["regime.order_intent.invalid_protective_stop"]),
    ...(finitePositive(targetPrice ?? 0) > 0 ? [] : ["regime.order_intent.invalid_target"]),
    ...shortSaleGateReasons(options),
  ];
}

function signedQuantityForEffect(positionEffect: PositionEffect, requestedQuantity: number, currentPosition: number): number {
  if (positionEffect === "enter_long") {
    return requestedQuantity;
  }
  if (positionEffect === "exit_long") {
    return -Math.min(requestedQuantity, Math.max(0, currentPosition));
  }
  if (positionEffect === "enter_short") {
    return -requestedQuantity;
  }
  if (positionEffect === "cover_short") {
    return Math.min(requestedQuantity, Math.abs(Math.min(0, currentPosition)));
  }
  return 0;
}

function confirmedRegimeId(result: RegimeSelectionResult): MarketRegimeId {
  return result.confirmedState?.confirmedRegime ?? result.rawClassification?.rawRegime ?? "no_trade";
}

function familyScoreRecord(result: RegimeSelectionResult): Record<string, number> {
  const scores: Record<string, number> = {};
  for (const family of result.familyScores ?? []) {
    scores[family.family] = roundCurrency(family.buyScore - family.sellScore);
  }
  return scores;
}

function finitePositive(value: number | undefined): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0;
}

function finiteNonNegative(value: number | undefined): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

function addMinutesIso(timestamp: string, minutes: number): string {
  const time = new Date(timestamp).getTime();
  if (!Number.isFinite(time)) {
    return new Date(0).toISOString();
  }
  return new Date(time + minutes * 60_000).toISOString();
}

function stableKeyPart(value: string): string {
  return value.trim().replace(/[^A-Za-z0-9_.-]+/g, "_") || "unknown";
}

function emptyRegimePositionSizing(settings: RegimeTradingSettings, blockedReason: string): RegimePositionSizingResult {
  return {
    signalStrength: 0,
    signalStrengthMultiplier: 0,
    sizeMultiplier: 0,
    finalQuantity: 0,
    requestedQuantityBeforeGlobalCapacity: 0,
    riskDollars: 0,
    stopDistance: 0,
    effectiveTargetR: settings.takeProfitR,
    targetDistance: 0,
    riskBasedQuantity: 0,
    allocationBasedQuantity: 0,
    positionBasedQuantity: 0,
    buyingPowerQuantity: 0,
    liquidityBasedQuantity: 0,
    shareLimitQuantity: 0,
    globalRiskCapacityQuantity: null,
    sharesByRisk: 0,
    sharesByOrder: 0,
    sharesByCapital: 0,
    sharesByBuyingPower: 0,
    sharesByLiquidity: 0,
    availableBuyingPower: 0,
    accountEquity: settings.startingCapital,
    maxPositionDollars: 0,
    currentPositionValue: 0,
    limitingFactor: "sizing",
    quantityCaps: [
      { label: "risk", quantity: 0 },
      { label: "allocation", quantity: 0 },
      { label: "position", quantity: 0 },
      { label: "buying_power", quantity: 0 },
      { label: "liquidity", quantity: 0 },
      { label: "share_limit", quantity: 0 },
      { label: "global_risk_capacity", quantity: null },
    ],
    blockedReason,
    blockerCodes: [blockedReason],
  };
}

function roundCurrency(value: number): number {
  return Math.round(value * 100) / 100;
}

function formatCurrency(value: number | null): string {
  return value === null ? "NA" : `$${value.toFixed(2)}`;
}
