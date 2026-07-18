import type { MarketCandle } from "../../../trading/shared/market-data-types.ts";

export type RegimeFeedAvailability = "available" | "unknown";
export type RegimeQuoteFreshnessStatus = "fresh" | "stale" | "unknown";
export type RegimeScheduledEventState = "none" | "elevated" | "blackout" | "unknown";
export type RegimeMarketState = "risk_on" | "neutral" | "risk_off" | "unknown";
export type RegimeHaltState = "none" | "halted" | "luld_pause" | "unknown";
export type RegimeCircuitBreakerState = "none" | "watch" | "active" | "unknown";

export type RegimeQuoteFeedInput = {
  bid?: number | null;
  ask?: number | null;
  timestamp?: string | null;
  receivedAt?: string | null;
  maxAgeMs?: number;
  source?: string | null;
};

export type RegimeRelativeStrengthInput = {
  symbol: "QQQ" | "IWM";
  returnPercent?: number | null;
  relativeToPrimaryPercent?: number | null;
  state?: RegimeMarketState;
  source?: string | null;
};

export type RegimeMarketBreadthInput = {
  advanceDeclineRatio?: number | null;
  percentAboveVwap?: number | null;
  state?: RegimeMarketState;
  source?: string | null;
};

export type RegimeVolatilityIndexInput = {
  symbol?: "VIX";
  value?: number | null;
  changePercent?: number | null;
  state?: RegimeMarketState;
  source?: string | null;
};

export type RegimeFuturesInput = {
  symbol?: "ES";
  changePercent?: number | null;
  trend?: RegimeMarketState;
  source?: string | null;
};

export type RegimeScheduledEventInput = {
  state?: RegimeScheduledEventState;
  eventName?: string | null;
  minutesUntil?: number | null;
  source?: string | null;
};

export type RegimeHaltCircuitInput = {
  haltState?: RegimeHaltState;
  circuitBreakerState?: RegimeCircuitBreakerState;
  reason?: string | null;
  source?: string | null;
};

export type RegimeContextFeedInput = {
  quote?: RegimeQuoteFeedInput;
  qqqRelativeStrength?: Omit<RegimeRelativeStrengthInput, "symbol">;
  iwmRelativeStrength?: Omit<RegimeRelativeStrengthInput, "symbol">;
  marketBreadth?: RegimeMarketBreadthInput;
  vix?: RegimeVolatilityIndexInput;
  esFutures?: RegimeFuturesInput;
  scheduledEconomicEvent?: RegimeScheduledEventInput;
  haltLuldCircuitBreaker?: RegimeHaltCircuitInput;
};

export type RegimeQuoteFreshnessSnapshot = {
  availability: RegimeFeedAvailability;
  status: RegimeQuoteFreshnessStatus;
  bid: number | null;
  ask: number | null;
  spreadPercent: number | null;
  quoteTimestamp: string | null;
  observedAt: string;
  ageMs: number | null;
  source: string | null;
};

export type RegimeRelativeStrengthSnapshot = {
  availability: RegimeFeedAvailability;
  symbol: "QQQ" | "IWM";
  returnPercent: number | null;
  relativeToPrimaryPercent: number | null;
  state: RegimeMarketState;
  source: string | null;
};

export type RegimeMarketBreadthSnapshot = {
  availability: RegimeFeedAvailability;
  advanceDeclineRatio: number | null;
  percentAboveVwap: number | null;
  state: RegimeMarketState;
  source: string | null;
};

export type RegimeVolatilityIndexSnapshot = {
  availability: RegimeFeedAvailability;
  symbol: "VIX";
  value: number | null;
  changePercent: number | null;
  state: RegimeMarketState;
  source: string | null;
};

export type RegimeFuturesSnapshot = {
  availability: RegimeFeedAvailability;
  symbol: "ES";
  changePercent: number | null;
  trend: RegimeMarketState;
  source: string | null;
};

export type RegimeScheduledEventSnapshot = {
  availability: RegimeFeedAvailability;
  state: RegimeScheduledEventState;
  eventName: string | null;
  minutesUntil: number | null;
  source: string | null;
};

export type RegimeHaltCircuitSnapshot = {
  availability: RegimeFeedAvailability;
  haltState: RegimeHaltState;
  circuitBreakerState: RegimeCircuitBreakerState;
  newEntriesBlocked: boolean;
  reason: string | null;
  source: string | null;
};

export type RegimeContextFeedsSnapshot = {
  quoteFreshness: RegimeQuoteFreshnessSnapshot;
  qqqRelativeStrength: RegimeRelativeStrengthSnapshot;
  iwmRelativeStrength: RegimeRelativeStrengthSnapshot;
  marketBreadth: RegimeMarketBreadthSnapshot;
  vix: RegimeVolatilityIndexSnapshot;
  esFutures: RegimeFuturesSnapshot;
  scheduledEconomicEvent: RegimeScheduledEventSnapshot;
  haltLuldCircuitBreaker: RegimeHaltCircuitSnapshot;
};

export function normalizeRegimeContextFeeds(input: RegimeContextFeedInput = {}, observedAt: string): RegimeContextFeedsSnapshot {
  return Object.freeze({
    quoteFreshness: resolveRegimeQuoteFreshness(input.quote, observedAt),
    qqqRelativeStrength: relativeStrengthSnapshot("QQQ", input.qqqRelativeStrength),
    iwmRelativeStrength: relativeStrengthSnapshot("IWM", input.iwmRelativeStrength),
    marketBreadth: marketBreadthSnapshot(input.marketBreadth),
    vix: volatilityIndexSnapshot(input.vix),
    esFutures: futuresSnapshot(input.esFutures),
    scheduledEconomicEvent: scheduledEventSnapshot(input.scheduledEconomicEvent),
    haltLuldCircuitBreaker: haltCircuitSnapshot(input.haltLuldCircuitBreaker),
  });
}

export function emptyRegimeContextFeeds(observedAt: string): RegimeContextFeedsSnapshot {
  return normalizeRegimeContextFeeds({}, observedAt);
}

export function resolveRegimeQuoteFreshness(input: RegimeQuoteFeedInput | undefined, observedAt: string): RegimeQuoteFreshnessSnapshot {
  if (!input) {
    return Object.freeze({
      availability: "unknown",
      status: "unknown",
      bid: null,
      ask: null,
      spreadPercent: null,
      quoteTimestamp: null,
      observedAt,
      ageMs: null,
      source: null,
    });
  }
  const quoteTimestamp = input.timestamp ?? input.receivedAt ?? null;
  const ageMs = quoteTimestamp === null ? null : Math.max(0, new Date(observedAt).getTime() - new Date(quoteTimestamp).getTime());
  const maxAgeMs = input.maxAgeMs ?? 60_000;
  const bid = finiteOrNull(input.bid);
  const ask = finiteOrNull(input.ask);
  const mid = bid !== null && ask !== null ? (bid + ask) / 2 : null;
  return Object.freeze({
    availability: "available",
    status: ageMs !== null && ageMs > maxAgeMs ? "stale" : "fresh",
    bid,
    ask,
    spreadPercent: mid !== null && mid > 0 ? Math.max(0, ask! - bid!) / mid : null,
    quoteTimestamp,
    observedAt,
    ageMs,
    source: input.source ?? null,
  });
}

export function regimeContextFeedsFromSharedSnapshot(_latest: MarketCandle, input?: RegimeContextFeedInput): RegimeContextFeedInput {
  return input ?? {};
}

function relativeStrengthSnapshot(symbol: "QQQ" | "IWM", input?: Omit<RegimeRelativeStrengthInput, "symbol">): RegimeRelativeStrengthSnapshot {
  return Object.freeze({
    availability: input ? "available" : "unknown",
    symbol,
    returnPercent: finiteOrNull(input?.returnPercent),
    relativeToPrimaryPercent: finiteOrNull(input?.relativeToPrimaryPercent),
    state: input?.state ?? "unknown",
    source: input?.source ?? null,
  });
}

function marketBreadthSnapshot(input?: RegimeMarketBreadthInput): RegimeMarketBreadthSnapshot {
  return Object.freeze({
    availability: input ? "available" : "unknown",
    advanceDeclineRatio: finiteOrNull(input?.advanceDeclineRatio),
    percentAboveVwap: finiteOrNull(input?.percentAboveVwap),
    state: input?.state ?? "unknown",
    source: input?.source ?? null,
  });
}

function volatilityIndexSnapshot(input?: RegimeVolatilityIndexInput): RegimeVolatilityIndexSnapshot {
  return Object.freeze({
    availability: input ? "available" : "unknown",
    symbol: "VIX",
    value: finiteOrNull(input?.value),
    changePercent: finiteOrNull(input?.changePercent),
    state: input?.state ?? "unknown",
    source: input?.source ?? null,
  });
}

function futuresSnapshot(input?: RegimeFuturesInput): RegimeFuturesSnapshot {
  return Object.freeze({
    availability: input ? "available" : "unknown",
    symbol: "ES",
    changePercent: finiteOrNull(input?.changePercent),
    trend: input?.trend ?? "unknown",
    source: input?.source ?? null,
  });
}

function scheduledEventSnapshot(input?: RegimeScheduledEventInput): RegimeScheduledEventSnapshot {
  return Object.freeze({
    availability: input ? "available" : "unknown",
    state: input?.state ?? "unknown",
    eventName: input?.eventName ?? null,
    minutesUntil: finiteOrNull(input?.minutesUntil),
    source: input?.source ?? null,
  });
}

function haltCircuitSnapshot(input?: RegimeHaltCircuitInput): RegimeHaltCircuitSnapshot {
  const haltState = input?.haltState ?? "unknown";
  const circuitBreakerState = input?.circuitBreakerState ?? "unknown";
  return Object.freeze({
    availability: input ? "available" : "unknown",
    haltState,
    circuitBreakerState,
    newEntriesBlocked: haltState === "halted" || haltState === "luld_pause" || circuitBreakerState === "active",
    reason: input?.reason ?? null,
    source: input?.source ?? null,
  });
}

function finiteOrNull(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

