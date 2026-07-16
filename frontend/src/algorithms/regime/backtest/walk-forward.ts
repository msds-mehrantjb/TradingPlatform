import type { RegimeBacktestInput, RegimeBacktestResult } from "./types.ts";
import { runRegimeBacktest } from "./engine.ts";

export function runRegimeWalkForward(input: RegimeBacktestInput): RegimeBacktestResult[] {
  return [runRegimeBacktest(input)];
}
