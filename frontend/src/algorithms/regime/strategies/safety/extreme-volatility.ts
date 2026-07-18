import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { safetyGate } from "../base.ts";

export function extremeVolatilityGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  return market.atr.regime === "extreme"
    ? safetyGate(false, "Extreme volatility blocks new Regime entries")
    : safetyGate(true, `ATR regime ${market.atr.regime.replaceAll("_", " ")} is within Regime limits`);
}

