import type {
  LegacyRegimeAlias,
  MarketRegimeId,
  RegimeNoTradeTag,
  RegimeProfileModifiers,
} from "../types.ts";

export const neutralRegimeProfileModifier: RegimeProfileModifiers = {
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

export const REGIME_PROFILE_MATRIX: Readonly<Record<MarketRegimeId, RegimeProfileModifiers>> = Object.freeze({
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
});

export const REGIME_LEGACY_ALIAS_PROFILE_MATRIX: Readonly<Record<LegacyRegimeAlias | RegimeNoTradeTag, RegimeProfileModifiers>> = Object.freeze({
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
});

export function profileModifiersForKey(key: MarketRegimeId | LegacyRegimeAlias | RegimeNoTradeTag): RegimeProfileModifiers {
  return REGIME_PROFILE_MATRIX[key as MarketRegimeId] ?? REGIME_LEGACY_ALIAS_PROFILE_MATRIX[key as LegacyRegimeAlias | RegimeNoTradeTag];
}

export function neutralRegimeProfileReason(reason: string): RegimeProfileModifiers {
  return { ...neutralRegimeProfileModifier, reasons: [reason] };
}

function profile(profileId: string, risk: number, allocation: number, position: number, atrStop: number, targetR: number, reason: string): RegimeProfileModifiers {
  return {
    ...neutralRegimeProfileModifier,
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
