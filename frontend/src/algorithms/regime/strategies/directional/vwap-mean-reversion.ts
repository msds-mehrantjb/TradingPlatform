import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function vwapMeanReversion(market: RegimeMarketContext): RegimeRawStrategySignal {
  const distance = (market.latest.close - market.vwap) / Math.max(market.vwap, 0.01);
  const choppy = market.adx !== null ? market.adx.regime === "range" || market.adx.regime === "mixed" : Math.abs(market.vwapSlope) < 0.0002;
  if (choppy && distance < -0.003) {
    return vote("Buy", Math.min(0.78, 0.52 + Math.abs(distance) * 35), "Price is stretched below VWAP in a weak-trend tape");
  }
  if (choppy && distance > 0.003) {
    return vote("Sell", Math.min(0.78, 0.52 + Math.abs(distance) * 35), "Price is stretched above VWAP in a weak-trend tape");
  }
  return vote("Hold", 0.16, "VWAP mean-reversion setup is not active");
}

