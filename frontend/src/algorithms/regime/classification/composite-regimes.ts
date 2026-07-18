import type { LegacyRegimeAlias, MarketRegimeId, RegimeOpportunityTag } from "../types.ts";

export const REGIME_COMPOSITE_REGIME_IDS = [
  "strong_uptrend",
  "weak_uptrend",
  "strong_downtrend",
  "weak_downtrend",
  "range_bound",
  "sideways_range",
  "choppy_mixed",
  "opening_breakout",
  "intraday_expansion",
  "high_volatility_trend",
  "low_volatility_quiet",
  "failed_breakout_reversal",
  "gap_session",
  "event_risk",
  "liquidity_stress",
  "extreme_volatility_no_trade",
] as const satisfies readonly MarketRegimeId[];

export const REGIME_LEGACY_ALIASES = [
  "low_volatility",
  "normal_volatility",
  "high_volatility",
  "trend_continuation",
  "bullish_breakout",
  "bearish_breakout",
  "bullish_reversal_risk",
  "bearish_reversal_risk",
  "mean_reversion",
] as const satisfies readonly LegacyRegimeAlias[];

export const REGIME_OPPORTUNITY_TAGS = [
  "trend_continuation",
  "bullish_breakout",
  "bearish_breakout",
  "bullish_reversal_risk",
  "bearish_reversal_risk",
  "mean_reversion",
  "no_trade",
] as const satisfies readonly RegimeOpportunityTag[];

export function isCanonicalMarketRegimeId(value: string): value is MarketRegimeId {
  return (REGIME_COMPOSITE_REGIME_IDS as readonly string[]).includes(value);
}

export function isLegacyRegimeAlias(value: string): value is LegacyRegimeAlias {
  return (REGIME_LEGACY_ALIASES as readonly string[]).includes(value);
}

export {
  compositeRegimeIdFromAxes,
} from "../classifier.ts";
export type {
  LegacyRegimeAlias,
  MarketRegimeId,
  RegimeOpportunityTag,
} from "../types.ts";

