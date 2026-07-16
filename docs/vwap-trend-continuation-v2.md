# VWAP Trend Continuation V2

This document captures the initial V2 implementation of the VWAP Trend Continuation directional strategy.

## Identity

- Strategy id: `vwap_trend_continuation`
- Strategy name: `VWAP Trend Continuation`
- Strategy version: `2.0.0`
- Family: `TREND`
- Role: `DIRECTIONAL`
- Output model: canonical `StrategySignal`

## Required Inputs

The strategy consumes backend point-in-time features and completed SPY 1-minute candles from the feature snapshot.

Required feature fields:

- `sessionVwap`
- `sessionVwapSlope`
- `distanceFromVwapAtr`
- `spy1mEma9`
- `spy1mEma20`
- `spy1mEma9Slope`
- `spy1mEma20Slope`
- `spy1mAtr14`
- `spy1mRelativeVolume`
- `spy1mHigherHighHigherLow`
- `spy1mLowerHighLowerLow`
- `spy1mRollingHigh20`
- `spy1mRollingLow20`

The strategy does not read `session.directionBias`, `event.directionBias`, or equivalent proxy direction fields. Missing or unready required measurements return `HOLD`, `eligible=false`, and `dataReady=false`.

## Buy Logic

A Buy setup requires all of the following:

- rising VWAP slope
- close maintaining or reclaiming above VWAP
- EMA9 above EMA20
- positive EMA slope alignment
- bullish short-term structure
- recent pullback toward VWAP
- reclaim confirmation candle
- confirmation volume above the configurable baseline ratio
- entry distance from VWAP inside the configurable ATR band

## Sell Logic

Sell uses the inverse conditions:

- falling VWAP slope
- close maintaining or rejecting below VWAP
- EMA9 below EMA20
- negative EMA slope alignment
- bearish short-term structure
- recent pullback toward VWAP from below
- rejection confirmation candle
- confirmation volume
- non-excessive entry distance

## Important Guardrails

The strategy does not activate merely because price is above or below VWAP. It requires a pullback-and-confirmation sequence plus trend, structure, volume, and distance evidence.

The implementation is intentionally independent from Multi-Timeframe Trend Alignment. It does not consume or copy the multi-timeframe score; it evaluates VWAP-specific continuation evidence from the raw completed candle prefix and current feature snapshot.

## Configuration

The implementation stores thresholds in `VwapTrendContinuationConfig` and emits a deterministic `configurationHash`.

Configurable values include:

- minimum VWAP slope
- minimum EMA slope
- pullback and volume lookbacks
- pullback ATR tolerance
- reclaim/rejection ATR thresholds
- confirmation volume ratio
- maximum and extended entry distance from VWAP

## Tests

Coverage includes:

- flat VWAP plus choppy price returns `HOLD`
- valid pullback and reclaim in an uptrend returns `BUY`
- valid pullback and rejection in a downtrend returns `SELL`
- excessively extended entries are rejected
- price above VWAP without a qualifying pullback returns `HOLD`
- missing required feature data returns unavailable `HOLD`
- changing only `event.directionBias` does not change the result
