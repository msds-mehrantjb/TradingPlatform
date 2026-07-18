import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { safetyGate } from "../base.ts";

export function circuitBreakerGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.contextFeeds.haltLuldCircuitBreaker.circuitBreakerState === "active") {
    return safetyGate(false, market.contextFeeds.haltLuldCircuitBreaker.reason ?? "Circuit-breaker state blocks new Regime entries");
  }
  if (market.contextFeeds.haltLuldCircuitBreaker.circuitBreakerState === "watch") {
    return safetyGate(false, "Circuit-breaker watch blocks new Regime entries");
  }
  if (market.contextFeeds.haltLuldCircuitBreaker.circuitBreakerState === "none") {
    return safetyGate(true, "No circuit-breaker state in supplied Regime feed");
  }
  return safetyGate(true, "No circuit-breaker state is attached; gate passes until circuit-breaker data is supplied");
}

