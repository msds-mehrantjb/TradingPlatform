# Bollinger/ATR Reversion V2

This document captures the initial V2 implementation of the combined Bollinger/ATR Reversion directional strategy.

## Identity

- Strategy id: `bollinger_atr_reversion`
- Strategy name: `Bollinger/ATR Reversion`
- Version: `2.0.0`
- Family: `MEAN_REVERSION`
- Role: `DIRECTIONAL`
- Configuration version: `bollinger_atr_reversion_v1`

## Alias Migration

The old strategy names resolve to this single canonical module:

- `Bollinger Band Reversion`
- `ATR Overextension Reversion`
- `Bollinger/ATR Reversion`

Registry resolution deduplicates aliases, so the old Bollinger and ATR names cannot cast two separate directional votes.

## Required Inputs

- Completed SPY 1-minute candles through the evaluation timestamp
- SPY 1-minute Bollinger Bands
- Bollinger width percentile
- SPY 1-minute ATR and ADX
- ATR-adjusted distance from equilibrium
- Relative volume
- Rolling high and rolling low

The strategy returns an ineligible Hold with `dataReady=false` when the feature snapshot or any required measurement is missing, stale, malformed, or unavailable.

## Decision Policy

The strategy combines the old Bollinger-band extension and ATR-overextension concepts:

- Buy setup: price temporarily extends below the lower band, remains far enough from equilibrium in ATR units, and then re-enters the band with rejection or downside deceleration.
- Sell setup: price temporarily extends above the upper band, remains far enough from equilibrium in ATR units, and then re-enters the band with rejection or upside deceleration.
- Hold: no meaningful band extension, ATR distance is too small, there is no re-entry, momentum has not decelerated, volume looks like continuation, or the regime is unsuitable.

The middle Bollinger band is the natural target reference. Structural invalidation uses rolling low for Buy setups and rolling high for Sell setups when available.

## Trend Expansion Suppression

The strategy distinguishes temporary overextension from sustained trend expansion. It suppresses countertrend entries when ADX is above the configured threshold or when band-width percentile is elevated and price is repeatedly closing outside the outer band. This prevents band walks in strong trends from being treated as reversal setups.

## Configuration

All thresholds are stored on `BollingerAtrReversionConfig`, including:

- extension and momentum lookbacks,
- minimum band extension in ATR units,
- minimum distance from equilibrium in ATR units,
- re-entry buffer,
- ADX and band-width thresholds,
- outside-close count for band-walk detection,
- rejection wick ratio,
- deceleration ratio,
- continuation-volume limit.

The config emits a deterministic `configurationHash`.
