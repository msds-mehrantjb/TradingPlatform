from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from backend.app.domain.exchange_calendar import ExchangeSession
from backend.app.domain.feature_engine import (
    BidAskQuote,
    FeatureQuality,
    MarketCandle,
    PointInTimeFeatureEngine,
    PointInTimeFeatureRequest,
    PriorDayOHLC,
)
from backend.app.domain.models import Signal
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.directional.first_pullback_after_open import (
    FirstPullbackAfterOpenConfig,
    FirstPullbackAfterOpenStrategy,
    INDICATORS,
    RelativeVolumeEvidenceMode,
    VwapPreservationMode,
    _indicator_candles,
    _regular_session_candles,
)
from backend.app.strategies.registry import resolve_strategy


SESSION_DATE = date(2026, 1, 5)
NEXT_SESSION_DATE = date(2026, 1, 6)
OPEN_UTC = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
NEXT_OPEN_UTC = datetime(2026, 1, 6, 14, 30, tzinfo=UTC)


def candle_at(open_utc: datetime, minute: int, open_price: float, high: float, low: float, close: float, volume: float) -> MarketCandle:
    return MarketCandle(
        timestamp=open_utc + timedelta(minutes=minute),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        tradeCount=1000 + minute,
        provider="fixture",
        symbol="SPY",
        timeframe="1Min",
    )


def flat_opening(open_utc: datetime = OPEN_UTC, count: int = 20) -> list[MarketCandle]:
    candles: list[MarketCandle] = []
    for minute in range(count):
        base = 100 + (0.01 if minute % 2 else 0)
        candles.append(candle_at(open_utc, minute, base, base + 0.06, base - 0.06, base + 0.01, 100000))
    return candles


def first_pullback_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    candles = flat_opening(open_utc)
    candles.extend(
        [
            candle_at(open_utc, 20, 100.00, 100.55, 99.95, 100.45, 230000),
            candle_at(open_utc, 21, 100.45, 101.05, 100.35, 100.95, 240000),
            candle_at(open_utc, 22, 100.95, 101.00, 100.40, 100.55, 110000),
            candle_at(open_utc, 23, 100.50, 101.12, 100.46, 101.05, 130000),
        ]
    )
    return candles


def second_pullback_extension(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    candles = first_pullback_sequence(open_utc)
    candles.extend(
        [
            candle_at(open_utc, 24, 101.16, 101.35, 101.10, 101.30, 150000),
            candle_at(open_utc, 25, 101.30, 101.40, 100.80, 100.95, 120000),
            candle_at(open_utc, 26, 100.95, 101.52, 100.90, 101.45, 145000),
        ]
    )
    return candles


def invalidated_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    candles = flat_opening(open_utc)
    candles.extend(
        [
            candle_at(open_utc, 20, 100.00, 100.55, 99.95, 100.45, 230000),
            candle_at(open_utc, 21, 100.45, 101.05, 100.35, 100.95, 240000),
            candle_at(open_utc, 22, 100.95, 101.00, 99.70, 99.90, 115000),
        ]
    )
    return candles


def wick_only_origin_breach_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    candles = flat_opening(open_utc)
    candles.extend(
        [
            candle_at(open_utc, 20, 100.00, 100.55, 99.95, 100.45, 230000),
            candle_at(open_utc, 21, 100.45, 101.05, 100.35, 100.95, 240000),
            candle_at(open_utc, 22, 100.95, 101.00, 99.70, 100.55, 115000),
        ]
    )
    return candles


def bearish_first_pullback_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    candles = flat_opening(open_utc)
    candles.extend(
        [
            candle_at(open_utc, 20, 100.00, 100.05, 99.45, 99.55, 230000),
            candle_at(open_utc, 21, 99.55, 99.65, 98.95, 99.05, 240000),
            candle_at(open_utc, 22, 99.05, 99.15, 98.70, 98.78, 250000),
            candle_at(open_utc, 23, 98.78, 99.25, 98.74, 99.12, 110000),
            candle_at(open_utc, 24, 99.10, 99.16, 98.55, 98.60, 130000),
        ]
    )
    return candles


def early_first_pullback_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    return [
        candle_at(open_utc, 0, 100.00, 100.42, 99.95, 100.35, 230000),
        candle_at(open_utc, 1, 100.35, 100.82, 100.30, 100.70, 240000),
        candle_at(open_utc, 2, 100.70, 101.08, 100.65, 100.95, 250000),
        candle_at(open_utc, 3, 100.95, 101.25, 100.90, 101.20, 260000),
        candle_at(open_utc, 4, 101.20, 101.22, 100.78, 100.90, 120000),
        candle_at(open_utc, 5, 100.90, 101.00, 100.65, 100.78, 110000),
        candle_at(open_utc, 6, 100.78, 101.35, 100.76, 101.30, 150000),
    ]


def first_pullback_with_pause_before_confirmation(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    return [
        candle_at(open_utc, 0, 100.00, 100.42, 99.95, 100.35, 230000),
        candle_at(open_utc, 1, 100.35, 100.82, 100.30, 100.70, 240000),
        candle_at(open_utc, 2, 100.70, 101.08, 100.65, 100.95, 250000),
        candle_at(open_utc, 3, 100.95, 101.25, 100.90, 101.20, 260000),
        candle_at(open_utc, 4, 101.20, 101.22, 100.78, 100.90, 120000),
        candle_at(open_utc, 5, 100.90, 101.00, 100.65, 100.78, 110000),
        candle_at(open_utc, 6, 100.78, 101.02, 100.76, 100.92, 90000),
        candle_at(open_utc, 7, 100.92, 101.38, 100.90, 101.32, 150000),
    ]


def vwap_close_loss_then_reclaim_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    candles = early_first_pullback_sequence(open_utc)
    candles[4] = candle_at(open_utc, 4, 101.20, 101.22, 100.62, 100.70, 120000)
    candles[5] = candle_at(open_utc, 5, 100.70, 101.00, 100.60, 100.78, 110000)
    candles[6] = candle_at(open_utc, 6, 100.78, 101.35, 100.76, 101.30, 150000)
    return candles


def extended_impulse_then_pullback_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    return [
        candle_at(open_utc, 0, 100.00, 100.40, 99.95, 100.32, 200000),
        candle_at(open_utc, 1, 100.32, 100.82, 100.28, 100.74, 210000),
        candle_at(open_utc, 2, 100.74, 101.10, 100.70, 100.98, 220000),
        candle_at(open_utc, 3, 100.98, 101.48, 100.95, 101.38, 260000),
        candle_at(open_utc, 4, 101.38, 101.88, 101.32, 101.76, 280000),
        candle_at(open_utc, 5, 101.76, 101.90, 101.10, 101.30, 120000),
        candle_at(open_utc, 6, 101.30, 101.42, 100.95, 101.12, 110000),
        candle_at(open_utc, 7, 101.12, 102.00, 101.08, 101.93, 150000),
    ]


def first_pullback_too_shallow_then_valid_looking_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    return [
        candle_at(open_utc, 0, 100.00, 100.42, 99.95, 100.35, 230000),
        candle_at(open_utc, 1, 100.35, 100.82, 100.30, 100.70, 240000),
        candle_at(open_utc, 2, 100.70, 101.08, 100.65, 100.95, 250000),
        candle_at(open_utc, 3, 100.95, 101.25, 100.90, 101.20, 260000),
        candle_at(open_utc, 4, 101.20, 101.24, 101.02, 101.06, 115000),
        candle_at(open_utc, 5, 101.06, 101.32, 101.04, 101.28, 125000),
        candle_at(open_utc, 6, 101.28, 101.34, 100.72, 100.82, 105000),
        candle_at(open_utc, 7, 100.82, 101.42, 100.78, 101.36, 140000),
    ]


def first_pullback_too_deep_then_valid_looking_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    return [
        candle_at(open_utc, 0, 100.00, 100.42, 99.95, 100.35, 230000),
        candle_at(open_utc, 1, 100.35, 100.82, 100.30, 100.70, 240000),
        candle_at(open_utc, 2, 100.70, 101.08, 100.65, 100.95, 250000),
        candle_at(open_utc, 3, 100.95, 101.25, 100.90, 101.20, 260000),
        candle_at(open_utc, 4, 101.20, 101.22, 100.30, 100.58, 115000),
        candle_at(open_utc, 5, 100.58, 101.05, 100.52, 100.98, 125000),
        candle_at(open_utc, 6, 100.98, 101.20, 100.72, 100.84, 105000),
        candle_at(open_utc, 7, 100.84, 101.45, 100.80, 101.38, 140000),
    ]


def first_pullback_high_volume_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    return [
        candle_at(open_utc, 0, 100.00, 100.42, 99.95, 100.35, 230000),
        candle_at(open_utc, 1, 100.35, 100.82, 100.30, 100.70, 240000),
        candle_at(open_utc, 2, 100.70, 101.08, 100.65, 100.95, 250000),
        candle_at(open_utc, 3, 100.95, 101.25, 100.90, 101.20, 260000),
        candle_at(open_utc, 4, 101.20, 101.22, 100.74, 100.86, 300000),
        candle_at(open_utc, 5, 100.86, 101.28, 100.82, 101.18, 125000),
    ]


def first_pullback_expires_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    return [
        candle_at(open_utc, 0, 100.00, 100.42, 99.95, 100.35, 230000),
        candle_at(open_utc, 1, 100.35, 100.82, 100.30, 100.70, 240000),
        candle_at(open_utc, 2, 100.70, 101.08, 100.65, 100.95, 250000),
        candle_at(open_utc, 3, 100.95, 101.25, 100.90, 101.20, 260000),
        candle_at(open_utc, 4, 101.20, 101.22, 100.75, 100.86, 115000),
        candle_at(open_utc, 5, 100.86, 101.05, 100.82, 101.00, 110000),
        candle_at(open_utc, 6, 101.00, 101.08, 100.90, 101.02, 108000),
        candle_at(open_utc, 7, 101.02, 101.10, 100.92, 101.04, 106000),
        candle_at(open_utc, 8, 101.04, 101.12, 100.94, 101.06, 104000),
    ]


def first_pullback_trend_reversal_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    return [
        candle_at(open_utc, 0, 100.00, 100.42, 99.95, 100.35, 230000),
        candle_at(open_utc, 1, 100.35, 100.82, 100.30, 100.70, 240000),
        candle_at(open_utc, 2, 100.70, 101.08, 100.65, 100.95, 250000),
        candle_at(open_utc, 3, 100.95, 101.25, 100.90, 101.20, 260000),
        candle_at(open_utc, 4, 101.20, 101.22, 99.70, 99.90, 115000),
    ]


def inefficient_impulse_then_pullback_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    return [
        candle_at(open_utc, 0, 100.00, 100.80, 99.95, 100.70, 230000),
        candle_at(open_utc, 1, 100.70, 100.85, 100.18, 100.25, 240000),
        candle_at(open_utc, 2, 100.25, 101.35, 100.20, 101.20, 250000),
        candle_at(open_utc, 3, 101.20, 101.25, 100.78, 100.90, 110000),
        candle_at(open_utc, 4, 100.90, 101.35, 100.84, 101.28, 130000),
    ]


def late_impulse_then_pullback_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    candles = flat_opening(open_utc, count=35)
    candles.extend(
        [
            candle_at(open_utc, 35, 100.00, 100.55, 99.95, 100.45, 230000),
            candle_at(open_utc, 36, 100.45, 101.05, 100.35, 100.95, 240000),
            candle_at(open_utc, 37, 100.95, 101.35, 100.90, 101.25, 250000),
            candle_at(open_utc, 38, 101.25, 101.30, 100.78, 100.90, 120000),
            candle_at(open_utc, 39, 100.90, 101.45, 100.86, 101.38, 150000),
        ]
    )
    return candles


def weak_body_impulse_then_pullback_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    return [
        candle_at(open_utc, 0, 100.00, 100.80, 99.90, 100.20, 230000),
        candle_at(open_utc, 1, 100.20, 101.20, 100.15, 100.45, 240000),
        candle_at(open_utc, 2, 100.45, 101.80, 100.40, 100.75, 250000),
        candle_at(open_utc, 3, 100.75, 102.20, 100.70, 101.00, 260000),
        candle_at(open_utc, 4, 101.00, 101.10, 100.60, 100.72, 120000),
        candle_at(open_utc, 5, 100.72, 101.35, 100.70, 101.20, 150000),
    ]


def high_volume_first_pullback_then_lower_volume_second(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    rows = first_pullback_high_volume_sequence(open_utc)
    rows.extend(
        [
            candle_at(open_utc, 6, 101.18, 101.45, 101.12, 101.38, 120000),
            candle_at(open_utc, 7, 101.38, 101.44, 100.78, 100.92, 90000),
            candle_at(open_utc, 8, 100.92, 101.55, 100.90, 101.48, 140000),
        ]
    )
    return rows


def failed_confirmation_then_later_confirmation_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    return [
        candle_at(open_utc, 0, 100.00, 100.42, 99.95, 100.35, 230000),
        candle_at(open_utc, 1, 100.35, 100.82, 100.30, 100.70, 240000),
        candle_at(open_utc, 2, 100.70, 101.08, 100.65, 100.95, 250000),
        candle_at(open_utc, 3, 100.95, 101.25, 100.90, 101.20, 260000),
        candle_at(open_utc, 4, 101.20, 101.22, 100.76, 100.88, 115000),
        candle_at(open_utc, 5, 100.88, 101.00, 100.66, 100.78, 110000),
        candle_at(open_utc, 6, 100.78, 101.10, 100.76, 101.05, 100000),
        candle_at(open_utc, 7, 100.98, 101.42, 100.96, 101.34, 150000),
    ]


def multi_close_vwap_loss_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    candles = early_first_pullback_sequence(open_utc)
    candles[4] = candle_at(open_utc, 4, 101.20, 101.22, 100.55, 100.62, 120000)
    candles[5] = candle_at(open_utc, 5, 100.62, 100.88, 100.45, 100.58, 110000)
    candles[6] = candle_at(open_utc, 6, 100.58, 101.35, 100.56, 101.30, 150000)
    return candles


def ema_structure_reversal_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    rows = [
        candle_at(open_utc, 0, 100.00, 100.42, 99.95, 100.35, 230000),
        candle_at(open_utc, 1, 100.35, 100.82, 100.30, 100.70, 240000),
        candle_at(open_utc, 2, 100.70, 101.08, 100.65, 100.95, 250000),
        candle_at(open_utc, 3, 100.95, 101.25, 100.90, 101.20, 260000),
        candle_at(open_utc, 4, 101.20, 101.22, 100.50, 100.72, 130000),
        candle_at(open_utc, 5, 100.72, 100.80, 100.18, 100.30, 125000),
        candle_at(open_utc, 6, 100.30, 100.70, 100.10, 100.58, 120000),
    ]
    return rows


def no_pivot_break_confirmation_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    rows = early_first_pullback_sequence(open_utc)
    rows[6] = candle_at(open_utc, 6, 100.78, 101.12, 100.76, 101.10, 150000)
    return rows


def large_upper_wick_confirmation_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    rows = early_first_pullback_sequence(open_utc)
    rows[6] = candle_at(open_utc, 6, 100.78, 101.78, 100.76, 101.30, 150000)
    return rows


def weak_volume_confirmation_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    rows = early_first_pullback_sequence(open_utc)
    rows[6] = candle_at(open_utc, 6, 100.78, 101.35, 100.76, 101.30, 90000)
    return rows


def delayed_pullback_after_impulse_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    return [
        candle_at(open_utc, 0, 100.00, 100.42, 99.95, 100.35, 230000),
        candle_at(open_utc, 1, 100.35, 100.82, 100.30, 100.70, 240000),
        candle_at(open_utc, 2, 100.70, 101.20, 100.65, 101.08, 250000),
        candle_at(open_utc, 3, 101.08, 101.18, 101.00, 101.08, 110000),
        candle_at(open_utc, 4, 101.08, 101.18, 101.00, 101.08, 110000),
        candle_at(open_utc, 5, 101.08, 101.18, 101.00, 101.08, 110000),
        candle_at(open_utc, 6, 101.08, 101.18, 101.00, 101.08, 110000),
        candle_at(open_utc, 7, 101.08, 101.18, 101.00, 101.08, 110000),
        candle_at(open_utc, 8, 101.08, 101.12, 100.72, 100.82, 110000),
        candle_at(open_utc, 9, 100.82, 101.42, 100.80, 101.34, 150000),
    ]


def prior_session_warmup(open_utc: datetime = OPEN_UTC, count: int = 80) -> list[MarketCandle]:
    prior_open = open_utc - timedelta(days=3)
    rows: list[MarketCandle] = []
    for minute in range(count):
        base = 99.6 + (minute * 0.005)
        rows.append(candle_at(prior_open, minute, base, base + 0.08, base - 0.08, base + 0.01, 90000 + (minute % 7) * 1000))
    return rows


def bearish_prior_session_warmup(open_utc: datetime = OPEN_UTC, count: int = 80) -> list[MarketCandle]:
    prior_open = open_utc - timedelta(days=3)
    rows: list[MarketCandle] = []
    for minute in range(count):
        base = 102.0 - (minute * 0.018)
        rows.append(candle_at(prior_open, minute, base + 0.01, base + 0.08, base - 0.08, base - 0.01, 90000 + (minute % 7) * 1000))
    return rows


def auxiliary_candles(open_utc: datetime, symbol: str, drift: float = 0.01, count: int = 80) -> list[MarketCandle]:
    rows = []
    for minute in range(count):
        base = 100 + (minute * drift)
        rows.append(candle_at(open_utc, minute, base, base + 0.08, base - 0.08, base + drift, 100000))
        rows[-1] = rows[-1].model_copy(update={"symbol": symbol})
    return rows


def timeframe_history(*, end: datetime, step_minutes: int, timeframe: str, count: int = 80) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    start = end - timedelta(minutes=step_minutes * (count - 1))
    for index in range(count):
        timestamp = start + timedelta(minutes=step_minutes * index)
        base = 100 + index * 0.01
        rows.append(
            MarketCandle(
                timestamp=timestamp,
                open=base,
                high=base + 0.08,
                low=base - 0.08,
                close=base + 0.01,
                volume=500000,
                tradeCount=5000 + index,
                provider="fixture",
                symbol="SPY",
                timeframe=timeframe,  # type: ignore[arg-type]
            )
        )
    return rows


def request_for(candles: list[MarketCandle], session_date: date = SESSION_DATE) -> PointInTimeFeatureRequest:
    evaluation = candles[-1].timestamp + timedelta(minutes=1, seconds=2)
    open_utc = candles[0].timestamp
    return PointInTimeFeatureRequest(
        evaluationTimestamp=evaluation,
        sessionDate=session_date,
        spy1mCandles=candles,
        spy5mCandles=timeframe_history(end=evaluation, step_minutes=5, timeframe="5Min"),
        spy15mCandles=timeframe_history(end=evaluation, step_minutes=15, timeframe="15Min"),
        sessionVwap=sum(c.close * c.volume for c in candles) / sum(c.volume for c in candles),
        sessionVwapTimestamp=evaluation,
        qqqAlignedCandles=auxiliary_candles(open_utc, "QQQ", count=90),
        iwmAlignedCandles=auxiliary_candles(open_utc, "IWM", count=90),
        priorDayOHLC=PriorDayOHLC(sessionDate=session_date - timedelta(days=3), open=99, high=101, low=98, close=100),
        quote=BidAskQuote(bid=candles[-1].close - 0.01, ask=candles[-1].close + 0.01, timestamp=evaluation),
        breadthComponents={"XLK": auxiliary_candles(open_utc, "XLK", count=90)},
    )


def request_for_early_open(candles: list[MarketCandle], session_date: date = SESSION_DATE) -> PointInTimeFeatureRequest:
    evaluation = candles[-1].timestamp + timedelta(minutes=1, seconds=2)
    open_utc = OPEN_UTC if session_date == SESSION_DATE else NEXT_OPEN_UTC
    regular_session = [candle for candle in candles if candle.timestamp.date() == open_utc.date()]
    session_vwap = sum(c.close * c.volume for c in regular_session) / sum(c.volume for c in regular_session)
    return PointInTimeFeatureRequest(
        evaluationTimestamp=evaluation,
        sessionDate=session_date,
        spy1mCandles=[*prior_session_warmup(open_utc), *candles],
        spy5mCandles=timeframe_history(end=evaluation, step_minutes=5, timeframe="5Min"),
        spy15mCandles=timeframe_history(end=evaluation, step_minutes=15, timeframe="15Min"),
        sessionVwap=session_vwap,
        sessionVwapTimestamp=evaluation,
        qqqAlignedCandles=auxiliary_candles(open_utc, "QQQ", count=90),
        iwmAlignedCandles=auxiliary_candles(open_utc, "IWM", count=90),
        priorDayOHLC=PriorDayOHLC(sessionDate=session_date - timedelta(days=3), open=99, high=101, low=98, close=100),
        quote=BidAskQuote(bid=candles[-1].close - 0.01, ask=candles[-1].close + 0.01, timestamp=evaluation),
        breadthComponents={"XLK": auxiliary_candles(open_utc, "XLK", count=90)},
    )


def evaluate(candles: list[MarketCandle], session_date: date = SESSION_DATE):
    snapshot = PointInTimeFeatureEngine().compute(request_for(candles, session_date))
    return evaluate_snapshot(snapshot)


def evaluate_snapshot(snapshot):
    strategy = FirstPullbackAfterOpenStrategy()
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("first_pullback_after_open"),
        featureSnapshot=snapshot,
        configurationHash=strategy.config.configurationHash,
    )
    return strategy.evaluate(context)


def evaluate_snapshot_with_config(snapshot, config: FirstPullbackAfterOpenConfig):
    strategy = FirstPullbackAfterOpenStrategy(config)
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("first_pullback_after_open"),
        featureSnapshot=snapshot,
        configurationHash=strategy.config.configurationHash,
    )
    return strategy.evaluate(context)


class FirstPullbackAfterOpenTest(unittest.TestCase):
    def setUp(self) -> None:
        FirstPullbackAfterOpenStrategy.reset_state_store()

    def test_no_impulse_means_hold(self) -> None:
        result = evaluate(flat_opening(count=50))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.no_opening_impulse", result.reasonCodes)

    def test_first_pullback_confirmation_generates_buy(self) -> None:
        result = evaluate(first_pullback_sequence())

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("first_pullback.completed", result.reasonCodes)
        self.assertIsNotNone(result.structuralInvalidationPrice)
        self.assertIn("timeSinceMarketOpenMinutes", result.features)
        confidence = result.features["firstPullbackConfidence"]
        self.assertEqual(confidence["weights"]["impulseQuality"], 0.20)
        self.assertEqual(confidence["weights"]["establishedTrendQuality"], 0.15)
        self.assertEqual(confidence["weights"]["pullbackDepthAndStructure"], 0.20)
        self.assertEqual(confidence["weights"]["pullbackVolumeQuality"], 0.15)
        self.assertEqual(confidence["weights"]["vwapAnchorPreservation"], 0.10)
        self.assertEqual(confidence["weights"]["confirmationQuality"], 0.15)
        self.assertEqual(confidence["weights"]["timingAndDataQuality"], 0.05)
        self.assertAlmostEqual(confidence["finalConfidence"], result.confidence, places=4)
        self.assertTrue(confidence["actionable"])
        exchange_session = result.features["firstPullbackExchangeSession"]
        self.assertEqual(exchange_session["sessionId"], "XNYS:2026-01-05")
        self.assertEqual(exchange_session["openTimestamp"], "2026-01-05T14:30:00Z")
        self.assertEqual(exchange_session["timestampConvention"], "bar_start_utc")

    def test_official_exchange_session_closure_returns_hold(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(first_pullback_sequence()))
        closed_session = ExchangeSession(
            exchange="XNYS",
            sessionId="XNYS:2026-01-05:halt",
            sessionDate=SESSION_DATE,
            openTimestamp=None,
            closeTimestamp=None,
            isTradingSession=False,
            isUnexpectedClosure=True,
            closureReason="unexpected_closure",
            provider="provider-calendar",
        )
        raw_inputs = {**snapshot.rawInputs, "exchangeSession": closed_session.model_dump(mode="json")}
        result = evaluate_snapshot(snapshot.model_copy(update={"rawInputs": raw_inputs}))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.exchange_session_closed", result.reasonCodes)
        self.assertEqual(result.features["firstPullbackExchangeSession"]["sessionId"], "XNYS:2026-01-05:halt")

    def test_completed_setup_below_configured_confidence_threshold_is_not_eligible(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(first_pullback_sequence()))
        config = FirstPullbackAfterOpenConfig(minimumActionableConfidence=0.95)
        result = evaluate_snapshot_with_config(snapshot, config)

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.confidence_below_actionable_minimum", result.reasonCodes)
        self.assertFalse(result.features["firstPullbackConfidence"]["actionable"])

    def test_early_open_impulse_uses_prior_history_for_indicator_warmup(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(early_first_pullback_sequence()))
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("first_pullback.completed", result.reasonCodes)
        self.assertLess(snapshot.features["timeSinceMarketOpenMinutes"].value, 10)
        established_trend = result.features["firstPullbackEstablishedTrend"]
        self.assertTrue(established_trend["established"])
        self.assertGreaterEqual(established_trend["directionalEfficiency"], 0.65)
        self.assertTrue(result.features["firstPullbackVwapPolicy"]["vwapPreserved"])
        pullback = result.features["firstPullback"]
        self.assertEqual(pullback["pullbackStart"], "2026-01-05T14:34:00Z")
        self.assertEqual(pullback["pullbackEnd"], "2026-01-05T14:36:00Z")
        self.assertEqual(pullback["pullbackDuration"], 2.0)
        self.assertEqual(pullback["countertrendCandleCount"], 2)
        self.assertEqual(pullback["pauseCandleCount"], 0)
        self.assertAlmostEqual(pullback["directionalEfficiency"], 1.0, places=4)
        self.assertAlmostEqual(pullback["maximumRetracement"], 0.48, places=4)
        self.assertAlmostEqual(pullback["averageCountertrendVolume"], 115000.0, places=4)
        relative_volume = result.features["firstPullbackRelativeVolume"]
        self.assertTrue(relative_volume["dataReady"])
        self.assertAlmostEqual(relative_volume["impulseCumulativeRelativeVolume"], 2.6776, places=4)
        self.assertAlmostEqual(relative_volume["impulseAverageRelativeVolume"], 2.6765, places=4)
        self.assertAlmostEqual(relative_volume["pullbackAverageRelativeVolume"], 1.2172, places=4)
        self.assertAlmostEqual(relative_volume["confirmationRelativeVolume"], 1.5625, places=4)
        self.assertAlmostEqual(relative_volume["pullbackVolumeRatio"], 0.4545, places=4)
        confirmation_quality = result.features["firstPullbackConfirmationQuality"]
        self.assertTrue(confirmation_quality["passed"])
        self.assertTrue(confirmation_quality["closeBeyondPullbackPivot"])
        self.assertTrue(confirmation_quality["closeBeyondPreviousExtreme"])
        self.assertTrue(confirmation_quality["closeAboveAnchor"])
        self.assertTrue(confirmation_quality["vwapOk"])
        self.assertGreaterEqual(confirmation_quality["closeLocation"], 0.65)
        self.assertTrue(confirmation_quality["confirmationVolumeOk"])
        self.assertTrue(confirmation_quality["rangeOk"])

    def test_opening_warmup_boundaries_are_explicit(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(early_first_pullback_sequence()))
        result = evaluate_snapshot(snapshot)

        impulse = result.features["firstPullbackImpulse"]
        pullback = result.features["firstPullback"]
        self.assertEqual(impulse["startTimestamp"], "2026-01-05T14:30:00Z")
        self.assertEqual(impulse["quality"]["startMinute"], 0.0)
        self.assertEqual(impulse["endTimestamp"], "2026-01-05T14:34:00Z")
        self.assertEqual(pullback["pullbackEnd"], "2026-01-05T14:36:00Z")
        self.assertLess(pullback["pullbackDuration"], 10)
        self.assertEqual(snapshot.rawInputs["exchangeSession"]["openTimestamp"], "2026-01-05T14:30:00Z")

    def test_prior_session_atr_warmup_works_without_contaminating_session_vwap(self) -> None:
        session_candles = early_first_pullback_sequence()
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(session_candles))
        result = evaluate_snapshot(snapshot)
        expected_session_vwap = sum(c.close * c.volume for c in session_candles) / sum(c.volume for c in session_candles)

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(snapshot.features["spy1mAtr14"].value)
        self.assertAlmostEqual(snapshot.features["sessionVwap"].value, expected_session_vwap, places=6)
        self.assertGreater(result.features["firstPullbackImpulse"]["atr"], 0)

    def test_missing_historical_warmup_returns_explicit_blocker(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(early_first_pullback_sequence()))
        raw_inputs = {**snapshot.rawInputs, "spy1mCandles": [candle.model_dump(mode="json") for candle in early_first_pullback_sequence()]}
        result = evaluate_snapshot(snapshot.model_copy(update={"rawInputs": raw_inputs}))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertFalse(result.dataReady)
        self.assertIn("required_data_unavailable", result.reasonCodes)

    def test_confirmation_fails_when_close_location_is_weak(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(early_first_pullback_sequence()))
        result = evaluate_snapshot_with_config(snapshot, FirstPullbackAfterOpenConfig(minConfirmationCloseLocation=0.95))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("confirmation.close_location_weak", result.reasonCodes)
        quality = result.features["firstPullbackConfirmationQuality"]
        self.assertFalse(quality["passed"])
        self.assertFalse(quality["closeLocationOk"])

    def test_confirmation_fails_when_trigger_candle_is_excessively_large(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(early_first_pullback_sequence()))
        result = evaluate_snapshot_with_config(snapshot, FirstPullbackAfterOpenConfig(maxConfirmationRangeAtr=0.5))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("confirmation.trigger_range_invalid", result.reasonCodes)
        quality = result.features["firstPullbackConfirmationQuality"]
        self.assertFalse(quality["passed"])
        self.assertFalse(quality["rangeOk"])

    def test_previous_candle_break_without_pullback_pivot_break_does_not_confirm(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(no_pivot_break_confirmation_sequence()))
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("confirmation.pivot_not_cleared", result.reasonCodes)
        quality = result.features["firstPullbackConfirmationQuality"]
        self.assertFalse(quality["passed"])
        self.assertTrue(quality["closeBeyondPreviousExtreme"])
        self.assertFalse(quality["closeBeyondPullbackPivot"])

    def test_large_upper_wick_on_bullish_confirmation_is_rejected(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(large_upper_wick_confirmation_sequence()))
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("confirmation.rejection_wick_too_large", result.reasonCodes)
        quality = result.features["firstPullbackConfirmationQuality"]
        self.assertFalse(quality["passed"])
        self.assertFalse(quality["rejectionWickOk"])

    def test_confirmation_volume_remaining_weak_is_rejected(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(weak_volume_confirmation_sequence()))
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("confirmation.volume_not_greater_than_pullback", result.reasonCodes)
        quality = result.features["firstPullbackConfirmationQuality"]
        self.assertFalse(quality["passed"])
        self.assertFalse(quality["confirmationVolumeOk"])

    def test_confirmation_candle_overextended_from_anchor_is_rejected(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(early_first_pullback_sequence()))
        result = evaluate_snapshot_with_config(snapshot, FirstPullbackAfterOpenConfig(maxConfirmationVwapDistanceAtr=0.05))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("confirmation.entry_extension_excessive", result.reasonCodes)
        quality = result.features["firstPullbackConfirmationQuality"]
        self.assertFalse(quality["passed"])
        self.assertFalse(quality["extensionOk"])

    def test_strict_relative_volume_blocks_missing_time_of_day_baseline(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(first_pullback_sequence()))
        config = FirstPullbackAfterOpenConfig(relativeVolumeEvidenceMode=RelativeVolumeEvidenceMode.STRICT)
        result = evaluate_snapshot_with_config(snapshot, config)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.volume_unavailable", result.reasonCodes)

    def test_optional_relative_volume_missing_uses_penalized_contribution(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(first_pullback_sequence()))
        default_result = evaluate_snapshot(snapshot)
        penalized_config = FirstPullbackAfterOpenConfig(optionalRelativeVolumeMissingContribution=0.0)
        penalized_result = evaluate_snapshot_with_config(snapshot, penalized_config)

        self.assertEqual(default_result.signal, Signal.BUY.value)
        self.assertFalse(default_result.features["firstPullbackRelativeVolume"]["dataReady"])
        self.assertLess(penalized_result.confidence, default_result.confidence)

    def test_pullback_pause_candle_is_not_averaged_into_countertrend_volume(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(first_pullback_with_pause_before_confirmation()))
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.BUY.value)
        pullback = result.features["firstPullback"]
        self.assertEqual(pullback["countertrendCandleCount"], 2)
        self.assertEqual(pullback["pauseCandleCount"], 1)
        self.assertAlmostEqual(pullback["averageCountertrendVolume"], 115000.0, places=4)
        self.assertEqual(pullback["pullbackEnd"], "2026-01-05T14:36:00Z")

    def test_pullback_decelerating_phase_is_reported_before_confirmation(self) -> None:
        partial = first_pullback_with_pause_before_confirmation()[:-1]
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(partial))
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertEqual(result.features["firstPullbackState"], "pullback_decelerating")
        pullback = result.features["firstPullback"]
        self.assertEqual(pullback["countertrendCandleCount"], 2)
        self.assertEqual(pullback["pauseCandleCount"], 1)

    def test_confirmation_candle_must_be_finalized_before_signal(self) -> None:
        request = request_for_early_open(early_first_pullback_sequence()).model_copy(
            update={
                "evaluationTimestamp": OPEN_UTC + timedelta(minutes=7),
                "finalizationLagSeconds": 1,
            }
        )
        snapshot = PointInTimeFeatureEngine().compute(request)
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.waiting_for_confirmation", result.reasonCodes)
        self.assertIsNone(result.features["firstPullbackConfirmationBar"])
        self.assertIsNone(result.features["firstPullbackExecution"])

    def test_finalized_confirmation_bar_metadata_and_execution_policy_are_returned(self) -> None:
        request = request_for_early_open(early_first_pullback_sequence()).model_copy(
            update={
                "evaluationTimestamp": OPEN_UTC + timedelta(minutes=7, seconds=1),
                "finalizationLagSeconds": 1,
            }
        )
        snapshot = PointInTimeFeatureEngine().compute(request)
        raw_candles = [dict(candle) for candle in snapshot.rawInputs["spy1mCandles"]]
        raw_candles[-1]["providerRevision"] = "fixture-revision-7"
        snapshot = snapshot.model_copy(update={"rawInputs": {**snapshot.rawInputs, "spy1mCandles": raw_candles}})
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.BUY.value)
        confirmation_bar = result.features["firstPullbackConfirmationBar"]
        self.assertEqual(confirmation_bar["barStartTimestamp"], "2026-01-05T14:36:00Z")
        self.assertEqual(confirmation_bar["barEndTimestamp"], "2026-01-05T14:37:00Z")
        self.assertTrue(confirmation_bar["wasFinalized"])
        self.assertEqual(confirmation_bar["providerRevision"], "fixture-revision-7")
        execution = result.features["firstPullbackExecution"]
        self.assertEqual(execution["executionTiming"], "next_permitted_event_after_confirmation")
        self.assertEqual(execution["executionPricePolicy"], "external_execution_engine_next_executable_price")
        self.assertTrue(execution["doesNotAssumeConfirmationCandlePrice"])
        self.assertEqual(execution["earliestExecutionTimestamp"], "2026-01-05T14:37:01Z")

    def test_repeated_completed_candle_reuses_event_without_actionable_duplicate(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(early_first_pullback_sequence()))
        first = evaluate_snapshot(snapshot)
        second = evaluate_snapshot(snapshot)

        self.assertEqual(first.signal, Signal.BUY.value)
        self.assertTrue(first.eligible)
        self.assertFalse(first.features["firstPullbackPersistentState"]["signalConsumed"])
        self.assertEqual(second.signal, Signal.BUY.value)
        self.assertFalse(second.eligible)
        self.assertIn("first_pullback.signal_already_emitted", second.reasonCodes)
        first_state = first.features["firstPullbackPersistentState"]
        second_state = second.features["firstPullbackPersistentState"]
        self.assertEqual(second_state["setupId"], first_state["setupId"])
        self.assertEqual(second_state["eventId"], first_state["eventId"])
        self.assertTrue(second_state["signalEmitted"])
        self.assertTrue(second_state["signalConsumed"])
        self.assertEqual(second_state["lastProcessedBarEnd"], first_state["lastProcessedBarEnd"])

    def test_evaluation_one_candle_later_does_not_emit_again(self) -> None:
        first_snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(early_first_pullback_sequence()))
        first = evaluate_snapshot(first_snapshot)
        later_candles = [
            *early_first_pullback_sequence(),
            candle_at(OPEN_UTC, 7, 101.30, 101.42, 101.22, 101.36, 120000),
        ]
        later_snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(later_candles))
        second = evaluate_snapshot(later_snapshot)

        self.assertEqual(first.signal, Signal.BUY.value)
        self.assertTrue(first.eligible)
        self.assertEqual(second.signal, Signal.HOLD.value)
        self.assertFalse(second.eligible)
        self.assertIn("first_pullback.already_completed", second.reasonCodes)

    def test_restart_restores_session_state_for_same_setup(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(early_first_pullback_sequence()))
        first = evaluate_snapshot(snapshot)
        state = first.features["firstPullbackPersistentState"]
        restored_state = {
            "algorithmId": state["algorithmId"],
            "strategyId": state["strategyId"],
            "symbol": state["symbol"],
            "sessionDate": state["sessionDate"],
            "setupId": state["setupId"],
            "eventId": state["eventId"],
            "state": state["state"],
            "signalEmitted": True,
            "signalEmittedAt": state["signalEmittedAt"],
            "signalConsumed": True,
            "invalidationReason": state["invalidationReason"],
            "lastProcessedBarEnd": state["lastProcessedBarEnd"],
        }
        FirstPullbackAfterOpenStrategy.reset_state_store()
        raw_inputs = {**snapshot.rawInputs, "firstPullbackPersistentState": restored_state}
        second = evaluate_snapshot(snapshot.model_copy(update={"rawInputs": raw_inputs}))

        self.assertEqual(first.signal, Signal.BUY.value)
        self.assertEqual(second.signal, Signal.BUY.value)
        self.assertFalse(second.eligible)
        self.assertIn("first_pullback.signal_already_emitted", second.reasonCodes)

    def test_missed_evaluation_is_recorded_without_relabeling_later_pullback(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(second_pullback_extension()))
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.already_completed", result.reasonCodes)
        self.assertEqual(result.features["firstPullbackPersistentState"]["state"], "session_complete")

    def test_strict_vwap_policy_blocks_excessive_intrabar_penetration(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(early_first_pullback_sequence()))
        config = FirstPullbackAfterOpenConfig(vwapPenetrationToleranceAtr=0.05)
        result = evaluate_snapshot_with_config(snapshot, config)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.vwap_not_preserved", result.reasonCodes)
        vwap_policy = result.features["firstPullbackVwapPolicy"]
        self.assertFalse(vwap_policy["vwapPreserved"])
        self.assertGreater(vwap_policy["maximumVwapPenetrationAtr"], 0.05)
        self.assertEqual(vwap_policy["barsClosedWrongSideOfVwap"], 0)
        self.assertTrue(vwap_policy["vwapReclaimed"])
        self.assertIsNone(vwap_policy["vwapReclaimTimestamp"])

    def test_moderate_vwap_policy_allows_temporary_vwap_tag(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(early_first_pullback_sequence()))
        config = FirstPullbackAfterOpenConfig(
            vwapPreservationMode=VwapPreservationMode.MODERATE,
            vwapPenetrationToleranceAtr=0.05,
        )
        result = evaluate_snapshot_with_config(snapshot, config)

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        vwap_policy = result.features["firstPullbackVwapPolicy"]
        self.assertTrue(vwap_policy["passed"])
        self.assertTrue(vwap_policy["vwapPreserved"])
        self.assertGreater(vwap_policy["maximumVwapPenetrationAtr"], 0.05)

    def test_moderate_vwap_policy_records_wrong_side_close_and_reclaim(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(vwap_close_loss_then_reclaim_sequence()))
        config = FirstPullbackAfterOpenConfig(vwapPreservationMode=VwapPreservationMode.MODERATE)
        result = evaluate_snapshot_with_config(snapshot, config)

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        vwap_policy = result.features["firstPullbackVwapPolicy"]
        self.assertEqual(vwap_policy["barsClosedWrongSideOfVwap"], 1)
        self.assertTrue(vwap_policy["vwapReclaimed"])
        self.assertEqual(vwap_policy["vwapReclaimTimestamp"], "2026-01-05T14:35:00+00:00")

    def test_context_vwap_policy_penalizes_without_blocking(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(early_first_pullback_sequence()))
        baseline = evaluate_snapshot(snapshot)
        config = FirstPullbackAfterOpenConfig(
            vwapPreservationMode=VwapPreservationMode.CONTEXT,
            vwapPenetrationToleranceAtr=0.05,
        )
        result = evaluate_snapshot_with_config(snapshot, config)

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertLess(result.confidence, baseline.confidence)
        vwap_policy = result.features["firstPullbackVwapPolicy"]
        self.assertTrue(vwap_policy["passed"])
        self.assertFalse(vwap_policy["vwapPreserved"])
        self.assertIn("vwap_policy.context_penalty", result.reasonCodes)

    def test_established_trend_rejects_inefficient_impulse_path(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(inefficient_impulse_then_pullback_sequence()))
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.no_opening_impulse", result.reasonCodes)
        self.assertIn("impulse_quality.efficiency_failed", result.reasonCodes)

    def test_late_impulse_start_is_not_treated_as_opening_impulse(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(late_impulse_then_pullback_sequence()))
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.no_opening_impulse", result.reasonCodes)
        self.assertIn("impulse_quality.started_too_late", result.reasonCodes)

    def test_weak_body_impulse_quality_is_rejected(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(weak_body_impulse_then_pullback_sequence()))
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.no_opening_impulse", result.reasonCodes)
        self.assertIn("impulse_quality.body_to_range_failed", result.reasonCodes)

    def test_optional_five_minute_permission_is_read_only_context(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(early_first_pullback_sequence()))
        snapshot = snapshot.model_copy(update={"rawInputs": {**snapshot.rawInputs, "spy5mCandles": []}})
        config = FirstPullbackAfterOpenConfig(requireFiveMinutePermission=True)
        result = evaluate_snapshot_with_config(snapshot, config)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.trend_not_established", result.reasonCodes)
        self.assertIn("established_trend.five_minute_permission_failed", result.reasonCodes)

    def test_bearish_first_pullback_confirmation_generates_sell(self) -> None:
        result = evaluate(bearish_first_pullback_sequence())

        self.assertEqual(result.signal, Signal.SELL.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("first_pullback.completed", result.reasonCodes)

    def test_pullback_breaking_impulse_origin_is_invalidated(self) -> None:
        result = evaluate(invalidated_sequence())

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.impulse_origin_broken", result.reasonCodes)
        self.assertIn("origin.close_violation", result.reasonCodes)
        self.assertIn("state:first_pullback_rejected", result.reasonCodes)
        self.assertIn("pullback:trend_reversal", result.reasonCodes)
        levels = result.features["firstPullbackInvalidationLevels"]
        thesis = levels["thesisInvalidation"]["violation"]
        self.assertTrue(thesis["wickViolation"])
        self.assertTrue(thesis["closeViolation"])
        self.assertTrue(thesis["hardViolation"])

    def test_wick_through_origin_without_close_is_not_origin_invalidation(self) -> None:
        result = evaluate(wick_only_origin_breach_sequence())

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertNotIn("first_pullback.impulse_origin_broken", result.reasonCodes)
        levels = result.features["firstPullbackInvalidationLevels"]
        thesis = levels["thesisInvalidation"]["violation"]
        self.assertTrue(thesis["wickViolation"])
        self.assertFalse(thesis["closeViolation"])
        self.assertFalse(thesis["acceptanceBeyondLevel"])
        self.assertFalse(thesis["hardViolation"])

    def test_later_second_pullback_is_not_labeled_as_first(self) -> None:
        result = evaluate(second_pullback_extension())

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.already_completed", result.reasonCodes)
        self.assertIn("state:session_complete", result.reasonCodes)

    def test_first_pullback_too_shallow_locks_out_later_valid_looking_pullback(self) -> None:
        result = evaluate_snapshot(PointInTimeFeatureEngine().compute(request_for_early_open(first_pullback_too_shallow_then_valid_looking_sequence())))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.too_shallow", result.reasonCodes)
        self.assertIn("pullback:too_shallow", result.reasonCodes)
        self.assertIn("first_pullback.session_locked", result.reasonCodes)
        self.assertNotIn("first_pullback.completed", result.reasonCodes)

    def test_first_pullback_too_deep_locks_out_later_valid_looking_pullback(self) -> None:
        result = evaluate_snapshot(PointInTimeFeatureEngine().compute(request_for_early_open(first_pullback_too_deep_then_valid_looking_sequence())))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.too_deep", result.reasonCodes)
        self.assertIn("pullback:too_deep", result.reasonCodes)
        self.assertIn("first_pullback.session_locked", result.reasonCodes)
        self.assertNotIn("first_pullback.completed", result.reasonCodes)

    def test_pullback_depth_is_invalidated_while_still_forming(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(first_pullback_too_deep_then_valid_looking_sequence()))
        result = evaluate_snapshot_with_config(
            snapshot,
            FirstPullbackAfterOpenConfig(
                pullbackRetracementMax=0.45,
                relativeVolumeEvidenceMode=RelativeVolumeEvidenceMode.OPTIONAL,
            ),
        )

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.too_deep", result.reasonCodes)
        self.assertIn("pullback:too_deep", result.reasonCodes)

    def test_confirmation_after_qualification_window_expires(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(first_pullback_expires_sequence()))
        result = evaluate_snapshot_with_config(
            snapshot,
            FirstPullbackAfterOpenConfig(maximumBarsFromQualificationToConfirmation=1),
        )

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.expired", result.reasonCodes)
        self.assertIn("pullback:expired", result.reasonCodes)

    def test_maximum_pullback_bars_expires_forming_pullback(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(first_pullback_expires_sequence()))
        result = evaluate_snapshot_with_config(
            snapshot,
            FirstPullbackAfterOpenConfig(maximumPullbackBars=1),
        )

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.expired", result.reasonCodes)
        self.assertIn("pullback:expired", result.reasonCodes)

    def test_first_pullback_with_excessive_volume_is_rejected(self) -> None:
        result = evaluate_snapshot(PointInTimeFeatureEngine().compute(request_for_early_open(first_pullback_high_volume_sequence())))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.too_high_volume", result.reasonCodes)
        self.assertIn("pullback:too_high_volume", result.reasonCodes)
        self.assertIn("first_pullback.session_locked", result.reasonCodes)

    def test_high_volume_first_pullback_locks_out_lower_volume_second_pullback(self) -> None:
        result = evaluate_snapshot(PointInTimeFeatureEngine().compute(request_for_early_open(high_volume_first_pullback_then_lower_volume_second())))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.too_high_volume", result.reasonCodes)
        self.assertIn("first_pullback.session_locked", result.reasonCodes)
        self.assertEqual(result.features["firstPullback"]["pullbackStart"], "2026-01-05T14:34:00Z")
        self.assertNotIn("first_pullback.completed", result.reasonCodes)

    def test_failed_first_confirmation_is_not_replaced_by_later_confirmation(self) -> None:
        result = evaluate_snapshot(PointInTimeFeatureEngine().compute(request_for_early_open(failed_confirmation_then_later_confirmation_sequence())))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.confirmation_failed", result.reasonCodes)
        self.assertIn("confirmation.pivot_not_cleared", result.reasonCodes)
        self.assertIn("first_pullback.session_locked", result.reasonCodes)
        self.assertNotIn("first_pullback.completed", result.reasonCodes)

    def test_first_pullback_expires_without_confirmation(self) -> None:
        result = evaluate_snapshot(PointInTimeFeatureEngine().compute(request_for_early_open(first_pullback_expires_sequence())))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.expired", result.reasonCodes)
        self.assertIn("pullback:expired", result.reasonCodes)
        self.assertIn("first_pullback.session_locked", result.reasonCodes)

    def test_first_pullback_reversing_trend_locks_session(self) -> None:
        result = evaluate_snapshot(PointInTimeFeatureEngine().compute(request_for_early_open(first_pullback_trend_reversal_sequence())))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.trend_reversal", result.reasonCodes)
        self.assertIn("pullback:trend_reversal", result.reasonCodes)
        self.assertIn("first_pullback.session_locked", result.reasonCodes)

    def test_multiple_closes_below_vwap_are_blocked_in_moderate_mode(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(multi_close_vwap_loss_sequence()))
        result = evaluate_snapshot_with_config(snapshot, FirstPullbackAfterOpenConfig(vwapPreservationMode=VwapPreservationMode.MODERATE))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.vwap_not_preserved", result.reasonCodes)
        vwap_policy = result.features["firstPullbackVwapPolicy"]
        self.assertGreaterEqual(vwap_policy["barsClosedWrongSideOfVwap"], 2)
        self.assertIn("vwap_policy.wrong_side_structure", vwap_policy["reasonCodes"])

    def test_ema_structure_reversal_blocks_established_trend(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(ema_structure_reversal_sequence()))
        strategy = FirstPullbackAfterOpenStrategy()
        context = StrategyEvaluationContext(
            registryEntry=resolve_strategy("first_pullback_after_open"),
            featureSnapshot=snapshot,
            configurationHash=strategy.config.configurationHash,
        )
        candles = _regular_session_candles(snapshot.rawInputs["spy1mCandles"], context)
        state = strategy._run_state_machine(
            candles,
            _indicator_candles(snapshot.rawInputs["spy1mCandles"], context, include_premarket=strategy.config.includeApprovedPremarketInIndicatorWarmup),
            context.sessionDate,
        )
        impulse = state.impulse
        self.assertIsNotNone(impulse)
        assert impulse is not None
        end_timestamp = impulse.endTimestamp - timedelta(minutes=1)
        established = strategy._established_trend(
            candles,
            impulse.endIndex + 1,
            impulse,
            INDICATORS.vwap_series(candles),
            {end_timestamp: 99.80},
            {end_timestamp: 100.20},
            {end_timestamp: 100.25},
            {},
        )

        self.assertFalse(established.established)
        self.assertFalse(established.emaRelationshipOk)
        self.assertFalse(established.ema20SlopeOk)
        self.assertIn("established_trend.ema_relationship_failed", established.reasonCodes)
        self.assertIn("established_trend.ema20_slope_failed", established.reasonCodes)

    def test_maximum_bars_from_impulse_to_pullback_expires_setup(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(delayed_pullback_after_impulse_sequence()))
        result = evaluate_snapshot_with_config(snapshot, FirstPullbackAfterOpenConfig(maximumBarsFromImpulseToPullback=2))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.expired", result.reasonCodes)
        self.assertIn("pullback:expired", result.reasonCodes)

    def test_impulse_building_updates_extreme_until_countertrend_begins(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for_early_open(extended_impulse_then_pullback_sequence()))
        strategy = FirstPullbackAfterOpenStrategy()
        context = StrategyEvaluationContext(
            registryEntry=resolve_strategy("first_pullback_after_open"),
            featureSnapshot=snapshot,
            configurationHash=strategy.config.configurationHash,
        )
        raw_candles = snapshot.rawInputs["spy1mCandles"]
        state = strategy._run_state_machine(
            _regular_session_candles(raw_candles, context),
            _indicator_candles(raw_candles, context, include_premarket=strategy.config.includeApprovedPremarketInIndicatorWarmup),
            context.sessionDate,
        )
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertIn("state:signal_emitted", result.reasonCodes)
        self.assertIsNotNone(state.impulse)
        self.assertIsNotNone(state.pullback)
        self.assertEqual(state.impulse.endIndex, 4)
        self.assertAlmostEqual(state.impulse.extremePrice, 101.88, places=4)
        self.assertAlmostEqual(state.impulse.averageVolume, 234000.0, places=4)
        self.assertGreater(state.pullback.retracement, 0.35)
        levels = result.features["firstPullbackInvalidationLevels"]
        self.assertAlmostEqual(result.structuralInvalidationPrice, levels["entryInvalidation"]["level"], places=4)
        self.assertAlmostEqual(levels["entryInvalidation"]["level"], 100.95, places=4)
        self.assertAlmostEqual(levels["thesisInvalidation"]["level"], 100.0 - state.impulse.atr * strategy.config.originBreakAtrBuffer, places=4)
        self.assertGreater(result.regimeFit, 0.7)
        regime_fit = result.features["firstPullbackRegimeFit"]
        self.assertEqual(regime_fit["score"], result.regimeFit)
        self.assertIn("trendStrength", regime_fit["components"])
        self.assertIn("vwapCrossingFrequency", regime_fit["components"])

    def test_historical_reliability_comes_from_performance_tracker(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(first_pullback_sequence()))
        base = evaluate_snapshot(snapshot)
        regime_key = base.features["firstPullbackRegimeFit"]["regimeKey"]
        FirstPullbackAfterOpenStrategy.reset_state_store()
        raw_inputs = {
            **snapshot.rawInputs,
            "strategyPerformance": {
                "version": "walk_forward_reliability_v1",
                "strategies": {
                    "first_pullback_after_open": {
                        "regimes": {
                            regime_key: {
                                "walkForwardReliability": 0.73,
                                "sampleSize": 84,
                                "window": {"folds": 5, "mode": "purged_walk_forward"},
                                "version": "first_pullback_walk_forward_v3",
                            }
                        }
                    }
                },
            },
        }
        result = evaluate_snapshot(snapshot.model_copy(update={"rawInputs": raw_inputs}))

        self.assertEqual(result.reliability, 0.73)
        self.assertEqual(result.reliabilityVersion, "first_pullback_walk_forward_v3")
        reliability = result.features["firstPullbackHistoricalReliability"]
        self.assertEqual(reliability["score"], 0.73)
        self.assertEqual(reliability["sourceWindow"]["source"], "walk_forward_performance_tracker")
        self.assertEqual(result.features["setupConfidence"], result.confidence)

    def test_missing_required_data_generates_hold_not_eligible(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(first_pullback_sequence()))
        features = {
            **snapshot.features,
            "timeSinceMarketOpenMinutes": snapshot.features["timeSinceMarketOpenMinutes"].model_copy(update={"quality": FeatureQuality.MISSING}),
        }
        result = evaluate_snapshot(snapshot.model_copy(update={"features": features}))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertFalse(result.dataReady)
        self.assertIn("required_data_unavailable", result.reasonCodes)

    def test_session_series_quality_is_reported_for_valid_input(self) -> None:
        result = evaluate(first_pullback_sequence())

        quality = result.features["firstPullbackSessionSeriesQuality"]
        self.assertTrue(quality["isComplete"])
        self.assertTrue(quality["isFresh"])
        self.assertFalse(quality["hasDuplicates"])
        self.assertFalse(quality["hasMissingIntervals"])
        self.assertFalse(quality["hasOutOfOrderBars"])
        self.assertFalse(quality["hasZeroVolumeBars"])
        self.assertTrue(quality["symbolMatches"])
        self.assertTrue(quality["timeframeMatches"])
        self.assertTrue(quality["sessionMatches"])
        self.assertEqual(quality["qualityReasonCodes"], [])

    def test_session_series_quality_failures_return_hold(self) -> None:
        base_request = request_for(first_pullback_sequence())
        cases = [
            (
                "duplicate",
                [*base_request.spy1mCandles, base_request.spy1mCandles[-1]],
                "session_series.duplicates",
                "hasDuplicates",
            ),
            (
                "missing_interval",
                [*base_request.spy1mCandles[:10], *base_request.spy1mCandles[11:]],
                "session_series.missing_intervals",
                "hasMissingIntervals",
            ),
            (
                "out_of_order",
                [*base_request.spy1mCandles[:-2], base_request.spy1mCandles[-1], base_request.spy1mCandles[-2]],
                "session_series.out_of_order",
                "hasOutOfOrderBars",
            ),
            (
                "zero_volume",
                [*base_request.spy1mCandles[:-1], base_request.spy1mCandles[-1].model_copy(update={"volume": 0})],
                "session_series.zero_volume",
                "hasZeroVolumeBars",
            ),
            (
                "symbol_mismatch",
                [*base_request.spy1mCandles[:-1], base_request.spy1mCandles[-1].model_copy(update={"symbol": "QQQ"})],
                "session_series.symbol_mismatch",
                "symbolMatches",
            ),
            (
                "timeframe_mismatch",
                [*base_request.spy1mCandles[:-1], base_request.spy1mCandles[-1].model_copy(update={"timeframe": "5Min"})],
                "session_series.timeframe_mismatch",
                "timeframeMatches",
            ),
        ]

        for label, candles, reason_code, field in cases:
            with self.subTest(label=label):
                snapshot = PointInTimeFeatureEngine().compute(base_request)
                raw_inputs = {
                    **snapshot.rawInputs,
                    "spy1mCandles": [candle.model_dump(mode="json") for candle in candles],
                }
                snapshot = snapshot.model_copy(update={"rawInputs": raw_inputs})
                result = evaluate_snapshot(snapshot)
                quality = result.features["firstPullbackSessionSeriesQuality"]

                self.assertEqual(result.signal, Signal.HOLD.value)
                self.assertFalse(result.eligible)
                self.assertIn("first_pullback.session_series_quality_failed", result.reasonCodes)
                self.assertIn(reason_code, result.reasonCodes)
                if field in {"symbolMatches", "timeframeMatches"}:
                    self.assertFalse(quality[field])
                else:
                    self.assertTrue(quality[field])

    def test_stale_final_bar_returns_explicit_quality_failure(self) -> None:
        base_request = request_for(first_pullback_sequence()).model_copy(
            update={"evaluationTimestamp": first_pullback_sequence()[-1].timestamp + timedelta(minutes=4, seconds=2)}
        )
        snapshot = PointInTimeFeatureEngine().compute(base_request)
        result = evaluate_snapshot(snapshot)
        quality = result.features["firstPullbackSessionSeriesQuality"]

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertFalse(quality["isFresh"])
        self.assertIn("first_pullback.session_series_quality_failed", result.reasonCodes)
        self.assertIn("session_series.stale", result.reasonCodes)

    def test_auxiliary_global_snapshot_failure_does_not_block_internal_setup(self) -> None:
        request = request_for(first_pullback_sequence()).model_copy(
            update={
                "qqqAlignedCandles": [],
                "iwmAlignedCandles": [],
                "breadthComponents": {},
                "quote": None,
            }
        )
        snapshot = PointInTimeFeatureEngine().compute(request)
        result = evaluate_snapshot(snapshot)

        self.assertFalse(snapshot.dataReady)
        self.assertFalse(snapshot.globalSnapshotReady)
        self.assertTrue(snapshot.strategyRequiredDataReady)
        self.assertFalse(snapshot.auxiliaryContextReady)
        self.assertFalse(snapshot.executionDataReady)
        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("first_pullback.completed", result.reasonCodes)

    def test_pullback_without_confirmation_is_boundary_hold(self) -> None:
        result = evaluate(first_pullback_sequence()[:-1])

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.waiting_for_confirmation", result.reasonCodes)

    def test_changing_only_event_direction_field_does_not_change_result(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(first_pullback_sequence()))
        base = evaluate_snapshot(snapshot)
        features_with_event_direction = {
            **snapshot.features,
            "event.directionBias": snapshot.features["sessionVwap"].model_copy(update={"value": "short"}),
        }
        FirstPullbackAfterOpenStrategy.reset_state_store()
        changed = evaluate_snapshot(snapshot.model_copy(update={"features": features_with_event_direction}))

        self.assertEqual(changed.signal, base.signal)
        self.assertEqual(changed.confidence, base.confidence)
        self.assertEqual(changed.reasonCodes, base.reasonCodes)

    def test_session_state_resets_on_next_trading_day(self) -> None:
        next_day = first_pullback_sequence(NEXT_OPEN_UTC)
        result = evaluate(next_day, NEXT_SESSION_DATE)

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertIn("first_pullback.completed", result.reasonCodes)


if __name__ == "__main__":
    unittest.main()
