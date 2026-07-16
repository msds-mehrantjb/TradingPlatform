# Liquidity Sweep Reversal V2

This document captures the initial V2 implementation of the Liquidity Sweep Reversal directional strategy.

## Identity

- Strategy id: `liquidity_sweep_reversal`
- Strategy name: `Liquidity Sweep Reversal`
- Strategy version: `2.0.0`
- Family: `REVERSAL`
- Role: `DIRECTIONAL`
- Output model: canonical `StrategySignal`

## Liquidity Levels

The strategy builds identifiable liquidity levels from:

- prior swing high and low
- prior-day high and low
- premarket high and low
- opening-range high and low
- session high and low
- rolling 20-candle high and low

The reversal direction is derived only from the sweep: upside sweep/reclaim can produce `SELL`; downside sweep/reclaim can produce `BUY`.

## Sweep Evidence

A valid sweep reversal requires:

- penetration beyond the liquidity level by a configurable spread/ATR/minimum buffer
- wick or excursion magnitude through the level
- close back through the level
- rejection candle quality
- volume and trade-count activity confirmation
- acceptable spread

Continued moves beyond the level return `HOLD` and are not classified as sweep reversals.

## Configuration

The implementation stores thresholds in `LiquiditySweepReversalConfig` and emits a deterministic `configurationHash`.

Configurable values include:

- sweep and swing lookbacks
- minimum sweep buffer
- ATR and spread buffer multipliers
- wick and rejection-body quality thresholds
- close-back buffer
- volume and trade-count activity ratios
- max spread
- derived session-level inclusion

## Tests

Coverage includes:

- continued move beyond the level returns `HOLD`
- upside sweep/reclaim returns `SELL`
- downside sweep/reclaim returns `BUY`
- missing reference levels return ineligible `HOLD`
- missing required feature data returns unavailable `HOLD`
- changing only `event.directionBias` does not change the result
