import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { safetyGate } from "../base.ts";

export function unsupportedSessionGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  return market.timeOfDay.newTradesAllowed
    ? safetyGate(true, "Session supports new Regime entries")
    : safetyGate(false, "Unsupported session blocks new Regime entries");
}

