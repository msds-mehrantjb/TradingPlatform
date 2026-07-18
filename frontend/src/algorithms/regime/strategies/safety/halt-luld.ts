import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { safetyGate } from "../base.ts";

export function haltLuldGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.contextFeeds.haltLuldCircuitBreaker.haltState === "halted" || market.contextFeeds.haltLuldCircuitBreaker.haltState === "luld_pause") {
    return safetyGate(false, market.contextFeeds.haltLuldCircuitBreaker.reason ?? "Halt/LULD state blocks new Regime entries");
  }
  return market.latest.volume <= 0
    ? safetyGate(false, "Zero-volume latest candle may indicate halt/LULD; new Regime entries blocked")
    : safetyGate(true, "No halt/LULD condition detected in supplied snapshot");
}

