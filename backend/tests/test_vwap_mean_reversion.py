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
from backend.app.strategies.directional.vwap_mean_reversion import (
    VwapMeanReversionStrategy,
)
from backend.app.strategies.registry import resolve_strategy


SESSION_DATE = date(2026, 1, 5)
OPEN_UTC = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def candle_at(
    minute: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100000,
    trade_count: int = 1000,
) -> MarketCandle:
    return MarketCandle(
        timestamp=OPEN_UTC + timedelta(minutes=minute),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        tradeCount=trade_count + minute,
        provider="fixture",
        symbol="SPY",
        timeframe="1Min",
    )


def range_base(count: int = 34) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        center = 100.0 + (0.04 if minute % 2 else -0.04)
        rows.append(candle_at(minute, center - 0.01, center + 0.12, center - 0.12, center + 0.01, 100000))
    return rows


def long_reversion_sequence() -> list[MarketCandle]:
    rows = range_base()
    rows.extend(
        [
            candle_at(34, 99.85, 99.92, 99.30, 99.42, 115000, 1300),
            candle_at(35, 99.42, 99.58, 99.08, 99.48, 90000, 1300),
        ]
    )
    return rows


def short_reversion_sequence() -> list[MarketCandle]:
    rows = range_base()
    rows.extend(
        [
            candle_at(34, 100.15, 100.70, 100.08, 100.58, 115000, 1300),
            candle_at(35, 100.58, 100.92, 100.42, 100.52, 90000, 1300),
        ]
    )
    return rows


def large_distance_without_momentum_loss_sequence() -> list[MarketCandle]:
    rows = range_base()
    rows.extend(
        [
            candle_at(34, 99.95, 100.00, 99.45, 99.50, 115000, 1300),
            candle_at(35, 99.50, 99.52, 98.96, 99.02, 115000, 1300),
        ]
    )
    return rows


def strong_uptrend_sequence(count: int = 42) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        base = 99.0 + minute * 0.08
        rows.append(candle_at(minute, base, base + 0.12, base - 0.03, base + 0.10, 160000, 2000))
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


def auxiliary_candles(symbol: str, count: int = 90) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        base = 100 + minute * 0.01
        rows.append(candle_at(minute, base, base + 0.08, base - 0.08, base + 0.01).model_copy(update={"symbol": symbol}))
    return rows


def request_for(candles: list[MarketCandle], *, session_vwap: float = 100.0) -> PointInTimeFeatureRequest:
    evaluation = candles[-1].timestamp
    return PointInTimeFeatureRequest(
        evaluationTimestamp=evaluation,
        sessionDate=SESSION_DATE,
        spy1mCandles=candles,
        spy5mCandles=timeframe_history(end=evaluation, step_minutes=5, timeframe="5Min"),
        spy15mCandles=timeframe_history(end=evaluation, step_minutes=15, timeframe="15Min"),
        sessionVwap=session_vwap,
        sessionVwapTimestamp=evaluation,
        qqqAlignedCandles=auxiliary_candles("QQQ"),
        iwmAlignedCandles=auxiliary_candles("IWM"),
        priorDayOHLC=PriorDayOHLC(sessionDate=date(2026, 1, 2), open=99, high=101, low=98, close=100),
        quote=BidAskQuote(bid=candles[-1].close - 0.01, ask=candles[-1].close + 0.01, timestamp=evaluation),
        breadthComponents={"XLK": auxiliary_candles("XLK")},
    )


def with_feature(snapshot, name: str, value: float, quality: FeatureQuality = FeatureQuality.READY):
    features = {
        **snapshot.features,
        name: snapshot.features[name].model_copy(update={"value": value, "quality": quality}),
    }
    return snapshot.model_copy(update={"features": features})


def stable_range_snapshot(candles: list[MarketCandle], *, session_vwap: float = 100.0):
    snapshot = PointInTimeFeatureEngine().compute(request_for(candles, session_vwap=session_vwap))
    snapshot = with_feature(snapshot, "spy1mAdx14", 16.0)
    snapshot = with_feature(snapshot, "sessionVwapSlope", 0.0)
    return snapshot


def evaluate(candles: list[MarketCandle], *, session_vwap: float = 100.0):
    return evaluate_snapshot(stable_range_snapshot(candles, session_vwap=session_vwap))


def evaluate_snapshot(snapshot):
    strategy = VwapMeanReversionStrategy()
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("vwap_mean_reversion"),
        featureSnapshot=snapshot,
        configurationHash=strategy.config.configurationHash,
    )
    return strategy.evaluate(context)


class VwapMeanReversionTest(unittest.TestCase):
    def test_large_distance_alone_is_insufficient(self) -> None:
        result = evaluate(large_distance_without_momentum_loss_sequence())

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("vwap_mean_reversion.no_momentum_loss", result.reasonCodes)

    def test_high_adx_continuation_suppresses_setup(self) -> None:
        snapshot = stable_range_snapshot(long_reversion_sequence())
        snapshot = with_feature(snapshot, "spy1mAdx14", 38.0)
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("vwap_mean_reversion.strong_trend_suppressed", result.reasonCodes)

    def test_long_reversion_fixture_can_produce_buy(self) -> None:
        result = evaluate(long_reversion_sequence())

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("vwap_mean_reversion.confirmed", result.reasonCodes)
        self.assertIn("distanceFromVwapAtr", result.features)

    def test_short_reversion_fixture_can_produce_sell(self) -> None:
        result = evaluate(short_reversion_sequence())

        self.assertEqual(result.signal, Signal.SELL.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("vwap_mean_reversion.confirmed", result.reasonCodes)

    def test_strong_trend_does_not_generate_repeated_countertrend_signals(self) -> None:
        candles = strong_uptrend_sequence()

        for end_index in range(34, len(candles)):
            with self.subTest(end_index=end_index):
                snapshot = PointInTimeFeatureEngine().compute(request_for(candles[: end_index + 1], session_vwap=100.0))
                snapshot = with_feature(snapshot, "spy1mAdx14", 42.0)
                snapshot = with_feature(snapshot, "sessionVwapSlope", 0.0012)
                result = evaluate_snapshot(snapshot)

                self.assertEqual(result.signal, Signal.HOLD.value)
                self.assertFalse(result.eligible)
                self.assertIn("vwap_mean_reversion.strong_trend_suppressed", result.reasonCodes)

    def test_missing_required_data_generates_hold_not_eligible(self) -> None:
        snapshot = stable_range_snapshot(long_reversion_sequence())
        snapshot = with_feature(snapshot, "distanceFromVwapAtr", -1.6, FeatureQuality.MISSING)
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertFalse(result.dataReady)
        self.assertIn("required_data_unavailable", result.reasonCodes)

    def test_changing_only_event_direction_does_not_change_result(self) -> None:
        snapshot = stable_range_snapshot(long_reversion_sequence())
        base = evaluate_snapshot(snapshot)
        features_with_event_direction = {
            **snapshot.features,
            "event.directionBias": snapshot.features["sessionVwap"].model_copy(update={"value": "short"}),
        }
        changed = evaluate_snapshot(snapshot.model_copy(update={"features": features_with_event_direction}))

        self.assertEqual(changed.signal, base.signal)
        self.assertEqual(changed.confidence, base.confidence)
        self.assertEqual(changed.reasonCodes, base.reasonCodes)


if __name__ == "__main__":
    unittest.main()
