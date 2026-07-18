import type { RegimeAggregationFamily } from "../types.ts";

export function regimeStrategyAggregationFamily(strategyId: string): RegimeAggregationFamily {
  if (["vwap_trend_continuation", "vwap_mean_reversion"].includes(strategyId)) {
    return "vwap";
  }
  if (strategyId === "gap_continuation_fade") {
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

export function representedRegimeFamilies(strategyIds: readonly string[]): RegimeAggregationFamily[] {
  return Array.from(new Set(strategyIds.map((strategyId) => regimeStrategyAggregationFamily(strategyId))));
}

export function hasMinimumIndependentFamilyParticipation(strategyIds: readonly string[], minimumFamilyCount: number): boolean {
  return representedRegimeFamilies(strategyIds).length >= minimumFamilyCount;
}
