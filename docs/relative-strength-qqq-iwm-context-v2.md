# Relative Strength vs QQQ/IWM Context V2

This document captures the initial V2 implementation of the Relative Strength vs QQQ/IWM context module.

## Identity

- Context id: `relative_strength_qqq_iwm`
- Context name: `Relative Strength vs QQQ/IWM`
- Version: `2.0.0`
- Family: `MARKET_CONTEXT`
- Role: `CONTEXT`
- Configuration version: `relative_strength_qqq_iwm_v1`

## Inputs

- Timestamp-aligned SPY 1-minute candles
- Timestamp-aligned QQQ 1-minute candles
- Timestamp-aligned IWM 1-minute candles
- Point-in-time feature snapshot quality for QQQ/IWM alignment

The feature snapshot now preserves completed QQQ and IWM candle history in `rawInputs` so the context module can calculate decision-time returns over multiple horizons without using future data.

## Calculation

For each configured horizon, the module calculates:

```text
relative_return = SPY return - 0.5 * QQQ return - 0.5 * IWM return
```

The initial configured horizons are 1, 5, and 15 minutes. The primary effect uses the 5-minute horizon by default.

When enough history exists, the module also calculates a rolling normalized relative-strength score from prior relative-return observations. The primary horizon takes priority; the rolling score only helps classify near-neutral cases.

## Context Contract

This module returns a `ContextSignal`, not a directional strategy vote. It always emits `Signal.HOLD` with flat direction. Its feature payload includes a `contextEffect` that downstream ensemble logic can use to:

- confirm or strengthen long candidates,
- confirm or strengthen short candidates,
- veto short candidates under strong positive conflict,
- veto long candidates under strong negative conflict,
- remain neutral.

Context alone must not create Buy or Sell.

## Data Safety

Missing, stale, malformed, or unavailable QQQ/IWM inputs produce `dataReady=false`, a flat Hold context signal, and an explanation. The module never substitutes SPY session direction, event direction, or another proxy when auxiliary-symbol data is unavailable.

## Configuration

All thresholds are stored on `RelativeStrengthQqqIwmConfig`, including:

- horizons,
- primary horizon,
- rolling score horizon and lookback,
- positive and negative thresholds,
- strong-conflict threshold,
- maximum timestamp alignment lag.

The config emits a deterministic `configurationHash`.
