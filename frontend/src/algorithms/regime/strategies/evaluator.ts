import type { DirectionalStrategyResult, RegimeMarketContext, RegimeRawStrategySignal, RegimeStrategyDefinition } from "../types.ts";
import { regimeSelectionStrategies } from "./registry.ts";

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

