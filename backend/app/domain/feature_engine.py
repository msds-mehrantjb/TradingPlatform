from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from enum import Enum
from math import sqrt
from statistics import mean, pstdev
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.domain.exchange_calendar import ExchangeCalendarService, ExchangeSession, NEW_YORK
from backend.app.domain.indicator_service import PointInTimeIndicatorService
from backend.app.domain.models import DomainModel, _require_utc


INDICATORS = PointInTimeIndicatorService()
EXCHANGE_CALENDAR = ExchangeCalendarService()


class FeatureQuality(str, Enum):
    READY = "READY"
    MISSING = "MISSING"
    STALE = "STALE"
    INVALID = "INVALID"
    DEMO_REJECTED = "DEMO_REJECTED"


class MarketCandle(DomainModel):
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    tradeCount: int | None = Field(default=None, ge=0)
    provider: str = "market_data"
    symbol: str | None = None
    timeframe: Literal["1Min", "5Min", "15Min"] | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def prices_must_be_consistent(self) -> MarketCandle:
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError("candle OHLC geometry is invalid")
        return self


class PriorDayOHLC(DomainModel):
    sessionDate: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)


class PremarketLevels(DomainModel):
    high: float | None = Field(default=None, gt=0)
    low: float | None = Field(default=None, gt=0)
    sourceTimestamp: datetime | None = None

    @field_validator("sourceTimestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


class OpeningRangeLevels(DomainModel):
    high: float | None = Field(default=None, gt=0)
    low: float | None = Field(default=None, gt=0)
    startTimestamp: datetime | None = None
    endTimestamp: datetime | None = None

    @field_validator("startTimestamp", "endTimestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


class BidAskQuote(DomainModel):
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def ask_must_not_be_below_bid(self) -> BidAskQuote:
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")
        return self


class FeatureValue(DomainModel):
    value: Any = None
    sourceTimestamp: datetime | None = None
    quality: FeatureQuality
    explanation: str

    @field_validator("sourceTimestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


class PointInTimeFeatureRequest(DomainModel):
    evaluationTimestamp: datetime
    sessionDate: date
    spy1mCandles: list[MarketCandle]
    spy5mCandles: list[MarketCandle]
    spy15mCandles: list[MarketCandle]
    sessionVwap: float | None = Field(default=None, gt=0)
    sessionVwapTimestamp: datetime | None = None
    qqqAlignedCandles: list[MarketCandle] = Field(default_factory=list)
    iwmAlignedCandles: list[MarketCandle] = Field(default_factory=list)
    priorDayOHLC: PriorDayOHLC | None = None
    premarket: PremarketLevels | None = None
    openingRange: OpeningRangeLevels | None = None
    quote: BidAskQuote | None = None
    economicEventState: dict[str, Any] = Field(default_factory=dict)
    breadthComponents: dict[str, list[MarketCandle]] = Field(default_factory=dict)
    externalBreadthFeed: dict[str, Any] = Field(default_factory=dict)
    maxAuxiliaryAgeSeconds: int = Field(default=300, ge=0)
    finalizationLagSeconds: int = Field(default=1, ge=0)
    allowExtendedHours: bool = False
    exchangeSession: ExchangeSession | None = None
    exchangeCalendarOverrides: dict[str, Any] = Field(default_factory=dict)
    consumedTrendTriggerIds: list[str] = Field(default_factory=list)
    trendTriggerCooldownUntil: datetime | None = None
    executionStyle: Literal["live", "replay", "decision_recording", "ml", "backtest"] = "live"
    forModelTraining: bool = False

    @field_validator("evaluationTimestamp", "sessionVwapTimestamp", "trendTriggerCooldownUntil")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


class PointInTimeFeatureSnapshot(DomainModel):
    engineVersion: str
    evaluationTimestamp: datetime
    sessionDate: date
    anchorTimestamp: datetime | None
    corePriceDataReady: bool = False
    strategyRequiredFeaturesReady: bool = False
    strategyRequiredDataReady: bool = False
    auxiliaryMarketContextReady: bool = False
    auxiliaryContextReady: bool = False
    executionDataReady: bool = False
    globalSnapshotReady: bool = False
    dataReady: bool
    eligibleForTraining: bool
    reasonCodes: list[str]
    features: dict[str, FeatureValue]
    rawInputs: dict[str, Any]

    @field_validator("evaluationTimestamp", "anchorTimestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


class PointInTimeFeatureEngine:
    version = "point_in_time_feature_engine_v1"

    def compute(self, request: PointInTimeFeatureRequest) -> PointInTimeFeatureSnapshot:
        reason_codes: list[str] = []
        features: dict[str, FeatureValue] = {}
        local_eval = _new_york_datetime(request.evaluationTimestamp)
        exchange_session = request.exchangeSession or EXCHANGE_CALENDAR.session_for_date(
            request.sessionDate,
            overrides=request.exchangeCalendarOverrides,
        )
        session_matches = exchange_session.sessionDate == request.sessionDate
        if exchange_session.can_trade:
            session_matches = session_matches and exchange_session.contains_timestamp(request.evaluationTimestamp)
        else:
            session_matches = session_matches and local_eval.date() == request.sessionDate
        if not session_matches:
            reason_codes.append("session_date_mismatch")
        if not exchange_session.can_trade:
            reason_codes.append("exchange_session_closed")

        spy_1m = _completed_candles(request.spy1mCandles, request.evaluationTimestamp, request.finalizationLagSeconds)
        spy_5m = _completed_candles(request.spy5mCandles, request.evaluationTimestamp, request.finalizationLagSeconds)
        spy_15m = _completed_candles(request.spy15mCandles, request.evaluationTimestamp, request.finalizationLagSeconds)
        timeframe_quality = {
            "1m": _timeframe_quality("1m", request.spy1mCandles, spy_1m, request.evaluationTimestamp, request.finalizationLagSeconds, allow_extended_hours=request.allowExtendedHours),
            "5m": _timeframe_quality("5m", request.spy5mCandles, spy_5m, request.evaluationTimestamp, request.finalizationLagSeconds, allow_extended_hours=request.allowExtendedHours),
            "15m": _timeframe_quality("15m", request.spy15mCandles, spy_15m, request.evaluationTimestamp, request.finalizationLagSeconds, allow_extended_hours=request.allowExtendedHours),
        }
        for label, quality in timeframe_quality.items():
            for reason in quality["reason_codes"]:
                reason_codes.append(f"spy_{label}_{reason}")
        anchor = spy_1m[-1] if spy_1m else None
        anchor_timestamp = anchor.timestamp if anchor else None
        if not anchor:
            reason_codes.append("missing_spy_1m_anchor")

        if request.forModelTraining and _contains_demo_data(spy_1m + spy_5m + spy_15m):
            reason_codes.append("demo_data_rejected_for_training")

        qqq, qqq_quality = _aligned_auxiliary(request.qqqAlignedCandles, anchor_timestamp, request.maxAuxiliaryAgeSeconds, request.finalizationLagSeconds)
        iwm, iwm_quality = _aligned_auxiliary(request.iwmAlignedCandles, anchor_timestamp, request.maxAuxiliaryAgeSeconds, request.finalizationLagSeconds)
        if qqq_quality != FeatureQuality.READY:
            reason_codes.append(f"qqq_{qqq_quality.value.lower()}")
        if iwm_quality != FeatureQuality.READY:
            reason_codes.append(f"iwm_{iwm_quality.value.lower()}")

        breadth_latest: dict[str, MarketCandle] = {}
        breadth_quality = FeatureQuality.READY if request.breadthComponents else FeatureQuality.MISSING
        if not request.breadthComponents and not request.externalBreadthFeed:
            reason_codes.append("missing_breadth_components")
        for name, candles in request.breadthComponents.items():
            latest, quality = _aligned_auxiliary(candles, anchor_timestamp, request.maxAuxiliaryAgeSeconds, request.finalizationLagSeconds)
            if latest:
                breadth_latest[name] = latest
            if quality != FeatureQuality.READY:
                breadth_quality = quality
                reason_codes.append(f"breadth_{name}_{quality.value.lower()}")

        if not spy_5m:
            reason_codes.append("missing_spy_5m")
        if not spy_15m:
            reason_codes.append("missing_spy_15m")
        if request.priorDayOHLC is None:
            reason_codes.append("missing_prior_day_ohlc")

        if request.quote and anchor_timestamp:
            quote_age = abs((anchor_timestamp - request.quote.timestamp).total_seconds())
            if quote_age > request.maxAuxiliaryAgeSeconds:
                reason_codes.append("quote_stale")

        for timeframe, candles in (("1m", spy_1m), ("5m", spy_5m), ("15m", spy_15m)):
            _add_timeframe_features(features, timeframe, candles)

        latest = anchor
        latest_at = anchor_timestamp
        atr_1m = features.get("spy1mAtr14").value if features.get("spy1mAtr14") else None
        ema_20_1m = features.get("spy1mEma20").value if features.get("spy1mEma20") else None
        current_vwap = request.sessionVwap if request.sessionVwap is not None else _session_vwap(spy_1m)
        vwap_timestamp = request.sessionVwapTimestamp or latest_at
        vwap_slope = _slope([_session_vwap(spy_1m[: index + 1]) for index in range(len(spy_1m))], 5)

        features["sessionVwap"] = _feature(current_vwap, vwap_timestamp, FeatureQuality.READY if current_vwap is not None else FeatureQuality.MISSING, "Session VWAP at evaluation time.")
        features["sessionVwapSlope"] = _feature(vwap_slope, latest_at, _quality_for_value(vwap_slope), "Session VWAP slope over recent completed candles.")
        features["distanceFromVwapAtr"] = _feature(
            ((latest.close - current_vwap) / atr_1m) if latest and current_vwap and atr_1m else None,
            latest_at,
            FeatureQuality.READY if latest and current_vwap and atr_1m else FeatureQuality.MISSING,
            "Latest close distance from VWAP in ATR units.",
        )
        features["distanceFromEma20Atr"] = _feature(
            ((latest.close - ema_20_1m) / atr_1m) if latest and ema_20_1m and atr_1m else None,
            latest_at,
            FeatureQuality.READY if latest and ema_20_1m and atr_1m else FeatureQuality.MISSING,
            "Latest close distance from EMA20 in ATR units.",
        )

        if request.priorDayOHLC and latest:
            gap_pct = ((spy_1m[0].open - request.priorDayOHLC.close) / request.priorDayOHLC.close) * 100 if spy_1m else None
            features["gapPercent"] = _feature(gap_pct, latest_at, _quality_for_value(gap_pct), "Gap from prior-day close to session open, percent.")

        if request.quote:
            spread = request.quote.ask - request.quote.bid
            midpoint = (request.quote.ask + request.quote.bid) / 2
            quality = FeatureQuality.READY
            if anchor_timestamp and abs((anchor_timestamp - request.quote.timestamp).total_seconds()) > request.maxAuxiliaryAgeSeconds:
                quality = FeatureQuality.STALE
            features["spreadDollars"] = _feature(spread, request.quote.timestamp, quality, "Bid/ask spread in dollars.")
            features["spreadBasisPoints"] = _feature((spread / midpoint) * 10_000 if midpoint else None, request.quote.timestamp, quality, "Bid/ask spread in basis points.")
        else:
            features["spreadDollars"] = _feature(None, None, FeatureQuality.MISSING, "Bid/ask quote unavailable.")
            features["spreadBasisPoints"] = _feature(None, None, FeatureQuality.MISSING, "Bid/ask quote unavailable.")

        features["timeSinceMarketOpenMinutes"] = _feature(
            exchange_session.minutes_after_open(request.evaluationTimestamp) if exchange_session.can_trade else None,
            request.evaluationTimestamp,
            FeatureQuality.READY if exchange_session.can_trade else FeatureQuality.MISSING,
            "Minutes since official exchange session open.",
        )
        features["timeUntilMarketCloseMinutes"] = _feature(
            ((exchange_session.closeTimestamp - request.evaluationTimestamp).total_seconds() / 60) if exchange_session.closeTimestamp else None,
            request.evaluationTimestamp,
            FeatureQuality.READY if exchange_session.closeTimestamp else FeatureQuality.MISSING,
            "Minutes until official exchange session close.",
        )
        features["qqqClose"] = _feature(qqq.close if qqq else None, qqq.timestamp if qqq else None, qqq_quality, "Aligned QQQ close.")
        features["iwmClose"] = _feature(iwm.close if iwm else None, iwm.timestamp if iwm else None, iwm_quality, "Aligned IWM close.")
        features["relativeStrengthQqq"] = _feature(_relative_strength(latest, qqq), latest_at if qqq else None, qqq_quality, "SPY close divided by aligned QQQ close.")
        features["relativeStrengthIwm"] = _feature(_relative_strength(latest, iwm), latest_at if iwm else None, iwm_quality, "SPY close divided by aligned IWM close.")
        features["breadthProxyAverageReturn"] = _feature(_breadth_proxy_return(breadth_latest), anchor_timestamp, breadth_quality, "Average same-timestamp breadth proxy return.")
        features["economicEventState"] = _feature(request.economicEventState or {}, request.evaluationTimestamp, FeatureQuality.READY, "Economic-event state supplied by caller.")

        if request.premarket:
            features["premarketHigh"] = _feature(request.premarket.high, request.premarket.sourceTimestamp, _quality_for_value(request.premarket.high), "Premarket high.")
            features["premarketLow"] = _feature(request.premarket.low, request.premarket.sourceTimestamp, _quality_for_value(request.premarket.low), "Premarket low.")
        if request.openingRange:
            features["openingRangeHigh"] = _feature(request.openingRange.high, request.openingRange.endTimestamp, _quality_for_value(request.openingRange.high), "Opening range high.")
            features["openingRangeLow"] = _feature(request.openingRange.low, request.openingRange.endTimestamp, _quality_for_value(request.openingRange.low), "Opening range low.")

        timeframe_data_ready = all(not quality["reason_codes"] for quality in timeframe_quality.values())
        core_price_data_ready = bool(anchor and spy_5m and spy_15m and session_matches and timeframe_data_ready)
        strategy_required_data_ready = bool(anchor and session_matches)
        strategy_required_features_ready = bool(
            core_price_data_ready and _features_ready(features, _multi_timeframe_required_feature_names())
        )
        auxiliary_market_context_ready = bool(
            qqq_quality == FeatureQuality.READY
            and iwm_quality == FeatureQuality.READY
            and (breadth_quality == FeatureQuality.READY or request.externalBreadthFeed is not None)
        )
        execution_data_ready = bool(request.quote and "quote_stale" not in reason_codes)
        stale_or_missing_required = any(
            code
            for code in reason_codes
            if code.startswith(("qqq_", "iwm_", "breadth_", "missing_spy", "missing_prior", "session_date", "exchange_session", "quote_stale"))
        )
        demo_rejected = "demo_data_rejected_for_training" in reason_codes
        data_ready = bool(anchor and not stale_or_missing_required and session_matches)
        eligible_for_training = bool(data_ready and request.forModelTraining and not demo_rejected)
        if request.forModelTraining and not eligible_for_training and not demo_rejected:
            reason_codes.append("training_snapshot_not_ready")

        return PointInTimeFeatureSnapshot(
            engineVersion=self.version,
            evaluationTimestamp=request.evaluationTimestamp,
            sessionDate=request.sessionDate,
            anchorTimestamp=anchor_timestamp,
            corePriceDataReady=core_price_data_ready,
            strategyRequiredFeaturesReady=strategy_required_features_ready,
            strategyRequiredDataReady=strategy_required_data_ready,
            auxiliaryMarketContextReady=auxiliary_market_context_ready,
            auxiliaryContextReady=auxiliary_market_context_ready,
            executionDataReady=execution_data_ready,
            globalSnapshotReady=data_ready,
            dataReady=data_ready,
            eligibleForTraining=eligible_for_training,
            reasonCodes=_unique(reason_codes),
            features=features,
            rawInputs=_raw_inputs(
                request,
                spy_1m,
                spy_5m,
                spy_15m,
                qqq,
                iwm,
                breadth_latest,
                anchor_timestamp,
                timeframe_quality,
                exchange_session,
                readiness={
                    "corePriceDataReady": core_price_data_ready,
                    "strategyRequiredFeaturesReady": strategy_required_features_ready,
                    "strategyRequiredDataReady": strategy_required_data_ready,
                    "auxiliaryMarketContextReady": auxiliary_market_context_ready,
                    "auxiliaryContextReady": auxiliary_market_context_ready,
                    "executionDataReady": execution_data_ready,
                    "globalSnapshotReady": data_ready,
                    "aggregateDataReady": data_ready,
                },
            ),
        )


def compute_point_in_time_features(request: PointInTimeFeatureRequest) -> PointInTimeFeatureSnapshot:
    return PointInTimeFeatureEngine().compute(request)


def _new_york_datetime(value: datetime) -> datetime:
    return _require_utc(value).astimezone(NEW_YORK)


def _completed_candles(candles: list[MarketCandle], evaluation_timestamp: datetime | None, finalization_lag_seconds: int = 0) -> list[MarketCandle]:
    if evaluation_timestamp is None:
        return []
    cutoff = evaluation_timestamp - timedelta(seconds=finalization_lag_seconds)
    return sorted(
        (candle for candle in candles if _bar_end_timestamp(candle) <= cutoff),
        key=lambda candle: candle.timestamp,
    )


def _timeframe_quality(
    label: str,
    input_candles: list[MarketCandle],
    completed_candles: list[MarketCandle],
    evaluation_timestamp: datetime,
    finalization_lag_seconds: int,
    *,
    allow_extended_hours: bool,
) -> dict[str, Any]:
    duration = _label_duration(label)
    expected_last_bar_end = _expected_last_bar_end(evaluation_timestamp, duration, finalization_lag_seconds)
    last_bar = completed_candles[-1] if completed_candles else None
    last_bar_end = _bar_end_timestamp(last_bar) if last_bar else None
    age_seconds = (evaluation_timestamp - last_bar_end).total_seconds() if last_bar_end else None
    required_history = _required_history_count(label)
    completed_timestamps = [candle.timestamp for candle in completed_candles]
    input_timestamps = [candle.timestamp for candle in input_candles if _bar_end_timestamp(candle) <= evaluation_timestamp - timedelta(seconds=finalization_lag_seconds)]
    is_ordered = all(left < right for left, right in zip(input_timestamps, input_timestamps[1:]))
    has_duplicates = len(set(input_timestamps)) != len(input_timestamps)
    has_gaps = _has_timeframe_gaps(completed_candles, duration)
    is_boundary_aligned = all(_boundary_aligned(candle.timestamp, duration) for candle in completed_candles)
    has_required_history = len(completed_candles) >= required_history
    is_fresh = bool(last_bar_end and last_bar_end >= expected_last_bar_end)
    is_complete = bool(completed_candles and all(_bar_end_timestamp(candle) <= evaluation_timestamp - timedelta(seconds=finalization_lag_seconds) for candle in completed_candles))
    has_extended_hours = any(not _regular_hours_candle(candle) for candle in completed_candles)
    has_regular_hours = any(_regular_hours_candle(candle) for candle in completed_candles)
    reason_codes: list[str] = []
    if not is_complete:
        reason_codes.append("incomplete")
    if not is_fresh:
        reason_codes.append("stale_or_missing_recent")
    if not is_boundary_aligned:
        reason_codes.append("misaligned_boundary")
    if not has_required_history:
        reason_codes.append("insufficient_history")
    if has_gaps:
        reason_codes.append("has_gaps")
    if has_duplicates:
        reason_codes.append("has_duplicates")
    if not is_ordered:
        reason_codes.append("out_of_order")
    if has_extended_hours and not allow_extended_hours:
        reason_codes.append("extended_hours_not_allowed")
    if has_extended_hours and has_regular_hours and not allow_extended_hours:
        reason_codes.append("mixed_regular_extended_hours")
    quality_score = 1.0
    for penalty in (
        0.20 if not is_complete else 0.0,
        0.25 if not is_fresh else 0.0,
        0.25 if not is_boundary_aligned else 0.0,
        0.20 if not has_required_history else 0.0,
        0.20 if has_gaps else 0.0,
        0.25 if has_duplicates else 0.0,
        0.15 if not is_ordered else 0.0,
        0.20 if has_extended_hours and not allow_extended_hours else 0.0,
        0.10 if has_extended_hours and has_regular_hours and not allow_extended_hours else 0.0,
    ):
        quality_score -= penalty
    return {
        "timeframe": label,
        "is_complete": is_complete,
        "is_fresh": is_fresh,
        "is_boundary_aligned": is_boundary_aligned,
        "is_ordered": is_ordered,
        "has_required_history": has_required_history,
        "has_gaps": has_gaps,
        "has_duplicates": has_duplicates,
        "last_bar_start": last_bar.timestamp.isoformat().replace("+00:00", "Z") if last_bar else None,
        "last_bar_end": last_bar_end.isoformat().replace("+00:00", "Z") if last_bar_end else None,
        "expected_last_bar_end": expected_last_bar_end.isoformat().replace("+00:00", "Z"),
        "age_seconds": age_seconds,
        "quality_score": round(max(0.0, min(1.0, quality_score)), 4),
        "reason_codes": reason_codes,
    }


def _required_history_count(label: str) -> int:
    return {"1m": 30, "5m": 30, "15m": 30}.get(label, 30)


def _label_duration(label: str) -> timedelta:
    if label == "5m":
        return timedelta(minutes=5)
    if label == "15m":
        return timedelta(minutes=15)
    return timedelta(minutes=1)


def _expected_last_bar_end(evaluation_timestamp: datetime, duration: timedelta, finalization_lag_seconds: int) -> datetime:
    cutoff = evaluation_timestamp - timedelta(seconds=finalization_lag_seconds)
    midnight = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_seconds = int((cutoff - midnight).total_seconds())
    duration_seconds = int(duration.total_seconds())
    completed_intervals = elapsed_seconds // duration_seconds
    return midnight + timedelta(seconds=completed_intervals * duration_seconds)


def _has_timeframe_gaps(candles: list[MarketCandle], duration: timedelta) -> bool:
    if len(candles) < 2:
        return False
    for left, right in zip(candles, candles[1:]):
        if right.timestamp - left.timestamp != duration:
            return True
    return False


def _boundary_aligned(timestamp: datetime, duration: timedelta) -> bool:
    midnight = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    return int((timestamp - midnight).total_seconds()) % int(duration.total_seconds()) == 0


def _regular_hours_candle(candle: MarketCandle) -> bool:
    session = EXCHANGE_CALENDAR.session_for_date(_new_york_datetime(candle.timestamp).date())
    return session.contains_timestamp(candle.timestamp) and session.contains_timestamp(_bar_end_timestamp(candle) - timedelta(microseconds=1))


def _bar_end_timestamp(candle: MarketCandle) -> datetime:
    return candle.timestamp + _timeframe_duration(candle.timeframe)


def _timeframe_duration(timeframe: str | None) -> timedelta:
    if timeframe == "5Min":
        return timedelta(minutes=5)
    if timeframe == "15Min":
        return timedelta(minutes=15)
    return timedelta(minutes=1)


def _contains_demo_data(candles: list[MarketCandle]) -> bool:
    return any(candle.provider.lower() in {"demo", "fallback"} for candle in candles)


def _aligned_auxiliary(candles: list[MarketCandle], anchor_timestamp: datetime | None, max_age_seconds: int, finalization_lag_seconds: int = 0) -> tuple[MarketCandle | None, FeatureQuality]:
    if anchor_timestamp is None:
        return None, FeatureQuality.MISSING
    completed = _completed_candles(candles, anchor_timestamp, finalization_lag_seconds)
    if not completed:
        return None, FeatureQuality.MISSING
    latest = max(completed, key=lambda candle: candle.timestamp)
    age_seconds = (anchor_timestamp - latest.timestamp).total_seconds()
    if age_seconds > max_age_seconds:
        return latest, FeatureQuality.STALE
    if latest.provider.lower() in {"demo", "fallback"}:
        return latest, FeatureQuality.DEMO_REJECTED
    return latest, FeatureQuality.READY


def _add_timeframe_features(features: dict[str, FeatureValue], label: str, candles: list[MarketCandle]) -> None:
    prefix = f"spy{label}"
    closes = [candle.close for candle in candles]
    highs = [candle.high for candle in candles]
    lows = [candle.low for candle in candles]
    volumes = [candle.volume for candle in candles]
    latest_at = candles[-1].timestamp if candles else None

    ema9_series = INDICATORS.ema_series(closes, 9)
    ema20_series = INDICATORS.ema_series(closes, 20)
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    atr14 = _last(INDICATORS.atr_series(candles, 14))
    adx14 = _adx(candles, 14)
    rsi14 = _rsi(closes, 14)
    macd = _macd(closes)
    bands = _bollinger(closes, 20, 2.0)
    widths = _bollinger_width_series(closes, 20, 2.0)
    realized_vol_series = _rolling_realized_volatility(closes, 20)
    rolling_high_low = _rolling_high_low(highs, lows, 20)
    structure = _market_structure(highs, lows)

    features[f"{prefix}Ema9"] = _feature(_last(ema9_series), latest_at, _quality_for_value(_last(ema9_series)), f"{label} EMA 9.")
    features[f"{prefix}Ema20"] = _feature(_last(ema20_series), latest_at, _quality_for_value(_last(ema20_series)), f"{label} EMA 20.")
    features[f"{prefix}Ema9Slope"] = _feature(_slope(ema9_series, 3), latest_at, _quality_for_value(_slope(ema9_series, 3)), f"{label} EMA 9 slope.")
    features[f"{prefix}Ema20Slope"] = _feature(_slope(ema20_series, 3), latest_at, _quality_for_value(_slope(ema20_series, 3)), f"{label} EMA 20 slope.")
    features[f"{prefix}Sma20"] = _feature(sma20, latest_at, _quality_for_value(sma20), f"{label} SMA 20.")
    features[f"{prefix}Sma50"] = _feature(sma50, latest_at, _quality_for_value(sma50), f"{label} SMA 50.")
    features[f"{prefix}Atr14"] = _feature(atr14, latest_at, _quality_for_value(atr14), f"{label} ATR 14.")
    features[f"{prefix}Adx14"] = _feature(adx14, latest_at, _quality_for_value(adx14), f"{label} ADX 14.")
    features[f"{prefix}Rsi14"] = _feature(rsi14, latest_at, _quality_for_value(rsi14), f"{label} RSI 14.")
    features[f"{prefix}Macd"] = _feature(macd, latest_at, _quality_for_value(macd), f"{label} MACD.")
    features[f"{prefix}BollingerBands"] = _feature(bands, latest_at, _quality_for_value(bands), f"{label} Bollinger Bands.")
    features[f"{prefix}BollingerWidthPercentile"] = _feature(_percentile_rank(widths, _last(widths)), latest_at, _quality_for_value(_percentile_rank(widths, _last(widths))), f"{label} Bollinger width percentile.")
    features[f"{prefix}RealizedVolatilityPercentile"] = _feature(_percentile_rank(realized_vol_series, _last(realized_vol_series)), latest_at, _quality_for_value(_percentile_rank(realized_vol_series, _last(realized_vol_series))), f"{label} realized-volatility percentile.")
    features[f"{prefix}RelativeVolume"] = _feature(_relative_volume(volumes, 20), latest_at, _quality_for_value(_relative_volume(volumes, 20)), f"{label} relative volume.")
    features[f"{prefix}RollingHigh20"] = _feature(rolling_high_low[0], latest_at, _quality_for_value(rolling_high_low[0]), f"{label} rolling 20-candle high.")
    features[f"{prefix}RollingLow20"] = _feature(rolling_high_low[1], latest_at, _quality_for_value(rolling_high_low[1]), f"{label} rolling 20-candle low.")
    features[f"{prefix}HigherHighHigherLow"] = _feature(
        structure["higherHighHigherLow"] if structure is not None else None,
        latest_at,
        _quality_for_value(structure),
        f"{label} higher-high/higher-low structure.",
    )
    features[f"{prefix}LowerHighLowerLow"] = _feature(
        structure["lowerHighLowerLow"] if structure is not None else None,
        latest_at,
        _quality_for_value(structure),
        f"{label} lower-high/lower-low structure.",
    )


def _feature(value: Any, timestamp: datetime | None, quality: FeatureQuality, explanation: str) -> FeatureValue:
    return FeatureValue(value=value, sourceTimestamp=timestamp, quality=quality, explanation=explanation)


def _quality_for_value(value: Any) -> FeatureQuality:
    return FeatureQuality.MISSING if value is None else FeatureQuality.READY


def _features_ready(features: dict[str, FeatureValue], names: tuple[str, ...]) -> bool:
    return all(features.get(name) is not None and features[name].quality == FeatureQuality.READY.value for name in names)


def _multi_timeframe_required_feature_names() -> tuple[str, ...]:
    names: list[str] = ["sessionVwap", "sessionVwapSlope", "distanceFromVwapAtr"]
    for timeframe in ("1m", "5m", "15m"):
        prefix = f"spy{timeframe}"
        names.extend(
            [
                f"{prefix}Ema9",
                f"{prefix}Ema20",
                f"{prefix}Atr14",
                f"{prefix}Adx14",
                f"{prefix}HigherHighHigherLow",
                f"{prefix}LowerHighLowerLow",
                f"{prefix}RollingHigh20",
                f"{prefix}RollingLow20",
            ]
        )
    return tuple(names)


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return mean(values[-period:])


def _slope(values: list[float | None], lookback: int) -> float | None:
    ready = [value for value in values if value is not None]
    if len(ready) <= lookback:
        return None
    previous = ready[-lookback - 1]
    latest = ready[-1]
    if previous == 0:
        return None
    return (latest - previous) / previous


def _adx(candles: list[MarketCandle], period: int) -> float | None:
    if len(candles) <= period * 2:
        return None
    rows: list[float] = []
    for index in range(1, len(candles)):
        current = candles[index]
        previous = candles[index - 1]
        up_move = current.high - previous.high
        down_move = previous.low - current.low
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0
        true_range = max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close))
        if true_range <= 0:
            rows.append(0)
            continue
        plus_di = 100 * (plus_dm / true_range)
        minus_di = 100 * (minus_dm / true_range)
        denominator = plus_di + minus_di
        rows.append(0 if denominator == 0 else 100 * abs(plus_di - minus_di) / denominator)
    if len(rows) < period:
        return None
    return mean(rows[-period:])


def _rsi(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    gains = []
    losses = []
    for index in range(len(values) - period, len(values)):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    average_loss = mean(losses)
    if average_loss == 0:
        return 100
    rs = mean(gains) / average_loss
    return 100 - (100 / (1 + rs))


def _macd(values: list[float]) -> dict[str, float] | None:
    ema12 = [value for value in INDICATORS.ema_series(values, 12) if value is not None]
    ema26 = [value for value in INDICATORS.ema_series(values, 26) if value is not None]
    if not ema12 or not ema26:
        return None
    aligned = min(len(ema12), len(ema26))
    macd_series = [ema12[-aligned + index] - ema26[-aligned + index] for index in range(aligned)]
    signal_series = [value for value in INDICATORS.ema_series(macd_series, 9) if value is not None]
    if not signal_series:
        return None
    macd_value = macd_series[-1]
    signal_value = signal_series[-1]
    return {"macd": macd_value, "signal": signal_value, "histogram": macd_value - signal_value}


def _bollinger(values: list[float], period: int, deviations: float) -> dict[str, float] | None:
    if len(values) < period:
        return None
    sample = values[-period:]
    middle = mean(sample)
    deviation = pstdev(sample)
    return {"upper": middle + deviation * deviations, "middle": middle, "lower": middle - deviation * deviations}


def _bollinger_width_series(values: list[float], period: int, deviations: float) -> list[float | None]:
    widths: list[float | None] = []
    for index in range(len(values)):
        bands = _bollinger(values[: index + 1], period, deviations)
        if not bands or bands["middle"] == 0:
            widths.append(None)
        else:
            widths.append((bands["upper"] - bands["lower"]) / bands["middle"])
    return widths


def _rolling_realized_volatility(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = []
    for index in range(len(values)):
        sample = values[: index + 1]
        if len(sample) <= period:
            result.append(None)
            continue
        returns = [(sample[offset] - sample[offset - 1]) / sample[offset - 1] for offset in range(len(sample) - period, len(sample)) if sample[offset - 1] != 0]
        result.append(sqrt(mean([value * value for value in returns])) if returns else None)
    return result


def _percentile_rank(values: list[float | None], current: float | None) -> float | None:
    ready = [value for value in values if value is not None]
    if current is None or len(ready) < 5:
        return None
    return sum(1 for value in ready if value <= current) / len(ready)


def _relative_volume(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    baseline = mean(values[-period - 1 : -1])
    return values[-1] / baseline if baseline else None


def _rolling_high_low(highs: list[float], lows: list[float], period: int) -> tuple[float | None, float | None]:
    if len(highs) < period or len(lows) < period:
        return None, None
    return max(highs[-period:]), min(lows[-period:])


def _market_structure(highs: list[float], lows: list[float]) -> dict[str, bool] | None:
    if len(highs) < 4 or len(lows) < 4:
        return None
    return {
        "higherHighHigherLow": highs[-1] > highs[-2] > highs[-3] and lows[-1] > lows[-2] > lows[-3],
        "lowerHighLowerLow": highs[-1] < highs[-2] < highs[-3] and lows[-1] < lows[-2] < lows[-3],
    }


def _session_vwap(candles: list[MarketCandle]) -> float | None:
    return _last(INDICATORS.vwap_series(candles))


def _relative_strength(spy: MarketCandle | None, other: MarketCandle | None) -> float | None:
    if not spy or not other or other.close <= 0:
        return None
    return spy.close / other.close


def _breadth_proxy_return(candles: dict[str, MarketCandle]) -> float | None:
    returns = [(candle.close - candle.open) / candle.open for candle in candles.values() if candle.open > 0]
    return mean(returns) if returns else None


def _last(values: list[Any]) -> Any:
    return values[-1] if values else None


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _raw_inputs(
    request: PointInTimeFeatureRequest,
    spy_1m: list[MarketCandle],
    spy_5m: list[MarketCandle],
    spy_15m: list[MarketCandle],
    qqq: MarketCandle | None,
    iwm: MarketCandle | None,
    breadth: dict[str, MarketCandle],
    anchor_timestamp: datetime | None,
    timeframe_quality: dict[str, dict[str, Any]],
    exchange_session: ExchangeSession,
    readiness: dict[str, bool],
) -> dict[str, Any]:
    qqq_completed = _completed_candles(request.qqqAlignedCandles, anchor_timestamp, request.finalizationLagSeconds) if anchor_timestamp else []
    iwm_completed = _completed_candles(request.iwmAlignedCandles, anchor_timestamp, request.finalizationLagSeconds) if anchor_timestamp else []
    breadth_component_candles = {
        name: [candle.model_dump(mode="json") for candle in _completed_candles(candles, anchor_timestamp, request.finalizationLagSeconds)]
        for name, candles in request.breadthComponents.items()
    } if anchor_timestamp else {}
    return {
        "executionStyle": request.executionStyle,
        "evaluationTimestamp": request.evaluationTimestamp.isoformat().replace("+00:00", "Z"),
        "finalizationLagSeconds": request.finalizationLagSeconds,
        "allowExtendedHours": request.allowExtendedHours,
        "exchangeSession": exchange_session.model_dump(mode="json"),
        "exchangeCalendarOverrides": request.exchangeCalendarOverrides,
        "spy1mCandles": [candle.model_dump(mode="json") for candle in spy_1m],
        "spy5mCandles": [candle.model_dump(mode="json") for candle in spy_5m],
        "spy15mCandles": [candle.model_dump(mode="json") for candle in spy_15m],
        "spy1mBarWindows": [_bar_window(candle) for candle in spy_1m],
        "spy5mBarWindows": [_bar_window(candle) for candle in spy_5m],
        "spy15mBarWindows": [_bar_window(candle) for candle in spy_15m],
        "timeframeQuality": timeframe_quality,
        "readiness": readiness,
        "sessionVwap": request.sessionVwap,
        "qqqAlignedCandle": qqq.model_dump(mode="json") if qqq else None,
        "iwmAlignedCandle": iwm.model_dump(mode="json") if iwm else None,
        "qqqAlignedCandles": [candle.model_dump(mode="json") for candle in qqq_completed],
        "iwmAlignedCandles": [candle.model_dump(mode="json") for candle in iwm_completed],
        "priorDayOHLC": request.priorDayOHLC.model_dump(mode="json") if request.priorDayOHLC else None,
        "premarket": request.premarket.model_dump(mode="json") if request.premarket else None,
        "openingRange": request.openingRange.model_dump(mode="json") if request.openingRange else None,
        "quote": request.quote.model_dump(mode="json") if request.quote else None,
        "economicEventState": request.economicEventState,
        "breadthComponents": {name: candle.model_dump(mode="json") for name, candle in breadth.items()},
        "breadthComponentCandles": breadth_component_candles,
        "externalBreadthFeed": request.externalBreadthFeed,
        "maxAuxiliaryAgeSeconds": request.maxAuxiliaryAgeSeconds,
        "allowExtendedHours": request.allowExtendedHours,
        "consumedTrendTriggerIds": request.consumedTrendTriggerIds,
        "trendTriggerCooldownUntil": request.trendTriggerCooldownUntil.isoformat().replace("+00:00", "Z") if request.trendTriggerCooldownUntil else None,
        "forModelTraining": request.forModelTraining,
    }


def _bar_window(candle: MarketCandle) -> dict[str, str]:
    return {
        "barStartTimestamp": candle.timestamp.isoformat().replace("+00:00", "Z"),
        "barEndTimestamp": _bar_end_timestamp(candle).isoformat().replace("+00:00", "Z"),
    }
