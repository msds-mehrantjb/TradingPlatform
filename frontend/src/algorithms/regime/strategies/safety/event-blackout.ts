import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { safetyGate } from "../base.ts";

export function eventBlackoutGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.contextFeeds.scheduledEconomicEvent.state === "blackout") {
    return safetyGate(false, "Scheduled event blackout blocks new Regime entries");
  }
  if (market.contextFeeds.scheduledEconomicEvent.state === "elevated") {
    return safetyGate(false, "Elevated scheduled event risk blocks new Regime entries");
  }
  if (market.contextFeeds.scheduledEconomicEvent.state === "none") {
    return safetyGate(true, "No scheduled economic event risk in supplied Regime feed");
  }
  return safetyGate(true, "No Regime event-blackout feed is attached; gate passes until an event blackout is supplied");
}

