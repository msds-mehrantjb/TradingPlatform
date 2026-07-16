import type { MarketCandle, TradingTimeframe } from "../../trading/shared/market-data-types.ts";
import type { RegimeAdxContext, RegimeAdxRegime, RegimeMarketStructure } from "./types.ts";

export function clampNumber(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.max(min, Math.min(max, value));
}

export function roundNumber(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

export function simpleMovingAverage(values: number[], period: number): number | null {
  if (values.length < period || period <= 0) {
    return null;
  }
  const sample = values.slice(-period);
  return sample.reduce((sum, value) => sum + value, 0) / period;
}

export function exponentialMovingAverageSeries(values: number[], period: number): Array<number | null> {
  if (period <= 0) {
    return values.map(() => null);
  }
  const multiplier = 2 / (period + 1);
  const result: Array<number | null> = [];
  let ema: number | null = null;
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (index + 1 < period) {
      result.push(null);
      continue;
    }
    if (ema === null) {
      ema = values.slice(index + 1 - period, index + 1).reduce((sum, item) => sum + item, 0) / period;
    } else {
      ema = (value - ema) * multiplier + ema;
    }
    result.push(ema);
  }
  return result;
}

export function lastDefined(values: Array<number | null>): number | null {
  for (let index = values.length - 1; index >= 0; index -= 1) {
    if (values[index] !== null) {
      return values[index];
    }
  }
  return null;
}

export function aggregateCandlesToMinutes(candles: MarketCandle[], minutes: number, timeframe: TradingTimeframe): MarketCandle[] {
  const buckets = new Map<string, MarketCandle[]>();
  candles.forEach((candle) => {
    const date = new Date(candle.timestamp);
    if (Number.isNaN(date.getTime())) {
      return;
    }
    const bucketMinute = Math.floor(date.getUTCMinutes() / minutes) * minutes;
    date.setUTCMinutes(bucketMinute, 0, 0);
    const bucketKey = date.toISOString();
    buckets.set(bucketKey, [...(buckets.get(bucketKey) ?? []), candle]);
  });

  return Array.from(buckets.entries())
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([timestamp, bucket]) => {
      const first = bucket[0];
      const last = bucket[bucket.length - 1];
      const volume = bucket.reduce((sum, candle) => sum + candle.volume, 0);
      const tradeCount = bucket.reduce((sum, candle) => sum + (candle.trade_count ?? 0), 0);
      const vwapNumerator = bucket.reduce((sum, candle) => sum + (candle.vwap ?? candle.close) * candle.volume, 0);
      return {
        ...last,
        timeframe,
        timestamp,
        open: first.open,
        high: Math.max(...bucket.map((candle) => candle.high)),
        low: Math.min(...bucket.map((candle) => candle.low)),
        close: last.close,
        volume,
        trade_count: tradeCount || null,
        vwap: volume ? vwapNumerator / volume : null,
      };
    });
}

export function aggregateCandlesToFiveMinute(candles: MarketCandle[]): MarketCandle[] {
  return aggregateCandlesToMinutes(candles, 5, "5Min");
}

export function sessionVwapValue(candles: MarketCandle[]): number {
  let cumulativePriceVolume = 0;
  let cumulativeVolume = 0;
  candles.forEach((candle) => {
    const volume = Math.max(0, candle.volume);
    const typical = (candle.high + candle.low + candle.close) / 3;
    cumulativePriceVolume += typical * volume;
    cumulativeVolume += volume;
  });
  return cumulativeVolume ? cumulativePriceVolume / cumulativeVolume : candles[candles.length - 1]?.close ?? 0;
}

export function openingRangeValues(candles: MarketCandle[], count: number): { high: number; low: number } {
  const opening = candles.slice(0, Math.min(count, candles.length));
  return {
    high: Math.max(...opening.map((candle) => candle.high)),
    low: Math.min(...opening.map((candle) => candle.low)),
  };
}

export function bollingerBands(values: number[], period: number, deviations: number): { middle: number; upper: number; lower: number } | null {
  if (values.length < period) {
    return null;
  }
  const sample = values.slice(-period);
  const middle = sample.reduce((sum, value) => sum + value, 0) / period;
  const variance = sample.reduce((sum, value) => sum + (value - middle) ** 2, 0) / period;
  const width = Math.sqrt(variance) * deviations;
  return { middle, upper: middle + width, lower: middle - width };
}

export function averageTrueRange(candles: MarketCandle[], period: number): number | null {
  if (period <= 0 || candles.length <= period) {
    return null;
  }
  const sample = candles.slice(-(period + 1));
  const ranges: number[] = [];
  for (let index = 1; index < sample.length; index += 1) {
    const current = sample[index];
    const previous = sample[index - 1];
    ranges.push(Math.max(current.high - current.low, Math.abs(current.high - previous.close), Math.abs(current.low - previous.close)));
  }
  return ranges.length ? ranges.reduce((sum, value) => sum + value, 0) / ranges.length : null;
}

export function rollingAtrSeries(candles: MarketCandle[], period: number): number[] {
  if (period <= 0 || candles.length <= period) {
    return [];
  }
  const values: number[] = [];
  for (let end = period + 1; end <= candles.length; end += 1) {
    const atr = averageTrueRange(candles.slice(0, end), period);
    if (atr !== null) {
      values.push(atr);
    }
  }
  return values;
}

export function averageDirectionalIndex(candles: MarketCandle[], period: number): RegimeAdxContext | null {
  if (period <= 1 || candles.length <= period * 2) {
    return null;
  }
  const plusDm: number[] = [];
  const minusDm: number[] = [];
  const trueRanges: number[] = [];
  for (let index = 1; index < candles.length; index += 1) {
    const current = candles[index];
    const previous = candles[index - 1];
    const upMove = current.high - previous.high;
    const downMove = previous.low - current.low;
    plusDm.push(upMove > downMove && upMove > 0 ? upMove : 0);
    minusDm.push(downMove > upMove && downMove > 0 ? downMove : 0);
    trueRanges.push(Math.max(current.high - current.low, Math.abs(current.high - previous.close), Math.abs(current.low - previous.close)));
  }
  const rows: Array<{ plusDi: number; minusDi: number; dx: number }> = [];
  for (let index = period; index <= trueRanges.length; index += 1) {
    const tr = trueRanges.slice(index - period, index).reduce((sum, value) => sum + value, 0);
    if (!tr) {
      continue;
    }
    const plusDi = (plusDm.slice(index - period, index).reduce((sum, value) => sum + value, 0) / tr) * 100;
    const minusDi = (minusDm.slice(index - period, index).reduce((sum, value) => sum + value, 0) / tr) * 100;
    const directionalTotal = plusDi + minusDi;
    if (directionalTotal) {
      rows.push({ plusDi, minusDi, dx: (Math.abs(plusDi - minusDi) / directionalTotal) * 100 });
    }
  }
  const recent = rows.slice(-period);
  if (!recent.length) {
    return null;
  }
  const adx = recent.reduce((sum, row) => sum + row.dx, 0) / recent.length;
  const previous = rows.slice(-(period * 2), -period);
  const previousAdx = previous.length ? previous.reduce((sum, row) => sum + row.dx, 0) / previous.length : adx;
  const latest = rows.at(-1)!;
  const slope = adx - previousAdx;
  return {
    adx: roundNumber(adx, 2),
    plusDi: roundNumber(latest.plusDi, 2),
    minusDi: roundNumber(latest.minusDi, 2),
    slope: roundNumber(slope, 2),
    regime: adxRegime(adx, latest.plusDi, latest.minusDi, slope),
  };
}

export function adxRegime(adx: number, plusDi: number, minusDi: number, slope: number): RegimeAdxRegime {
  if (adx < 15) {
    return "range";
  }
  if (adx <= 25) {
    return "mixed";
  }
  if (plusDi > minusDi) {
    return adx >= 35 && slope >= 0 ? "very_strong_bullish_trend" : "bullish_trend";
  }
  if (minusDi > plusDi) {
    return adx >= 35 && slope >= 0 ? "very_strong_bearish_trend" : "bearish_trend";
  }
  return "mixed";
}

export function relativeStrengthIndex(values: number[], period: number): number | null {
  if (values.length <= period || period <= 0) {
    return null;
  }
  const sample = values.slice(-(period + 1));
  let gains = 0;
  let losses = 0;
  for (let index = 1; index < sample.length; index += 1) {
    const change = sample[index] - sample[index - 1];
    if (change >= 0) {
      gains += change;
    } else {
      losses += Math.abs(change);
    }
  }
  const averageGain = gains / period;
  const averageLoss = losses / period;
  if (averageLoss === 0) {
    return 100;
  }
  const rs = averageGain / averageLoss;
  return 100 - 100 / (1 + rs);
}

export function macdValues(values: number[]): { macd: number; signal: number; histogram: number } | null {
  const fast = lastDefined(exponentialMovingAverageSeries(values, 12));
  const slow = lastDefined(exponentialMovingAverageSeries(values, 26));
  if (fast === null || slow === null) {
    return null;
  }
  const macd = fast - slow;
  const macdSeries = values.map((_, index) => {
    const fastValue = lastDefined(exponentialMovingAverageSeries(values.slice(0, index + 1), 12));
    const slowValue = lastDefined(exponentialMovingAverageSeries(values.slice(0, index + 1), 26));
    return fastValue !== null && slowValue !== null ? fastValue - slowValue : null;
  });
  const signal = lastDefined(exponentialMovingAverageSeries(macdSeries.filter((value): value is number => value !== null), 9));
  if (signal === null) {
    return null;
  }
  return { macd, signal, histogram: macd - signal };
}

export function isRegularSession(timestamp: string): boolean {
  const minutes = easternMinutes(timestamp);
  return minutes >= 9 * 60 + 30 && minutes < 16 * 60;
}

export function isPremarketSession(timestamp: string): boolean {
  const minutes = easternMinutes(timestamp);
  return minutes >= 4 * 60 && minutes < 9 * 60 + 30;
}

export function easternDateString(timestamp: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(timestamp));
}

export function easternMinutes(timestamp: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date(timestamp));
  const hour = Number(parts.find((part) => part.type === "hour")?.value ?? "0");
  const minute = Number(parts.find((part) => part.type === "minute")?.value ?? "0");
  return hour * 60 + minute;
}

export function sessionLabelForMinutes(minutes: number): string {
  if (minutes < 10 * 60 + 30) {
    return "Opening drive";
  }
  if (minutes < 12 * 60) {
    return "Morning continuation";
  }
  if (minutes < 14 * 60) {
    return "Midday";
  }
  if (minutes < 15 * 60 + 30) {
    return "Afternoon";
  }
  return "Closing window";
}

export function marketStructureContext(candles: MarketCandle[], vwap: number): RegimeMarketStructure | null {
  const lookback = candles.slice(-50);
  if (lookback.length < 12) {
    return null;
  }
  const window = lookback.length >= 24 ? 4 : 3;
  const swingHighs: Array<{ index: number; value: number }> = [];
  const swingLows: Array<{ index: number; value: number }> = [];

  for (let index = window; index < lookback.length - window; index += 1) {
    const sample = lookback.slice(index - window, index + window + 1);
    const candle = lookback[index];
    if (candle.high === Math.max(...sample.map((item) => item.high))) {
      swingHighs.push({ index, value: candle.high });
    }
    if (candle.low === Math.min(...sample.map((item) => item.low))) {
      swingLows.push({ index, value: candle.low });
    }
  }

  const latest = lookback.at(-1)!;
  const highs = swingHighs.slice(-2);
  const lows = swingLows.slice(-2);
  const previousHigh = highs.at(-2);
  const latestHigh = highs.at(-1);
  const previousLow = lows.at(-2);
  const latestLow = lows.at(-1);
  const higherHigh = Boolean(previousHigh && latestHigh && latestHigh.value > previousHigh.value);
  const lowerHigh = Boolean(previousHigh && latestHigh && latestHigh.value < previousHigh.value);
  const higherLow = Boolean(previousLow && latestLow && latestLow.value > previousLow.value);
  const lowerLow = Boolean(previousLow && latestLow && latestLow.value < previousLow.value);
  const resistance = latestHigh?.value ?? Math.max(...lookback.slice(-20).map((candle) => candle.high));
  const support = latestLow?.value ?? Math.min(...lookback.slice(-20).map((candle) => candle.low));
  const tolerance = Math.max(latest.close * 0.0008, 0.01);
  const breakOfStructureUp = previousHigh ? latest.close > previousHigh.value : false;
  const breakOfStructureDown = previousLow ? latest.close < previousLow.value : false;
  const changeOfCharacterUp = lowerLow && latest.close > resistance;
  const changeOfCharacterDown = higherHigh && latest.close < support;
  const successfulSupportRetest = latest.low <= support + tolerance && latest.close > support && latest.close > vwap;
  const failedResistanceRetest = latest.high >= resistance - tolerance && latest.close < resistance && latest.close < vwap;
  const breakRetestSucceeded = breakOfStructureUp && latest.low <= resistance + tolerance && latest.close > resistance;
  const breakRetestFailed = breakOfStructureDown && latest.high >= support - tolerance && latest.close < support;
  const labels = [
    higherHigh ? "HH" : "",
    higherLow ? "HL" : "",
    lowerHigh ? "LH" : "",
    lowerLow ? "LL" : "",
    breakOfStructureUp ? "bullish BOS" : "",
    breakOfStructureDown ? "bearish BOS" : "",
    changeOfCharacterUp ? "bullish CHoCH" : "",
    changeOfCharacterDown ? "bearish CHoCH" : "",
  ].filter(Boolean);

  return {
    higherHigh,
    higherLow,
    lowerHigh,
    lowerLow,
    breakOfStructureUp,
    breakOfStructureDown,
    changeOfCharacterUp,
    changeOfCharacterDown,
    successfulSupportRetest,
    failedResistanceRetest,
    breakRetestSucceeded,
    breakRetestFailed,
    swingHigh: resistance,
    swingLow: support,
    summary: `${labels.length ? labels.join(", ") : "no clear swing sequence"}; support ${priceLabel(support)}, resistance ${priceLabel(resistance)}`,
  };
}

export function priceLabel(value: number): string {
  return `$${value.toFixed(2)}`;
}

export function probabilityLabel(value: number): string {
  return `${roundNumber(value * 100, 1)}%`;
}
