# Volatility Breakout V2

This document captures the initial V2 implementation of the Volatility Breakout directional strategy.

## Identity

- Strategy id: `volatility_breakout`
- Strategy name: `Volatility Breakout`
- Strategy version: `2.0.0`
- Family: `BREAKOUT`
- Role: `DIRECTIONAL`
- Output model: canonical `StrategySignal`

## Required Inputs

Required feature fields:

- `spy1mAtr14`
- `spy1mBollingerWidthPercentile`
- `spy1mRealizedVolatilityPercentile`
- `spy1mRelativeVolume`
- `spy1mRollingHigh20`
- `spy1mRollingLow20`
- `spreadDollars`
- `spreadBasisPoints`

The strategy also consumes completed SPY 1-minute candles from `rawInputs`.

The strategy does not read `session.directionBias`, `event.directionBias`, opening-range levels, or equivalent proxy direction fields. Missing or unready required measurements return `HOLD`, `eligible=false`, and `dataReady=false`.

## Breakout Evidence

A valid setup requires:

- prior rolling range or Bollinger-width compression
- ATR/true-range or realized-volatility expansion
- volume expansion
- close through a configurable rolling high or low
- directional candle quality
- acceptable spread and liquidity

The level break is based on rolling structure, not the opening range. If a rolling level happens to coincide with the opening range, that is incidental rather than a dependency.

## Guardrails

- Expansion without a rolling high/low break returns `HOLD`.
- A rolling level break without volatility expansion returns `HOLD`.
- Wide spread or weak liquidity returns `HOLD`.
- Weak directional candles return `HOLD`.

## Correlation Diagnostics

`backend.app.ensemble.diagnostics.strategy_signal_correlation` records pairwise signal overlap, entry overlap, identical-signal rate, and direction correlation. This is intended for later ensemble evaluation so we can verify that breakout modules add distinct evidence instead of duplicating each other.

The Step 10 tests compare `opening_range_breakout` and `volatility_breakout` on synthetic histories and assert that their signal histories are not identical.

## Tests

Coverage includes:

- expansion without level break returns `HOLD`
- level break without volatility expansion returns `HOLD`
- bullish volatility breakout returns `BUY`
- bearish volatility breakout returns `SELL`
- Opening Range Breakout and Volatility Breakout histories are not identical
- correlation diagnostics report overlap metrics
- missing required feature data returns unavailable `HOLD`
- changing only `event.directionBias` does not change the result
