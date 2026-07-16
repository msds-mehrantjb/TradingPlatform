# Multi-Timeframe Trend Alignment V2

This document captures the initial V2 implementation of the Multi-Timeframe Trend Alignment directional strategy.

## Identity

- Strategy id: `multi_timeframe_trend_alignment`
- Strategy name: `Multi-Timeframe Trend Alignment`
- Strategy version: `2.0.0`
- Family: `TREND`
- Role: `DIRECTIONAL`
- Output model: canonical `StrategySignal`

## Required inputs

The strategy consumes point-in-time feature snapshots produced by `PointInTimeFeatureEngine`.

Required measurements:

- Completed SPY 1-minute candle features
- Completed SPY 5-minute candle features
- Completed SPY 15-minute candle features
- Session VWAP and VWAP slope
- Raw completed candles for each timeframe, preserved in the feature snapshot

The strategy does not read `session.directionBias`, `event.directionBias`, or equivalent proxy direction fields. Missing, stale, malformed, or unavailable required inputs return `HOLD`, `eligible=false`, and `dataReady=false`.

## Timeframe state

Each timeframe is scored independently from the same configurable evidence set:

- EMA9 versus EMA20
- EMA9/EMA20 average slope
- Latest close versus session VWAP
- Session VWAP slope
- Higher-high/higher-low or lower-high/lower-low structure
- Recent price momentum

The weighted score is normalized to the range `[-1, 1]`. Positive scores are bullish, negative scores are bearish, and weak absolute scores are neutral.

## Decision policy

The initial policy is intentionally simple and configurable.

- `BUY`: at least two timeframes are bullish, no timeframe is strongly bearish, and the 1-minute timeframe has a usable bullish entry state.
- `SELL`: at least two timeframes are bearish, no timeframe is strongly bullish, and the 1-minute timeframe has a usable bearish entry state.
- `HOLD`: timeframes conflict, evidence is weak, or required data is unavailable.

Confidence combines:

- aligned timeframe count
- average trend strength
- slope consistency
- structure consistency
- feature data quality

Structural invalidation is included when available. Buy signals use the lowest aligned rolling low; Sell signals use the highest aligned rolling high.

## Configuration

The implementation stores thresholds and weights in `MultiTimeframeTrendAlignmentConfig` and emits a deterministic `configurationHash`.

Configurable values include:

- bullish, bearish, strong-bullish, and strong-bearish thresholds
- minimum aligned timeframe count
- entry usability threshold
- minimum slope and momentum magnitudes
- momentum lookback
- evidence weights

## Tests

Coverage includes:

- three bullish timeframes generate `BUY`
- three bearish timeframes generate `SELL`
- material timeframe conflict generates `HOLD`
- missing required data generates unavailable `HOLD`
- two aligned timeframes with a weak third timeframe can pass the boundary
- changing only `event.directionBias` does not change the result
