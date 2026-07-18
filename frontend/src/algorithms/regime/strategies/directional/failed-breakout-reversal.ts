import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function failedBreakoutReversal(market: RegimeMarketContext): RegimeRawStrategySignal {
  const failedHigh = market.latest.high > market.priorHigh && market.latest.close < market.priorHigh;
  const failedLow = market.latest.low < market.priorLow && market.latest.close > market.priorLow;
  if (failedHigh) {
    return vote("Sell", 0.7, "Prior high breakout failed back below range");
  }
  if (failedLow) {
    return vote("Buy", 0.7, "Prior low breakdown failed back above range");
  }
  return vote("Hold", 0.14, "No failed breakout reversal");
}

