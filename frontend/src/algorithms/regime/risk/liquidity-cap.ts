export function regimeLiquidityCap(latestVolume: number, maxParticipationPercent: number): number {
  return maxParticipationPercent > 0 ? Math.max(0, latestVolume) * (maxParticipationPercent / 100) : Number.POSITIVE_INFINITY;
}
