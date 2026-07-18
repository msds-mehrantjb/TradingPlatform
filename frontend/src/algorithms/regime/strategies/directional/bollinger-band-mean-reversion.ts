import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function bollingerBandMeanReversion(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (!market.bands) {
    return vote("Hold", 0, "Waiting for Bollinger history");
  }
  const width = Math.max(market.bands.upper - market.bands.lower, 0.01);
  if (market.latest.close < market.bands.lower) {
    return vote("Buy", Math.min(0.9, 0.52 + ((market.bands.lower - market.latest.close) / width) * 2), "Price is stretched below lower Bollinger band");
  }
  if (market.latest.close > market.bands.upper) {
    return vote("Sell", Math.min(0.9, 0.52 + ((market.latest.close - market.bands.upper) / width) * 2), "Price is stretched above upper Bollinger band");
  }
  return vote("Hold", 0.12, "Price is inside Bollinger bands");
}

