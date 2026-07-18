import { priceLabel, probabilityLabel } from "./indicators.ts";
import type {
  DirectionalStrategyResult,
  MarketRegimeId,
  RegimeMarketContext,
  RegimeRawStrategySignal,
  RegimeStrategyDefinition,
  StrategyFamily,
  StrategyRole,
} from "./types.ts";

const baseWeights = {
  moving_average_trend: 0.11,
  trend_pullback: 0.1,
  rsi_mean_reversion: 0.07,
  bollinger_band_mean_reversion: 0.07,
  opening_range_breakout: 0.1,
  intraday_breakout: 0.1,
  macd_momentum: 0.08,
  market_structure: 0.12,
  gap_continuation_fade: 0.06,
};

type StrategyDefinitionInput = {
  id: string;
  name: string;
  role: StrategyRole;
  family: StrategyFamily;
  supportedDirections?: Array<"long" | "short">;
  requiredInputs?: string[];
  minimumBars?: number;
  supportedRegimes?: MarketRegimeId[];
  incompatibleRegimes?: MarketRegimeId[];
  enabledByDefault?: boolean;
  baseWeight?: number;
  version?: string;
  aliases?: string[];
  key?: string;
  signal: (market: RegimeMarketContext) => RegimeRawStrategySignal;
};

const allRegimes: MarketRegimeId[] = [
  "strong_uptrend",
  "weak_uptrend",
  "strong_downtrend",
  "weak_downtrend",
  "range_bound",
  "sideways_range",
  "opening_breakout",
  "intraday_expansion",
  "high_volatility_trend",
  "low_volatility_quiet",
  "failed_breakout_reversal",
  "choppy_mixed",
  "gap_session",
  "event_risk",
  "liquidity_stress",
  "extreme_volatility_no_trade",
];

function defineStrategy(input: StrategyDefinitionInput): RegimeStrategyDefinition {
  return {
    supportedDirections: input.supportedDirections ?? ["long", "short"],
    requiredInputs: input.requiredInputs ?? ["candles", "latest"],
    minimumBars: input.minimumBars ?? 5,
    supportedRegimes: input.supportedRegimes ?? allRegimes,
    incompatibleRegimes: input.incompatibleRegimes ?? [],
    enabledByDefault: input.enabledByDefault ?? true,
    baseWeight: input.baseWeight ?? 0,
    version: input.version ?? "1.0.0",
    aliases: input.aliases ?? [],
    key: input.key,
    id: input.id,
    name: input.name,
    role: input.role,
    family: input.family,
    signal: input.signal,
  };
}

export const regimeSelectionStrategies: RegimeStrategyDefinition[] = [
  defineStrategy({ key: "C1", id: "moving_average_trend", name: "Moving Average Trend", role: "directional", family: "trend_momentum", baseWeight: baseWeights.moving_average_trend, requiredInputs: ["candles", "latest", "sma20", "sma50"], minimumBars: 50, signal: movingAverageTrend }),
  defineStrategy({ key: "C2", id: "vwap_position", name: "VWAP Position", role: "regime_context", family: "regime_context", requiredInputs: ["candles", "latest", "vwap"], minimumBars: 5, signal: vwapPosition }),
  defineStrategy({ key: "C3", id: "trend_pullback", name: "Trend Pullback", role: "directional", family: "trend_momentum", baseWeight: baseWeights.trend_pullback, aliases: ["first_pullback_after_open"], requiredInputs: ["candles", "latest", "sma20", "sma50", "vwap"], minimumBars: 50, signal: trendPullback }),
  defineStrategy({ key: "C4", id: "rsi_mean_reversion", name: "RSI Mean Reversion", role: "directional", family: "mean_reversion", baseWeight: baseWeights.rsi_mean_reversion, requiredInputs: ["candles", "latest", "rsi"], minimumBars: 15, signal: rsiMeanReversion }),
  defineStrategy({ key: "C5", id: "bollinger_band_mean_reversion", name: "Bollinger Band Mean Reversion", role: "directional", family: "mean_reversion", baseWeight: baseWeights.bollinger_band_mean_reversion, aliases: ["bollinger_atr_reversion"], requiredInputs: ["candles", "latest", "bollinger_bands"], minimumBars: 20, signal: bollingerMeanReversion }),
  defineStrategy({ key: "C6", id: "opening_range_breakout", name: "Opening Range Breakout", role: "directional", family: "breakout", baseWeight: baseWeights.opening_range_breakout, requiredInputs: ["candles", "latest", "opening_range"], minimumBars: 15, signal: openingRangeBreakout }),
  defineStrategy({ key: "C7", id: "intraday_breakout", name: "Intraday Breakout", role: "directional", family: "breakout", baseWeight: baseWeights.intraday_breakout, requiredInputs: ["candles", "latest", "recent_range"], minimumBars: 21, signal: intradayBreakout }),
  defineStrategy({ key: "C8", id: "macd_momentum", name: "MACD Momentum", role: "directional", family: "trend_momentum", baseWeight: baseWeights.macd_momentum, requiredInputs: ["candles", "latest", "macd"], minimumBars: 26, signal: macdMomentum }),
  defineStrategy({ key: "C9", id: "market_structure", name: "Market Structure", role: "directional", family: "trend_momentum", baseWeight: baseWeights.market_structure, requiredInputs: ["candles", "latest", "market_structure"], minimumBars: 10, signal: marketStructure }),
  defineStrategy({ key: "C10", id: "gap_continuation_fade", name: "Gap Continuation/Fade", role: "directional", family: "gap_session_event", baseWeight: baseWeights.gap_continuation_fade, requiredInputs: ["candles", "latest", "prior_close", "opening_range"], minimumBars: 15, signal: gapContinuationFade }),
  defineStrategy({ key: "C11", id: "volume_confirmation", name: "Volume Confirmation", role: "confirmation", family: "confirmation", requiredInputs: ["candles", "latest", "volume"], minimumBars: 5, signal: volumeConfirmation }),
  defineStrategy({ key: "R1", id: "vwap_trend_continuation", name: "VWAP Trend Continuation", role: "directional", family: "trend_momentum", baseWeight: 0.1, requiredInputs: ["candles", "latest", "vwap", "sma20", "sma50"], minimumBars: 50, signal: vwapTrendContinuation }),
  defineStrategy({ key: "R2", id: "vwap_mean_reversion", name: "VWAP Mean Reversion", role: "directional", family: "mean_reversion", baseWeight: 0.09, requiredInputs: ["candles", "latest", "vwap", "adx"], minimumBars: 15, signal: vwapMeanReversion }),
  defineStrategy({ key: "R3", id: "failed_breakout_reversal", name: "Failed Breakout Reversal", role: "directional", family: "reversal", baseWeight: 0.08, aliases: ["failed_breakout_strategy"], requiredInputs: ["candles", "latest", "recent_range"], minimumBars: 21, signal: failedBreakoutReversal }),
  defineStrategy({ key: "R4", id: "liquidity_sweep_reversal", name: "Liquidity Sweep Reversal", role: "directional", family: "reversal", baseWeight: 0.08, requiredInputs: ["candles", "latest", "recent_range", "volume"], minimumBars: 21, signal: liquiditySweepReversal }),
  defineStrategy({ key: "R5", id: "adx_trend_strength", name: "ADX Trend Strength", role: "confirmation", family: "confirmation", requiredInputs: ["candles", "latest", "adx"], minimumBars: 15, signal: adxTrendStrength }),
  defineStrategy({ key: "R6", id: "atr_volatility_regime", name: "ATR Volatility Regime", role: "regime_context", family: "regime_context", requiredInputs: ["candles", "latest", "atr"], minimumBars: 15, signal: atrVolatilityRegime }),
  defineStrategy({ key: "R7", id: "volatility_breakout", name: "Volatility Breakout", role: "directional", family: "breakout", baseWeight: 0.08, requiredInputs: ["candles", "latest", "atr", "recent_range", "volume"], minimumBars: 21, signal: volatilityBreakout }),
  defineStrategy({ key: "R8", id: "cash_avoid_filter", name: "Cash/Avoid Trading", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest", "spread_liquidity", "time_of_day"], minimumBars: 5, signal: cashAvoidFilter }),
  defineStrategy({ id: "missing_critical_data", name: "Missing Critical Data", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest"], minimumBars: 5, signal: missingCriticalDataGate }),
  defineStrategy({ id: "stale_data", name: "Stale Data", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest"], minimumBars: 5, signal: staleDataGate }),
  defineStrategy({ id: "extreme_volatility", name: "Extreme Volatility", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest", "atr"], minimumBars: 15, signal: extremeVolatilityGate }),
  defineStrategy({ id: "excessive_spread", name: "Excessive Spread", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest", "spread_liquidity"], minimumBars: 5, signal: excessiveSpreadGate }),
  defineStrategy({ id: "insufficient_liquidity", name: "Insufficient Liquidity", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest", "volume"], minimumBars: 5, signal: insufficientLiquidityGate }),
  defineStrategy({ id: "event_blackout", name: "Event Blackout", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest"], minimumBars: 5, signal: eventBlackoutGate }),
  defineStrategy({ id: "halt_luld", name: "Halt/LULD", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest"], minimumBars: 5, signal: haltLuldGate }),
  defineStrategy({ id: "circuit_breaker", name: "Circuit Breaker", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest"], minimumBars: 5, signal: circuitBreakerGate }),
  defineStrategy({ id: "unsupported_session", name: "Unsupported Session", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest", "time_of_day"], minimumBars: 5, signal: unsupportedSessionGate }),
];

function vote(signal: RegimeRawStrategySignal["signal"], confidence: number, reason: string): RegimeRawStrategySignal {
  return { signal, confidence, reason, quality: confidence, evidence: {} };
}

export function evaluateRegimeStrategyDefinition(strategy: RegimeStrategyDefinition, market: RegimeMarketContext): RegimeRawStrategySignal {
  if (!strategy.enabledByDefault) {
    return {
      signal: "Hold",
      confidence: 0,
      eligible: false,
      reason: `${strategy.name} is disabled`,
    };
  }
  if (market.candles.length < strategy.minimumBars) {
    return {
      signal: "Hold",
      confidence: 0,
      eligible: false,
      reason: `${strategy.name} needs ${strategy.minimumBars} bars, has ${market.candles.length}`,
    };
  }
  const missingInputs = missingRequiredInputs(strategy, market);
  if (missingInputs.length) {
    return {
      signal: "Hold",
      confidence: 0,
      eligible: false,
      reason: `${strategy.name} missing required inputs: ${missingInputs.join(", ")}`,
    };
  }
  const raw = strategy.signal(market);
  return {
    ...raw,
    confidence: finiteNumberOrDefault(raw.confidence, 0),
    quality: finiteNumberOrDefault(raw.quality ?? raw.confidence, 0),
    evidence: { ...defaultStrategyEvidence(market), ...(raw.evidence ?? {}) },
    role: raw.role ?? strategy.role,
    eligible: raw.eligible ?? true,
  };
}

export function buildDirectionalStrategyResult(input: {
  strategy: RegimeStrategyDefinition;
  raw: RegimeRawStrategySignal;
  market: RegimeMarketContext;
  decisionTimestamp: string;
  effectiveWeight: number;
}): DirectionalStrategyResult | null {
  if (input.strategy.role !== "directional") {
    return null;
  }
  const eligible = input.raw.eligible !== false;
  const confidence = clamp01(input.raw.confidence);
  const quality = clamp01(input.raw.quality ?? input.raw.confidence);
  const signal = input.raw.signal;
  const direction = signal === "Buy" ? 1 : signal === "Sell" ? -1 : 0;
  const effectiveWeight = Math.max(0, finiteNumberOrDefault(input.effectiveWeight, 0));
  const result: DirectionalStrategyResult = {
    strategyId: input.strategy.id,
    family: input.strategy.family,
    role: "directional",
    eligible,
    signal,
    confidence,
    quality,
    effectiveWeight,
    signedContribution: direction * effectiveWeight * confidence * quality,
    timestamp: input.decisionTimestamp,
    evidence: sanitizeEvidence(input.raw.evidence ?? {}, input.market.latest.timestamp),
    reason: input.raw.reason,
    invalidReason: input.raw.invalidReason,
  };
  const validationErrors = validateDirectionalStrategyResult(result, {
    knownStrategyIds: regimeSelectionStrategies.map((strategy) => strategy.id),
    decisionTimestamp: input.decisionTimestamp,
    latestCandleTimestamp: input.market.latest.timestamp,
  });
  return validationErrors.length
    ? {
        ...result,
        eligible: false,
        confidence: 0,
        quality: 0,
        effectiveWeight: 0,
        signedContribution: 0,
        signal: "Hold",
        invalidReason: validationErrors.join("; "),
      }
    : result;
}

export function validateDirectionalStrategyResult(
  result: DirectionalStrategyResult,
  context: {
    knownStrategyIds: string[];
    decisionTimestamp: string;
    latestCandleTimestamp: string;
  },
): string[] {
  const errors: string[] = [];
  if (!context.knownStrategyIds.includes(result.strategyId)) {
    errors.push(`Unknown strategy ID ${result.strategyId}`);
  }
  if (result.role !== "directional") {
    errors.push("Directional result role must be directional");
  }
  validateFiniteRange(result.confidence, 0, 1, "confidence", errors);
  validateFiniteRange(result.quality, 0, 1, "quality", errors);
  validateFiniteRange(result.effectiveWeight, 0, Number.POSITIVE_INFINITY, "effectiveWeight", errors);
  validateFiniteRange(result.signedContribution, Number.NEGATIVE_INFINITY, Number.POSITIVE_INFINITY, "signedContribution", errors);
  if (!result.timestamp || !Number.isFinite(new Date(result.timestamp).getTime())) {
    errors.push("Missing or invalid timestamp");
  }
  if (new Date(result.timestamp).getTime() > new Date(context.decisionTimestamp).getTime()) {
    errors.push("Strategy output timestamp is newer than decision timestamp");
  }
  const evidenceTimestamp = evidenceTimestampValue(result.evidence);
  if (evidenceTimestamp !== null && evidenceTimestamp > new Date(context.latestCandleTimestamp).getTime()) {
    errors.push("Evidence generated from future candles");
  }
  if (result.signal === "Buy" && result.signedContribution < 0) {
    errors.push("Buy signedContribution must be positive or zero");
  }
  if (result.signal === "Sell" && result.signedContribution > 0) {
    errors.push("Sell signedContribution must be negative or zero");
  }
  if (result.signal === "Hold" && result.signedContribution !== 0) {
    errors.push("Hold signedContribution must be zero");
  }
  if (!result.reason.trim()) {
    errors.push("Missing human-readable reason");
  }
  if (!result.evidence || typeof result.evidence !== "object") {
    errors.push("Missing machine-readable evidence");
  }
  return errors;
}

function validateFiniteRange(value: number, min: number, max: number, label: string, errors: string[]) {
  if (!Number.isFinite(value)) {
    errors.push(`${label} must be finite`);
    return;
  }
  if (value < min || value > max) {
    errors.push(`${label} outside ${min}-${max}`);
  }
}

function sanitizeEvidence(
  evidence: Record<string, number | string | boolean | null>,
  latestCandleTimestamp: string,
): Record<string, number | string | boolean | null> {
  return {
    latestCandleTimestamp,
    ...evidence,
  };
}

function evidenceTimestampValue(evidence: Record<string, number | string | boolean | null>): number | null {
  const raw = evidence.latestCandleTimestamp ?? evidence.sourceCandleTimestamp ?? evidence.timestamp;
  if (typeof raw !== "string") {
    return null;
  }
  const time = new Date(raw).getTime();
  return Number.isFinite(time) ? time : null;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, finiteNumberOrDefault(value, 0)));
}

function finiteNumberOrDefault(value: number, fallback: number): number {
  return Number.isFinite(value) ? value : fallback;
}

function missingRequiredInputs(strategy: RegimeStrategyDefinition, market: RegimeMarketContext): string[] {
  return strategy.requiredInputs.filter((input) => !hasRequiredInput(input, market));
}

function hasRequiredInput(input: string, market: RegimeMarketContext): boolean {
  switch (input) {
    case "candles":
      return market.candles.length > 0;
    case "latest":
      return Boolean(market.latest);
    case "sma20":
      return market.sma20 !== null;
    case "sma50":
      return market.sma50 !== null;
    case "vwap":
      return Number.isFinite(market.vwap);
    case "rsi":
      return market.rsi !== null;
    case "macd":
      return market.macd !== null;
    case "bollinger_bands":
      return market.bands !== null;
    case "adx":
      return market.adx !== null;
    case "atr":
      return market.atr.atr1m !== null || market.atr.atr5m !== null;
    case "market_structure":
      return market.structure !== null;
    case "opening_range":
      return Number.isFinite(market.openingRange.high) && Number.isFinite(market.openingRange.low);
    case "recent_range":
      return Number.isFinite(market.priorHigh) && Number.isFinite(market.priorLow);
    case "prior_close":
      return market.priorClose !== null;
    case "volume":
      return Number.isFinite(market.latest.volume) && market.latest.volume >= 0;
    case "spread_liquidity":
      return Boolean(market.spreadLiquidity);
    case "time_of_day":
      return Boolean(market.timeOfDay);
    default:
      return true;
  }
}

function defaultStrategyEvidence(market: RegimeMarketContext): Record<string, number | string | boolean | null> {
  return {
    close: market.latest.close,
    volume: market.latest.volume,
    vwap: market.vwap,
    sma20: market.sma20,
    sma50: market.sma50,
    rsi: market.rsi,
    atrPercent: market.atr.atrPercent,
    adx: market.adx?.adx ?? null,
    sourceCandleTimestamp: market.latest.timestamp,
  };
}

function movingAverageTrend(market: RegimeMarketContext): RegimeRawStrategySignal {
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

function vwapPosition(market: RegimeMarketContext): RegimeRawStrategySignal {
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
  if (market.latest.close > market.vwap) {
    buyConfidence += 0.25;
    buyReasons.push("price above VWAP");
  }
  if (market.vwapSlope > 0.00005) {
    buyConfidence += 0.2;
    buyReasons.push("VWAP slope positive");
  }
  if (closesAbove === 3) {
    buyConfidence += 0.2;
    buyReasons.push("last 3 closes above VWAP");
  }
  if (pullbackHeldVwap) {
    buyConfidence += 0.2;
    buyReasons.push("pullback held VWAP");
  }
  if (volumeSupportsBuy) {
    buyConfidence += 0.15;
    buyReasons.push("volume supports move");
  }

  let sellConfidence = 0;
  const sellReasons: string[] = [];
  if (market.latest.close < market.vwap) {
    sellConfidence += 0.25;
    sellReasons.push("price below VWAP");
  }
  if (market.vwapSlope < -0.00005) {
    sellConfidence += 0.2;
    sellReasons.push("VWAP slope negative");
  }
  if (closesBelow === 3) {
    sellConfidence += 0.2;
    sellReasons.push("last 3 closes below VWAP");
  }
  if (rejectedVwap) {
    sellConfidence += 0.2;
    sellReasons.push("retest rejected VWAP");
  }
  if (volumeSupportsSell) {
    sellConfidence += 0.15;
    sellReasons.push("volume supports move");
  }

  const distanceText = `distance ${probabilityLabel(Math.abs(distance))}`;
  if (buyConfidence >= 0.45 && buyConfidence > sellConfidence) {
    return vote("Hold", Math.min(1, buyConfidence), `VWAP bullish context: ${buyReasons.join(", ")}; ${distanceText}`);
  }
  if (sellConfidence >= 0.45 && sellConfidence > buyConfidence) {
    return vote("Hold", Math.min(1, sellConfidence), `VWAP bearish context: ${sellReasons.join(", ")}; ${distanceText}`);
  }
  return vote("Hold", Math.max(0.2, Math.min(0.4, Math.max(buyConfidence, sellConfidence))), `VWAP acceptance is mixed; ${distanceText}`);
}

function trendPullback(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.sma20 === null || market.sma50 === null) {
    return vote("Hold", 0, "Waiting for trend moving averages");
  }
  const nearSma20 = Math.abs(market.latest.close - market.sma20) / market.latest.close < 0.0035;
  const trendUp = market.sma20 > market.sma50 && market.latest.close > market.vwap;
  const trendDown = market.sma20 < market.sma50 && market.latest.close < market.vwap;
  if (trendUp && nearSma20 && market.latest.close > market.openingRange.high) {
    return vote("Buy", 0.68, "Uptrend pullback is holding near 20 SMA");
  }
  if (trendDown && nearSma20 && market.latest.close < market.openingRange.low) {
    return vote("Sell", 0.68, "Downtrend pullback is rejecting near 20 SMA");
  }
  return vote("Hold", 0.2, "No clean trend pullback");
}

function vwapTrendContinuation(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.sma20 === null || market.sma50 === null) {
    return vote("Hold", 0, "Waiting for VWAP trend history");
  }
  const trendUp = market.sma20 > market.sma50 && market.latest.close > market.vwap;
  const trendDown = market.sma20 < market.sma50 && market.latest.close < market.vwap;
  if (trendUp && market.latest.close > market.openingRange.high) {
    return vote("Buy", 0.68, "VWAP, moving averages, and opening range agree upward");
  }
  if (trendDown && market.latest.close < market.openingRange.low) {
    return vote("Sell", 0.68, "VWAP, moving averages, and opening range agree downward");
  }
  return vote("Hold", 0.18, "VWAP trend continuation is not confirmed");
}

function vwapMeanReversion(market: RegimeMarketContext): RegimeRawStrategySignal {
  const distance = (market.latest.close - market.vwap) / Math.max(market.vwap, 0.01);
  const choppy = market.adx !== null ? market.adx.regime === "range" || market.adx.regime === "mixed" : Math.abs(market.vwapSlope) < 0.0002;
  if (choppy && distance < -0.003) {
    return vote("Buy", Math.min(0.78, 0.52 + Math.abs(distance) * 35), "Price is stretched below VWAP in a weak-trend tape");
  }
  if (choppy && distance > 0.003) {
    return vote("Sell", Math.min(0.78, 0.52 + Math.abs(distance) * 35), "Price is stretched above VWAP in a weak-trend tape");
  }
  return vote("Hold", 0.16, "VWAP mean-reversion setup is not active");
}

function rsiMeanReversion(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.rsi === null) {
    return vote("Hold", 0, "Waiting for RSI history");
  }
  if (market.rsi <= 30) {
    return vote("Buy", Math.min(0.9, 0.5 + (30 - market.rsi) / 35), `RSI ${market.rsi.toFixed(1)} is oversold`);
  }
  if (market.rsi >= 70) {
    return vote("Sell", Math.min(0.9, 0.5 + (market.rsi - 70) / 35), `RSI ${market.rsi.toFixed(1)} is overbought`);
  }
  return vote("Hold", 0.15, `RSI ${market.rsi.toFixed(1)} is neutral`);
}

function bollingerMeanReversion(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (!market.bands) {
    return vote("Hold", 0, "Waiting for Bollinger history");
  }
  const width = Math.max(market.bands.upper - market.bands.lower, 0.01);
  if (market.latest.close < market.bands.lower) {
    return vote("Buy", Math.min(0.9, 0.52 + ((market.bands.lower - market.latest.close) / width) * 2), "Price is stretched below lower Bollinger band");
  }
  if (market.latest.close > market.bands.upper) {
    return vote("Sell", Math.min(0.9, 0.52 + ((market.latest.close - market.bands.upper) / width) * 2), "Price is stretched above upper Bollinger band");
  }
  return vote("Hold", 0.12, "Price is inside Bollinger bands");
}

function openingRangeBreakout(market: RegimeMarketContext): RegimeRawStrategySignal {
  const volumeExpansion = market.latest.volume > market.averageVolume * 1.15;
  if (market.latest.close > market.openingRange.high && volumeExpansion) {
    return vote("Buy", 0.72, `Close broke opening high ${priceLabel(market.openingRange.high)} with volume`);
  }
  if (market.latest.close < market.openingRange.low && volumeExpansion) {
    return vote("Sell", 0.72, `Close broke opening low ${priceLabel(market.openingRange.low)} with volume`);
  }
  return vote("Hold", 0.18, "Opening range has not broken with volume");
}

function intradayBreakout(market: RegimeMarketContext): RegimeRawStrategySignal {
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

function failedBreakoutReversal(market: RegimeMarketContext): RegimeRawStrategySignal {
  const failedHigh = market.latest.high > market.priorHigh && market.latest.close < market.priorHigh;
  const failedLow = market.latest.low < market.priorLow && market.latest.close > market.priorLow;
  if (failedHigh) {
    return vote("Sell", 0.7, "Prior high breakout failed back below range");
  }
  if (failedLow) {
    return vote("Buy", 0.7, "Prior low breakdown failed back above range");
  }
  return vote("Hold", 0.14, "No failed breakout reversal");
}

function liquiditySweepReversal(market: RegimeMarketContext): RegimeRawStrategySignal {
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

function macdMomentum(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (!market.macd) {
    return vote("Hold", 0, "Waiting for MACD history");
  }
  const confidence = Math.min(0.86, 0.45 + Math.abs(market.macd.histogram) / Math.max(market.latest.close * 0.001, 0.01));
  if (market.macd.macd > market.macd.signal && market.macd.histogram > 0) {
    return vote("Buy", confidence, `MACD histogram ${market.macd.histogram.toFixed(3)} is positive`);
  }
  if (market.macd.macd < market.macd.signal && market.macd.histogram < 0) {
    return vote("Sell", confidence, `MACD histogram ${market.macd.histogram.toFixed(3)} is negative`);
  }
  return vote("Hold", 0.12, "MACD is flat or crossing");
}

function adxTrendStrength(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.adx === null || market.sma20 === null || market.sma50 === null) {
    return vote("Hold", 0, "Waiting for ADX trend history");
  }
  if (market.adx.adx < 20) {
    return vote("Hold", Math.min(0.45, market.adx.adx / 50), `ADX ${market.adx.adx.toFixed(1)} is too weak for trend`);
  }
  const confidence = Math.min(0.9, 0.45 + (market.adx.adx - 20) / 45);
  if (market.sma20 > market.sma50 && market.latest.close > market.vwap) {
    return vote("Hold", confidence, `ADX ${market.adx.adx.toFixed(1)} confirms bullish trend strength`);
  }
  if (market.sma20 < market.sma50 && market.latest.close < market.vwap) {
    return vote("Hold", confidence, `ADX ${market.adx.adx.toFixed(1)} confirms bearish trend strength`);
  }
  return vote("Hold", 0.2, `ADX ${market.adx.adx.toFixed(1)} lacks directional alignment`);
}

function volumeConfirmation(market: RegimeMarketContext): RegimeRawStrategySignal {
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

function atrVolatilityRegime(market: RegimeMarketContext): RegimeRawStrategySignal {
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

function marketStructure(market: RegimeMarketContext): RegimeRawStrategySignal {
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

function gapContinuationFade(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.priorClose === null) {
    return vote("Hold", 0, "Waiting for prior close");
  }
  const gap = (market.dayOpen - market.priorClose) / market.priorClose;
  if (Math.abs(gap) < 0.0015) {
    return vote("Hold", 0.1, "Opening gap is too small");
  }
  if (gap > 0 && market.latest.close > market.openingRange.high) {
    return vote("Buy", Math.min(0.74, 0.5 + Math.abs(gap) * 70), "Gap up is continuing above opening range");
  }
  if (gap < 0 && market.latest.close < market.openingRange.low) {
    return vote("Sell", Math.min(0.74, 0.5 + Math.abs(gap) * 70), "Gap down is continuing below opening range");
  }
  if (gap > 0 && market.latest.close < market.dayOpen) {
    return vote("Sell", Math.min(0.68, 0.48 + Math.abs(gap) * 55), "Gap up is fading below day open");
  }
  if (gap < 0 && market.latest.close > market.dayOpen) {
    return vote("Buy", Math.min(0.68, 0.48 + Math.abs(gap) * 55), "Gap down is fading above day open");
  }
  return vote("Hold", 0.14, "Gap context has no continuation or fade trigger");
}

function volatilityBreakout(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.atr.atr1m === null || market.candles.length < 21) {
    return vote("Hold", 0, "Waiting for ATR breakout history");
  }
  const volumeExpansion = market.latest.volume > market.averageVolume * 1.2;
  const breaksHigh = market.latest.close > market.priorHigh;
  const breaksLow = market.latest.close < market.priorLow;
  const atrExpands = market.atr.regime === "high" || market.atr.regime === "extreme";
  if (breaksHigh && volumeExpansion && atrExpands) {
    return vote("Buy", 0.7, "Price, volume, and ATR expanded above recent range");
  }
  if (breaksLow && volumeExpansion && atrExpands) {
    return vote("Sell", 0.7, "Price, volume, and ATR expanded below recent range");
  }
  return vote("Hold", 0.16, "Volatility breakout is not confirmed");
}

function cashAvoidFilter(market: RegimeMarketContext): RegimeRawStrategySignal {
  const blockers: string[] = [];
  if (market.spreadLiquidity.spreadTooWide) blockers.push("spread too wide");
  if (market.spreadLiquidity.volumeTooLow || market.volume.relativeVolume < 0.55) blockers.push("volume too light");
  if (!market.timeOfDay.newTradesAllowed) blockers.push("outside new-trade window");
  return blockers.length
    ? {
        role: "safety_gate",
        signal: "Hold",
        confidence: 0,
        eligible: true,
        passed: false,
        blockNewEntries: true,
        reason: `Avoid trading: ${blockers.join(", ")}`,
      }
    : {
        role: "safety_gate",
        signal: "Hold",
        confidence: 0,
        eligible: true,
        passed: true,
        blockNewEntries: false,
        reason: "Cash filter has no hard block",
      };
}

function safetyGate(passed: boolean, reason: string): RegimeRawStrategySignal {
  return {
    role: "safety_gate",
    signal: "Hold",
    confidence: 0,
    eligible: true,
    passed,
    blockNewEntries: !passed,
    reason,
  };
}

function missingCriticalDataGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  const missing: string[] = [];
  if (!market.candles.length) missing.push("regular-session candles");
  if (!market.latest) missing.push("latest quote");
  if (!Number.isFinite(market.vwap)) missing.push("VWAP");
  return missing.length ? safetyGate(false, `Missing critical data: ${missing.join(", ")}`) : safetyGate(true, "Critical Regime inputs are present");
}

function staleDataGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.contextFeeds.quoteFreshness.status === "stale") {
    return safetyGate(false, "Stale quote freshness blocks new Regime entries");
  }
  if (market.contextFeeds.quoteFreshness.status === "fresh") {
    return safetyGate(true, "Quote freshness is within Regime limits");
  }
  return safetyGate(true, "Stale-data check has no quote freshness feed; no stale condition detected in supplied snapshot");
}

function extremeVolatilityGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  return market.atr.regime === "extreme"
    ? safetyGate(false, "Extreme volatility blocks new Regime entries")
    : safetyGate(true, `ATR regime ${market.atr.regime.replaceAll("_", " ")} is within Regime limits`);
}

function excessiveSpreadGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  return market.spreadLiquidity.spreadTooWide
    ? safetyGate(false, "Excessive spread blocks new Regime entries")
    : safetyGate(true, "Spread is within Regime limits");
}

function insufficientLiquidityGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  return market.spreadLiquidity.volumeTooLow || market.volume.relativeVolume < 0.55
    ? safetyGate(false, "Insufficient liquidity blocks new Regime entries")
    : safetyGate(true, "Liquidity is within Regime limits");
}

function eventBlackoutGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.contextFeeds.scheduledEconomicEvent.state === "blackout") {
    return safetyGate(false, "Scheduled event blackout blocks new Regime entries");
  }
  if (market.contextFeeds.scheduledEconomicEvent.state === "elevated") {
    return safetyGate(false, "Elevated scheduled event risk blocks new Regime entries");
  }
  if (market.contextFeeds.scheduledEconomicEvent.state === "none") {
    return safetyGate(true, "No scheduled economic event risk in supplied Regime feed");
  }
  return safetyGate(true, "No Regime event-blackout feed is attached; gate passes until an event blackout is supplied");
}

function haltLuldGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.contextFeeds.haltLuldCircuitBreaker.haltState === "halted" || market.contextFeeds.haltLuldCircuitBreaker.haltState === "luld_pause") {
    return safetyGate(false, market.contextFeeds.haltLuldCircuitBreaker.reason ?? "Halt/LULD state blocks new Regime entries");
  }
  return market.latest.volume <= 0
    ? safetyGate(false, "Zero-volume latest candle may indicate halt/LULD; new Regime entries blocked")
    : safetyGate(true, "No halt/LULD condition detected in supplied snapshot");
}

function circuitBreakerGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  if (market.contextFeeds.haltLuldCircuitBreaker.circuitBreakerState === "active") {
    return safetyGate(false, market.contextFeeds.haltLuldCircuitBreaker.reason ?? "Circuit-breaker state blocks new Regime entries");
  }
  if (market.contextFeeds.haltLuldCircuitBreaker.circuitBreakerState === "watch") {
    return safetyGate(false, "Circuit-breaker watch blocks new Regime entries");
  }
  if (market.contextFeeds.haltLuldCircuitBreaker.circuitBreakerState === "none") {
    return safetyGate(true, "No circuit-breaker state in supplied Regime feed");
  }
  return safetyGate(true, "No circuit-breaker state is attached; gate passes until circuit-breaker data is supplied");
}

function unsupportedSessionGate(market: RegimeMarketContext): RegimeRawStrategySignal {
  return market.timeOfDay.newTradesAllowed
    ? safetyGate(true, "Session supports new Regime entries")
    : safetyGate(false, "Unsupported session blocks new Regime entries");
}
