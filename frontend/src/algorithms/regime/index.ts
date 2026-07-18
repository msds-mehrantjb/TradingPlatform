export {
  buildRawRegimeCondition,
  classifyRawRegime,
  classifyRegimeAxes,
  compositeRegimeIdFromAxes,
} from "./classifier.ts";
export {
  isCanonicalMarketRegimeId,
  isLegacyRegimeAlias,
  REGIME_COMPOSITE_REGIME_IDS,
  REGIME_LEGACY_ALIASES,
  REGIME_OPPORTUNITY_TAGS,
} from "./classification/composite-regimes.ts";
export { isRegimeRiskOffTransition, REGIME_RISK_OFF_REGIME_IDS } from "./classification/transition-policy.ts";
export { DEFAULT_REGIME_HYSTERESIS_SETTINGS, resolveRegimeHysteresisSettings } from "./config.ts";
export {
  REGIME_ALGORITHM_ID,
  REGIME_ALGORITHM_VERSION,
  REGIME_IDENTITY_CONTRACT_FILES,
  REGIME_PROFILE_VERSION,
  REGIME_SETTINGS_VERSION,
  REGIME_STRATEGY_CATALOG_VERSION,
} from "./versions.ts";
export {
  validateRegimeHysteresisSettings,
  validateRegimeIdentityContracts,
  validateRegimeTradingSettings,
} from "./validation.ts";
export { confirmedRegimeCondition } from "./hysteresis.ts";
export { buildRegimeFeatureSnapshot, buildRegimeFeatureSnapshotFromContext } from "./market/feature-snapshot.ts";
export {
  emptyRegimeContextFeeds,
  normalizeRegimeContextFeeds,
  regimeContextFeedsFromSharedSnapshot,
  resolveRegimeQuoteFreshness,
} from "./market/context-feeds.ts";
export { buildRegimeMarketSnapshot } from "./market/market-snapshot.ts";
export { resolveRegimeSessionContext } from "./market/session-context.ts";
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
} from "./dynamic-profile.ts";
export { buildRegimeOrderIntent, buildRegimeTargetOrder, generateRegimeOrderIntentIdempotencyKey, resolveRegimePositionEffect } from "./order-intent.ts";
export { calculateRegimePositionSize, calculateRegimePositionSizing, signalStrengthMultiplierForWinningStrength } from "./position-sizing.ts";
export { manageRegimeOpenPosition } from "./trade-management.ts";
export {
  REGIME_CONFIRMATION_MODULE_INVENTORY,
  REGIME_CONTEXT_MODULE_INVENTORY,
  REGIME_DIRECTIONAL_STRATEGY_INVENTORY,
  REGIME_SAFETY_GATE_INVENTORY,
  REGIME_STRATEGY_ROLE_INVENTORY,
  REGIME_TOTAL_STRATEGY_DEFINITION_COUNT,
} from "./strategies/registry.ts";
export { REGIME_STRATEGY_ALIAS_INVENTORY, REGIME_STRATEGY_ALIAS_MAP } from "./strategies/alias-map.ts";
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
export type * from "./classification/composite-regimes.ts";
export type * from "./market/context-feeds.ts";
export type * from "./market/feature-snapshot.ts";
export type * from "./market/market-snapshot.ts";
export type * from "./backtest/types.ts";
export type * from "./types.ts";
