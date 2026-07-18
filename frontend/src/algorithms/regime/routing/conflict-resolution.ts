import { clampNumber, roundNumber } from "../indicators.ts";
import type {
  ContextResult,
  MarketRegimeId,
  RegimeStrategyDefinition,
  RegimeStrategySignal,
} from "../types.ts";
import { REGIME_NO_DIRECTIONAL_REGIMES, REGIME_COMPATIBILITY_MATRIX } from "./compatibility-matrix.ts";

export function contextMultiplierForSignal(
  strategy: RegimeStrategyDefinition,
  signal: RegimeStrategySignal,
  contextResults: ContextResult[],
): number {
  if (strategy.role !== "directional" || signal === "hold") {
    return 1;
  }
  return roundNumber(
    contextResults.reduce((multiplier, context) => multiplier * contextCompatibilityMultiplier(context, signal), 1),
    4,
  );
}

export function regimeCompatibilityMultiplier(strategyId: string, confirmedRegime: MarketRegimeId): number {
  const selected = REGIME_COMPATIBILITY_MATRIX[confirmedRegime]?.includes(strategyId) ?? false;
  return selected && !REGIME_NO_DIRECTIONAL_REGIMES.has(confirmedRegime) ? 1 : 0;
}

export function reliabilityMultiplier(_strategy: RegimeStrategyDefinition): number {
  return 1;
}

export function correlationPenalty(_strategy: RegimeStrategyDefinition): number {
  return 1;
}

export function contextBaseMultiplier(signal: "Buy" | "Sell" | "Hold", confidence: number, eligible: boolean): number {
  if (!eligible) {
    return 0.8;
  }
  if (signal === "Hold") {
    return roundNumber(Math.max(0.65, 1 - clampNumber(confidence, 0, 1) * 0.35), 4);
  }
  return 1;
}

function contextCompatibilityMultiplier(context: ContextResult, signal: RegimeStrategySignal): number {
  if (!context.eligible) {
    return 0.8;
  }
  const contextSignal = context.signal === "Buy" ? "buy" : context.signal === "Sell" ? "sell" : "hold";
  if (contextSignal === "hold") {
    return context.multiplier;
  }
  if (contextSignal === signal) {
    return 1;
  }
  return roundNumber(Math.max(0.5, 1 - context.confidence * 0.5), 4);
}
