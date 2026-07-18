import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function adxTrendStrength(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.adx === null || market.sma20 === null || market.sma50 === null) {
    return vote("Hold", 0, "Waiting for ADX trend history");
  }
  if (market.adx.adx < 20) {
    return vote("Hold", Math.min(0.45, market.adx.adx / 50), `ADX ${market.adx.adx.toFixed(1)} is too weak for trend`);
  }
  const confidence = Math.min(0.9, 0.45 + (market.adx.adx - 20) / 45);
  if (market.sma20 > market.sma50 && market.latest.close > market.vwap) {
    return vote("Hold", confidence, `ADX ${market.adx.adx.toFixed(1)} confirms bullish trend strength`);
  }
  if (market.sma20 < market.sma50 && market.latest.close < market.vwap) {
    return vote("Hold", confidence, `ADX ${market.adx.adx.toFixed(1)} confirms bearish trend strength`);
  }
  return vote("Hold", 0.2, `ADX ${market.adx.adx.toFixed(1)} lacks directional alignment`);
}

