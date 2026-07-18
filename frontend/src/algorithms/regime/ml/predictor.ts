import type { RegimeMlArtifact, RegimeMlFeatureVector, RegimeMlPrediction } from "./types.ts";
import type { MarketRegimeId } from "../types.ts";

export function predictRegimeMl(features: RegimeMlFeatureVector | null, artifact: RegimeMlArtifact | null, mode: RegimeMlPrediction["mode"]): RegimeMlPrediction {
  if (mode === "off") {
    return disabledPrediction(mode, features, ["regime.ml.off"]);
  }
  if (!features || !artifact) {
    return disabledPrediction(mode, features, ["regime.ml.no_loaded_artifact"]);
  }
  if (!artifact.coefficients || !artifact.intercepts) {
    return disabledPrediction(mode, features, ["regime.ml.artifact_has_no_supported_parameters"]);
  }
  const probabilityVector = softmaxScores(features, artifact);
  const predictedRegime = highestProbabilityRegime(probabilityVector);
  const transitionProbability =
    artifact.transition_coefficients || typeof artifact.transition_intercept === "number"
      ? sigmoid((artifact.transition_intercept ?? 0) + Object.entries(artifact.transition_coefficients ?? {}).reduce((sum, [feature, coefficient]) => sum + featureValue(features, feature) * coefficient, 0))
      : null;
  const deterministicRegime = String(features.values.confirmedRuleRegime ?? "");
  const deterministicStabilityConfidence = deterministicRegime && isMarketRegimeId(deterministicRegime)
    ? probabilityVector[deterministicRegime] ?? null
    : null;
  return {
    enabled: true,
    mode,
    probabilityVector,
    predictedRegime,
    transitionProbability,
    deterministicStabilityConfidence,
    missingFeatureMask: features.missingFeatureMask,
    reasonCodes: ["regime.ml.prediction_shadow_safe"],
  };
}

function disabledPrediction(mode: RegimeMlPrediction["mode"], features: RegimeMlFeatureVector | null, reasonCodes: string[]): RegimeMlPrediction {
  return {
    enabled: false,
    mode,
    probabilityVector: {},
    predictedRegime: null,
    transitionProbability: null,
    deterministicStabilityConfidence: null,
    missingFeatureMask: features?.missingFeatureMask ?? {},
    reasonCodes,
  };
}

function softmaxScores(features: RegimeMlFeatureVector, artifact: RegimeMlArtifact): Partial<Record<MarketRegimeId, number>> {
  const scores = Object.entries(artifact.coefficients ?? {}).map(([regime, coefficients]) => {
    const score = (artifact.intercepts?.[regime] ?? 0) + Object.entries(coefficients).reduce((sum, [feature, coefficient]) => sum + featureValue(features, feature) * coefficient, 0);
    return [regime, score] as const;
  });
  const maxScore = Math.max(...scores.map(([, score]) => score), 0);
  const expScores = scores.map(([regime, score]) => [regime, Math.exp(score - maxScore)] as const);
  const total = expScores.reduce((sum, [, score]) => sum + score, 0);
  return Object.fromEntries(expScores.filter(([regime]) => isMarketRegimeId(regime)).map(([regime, score]) => [regime, total > 0 ? score / total : 0]));
}

function highestProbabilityRegime(probabilityVector: Partial<Record<MarketRegimeId, number>>): MarketRegimeId | null {
  return (Object.entries(probabilityVector).sort((left, right) => right[1] - left[1])[0]?.[0] as MarketRegimeId | undefined) ?? null;
}

function featureValue(features: RegimeMlFeatureVector, featureName: string): number {
  const value = features.values[featureName];
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "boolean") return value ? 1 : 0;
  return 0;
}

function sigmoid(value: number): number {
  return 1 / (1 + Math.exp(-value));
}

function isMarketRegimeId(value: string): value is MarketRegimeId {
  return [
    "strong_uptrend",
    "weak_uptrend",
    "strong_downtrend",
    "weak_downtrend",
    "range_bound",
    "sideways_range",
    "opening_breakout",
    "intraday_expansion",
    "high_volatility_trend",
    "low_volatility_quiet",
    "failed_breakout_reversal",
    "choppy_mixed",
    "gap_session",
    "event_risk",
    "liquidity_stress",
    "extreme_volatility_no_trade",
  ].includes(value);
}
