"""Meta-Strategy-owned immutable point-in-time market snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol

from pydantic import Field, field_validator, model_validator

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyContractModel, MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.indicators import (
    adx,
    atr,
    bollinger_bands,
    breadth_state,
    close_values,
    completed_candles,
    ema,
    gap_state,
    latest_at_or_before,
    liquidity_state,
    macd,
    relative_strength_context,
    relative_volume,
    rsi,
    session_phase,
    sma,
    spread_bps,
    spread_dollars,
    timestamp_value,
    vwap,
)
from backend.app.algorithms.meta_strategy.versions import (
    META_STRATEGY_ALGORITHM_VERSION,
    META_STRATEGY_CONFIGURATION_VERSION,
    META_STRATEGY_STRATEGY_CATALOG_VERSION,
)


META_STRATEGY_MARKET_SNAPSHOT_VERSION = "meta_strategy_market_snapshot_v1"


class MetaStrategySnapshotStrategy(Protocol):
    def evaluate(self, snapshot: MetaStrategyMarketSnapshot) -> Any:
        ...


class MetaStrategySnapshotCandle(MetaStrategyContractModel):
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    symbol: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    provider: str = "market_data"

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def ohlc_geometry_must_be_valid(self) -> MetaStrategySnapshotCandle:
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError("candle OHLC geometry is invalid")
        return self


class MetaStrategySnapshotQuote(MetaStrategyContractModel):
    timestamp: datetime
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    symbol: str = Field(min_length=1)
    provider: str = "market_data"

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def ask_must_not_be_below_bid(self) -> MetaStrategySnapshotQuote:
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")
        return self


class MetaStrategyMarketSnapshotRequest(MetaStrategyContractModel):
    decision_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    decision_timestamp: datetime
    one_minute_candles: tuple[MetaStrategySnapshotCandle, ...]
    five_minute_candles: tuple[MetaStrategySnapshotCandle, ...] = ()
    fifteen_minute_candles: tuple[MetaStrategySnapshotCandle, ...] = ()
    quotes: tuple[MetaStrategySnapshotQuote, ...] = ()
    qqq_candles: tuple[MetaStrategySnapshotCandle, ...] = ()
    iwm_candles: tuple[MetaStrategySnapshotCandle, ...] = ()
    breadth_components: dict[str, tuple[MetaStrategySnapshotCandle, ...]] = Field(default_factory=dict)
    prior_close: float | None = Field(default=None, gt=0)
    economic_event_state: dict[str, Any] = Field(default_factory=dict)
    finalization_lag_seconds: int = Field(default=0, ge=0)
    configuration_version: str = META_STRATEGY_CONFIGURATION_VERSION
    strategy_catalog_version: str = META_STRATEGY_STRATEGY_CATALOG_VERSION

    @field_validator("decision_timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision_timestamp must be timezone-aware")
        return value


def build_meta_strategy_market_snapshot(request: MetaStrategyMarketSnapshotRequest) -> MetaStrategyMarketSnapshot:
    one_minute = completed_candles(
        request.one_minute_candles,
        request.decision_timestamp,
        finalization_lag_seconds=request.finalization_lag_seconds,
    )
    five_minute = completed_candles(
        request.five_minute_candles,
        request.decision_timestamp,
        finalization_lag_seconds=request.finalization_lag_seconds,
    )
    fifteen_minute = completed_candles(
        request.fifteen_minute_candles,
        request.decision_timestamp,
        finalization_lag_seconds=request.finalization_lag_seconds,
    )
    if not one_minute:
        raise ValueError("at least one completed one-minute candle is required")

    anchor = one_minute[-1]
    quote = latest_at_or_before(request.quotes, request.decision_timestamp)
    qqq = _latest_completed(request.qqq_candles, request.decision_timestamp, request.finalization_lag_seconds)
    iwm = _latest_completed(request.iwm_candles, request.decision_timestamp, request.finalization_lag_seconds)
    breadth = {
        symbol: latest
        for symbol, latest in (
            (
                symbol,
                _latest_completed(candles, request.decision_timestamp, request.finalization_lag_seconds),
            )
            for symbol, candles in request.breadth_components.items()
        )
        if latest is not None
    }

    candle_sets = {
        "1m": one_minute,
        "5m": five_minute,
        "15m": fifteen_minute,
    }
    moving_averages = {
        label: _moving_average_values(candles)
        for label, candles in candle_sets.items()
    }
    atr_values = {label: atr(candles, 14) for label, candles in candle_sets.items()}
    relative_volume_values = {label: relative_volume(candles, 20) for label, candles in candle_sets.items()}
    quote_spread = {
        "dollars": spread_dollars(quote),
        "basisPoints": spread_bps(quote),
        "quoteTimestamp": timestamp_value(quote, "timestamp").isoformat() if quote is not None else None,
    }
    liquidity = liquidity_state(one_minute, quote, relative_volume_value=relative_volume_values["1m"])

    return MetaStrategyMarketSnapshot(
        algorithm_id=ALGORITHM_ID,
        algorithm_version=META_STRATEGY_ALGORITHM_VERSION,
        configuration_version=request.configuration_version,
        strategy_catalog_version=request.strategy_catalog_version,
        decision_id=request.decision_id,
        snapshot_id=request.snapshot_id,
        timestamp=request.decision_timestamp,
        symbol=request.symbol,
        last_price=anchor.close,
        bid_price=quote.bid if quote is not None else None,
        ask_price=quote.ask if quote is not None else None,
        spread_bps=quote_spread["basisPoints"],
        volume=anchor.volume,
        source_cutoff_timestamp=request.decision_timestamp,
        point_in_time=True,
        candles={
            "1m": _dump_candles(one_minute),
            "5m": _dump_candles(five_minute),
            "15m": _dump_candles(fifteen_minute),
        },
        quote=quote.model_dump(mode="json") if quote is not None else None,
        vwap=vwap(one_minute),
        moving_averages=moving_averages,
        atr=atr_values,
        adx={label: adx(candles, 14) for label, candles in candle_sets.items()},
        rsi={label: rsi(close_values(candles), 14) for label, candles in candle_sets.items()},
        macd={label: macd(close_values(candles)) for label, candles in candle_sets.items()},
        bollinger_bands={label: bollinger_bands(close_values(candles), 20, 2.0) for label, candles in candle_sets.items()},
        relative_volume=relative_volume_values,
        spread=quote_spread,
        liquidity=liquidity,
        session_phase=session_phase(request.decision_timestamp),
        gap_state=gap_state(one_minute, request.prior_close),
        qqq_iwm_context=relative_strength_context(anchor, qqq, iwm),
        breadth=breadth_state(breadth),
        economic_event_state=dict(request.economic_event_state),
        features={
            "snapshotVersion": META_STRATEGY_MARKET_SNAPSHOT_VERSION,
            "pointInTimeCutoff": request.decision_timestamp.isoformat(),
            "finalizationLagSeconds": request.finalization_lag_seconds,
        },
    )


def meta_strategy_strategy_uses_snapshot_only(strategy: MetaStrategySnapshotStrategy, snapshot: MetaStrategyMarketSnapshot) -> Any:
    return strategy.evaluate(snapshot)


def _moving_average_values(candles: tuple[MetaStrategySnapshotCandle, ...]) -> dict[str, float | None]:
    closes = close_values(candles)
    return {
        "sma20": sma(closes, 20),
        "sma50": sma(closes, 50),
        "ema9": ema(closes, 9),
        "ema20": ema(closes, 20),
        "ema50": ema(closes, 50),
    }


def _latest_completed(
    candles: Iterable[MetaStrategySnapshotCandle],
    decision_timestamp: datetime,
    finalization_lag_seconds: int,
) -> MetaStrategySnapshotCandle | None:
    completed = completed_candles(candles, decision_timestamp, finalization_lag_seconds=finalization_lag_seconds)
    return completed[-1] if completed else None


def _dump_candles(candles: tuple[MetaStrategySnapshotCandle, ...]) -> tuple[dict[str, Any], ...]:
    return tuple(candle.model_dump(mode="json") for candle in candles)


__all__ = [
    "META_STRATEGY_MARKET_SNAPSHOT_VERSION",
    "MetaStrategyMarketSnapshot",
    "MetaStrategyMarketSnapshotRequest",
    "MetaStrategySnapshotCandle",
    "MetaStrategySnapshotQuote",
    "MetaStrategySnapshotStrategy",
    "build_meta_strategy_market_snapshot",
    "meta_strategy_strategy_uses_snapshot_only",
]
