import type { RegimeSelectionResult } from "../types.ts";

export type RegimeShadowComparisonResult = {
  algorithmId: "regime";
  matched: boolean;
  comparedFields: readonly string[];
  differences: string[];
  submitOrders: false;
};

export function compareRegimeShadowDecisions(
  baseline: RegimeSelectionResult,
  candidate: RegimeSelectionResult,
): RegimeShadowComparisonResult {
  const comparedFields = ["signal", "confirmedCondition", "buyScore", "sellScore", "tradeAllowed"] as const;
  const differences = comparedFields.filter((field) => baseline[field] !== candidate[field]).map((field) => `regime.shadow_comparison.changed:${field}`);
  return {
    algorithmId: "regime",
    matched: differences.length === 0,
    comparedFields,
    differences,
    submitOrders: false,
  };
}
