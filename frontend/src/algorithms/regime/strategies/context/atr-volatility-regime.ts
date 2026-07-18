import { probabilityLabel } from "../../indicators.ts";
import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function atrVolatilityRegime(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.sma20 === null || market.sma50 === null) {
    return vote("Hold", 0, "Waiting for ATR regime history");
  }
  const atrPercent = market.atr.atrPercent;
  if (market.atr.regime === "too_low" || market.atr.regime === "extreme") {
    return vote("Hold", 0.25, `ATR regime ${market.atr.regime.replaceAll("_", " ")} is not tradable`);
  }
  const confidence = Math.min(0.78, 0.45 + atrPercent * 35);
  if (market.sma20 > market.sma50 && market.latest.close > market.vwap) {
    return vote("Hold", confidence, `ATR regime ${probabilityLabel(atrPercent)} supports bullish trend sizing`);
  }
  if (market.sma20 < market.sma50 && market.latest.close < market.vwap) {
    return vote("Hold", confidence, `ATR regime ${probabilityLabel(atrPercent)} supports bearish trend sizing`);
  }
  return vote("Hold", 0.18, `ATR regime ${probabilityLabel(atrPercent)} has no directional edge`);
}

