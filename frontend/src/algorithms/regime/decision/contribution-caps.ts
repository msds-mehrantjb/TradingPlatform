import { REGIME_MAX_FAMILY_CONTRIBUTION, REGIME_MAX_INDIVIDUAL_STRATEGY_CONTRIBUTION } from "../config.ts";
import { clampNumber, roundNumber } from "../indicators.ts";
import type { RegimeFamilyScore } from "../types.ts";

export function cappedRegimeStrategyContribution(confidence: number, effectiveWeight: number): number {
  return Math.min(
    REGIME_MAX_INDIVIDUAL_STRATEGY_CONTRIBUTION,
    Math.max(0, clampNumber(confidence, 0, 1) * Math.max(0, effectiveWeight)),
  );
}

export function applyRegimeFamilyContributionCap(family: RegimeFamilyScore): RegimeFamilyScore {
  const familyTotal = family.buyScore + family.sellScore;
  const familyScale = familyTotal > REGIME_MAX_FAMILY_CONTRIBUTION ? REGIME_MAX_FAMILY_CONTRIBUTION / familyTotal : 1;
  return {
    ...family,
    buyScore: roundNumber(family.buyScore * familyScale, 4),
    sellScore: roundNumber(family.sellScore * familyScale, 4),
  };
}
