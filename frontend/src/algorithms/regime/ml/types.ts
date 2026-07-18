import type { MarketCandle } from "../../../trading/shared/market-data-types.ts";
import type { MarketRegimeId, RegimeFamilyScore, RegimeNoTradeTag, RegimeSelectionResult } from "../types.ts";

export type RegimeMlMode = "off" | "shadow" | "confirm_only" | "active";

export type RegimeMlModelType =
  | "multinomial_logistic_regression"
  | "regularized_transition_logistic_regression"
  | "tree_baseline";

export type RegimeMlFeatureVector = {
  featureVersion: "regime_ml_features_v1";
  values: Record<string, number | string | boolean | null>;
  missingFeatureMask: Record<string, boolean>;
  decisionTimestamp: string;
};

export type RegimeMlArtifact = {
  algorithm_id: "regime";
  model_version: string;
  feature_schema_version: RegimeMlFeatureVector["featureVersion"];
  label_version: string;
  training_start: string;
  training_end: string;
  validation_periods: Array<{ start: string; end: string }>;
  test_period: { start: string; end: string } | null;
  model_type: RegimeMlModelType;
  hyperparameters: Record<string, number | string | boolean | null>;
  metrics: RegimeMlValidationMetrics;
  class_distribution: Record<string, number>;
  calibration_data: Array<{ predicted: number; observed: number; count: number }>;
  feature_names: string[];
  feature_imputation_policy: Record<string, "zero" | "median" | "mode" | "missing_indicator" | "none">;
  artifact_hash: string;
  created_at: string;
  promotion_status: "untrusted" | "shadow" | "confirm_only" | "active" | "rollback";
  trusted: boolean;
  unsupported?: boolean;
  coefficients?: Record<string, Record<string, number>>;
  intercepts?: Record<string, number>;
  transition_coefficients?: Record<string, number>;
  transition_intercept?: number;
};

export type RegimeMlPrediction = {
  enabled: boolean;
  mode: RegimeMlMode;
  probabilityVector: Partial<Record<MarketRegimeId, number>>;
  predictedRegime: MarketRegimeId | null;
  transitionProbability: number | null;
  deterministicStabilityConfidence: number | null;
  missingFeatureMask: Record<string, boolean>;
  reasonCodes: string[];
};

export type RegimeMlSnapshot = {
  mode: RegimeMlMode;
  features: RegimeMlFeatureVector | null;
  prediction: RegimeMlPrediction;
  appliedEffect: "none" | "shadow_only" | "blocked_transition" | "reduced_confidence" | "reduced_size";
  reasonCodes: string[];
};

export type RegimeDecisionSnapshot = {
  algorithm_id: "regime";
  algorithmVersion: string;
  settingsVersion: string;
  strategyVersion: string;
  profileVersion: string;
  modelVersion: string | null;
  symbol: string;
  dataTimestamp: string;
  decisionId: string;
  orderId: string | null;
  decisionTimestamp: string;
  pointInTimeFeatures: RegimeMlFeatureVector | null;
  axes: Record<string, string>;
  missingInputs: string[];
  rawRuleRegime: MarketRegimeId | RegimeNoTradeTag;
  confirmedRuleRegime: MarketRegimeId | RegimeNoTradeTag;
  hysteresisState: Record<string, unknown> | null;
  selectedStrategies: RegimeSelectionResult["selectedStrategies"];
  skippedStrategies: RegimeSelectionResult["skippedStrategies"];
  contextResults: unknown[];
  safetyResults: unknown[];
  familyAggregation: RegimeFamilyScore[];
  baseSettings: Record<string, unknown>;
  effectiveSettings: Record<string, unknown> | null;
  mlMode: RegimeMlMode;
  mlProbabilityVector: Partial<Record<MarketRegimeId, number>>;
  mlPredictedRegime: MarketRegimeId | null;
  transitionProbability: number | null;
  missingFeatureMask: Record<string, boolean>;
  globalGateOutcome: Record<string, unknown> | null;
  brokerReconciliationResult: Record<string, unknown> | null;
  strategyOutputs: RegimeSelectionResult["selectedStrategies"];
  familyScores: RegimeFamilyScore[];
  effectiveProfile: Record<string, number | string | boolean | null>;
  finalDecision: {
    signal: RegimeSelectionResult["signal"];
    tradeAllowed: boolean;
    tradeBlockers: string[];
    buyScore: number;
    sellScore: number;
    directionalEdge: number;
  };
  subsequentRealizedRegimeLabel: null;
  subsequentTradeResultRef: null;
};

export type RegimeLabelDefinition = {
  algorithm_id: "regime";
  label_definition_version: string;
  future_observation_window_bars: number;
  thresholds: Record<string, number>;
  label_timestamp: string;
  source_candle_range: { start: string; end: string };
};

export type RegimeOfflineLabel = RegimeLabelDefinition & {
  realizedRegime: MarketRegimeId;
  transitionOccurred: boolean;
};

export type RegimeMlValidationMetrics = {
  macro_f1: number | null;
  per_regime_precision_recall: Record<string, { precision: number | null; recall: number | null }>;
  balanced_accuracy: number | null;
  log_loss: number | null;
  brier_score: number | null;
  calibration_error: number | null;
  confusion_matrix: Record<string, Record<string, number>>;
  transition_detection_delay_bars: number | null;
  confirm_only_trading_results: Record<string, number | string | null>;
  performance_by_year: Record<string, Record<string, number | null>>;
  performance_by_volatility_state: Record<string, Record<string, number | null>>;
};

export type RegimeMlValidationPlan = {
  validationType: "time_ordered";
  expandingWindowWalkForward: boolean;
  rollingWindowWalkForward: boolean;
  purgeLabelWindowBars: number;
  embargoBars: number;
  finalTestPeriodUntouched: boolean;
  baselines: Array<"most_common_regime" | "previous_regime" | "deterministic_rule_classifier" | "random">;
  reportedMetrics: Array<keyof RegimeMlValidationMetrics>;
};

export type RegimeMlArtifactLoadResult = {
  artifact: RegimeMlArtifact | null;
  loaded: boolean;
  reasonCodes: string[];
};

export type RegimeMlPromotionReport = {
  promoted: boolean;
  targetMode: RegimeMlMode;
  reasonCodes: string[];
  leakageTestsPassed: boolean;
  classCoverageSufficient: boolean;
  walkForwardStable: boolean;
  calibrationAcceptable: boolean;
  improvesOrPreservesDrawdownAndExpectancy: boolean;
  notDependentOnIsolatedPeriod: boolean;
  fallbackAvailable: boolean;
  rollbackArtifactRetained: boolean;
  baseline: RegimeSelectionResult | null;
};

export type RegimeLabelBuildInput = {
  decisionTimestamp: string;
  futureCandles: MarketCandle[];
  labelDefinitionVersion: string;
  futureObservationWindowBars: number;
  thresholds: Record<string, number>;
};
