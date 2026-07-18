import { regimeSelectionFeatures } from "../classifier.ts";
import type { RegimeClassifierFeatures, RegimeMarketContext } from "../types.ts";
import { REGIME_ALGORITHM_ID } from "../versions.ts";
import type { RegimeMarketSnapshot } from "./market-snapshot.ts";

export type RegimeFeatureSnapshot = {
  readonly algorithmId: typeof REGIME_ALGORITHM_ID;
  readonly symbol: string;
  readonly timestamp: string;
  readonly features: Readonly<RegimeClassifierFeatures>;
  readonly contextFeedEvidence: Readonly<Record<string, string | number | boolean | null>>;
};

export function buildRegimeFeatureSnapshot(snapshot: RegimeMarketSnapshot): RegimeFeatureSnapshot | null {
  if (!snapshot.marketContext) {
    return null;
  }
  return buildRegimeFeatureSnapshotFromContext(snapshot.symbol, snapshot.marketContext);
}

export function buildRegimeFeatureSnapshotFromContext(symbol: string, market: RegimeMarketContext): RegimeFeatureSnapshot {
  const features = regimeSelectionFeatures(market);
  return Object.freeze({
    algorithmId: REGIME_ALGORITHM_ID,
    symbol,
    timestamp: market.latest.timestamp,
    features: Object.freeze(features),
    contextFeedEvidence: Object.freeze({
      quoteFreshness: market.contextFeeds.quoteFreshness.status,
      qqqRelativeStrength: market.contextFeeds.qqqRelativeStrength.relativeToPrimaryPercent,
      iwmRelativeStrength: market.contextFeeds.iwmRelativeStrength.relativeToPrimaryPercent,
      marketBreadth: market.contextFeeds.marketBreadth.advanceDeclineRatio,
      vixState: market.contextFeeds.vix.state,
      esFuturesState: market.contextFeeds.esFutures.trend,
      scheduledEventState: market.contextFeeds.scheduledEconomicEvent.state,
      haltLuldBlocked: market.contextFeeds.haltLuldCircuitBreaker.newEntriesBlocked,
    }),
  });
}

