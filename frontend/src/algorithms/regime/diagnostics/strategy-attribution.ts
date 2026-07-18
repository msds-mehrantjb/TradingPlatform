import type { RegimeSelectionResult } from "../types.ts";

export type RegimeStrategyAttribution = {
  algorithmId: "regime";
  selectedStrategies: Array<{ strategyId: string; family: string; signal: string; confidence: number }>;
  skippedStrategies: Array<{ name: string; reason: string }>;
  familyScores: Array<{ family: string; buyScore: number; sellScore: number; activeStrategyCount: number }>;
  activeStrategyCount: number;
  activeFamilyCount: number;
};

export function buildRegimeStrategyAttribution(result: RegimeSelectionResult): RegimeStrategyAttribution {
  return {
    algorithmId: "regime",
    selectedStrategies: result.selectedStrategies.map((strategy) => ({
      strategyId: strategy.strategy,
      family: strategy.family,
      signal: strategy.signal,
      confidence: strategy.confidence,
    })),
    skippedStrategies: result.skippedStrategies,
    familyScores: (result.familyScores ?? []).map((score) => ({
      family: score.family,
      buyScore: score.buyScore,
      sellScore: score.sellScore,
      activeStrategyCount: score.activeStrategyCount,
    })),
    activeStrategyCount: result.activeStrategyCount,
    activeFamilyCount: result.activeFamilyCount,
  };
}
