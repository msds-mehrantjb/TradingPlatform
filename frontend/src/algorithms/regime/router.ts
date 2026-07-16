import { clampNumber, roundNumber } from "./indicators.ts";
import { evaluateRegimeStrategyDefinition, regimeSelectionStrategies } from "./strategy-catalog.ts";
import type {
  ContextResult,
  MarketRegimeId,
  RegimeAggregationFamily,
  RegimeMarketContext,
  RegimeOpportunityState,
  RegimePrimaryTrend,
  RegimeStrategyDefinition,
  RegimeStrategySignal,
  RegimeVolatilityState,
  SafetyGateResult,
  StrategyRoutingResult,
} from "./types.ts";

const noDirectionalRegimes = new Set<MarketRegimeId>(["extreme_volatility_no_trade", "event_risk", "liquidity_stress", "no_trade"]);

const regimeDirectionalMap: Record<MarketRegimeId, string[]> = {
  strong_uptrend: ["moving_average_trend", "trend_pullback", "macd_momentum", "market_structure", "vwap_trend_continuation"],
  weak_uptrend: ["trend_pullback", "market_structure", "vwap_trend_continuation"],
  strong_downtrend: ["moving_average_trend", "trend_pullback", "macd_momentum", "market_structure", "vwap_trend_continuation"],
  weak_downtrend: ["trend_pullback", "market_structure", "vwap_trend_continuation"],
  range_bound: ["rsi_mean_reversion", "bollinger_band_mean_reversion", "vwap_mean_reversion"],
  sideways_range: ["rsi_mean_reversion", "bollinger_band_mean_reversion", "vwap_mean_reversion"],
  opening_breakout: ["opening_range_breakout", "volatility_breakout", "trend_pullback"],
  intraday_expansion: ["intraday_breakout", "volatility_breakout", "market_structure"],
  high_volatility_trend: ["market_structure", "moving_average_trend", "volatility_breakout"],
  low_volatility_quiet: ["rsi_mean_reversion", "bollinger_band_mean_reversion", "vwap_mean_reversion"],
  failed_breakout_reversal: ["failed_breakout_reversal", "liquidity_sweep_reversal"],
  choppy_mixed: ["rsi_mean_reversion", "bollinger_band_mean_reversion", "vwap_mean_reversion"],
  gap_session: ["gap_continuation_fade", "moving_average_trend", "market_structure", "failed_breakout_reversal", "liquidity_sweep_reversal"],
  event_risk: [],
  liquidity_stress: [],
  extreme_volatility_no_trade: [],
  low_volatility: ["rsi_mean_reversion", "bollinger_band_mean_reversion", "vwap_mean_reversion"],
  normal_volatility: ["moving_average_trend", "trend_pullback", "market_structure", "vwap_mean_reversion"],
  high_volatility: ["market_structure", "volatility_breakout", "failed_breakout_reversal", "liquidity_sweep_reversal"],
  trend_continuation: ["moving_average_trend", "trend_pullback", "macd_momentum", "market_structure", "vwap_trend_continuation"],
  bullish_breakout: ["opening_range_breakout", "intraday_breakout", "volatility_breakout", "market_structure"],
  bearish_breakout: ["opening_range_breakout", "intraday_breakout", "volatility_breakout", "market_structure"],
  bullish_reversal_risk: ["failed_breakout_reversal", "liquidity_sweep_reversal", "rsi_mean_reversion", "vwap_mean_reversion"],
  bearish_reversal_risk: ["failed_breakout_reversal", "liquidity_sweep_reversal", "rsi_mean_reversion", "vwap_mean_reversion"],
  mean_reversion: ["rsi_mean_reversion", "bollinger_band_mean_reversion", "vwap_mean_reversion"],
  no_trade: [],
};

export function routeRegimeStrategies(confirmedRegime: MarketRegimeId, market: RegimeMarketContext): StrategyRoutingResult {
  const eligibleDirectionalIds = new Set(regimeDirectionalMap[confirmedRegime] ?? []);
  const selectedStrategyIds = noDirectionalRegimes.has(confirmedRegime) ? [] : Array.from(eligibleDirectionalIds);
  const skippedStrategies = regimeSelectionStrategies
    .filter((strategy) => strategy.role === "directional" && !selectedStrategyIds.includes(strategy.id))
    .map((strategy) => ({
      strategyId: strategy.id,
      reason: noDirectionalRegimes.has(confirmedRegime)
        ? `Skipped: ${confirmedRegime} allows no new directional strategies`
        : `Skipped: ${strategy.name} is not compatible with ${confirmedRegime}`,
    }));
  const contextResults = regimeSelectionStrategies
    .filter((strategy) => strategy.role === "confirmation" || strategy.role === "regime_context")
    .map((strategy) => contextResultFromStrategy(strategy, market));
  const safetyResults = regimeSelectionStrategies
    .filter((strategy) => strategy.role === "safety_gate")
    .map((strategy) => safetyResultFromStrategy(strategy, market));

  return {
    confirmedRegime,
    selectedStrategyIds,
    skippedStrategies,
    contextResults,
    safetyResults,
  };
}

export function contextMultiplierForSignal(
  strategy: RegimeStrategyDefinition,
  signal: RegimeStrategySignal,
  contextResults: ContextResult[],
): number {
  if (strategy.role !== "directional" || signal === "hold") {
    return 1;
  }
  return roundNumber(
    contextResults.reduce((multiplier, context) => multiplier * contextCompatibilityMultiplier(context, signal), 1),
    4,
  );
}

export function regimeCompatibilityMultiplier(strategyId: string, confirmedRegime: MarketRegimeId): number {
  const selected = regimeDirectionalMap[confirmedRegime]?.includes(strategyId) ?? false;
  return selected && !noDirectionalRegimes.has(confirmedRegime) ? 1 : 0;
}

export function reliabilityMultiplier(_strategy: RegimeStrategyDefinition): number {
  return 1;
}

export function correlationPenalty(_strategy: RegimeStrategyDefinition): number {
  return 1;
}

export function regimeStrategyAggregationFamily(strategyId: string): RegimeAggregationFamily {
  if (["vwap_trend_continuation", "vwap_mean_reversion"].includes(strategyId)) {
    return "vwap";
  }
  if (["gap_continuation_fade"].includes(strategyId)) {
    return "gap_event";
  }
  if (["opening_range_breakout", "intraday_breakout", "volatility_breakout"].includes(strategyId)) {
    return "breakout";
  }
  if (["rsi_mean_reversion", "bollinger_band_mean_reversion"].includes(strategyId)) {
    return "mean_reversion";
  }
  if (["failed_breakout_reversal", "liquidity_sweep_reversal"].includes(strategyId)) {
    return "reversal";
  }
  return "trend";
}

export function regimeStrategySelectorReason(strategyId: string, confirmedRegime: MarketRegimeId): string {
  return regimeDirectionalMap[confirmedRegime]?.includes(strategyId)
    ? `Selected for confirmed regime ${confirmedRegime}`
    : `Selected as non-directional Regime ${confirmedRegime} context`;
}

export function regimeStrategyAvoidReason(strategyId: string, confirmedRegime: MarketRegimeId): string {
  return noDirectionalRegimes.has(confirmedRegime)
    ? `Avoided: ${confirmedRegime} permits no new directional strategies`
    : `Avoided: ${strategyId} is not compatible with ${confirmedRegime}`;
}

export function regimeSelectedStrategySlugs(
  primaryTrend: RegimePrimaryTrend,
  volatility: RegimeVolatilityState,
  opportunity: RegimeOpportunityState,
): string[] {
  return regimeDirectionalMap[legacyRegimeId(primaryTrend, volatility, opportunity)] ?? [];
}

function contextResultFromStrategy(strategy: RegimeStrategyDefinition, market: RegimeMarketContext): ContextResult {
  const raw = evaluateRegimeStrategyDefinition(strategy, market);
  return {
    strategyId: strategy.id,
    role: strategy.role === "confirmation" ? "confirmation" : "regime_context",
    eligible: raw.eligible !== false,
    multiplier: contextBaseMultiplier(raw.signal, raw.confidence, raw.eligible !== false),
    reason: raw.reason,
    signal: raw.signal,
    confidence: clampNumber(raw.confidence, 0, 1),
  };
}

function safetyResultFromStrategy(strategy: RegimeStrategyDefinition, market: RegimeMarketContext): SafetyGateResult {
  const raw = evaluateRegimeStrategyDefinition(strategy, market);
  return {
    strategyId: strategy.id,
    passed: raw.passed !== false,
    blockNewEntries: raw.blockNewEntries === true,
    reason: raw.reason,
  };
}

function contextCompatibilityMultiplier(context: ContextResult, signal: RegimeStrategySignal): number {
  if (!context.eligible) {
    return 0.8;
  }
  const contextSignal = context.signal === "Buy" ? "buy" : context.signal === "Sell" ? "sell" : "hold";
  if (contextSignal === "hold") {
    return context.multiplier;
  }
  if (contextSignal === signal) {
    return 1;
  }
  return roundNumber(Math.max(0.5, 1 - context.confidence * 0.5), 4);
}

function contextBaseMultiplier(signal: "Buy" | "Sell" | "Hold", confidence: number, eligible: boolean): number {
  if (!eligible) {
    return 0.8;
  }
  if (signal === "Hold") {
    return roundNumber(Math.max(0.65, 1 - clampNumber(confidence, 0, 1) * 0.35), 4);
  }
  return 1;
}

function legacyRegimeId(primaryTrend: RegimePrimaryTrend, volatility: RegimeVolatilityState, opportunity: RegimeOpportunityState): MarketRegimeId {
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
