import { buildRegimeDecisionSnapshot } from "../persistence.ts";
import type { RegimeDecisionInput, RegimeSelectionResult } from "../types.ts";
import type { RegimeMlSnapshot } from "../ml/types.ts";

export function buildRegimeDecisionEvidence(
  result: RegimeSelectionResult,
  mlSnapshot: RegimeMlSnapshot,
  input: RegimeDecisionInput,
) {
  return buildRegimeDecisionSnapshot(result, mlSnapshot.features, mlSnapshot.prediction, {
    symbol: input.marketData.symbol,
    settingsVersion: input.baseSettingsVersion,
    baseSettings: (input.settings ?? {}) as Record<string, unknown>,
    modelVersion: input.mlArtifact?.model_version ?? null,
  });
}
