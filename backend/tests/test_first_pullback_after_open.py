from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

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
    FirstPullbackAfterOpenStrategy,
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
            candle_at(open_utc, 22, 100.95, 101.00, 100.35, 100.50, 110000),
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


def bearish_first_pullback_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    candles = flat_opening(open_utc)
    candles.extend(
        [
            candle_at(open_utc, 20, 100.00, 100.05, 99.45, 99.55, 230000),
            candle_at(open_utc, 21, 99.55, 99.65, 98.95, 99.05, 240000),
            candle_at(open_utc, 22, 99.05, 99.65, 99.00, 99.50, 110000),
            candle_at(open_utc, 23, 99.50, 99.54, 98.82, 98.86, 130000),
        ]
    )
    return candles


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
    evaluation = candles[-1].timestamp
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


class FirstPullbackAfterOpenTest(unittest.TestCase):
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
        self.assertIn("spy1mAtr14", result.features)

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
        self.assertIn("state:invalidated", result.reasonCodes)

    def test_later_second_pullback_is_not_labeled_as_first(self) -> None:
        result = evaluate(second_pullback_extension())

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("first_pullback.already_completed", result.reasonCodes)
        self.assertIn("state:completed", result.reasonCodes)

    def test_missing_required_data_generates_hold_not_eligible(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(first_pullback_sequence()))
        features = {
            **snapshot.features,
            "spy1mAtr14": snapshot.features["spy1mAtr14"].model_copy(update={"quality": FeatureQuality.MISSING}),
        }
        result = evaluate_snapshot(snapshot.model_copy(update={"features": features}))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertFalse(result.dataReady)
        self.assertIn("required_data_unavailable", result.reasonCodes)

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
