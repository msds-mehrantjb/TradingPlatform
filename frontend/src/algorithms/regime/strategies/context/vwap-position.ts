import { probabilityLabel } from "../../indicators.ts";
import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function vwapPosition(market: RegimeMarketContext): RegimeRawStrategySignal {
  const distance = (market.latest.close - market.vwap) / Math.max(market.vwap, 0.01);
  const recent = market.candles.slice(-5);
  const lastThree = market.candles.slice(-3);
  const closesAbove = lastThree.filter((candle) => candle.close > market.vwap).length;
  const closesBelow = lastThree.filter((candle) => candle.close < market.vwap).length;
  const volumeSupportsBuy = market.latest.volume > market.averageVolume * 1.1 && market.latest.close >= market.latest.open;
  const volumeSupportsSell = market.latest.volume > market.averageVolume * 1.1 && market.latest.close <= market.latest.open;
  const pullbackHeldVwap = recent.some((candle) => candle.low <= market.vwap * 1.001 && candle.close > market.vwap);
  const rejectedVwap = recent.some((candle) => candle.high >= market.vwap * 0.999 && candle.close < market.vwap);

  let buyConfidence = 0;
  const buyReasons: string[] = [];
  if (market.latest.close > market.vwap) buyConfidence += 0.25, buyReasons.push("price above VWAP");
  if (market.vwapSlope > 0.00005) buyConfidence += 0.2, buyReasons.push("VWAP slope positive");
  if (closesAbove === 3) buyConfidence += 0.2, buyReasons.push("last 3 closes above VWAP");
  if (pullbackHeldVwap) buyConfidence += 0.2, buyReasons.push("pullback held VWAP");
  if (volumeSupportsBuy) buyConfidence += 0.15, buyReasons.push("volume supports move");

  let sellConfidence = 0;
  const sellReasons: string[] = [];
  if (market.latest.close < market.vwap) sellConfidence += 0.25, sellReasons.push("price below VWAP");
  if (market.vwapSlope < -0.00005) sellConfidence += 0.2, sellReasons.push("VWAP slope negative");
  if (closesBelow === 3) sellConfidence += 0.2, sellReasons.push("last 3 closes below VWAP");
  if (rejectedVwap) sellConfidence += 0.2, sellReasons.push("retest rejected VWAP");
  if (volumeSupportsSell) sellConfidence += 0.15, sellReasons.push("volume supports move");

  const distanceText = `distance ${probabilityLabel(Math.abs(distance))}`;
  if (buyConfidence >= 0.45 && buyConfidence > sellConfidence) {
    return vote("Hold", Math.min(1, buyConfidence), `VWAP bullish context: ${buyReasons.join(", ")}; ${distanceText}`);
  }
  if (sellConfidence >= 0.45 && sellConfidence > buyConfidence) {
    return vote("Hold", Math.min(1, sellConfidence), `VWAP bearish context: ${sellReasons.join(", ")}; ${distanceText}`);
  }
  return vote("Hold", Math.max(0.2, Math.min(0.4, Math.max(buyConfidence, sellConfidence))), `VWAP acceptance is mixed; ${distanceText}`);
}

