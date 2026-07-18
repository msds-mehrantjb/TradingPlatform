export function regimeTargetDistance(stopDistance: number, effectiveTargetR: number): number {
  return Math.max(0, stopDistance) * Math.max(0, effectiveTargetR);
}
