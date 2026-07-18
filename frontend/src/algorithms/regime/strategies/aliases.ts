export const REGIME_STRATEGY_ALIAS_INVENTORY = [
  { alias: "first_pullback_after_open", canonicalStrategyId: "trend_pullback" },
  { alias: "bollinger_atr_reversion", canonicalStrategyId: "bollinger_band_mean_reversion" },
  { alias: "failed_breakout_strategy", canonicalStrategyId: "failed_breakout_reversal" },
] as const;

