import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { safetyGate } from "../base.ts";

export function insufficientLiquidityGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  return market.spreadLiquidity.volumeTooLow || market.volume.relativeVolume < 0.55
    ? safetyGate(false, "Insufficient liquidity blocks new Regime entries")
    : safetyGate(true, "Liquidity is within Regime limits");
}

