import { priceLabel } from "../../indicators.ts";
import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function movingAverageTrend(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.sma20 === null || market.sma50 === null) {
    return vote("Hold", 0, "Waiting for 50 candles");
  }
  const spread = Math.abs(market.sma20 - market.sma50) / market.latest.close;
  const confidence = Math.min(0.95, 0.45 + spread * 80);
  if (market.sma20 > market.sma50 && market.latest.close > market.sma20) {
    return vote("Buy", confidence, `20 SMA ${priceLabel(market.sma20)} above 50 SMA ${priceLabel(market.sma50)}`);
  }
  if (market.sma20 < market.sma50 && market.latest.close < market.sma20) {
    return vote("Sell", confidence, `20 SMA ${priceLabel(market.sma20)} below 50 SMA ${priceLabel(market.sma50)}`);
  }
  return vote("Hold", 0.2, "Moving averages are mixed");
}

