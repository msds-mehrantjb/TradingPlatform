import { probabilityLabel } from "../../indicators.ts";
import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";
import { vote } from "../base.ts";

export function volumeConfirmation(market: RegimeMarketContext): RegimeRawStrategySignal {
  const volume = market.volume;
  if (volume.weakVolume || volume.smallCandle) {
    return vote("Hold", 0.25, `Weak participation: volume ${volume.relativeVolume.toFixed(2)}x, range ${probabilityLabel(volume.rangePercent)}`);
  }
  let buyConfidence = 0;
  const buyReasons: string[] = [];
  if (volume.bullishCandle) buyConfidence += 0.25, buyReasons.push("bullish candle");
  if (volume.relativeVolume >= 1) buyConfidence += Math.min(0.25, 0.12 + (volume.relativeVolume - 1) * 0.12), buyReasons.push(`${volume.relativeVolume.toFixed(2)}x volume`);
  if (volume.breaksResistance || volume.holdsKeyLevel) buyConfidence += 0.25, buyReasons.push(volume.breaksResistance ? "breaks key resistance" : "holds key level");
  if (volume.spreadAcceptable) buyConfidence += 0.15, buyReasons.push("range/spread acceptable");
  if (volume.volumeSpike) buyConfidence += 0.1, buyReasons.push("volume spike");

  let sellConfidence = 0;
  const sellReasons: string[] = [];
  if (volume.bearishCandle) sellConfidence += 0.25, sellReasons.push("bearish candle");
  if (volume.relativeVolume >= 1) sellConfidence += Math.min(0.25, 0.12 + (volume.relativeVolume - 1) * 0.12), sellReasons.push(`${volume.relativeVolume.toFixed(2)}x volume`);
  if (volume.breaksSupport || volume.rejectsResistance) sellConfidence += 0.25, sellReasons.push(volume.breaksSupport ? "breaks support" : "rejects resistance");
  if (volume.spreadAcceptable) sellConfidence += 0.15, sellReasons.push("range/spread acceptable");
  if (volume.volumeSpike) sellConfidence += 0.1, sellReasons.push("volume spike");

  if (buyConfidence >= 0.45 && buyConfidence > sellConfidence) {
    return vote("Hold", Math.min(1, buyConfidence), `Volume confirms bullish participation: ${buyReasons.join(", ")}`);
  }
  if (sellConfidence >= 0.45 && sellConfidence > buyConfidence) {
    return vote("Hold", Math.min(1, sellConfidence), `Volume confirms bearish participation: ${sellReasons.join(", ")}`);
  }
  return vote("Hold", Math.max(0.2, Math.min(0.4, Math.max(buyConfidence, sellConfidence))), `Volume participation is mixed at ${volume.relativeVolume.toFixed(2)}x`);
}

