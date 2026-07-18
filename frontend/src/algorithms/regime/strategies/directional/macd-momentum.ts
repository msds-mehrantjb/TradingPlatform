import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function macdMomentum(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (!market.macd) {
    return vote("Hold", 0, "Waiting for MACD history");
  }
  const confidence = Math.min(0.86, 0.45 + Math.abs(market.macd.histogram) / Math.max(market.latest.close * 0.001, 0.01));
  if (market.macd.macd > market.macd.signal && market.macd.histogram > 0) {
    return vote("Buy", confidence, `MACD histogram ${market.macd.histogram.toFixed(3)} is positive`);
  }
  if (market.macd.macd < market.macd.signal && market.macd.histogram < 0) {
    return vote("Sell", confidence, `MACD histogram ${market.macd.histogram.toFixed(3)} is negative`);
  }
  return vote("Hold", 0.12, "MACD is flat or crossing");
}

