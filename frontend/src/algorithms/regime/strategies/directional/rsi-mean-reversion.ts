import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function rsiMeanReversion(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.rsi === null) {
    return vote("Hold", 0, "Waiting for RSI history");
  }
  if (market.rsi <= 30) {
    return vote("Buy", Math.min(0.9, 0.5 + (30 - market.rsi) / 35), `RSI ${market.rsi.toFixed(1)} is oversold`);
  }
  if (market.rsi >= 70) {
    return vote("Sell", Math.min(0.9, 0.5 + (market.rsi - 70) / 35), `RSI ${market.rsi.toFixed(1)} is overbought`);
  }
  return vote("Hold", 0.15, `RSI ${market.rsi.toFixed(1)} is neutral`);
}

