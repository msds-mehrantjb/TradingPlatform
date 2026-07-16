# Failed Breakout Reversal V2

This document captures the initial V2 implementation of the Failed Breakout Reversal directional strategy.

## Identity

- Strategy id: `failed_breakout_reversal`
- Strategy name: `Failed Breakout Reversal`
- Strategy version: `2.0.0`
- Family: `REVERSAL`
- Role: `DIRECTIONAL`
- Output model: canonical `StrategySignal`

## Reference Levels

The strategy builds actual reference levels from the point-in-time snapshot and raw inputs:

- opening-range high and low
- prior-day high and low
- premarket high and low
- recent swing high and low
- well-defined intraday range high and low
- rolling 20-candle high and low

It does not use session direction, event direction, or any other proxy to choose reversal direction.

## Failed Breakout Logic

An upside failure can produce `SELL` when:

- price penetrates a high-side reference level by the configured spread/ATR/minimum buffer
- price cannot remain beyond the level
- the latest completed candle closes back inside the prior range
- the reversal candle confirms with bearish body quality
- spread and liquidity checks pass

A downside failure uses the inverse conditions and can produce `BUY`.

Normal breakouts that hold outside the reference range return `HOLD`.

## Configuration

The implementation stores thresholds in `FailedBreakoutReversalConfig` and emits a deterministic `configurationHash`.

Configurable values include:

- failure lookback
- swing and intraday range lookbacks
- minimum penetration buffer
- ATR and spread buffer multipliers
- close-back-inside buffer
- reversal candle body quality
- max spread and minimum volume
- optional next-candle confirmation requirement

## Tests

Coverage includes:

- normal breakout holding outside the range returns `HOLD`
- failed upside breakout returns `SELL`
- failed downside breakout returns `BUY`
- missing required feature data returns unavailable `HOLD`
- changing only `session.directionBias` does not change the result
