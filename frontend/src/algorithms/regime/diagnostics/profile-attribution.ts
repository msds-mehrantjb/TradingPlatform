import type { RegimeSelectionResult } from "../types.ts";

export type RegimeProfileAttribution = {
  algorithmId: "regime";
  profileId: string | null;
  profileVersion: string | null;
  baseSettingsVersion: string | null;
  effectiveMinimumWinningScore: number | null;
  effectiveMinimumDirectionalEdge: number | null;
  effectiveMaximumTrades: number | null;
  newEntriesAllowed: boolean | null;
  reasons: readonly string[];
};

export function buildRegimeProfileAttribution(result: RegimeSelectionResult): RegimeProfileAttribution {
  const effective = result.effectiveSettings;
  return {
    algorithmId: "regime",
    profileId: effective?.profileId ?? null,
    profileVersion: effective?.profileVersion ?? null,
    baseSettingsVersion: effective?.baseSettingsVersion ?? null,
    effectiveMinimumWinningScore: effective?.effectiveMinimumWinningScore ?? null,
    effectiveMinimumDirectionalEdge: effective?.effectiveMinimumDirectionalEdge ?? null,
    effectiveMaximumTrades: effective?.effectiveMaximumTrades ?? null,
    newEntriesAllowed: effective?.newEntriesAllowed ?? null,
    reasons: effective?.reasons ?? [],
  };
}
