import { REGIME_STRATEGY_ALIAS_INVENTORY, REGIME_STRATEGY_ALIAS_MAP } from "../strategies/alias-map.ts";

export type RegimeAliasDeduplicationResult = {
  alias: string;
  canonicalStrategyId: string;
  alreadyVoted: boolean;
};

export function canonicalRegimeRoutingStrategyId(strategyId: string): string {
  return REGIME_STRATEGY_ALIAS_MAP[strategyId as keyof typeof REGIME_STRATEGY_ALIAS_MAP] ?? strategyId;
}

export function dedupeRegimeStrategyIds(strategyIds: readonly string[]): string[] {
  return Array.from(new Set(strategyIds.map((strategyId) => canonicalRegimeRoutingStrategyId(strategyId))));
}

export function aliasDeduplicationForSelectedStrategies(selectedStrategyIds: readonly string[]): RegimeAliasDeduplicationResult[] {
  const selected = new Set(selectedStrategyIds);
  return REGIME_STRATEGY_ALIAS_INVENTORY.map((entry) => ({
    alias: entry.alias,
    canonicalStrategyId: entry.canonicalStrategyId,
    alreadyVoted: selected.has(entry.canonicalStrategyId),
  }));
}
