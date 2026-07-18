export { canonicalRegimeRoutingStrategyId, dedupeRegimeStrategyIds } from "./routing/alias-deduplication.ts";
export {
  REGIME_COMPATIBILITY_MATRIX,
  REGIME_LEGACY_ROUTING_MATRIX,
  REGIME_NO_DIRECTIONAL_REGIMES,
  legacyRegimeId,
  permittedDirectionForRegime,
  strategyIdsForConfirmedRegime,
  strategyIdsForRegimeRoutingKey,
} from "./routing/compatibility-matrix.ts";
export {
  contextMultiplierForSignal,
  correlationPenalty,
  regimeCompatibilityMultiplier,
  reliabilityMultiplier,
} from "./routing/conflict-resolution.ts";
export {
  hasMinimumIndependentFamilyParticipation,
  regimeStrategyAggregationFamily,
  representedRegimeFamilies,
} from "./routing/regime-family-map.ts";
export { routeRegimeStrategies, regimeSelectedStrategySlugs } from "./routing/router.ts";
export {
  evaluateRoutingEligibility,
  regimeStrategyAvoidReason,
  regimeStrategySelectorReason,
  skippedStrategyReason,
} from "./routing/strategy-eligibility.ts";
