import type { RegimeMlArtifact, RegimeMlPromotionReport } from "./types.ts";
import type { RegimeSelectionResult } from "../types.ts";

export function evaluateRegimeMlPromotionPolicy(baseline: RegimeSelectionResult | null, artifact?: RegimeMlArtifact | null): RegimeMlPromotionReport {
  const leakageTestsPassed = artifact?.metrics.confirm_only_trading_results.leakageTestsPassed === 1;
  const classCoverageSufficient = Object.keys(artifact?.class_distribution ?? {}).length >= 4;
  const walkForwardStable = artifact?.metrics.confirm_only_trading_results.walkForwardStable === 1;
  const calibrationAcceptable = typeof artifact?.metrics.calibration_error === "number" && artifact.metrics.calibration_error <= 0.08;
  const improvesOrPreservesDrawdownAndExpectancy = artifact?.metrics.confirm_only_trading_results.preservesDrawdownAndExpectancy === 1;
  const notDependentOnIsolatedPeriod = artifact?.metrics.confirm_only_trading_results.notDependentOnIsolatedPeriod === 1;
  const fallbackAvailable = true;
  const rollbackArtifactRetained = artifact?.metrics.confirm_only_trading_results.rollbackArtifactRetained === 1;
  const promoted =
    leakageTestsPassed &&
    classCoverageSufficient &&
    walkForwardStable &&
    calibrationAcceptable &&
    improvesOrPreservesDrawdownAndExpectancy &&
    notDependentOnIsolatedPeriod &&
    fallbackAvailable &&
    rollbackArtifactRetained;
  return {
    promoted,
    targetMode: promoted ? "confirm_only" : "shadow",
    reasonCodes: promoted ? ["regime.ml.promotion_confirm_only_allowed"] : ["regime.ml.promotion_shadow_only_until_validated"],
    leakageTestsPassed,
    classCoverageSufficient,
    walkForwardStable,
    calibrationAcceptable,
    improvesOrPreservesDrawdownAndExpectancy,
    notDependentOnIsolatedPeriod,
    fallbackAvailable,
    rollbackArtifactRetained,
    baseline,
  };
}
