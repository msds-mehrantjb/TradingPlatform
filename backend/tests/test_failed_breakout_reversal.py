from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from backend.app.domain.feature_engine import (
    BidAskQuote,
    FeatureQuality,
    MarketCandle,
    OpeningRangeLevels,
    PointInTimeFeatureEngine,
    PointInTimeFeatureRequest,
    PremarketLevels,
    PriorDayOHLC,
)
from backend.app.domain.models import Signal
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.directional.failed_breakout_reversal import FailedBreakoutReversalStrategy
from backend.app.strategies.registry import resolve_strategy


SESSION_DATE = date(2026, 1, 5)
OPEN_UTC = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def candle_at(minute: int, open_price: float, high: float, low: float, close: float, volume: float = 100000) -> MarketCandle:
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


def base_session(count: int = 38) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        base = 100.0 + (0.02 if minute % 2 else -0.02)
        rows.append(candle_at(minute, base, 100.16, 99.84, 100.0 - (0.01 if minute % 2 else -0.01)))
    return rows


def normal_breakout_holds_sequence() -> list[MarketCandle]:
    rows = base_session()
    rows.extend(
        [
            candle_at(38, 100.08, 100.55, 100.05, 100.38, 140000),
            candle_at(39, 100.38, 100.62, 100.30, 100.50, 150000),
        ]
    )
    return rows


def failed_upside_sequence() -> list[MarketCandle]:
    rows = base_session()
    rows.extend(
        [
            candle_at(38, 100.08, 100.58, 100.04, 100.34, 150000),
            candle_at(39, 100.34, 100.36, 99.92, 100.02, 160000),
        ]
    )
    return rows


def failed_downside_sequence() -> list[MarketCandle]:
    rows = base_session()
    rows.extend(
        [
            candle_at(38, 99.92, 99.96, 99.42, 99.66, 150000),
            candle_at(39, 99.66, 100.08, 99.64, 99.98, 160000),
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
        rows.append(candle_at(minute, base, base + 0.08, base - 0.08, base + 0.01).model_copy(update={"symbol": symbol}))
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
        priorDayOHLC=PriorDayOHLC(sessionDate=date(2026, 1, 2), open=100, high=100.65, low=99.35, close=100),
        premarket=PremarketLevels(high=100.45, low=99.55, sourceTimestamp=OPEN_UTC - timedelta(minutes=1)),
        openingRange=OpeningRangeLevels(
            high=100.20,
            low=99.80,
            startTimestamp=OPEN_UTC,
            endTimestamp=OPEN_UTC + timedelta(minutes=14),
        ),
        quote=BidAskQuote(bid=candles[-1].close - 0.01, ask=candles[-1].close + 0.01, timestamp=evaluation),
        breadthComponents={"XLK": auxiliary_candles("XLK")},
    )


def evaluate(candles: list[MarketCandle]):
    snapshot = PointInTimeFeatureEngine().compute(request_for(candles))
    return evaluate_snapshot(snapshot)


def evaluate_snapshot(snapshot):
    strategy = FailedBreakoutReversalStrategy()
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("failed_breakout_reversal"),
        featureSnapshot=snapshot,
        configurationHash=strategy.config.configurationHash,
    )
    return strategy.evaluate(context)


class FailedBreakoutReversalTest(unittest.TestCase):
    def test_normal_breakout_holding_outside_does_not_trigger_reversal(self) -> None:
        result = evaluate(normal_breakout_holds_sequence())

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertNotIn("failed_breakout.reversal_confirmed", result.reasonCodes)

    def test_failed_upside_breakout_can_produce_sell(self) -> None:
        result = evaluate(failed_upside_sequence())

        self.assertEqual(result.signal, Signal.SELL.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("failed_breakout.reversal_confirmed", result.reasonCodes)
        self.assertIn("level:opening_range_high", result.reasonCodes)

    def test_failed_downside_breakout_can_produce_buy(self) -> None:
        result = evaluate(failed_downside_sequence())

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("failed_breakout.reversal_confirmed", result.reasonCodes)
        self.assertIn("level:opening_range_low", result.reasonCodes)

    def test_missing_required_data_generates_hold_not_eligible(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(failed_upside_sequence()))
        features = {
            **snapshot.features,
            "spreadBasisPoints": snapshot.features["spreadBasisPoints"].model_copy(update={"quality": FeatureQuality.MISSING}),
        }
        result = evaluate_snapshot(snapshot.model_copy(update={"features": features}))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertFalse(result.dataReady)
        self.assertIn("required_data_unavailable", result.reasonCodes)

    def test_changing_only_session_direction_does_not_change_setup(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(failed_upside_sequence()))
        base = evaluate_snapshot(snapshot)
        features_with_session_direction = {
            **snapshot.features,
            "session.directionBias": snapshot.features["spreadDollars"].model_copy(update={"value": "long"}),
        }
        changed = evaluate_snapshot(snapshot.model_copy(update={"features": features_with_session_direction}))

        self.assertEqual(changed.signal, base.signal)
        self.assertEqual(changed.confidence, base.confidence)
        self.assertEqual(changed.reasonCodes, base.reasonCodes)


if __name__ == "__main__":
    unittest.main()
