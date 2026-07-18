export const REGIME_STRATEGY_ALIAS_MAP = {
  first_pullback_after_open: "trend_pullback",
  bollinger_atr_reversion: "bollinger_band_mean_reversion",
  failed_breakout_strategy: "failed_breakout_reversal",
} as const;

export const REGIME_STRATEGY_ALIAS_INVENTORY = Object.entries(REGIME_STRATEGY_ALIAS_MAP).map(([alias, canonicalStrategyId]) => ({
  alias,
  canonicalStrategyId,
})) as Array<{ alias: keyof typeof REGIME_STRATEGY_ALIAS_MAP; canonicalStrategyId: typeof REGIME_STRATEGY_ALIAS_MAP[keyof typeof REGIME_STRATEGY_ALIAS_MAP] }>;

