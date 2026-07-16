# Market Breadth Momentum Context V2

This document captures the initial V2 implementation of the Market Breadth Momentum context module.

## Identity

- Context id: `market_breadth_momentum`
- Context name: `Market Breadth Momentum`
- Version: `2.0.0`
- Family: `MARKET_CONTEXT`
- Role: `CONTEXT`
- Configuration version: `market_breadth_momentum_v1`

## Breadth Sources

The module supports two source types:

- `breadth_feed`: a proper external breadth feed supplied in the point-in-time snapshot.
- `breadth_proxy`: a configurable ETF or constituent basket used when a full feed is unavailable.

Proxy results are labeled `ETF/constituent proxy basket, not true market breadth`. The frontend model includes `breadthSourceKind` and `breadthSourceLabel` so the UI can distinguish true breadth from proxy breadth.

## Proxy Basket

The default proxy basket is configurable and currently contains broad sector ETFs:

`XLK`, `XLF`, `XLY`, `XLP`, `XLV`, `XLI`, `XLE`, `XLB`, `XLU`, `XLRE`, `XLC`.

The point-in-time feature snapshot preserves completed breadth component candle histories under `rawInputs.breadthComponentCandles`.

## Metrics

The module calculates:

- percentage of components with positive return,
- percentage above component VWAP,
- percentage above component EMA20,
- median component return,
- up-volume versus down-volume ratio,
- return dispersion,
- data coverage.

It requires minimum component coverage and freshness. Empty, stale, malformed, or under-covered baskets return `dataReady=false`.

## Context Contract

The module returns a `ContextSignal`, not a directional vote. It always emits `Signal.HOLD` with flat direction. Its feature payload includes `contextEffect`, which later ensemble logic can use to confirm, weaken, or remain neutral around existing candidates.

Market breadth context alone must not create Buy or Sell.

## Configuration

All thresholds are stored on `MarketBreadthMomentumConfig`, including:

- source mode,
- proxy basket,
- return horizon,
- minimum component coverage,
- freshness limit,
- positive and negative breadth thresholds,
- minimum absolute median return.

The config emits a deterministic `configurationHash`.
