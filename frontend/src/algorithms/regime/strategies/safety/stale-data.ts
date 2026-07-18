import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { safetyGate } from "../base.ts";

export function staleDataGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.contextFeeds.quoteFreshness.status === "stale") {
    return safetyGate(false, "Stale quote freshness blocks new Regime entries");
  }
  if (market.contextFeeds.quoteFreshness.status === "fresh") {
    return safetyGate(true, "Quote freshness is within Regime limits");
  }
  return safetyGate(true, "Stale-data check has no quote freshness feed; no stale condition detected in supplied snapshot");
}

