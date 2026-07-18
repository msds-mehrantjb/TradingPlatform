import { roundNumber } from "../indicators.ts";
import type { RegimeSelectedStrategy } from "../types.ts";

export function activeDirectionalRegimeOutputs(strategyOutputs: RegimeSelectedStrategy[]): RegimeSelectedStrategy[] {
  return strategyOutputs.filter((output) => output.role === "directional" && output.eligible);
}

export function votingDirectionalRegimeOutputs(strategyOutputs: RegimeSelectedStrategy[]): RegimeSelectedStrategy[] {
  return activeDirectionalRegimeOutputs(strategyOutputs).filter((output) => output.signal === "buy" || output.signal === "sell");
}

export function regimeAbstentionRate(strategyOutputs: RegimeSelectedStrategy[], votingOutputs: RegimeSelectedStrategy[]): number {
  return strategyOutputs.length ? roundNumber((strategyOutputs.length - votingOutputs.length) / strategyOutputs.length, 4) : 1;
}
