# Gap Continuation / Gap Fade V2

This document captures the initial V2 implementation of the Gap Continuation / Gap Fade directional strategy.

## Identity

- Strategy id: `gap_continuation_gap_fade`
- Strategy name: `Gap Continuation / Gap Fade`
- Version: `2.0.0`
- Family: `GAP_SESSION`
- Role: `DIRECTIONAL`
- Configuration version: `gap_continuation_gap_fade_v1`

## Required Inputs

- Completed SPY 1-minute regular-session candles through the evaluation timestamp
- Prior regular-session close
- Current regular-session open
- Premarket high and low
- ATR and ADX
- Initial and current relative volume
- Market context from breadth and relative strength
- Economic-event state
- Session clock in New York time

The strategy returns an ineligible Hold with `dataReady=false` when required data is missing, stale, malformed, or unavailable.

## Gap Calculation

The strategy calculates the session gap from the prior regular-session close to the current regular-session open. It classifies:

- gap direction: up, down, or none,
- gap magnitude in percent,
- gap magnitude in ATR units,
- opening position versus the premarket range,
- initial volume behavior,
- opening structure,
- market context,
- economic-event risk.

No event or session direction field is used as a directional substitute.

## Internal Setup States

The strategy evaluates two internal states:

- `gap_continuation`: the open gaps away from the prior close, opens outside the premarket range, holds the opening structure, and continues in the gap direction with sufficient early volume.
- `gap_fade`: the open gaps away from the prior close but fails and moves back toward the prior close within the configured fade window.

The two states are mutually exclusive. If both partial patterns appear, the strategy deterministically selects one state by evidence score and disables the other. If neither state is clean, the result is Hold.

## Session Windows

Activation is limited by configurable minute windows from the New York regular-session open:

- continuation window,
- fade window.

Signals outside the configured windows return Hold. The strategy is stateless between evaluations and derives each decision from the supplied session date and completed regular-session candles, so it resets naturally each trading day.

## Configuration

All thresholds are stored on `GapContinuationFadeConfig`, including:

- minimum gap percent,
- minimum gap ATR multiple,
- opening-structure candle count,
- minimum initial relative volume,
- continuation and fade session windows,
- minimum continuation progress,
- minimum fade progress,
- maximum ADX for fade setups,
- maximum event-risk score.

The config emits a deterministic `configurationHash`.
