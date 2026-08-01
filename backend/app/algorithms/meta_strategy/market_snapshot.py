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
    sma,
    spread_bps,
    spread_dollars,
    timestamp_value,
    vwap,
)
from backend.app.algorithms.meta_strategy.session import meta_strategy_session_at
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
    vwap_value = vwap(one_minute)
    derived_features = _derived_market_features(
        one_minute=one_minute,
        decision_timestamp=request.decision_timestamp,
        last_price=anchor.close,
        atr_value=atr_values["1m"],
        vwap_value=vwap_value,
        bollinger_bands_value=bollinger_bands(close_values(one_minute), 20, 2.0),
    )

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
        vwap=vwap_value,
        moving_averages=moving_averages,
        atr=atr_values,
        adx={label: adx(candles, 14) for label, candles in candle_sets.items()},
        rsi={label: rsi(close_values(candles), 14) for label, candles in candle_sets.items()},
        macd={label: macd(close_values(candles)) for label, candles in candle_sets.items()},
        bollinger_bands={label: bollinger_bands(close_values(candles), 20, 2.0) for label, candles in candle_sets.items()},
        relative_volume=relative_volume_values,
        spread=quote_spread,
        liquidity=liquidity,
        session_phase=meta_strategy_session_at(request.decision_timestamp).value,
        gap_state=gap_state(one_minute, request.prior_close),
        qqq_iwm_context=relative_strength_context(anchor, qqq, iwm),
        breadth=breadth_state(breadth),
        economic_event_state=dict(request.economic_event_state),
        features={
            "snapshotVersion": META_STRATEGY_MARKET_SNAPSHOT_VERSION,
            "pointInTimeCutoff": request.decision_timestamp.isoformat(),
            "finalizationLagSeconds": request.finalization_lag_seconds,
            **derived_features,
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


def _derived_market_features(
    *,
    one_minute: tuple[MetaStrategySnapshotCandle, ...],
    decision_timestamp: datetime,
    last_price: float,
    atr_value: float | None,
    vwap_value: float | None,
    bollinger_bands_value: dict[str, float] | None,
) -> dict[str, Any]:
    atr_safe = float(atr_value or 0.0)
    opening = _opening_range(one_minute, decision_timestamp)
    prior = _previous_day_levels(one_minute, decision_timestamp)
    premarket = _premarket_levels(one_minute, decision_timestamp)
    session = _session_levels(one_minute, decision_timestamp)
    latest = one_minute[-1]
    previous = one_minute[-2] if len(one_minute) >= 2 else latest
    recent = one_minute[-20:]
    recent_high = max(candle.high for candle in recent)
    recent_low = min(candle.low for candle in recent)
    reclaim_distance = _atr_distance(last_price, previous.close, atr_safe)
    failed_side = "none"
    if previous.high >= recent_high and latest.close < previous.high:
        failed_side = "upside"
    elif previous.low <= recent_low and latest.close > previous.low:
        failed_side = "downside"
    sweep_side = "none"
    if latest.high >= recent_high and latest.close < latest.open:
        sweep_side = "buy_side"
    elif latest.low <= recent_low and latest.close > latest.open:
        sweep_side = "sell_side"
    body = max(abs(latest.close - latest.open), 0.000001)
    upper_wick = max(0.0, latest.high - max(latest.open, latest.close))
    lower_wick = max(0.0, min(latest.open, latest.close) - latest.low)
    rejection_wick_ratio = max(upper_wick, lower_wick) / body
    pullback_depth = _pullback_depth_atr(one_minute, atr_safe, vwap_value)
    vwap_relationship = "unknown"
    if vwap_value is not None:
        vwap_relationship = "above" if last_price > vwap_value else "below" if last_price < vwap_value else "at"
    vwap_slope = _vwap_slope(one_minute)
    width_percentile = _bollinger_width_percentile(bollinger_bands_value, one_minute)
    return {
        "pullbackDepthAtr": round(pullback_depth, 6),
        "failedBreakoutSide": failed_side,
        "reclaimDistanceAtr": round(abs(reclaim_distance), 6),
        "sweepSide": sweep_side,
        "rejectionWickRatio": round(rejection_wick_ratio, 6),
        "openingRangeHigh": opening["high"],
        "openingRangeLow": opening["low"],
        "openingRangeDurationMinutes": opening["durationMinutes"],
        "previousDayHigh": prior["high"],
        "previousDayLow": prior["low"],
        "premarketHigh": premarket["high"],
        "premarketLow": premarket["low"],
        "sessionHigh": session["high"],
        "sessionLow": session["low"],
        "recentSwingHigh": recent_high,
        "recentSwingLow": recent_low,
        "marketStructureState": _market_structure_state(one_minute, last_price),
        "realizedVolatilityPercentile": _realized_volatility_percentile(one_minute),
        "vwapRelationship": vwap_relationship,
        "vwapSlope": round(vwap_slope, 8),
        "bollingerWidthPercentile": round(width_percentile, 6),
        "priorCompression": width_percentile <= 0.35,
        "atrExpansion": _atr_expansion(one_minute),
        "gapTradeType": "continuation",
        "haltLuldState": "clear",
    }


def _opening_range(candles: tuple[MetaStrategySnapshotCandle, ...], decision_timestamp: datetime) -> dict[str, float | None]:
    local_date = decision_timestamp.astimezone(candles[-1].timestamp.tzinfo).date()
    opening_rows = [
        candle
        for candle in candles
        if candle.timestamp.date() == local_date and 14 <= candle.timestamp.hour <= 15
    ][:30]
    source = opening_rows or candles[: min(30, len(candles))]
    return {
        "high": max((candle.high for candle in source), default=None),
        "low": min((candle.low for candle in source), default=None),
        "durationMinutes": len(source),
    }


def _previous_day_levels(candles: tuple[MetaStrategySnapshotCandle, ...], decision_timestamp: datetime) -> dict[str, float | None]:
    local_date = decision_timestamp.astimezone(candles[-1].timestamp.tzinfo).date()
    prior_rows = [candle for candle in candles if candle.timestamp.date() < local_date]
    return {"high": max((candle.high for candle in prior_rows), default=None), "low": min((candle.low for candle in prior_rows), default=None)}


def _premarket_levels(candles: tuple[MetaStrategySnapshotCandle, ...], decision_timestamp: datetime) -> dict[str, float | None]:
    local_date = decision_timestamp.astimezone(candles[-1].timestamp.tzinfo).date()
    rows = [candle for candle in candles if candle.timestamp.date() == local_date and 8 <= candle.timestamp.hour < 14]
    return {"high": max((candle.high for candle in rows), default=None), "low": min((candle.low for candle in rows), default=None)}


def _session_levels(candles: tuple[MetaStrategySnapshotCandle, ...], decision_timestamp: datetime) -> dict[str, float | None]:
    local_date = decision_timestamp.astimezone(candles[-1].timestamp.tzinfo).date()
    rows = [candle for candle in candles if candle.timestamp.date() == local_date and candle.timestamp <= decision_timestamp]
    return {"high": max((candle.high for candle in rows), default=None), "low": min((candle.low for candle in rows), default=None)}


def _market_structure_state(candles: tuple[MetaStrategySnapshotCandle, ...], last_price: float) -> str:
    recent = candles[-20:]
    if len(recent) < 3:
        return "unknown"
    midpoint = (max(candle.high for candle in recent) + min(candle.low for candle in recent)) / 2
    if last_price > midpoint and recent[-1].close > recent[-2].close:
        return "higher_high_higher_low"
    if last_price < midpoint and recent[-1].close < recent[-2].close:
        return "lower_low_lower_high"
    return "range"


def _realized_volatility_percentile(candles: tuple[MetaStrategySnapshotCandle, ...]) -> float:
    closes = close_values(candles)
    if len(closes) < 20:
        return 0.5
    returns = [abs((closes[index] - closes[index - 1]) / closes[index - 1]) for index in range(1, len(closes)) if closes[index - 1]]
    if not returns:
        return 0.5
    current = sum(returns[-10:]) / min(10, len(returns))
    return sum(1 for value in returns if value <= current) / len(returns)


def _atr_expansion(candles: tuple[MetaStrategySnapshotCandle, ...]) -> bool:
    if len(candles) < 30:
        return False
    current = atr(candles[-14:], 5)
    prior = atr(candles[-28:-14], 5)
    return bool(current is not None and prior is not None and current > prior * 1.15)


def _atr_distance(left: float, right: float, atr_value: float) -> float:
    return (left - right) / atr_value if atr_value > 0 else 0.0


def _pullback_depth_atr(candles: tuple[MetaStrategySnapshotCandle, ...], atr_value: float, vwap_value: float | None) -> float:
    if atr_value <= 0 or vwap_value is None:
        return 0.0
    recent = candles[-10:]
    if not recent:
        return 0.0
    latest_close = recent[-1].close
    if latest_close >= vwap_value:
        return max(0.0, (max(candle.high for candle in recent) - latest_close) / atr_value)
    return max(0.0, (latest_close - min(candle.low for candle in recent)) / atr_value)


def _vwap_slope(candles: tuple[MetaStrategySnapshotCandle, ...]) -> float:
    if len(candles) < 10:
        return 0.0
    first = vwap(candles[-10:-5])
    second = vwap(candles[-5:])
    if first is None or second is None or first == 0:
        return 0.0
    return (second - first) / first


def _bollinger_width_percentile(bands: dict[str, float] | None, candles: tuple[MetaStrategySnapshotCandle, ...]) -> float:
    if not bands or not bands.get("middle"):
        return 0.0
    current = (float(bands["upper"]) - float(bands["lower"])) / float(bands["middle"])
    closes = close_values(candles)
    widths = []
    for end in range(20, len(closes) + 1):
        sample = bollinger_bands(closes[:end], 20, 2.0)
        if sample and sample.get("middle"):
            widths.append((float(sample["upper"]) - float(sample["lower"])) / float(sample["middle"]))
    if not widths:
        return 0.0
    return sum(1 for width in widths if width <= current) / len(widths)


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
