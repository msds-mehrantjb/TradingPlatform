import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function trendPullback(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.sma20 === null || market.sma50 === null) {
    return vote("Hold", 0, "Waiting for trend moving averages");
  }
  const nearSma20 = Math.abs(market.latest.close - market.sma20) / market.latest.close < 0.0035;
  const trendUp = market.sma20 > market.sma50 && market.latest.close > market.vwap;
  const trendDown = market.sma20 < market.sma50 && market.latest.close < market.vwap;
  if (trendUp && nearSma20 && market.latest.close > market.openingRange.high) {
    return vote("Buy", 0.68, "Uptrend pullback is holding near 20 SMA");
  }
  if (trendDown && nearSma20 && market.latest.close < market.openingRange.low) {
    return vote("Sell", 0.68, "Downtrend pullback is rejecting near 20 SMA");
  }
  return vote("Hold", 0.2, "No clean trend pullback");
}

