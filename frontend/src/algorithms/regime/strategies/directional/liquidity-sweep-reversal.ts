import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function liquiditySweepReversal(market: RegimeMarketContext): RegimeRawStrategySignal {
  const volumeExpansion = market.latest.volume > market.averageVolume * 1.15;
  const failedHigh = market.latest.high > market.priorHigh && market.latest.close < market.priorHigh;
  const failedLow = market.latest.low < market.priorLow && market.latest.close > market.priorLow;
  if (volumeExpansion && failedHigh) {
    return vote("Sell", 0.72, "High-side liquidity sweep failed with expanded volume");
  }
  if (volumeExpansion && failedLow) {
    return vote("Buy", 0.72, "Low-side liquidity sweep failed with expanded volume");
  }
  return vote("Hold", 0.14, "No volume-backed liquidity sweep reversal");
}

