import type { RegimeMlArtifact, RegimeMlArtifactLoadResult, RegimeMlFeatureVector } from "./types.ts";

export function loadDisabledRegimeMlArtifact(): RegimeMlArtifact {
  return {
    algorithm_id: "regime",
    model_version: "regime_ml_disabled_v1",
    feature_schema_version: "regime_ml_features_v1",
    label_version: "none",
    training_start: "",
    training_end: "",
    validation_periods: [],
    test_period: null,
    model_type: "multinomial_logistic_regression",
    hyperparameters: {},
    metrics: emptyMetrics(),
    class_distribution: {},
    calibration_data: [],
    feature_names: [],
    feature_imputation_policy: {},
    artifact_hash: "",
    created_at: "",
    promotion_status: "untrusted",
    trusted: false,
    unsupported: true,
  };
}

export function loadRegimeMlArtifact(
  artifact: RegimeMlArtifact | null | undefined,
  features: RegimeMlFeatureVector,
  decisionTimestamp: string,
): RegimeMlArtifactLoadResult {
  if (!artifact) {
    return { artifact: null, loaded: false, reasonCodes: ["regime.ml.no_artifact"] };
  }
  const reasonCodes = validateRegimeMlArtifact(artifact, features, decisionTimestamp);
  return {
    artifact: reasonCodes.length ? null : artifact,
    loaded: reasonCodes.length === 0,
    reasonCodes,
  };
}

export function validateRegimeMlArtifact(
  artifact: RegimeMlArtifact,
  features: RegimeMlFeatureVector,
  decisionTimestamp: string,
): string[] {
  const reasons: string[] = [];
  if (artifact.algorithm_id !== "regime") reasons.push("regime.ml.artifact_wrong_algorithm");
  if (artifact.feature_schema_version !== features.featureVersion) reasons.push("regime.ml.feature_schema_mismatch");
  if (!artifact.artifact_hash || !/^[a-f0-9]{16,128}$/i.test(artifact.artifact_hash)) reasons.push("regime.ml.invalid_artifact_hash");
  if (artifact.training_end && new Date(artifact.training_end).getTime() > new Date(decisionTimestamp).getTime()) reasons.push("regime.ml.training_after_decision");
  if (artifact.trusted !== true || artifact.promotion_status === "untrusted") reasons.push("regime.ml.artifact_untrusted");
  if (artifact.unsupported || !["multinomial_logistic_regression", "regularized_transition_logistic_regression", "tree_baseline"].includes(artifact.model_type)) {
    reasons.push("regime.ml.unsupported_model_version");
  }
  artifact.feature_names.forEach((featureName) => {
    if (features.missingFeatureMask[featureName] === true && artifact.feature_imputation_policy[featureName] === "none") {
      reasons.push(`regime.ml.required_feature_unavailable:${featureName}`);
    }
  });
  return reasons;
}

function emptyMetrics() {
  return {
    macro_f1: null,
    per_regime_precision_recall: {},
    balanced_accuracy: null,
    log_loss: null,
    brier_score: null,
    calibration_error: null,
    confusion_matrix: {},
    transition_detection_delay_bars: null,
    confirm_only_trading_results: {},
    performance_by_year: {},
    performance_by_volatility_state: {},
  };
}
