import type {
  LegacyRegimeAlias,
  MarketRegimeId,
  RegimeNoTradeTag,
  RegimeOpportunityState,
  RegimePrimaryTrend,
  RegimeVolatilityState,
} from "../types.ts";

export type RegimeRoutingKey = MarketRegimeId | LegacyRegimeAlias | RegimeNoTradeTag;
export type RegimePermittedDirection = "long" | "short" | "both" | "none";

export const REGIME_NO_DIRECTIONAL_REGIMES = new Set<MarketRegimeId>([
  "extreme_volatility_no_trade",
  "event_risk",
  "liquidity_stress",
]);

export const REGIME_COMPATIBILITY_MATRIX: Readonly<Record<MarketRegimeId, readonly string[]>> = Object.freeze({
  strong_uptrend: Object.freeze(["moving_average_trend", "trend_pullback", "macd_momentum", "market_structure", "vwap_trend_continuation"]),
  weak_uptrend: Object.freeze(["trend_pullback", "market_structure", "vwap_trend_continuation"]),
  strong_downtrend: Object.freeze(["moving_average_trend", "trend_pullback", "macd_momentum", "market_structure", "vwap_trend_continuation"]),
  weak_downtrend: Object.freeze(["trend_pullback", "market_structure", "vwap_trend_continuation"]),
  range_bound: Object.freeze(["rsi_mean_reversion", "bollinger_band_mean_reversion", "vwap_mean_reversion"]),
  sideways_range: Object.freeze(["rsi_mean_reversion", "bollinger_band_mean_reversion", "vwap_mean_reversion"]),
  opening_breakout: Object.freeze(["opening_range_breakout", "volatility_breakout", "trend_pullback"]),
  intraday_expansion: Object.freeze(["intraday_breakout", "volatility_breakout", "market_structure"]),
  high_volatility_trend: Object.freeze(["market_structure", "moving_average_trend", "volatility_breakout"]),
  low_volatility_quiet: Object.freeze(["rsi_mean_reversion", "bollinger_band_mean_reversion", "vwap_mean_reversion"]),
  failed_breakout_reversal: Object.freeze(["failed_breakout_reversal", "liquidity_sweep_reversal"]),
  choppy_mixed: Object.freeze(["rsi_mean_reversion", "bollinger_band_mean_reversion", "vwap_mean_reversion"]),
  gap_session: Object.freeze(["gap_continuation_fade", "moving_average_trend", "market_structure", "failed_breakout_reversal", "liquidity_sweep_reversal"]),
  event_risk: Object.freeze([]),
  liquidity_stress: Object.freeze([]),
  extreme_volatility_no_trade: Object.freeze([]),
});

export const REGIME_LEGACY_ROUTING_MATRIX: Readonly<Record<LegacyRegimeAlias | RegimeNoTradeTag, readonly string[]>> = Object.freeze({
  low_volatility: Object.freeze(["rsi_mean_reversion", "bollinger_band_mean_reversion", "vwap_mean_reversion"]),
  normal_volatility: Object.freeze(["moving_average_trend", "trend_pullback", "market_structure", "vwap_mean_reversion"]),
  high_volatility: Object.freeze(["market_structure", "volatility_breakout", "failed_breakout_reversal", "liquidity_sweep_reversal"]),
  trend_continuation: Object.freeze(["moving_average_trend", "trend_pullback", "macd_momentum", "market_structure", "vwap_trend_continuation"]),
  bullish_breakout: Object.freeze(["opening_range_breakout", "intraday_breakout", "volatility_breakout", "market_structure"]),
  bearish_breakout: Object.freeze(["opening_range_breakout", "intraday_breakout", "volatility_breakout", "market_structure"]),
  bullish_reversal_risk: Object.freeze(["failed_breakout_reversal", "liquidity_sweep_reversal", "rsi_mean_reversion", "vwap_mean_reversion"]),
  bearish_reversal_risk: Object.freeze(["failed_breakout_reversal", "liquidity_sweep_reversal", "rsi_mean_reversion", "vwap_mean_reversion"]),
  mean_reversion: Object.freeze(["rsi_mean_reversion", "bollinger_band_mean_reversion", "vwap_mean_reversion"]),
  no_trade: Object.freeze([]),
});

export function strategyIdsForConfirmedRegime(confirmedRegime: MarketRegimeId): string[] {
  return REGIME_NO_DIRECTIONAL_REGIMES.has(confirmedRegime)
    ? []
    : Array.from(REGIME_COMPATIBILITY_MATRIX[confirmedRegime] ?? []);
}

export function strategyIdsForRegimeRoutingKey(key: RegimeRoutingKey): string[] {
  return Array.from(
    REGIME_COMPATIBILITY_MATRIX[key as MarketRegimeId] ??
      REGIME_LEGACY_ROUTING_MATRIX[key as LegacyRegimeAlias | RegimeNoTradeTag] ??
      [],
  );
}

export function permittedDirectionForRegime(confirmedRegime: MarketRegimeId): RegimePermittedDirection {
  if (REGIME_NO_DIRECTIONAL_REGIMES.has(confirmedRegime)) {
    return "none";
  }
  if (confirmedRegime === "strong_uptrend" || confirmedRegime === "weak_uptrend" || confirmedRegime === "opening_breakout") {
    return "long";
  }
  if (confirmedRegime === "strong_downtrend" || confirmedRegime === "weak_downtrend") {
    return "short";
  }
  return "both";
}

export function legacyRegimeId(
  primaryTrend: RegimePrimaryTrend,
  volatility: RegimeVolatilityState,
  opportunity: RegimeOpportunityState,
): RegimeRoutingKey {
  if (opportunity === "No-trade") return "no_trade";
  if (opportunity === "Bullish breakout") return "bullish_breakout";
  if (opportunity === "Bearish breakout") return "bearish_breakout";
  if (opportunity === "Bullish reversal risk") return "bullish_reversal_risk";
  if (opportunity === "Bearish reversal risk") return "bearish_reversal_risk";
  if (opportunity === "Mean reversion") return "mean_reversion";
  if (volatility === "Low volatility") return "low_volatility";
  if (volatility === "High volatility") return "high_volatility";
  if (primaryTrend === "Strong uptrend") return "strong_uptrend";
  if (primaryTrend === "Weak uptrend") return "weak_uptrend";
  if (primaryTrend === "Strong downtrend") return "strong_downtrend";
  if (primaryTrend === "Weak downtrend") return "weak_downtrend";
  return "sideways_range";
}
