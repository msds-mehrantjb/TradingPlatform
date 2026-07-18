import { priceLabel } from "../../indicators.ts";
import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function intradayBreakout(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.candles.length < 21) {
    return vote("Hold", 0, "Waiting for 21 candles");
  }
  if (market.latest.close > market.priorHigh) {
    return vote("Buy", 0.62, `Close broke prior high ${priceLabel(market.priorHigh)}`);
  }
  if (market.latest.close < market.priorLow) {
    return vote("Sell", 0.62, `Close broke prior low ${priceLabel(market.priorLow)}`);
  }
  return vote("Hold", 0.1, "Price remains inside recent range");
}

