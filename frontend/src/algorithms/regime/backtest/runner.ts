import type { RegimeBacktestInput, RegimeBacktestResult } from "./types.ts";
import { runRegimeBacktest } from "./engine.ts";

export function runNodeRegimeBacktest(input: RegimeBacktestInput): RegimeBacktestResult {
  return runRegimeBacktest(input);
}
