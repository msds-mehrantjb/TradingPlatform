import { clampNumber, roundNumber } from "../indicators.ts";
import { REGIME_SETTINGS_VERSION } from "../versions.ts";
import type {
  EffectiveRegimeSettings,
  LegacyRegimeAlias,
  MarketRegimeId,
  RegimeMarketContext,
  RegimeNoTradeTag,
  RegimeProfileModifierBreakdown,
  RegimeProfileModifiers,
  RegimeSelectionResult,
  RegimeTradingSettings,
} from "../types.ts";
import { baseRegimeSettingsFromTradingSettings } from "./baseline-settings.ts";
import { boundedRegimeEffectiveSettings, clampRegimeProfileUnit, minimumRegimeProfileOverride } from "./profile-bounds.ts";
import { REGIME_PROFILE_VERSION } from "./profile-versioning.ts";
import {
  neutralRegimeProfileModifier,
  neutralRegimeProfileReason,
  profileModifiersForKey,
} from "./regime-profile-matrix.ts";

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

type RegimeProfileKey = MarketRegimeId | LegacyRegimeAlias | RegimeNoTradeTag;

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
  const confirmedRegime: RegimeProfileKey = context.result.confirmedState?.confirmedRegime ?? context.result.rawClassification?.rawRegime ?? "no_trade";
  const breakdown = buildRegimeProfileModifierBreakdown(context, confirmedRegime);
  const combined = combineRegimeProfileModifiers(Object.values(breakdown));
  const generatedAt = context.result.confirmedState?.timestamp ?? context.result.rawClassification?.timestamp ?? new Date(0).toISOString();
  return boundedRegimeEffectiveSettings(base, {
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
  });
}

export function buildRegimeProfileModifierBreakdown(context: DynamicProfileContext, confirmedRegime: RegimeProfileKey): RegimeProfileModifierBreakdown {
  return {
    profile: profileModifiersForKey(confirmedRegime),
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
    riskMultiplier: Math.min(1, combined.riskMultiplier * clampRegimeProfileUnit(modifier.riskMultiplier)),
    allocationMultiplier: Math.min(1, combined.allocationMultiplier * clampRegimeProfileUnit(modifier.allocationMultiplier)),
    positionMultiplier: Math.min(1, combined.positionMultiplier * clampRegimeProfileUnit(modifier.positionMultiplier)),
    liquidityParticipationMultiplier: Math.min(1, combined.liquidityParticipationMultiplier * clampRegimeProfileUnit(modifier.liquidityParticipationMultiplier)),
    signalSizeMultiplier: Math.min(1, combined.signalSizeMultiplier * clampRegimeProfileUnit(modifier.signalSizeMultiplier)),
    atrStopMultiplierAdjustment: combined.atrStopMultiplierAdjustment + Math.max(-0.5, Math.min(1, modifier.atrStopMultiplierAdjustment)),
    targetRMultiplier: Math.min(1.5, combined.targetRMultiplier * Math.max(0, modifier.targetRMultiplier)),
    winningScoreAdjustment: combined.winningScoreAdjustment + Math.max(0, modifier.winningScoreAdjustment),
    directionalEdgeAdjustment: combined.directionalEdgeAdjustment + Math.max(0, modifier.directionalEdgeAdjustment),
    regimeConfidenceAdjustment: combined.regimeConfidenceAdjustment + Math.max(0, modifier.regimeConfidenceAdjustment),
    maximumTradesOverride: minimumRegimeProfileOverride(combined.maximumTradesOverride, modifier.maximumTradesOverride),
    entryCutoffOverride: modifier.entryCutoffOverride ?? combined.entryCutoffOverride,
    pyramidingAllowed: combined.pyramidingAllowed && modifier.pyramidingAllowed,
    newEntriesAllowed: combined.newEntriesAllowed && modifier.newEntriesAllowed,
    reasons: [...combined.reasons, ...modifier.reasons],
  }), { ...neutralRegimeProfileModifier });
}

function timeOfDayModifier(market?: RegimeMarketContext | null): RegimeProfileModifiers {
  if (!market) return neutral("Time-of-day data unavailable; no loosening applied");
  if (!market.timeOfDay.newTradesAllowed) {
    return { ...neutralRegimeProfileModifier, riskMultiplier: 0, allocationMultiplier: 0, positionMultiplier: 0, signalSizeMultiplier: 0, newEntriesAllowed: false, reasons: ["Time of day blocks new entries"] };
  }
  if (market.timeOfDay.label === "Midday") {
    return { ...neutralRegimeProfileModifier, riskMultiplier: 0.85, allocationMultiplier: 0.9, reasons: ["Midday liquidity profile tightens risk"] };
  }
  if (market.timeOfDay.label === "Closing window") {
    return { ...neutralRegimeProfileModifier, riskMultiplier: 0.5, allocationMultiplier: 0.5, maximumTradesOverride: 1, reasons: ["Closing window allows only selective entries"] };
  }
  return neutral(`Time of day ${market.timeOfDay.label} permits baseline risk`);
}

function spreadModifier(market?: RegimeMarketContext | null): RegimeProfileModifiers {
  if (!market) return neutral("Spread data unavailable; no loosening applied");
  if (market.spreadLiquidity.spreadTooWide) {
    return { ...neutralRegimeProfileModifier, riskMultiplier: 0, allocationMultiplier: 0, positionMultiplier: 0, signalSizeMultiplier: 0, newEntriesAllowed: false, reasons: ["Spread modifier blocks new entries"] };
  }
  return neutral("Spread modifier preserves baseline limits");
}

function relativeVolumeModifier(market?: RegimeMarketContext | null): RegimeProfileModifiers {
  if (!market) return neutral("Relative volume unavailable; no loosening applied");
  if (market.volume.relativeVolume < 0.55) {
    return { ...neutralRegimeProfileModifier, riskMultiplier: 0, allocationMultiplier: 0, positionMultiplier: 0, signalSizeMultiplier: 0, newEntriesAllowed: false, reasons: ["Relative volume is too low for new entries"] };
  }
  if (market.volume.relativeVolume < 0.8) {
    return { ...neutralRegimeProfileModifier, riskMultiplier: 0.7, allocationMultiplier: 0.75, liquidityParticipationMultiplier: 0.7, reasons: ["Relative volume tightens sizing"] };
  }
  return neutral("Relative volume preserves baseline limits");
}

function accountDrawdownModifier(drawdownPercent?: number): RegimeProfileModifiers {
  if (drawdownPercent === undefined) return neutral("Account drawdown unavailable; no loosening applied");
  if (drawdownPercent >= 2) return { ...neutralRegimeProfileModifier, riskMultiplier: 0, allocationMultiplier: 0, positionMultiplier: 0, newEntriesAllowed: false, reasons: ["Account drawdown blocks new entries"] };
  if (drawdownPercent >= 1) return { ...neutralRegimeProfileModifier, riskMultiplier: 0.5, allocationMultiplier: 0.6, reasons: ["Account drawdown tightens risk"] };
  return neutral("Account drawdown within baseline");
}

function consecutiveLossModifier(losses?: number): RegimeProfileModifiers {
  if (losses === undefined) return neutral("Consecutive losses unavailable; no loosening applied");
  if (losses >= 3) return { ...neutralRegimeProfileModifier, riskMultiplier: 0, allocationMultiplier: 0, newEntriesAllowed: false, reasons: ["Consecutive losses block new entries"] };
  if (losses >= 2) return { ...neutralRegimeProfileModifier, riskMultiplier: 0.5, maximumTradesOverride: 1, reasons: ["Consecutive losses tighten profile"] };
  return neutral("Consecutive losses within baseline");
}

function accountExposureModifier(exposurePercent?: number): RegimeProfileModifiers {
  if (exposurePercent === undefined) return neutral("Account exposure unavailable; no loosening applied");
  if (exposurePercent >= 80) return { ...neutralRegimeProfileModifier, riskMultiplier: 0, allocationMultiplier: 0, positionMultiplier: 0, newEntriesAllowed: false, reasons: ["Account exposure blocks new entries"] };
  if (exposurePercent >= 50) return { ...neutralRegimeProfileModifier, riskMultiplier: 0.6, positionMultiplier: 0.6, reasons: ["Account exposure tightens profile"] };
  return neutral("Account exposure within baseline");
}

function regimeStabilityModifier(result: RegimeSelectionResult): RegimeProfileModifiers {
  if (result.conditionHeld || (result.confirmedState?.candidateRegime ?? null) !== null) {
    return { ...neutralRegimeProfileModifier, riskMultiplier: 0.7, allocationMultiplier: 0.75, winningScoreAdjustment: 0.04, regimeConfidenceAdjustment: 0.04, reasons: ["Regime stability modifier tightens during transitions"] };
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
    return { ...neutralRegimeProfileModifier, riskMultiplier: 0.65, allocationMultiplier: 0.7, signalSizeMultiplier: 0.7, winningScoreAdjustment: 0.05, reasons: ["Confirm-only ML disagreement tightens profile"] };
  }
  return neutral("Confirm-only ML agrees or is inconclusive");
}

function neutral(reason: string): RegimeProfileModifiers {
  return neutralRegimeProfileReason(reason);
}

function isMarketContext(value: unknown): value is RegimeMarketContext {
  return Boolean(value && typeof value === "object" && "latest" in value && "atr" in value);
}

function isTradingSettings(value: unknown): value is RegimeTradingSettings {
  return Boolean(value && typeof value === "object" && "startingCapital" in value && "orderAllocationPercent" in value);
}
