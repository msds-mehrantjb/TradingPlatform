import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function vwapTrendContinuation(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.sma20 === null || market.sma50 === null) {
    return vote("Hold", 0, "Waiting for VWAP trend history");
  }
  const trendUp = market.sma20 > market.sma50 && market.latest.close > market.vwap;
  const trendDown = market.sma20 < market.sma50 && market.latest.close < market.vwap;
  if (trendUp && market.latest.close > market.openingRange.high) {
    return vote("Buy", 0.68, "VWAP, moving averages, and opening range agree upward");
  }
  if (trendDown && market.latest.close < market.openingRange.low) {
    return vote("Sell", 0.68, "VWAP, moving averages, and opening range agree downward");
  }
  return vote("Hold", 0.18, "VWAP trend continuation is not confirmed");
}

