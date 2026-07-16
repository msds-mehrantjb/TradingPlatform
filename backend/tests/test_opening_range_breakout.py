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
from backend.app.strategies.directional.opening_range_breakout import (
    OpeningRangeBreakoutConfig,
    OpeningRangeBreakoutStrategy,
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


def opening_range_base(count: int = 22) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(min(count, 15)):
        rows.append(candle_at(minute, 100.00, 100.20, 99.80, 100.00 + (0.02 if minute % 2 else -0.02), 100000))
    for minute in range(15, count):
        rows.append(candle_at(minute, 100.00, 100.16, 99.86, 100.02 if minute % 2 else 99.98, 95000))
    return rows


def bullish_breakout_sequence() -> list[MarketCandle]:
    rows = opening_range_base(21)
    rows.append(candle_at(21, 100.08, 100.48, 100.05, 100.42, 180000))
    return rows


def bearish_breakout_sequence() -> list[MarketCandle]:
    rows = opening_range_base(21)
    rows.append(candle_at(21, 99.92, 99.95, 99.50, 99.58, 180000))
    return rows


def wick_only_sequence() -> list[MarketCandle]:
    rows = opening_range_base(21)
    rows.append(candle_at(21, 100.08, 100.55, 100.02, 100.18, 180000))
    return rows


def premature_breakout_sequence() -> list[MarketCandle]:
    rows = opening_range_base(4)
    rows.append(candle_at(4, 100.05, 100.70, 100.00, 100.62, 180000))
    return rows


def five_minute_breakout_sequence() -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(5):
        rows.append(candle_at(minute, 100.00, 100.12, 99.90, 100.00, 100000))
    for minute in range(5, 21):
        rows.append(candle_at(minute, 100.00, 100.08, 99.94, 100.02 if minute % 2 else 99.98, 95000))
    rows.append(candle_at(21, 100.04, 100.34, 100.02, 100.30, 180000))
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


def request_for(candles: list[MarketCandle]) -> PointInTimeFeatureRequest:
    evaluation = candles[-1].timestamp
    return PointInTimeFeatureRequest(
        evaluationTimestamp=evaluation,
        sessionDate=SESSION_DATE,
        spy1mCandles=candles,
        spy5mCandles=timeframe_history(end=evaluation, step_minutes=5, timeframe="5Min"),
        spy15mCandles=timeframe_history(end=evaluation, step_minutes=15, timeframe="15Min"),
        sessionVwap=sum(c.close * c.volume for c in candles) / sum(c.volume for c in candles),
        sessionVwapTimestamp=evaluation,
        qqqAlignedCandles=auxiliary_candles("QQQ"),
        iwmAlignedCandles=auxiliary_candles("IWM"),
        priorDayOHLC=PriorDayOHLC(sessionDate=date(2026, 1, 2), open=99, high=101, low=98, close=100),
        quote=BidAskQuote(bid=candles[-1].close - 0.01, ask=candles[-1].close + 0.01, timestamp=evaluation),
        breadthComponents={"XLK": auxiliary_candles("XLK")},
    )


def evaluate(candles: list[MarketCandle], config: OpeningRangeBreakoutConfig | None = None):
    snapshot = PointInTimeFeatureEngine().compute(request_for(candles))
    return evaluate_snapshot(snapshot, config)


def evaluate_snapshot(snapshot, config: OpeningRangeBreakoutConfig | None = None):
    strategy = OpeningRangeBreakoutStrategy(config)
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("opening_range_breakout"),
        featureSnapshot=snapshot,
        configurationHash=strategy.config.configurationHash,
    )
    return strategy.evaluate(context)


def setup_id(result) -> str | None:
    for code in result.reasonCodes:
        if code.startswith("setup_id:"):
            return code.removeprefix("setup_id:")
    return None


class OpeningRangeBreakoutTest(unittest.TestCase):
    def test_wick_beyond_range_without_confirming_close_holds(self) -> None:
        result = evaluate(wick_only_sequence())

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("opening_range.no_confirmed_breakout", result.reasonCodes)

    def test_breakout_before_range_completion_is_impossible(self) -> None:
        result = evaluate(premature_breakout_sequence())

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)

    def test_bullish_breakout_fixture_produces_buy(self) -> None:
        result = evaluate(bullish_breakout_sequence())

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("opening_range.breakout_confirmed", result.reasonCodes)
        self.assertIsNotNone(setup_id(result))

    def test_bearish_breakout_fixture_produces_sell(self) -> None:
        result = evaluate(bearish_breakout_sequence())

        self.assertEqual(result.signal, Signal.SELL.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("opening_range.breakout_confirmed", result.reasonCodes)

    def test_five_minute_opening_range_definition_is_supported(self) -> None:
        result = evaluate(five_minute_breakout_sequence(), OpeningRangeBreakoutConfig(openingRangeMinutes=5))

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)

    def test_later_bar_does_not_repeat_same_breakout_entry(self) -> None:
        first = evaluate(bullish_breakout_sequence())
        later = bullish_breakout_sequence()
        later.append(candle_at(22, 100.42, 100.55, 100.30, 100.50, 140000))
        repeated = evaluate(later)

        self.assertEqual(repeated.signal, Signal.HOLD.value)
        self.assertFalse(repeated.eligible)
        self.assertIn("opening_range.already_completed", repeated.reasonCodes)
        self.assertEqual(setup_id(repeated), setup_id(first))

    def test_duplicate_timestamp_bars_share_one_setup_id(self) -> None:
        base = bullish_breakout_sequence()
        duplicate = [*base, base[-1]]
        first = evaluate(base)
        duplicate_result = evaluate(duplicate)

        self.assertEqual(duplicate_result.signal, Signal.BUY.value)
        self.assertEqual(setup_id(duplicate_result), setup_id(first))

    def test_missing_required_data_generates_hold_not_eligible(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(bullish_breakout_sequence()))
        features = {
            **snapshot.features,
            "spreadDollars": snapshot.features["spreadDollars"].model_copy(update={"quality": FeatureQuality.MISSING}),
        }
        result = evaluate_snapshot(snapshot.model_copy(update={"features": features}))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertFalse(result.dataReady)
        self.assertIn("required_data_unavailable", result.reasonCodes)

    def test_changing_only_event_direction_field_does_not_change_result(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(bullish_breakout_sequence()))
        base = evaluate_snapshot(snapshot)
        features_with_event_direction = {
            **snapshot.features,
            "event.directionBias": snapshot.features["spreadDollars"].model_copy(update={"value": "short"}),
        }
        changed = evaluate_snapshot(snapshot.model_copy(update={"features": features_with_event_direction}))

        self.assertEqual(changed.signal, base.signal)
        self.assertEqual(changed.confidence, base.confidence)
        self.assertEqual(changed.reasonCodes, base.reasonCodes)


if __name__ == "__main__":
    unittest.main()
