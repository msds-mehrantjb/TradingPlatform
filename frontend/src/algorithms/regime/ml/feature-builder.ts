import type { RegimeMlFeatureVector } from "./types.ts";
import type { RegimeSelectionResult } from "../types.ts";

export function buildRegimeMlFeatures(result: RegimeSelectionResult): RegimeMlFeatureVector {
  const values: Record<string, number | string | boolean | null> = {
    signal: result.signal,
    rawRuleRegime: result.rawClassification?.rawRegime ?? null,
    confirmedRuleRegime: result.confirmedState?.confirmedRegime ?? null,
    rawRegimeConfidence: result.rawClassification?.confidence ?? null,
    confirmedRegimeConfidence: result.confirmedState?.confirmedConfidence ?? result.confidence,
    transitionCandidateRegime: result.confirmedState?.candidateRegime ?? null,
    transitionCandidateCount: result.confirmedState?.candidateCount ?? null,
    dwellBars: result.confirmedState?.dwellBars ?? null,
    conditionHeld: result.conditionHeld,
    buyScore: result.buyScore,
    sellScore: result.sellScore,
    winningScore: result.winningScore,
    directionalEdge: result.directionalEdge,
    activeStrategyCount: result.activeStrategyCount,
    activeFamilyCount: result.activeFamilyCount,
    abstentionRate: result.abstentionRate,
    selectedStrategyCount: result.selectedStrategyCount,
    tradeAllowed: result.tradeAllowed,
    volatilityState: result.volatility,
    opportunityState: result.opportunity,
    axisDirection: result.rawClassification?.axes.direction ?? null,
    axisVolatility: result.rawClassification?.axes.volatility ?? null,
    axisStructure: result.rawClassification?.axes.structure ?? null,
    axisLiquidity: result.rawClassification?.axes.liquidity ?? null,
    axisSession: result.rawClassification?.axes.session ?? null,
    axisEventRisk: result.rawClassification?.axes.eventRisk ?? null,
    rawBullScore: numericEvidence(result, "bullScore"),
    rawBearScore: numericEvidence(result, "bearScore"),
    rawAdx: numericEvidence(result, "adx"),
    rawAtrPercent: numericEvidence(result, "atrPercent"),
    rawAtrPercentile: numericEvidence(result, "atrPercentile"),
    rawRealizedVolatility: numericEvidence(result, "realizedVolatility"),
    rawRelativeVolume: numericEvidence(result, "relativeVolume"),
    rawSpreadPercent: numericEvidence(result, "spreadPercent"),
  };
  return {
    featureVersion: "regime_ml_features_v1",
    values,
    missingFeatureMask: Object.fromEntries(Object.entries(values).map(([key, value]) => [key, value === null])),
    decisionTimestamp: result.confirmedState?.timestamp ?? result.rawClassification?.timestamp ?? "",
  };
}

function numericEvidence(result: RegimeSelectionResult, key: string): number | null {
  const value = result.rawClassification?.evidence[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
