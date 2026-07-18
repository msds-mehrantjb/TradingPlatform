import type { RegimeSelectionResult } from "../types.ts";
import { buildRegimeClassificationTrace, type RegimeClassificationTrace } from "./classification-trace.ts";
import { buildRegimeDecisionTrace, type RegimeDecisionTrace } from "./decision-trace.ts";
import { buildRegimeProfileAttribution, type RegimeProfileAttribution } from "./profile-attribution.ts";
import { buildRegimeStrategyAttribution, type RegimeStrategyAttribution } from "./strategy-attribution.ts";

export const REGIME_DIAGNOSTICS_FILE_INVENTORY = [
  "diagnostics.ts",
  "decision-trace.ts",
  "classification-trace.ts",
  "strategy-attribution.ts",
  "profile-attribution.ts",
] as const;

export type RegimeDiagnosticsFile = typeof REGIME_DIAGNOSTICS_FILE_INVENTORY[number];

export type RegimeDiagnosticsInventoryStatus = {
  algorithmId: "regime";
  files: readonly RegimeDiagnosticsFile[];
  ownedTraceTypes: readonly [
    "decision_trace",
    "classification_trace",
    "strategy_attribution",
    "profile_attribution",
  ];
  readOnly: true;
};

export type RegimeDiagnosticsBundle = {
  algorithmId: "regime";
  decisionTrace: RegimeDecisionTrace;
  classificationTrace: RegimeClassificationTrace;
  strategyAttribution: RegimeStrategyAttribution;
  profileAttribution: RegimeProfileAttribution;
};

export function regimeDiagnosticsInventoryStatus(): RegimeDiagnosticsInventoryStatus {
  return {
    algorithmId: "regime",
    files: REGIME_DIAGNOSTICS_FILE_INVENTORY,
    ownedTraceTypes: ["decision_trace", "classification_trace", "strategy_attribution", "profile_attribution"],
    readOnly: true,
  };
}

export function buildRegimeDiagnosticsBundle(result: RegimeSelectionResult): RegimeDiagnosticsBundle {
  return {
    algorithmId: "regime",
    decisionTrace: buildRegimeDecisionTrace(result),
    classificationTrace: buildRegimeClassificationTrace(result),
    strategyAttribution: buildRegimeStrategyAttribution(result),
    profileAttribution: buildRegimeProfileAttribution(result),
  };
}
