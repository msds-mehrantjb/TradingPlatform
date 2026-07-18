import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { safetyGate } from "../base.ts";

export function excessiveSpreadGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  return market.spreadLiquidity.spreadTooWide
    ? safetyGate(false, "Excessive spread blocks new Regime entries")
    : safetyGate(true, "Spread is within Regime limits");
}

