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
export {
  appendRegimeTransitionHistory,
  confirmedRegimeCondition,
  createConfirmedRegimeState,
  emptyRegimeHysteresisForMarket,
  evaluateRegimeDwellPolicy,
  recentRegimeConditionKeys,
  recoverRegimeHysteresisState,
} from "./hysteresis.ts";
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
export { REGIME_ML_FILE_INVENTORY, REGIME_ML_INITIAL_MODE, REGIME_ML_SHADOW_FORBIDDEN_ACTIONS } from "./ml/types.ts";
export { defaultRegimeMlValidationPlan, regimeMlInventoryStatus } from "./ml/validation.ts";
export {
  REGIME_DIAGNOSTICS_FILE_INVENTORY,
  buildRegimeDiagnosticsBundle,
  regimeDiagnosticsInventoryStatus,
} from "./diagnostics/diagnostics.ts";
export {
  REGIME_FRONTEND_ROLLOUT_PHASES,
  REGIME_ROLLOUT_FILE_INVENTORY,
  evaluateRegimeFrontendRolloutPolicy,
} from "./rollout/rollout-policy.ts";
export { compareRegimeShadowDecisions } from "./rollout/shadow-comparison.ts";
export { evaluateRegimePaperStability } from "./rollout/paper-stability.ts";
export { regimeFrontendRollbackPolicy } from "./rollout/rollback-policy.ts";
export { REGIME_ALLOWED_SHARED_COMPONENTS, regimeSharedBoundaryStatus } from "./shared-boundaries.ts";
export {
  REGIME_COMPATIBILITY_MATRIX,
  REGIME_LEGACY_ROUTING_MATRIX,
  REGIME_NO_DIRECTIONAL_REGIMES,
  canonicalRegimeRoutingStrategyId,
  contextMultiplierForSignal,
  dedupeRegimeStrategyIds,
  evaluateRoutingEligibility,
  hasMinimumIndependentFamilyParticipation,
  permittedDirectionForRegime,
  regimeCompatibilityMultiplier,
  regimeStrategyAggregationFamily,
  representedRegimeFamilies,
  routeRegimeStrategies,
  strategyIdsForConfirmedRegime,
} from "./router.ts";
export {
  buildRegimeDecisionEvidence,
  calculateRegimeDecision,
  buildRegimeMarketContext,
  emptyRegimeSelectionResult,
  regimeDecisionGateSettings,
  regimeTradeBlockers,
  resolveRegimeDecision,
  secondBestScoreForDirection,
  signedRegimeNetScore,
  winningDirectionScore,
} from "./decision-engine.ts";
export {
  activeDirectionalRegimeOutputs,
  aggregateRegimeStrategyScores,
  applyRegimeFamilyContributionCap,
  cappedRegimeStrategyContribution,
  regimeAbstentionRate,
  regimeSystemWeightMultiplier,
  votingDirectionalRegimeOutputs,
} from "./family-aggregation.ts";
export { resolveRegimeDynamicProfile } from "./dynamic-profile.ts";
export {
  baseRegimeSettingsFromTradingSettings,
  boundedRegimeEffectiveSettings,
  buildRegimeProfileModifierBreakdown,
  combineRegimeProfileModifiers,
  REGIME_PROFILE_MATRIX,
  resolveEffectiveRegimeSettings,
  validateEffectiveRegimeProfile,
  validateRegimeProfileModifiers,
} from "./dynamic-profile.ts";
export {
  REGIME_EXECUTION_PIPELINE,
  adaptRegimeBrokerReconciliation,
  buildRegimeBrokerAttribution,
  buildRegimeExecutionPipeline,
  buildRegimeOrderIntent,
  buildRegimeTargetOrder,
  generateRegimeOrderIntentIdempotencyKey,
  resolveRegimePositionEffect,
  validateRegimeOrderIntent,
  validateRegimeTargetOrder,
} from "./order-intent.ts";
export {
  calculateRegimePositionSize,
  calculateRegimePositionSizing,
  regimeLiquidityCap,
  regimePositionAndBuyingPowerCaps,
  regimeRiskBudget,
  regimeSizingBlockers,
  regimeStopDistance,
  regimeTargetDistance,
  signalStrengthMultiplierForWinningStrength,
} from "./position-sizing.ts";
export {
  directionalExitPolicy,
  eventRiskReductionPolicy,
  manageRegimeOpenPosition,
  minutesBetween,
  profitTargetExitPolicy,
  protectiveStopExitPolicy,
  reconcileRegimeOrderLifecycle,
  regimeTransitionExitPolicy,
  summarizeRegimeTradeHistory,
  timeExitPolicy,
} from "./trade-management.ts";
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
export { REGIME_BACKTEST_FILE_INVENTORY, REGIME_BACKTEST_OWNED_CAPABILITIES } from "./backtest/types.ts";
export {
  DEFAULT_REGIME_BACKTEST_COSTS,
  DEFAULT_REGIME_BACKTEST_GLOBAL_GATE,
  closeRegimeBacktestTrade,
  evaluateRegimeOpenPositionExit,
  simulateRegimeGlobalGate,
  simulateRegimeNextBarEntry,
  updateRegimeExcursion,
} from "./backtest/execution-simulator.ts";
export { regimeBacktestDiagnostics, regimeBacktestInventoryStatus } from "./backtest/diagnostics.ts";
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
