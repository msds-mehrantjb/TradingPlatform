import { REGIME_MIN_INDEPENDENT_FAMILIES } from "../config.ts";
import { clampNumber } from "../indicators.ts";
import { evaluateRegimeStrategyDefinition, regimeSelectionStrategies } from "../strategy-catalog.ts";
import type {
  ContextResult,
  MarketRegimeId,
  RegimeMarketContext,
  RegimeOpportunityState,
  RegimePrimaryTrend,
  RegimeVolatilityState,
  SafetyGateResult,
  StrategyRoutingResult,
} from "../types.ts";
import { aliasDeduplicationForSelectedStrategies, dedupeRegimeStrategyIds } from "./alias-deduplication.ts";
import {
  legacyRegimeId,
  permittedDirectionForRegime,
  strategyIdsForConfirmedRegime,
  strategyIdsForRegimeRoutingKey,
} from "./compatibility-matrix.ts";
import { contextBaseMultiplier } from "./conflict-resolution.ts";
import { hasMinimumIndependentFamilyParticipation, representedRegimeFamilies } from "./regime-family-map.ts";
import { evaluateRoutingEligibility, skippedStrategyReason } from "./strategy-eligibility.ts";

export function routeRegimeStrategies(confirmedRegime: MarketRegimeId, market: RegimeMarketContext): StrategyRoutingResult {
  const selectedStrategyIds = dedupeRegimeStrategyIds(strategyIdsForConfirmedRegime(confirmedRegime));
  const selectedStrategyIdSet = new Set(selectedStrategyIds);
  const permittedDirection = permittedDirectionForRegime(confirmedRegime);
  const eligibility = regimeSelectionStrategies.map((strategy) =>
    evaluateRoutingEligibility(strategy, confirmedRegime, selectedStrategyIdSet, permittedDirection),
  );
  const skippedStrategies = regimeSelectionStrategies
    .filter((strategy) => strategy.role === "directional" && !selectedStrategyIdSet.has(strategy.id))
    .map((strategy) => ({
      strategyId: strategy.id,
      reason: skippedStrategyReason(strategy, confirmedRegime),
    }));
  const contextResults = regimeSelectionStrategies
    .filter((strategy) => strategy.role === "confirmation" || strategy.role === "regime_context")
    .map((strategy) => contextResultFromStrategy(strategy, market));
  const safetyResults = regimeSelectionStrategies
    .filter((strategy) => strategy.role === "safety_gate")
    .map((strategy) => safetyResultFromStrategy(strategy, market));
  const representedFamilies = representedRegimeFamilies(selectedStrategyIds);

  return {
    confirmedRegime,
    selectedStrategyIds,
    skippedStrategies,
    contextResults,
    safetyResults,
    incompatibleStrategyIds: eligibility.filter((entry) => entry.incompatible).map((entry) => entry.strategyId),
    permittedDirection,
    representedFamilies,
    aliasDeduplication: aliasDeduplicationForSelectedStrategies(selectedStrategyIds),
    minimumIndependentFamilyParticipationMet: hasMinimumIndependentFamilyParticipation(
      selectedStrategyIds,
      REGIME_MIN_INDEPENDENT_FAMILIES,
    ),
    abstainedStrategyIds: eligibility.filter((entry) => entry.shouldAbstain).map((entry) => entry.strategyId),
    disabledStrategyIds: eligibility.filter((entry) => entry.disabled).map((entry) => entry.strategyId),
    unhealthyStrategyIds: eligibility.filter((entry) => entry.unhealthy).map((entry) => entry.strategyId),
  };
}

export function regimeSelectedStrategySlugs(
  primaryTrend: RegimePrimaryTrend,
  volatility: RegimeVolatilityState,
  opportunity: RegimeOpportunityState,
): string[] {
  return dedupeRegimeStrategyIds(strategyIdsForRegimeRoutingKey(legacyRegimeId(primaryTrend, volatility, opportunity)));
}

function contextResultFromStrategy(strategy: Parameters<typeof evaluateRegimeStrategyDefinition>[0], market: RegimeMarketContext): ContextResult {
  const raw = evaluateRegimeStrategyDefinition(strategy, market);
  return {
    strategyId: strategy.id,
    role: strategy.role === "confirmation" ? "confirmation" : "regime_context",
    eligible: raw.eligible !== false,
    multiplier: contextBaseMultiplier(raw.signal, raw.confidence, raw.eligible !== false),
    reason: raw.reason,
    signal: raw.signal,
    confidence: clampNumber(raw.confidence, 0, 1),
  };
}

function safetyResultFromStrategy(strategy: Parameters<typeof evaluateRegimeStrategyDefinition>[0], market: RegimeMarketContext): SafetyGateResult {
  const raw = evaluateRegimeStrategyDefinition(strategy, market);
  return {
    strategyId: strategy.id,
    passed: raw.passed !== false,
    blockNewEntries: raw.blockNewEntries === true,
    reason: raw.reason,
  };
}
