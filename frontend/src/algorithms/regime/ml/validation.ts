import type { RegimeMlValidationPlan } from "./types.ts";

export function defaultRegimeMlValidationPlan(): RegimeMlValidationPlan {
  return {
    validationType: "time_ordered",
    expandingWindowWalkForward: true,
    rollingWindowWalkForward: true,
    purgeLabelWindowBars: 30,
    embargoBars: 30,
    finalTestPeriodUntouched: true,
    baselines: ["most_common_regime", "previous_regime", "deterministic_rule_classifier", "random"],
    reportedMetrics: [
      "macro_f1",
      "per_regime_precision_recall",
      "balanced_accuracy",
      "log_loss",
      "brier_score",
      "calibration_error",
      "confusion_matrix",
      "transition_detection_delay_bars",
      "confirm_only_trading_results",
      "performance_by_year",
      "performance_by_volatility_state",
    ],
  };
}
