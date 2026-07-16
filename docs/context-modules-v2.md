# Context Modules V2

This document captures the Step 18 context modules.

## Contract

All context modules return `ContextSignal` with `Signal.HOLD` and flat direction. They are not directional voters and cannot create or replace the ensemble candidate side.

Each module includes a `contextEffect` plus bounded fields such as `maxConfidenceAdjustment` or `recommendedRiskCap` in its feature payload. Missing context is visible with `dataReady=false` and reason codes rather than fabricated agreement.

## Economic Event Context

Returns:

- event importance,
- minutes until event,
- minutes since event,
- event state,
- observable directional reaction from price behavior,
- volatility shock,
- spread shock,
- recommended risk cap.

The module may recommend reduced risk but explicitly does not replace candidate side.

## Market Structure Context

Returns:

- higher-high/higher-low state,
- lower-high/lower-low state,
- range structure,
- break of structure,
- structure quality.

Its bounded effect can confirm directional candidates or reduce breakout confidence in range structure.

## Volume Confirmation

Returns:

- relative volume,
- breakout-volume confirmation,
- pullback-volume behavior,
- volume trend,
- data quality.

Its bounded effect can confirm breakout or pullback candidates.

## VWAP Position Context

Returns:

- price above/below VWAP,
- distance from VWAP in ATR units,
- VWAP slope,
- reclaim/rejection state.

Its bounded effect can confirm candidates that already exist; it does not create Buy or Sell.
