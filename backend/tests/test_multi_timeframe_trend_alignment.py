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
from backend.app.strategies.directional.multi_timeframe_trend_alignment import (
    MultiTimeframeTrendAlignmentStrategy,
)
from backend.app.strategies.registry import resolve_strategy


SESSION_DATE = date(2026, 1, 5)
EVALUATION = datetime(2026, 1, 5, 15, 29, tzinfo=UTC)


def synthetic_candles(
    *,
    symbol: str,
    timeframe: str,
    count: int = 80,
    step_minutes: int = 1,
    end: datetime = EVALUATION,
    start_price: float = 100,
    drift: float = 0.05,
    wave: float = 0.0,
) -> list[MarketCandle]:
    start = end - timedelta(minutes=step_minutes * (count - 1))
    rows: list[MarketCandle] = []
    for index in range(count):
        timestamp = start + timedelta(minutes=step_minutes * index)
        base = start_price + index * drift + ((-1) ** index * wave)
        open_price = base - drift * 0.2
        close = base + drift * 0.2
        high = max(open_price, close) + 0.08
        low = min(open_price, close) - 0.08
        rows.append(
            MarketCandle(
                timestamp=timestamp,
                open=max(0.01, open_price),
                high=max(0.01, high),
                low=max(0.01, low),
                close=max(0.01, close),
                volume=100000 + index * 500,
                tradeCount=1000 + index,
                provider="fixture",
                symbol=symbol,
                timeframe=timeframe,  # type: ignore[arg-type]
            )
        )
    return rows


def feature_request(*, drift_1m: float, drift_5m: float, drift_15m: float, session_vwap: float) -> PointInTimeFeatureRequest:
    return PointInTimeFeatureRequest(
        evaluationTimestamp=EVALUATION,
        sessionDate=SESSION_DATE,
        spy1mCandles=synthetic_candles(symbol="SPY", timeframe="1Min", step_minutes=1, drift=drift_1m),
        spy5mCandles=synthetic_candles(symbol="SPY", timeframe="5Min", step_minutes=5, drift=drift_5m),
        spy15mCandles=synthetic_candles(symbol="SPY", timeframe="15Min", step_minutes=15, drift=drift_15m),
        sessionVwap=session_vwap,
        sessionVwapTimestamp=EVALUATION,
        qqqAlignedCandles=synthetic_candles(symbol="QQQ", timeframe="1Min", step_minutes=1, drift=0.02),
        iwmAlignedCandles=synthetic_candles(symbol="IWM", timeframe="1Min", step_minutes=1, drift=0.02),
        priorDayOHLC=PriorDayOHLC(sessionDate=date(2026, 1, 2), open=99, high=101, low=98, close=100),
        quote=BidAskQuote(bid=103, ask=103.02, timestamp=EVALUATION),
        breadthComponents={
            "XLK": synthetic_candles(symbol="XLK", timeframe="1Min", step_minutes=1, drift=0.02),
        },
    )


def evaluate_request(request: PointInTimeFeatureRequest):
    snapshot = PointInTimeFeatureEngine().compute(request)
    strategy = MultiTimeframeTrendAlignmentStrategy()
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("multi_timeframe_trend_alignment"),
        featureSnapshot=snapshot,
        configurationHash=strategy.config.configurationHash,
    )
    return strategy.evaluate(context)


class MultiTimeframeTrendAlignmentTest(unittest.TestCase):
    def test_three_bullish_timeframes_generate_buy(self) -> None:
        result = evaluate_request(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertGreater(result.confidence, 0.6)
        self.assertIsNotNone(result.structuralInvalidationPrice)
        self.assertIn("multi_timeframe.bullish_alignment", result.reasonCodes)
        self.assertIn("spy1mEma9", result.features)
        self.assertIn("sessionVwap", result.features)

    def test_three_bearish_timeframes_generate_sell(self) -> None:
        result = evaluate_request(feature_request(drift_1m=-0.05, drift_5m=-0.04, drift_15m=-0.03, session_vwap=101))

        self.assertEqual(result.signal, Signal.SELL.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertGreater(result.confidence, 0.6)
        self.assertIsNotNone(result.structuralInvalidationPrice)
        self.assertIn("multi_timeframe.bearish_alignment", result.reasonCodes)

    def test_material_timeframe_conflict_generates_hold(self) -> None:
        result = evaluate_request(feature_request(drift_1m=0.05, drift_5m=-0.05, drift_15m=0.0, session_vwap=101))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("multi_timeframe.conflict", result.reasonCodes)

    def test_missing_data_generates_hold_not_eligible(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        features = {
            **snapshot.features,
            "spy5mEma20": snapshot.features["spy5mEma20"].model_copy(update={"quality": FeatureQuality.MISSING}),
        }
        snapshot = snapshot.model_copy(update={"features": features})
        strategy = MultiTimeframeTrendAlignmentStrategy()
        result = strategy.evaluate(
            StrategyEvaluationContext(
                registryEntry=resolve_strategy("multi_timeframe_trend_alignment"),
                featureSnapshot=snapshot,
                configurationHash=strategy.config.configurationHash,
            )
        )

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertFalse(result.dataReady)
        self.assertIn("required_data_unavailable", result.reasonCodes)

    def test_two_bullish_one_weak_bearish_boundary_can_generate_buy(self) -> None:
        result = evaluate_request(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.0, session_vwap=100.2))

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertIn("aligned_timeframes:2", result.reasonCodes)

    def test_changing_only_event_direction_field_does_not_change_result(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        strategy = MultiTimeframeTrendAlignmentStrategy()
        context = StrategyEvaluationContext(
            registryEntry=resolve_strategy("multi_timeframe_trend_alignment"),
            featureSnapshot=snapshot,
            configurationHash=strategy.config.configurationHash,
        )
        base = strategy.evaluate(context)

        features_with_event_direction = {
            **snapshot.features,
            "event.directionBias": snapshot.features["sessionVwap"].model_copy(update={"value": "short"}),
        }
        changed_snapshot = snapshot.model_copy(update={"features": features_with_event_direction})
        changed = strategy.evaluate(
            StrategyEvaluationContext(
                registryEntry=resolve_strategy("multi_timeframe_trend_alignment"),
                featureSnapshot=changed_snapshot,
                configurationHash=strategy.config.configurationHash,
            )
        )

        self.assertEqual(changed.signal, base.signal)
        self.assertEqual(changed.confidence, base.confidence)
        self.assertEqual(changed.reasonCodes, base.reasonCodes)


if __name__ == "__main__":
    unittest.main()
