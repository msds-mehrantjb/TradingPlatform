import {
  REGIME_NO_DIRECTIONAL_REGIMES,
  type RegimePermittedDirection,
} from "./compatibility-matrix.ts";
import type { MarketRegimeId, RegimeStrategyDefinition } from "../types.ts";

export type RegimeStrategyEligibility = {
  strategyId: string;
  eligible: boolean;
  incompatible: boolean;
  disabled: boolean;
  unhealthy: boolean;
  shouldAbstain: boolean;
  permittedDirection: RegimePermittedDirection;
  reason: string;
};

export function evaluateRoutingEligibility(
  strategy: RegimeStrategyDefinition,
  confirmedRegime: MarketRegimeId,
  selectedStrategyIds: ReadonlySet<string>,
  permittedDirection: RegimePermittedDirection,
): RegimeStrategyEligibility {
  const disabled = !strategy.enabledByDefault;
  const directional = strategy.role === "directional";
  const noDirectional = REGIME_NO_DIRECTIONAL_REGIMES.has(confirmedRegime);
  const compatible = directional && selectedStrategyIds.has(strategy.id);
  const incompatible = directional && !compatible;
  const shouldAbstain = disabled || !directional || incompatible || noDirectional;

  return {
    strategyId: strategy.id,
    eligible: directional && compatible && !disabled,
    incompatible,
    disabled,
    unhealthy: false,
    shouldAbstain,
    permittedDirection: directional ? permittedDirection : "none",
    reason: eligibilityReason(strategy, confirmedRegime, compatible, noDirectional, disabled),
  };
}

export function skippedStrategyReason(
  strategy: RegimeStrategyDefinition,
  confirmedRegime: MarketRegimeId,
): string {
  return REGIME_NO_DIRECTIONAL_REGIMES.has(confirmedRegime)
    ? `Skipped: ${confirmedRegime} allows no new directional strategies`
    : `Skipped: ${strategy.name} is not compatible with ${confirmedRegime}`;
}

export function regimeStrategySelectorReason(strategyId: string, confirmedRegime: MarketRegimeId): string {
  return REGIME_NO_DIRECTIONAL_REGIMES.has(confirmedRegime)
    ? `Selected as non-directional Regime ${confirmedRegime} context`
    : `Selected for confirmed regime ${confirmedRegime}`;
}

export function regimeStrategyAvoidReason(strategyId: string, confirmedRegime: MarketRegimeId): string {
  return REGIME_NO_DIRECTIONAL_REGIMES.has(confirmedRegime)
    ? `Avoided: ${confirmedRegime} permits no new directional strategies`
    : `Avoided: ${strategyId} is not compatible with ${confirmedRegime}`;
}

function eligibilityReason(
  strategy: RegimeStrategyDefinition,
  confirmedRegime: MarketRegimeId,
  compatible: boolean,
  noDirectional: boolean,
  disabled: boolean,
): string {
  if (disabled) {
    return `Disabled: ${strategy.name} is disabled in the Regime catalog`;
  }
  if (strategy.role !== "directional") {
    return `Abstained: ${strategy.name} is ${strategy.role} routing context`;
  }
  if (noDirectional) {
    return `Abstained: ${confirmedRegime} allows no new directional strategies`;
  }
  return compatible
    ? `Eligible: ${strategy.name} is compatible with ${confirmedRegime}`
    : `Incompatible: ${strategy.name} is not compatible with ${confirmedRegime}`;
}
