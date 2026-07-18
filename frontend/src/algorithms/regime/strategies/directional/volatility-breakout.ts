import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function volatilityBreakout(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.atr.atr1m === null || market.candles.length < 21) {
    return vote("Hold", 0, "Waiting for ATR breakout history");
  }
  const volumeExpansion = market.latest.volume > market.averageVolume * 1.2;
  const breaksHigh = market.latest.close > market.priorHigh;
  const breaksLow = market.latest.close < market.priorLow;
  const atrExpands = market.atr.regime === "high" || market.atr.regime === "extreme";
  if (breaksHigh && volumeExpansion && atrExpands) {
    return vote("Buy", 0.7, "Price, volume, and ATR expanded above recent range");
  }
  if (breaksLow && volumeExpansion && atrExpands) {
    return vote("Sell", 0.7, "Price, volume, and ATR expanded below recent range");
  }
  return vote("Hold", 0.16, "Volatility breakout is not confirmed");
}

