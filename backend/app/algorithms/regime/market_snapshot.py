"""Immutable Regime market-data input boundary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from backend.app.algorithms.regime.contracts import RegimeCandle, RegimeMarketSnapshot
from backend.app.algorithms.regime.context_feeds import build_regime_context_feeds


def build_regime_market_snapshot(payload: dict[str, Any]) -> RegimeMarketSnapshot:
    raw_primary = tuple(_raw_candle(c) for c in payload.get("primaryCandles") or payload.get("candles") or [])
    candles = tuple(_candle(c) for c in raw_primary)
    if not candles:
        raise ValueError("Regime market snapshot requires at least one primary candle.")
    one_minute_source = payload.get("oneMinuteCandles")
    raw_one_minute = tuple(_raw_candle(c) for c in one_minute_source) if one_minute_source else raw_primary
    one_minute = tuple(_candle(c) for c in raw_one_minute)
    sorted_one_minute = tuple(sorted(one_minute, key=lambda candle: candle.timestamp))
    five_minute = _derive_timeframe(sorted_one_minute, minutes=5)
    fifteen_minute = _derive_timeframe(sorted_one_minute, minutes=15)
    context_feeds = build_regime_context_feeds(payload.get("contextFeeds") or payload.get("context_feeds") or {})
    context_feeds["marketDataSource"] = _source_metadata(payload, raw_primary, raw_one_minute)
    return RegimeMarketSnapshot(
        symbol=str(payload.get("symbol") or "SPY").upper(),
        candles=tuple(sorted(candles, key=lambda candle: candle.timestamp)),
        one_minute_candles=sorted_one_minute,
        five_minute_candles=five_minute,
        context_feeds=context_feeds,
        fifteen_minute_candles=fifteen_minute,
    )


def _candle(raw: dict[str, Any]) -> RegimeCandle:
    return RegimeCandle(
        timestamp=str(raw.get("timestamp") or raw.get("t") or ""),
        open=_float(raw.get("open") or raw.get("o") or 0),
        high=_float(raw.get("high") or raw.get("h") or raw.get("close") or 0),
        low=_float(raw.get("low") or raw.get("l") or raw.get("close") or 0),
        close=_float(raw.get("close") or raw.get("c") or 0),
        volume=_float(raw.get("volume") or raw.get("v") or 0),
        vwap=_float(raw["vwap"]) if raw.get("vwap") is not None else None,
    )


def _raw_candle(raw: Any) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, dict) else {}


def _source_metadata(payload: dict[str, Any], raw_primary: tuple[dict[str, Any], ...], raw_one_minute: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    source_context = payload.get("contextFeeds") if isinstance(payload.get("contextFeeds"), dict) else payload.get("context_feeds") if isinstance(payload.get("context_feeds"), dict) else {}
    corporate_action = (
        payload.get("corporateAction")
        if isinstance(payload.get("corporateAction"), dict)
        else source_context.get("corporateAction")
        if isinstance(source_context.get("corporateAction"), dict)
        else source_context.get("corporate_action")
        if isinstance(source_context.get("corporate_action"), dict)
        else {}
    )
    return {
        "timeframe": payload.get("timeframe") or payload.get("timeFrame") or payload.get("time_frame") or "1Min",
        "observedAt": payload.get("observedAt") or payload.get("observed_at") or payload.get("publishedAt") or payload.get("published_at"),
        "dataAgeMs": payload.get("dataAgeMs") or payload.get("data_age_ms"),
        "primaryInputTimestamps": [_timestamp(row) for row in raw_primary],
        "oneMinuteInputTimestamps": [_timestamp(row) for row in raw_one_minute],
        "primaryCompletionFlags": [_completion_flag(row) for row in raw_primary],
        "oneMinuteCompletionFlags": [_completion_flag(row) for row in raw_one_minute],
        "suppliedFiveMinuteCount": len(payload.get("fiveMinuteCandles") or ()),
        "suppliedFifteenMinuteCount": len(payload.get("fifteenMinuteCandles") or payload.get("fifteen_minute_candles") or ()),
        "higherTimeframePolicy": "derived_point_in_time_from_finalized_one_minute",
        "corporateAction": corporate_action,
    }


def _derive_timeframe(one_minute: tuple[RegimeCandle, ...], *, minutes: int) -> tuple[RegimeCandle, ...]:
    rows: list[RegimeCandle] = []
    ordered = list(one_minute)
    for index in range(minutes - 1, len(ordered), minutes):
        window = ordered[index - minutes + 1 : index + 1]
        timestamps = [_parse_timestamp(candle.timestamp) for candle in window]
        if any(item is None for item in timestamps):
            continue
        if any((right - left) != timedelta(minutes=1) for left, right in zip(timestamps, timestamps[1:]) if left is not None and right is not None):
            continue
        rows.append(
            RegimeCandle(
                timestamp=window[-1].timestamp,
                open=window[0].open,
                high=max(candle.high for candle in window),
                low=min(candle.low for candle in window),
                close=window[-1].close,
                volume=sum(candle.volume for candle in window),
                vwap=_window_vwap(window),
            )
        )
    return tuple(rows)


def _window_vwap(window: list[RegimeCandle]) -> float | None:
    weighted = [(candle.vwap if candle.vwap is not None else candle.close, candle.volume) for candle in window if candle.volume > 0]
    total_volume = sum(volume for _, volume in weighted)
    if total_volume <= 0:
        return None
    return sum(price * volume for price, volume in weighted) / total_volume


def _timestamp(row: dict[str, Any]) -> str:
    return str(row.get("timestamp") or row.get("t") or "")


def _completion_flag(row: dict[str, Any]) -> bool | None:
    for key in ("completed", "isCompleted", "finalized", "finalised", "isFinalized", "isFinalised"):
        if key in row:
            return bool(row.get(key))
    return True


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
