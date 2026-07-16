import type { RegimeBacktestResult } from "./types.ts";

export function regimeBacktestDiagnostics(result: RegimeBacktestResult): string[] {
  return [`decisions:${result.decisions.length}`, `trades:${result.trades.length}`];
}
