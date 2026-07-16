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
from backend.app.strategies.directional.vwap_trend_continuation import (
    VwapTrendContinuationStrategy,
)
from backend.app.strategies.registry import resolve_strategy


SESSION_DATE = date(2026, 1, 5)
OPEN_UTC = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def candle_at(minute: int, open_price: float, high: float, low: float, close: float, volume: float) -> MarketCandle:
    return MarketCandle(
        timestamp=OPEN_UTC + timedelta(minutes=minute),
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


def warmup(direction: str = "up", count: int = 34) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        if direction == "down":
            base = 101.0 - minute * 0.012
            rows.append(candle_at(minute, base + 0.01, base + 0.05, base - 0.08, base - 0.01, 100000))
        elif direction == "flat":
            base = 100 + (0.03 if minute % 2 else -0.03)
            rows.append(candle_at(minute, base, base + 0.08, base - 0.08, 100 - (0.02 if minute % 2 else -0.02), 100000))
        else:
            base = 100.0 + minute * 0.012
            rows.append(candle_at(minute, base - 0.01, base + 0.08, base - 0.05, base + 0.01, 100000))
    return rows


def valid_buy_sequence() -> list[MarketCandle]:
    rows = warmup("up")
    rows.extend(
        [
            candle_at(34, 100.45, 100.80, 100.40, 100.76, 105000),
            candle_at(35, 100.76, 100.92, 100.72, 100.80, 90000),
            candle_at(36, 100.80, 101.08, 100.78, 101.02, 150000),
        ]
    )
    return rows


def valid_sell_sequence() -> list[MarketCandle]:
    rows = warmup("down")
    rows.extend(
        [
            candle_at(34, 100.55, 100.58, 100.20, 100.24, 105000),
            candle_at(35, 100.24, 100.32, 100.08, 100.20, 90000),
            candle_at(36, 100.20, 100.22, 99.90, 99.98, 150000),
        ]
    )
    return rows


def flat_choppy_sequence() -> list[MarketCandle]:
    rows = warmup("flat", count=37)
    return rows


def extended_buy_sequence() -> list[MarketCandle]:
    rows = valid_buy_sequence()
    rows[-1] = candle_at(36, 100.80, 101.86, 100.78, 101.78, 150000)
    return rows


def no_pullback_trend_sequence() -> list[MarketCandle]:
    rows = warmup("up")
    rows.extend(
        [
            candle_at(34, 100.45, 100.70, 100.42, 100.66, 105000),
            candle_at(35, 100.66, 100.88, 100.61, 100.84, 105000),
            candle_at(36, 100.84, 101.08, 100.80, 101.02, 150000),
        ]
    )
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
        rows.append(candle_at(minute, base, base + 0.08, base - 0.08, base + 0.01, 100000).model_copy(update={"symbol": symbol}))
    return rows


def request_for(candles: list[MarketCandle], *, session_vwap: float) -> PointInTimeFeatureRequest:
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


def evaluate(candles: list[MarketCandle], *, session_vwap: float):
    snapshot = PointInTimeFeatureEngine().compute(request_for(candles, session_vwap=session_vwap))
    return evaluate_snapshot(snapshot)


def evaluate_snapshot(snapshot):
    strategy = VwapTrendContinuationStrategy()
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("vwap_trend_continuation"),
        featureSnapshot=snapshot,
        configurationHash=strategy.config.configurationHash,
    )
    return strategy.evaluate(context)


class VwapTrendContinuationTest(unittest.TestCase):
    def test_flat_vwap_plus_choppy_price_produces_hold(self) -> None:
        result = evaluate(flat_choppy_sequence(), session_vwap=100.0)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("vwap_continuation.flat_vwap", result.reasonCodes)

    def test_valid_pullback_and_reclaim_in_uptrend_can_produce_buy(self) -> None:
        result = evaluate(valid_buy_sequence(), session_vwap=100.76)

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("vwap_continuation.completed", result.reasonCodes)
        self.assertIn("distanceFromVwapAtr", result.features)

    def test_valid_pullback_and_rejection_in_downtrend_can_produce_sell(self) -> None:
        result = evaluate(valid_sell_sequence(), session_vwap=100.24)

        self.assertEqual(result.signal, Signal.SELL.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("vwap_continuation.completed", result.reasonCodes)

    def test_excessively_extended_entry_is_rejected(self) -> None:
        result = evaluate(extended_buy_sequence(), session_vwap=100.76)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("vwap_continuation.entry_extended", result.reasonCodes)

    def test_above_vwap_without_pullback_does_not_activate(self) -> None:
        result = evaluate(no_pullback_trend_sequence(), session_vwap=99.0)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("vwap_continuation.no_pullback", result.reasonCodes)

    def test_missing_required_data_generates_hold_not_eligible(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(valid_buy_sequence(), session_vwap=100.76))
        features = {
            **snapshot.features,
            "sessionVwapSlope": snapshot.features["sessionVwapSlope"].model_copy(update={"quality": FeatureQuality.MISSING}),
        }
        result = evaluate_snapshot(snapshot.model_copy(update={"features": features}))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertFalse(result.dataReady)
        self.assertIn("required_data_unavailable", result.reasonCodes)

    def test_changing_only_event_direction_field_does_not_change_result(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(valid_buy_sequence(), session_vwap=100.76))
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
