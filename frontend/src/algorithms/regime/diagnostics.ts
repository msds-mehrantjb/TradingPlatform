import type { RegimeSelectionResult } from "./types.ts";

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
