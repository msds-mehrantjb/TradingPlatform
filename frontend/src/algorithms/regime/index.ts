export {
  buildRawRegimeCondition,
  classifyRawRegime,
  classifyRegimeAxes,
  compositeRegimeIdFromAxes,
} from "./classifier.ts";
export { DEFAULT_REGIME_HYSTERESIS_SETTINGS, resolveRegimeHysteresisSettings } from "./config.ts";
export { confirmedRegimeCondition } from "./hysteresis.ts";
export { loadRegimeMlArtifact, validateRegimeMlArtifact } from "./ml/artifact-loader.ts";
export { buildRegimeMlFeatures } from "./ml/feature-builder.ts";
export { buildOfflineRegimeLabel } from "./ml/label-builder.ts";
export { predictRegimeMl } from "./ml/predictor.ts";
export { evaluateRegimeMlPromotionPolicy } from "./ml/promotion-policy.ts";
export { defaultRegimeMlValidationPlan } from "./ml/validation.ts";
export {
  contextMultiplierForSignal,
  regimeStrategyAggregationFamily,
  routeRegimeStrategies,
} from "./router.ts";
export {
  calculateRegimeDecision,
  buildRegimeMarketContext,
  emptyRegimeSelectionResult,
  resolveRegimeDecision,
  secondBestScoreForDirection,
  signedRegimeNetScore,
  winningDirectionScore,
} from "./decision-engine.ts";
export { resolveRegimeDynamicProfile } from "./dynamic-profile.ts";
export {
  baseRegimeSettingsFromTradingSettings,
  buildRegimeProfileModifierBreakdown,
  combineRegimeProfileModifiers,
  resolveEffectiveRegimeSettings,
  REGIME_PROFILE_VERSION,
} from "./dynamic-profile.ts";
export { buildRegimeOrderIntent, buildRegimeTargetOrder, generateRegimeOrderIntentIdempotencyKey, resolveRegimePositionEffect } from "./order-intent.ts";
export { calculateRegimePositionSize, calculateRegimePositionSizing, signalStrengthMultiplierForWinningStrength } from "./position-sizing.ts";
export { manageRegimeOpenPosition } from "./trade-management.ts";
export { regimeBacktestCacheKey, runRegimeBacktest } from "./backtest/engine.ts";
export { runNodeRegimeBacktest } from "./backtest/runner.ts";
export { runRegimeWalkForward } from "./backtest/walk-forward.ts";
export {
  buildDirectionalStrategyResult,
  evaluateRegimeStrategyDefinition,
  regimeSelectionStrategies,
  validateDirectionalStrategyResult,
} from "./strategy-catalog.ts";
export type { RegimeOrderIntent, RegimeTargetOrder } from "./order-intent.ts";
export type { RegimePositionSizingResult } from "./position-sizing.ts";
export type * from "./backtest/types.ts";
export type * from "./types.ts";
