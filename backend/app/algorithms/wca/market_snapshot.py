"""WCA market snapshot boundary."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from backend.app.algorithms.wca.contracts import WcaCandle, WcaMarketSnapshot, WcaQuote


def build_wca_market_snapshot(
    *,
    symbol: str,
    data_timestamp: datetime,
    decision_timestamp: datetime,
    candles: Iterable[WcaCandle | Mapping[str, Any]],
    quote: WcaQuote | Mapping[str, Any] | None = None,
    source: str = "neutral_market_data",
    data_ready: bool = True,
    reason_codes: Iterable[str] = (),
) -> WcaMarketSnapshot:
    return WcaMarketSnapshot(
        symbol=symbol,
        data_timestamp=data_timestamp,
        decision_timestamp=decision_timestamp,
        candles=tuple(_candle(candle) for candle in candles),
        quote=_quote(quote),
        source=source,
        data_ready=data_ready,
        reason_codes=tuple(reason_codes),
    )


def validate_wca_market_snapshot(snapshot: WcaMarketSnapshot | Mapping[str, Any]) -> WcaMarketSnapshot:
    return WcaMarketSnapshot.model_validate(snapshot)


def _candle(candle: WcaCandle | Mapping[str, Any]) -> WcaCandle:
    return candle if isinstance(candle, WcaCandle) else WcaCandle.model_validate(candle)


def _quote(quote: WcaQuote | Mapping[str, Any] | None) -> WcaQuote | None:
    if quote is None or isinstance(quote, WcaQuote):
        return quote
    return WcaQuote.model_validate(quote)


__all__ = [
    "WcaCandle",
    "WcaMarketSnapshot",
    "WcaQuote",
    "build_wca_market_snapshot",
    "validate_wca_market_snapshot",
]
