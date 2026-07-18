import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function marketStructure(market: RegimeMarketContext): RegimeRawStrategySignal {
  const structure = market.structure ?? null;
  if (!structure) {
    return vote("Hold", 0, "Waiting for swing structure");
  }
  let buyConfidence = 0;
  const buyReasons: string[] = [];
  if (structure.higherHigh) buyConfidence += 0.25, buyReasons.push("higher high");
  if (structure.higherLow) buyConfidence += 0.25, buyReasons.push("higher low");
  if (market.latest.close > market.vwap) buyConfidence += 0.2, buyReasons.push("price above VWAP");
  if (structure.successfulSupportRetest || structure.breakRetestSucceeded) buyConfidence += 0.15, buyReasons.push(structure.breakRetestSucceeded ? "break/retest succeeded" : "pullback held support");
  if (market.latest.close > market.latest.open) buyConfidence += 0.15, buyReasons.push("bullish candle confirmation");

  let sellConfidence = 0;
  const sellReasons: string[] = [];
  if (structure.lowerLow) sellConfidence += 0.25, sellReasons.push("lower low");
  if (structure.lowerHigh) sellConfidence += 0.25, sellReasons.push("lower high");
  if (market.latest.close < market.vwap) sellConfidence += 0.2, sellReasons.push("price below VWAP");
  if (structure.failedResistanceRetest || structure.breakRetestFailed) sellConfidence += 0.15, sellReasons.push(structure.breakRetestFailed ? "break/retest failed" : "rally failed at resistance");
  if (market.latest.close < market.latest.open) sellConfidence += 0.15, sellReasons.push("bearish candle confirmation");

  if (buyConfidence >= 0.45 && buyConfidence > sellConfidence) {
    return vote("Buy", Math.min(1, buyConfidence), `${buyReasons.join(", ")}; ${structure.summary}`);
  }
  if (sellConfidence >= 0.45 && sellConfidence > buyConfidence) {
    return vote("Sell", Math.min(1, sellConfidence), `${sellReasons.join(", ")}; ${structure.summary}`);
  }
  return vote("Hold", Math.max(0.2, Math.min(0.4, Math.max(buyConfidence, sellConfidence))), `Structure is mixed; ${structure.summary}`);
}

