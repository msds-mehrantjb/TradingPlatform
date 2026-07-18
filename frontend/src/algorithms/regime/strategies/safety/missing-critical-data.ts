import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { safetyGate } from "../base.ts";

export function missingCriticalDataGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  const missing: string[] = [];
  if (!market.candles.length) missing.push("regular-session candles");
  if (!market.latest) missing.push("latest quote");
  if (!Number.isFinite(market.vwap)) missing.push("VWAP");
  return missing.length ? safetyGate(false, `Missing critical data: ${missing.join(", ")}`) : safetyGate(true, "Critical Regime inputs are present");
}

