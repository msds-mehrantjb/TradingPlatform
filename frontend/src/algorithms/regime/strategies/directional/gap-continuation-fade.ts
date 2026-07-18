import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function gapContinuationFade(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.priorClose === null) {
    return vote("Hold", 0, "Waiting for prior close");
  }
  const gap = (market.dayOpen - market.priorClose) / market.priorClose;
  if (Math.abs(gap) < 0.0015) {
    return vote("Hold", 0.1, "Opening gap is too small");
  }
  if (gap > 0 && market.latest.close > market.openingRange.high) {
    return vote("Buy", Math.min(0.74, 0.5 + Math.abs(gap) * 70), "Gap up is continuing above opening range");
  }
  if (gap < 0 && market.latest.close < market.openingRange.low) {
    return vote("Sell", Math.min(0.74, 0.5 + Math.abs(gap) * 70), "Gap down is continuing below opening range");
  }
  if (gap > 0 && market.latest.close < market.dayOpen) {
    return vote("Sell", Math.min(0.68, 0.48 + Math.abs(gap) * 55), "Gap up is fading below day open");
  }
  if (gap < 0 && market.latest.close > market.dayOpen) {
    return vote("Buy", Math.min(0.68, 0.48 + Math.abs(gap) * 55), "Gap down is fading above day open");
  }
  return vote("Hold", 0.14, "Gap context has no continuation or fade trigger");
}

