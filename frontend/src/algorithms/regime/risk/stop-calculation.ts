import type { RegimeSizingDefaults } from "../types.ts";

export function regimeStopDistance(
  entryPrice: number,
  primaryAtr: number | null,
  effectiveAtrStopMultiplier: number,
  defaults: RegimeSizingDefaults,
): number {
  const fixedStopFloor = Math.max(0, defaults.fixedStopDistanceDollars);
  const atrStopDistance = primaryAtr !== null && primaryAtr > 0 ? primaryAtr * effectiveAtrStopMultiplier : Number.NaN;
  const priceStopDistance = entryPrice > 0 ? entryPrice * (defaults.minimumStopDistancePercent / 100) : Number.NaN;
  return Math.max(fixedStopFloor, finiteOrZero(atrStopDistance), finiteOrZero(priceStopDistance));
}

function finiteOrZero(value: number): number {
  return Number.isFinite(value) ? value : 0;
}
