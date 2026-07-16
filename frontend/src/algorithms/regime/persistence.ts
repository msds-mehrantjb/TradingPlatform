import type { RegimeDecisionSnapshot, RegimeMlFeatureVector, RegimeMlPrediction } from "./ml/types.ts";
import type { RegimeSelectionResult } from "./types.ts";

export const REGIME_ALGORITHM_VERSION = "regime_algorithm_v2";
export const REGIME_SETTINGS_VERSION = "regime_base_settings_v1";
export const REGIME_STRATEGY_VERSION = "regime_strategy_catalog_v2";
export const REGIME_PROFILE_VERSION_FOR_PERSISTENCE = "regime_profile_matrix_v1";

export const REGIME_STORAGE_KEYS = {
  tradingSettings: "regime-selection-trading-settings-v1",
  targetOrderOverrides: "regime-selection-target-order-overrides-v1",
  tradeHistory: "trading-dashboard.regime-trade-history.v1",
  orderControlModes: "trading-dashboard.regime-order-control-modes.v1",
  orderControlOverrides: "trading-dashboard.regime-order-control-overrides.v1",
} as const;

export function buildRegimeDecisionSnapshot(
  result: RegimeSelectionResult,
  features: RegimeMlFeatureVector | null,
  prediction: RegimeMlPrediction,
  metadata: {
    symbol?: string;
    settingsVersion?: string;
    strategyVersion?: string;
    profileVersion?: string;
    modelVersion?: string | null;
    baseSettings?: Record<string, unknown>;
    globalGateOutcome?: Record<string, unknown> | null;
    brokerReconciliationResult?: Record<string, unknown> | null;
    orderId?: string | null;
  } = {},
): RegimeDecisionSnapshot {
  const decisionTimestamp = result.confirmedState?.timestamp ?? result.rawClassification?.timestamp ?? "";
  const symbol = metadata.symbol ?? "UNKNOWN";
  const decisionId = [
    "regime",
    symbol,
    decisionTimestamp,
    result.confirmedState?.confirmedRegime ?? result.rawClassification?.rawRegime ?? "no_trade",
    result.signal,
  ].join(":");
  return {
    algorithm_id: "regime",
    algorithmVersion: REGIME_ALGORITHM_VERSION,
    settingsVersion: metadata.settingsVersion ?? REGIME_SETTINGS_VERSION,
    strategyVersion: metadata.strategyVersion ?? REGIME_STRATEGY_VERSION,
    profileVersion: metadata.profileVersion ?? result.effectiveSettings?.profileVersion ?? REGIME_PROFILE_VERSION_FOR_PERSISTENCE,
    modelVersion: metadata.modelVersion ?? null,
    symbol,
    dataTimestamp: decisionTimestamp,
    decisionId,
    orderId: metadata.orderId ?? null,
    decisionTimestamp,
    pointInTimeFeatures: features,
    axes: result.rawClassification?.axes ?? {},
    missingInputs: result.rawClassification?.missingInputs ?? [],
    rawRuleRegime: result.rawClassification?.rawRegime ?? "no_trade",
    confirmedRuleRegime: result.confirmedState?.confirmedRegime ?? "no_trade",
    hysteresisState: result.confirmedState ?? null,
    selectedStrategies: result.selectedStrategies,
    skippedStrategies: result.skippedStrategies,
    contextResults: result.routing?.contextResults ?? [],
    safetyResults: result.routing?.safetyResults ?? [],
    familyAggregation: result.familyScores ?? [],
    baseSettings: metadata.baseSettings ?? {},
    effectiveSettings: result.effectiveSettings ?? null,
    mlMode: prediction.mode,
    mlProbabilityVector: prediction.probabilityVector,
    mlPredictedRegime: prediction.predictedRegime,
    transitionProbability: prediction.transitionProbability,
    missingFeatureMask: prediction.missingFeatureMask,
    globalGateOutcome: metadata.globalGateOutcome ?? null,
    brokerReconciliationResult: metadata.brokerReconciliationResult ?? null,
    strategyOutputs: result.selectedStrategies,
    familyScores: result.familyScores ?? [],
    effectiveProfile: {
      confidence: result.confidence,
      winningScore: result.winningScore,
      directionalEdge: result.directionalEdge,
      activeFamilyCount: result.activeFamilyCount,
      abstentionRate: result.abstentionRate,
    },
    finalDecision: {
      signal: result.signal,
      tradeAllowed: result.tradeAllowed,
      tradeBlockers: result.tradeBlockers,
      buyScore: result.buyScore,
      sellScore: result.sellScore,
      directionalEdge: result.directionalEdge,
    },
    subsequentRealizedRegimeLabel: null,
    subsequentTradeResultRef: null,
  };
}
