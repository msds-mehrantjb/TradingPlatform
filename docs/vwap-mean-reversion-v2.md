# VWAP Mean Reversion V2

This document captures the initial V2 implementation of the VWAP Mean Reversion directional strategy.

## Identity

- Strategy id: `vwap_mean_reversion`
- Strategy name: `VWAP Mean Reversion`
- Version: `2.0.0`
- Family: `MEAN_REVERSION`
- Role: `DIRECTIONAL`
- Configuration version: `vwap_mean_reversion_v1`

## Required Inputs

- Completed SPY 1-minute candles through the evaluation timestamp
- Session VWAP and VWAP slope
- Distance from VWAP in ATR units
- SPY 1-minute ATR, ADX, relative volume, rolling high, and rolling low

The strategy returns an ineligible Hold with `dataReady=false` when the feature snapshot or any required measurement is missing, stale, malformed, or unavailable.

## Decision Policy

The strategy evaluates mean reversion toward VWAP only when price is overextended and the current regime is range-like or weak-trend:

- Buy setup: latest close is below VWAP by at least the configured ATR distance, the rolling VWAP-deviation z-score is sufficiently negative, and the latest candles show downside rejection or downside deceleration.
- Sell setup: latest close is above VWAP by at least the configured ATR distance, the rolling VWAP-deviation z-score is sufficiently positive, and the latest candles show upside rejection or upside deceleration.
- Hold: distance alone is insufficient, z-score is not extreme, the regime is too directional, the move has not lost momentum, or volume behavior still looks like continuation.

The strategy computes the z-score from completed session candles only. It does not use future candles or final outcomes.

## Strong-Trend Rejection

The setup is suppressed when ADX exceeds the configured entry threshold or VWAP slope indicates a strong continuation regime in the candidate direction. This prevents blind fading and repeated countertrend signals during persistent trends.

## Target and Invalidation

VWAP is the natural destination. The emitted explanation also includes a partial target reference toward VWAP, bounded by a configurable ATR fraction. Structural invalidation uses the current rolling low for Buy setups and rolling high for Sell setups when available.

## Configuration

All thresholds are stored on `VwapMeanReversionConfig`, including:

- minimum VWAP distance in ATR units,
- minimum absolute VWAP-deviation z-score,
- ADX and VWAP-slope trend limits,
- rejection wick ratio,
- deceleration ratio,
- volume behavior threshold,
- partial target fraction toward VWAP.

The config emits a deterministic `configurationHash`.
