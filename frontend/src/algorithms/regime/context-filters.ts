import type { RegimeClassifierFeatures, RegimeMarketContext } from "./types.ts";

export function regimeNoTradeReasons(market: RegimeMarketContext, features: RegimeClassifierFeatures): string[] {
  const reasons: string[] = [];
  if (market.candles.length < 50) {
    reasons.push("Need at least 50 regular-session candles for EMA 50 and regime quality");
  }
  if (features.spreadTooWide) {
    reasons.push("Spread is too wide");
  }
  if (features.volumeTooLow) {
    reasons.push("Volume is too light versus the recent average");
  }
  if (Math.abs(features.bullScore - features.bearScore) <= 1 && Math.max(features.bullScore, features.bearScore) >= 2) {
    reasons.push(`Bull and bear scores are close (${features.bullScore}/${features.bearScore})`);
  }
  if (features.priceChoppingAroundVwap) {
    reasons.push("Price is chopping around VWAP");
  }
  if (market.timeOfDay.minutes >= 15 * 60 + 45) {
    reasons.push("New entries are late in the session");
  }
  if (market.contextFeeds.quoteFreshness.status === "stale") {
    reasons.push("Spread quote freshness is stale");
  }
  if (market.contextFeeds.scheduledEconomicEvent.state === "blackout") {
    reasons.push("Scheduled event blackout");
  } else if (market.contextFeeds.scheduledEconomicEvent.state === "elevated") {
    reasons.push("Scheduled event risk elevated");
  }
  if (market.contextFeeds.haltLuldCircuitBreaker.newEntriesBlocked) {
    reasons.push(market.contextFeeds.haltLuldCircuitBreaker.reason ?? "Halt/LULD or circuit-breaker state blocks new entries");
  }
  return reasons;
}
