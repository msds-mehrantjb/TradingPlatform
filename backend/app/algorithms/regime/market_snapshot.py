"""Immutable Regime market-data input boundary."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.regime.contracts import RegimeCandle, RegimeMarketSnapshot
from backend.app.algorithms.regime.context_feeds import build_regime_context_feeds


def build_regime_market_snapshot(payload: dict[str, Any]) -> RegimeMarketSnapshot:
    candles = tuple(_candle(c) for c in payload.get("primaryCandles") or payload.get("candles") or [])
    if not candles:
        raise ValueError("Regime market snapshot requires at least one primary candle.")
    one_minute_source = payload.get("oneMinuteCandles")
    one_minute = tuple(_candle(c) for c in one_minute_source) if one_minute_source else candles
    five_minute = tuple(_candle(c) for c in payload.get("fiveMinuteCandles") or [])
    return RegimeMarketSnapshot(
        symbol=str(payload.get("symbol") or "SPY").upper(),
        candles=tuple(sorted(candles, key=lambda candle: candle.timestamp)),
        one_minute_candles=tuple(sorted(one_minute, key=lambda candle: candle.timestamp)),
        five_minute_candles=tuple(sorted(five_minute, key=lambda candle: candle.timestamp)),
        context_feeds=build_regime_context_feeds(payload.get("contextFeeds") or payload.get("context_feeds") or {}),
    )


def _candle(raw: dict[str, Any]) -> RegimeCandle:
    return RegimeCandle(
        timestamp=str(raw.get("timestamp") or raw.get("t") or ""),
        open=float(raw.get("open") or raw.get("o") or 0),
        high=float(raw.get("high") or raw.get("h") or raw.get("close") or 0),
        low=float(raw.get("low") or raw.get("l") or raw.get("close") or 0),
        close=float(raw.get("close") or raw.get("c") or 0),
        volume=float(raw.get("volume") or raw.get("v") or 0),
        vwap=float(raw["vwap"]) if raw.get("vwap") is not None else None,
    )
