# Opening Range Breakout V2

This document captures the initial V2 implementation of the Opening Range Breakout directional strategy.

## Identity

- Strategy id: `opening_range_breakout`
- Strategy name: `Opening Range Breakout`
- Strategy version: `2.0.0`
- Family: `BREAKOUT`
- Role: `DIRECTIONAL`
- Output model: canonical `StrategySignal`

## Opening Range

The strategy constructs the opening range from completed SPY 1-minute session candles. The initial configurable definitions are:

- 5-minute opening range
- 15-minute opening range

The strategy never emits a breakout signal before the configured opening range is complete.

## Required Inputs

Required feature fields:

- `spy1mAtr14`
- `spy1mRelativeVolume`
- `spreadDollars`
- `timeSinceMarketOpenMinutes`

The strategy also consumes completed SPY 1-minute candles from `rawInputs`.

The strategy does not read `session.directionBias`, `event.directionBias`, or equivalent proxy direction fields. Missing or unready required measurements return `HOLD`, `eligible=false`, and `dataReady=false`.

## Breakout Evidence

A breakout requires configurable combinations of:

- close beyond the opening range
- minimum dollar breakout buffer
- spread-aware buffer
- ATR-aware buffer
- relative volume
- optional range compression filter
- optional retest confirmation

Wicks beyond the range do not trigger by themselves. The completed candle close must clear the relevant range boundary and the effective buffer.

## Duplicate Prevention

Each confirmed breakout receives a deterministic setup id derived from:

- strategy id
- New York session date
- opening range definition
- direction
- range high and low
- breakout timestamp

Exact duplicate timestamp bars collapse to the same setup id. Later bars after the first confirmed breakout return `HOLD` with `opening_range.already_completed` and the original setup id, preventing repeated entries from the same breakout event.

## Tests

Coverage includes:

- wick beyond the range without confirming close returns `HOLD`
- breakout before range completion cannot produce a signal
- bullish breakout returns `BUY`
- bearish breakout returns `SELL`
- 5-minute opening range definition is supported
- later bars do not repeat the same breakout entry
- duplicate timestamp bars share one setup id
- missing required feature data returns unavailable `HOLD`
- changing only `event.directionBias` does not change the result
