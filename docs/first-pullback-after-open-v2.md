# First Pullback After Open V2

This document captures the initial V2 implementation of the First Pullback After Open directional strategy.

## Identity

- Strategy id: `first_pullback_after_open`
- Strategy name: `First Pullback After Open`
- Strategy version: `2.0.0`
- Family: `TREND`
- Role: `DIRECTIONAL`
- Output model: canonical `StrategySignal`

## State Machine

The implementation replays the completed session candle prefix through a deterministic per-session state machine:

- `waiting_for_opening_impulse`
- `impulse_identified`
- `waiting_for_pullback`
- `waiting_for_confirmation`
- `completed`
- `invalidated`

The state is reconstructed from point-in-time raw inputs on every evaluation. This keeps live paper decisions, replay, and backtesting aligned, and avoids hidden mutable state that could diverge between runs.

## Required Inputs

The strategy uses the backend point-in-time feature snapshot and completed SPY 1-minute candles preserved in `rawInputs`.

Required feature fields:

- `sessionVwap`
- `spy1mEma9`
- `spy1mEma20`
- `spy1mAtr14`
- `spy1mRelativeVolume`
- `timeSinceMarketOpenMinutes`

The strategy does not read `session.directionBias`, `event.directionBias`, or equivalent proxy direction fields. Missing or unready required measurements return `HOLD`, `eligible=false`, and `dataReady=false`.

## Detection Rules

Opening impulse detection uses configurable evidence:

- ATR-normalized price displacement
- minimum percent displacement
- structure break beyond the impulse origin
- relative volume when enough prior session volume exists
- bounded opening impulse window

After an impulse is identified, the strategy waits for the first candle that moves against the impulse and touches a configurable pullback zone:

- impulse retracement percentage zone
- EMA9
- EMA20
- VWAP
- ATR-based zone tolerance

The setup invalidates if the pullback breaks the impulse origin beyond the configured ATR buffer.

Continuation confirmation requires:

- a candle in the impulse direction
- close beyond the prior pullback candle by a configurable ATR amount
- minimum candle body size in ATR units
- reduced pullback volume when enabled

## First Pullback Enforcement

The strategy only emits `BUY` or `SELL` on the confirmation candle of the first qualifying pullback. If evaluated later in the same session after completion, it returns `HOLD` with `first_pullback.already_completed`, so a later second pullback cannot be mislabeled as the first.

Session state resets naturally on the next New York trading date because the reducer filters raw candles by the explicit `sessionDate`.

## Tests

Coverage includes:

- no impulse returns `HOLD`
- bullish first pullback confirmation returns `BUY`
- bearish first pullback confirmation returns `SELL`
- impulse-origin break returns invalidated `HOLD`
- later second pullback returns already-completed `HOLD`
- missing required feature data returns unavailable `HOLD`
- pullback without confirmation remains boundary `HOLD`
- changing only `event.directionBias` does not change the result
- next trading day can generate a fresh first-pullback signal
