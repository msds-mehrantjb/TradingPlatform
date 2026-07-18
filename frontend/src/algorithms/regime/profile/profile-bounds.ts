import { clampNumber, roundNumber } from "../indicators.ts";
import type { EffectiveRegimeSettings, RegimeBaseSettings } from "../types.ts";

export function clampRegimeProfileUnit(value: number): number {
  return Number.isFinite(value) ? clampNumber(value, 0, 1) : 0;
}

export function minimumRegimeProfileOverride(left: number | null, right: number | null): number | null {
  if (left === null) return right;
  if (right === null) return left;
  return Math.min(left, right);
}

export function boundedRegimeEffectiveSettings(
  base: RegimeBaseSettings,
  effective: EffectiveRegimeSettings,
): EffectiveRegimeSettings {
  return {
    ...effective,
    effectiveRiskPercent: roundNumber(Math.min(base.baseRiskPercent, Math.max(0, effective.effectiveRiskPercent)), 4),
    effectiveOrderAllocationPercent: roundNumber(Math.min(base.orderAllocationPercent, Math.max(0, effective.effectiveOrderAllocationPercent)), 4),
    effectiveMaxPositionPercent: roundNumber(Math.min(base.maxPositionPercent, Math.max(0, effective.effectiveMaxPositionPercent)), 4),
    effectiveAtrStopMultiplier: roundNumber(Math.max(0, effective.effectiveAtrStopMultiplier), 4),
    effectiveTakeProfitR: roundNumber(Math.max(0, effective.effectiveTakeProfitR), 4),
    effectiveMaximumParticipationPercent: roundNumber(Math.min(base.maximumVolumeParticipationPercent, Math.max(0, effective.effectiveMaximumParticipationPercent)), 4),
    effectiveMinimumWinningScore: clampNumber(effective.effectiveMinimumWinningScore, base.minimumWinningScore, 1),
    effectiveMinimumDirectionalEdge: clampNumber(effective.effectiveMinimumDirectionalEdge, base.minimumDirectionalEdge, 1),
    effectiveMinimumRegimeConfidence: clampNumber(effective.effectiveMinimumRegimeConfidence, base.minimumRegimeConfidence, 1),
    effectiveMaximumTrades: Math.max(0, Math.min(base.maxTradesPerDay, Math.floor(effective.effectiveMaximumTrades))),
  };
}
