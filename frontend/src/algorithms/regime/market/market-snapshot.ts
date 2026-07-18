import type { MarketCandle, MarketDataSnapshot } from "../../../trading/shared/market-data-types.ts";
import { buildRegimeMarketContext } from "../decision-engine.ts";
import type { RegimeMarketContext } from "../types.ts";
import { REGIME_ALGORITHM_ID } from "../versions.ts";
import { normalizeRegimeContextFeeds, type RegimeContextFeedInput, type RegimeContextFeedsSnapshot } from "./context-feeds.ts";

export type RegimeMarketSnapshotInput = MarketDataSnapshot & {
  regimeContextFeeds?: RegimeContextFeedInput;
};

export type RegimeMarketSnapshot = {
  readonly algorithmId: typeof REGIME_ALGORITHM_ID;
  readonly symbol: string;
  readonly primarySymbolCandles: readonly MarketCandle[];
  readonly oneMinuteCandles: readonly MarketCandle[];
  readonly fiveMinuteCandles: readonly MarketCandle[];
  readonly allCandles: readonly MarketCandle[];
  readonly contextFeeds: RegimeContextFeedsSnapshot;
  readonly marketContext: Readonly<RegimeMarketContext> | null;
};

export function buildRegimeMarketSnapshot(input: RegimeMarketSnapshotInput): RegimeMarketSnapshot {
  const primarySymbolCandles = freezeCandles(input.primaryCandles);
  const oneMinuteCandles = freezeCandles(input.oneMinuteCandles ?? input.primaryCandles);
  const fiveMinuteCandles = freezeCandles(input.fiveMinuteCandles ?? []);
  const allCandles = freezeCandles(input.allCandles ?? input.primaryCandles);
  const latest = primarySymbolCandles.at(-1);
  const contextFeeds = normalizeRegimeContextFeeds(input.regimeContextFeeds, latest?.timestamp ?? new Date(0).toISOString());
  const marketContext = buildRegimeMarketContext({
    symbol: input.symbol,
    primaryCandles: primarySymbolCandles.slice(),
    oneMinuteCandles: oneMinuteCandles.slice(),
    fiveMinuteCandles: fiveMinuteCandles.slice(),
    allCandles: allCandles.slice(),
    regimeContextFeeds: input.regimeContextFeeds,
  });
  return Object.freeze({
    algorithmId: REGIME_ALGORITHM_ID,
    symbol: input.symbol,
    primarySymbolCandles,
    oneMinuteCandles,
    fiveMinuteCandles,
    allCandles,
    contextFeeds,
    marketContext: marketContext ? freezeMarketContext(marketContext) : null,
  });
}

function freezeCandles(candles: readonly MarketCandle[]): readonly MarketCandle[] {
  return Object.freeze(candles.map((candle) => Object.freeze({ ...candle })));
}

function freezeMarketContext(market: RegimeMarketContext): Readonly<RegimeMarketContext> {
  return Object.freeze({
    ...market,
    candles: freezeCandles(market.candles) as MarketCandle[],
    allCandles: freezeCandles(market.allCandles) as MarketCandle[],
    oneMinuteCandles: freezeCandles(market.oneMinuteCandles) as MarketCandle[],
    fiveMinuteCandles: freezeCandles(market.fiveMinuteCandles) as MarketCandle[],
    closes: Object.freeze(market.closes.slice()) as number[],
    contextFeeds: market.contextFeeds,
  });
}
