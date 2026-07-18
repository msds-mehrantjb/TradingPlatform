import type { RegimeHysteresisSettings } from "../types.ts";

export type RegimeDwellPolicyResult = {
  dwellBars: number;
  minimumDwellSatisfied: boolean;
};

export function evaluateRegimeDwellPolicy(previousDwellBars: number, settings: RegimeHysteresisSettings): RegimeDwellPolicyResult {
  const dwellBars = previousDwellBars + 1;
  return {
    dwellBars,
    minimumDwellSatisfied: dwellBars >= settings.minimumDwellBars,
  };
}
