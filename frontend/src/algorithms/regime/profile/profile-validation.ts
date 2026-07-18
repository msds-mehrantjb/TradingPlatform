import type { EffectiveRegimeSettings, RegimeBaseSettings, RegimeProfileModifiers } from "../types.ts";

export function validateRegimeProfileModifiers(modifier: RegimeProfileModifiers): string[] {
  const errors: string[] = [];
  for (const field of ["riskMultiplier", "allocationMultiplier", "positionMultiplier", "liquidityParticipationMultiplier", "signalSizeMultiplier"] as const) {
    const value = modifier[field];
    if (!Number.isFinite(value) || value < 0 || value > 1) {
      errors.push(`${field} must be finite and between 0 and 1`);
    }
  }
  if (!Number.isFinite(modifier.targetRMultiplier) || modifier.targetRMultiplier < 0 || modifier.targetRMultiplier > 1.5) {
    errors.push("targetRMultiplier must be finite and between 0 and 1.5");
  }
  if (modifier.maximumTradesOverride !== null && (!Number.isFinite(modifier.maximumTradesOverride) || modifier.maximumTradesOverride < 0)) {
    errors.push("maximumTradesOverride must be null or non-negative");
  }
  return errors;
}

export function validateEffectiveRegimeProfile(base: RegimeBaseSettings, effective: EffectiveRegimeSettings): string[] {
  const errors: string[] = [];
  if (effective.effectiveRiskPercent > base.baseRiskPercent) errors.push("effectiveRiskPercent exceeds baseline");
  if (effective.effectiveOrderAllocationPercent > base.orderAllocationPercent) errors.push("effectiveOrderAllocationPercent exceeds baseline");
  if (effective.effectiveMaxPositionPercent > base.maxPositionPercent) errors.push("effectiveMaxPositionPercent exceeds baseline");
  if (effective.effectiveMaximumParticipationPercent > base.maximumVolumeParticipationPercent) errors.push("effectiveMaximumParticipationPercent exceeds baseline");
  if (effective.effectiveMaximumTrades > base.maxTradesPerDay) errors.push("effectiveMaximumTrades exceeds baseline");
  if (effective.effectiveMinimumWinningScore < base.minimumWinningScore) errors.push("effectiveMinimumWinningScore loosens baseline");
  if (effective.effectiveMinimumDirectionalEdge < base.minimumDirectionalEdge) errors.push("effectiveMinimumDirectionalEdge loosens baseline");
  if (effective.effectiveMinimumRegimeConfidence < base.minimumRegimeConfidence) errors.push("effectiveMinimumRegimeConfidence loosens baseline");
  if (effective.pyramidingAllowed && !base.pyramidingEnabled) errors.push("pyramidingAllowed exceeds baseline permission");
  return errors;
}
