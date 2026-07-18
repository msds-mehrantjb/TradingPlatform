import type { RegimeSelectionResult } from "./types.ts";
export * from "./diagnostics/diagnostics.ts";
export * from "./diagnostics/decision-trace.ts";
export * from "./diagnostics/classification-trace.ts";
export * from "./diagnostics/strategy-attribution.ts";
export * from "./diagnostics/profile-attribution.ts";

export type RegimeDiagnosticSnapshot = {
  signal: string;
  condition: string;
  selectedStrategyCount: number;
  blockers: string[];
};

export function regimeDiagnostics(result: RegimeSelectionResult): RegimeDiagnosticSnapshot {
  return {
    signal: result.signal,
    condition: result.confirmedCondition,
    selectedStrategyCount: result.selectedStrategyCount,
    blockers: result.tradeBlockers,
  };
}
