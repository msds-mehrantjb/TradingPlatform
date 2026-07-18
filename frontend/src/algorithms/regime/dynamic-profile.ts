import { defaultRegimeTradingSettings } from "./config.ts";
import { clampNumber, roundNumber } from "./indicators.ts";
import { REGIME_PROFILE_VERSION, REGIME_SETTINGS_VERSION } from "./versions.ts";
import type {
  EffectiveRegimeSettings,
  MarketRegimeId,
  RegimeBaseSettings,
  RegimeMarketContext,
  RegimeProfileModifierBreakdown,
  RegimeProfileModifiers,
  RegimeSelectionResult,
  RegimeTradingSettings,
} from "./types.ts";

export { REGIME_PROFILE_VERSION };

export type RegimeDynamicProfile = {
  riskMultiplier: number;
  targetMultiplier: number;
  reasonCodes: string[];
  effectiveSettings?: EffectiveRegimeSettings;
  modifiers?: RegimeProfileModifierBreakdown;
};

type DynamicProfileContext = {
  market?: RegimeMarketContext | null;
  result: RegimeSelectionResult;
  settings?: RegimeTradingSettings;
  baseSettingsVersion?: string;
  accountDrawdownPercent?: number;
  consecutiveLosses?: number;
  accountExposurePercent?: number;
};

const neutralModifier: RegimeProfileModifiers = {
  riskMultiplier: 1,
  allocationMultiplier: 1,
  positionMultiplier: 1,
  liquidityParticipationMultiplier: 1,
  signalSizeMultiplier: 1,
  atrStopMultiplierAdjustment: 0,
  targetRMultiplier: 1,
  winningScoreAdjustment: 0,
  directionalEdgeAdjustment: 0,
  regimeConfidenceAdjustment: 0,
  maximumTradesOverride: null,
  entryCutoffOverride: null,
  pyramidingAllowed: true,
  newEntriesAllowed: true,
  reasons: [],
};

const profileMatrix: Record<MarketRegimeId, RegimeProfileModifiers> = {
  strong_uptrend: profile("strong_trend", 1, 1, 1, 1.1, 1.1, "Strong trend, normal volatility"),
  strong_downtrend: profile("strong_trend", 1, 1, 1, 1.1, 1.1, "Strong trend, normal volatility"),
  weak_uptrend: profile("weak_trend", 0.65, 0.7, 0.7, 1, 1, "Weak trend: selective entries"),
  weak_downtrend: profile("weak_trend", 0.65, 0.7, 0.7, 1, 1, "Weak trend: selective entries"),
  range_bound: profile("range_bound", 0.7, 0.75, 0.75, 0.9, 0.9, "Range-bound: mean reversion only"),
  sideways_range: profile("range_bound", 0.7, 0.75, 0.75, 0.9, 0.9, "Sideways range: mean reversion only"),
  opening_breakout: profile("breakout_expansion", 0.6, 0.7, 0.7, 1.25, 1.2, "Breakout expansion requires confirmation"),
  intraday_expansion: profile("breakout_expansion", 0.6, 0.7, 0.7, 1.25, 1.2, "Intraday expansion requires confirmation"),
  high_volatility_trend: profile("high_volatility_trend", 0.5, 0.6, 0.6, 1.35, 1.15, "High-volatility trend: selective entries"),
  low_volatility_quiet: profile("low_volatility_quiet", 0.4, 0.5, 0.5, 0.85, 0.8, "Low-volatility quiet: no breakout chasing"),
  choppy_mixed: { ...profile("choppy_mixed", 0.25, 0.35, 0.35, 1, 0.8, "Choppy/mixed: maximum one selective trade"), maximumTradesOverride: 1 },
  failed_breakout_reversal: profile("failed_breakout_reversal", 0.6, 0.7, 0.7, 1.05, 1, "Failed breakout/reversal: selective reversal entries"),
  gap_session: profile("gap_session", 0.6, 0.7, 0.7, 1.15, 1.05, "Gap session: confirmation required"),
  event_risk: noTradeProfile("event_blackout", "Event blackout: no new entries"),
  liquidity_stress: noTradeProfile("liquidity_stress", "Poor liquidity: no new entries"),
  extreme_volatility_no_trade: noTradeProfile("extreme_volatility", "Extreme volatility: no new entries"),
  low_volatility: profile("low_volatility_quiet", 0.4, 0.5, 0.5, 0.85, 0.8, "Low-volatility fallback profile"),
  normal_volatility: profile("normal_volatility", 0.8, 0.85, 0.85, 1, 1, "Normal-volatility fallback profile"),
  high_volatility: profile("high_volatility", 0.5, 0.6, 0.6, 1.35, 1.15, "High-volatility fallback profile"),
  trend_continuation: profile("trend_continuation", 0.85, 0.9, 0.9, 1.1, 1.05, "Trend continuation fallback profile"),
  bullish_breakout: profile("breakout_expansion", 0.6, 0.7, 0.7, 1.25, 1.2, "Bullish breakout fallback profile"),
  bearish_breakout: profile("breakout_expansion", 0.6, 0.7, 0.7, 1.25, 1.2, "Bearish breakout fallback profile"),
  bullish_reversal_risk: profile("reversal_risk", 0.5, 0.6, 0.6, 1.05, 0.95, "Bullish reversal risk fallback profile"),
  bearish_reversal_risk: profile("reversal_risk", 0.5, 0.6, 0.6, 1.05, 0.95, "Bearish reversal risk fallback profile"),
  mean_reversion: profile("mean_reversion", 0.7, 0.75, 0.75, 0.9, 0.9, "Mean reversion fallback profile"),
  no_trade: noTradeProfile("no_trade", "No-trade profile blocks new entries"),
};

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

export function resolveRegimeDynamicProfile(
  result: RegimeSelectionResult,
  marketOrSettings?: RegimeMarketContext | RegimeTradingSettings | null,
  maybeSettings?: RegimeTradingSettings,
  baseSettingsVersion = REGIME_SETTINGS_VERSION,
): RegimeDynamicProfile {
  const market = isMarketContext(marketOrSettings) ? marketOrSettings : null;
  const settings = maybeSettings ?? (isTradingSettings(marketOrSettings) ? marketOrSettings : undefined);
  const effectiveSettings = resolveEffectiveRegimeSettings({
    market,
    result,
    settings,
    baseSettingsVersion,
  });
  return {
    riskMultiplier: effectiveSettings.effectiveRiskPercent / Math.max(baseRegimeSettingsFromTradingSettings(settings).baseRiskPercent, 0.0001),
    targetMultiplier: effectiveSettings.effectiveTakeProfitR / Math.max(baseRegimeSettingsFromTradingSettings(settings).takeProfitR, 0.0001),
    reasonCodes: effectiveSettings.reasons,
    effectiveSettings,
  };
}

export function resolveEffectiveRegimeSettings(context: DynamicProfileContext): EffectiveRegimeSettings {
  const base = baseRegimeSettingsFromTradingSettings(context.settings);
  const confirmedRegime = context.result.confirmedState?.confirmedRegime ?? context.result.rawClassification?.rawRegime ?? "no_trade";
  const breakdown = buildRegimeProfileModifierBreakdown(context, confirmedRegime);
  const combined = combineRegimeProfileModifiers(Object.values(breakdown));
  const generatedAt = context.result.confirmedState?.timestamp ?? context.result.rawClassification?.timestamp ?? new Date(0).toISOString();
  return {
    baseSettingsVersion: context.baseSettingsVersion ?? REGIME_SETTINGS_VERSION,
    profileVersion: REGIME_PROFILE_VERSION,
    profileId: `${confirmedRegime}:${REGIME_PROFILE_VERSION}`,
    confirmedRegime,
    generatedAt,
    effectiveRiskPercent: roundNumber(base.baseRiskPercent * combined.riskMultiplier * combined.signalSizeMultiplier, 4),
    effectiveOrderAllocationPercent: roundNumber(base.orderAllocationPercent * combined.allocationMultiplier, 4),
    effectiveMaxPositionPercent: roundNumber(base.maxPositionPercent * combined.positionMultiplier, 4),
    effectiveAtrStopMultiplier: roundNumber(Math.max(0, base.atrStopMultiplier + combined.atrStopMultiplierAdjustment), 4),
    effectiveTakeProfitR: roundNumber(base.takeProfitR * combined.targetRMultiplier, 4),
    effectiveMaximumParticipationPercent: roundNumber(base.maximumVolumeParticipationPercent * combined.liquidityParticipationMultiplier, 4),
    effectiveMinimumWinningScore: clampNumber(base.minimumWinningScore + combined.winningScoreAdjustment, 0, 1),
    effectiveMinimumDirectionalEdge: clampNumber(base.minimumDirectionalEdge + combined.directionalEdgeAdjustment, 0, 1),
    effectiveMinimumRegimeConfidence: clampNumber(base.minimumRegimeConfidence + combined.regimeConfidenceAdjustment, 0, 1),
    effectiveMaximumTrades: Math.max(0, Math.min(base.maxTradesPerDay, combined.maximumTradesOverride ?? base.maxTradesPerDay)),
    newEntriesAllowed: combined.newEntriesAllowed,
    pyramidingAllowed: base.pyramidingEnabled && combined.pyramidingAllowed,
    reasons: combined.reasons,
  };
}

export function buildRegimeProfileModifierBreakdown(context: DynamicProfileContext, confirmedRegime: MarketRegimeId): RegimeProfileModifierBreakdown {
  return {
    profile: profileMatrix[confirmedRegime] ?? profileMatrix.no_trade,
    timeOfDay: timeOfDayModifier(context.market),
    eventProximity: neutral("Event proximity unavailable; no loosening applied"),
    spread: spreadModifier(context.market),
    quoteFreshness: neutral("Quote freshness unavailable; no loosening applied"),
    relativeVolume: relativeVolumeModifier(context.market),
    accountDrawdown: accountDrawdownModifier(context.accountDrawdownPercent),
    consecutiveLosses: consecutiveLossModifier(context.consecutiveLosses),
    accountExposure: accountExposureModifier(context.accountExposurePercent),
    regimeStability: regimeStabilityModifier(context.result),
    mlDisagreement: mlDisagreementModifier(context.result),
  };
}

export function combineRegimeProfileModifiers(modifiers: RegimeProfileModifiers[]): RegimeProfileModifiers {
  return modifiers.reduce((combined, modifier) => ({
    riskMultiplier: Math.min(1, combined.riskMultiplier * clampUnit(modifier.riskMultiplier)),
    allocationMultiplier: Math.min(1, combined.allocationMultiplier * clampUnit(modifier.allocationMultiplier)),
    positionMultiplier: Math.min(1, combined.positionMultiplier * clampUnit(modifier.positionMultiplier)),
    liquidityParticipationMultiplier: Math.min(1, combined.liquidityParticipationMultiplier * clampUnit(modifier.liquidityParticipationMultiplier)),
    signalSizeMultiplier: Math.min(1, combined.signalSizeMultiplier * clampUnit(modifier.signalSizeMultiplier)),
    atrStopMultiplierAdjustment: combined.atrStopMultiplierAdjustment + Math.max(-0.5, Math.min(1, modifier.atrStopMultiplierAdjustment)),
    targetRMultiplier: Math.min(1.5, combined.targetRMultiplier * Math.max(0, modifier.targetRMultiplier)),
    winningScoreAdjustment: combined.winningScoreAdjustment + Math.max(0, modifier.winningScoreAdjustment),
    directionalEdgeAdjustment: combined.directionalEdgeAdjustment + Math.max(0, modifier.directionalEdgeAdjustment),
    regimeConfidenceAdjustment: combined.regimeConfidenceAdjustment + Math.max(0, modifier.regimeConfidenceAdjustment),
    maximumTradesOverride: minOverride(combined.maximumTradesOverride, modifier.maximumTradesOverride),
    entryCutoffOverride: modifier.entryCutoffOverride ?? combined.entryCutoffOverride,
    pyramidingAllowed: combined.pyramidingAllowed && modifier.pyramidingAllowed,
    newEntriesAllowed: combined.newEntriesAllowed && modifier.newEntriesAllowed,
    reasons: [...combined.reasons, ...modifier.reasons],
  }), { ...neutralModifier });
}

function profile(profileId: string, risk: number, allocation: number, position: number, atrStop: number, targetR: number, reason: string): RegimeProfileModifiers {
  return {
    ...neutralModifier,
    riskMultiplier: Math.min(1, risk),
    allocationMultiplier: Math.min(1, allocation),
    positionMultiplier: Math.min(1, position),
    atrStopMultiplierAdjustment: atrStop - 1,
    targetRMultiplier: targetR,
    winningScoreAdjustment: risk < 0.7 ? 0.04 : 0,
    directionalEdgeAdjustment: risk < 0.7 ? 0.03 : 0,
    reasons: [`${profileId}: ${reason}`],
  };
}

function noTradeProfile(profileId: string, reason: string): RegimeProfileModifiers {
  return {
    ...profile(profileId, 0, 0, 0, 1, 1, reason),
    signalSizeMultiplier: 0,
    newEntriesAllowed: false,
    pyramidingAllowed: false,
    maximumTradesOverride: 0,
  };
}

function timeOfDayModifier(market?: RegimeMarketContext | null): RegimeProfileModifiers {
  if (!market) return neutral("Time-of-day data unavailable; no loosening applied");
  if (!market.timeOfDay.newTradesAllowed) {
    return { ...neutralModifier, riskMultiplier: 0, allocationMultiplier: 0, positionMultiplier: 0, signalSizeMultiplier: 0, newEntriesAllowed: false, reasons: ["Time of day blocks new entries"] };
  }
  if (market.timeOfDay.label === "Midday") {
    return { ...neutralModifier, riskMultiplier: 0.85, allocationMultiplier: 0.9, reasons: ["Midday liquidity profile tightens risk"] };
  }
  if (market.timeOfDay.label === "Closing window") {
    return { ...neutralModifier, riskMultiplier: 0.5, allocationMultiplier: 0.5, maximumTradesOverride: 1, reasons: ["Closing window allows only selective entries"] };
  }
  return neutral(`Time of day ${market.timeOfDay.label} permits baseline risk`);
}

function spreadModifier(market?: RegimeMarketContext | null): RegimeProfileModifiers {
  if (!market) return neutral("Spread data unavailable; no loosening applied");
  if (market.spreadLiquidity.spreadTooWide) {
    return { ...neutralModifier, riskMultiplier: 0, allocationMultiplier: 0, positionMultiplier: 0, signalSizeMultiplier: 0, newEntriesAllowed: false, reasons: ["Spread modifier blocks new entries"] };
  }
  return neutral("Spread modifier preserves baseline limits");
}

function relativeVolumeModifier(market?: RegimeMarketContext | null): RegimeProfileModifiers {
  if (!market) return neutral("Relative volume unavailable; no loosening applied");
  if (market.volume.relativeVolume < 0.55) {
    return { ...neutralModifier, riskMultiplier: 0, allocationMultiplier: 0, positionMultiplier: 0, signalSizeMultiplier: 0, newEntriesAllowed: false, reasons: ["Relative volume is too low for new entries"] };
  }
  if (market.volume.relativeVolume < 0.8) {
    return { ...neutralModifier, riskMultiplier: 0.7, allocationMultiplier: 0.75, liquidityParticipationMultiplier: 0.7, reasons: ["Relative volume tightens sizing"] };
  }
  return neutral("Relative volume preserves baseline limits");
}

function accountDrawdownModifier(drawdownPercent?: number): RegimeProfileModifiers {
  if (drawdownPercent === undefined) return neutral("Account drawdown unavailable; no loosening applied");
  if (drawdownPercent >= 2) return { ...neutralModifier, riskMultiplier: 0, allocationMultiplier: 0, positionMultiplier: 0, newEntriesAllowed: false, reasons: ["Account drawdown blocks new entries"] };
  if (drawdownPercent >= 1) return { ...neutralModifier, riskMultiplier: 0.5, allocationMultiplier: 0.6, reasons: ["Account drawdown tightens risk"] };
  return neutral("Account drawdown within baseline");
}

function consecutiveLossModifier(losses?: number): RegimeProfileModifiers {
  if (losses === undefined) return neutral("Consecutive losses unavailable; no loosening applied");
  if (losses >= 3) return { ...neutralModifier, riskMultiplier: 0, allocationMultiplier: 0, newEntriesAllowed: false, reasons: ["Consecutive losses block new entries"] };
  if (losses >= 2) return { ...neutralModifier, riskMultiplier: 0.5, maximumTradesOverride: 1, reasons: ["Consecutive losses tighten profile"] };
  return neutral("Consecutive losses within baseline");
}

function accountExposureModifier(exposurePercent?: number): RegimeProfileModifiers {
  if (exposurePercent === undefined) return neutral("Account exposure unavailable; no loosening applied");
  if (exposurePercent >= 80) return { ...neutralModifier, riskMultiplier: 0, allocationMultiplier: 0, positionMultiplier: 0, newEntriesAllowed: false, reasons: ["Account exposure blocks new entries"] };
  if (exposurePercent >= 50) return { ...neutralModifier, riskMultiplier: 0.6, positionMultiplier: 0.6, reasons: ["Account exposure tightens profile"] };
  return neutral("Account exposure within baseline");
}

function regimeStabilityModifier(result: RegimeSelectionResult): RegimeProfileModifiers {
  if (result.conditionHeld || (result.confirmedState?.candidateRegime ?? null) !== null) {
    return { ...neutralModifier, riskMultiplier: 0.7, allocationMultiplier: 0.75, winningScoreAdjustment: 0.04, regimeConfidenceAdjustment: 0.04, reasons: ["Regime stability modifier tightens during transitions"] };
  }
  return neutral("Regime stability preserves baseline limits");
}

function mlDisagreementModifier(result: RegimeSelectionResult): RegimeProfileModifiers {
  if (result.ml?.mode !== "confirm_only" || !result.ml.prediction.enabled) {
    return neutral("ML disagreement modifier inactive unless confirm-only ML is enabled");
  }
  const confirmedRegime = result.confirmedState?.confirmedRegime ?? null;
  const predicted = result.ml.prediction.predictedRegime;
  if (confirmedRegime && predicted && confirmedRegime !== predicted) {
    return { ...neutralModifier, riskMultiplier: 0.65, allocationMultiplier: 0.7, signalSizeMultiplier: 0.7, winningScoreAdjustment: 0.05, reasons: ["Confirm-only ML disagreement tightens profile"] };
  }
  return neutral("Confirm-only ML agrees or is inconclusive");
}

function neutral(reason: string): RegimeProfileModifiers {
  return { ...neutralModifier, reasons: [reason] };
}

function clampUnit(value: number): number {
  return Number.isFinite(value) ? clampNumber(value, 0, 1) : 0;
}

function minOverride(left: number | null, right: number | null): number | null {
  if (left === null) return right;
  if (right === null) return left;
  return Math.min(left, right);
}

function isMarketContext(value: unknown): value is RegimeMarketContext {
  return Boolean(value && typeof value === "object" && "latest" in value && "atr" in value);
}

function isTradingSettings(value: unknown): value is RegimeTradingSettings {
  return Boolean(value && typeof value === "object" && "startingCapital" in value && "orderAllocationPercent" in value);
}
