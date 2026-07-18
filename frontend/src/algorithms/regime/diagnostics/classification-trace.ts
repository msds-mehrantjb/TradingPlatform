import type { RegimeSelectionResult } from "../types.ts";

export type RegimeClassificationTrace = {
  algorithmId: "regime";
  rawRegime: string | null;
  confirmedRegime: string | null;
  axes: Record<string, string>;
  missingInputs: readonly string[];
  candidateRegime: string | null;
  confirmationCount: number;
  transitionReason: string | null;
};

export function buildRegimeClassificationTrace(result: RegimeSelectionResult): RegimeClassificationTrace {
  return {
    algorithmId: "regime",
    rawRegime: result.rawClassification?.rawRegime ?? null,
    confirmedRegime: result.confirmedState?.confirmedRegime ?? null,
    axes: result.rawClassification?.axes ?? {},
    missingInputs: result.rawClassification?.missingInputs ?? [],
    candidateRegime: result.confirmedState?.candidateRegime ?? null,
    confirmationCount: result.confirmationCount,
    transitionReason: result.confirmedState?.transitionReason ?? null,
  };
}
