import { regimeNoTradeReasons } from "./context-filters.ts";
import {
  easternDateString,
  isRegularSession,
  exponentialMovingAverageSeries,
  lastDefined,
  marketStructureContext,
  probabilityLabel,
  priceLabel,
  roundNumber,
} from "./indicators.ts";
import type {
  MarketRegimeId,
  RawRegimeClassification,
  RegimeAxes,
  RegimeClassifierFeatures,
  RegimeMarketContext,
  RegimeOpportunityState,
  RegimePrimaryTrend,
  RegimeSelectionFeature,
  RegimeVolatilityState,
} from "./types.ts";

export function regimeSelectionFeatures(market: RegimeMarketContext): RegimeClassifierFeatures {
  const ema20Series = exponentialMovingAverageSeries(market.closes, 20);
  const ema20 = lastDefined(ema20Series);
  const ema50 = lastDefined(exponentialMovingAverageSeries(market.closes, 50));
  const structure = marketStructureContext(market.candles, market.vwap);
  const priorDay = priorDayRangeForRegimeSelection(market);
  const vwapDistance = (market.latest.close - market.vwap) / Math.max(market.vwap, 0.01);
  const recentRange = recentRangeForRegimeSelection(market.candles);
  const openingBreakUp = market.latest.close > market.openingRange.high;
  const openingBreakDown = market.latest.close < market.openingRange.low;
  const priorDayBreakUp = priorDay ? market.latest.close > priorDay.high : false;
  const priorDayBreakDown = priorDay ? market.latest.close < priorDay.low : false;
  const ema20Slope = emaSlopeFromSeries(ema20Series, market.latest.close);
  const higherHighAndHigherLow = Boolean(structure?.higherHigh && structure.higherLow);
  const lowerHighAndLowerLow = Boolean(structure?.lowerHigh && structure.lowerLow);
  const macdHistogram = market.macd?.histogram ?? null;
  const atrPercent = market.atr.atr1m !== null ? market.atr.atr1m / Math.max(market.latest.close, 0.01) : market.atr.atrPercent;
  const atrHistory = oneMinuteAtrPercentHistoryForRegimeSelection(market);
  const atrPercentile = atrHistory.length && atrPercent ? percentileRank(atrHistory, atrPercent) : null;
  const atrExpanding = atrHistory.length >= 6 && atrPercent > meanNumber(atrHistory.slice(-6));
  const realizedVolatility = realizedVolatilityForRegimeSelection(market.closes);
  const bearishRejectionCandle = isBearishRejectionCandle(market.latest);
  const bullishRejectionCandle = isBullishRejectionCandle(market.latest);
  const recentVwapCrosses = recentLevelCrosses(market.candles.map((candle) => candle.close), market.vwap, 14);
  const priceChoppingAroundVwap = recentVwapCrosses >= 4 && Math.abs(vwapDistance) < 0.0015;
  const bullScore =
    (market.latest.close > market.vwap ? 1 : 0) +
    (ema20 !== null && market.latest.close > ema20 ? 1 : 0) +
    (ema20 !== null && ema50 !== null && ema20 > ema50 ? 1 : 0) +
    (ema20Slope > 0 ? 1 : 0) +
    (higherHighAndHigherLow ? 1 : 0) +
    (macdHistogram !== null && macdHistogram > 0 ? 1 : 0);
  const bearScore =
    (market.latest.close < market.vwap ? 1 : 0) +
    (ema20 !== null && market.latest.close < ema20 ? 1 : 0) +
    (ema20 !== null && ema50 !== null && ema20 < ema50 ? 1 : 0) +
    (ema20Slope < 0 ? 1 : 0) +
    (lowerHighAndLowerLow ? 1 : 0) +
    (macdHistogram !== null && macdHistogram < 0 ? 1 : 0);

  return {
    ema20,
    ema50,
    ema20Slope,
    vwap: market.vwap,
    adx: market.adx?.adx ?? null,
    atr: market.atr.atr1m,
    atrPercent,
    atrPercentile,
    atrExpanding,
    realizedVolatility,
    rsi: market.rsi,
    macdHistogram,
    bullScore,
    bearScore,
    volumeRatio: market.volume.relativeVolume,
    structure,
    higherHigh: Boolean(structure?.higherHigh),
    higherLow: Boolean(structure?.higherLow),
    lowerHigh: Boolean(structure?.lowerHigh),
    lowerLow: Boolean(structure?.lowerLow),
    higherHighAndHigherLow,
    lowerHighAndLowerLow,
    openingRangeHigh: market.openingRange.high,
    openingRangeLow: market.openingRange.low,
    recentRangeHigh: recentRange.high,
    recentRangeLow: recentRange.low,
    priorDayHigh: priorDay?.high ?? null,
    priorDayLow: priorDay?.low ?? null,
    distanceFromVwap: vwapDistance,
    openingBreakUp,
    openingBreakDown,
    priorDayBreakUp,
    priorDayBreakDown,
    bearishRejectionCandle,
    bullishRejectionCandle,
    priceChoppingAroundVwap,
    spreadTooWide: market.spreadLiquidity.spreadTooWide,
    volumeTooLow: market.spreadLiquidity.volumeTooLow || market.volume.relativeVolume < 0.55,
    display: [
      regimeFeature("Bull / Bear score", `${bullScore} / ${bearScore}`, Math.abs(bullScore - bearScore) <= 1 ? "warn" : "ok"),
      regimeFeature("EMA 20", ema20 === null ? "NA" : priceLabel(ema20), ema20 === null ? "na" : "ok"),
      regimeFeature("EMA 50", ema50 === null ? "NA" : priceLabel(ema50), ema50 === null ? "na" : "ok"),
      regimeFeature("EMA20 slope", probabilityLabel(ema20Slope), ema20Slope === 0 ? "warn" : "ok"),
      regimeFeature("VWAP", priceLabel(market.vwap), "ok"),
      regimeFeature("ADX 14", market.adx === null ? "NA" : market.adx.adx.toFixed(1), market.adx === null ? "na" : market.adx.adx >= 20 ? "ok" : "warn"),
      regimeFeature("ATR %ile", atrPercentile === null ? "NA" : probabilityLabel(atrPercentile), atrPercentile === null ? "na" : atrPercentile > 0.7 || atrPercentile < 0.3 ? "warn" : "ok"),
      regimeFeature("ATR expanding", atrExpanding ? "Yes" : "No", atrExpanding ? "ok" : "warn"),
      regimeFeature("RSI 14", market.rsi === null ? "NA" : market.rsi.toFixed(1), market.rsi === null ? "na" : market.rsi <= 30 || market.rsi >= 70 ? "warn" : "ok"),
      regimeFeature("MACD histogram", macdHistogram === null ? "NA" : macdHistogram.toFixed(3), macdHistogram === null ? "na" : macdHistogram === 0 ? "warn" : "ok"),
      regimeFeature("Volume ratio", `${market.volume.relativeVolume.toFixed(2)}x`, market.volume.relativeVolume < 0.75 ? "warn" : "ok"),
      regimeFeature("HH+HL / LH+LL", `${higherHighAndHigherLow ? "HH+HL" : "--"} / ${lowerHighAndLowerLow ? "LH+LL" : "--"}`, structure ? "ok" : "na"),
      regimeFeature("Recent range", `${priceLabel(recentRange.low)} - ${priceLabel(recentRange.high)}`, "ok"),
      regimeFeature("Prior day H/L", priorDay ? `${priceLabel(priorDay.low)} - ${priceLabel(priorDay.high)}` : "NA", priorDay ? "ok" : "na"),
      regimeFeature("Distance from VWAP", probabilityLabel(vwapDistance), Math.abs(vwapDistance) >= 0.004 ? "warn" : "ok"),
    ],
  };
}

export function buildRawRegimeCondition(market: RegimeMarketContext) {
  const features = regimeSelectionFeatures(market);
  const noTradeReasons = regimeNoTradeReasons(market, features);
  const classification = classifyRawRegime(market, features, noTradeReasons);
  const primaryTrend = classifyRegimePrimaryTrend(features);
  const volatility = classifyRegimeVolatility(features);
  const opportunity = classifyRegimeOpportunity(market, features, primaryTrend, noTradeReasons);
  const confidence = classification.confidence;
  return {
    classification,
    axes: classification.axes,
    rawRegime: classification.rawRegime,
    missingInputs: classification.missingInputs,
    evidence: classification.evidence,
    timestamp: classification.timestamp,
    features,
    noTradeReasons,
    primaryTrend,
    volatility,
    opportunity,
    confidence,
    key: classification.rawRegime,
    legacyKey: regimeConditionKey(primaryTrend, volatility, opportunity),
    contextKey: regimeConditionContextKey(market),
  };
}

export function classifyRawRegime(
  market: RegimeMarketContext,
  features: RegimeClassifierFeatures = regimeSelectionFeatures(market),
  noTradeReasons: string[] = regimeNoTradeReasons(market, features),
): RawRegimeClassification {
  const axes = classifyRegimeAxes(market, features, noTradeReasons);
  const rawRegime = compositeRegimeIdFromAxes(market, features, axes);
  const missingInputs = missingClassifierInputs(market, features);
  const confidence = rawRegimeConfidence(features, axes, noTradeReasons, missingInputs);
  return {
    axes,
    rawRegime,
    confidence,
    evidence: {
      close: market.latest.close,
      sma20: features.ema20,
      sma50: features.ema50,
      ema20Slope: features.ema20Slope,
      adx: features.adx,
      atrPercent: features.atrPercent,
      atrPercentile: features.atrPercentile,
      realizedVolatility: features.realizedVolatility,
      vwap: features.vwap,
      vwapSlope: market.vwapSlope,
      distanceFromVwap: features.distanceFromVwap,
      bullScore: features.bullScore,
      bearScore: features.bearScore,
      higherHighAndHigherLow: features.higherHighAndHigherLow,
      lowerHighAndLowerLow: features.lowerHighAndLowerLow,
      openingBreakUp: features.openingBreakUp,
      openingBreakDown: features.openingBreakDown,
      priorDayBreakUp: features.priorDayBreakUp,
      priorDayBreakDown: features.priorDayBreakDown,
      relativeVolume: features.volumeRatio,
      volumeTrend: volumeTrendLabel(market),
      spreadPercent: market.spreadLiquidity.spreadPercent,
      spreadTooWide: features.spreadTooWide,
      quoteFreshness: "unknown",
      qqqRelativeStrength: "unknown",
      iwmRelativeStrength: "unknown",
      marketBreadth: "unknown",
      vixState: "unknown",
      esFuturesState: "unknown",
      scheduledEventState: axes.eventRisk,
      timeOfDay: market.timeOfDay.label,
      noTradeReasons,
    },
    missingInputs,
    timestamp: market.latest.timestamp,
  };
}

export function classifyRegimeAxes(
  market: RegimeMarketContext,
  features: RegimeClassifierFeatures,
  noTradeReasons: string[],
): RegimeAxes {
  return {
    direction: classifyDirectionAxis(features),
    volatility: classifyVolatilityAxis(features, market),
    structure: classifyStructureAxis(market, features, noTradeReasons),
    liquidity: classifyLiquidityAxis(market, features),
    session: classifySessionAxis(market),
    eventRisk: classifyEventRiskAxis(noTradeReasons),
  };
}

export function classifyRegimePrimaryTrend(features: RegimeClassifierFeatures): RegimePrimaryTrend {
  const adx = features.adx ?? 0;
  if (features.bullScore >= 5 && adx >= 20) {
    return "Strong uptrend";
  }
  if (features.bullScore >= 3) {
    return "Weak uptrend";
  }
  if (features.bearScore >= 5 && adx >= 20) {
    return "Strong downtrend";
  }
  if (features.bearScore >= 3) {
    return "Weak downtrend";
  }
  return "Sideways / range-bound";
}

export function classifyRegimeVolatility(features: RegimeClassifierFeatures): RegimeVolatilityState {
  const volatility = classifyVolatilityAxisFromFeatures(features);
  if (volatility === "expanded" || volatility === "extreme") {
    return "High volatility";
  }
  if (volatility === "compressed") {
    return "Low volatility";
  }
  return "Normal volatility";
}

export function compositeRegimeIdFromAxes(
  market: RegimeMarketContext,
  features: RegimeClassifierFeatures,
  axes: RegimeAxes,
): MarketRegimeId {
  if (axes.volatility === "extreme") {
    return "extreme_volatility_no_trade";
  }
  if (axes.eventRisk === "blackout" || axes.eventRisk === "elevated") {
    return "event_risk";
  }
  if (axes.liquidity === "poor") {
    return "liquidity_stress";
  }
  if (isGapSession(market)) {
    return "gap_session";
  }
  if (axes.structure === "failed_breakout" || axes.structure === "reversal") {
    return "failed_breakout_reversal";
  }
  if (axes.structure === "breakout" && axes.session === "opening") {
    return "opening_breakout";
  }
  if (axes.structure === "breakout" || (axes.volatility === "expanded" && features.volumeRatio >= 1.2)) {
    return "intraday_expansion";
  }
  if (axes.volatility === "expanded" && axes.structure === "trend") {
    return "high_volatility_trend";
  }
  if (axes.volatility === "compressed" && axes.structure === "range") {
    return "low_volatility_quiet";
  }
  if (axes.structure === "range") {
    return "range_bound";
  }
  if (axes.structure === "mixed") {
    return "choppy_mixed";
  }
  if (axes.direction === "strong_up") {
    return "strong_uptrend";
  }
  if (axes.direction === "weak_up") {
    return "weak_uptrend";
  }
  if (axes.direction === "strong_down") {
    return "strong_downtrend";
  }
  if (axes.direction === "weak_down") {
    return "weak_downtrend";
  }
  return "choppy_mixed";
}

export function classifyRegimeOpportunity(
  market: RegimeMarketContext,
  features: RegimeClassifierFeatures,
  primaryTrend: RegimePrimaryTrend,
  noTradeReasons: string[],
): RegimeOpportunityState {
  if (noTradeReasons.length > 0) {
    return "No-trade";
  }
  const volumeConfirms = features.volumeRatio > 1.2;
  if (market.latest.close > features.recentRangeHigh && volumeConfirms && features.atrExpanding) {
    return "Bullish breakout";
  }
  if (market.latest.close < features.recentRangeLow && volumeConfirms && features.atrExpanding) {
    return "Bearish breakout";
  }
  if (features.rsi !== null && features.rsi > 70 && features.distanceFromVwap > 0.004 && features.bearishRejectionCandle) {
    return "Bearish reversal risk";
  }
  if (features.rsi !== null && features.rsi < 30 && features.distanceFromVwap < -0.004 && features.bullishRejectionCandle) {
    return "Bullish reversal risk";
  }
  if (primaryTrend === "Sideways / range-bound") {
    return "Mean reversion";
  }
  return "Trend continuation";
}

export function regimeMarketConditionConfidence(
  features: RegimeClassifierFeatures,
  primaryTrend: RegimePrimaryTrend,
  volatility: RegimeVolatilityState,
  opportunity: RegimeOpportunityState,
  noTradeReasons: string[],
): number {
  if (opportunity === "No-trade") {
    return roundNumber(Math.max(0, Math.min(0.6, 0.35 - noTradeReasons.length * 0.08)), 4);
  }
  const trendScore = Math.max(features.bullScore, features.bearScore) / 6;
  const trendClarity = Math.abs(features.bullScore - features.bearScore) / 6;
  const adxConfidence = Math.max(0, Math.min(1, (features.adx ?? 0) / 30));
  const volatilityConfidence =
    features.atrPercentile === null
      ? 0.5
      : volatility === "Normal volatility"
        ? 0.65
        : Math.max(features.atrPercentile, 1 - features.atrPercentile);
  const opportunityBoost =
    opportunity === "Bullish breakout" || opportunity === "Bearish breakout"
      ? features.atrExpanding && features.volumeRatio > 1.2
        ? 0.15
        : 0
      : opportunity === "Bullish reversal risk" || opportunity === "Bearish reversal risk"
        ? 0.1
        : 0.05;
  const trendPenalty = primaryTrend === "Sideways / range-bound" && opportunity === "Trend continuation" ? -0.15 : 0;
  return roundNumber(Math.max(0, Math.min(1, 0.2 + trendScore * 0.25 + trendClarity * 0.25 + adxConfidence * 0.15 + volatilityConfidence * 0.15 + opportunityBoost + trendPenalty)), 4);
}

export function regimeConditionKey(primaryTrend: RegimePrimaryTrend, volatility: RegimeVolatilityState, opportunity: RegimeOpportunityState): string {
  return `${primaryTrend} + ${volatility} + ${opportunity}`;
}

export function regimeConditionContextKey(market: RegimeMarketContext): string {
  return `${market.latest.symbol}|${easternDateString(market.latest.timestamp)}`;
}

function regimeFeature(name: string, value: string, status: RegimeSelectionFeature["status"]): RegimeSelectionFeature {
  return { name, value, status };
}

function classifyDirectionAxis(features: RegimeClassifierFeatures): RegimeAxes["direction"] {
  const adx = features.adx ?? 0;
  const edge = features.bullScore - features.bearScore;
  if (edge >= 4 && adx >= 20) {
    return "strong_up";
  }
  if (edge >= 2) {
    return "weak_up";
  }
  if (edge <= -4 && adx >= 20) {
    return "strong_down";
  }
  if (edge <= -2) {
    return "weak_down";
  }
  return "neutral";
}

function classifyVolatilityAxis(features: RegimeClassifierFeatures, market: RegimeMarketContext): RegimeAxes["volatility"] {
  if (market.atr.regime === "extreme" || features.atrPercentile !== null && features.atrPercentile >= 0.95 || features.realizedVolatility >= 0.006) {
    return "extreme";
  }
  return classifyVolatilityAxisFromFeatures(features);
}

function classifyVolatilityAxisFromFeatures(features: RegimeClassifierFeatures): RegimeAxes["volatility"] {
  if (features.atrPercentile !== null && features.atrPercentile > 0.75 || features.atrPercent >= 0.0025 || features.realizedVolatility >= 0.0035) {
    return "expanded";
  }
  if (features.atrPercentile !== null && features.atrPercentile < 0.3 || features.atrPercent > 0 && features.atrPercent < 0.0005 || features.realizedVolatility < 0.0007) {
    return "compressed";
  }
  return "normal";
}

function classifyStructureAxis(
  market: RegimeMarketContext,
  features: RegimeClassifierFeatures,
  noTradeReasons: string[],
): RegimeAxes["structure"] {
  const failedBreakout =
    market.structure?.breakRetestFailed ||
    market.structure?.changeOfCharacterDown ||
    market.structure?.changeOfCharacterUp ||
    features.bearishRejectionCandle ||
    features.bullishRejectionCandle;
  if (features.openingBreakUp || features.openingBreakDown || features.priorDayBreakUp || features.priorDayBreakDown) {
    return failedBreakout ? "failed_breakout" : "breakout";
  }
  if (failedBreakout) {
    return "reversal";
  }
  if (features.higherHighAndHigherLow || features.lowerHighAndLowerLow) {
    return "trend";
  }
  if (features.priceChoppingAroundVwap || noTradeReasons.some((reason) => reason.includes("close"))) {
    return "mixed";
  }
  if (Math.abs(features.distanceFromVwap) < 0.002 && (features.adx ?? 0) < 18) {
    return "range";
  }
  return "mixed";
}

function classifyLiquidityAxis(market: RegimeMarketContext, features: RegimeClassifierFeatures): RegimeAxes["liquidity"] {
  if (!Number.isFinite(market.spreadLiquidity.spreadPercent) || market.latest.volume < 0) {
    return "unknown";
  }
  if (features.spreadTooWide || features.volumeTooLow || features.volumeRatio < 0.45) {
    return "poor";
  }
  if (features.volumeRatio < 0.75 || market.spreadLiquidity.spreadPercent > market.spreadLiquidity.maxSpreadPercent * 0.75) {
    return "acceptable";
  }
  return "good";
}

function classifySessionAxis(market: RegimeMarketContext): RegimeAxes["session"] {
  const minutes = market.timeOfDay.minutes;
  if (minutes < 9 * 60 + 30 || minutes >= 16 * 60) {
    return "outside_regular";
  }
  if (minutes < 10 * 60 + 30) {
    return "opening";
  }
  if (minutes < 13 * 60 + 30) {
    return "midday";
  }
  if (minutes < 15 * 60 + 30) {
    return "afternoon";
  }
  return "closing";
}

function classifyEventRiskAxis(noTradeReasons: string[]): RegimeAxes["eventRisk"] {
  return noTradeReasons.some((reason) => reason.toLowerCase().includes("event blackout")) ? "blackout" : "none";
}

function missingClassifierInputs(market: RegimeMarketContext, features: RegimeClassifierFeatures): string[] {
  const missing: string[] = [];
  if (!market.oneMinuteCandles.length) missing.push("SPY one-minute candles");
  if (!market.fiveMinuteCandles.length) missing.push("SPY five-minute candles");
  if (features.ema20 === null || features.ema50 === null) missing.push("moving-average position and slope");
  if (!market.structure) missing.push("higher-high/higher-low structure");
  if (features.adx === null) missing.push("ADX");
  if (features.atr === null || features.atrPercentile === null) missing.push("ATR percentile");
  missing.push("spread quote freshness");
  missing.push("QQQ/IWM relative strength");
  missing.push("market breadth");
  missing.push("VIX state");
  missing.push("ES futures state");
  missing.push("scheduled event state");
  return missing;
}

function rawRegimeConfidence(
  features: RegimeClassifierFeatures,
  axes: RegimeAxes,
  noTradeReasons: string[],
  missingInputs: string[],
): number {
  const primaryTrend = classifyRegimePrimaryTrend(features);
  const volatility = classifyRegimeVolatility(features);
  const opportunity =
    axes.structure === "breakout"
      ? features.bullScore >= features.bearScore
        ? "Bullish breakout"
        : "Bearish breakout"
      : axes.structure === "failed_breakout" || axes.structure === "reversal"
        ? features.bullScore >= features.bearScore
          ? "Bearish reversal risk"
          : "Bullish reversal risk"
        : axes.structure === "range"
          ? "Mean reversion"
          : "Trend continuation";
  const baseline = regimeMarketConditionConfidence(features, primaryTrend, volatility, noTradeReasons.length ? "No-trade" : opportunity, noTradeReasons);
  const axisCompleteness = Math.max(0.55, 1 - missingInputs.length * 0.025);
  const riskOffBoost = axes.volatility === "extreme" || axes.liquidity === "poor" || axes.eventRisk === "blackout" ? 0.08 : 0;
  return roundNumber(Math.max(0, Math.min(1, baseline * axisCompleteness + riskOffBoost)), 4);
}

function realizedVolatilityForRegimeSelection(closes: number[]): number {
  const sample = closes.slice(-20);
  if (sample.length < 3) {
    return 0;
  }
  const returns: number[] = [];
  for (let index = 1; index < sample.length; index += 1) {
    const previous = sample[index - 1];
    if (previous > 0) {
      returns.push((sample[index] - previous) / previous);
    }
  }
  const mean = meanNumber(returns);
  return Math.sqrt(meanNumber(returns.map((value) => (value - mean) ** 2)));
}

function isGapSession(market: RegimeMarketContext): boolean {
  if (market.priorClose === null || market.priorClose <= 0) {
    return false;
  }
  return Math.abs((market.dayOpen - market.priorClose) / market.priorClose) >= 0.0035;
}

function volumeTrendLabel(market: RegimeMarketContext): "rising" | "falling" | "flat" | "unknown" {
  const sample = market.candles.slice(-10);
  if (sample.length < 6) {
    return "unknown";
  }
  const first = meanNumber(sample.slice(0, 5).map((candle) => candle.volume));
  const last = meanNumber(sample.slice(-5).map((candle) => candle.volume));
  if (last > first * 1.1) {
    return "rising";
  }
  if (last < first * 0.9) {
    return "falling";
  }
  return "flat";
}

function priorDayRangeForRegimeSelection(market: RegimeMarketContext): { high: number; low: number } | null {
  const latestDay = easternDateString(market.latest.timestamp);
  const priorCandles = market.allCandles
    .filter((candle) => isRegularSession(candle.timestamp) && easternDateString(candle.timestamp) < latestDay)
    .sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());
  const priorDay = priorCandles.at(-1) ? easternDateString(priorCandles.at(-1)!.timestamp) : "";
  const dayCandles = priorCandles.filter((candle) => easternDateString(candle.timestamp) === priorDay);
  if (!dayCandles.length) {
    return null;
  }
  return {
    high: Math.max(...dayCandles.map((candle) => candle.high)),
    low: Math.min(...dayCandles.map((candle) => candle.low)),
  };
}

function recentRangeForRegimeSelection(candles: RegimeMarketContext["candles"]): { high: number; low: number } {
  const sample = candles.slice(-21, -1);
  const rangeCandles = sample.length ? sample : candles.slice(-20);
  return {
    high: Math.max(...rangeCandles.map((candle) => candle.high)),
    low: Math.min(...rangeCandles.map((candle) => candle.low)),
  };
}

function emaSlopeFromSeries(values: Array<number | null>, latestPrice: number): number {
  const defined = values.filter((value): value is number => value !== null);
  if (defined.length < 6) {
    return 0;
  }
  const current = defined.at(-1)!;
  const previous = defined.at(-6)!;
  return (current - previous) / Math.max(latestPrice, 0.01);
}

function oneMinuteAtrPercentHistoryForRegimeSelection(market: RegimeMarketContext): number[] {
  const latestTime = new Date(market.latest.timestamp).getTime();
  const source = (market.oneMinuteCandles.length ? market.oneMinuteCandles : market.allCandles)
    .filter((candle) => isRegularSession(candle.timestamp) && new Date(candle.timestamp).getTime() <= latestTime)
    .sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());
  const historySource = source.length >= 80 ? source.slice(-1600) : market.candles;
  const values: number[] = [];
  for (let end = 15; end <= historySource.length; end += 1) {
    const sample = historySource.slice(0, end);
    const atr = averageTrueRangeLocal(sample, 14);
    const close = sample.at(-1)?.close ?? 0;
    if (atr !== null && close > 0) {
      values.push(atr / close);
    }
  }
  return values;
}

function averageTrueRangeLocal(candles: RegimeMarketContext["candles"], period: number): number | null {
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

function percentileRank(values: number[], current: number): number | null {
  const finiteValues = values.filter((value) => Number.isFinite(value)).sort((left, right) => left - right);
  if (!finiteValues.length) {
    return null;
  }
  return finiteValues.filter((value) => value <= current).length / finiteValues.length;
}

function meanNumber(values: number[]): number {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

function recentLevelCrosses(values: number[], level: number, lookback: number): number {
  const sample = values.slice(-lookback);
  if (sample.length < 2 || !level) {
    return 0;
  }
  const signs = sample.map((value) => value >= level);
  return signs.slice(1).reduce((count, sign, index) => count + (sign !== signs[index] ? 1 : 0), 0);
}

function isBearishRejectionCandle(candle: RegimeMarketContext["latest"]): boolean {
  const range = Math.max(candle.high - candle.low, 0.01);
  const body = Math.abs(candle.close - candle.open);
  const upperWick = candle.high - Math.max(candle.open, candle.close);
  const closeBelowMidpoint = candle.close < candle.low + range * 0.45;
  return upperWick >= Math.max(body * 1.2, range * 0.35) && closeBelowMidpoint;
}

function isBullishRejectionCandle(candle: RegimeMarketContext["latest"]): boolean {
  const range = Math.max(candle.high - candle.low, 0.01);
  const body = Math.abs(candle.close - candle.open);
  const lowerWick = Math.min(candle.open, candle.close) - candle.low;
  const closeAboveMidpoint = candle.close > candle.low + range * 0.55;
  return lowerWick >= Math.max(body * 1.2, range * 0.35) && closeAboveMidpoint;
}
