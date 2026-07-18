import { priceLabel } from "../../indicators.ts";
import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function openingRangeBreakout(market: RegimeMarketContext): RegimeRawStrategySignal {
  const volumeExpansion = market.latest.volume > market.averageVolume * 1.15;
  if (market.latest.close > market.openingRange.high && volumeExpansion) {
    return vote("Buy", 0.72, `Close broke opening high ${priceLabel(market.openingRange.high)} with volume`);
  }
  if (market.latest.close < market.openingRange.low && volumeExpansion) {
    return vote("Sell", 0.72, `Close broke opening low ${priceLabel(market.openingRange.low)} with volume`);
  }
  return vote("Hold", 0.18, "Opening range has not broken with volume");
}

