from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.market_condition import classify_market_condition
from backend.app.algorithms.weighted_voting.models import (
    WeightedCandle,
    WeightedEventRiskLevel,
    WeightedLiquidityLevel,
    WeightedMarketQuality,
    WeightedMarketSnapshot,
    WeightedSessionPhase,
    WeightedSide,
    WeightedTrendDirection,
    WeightedVolatilityLevel,
)


BASE_TS = datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc)


class WeightedVotingMarketConditionTest(unittest.TestCase):
    def test_classifier_uses_raw_market_data_and_does_not_output_trade_direction(self) -> None:
        condition = classify_market_condition(snapshot(candles=trend_candles(slope_per_candle=0.05), bid=101.0, ask=101.02))

        self.assertEqual(condition.trend_direction, WeightedTrendDirection.STRONG_UPTREND.value)
        self.assertEqual(condition.volatility_level, WeightedVolatilityLevel.NORMAL.value)
        self.assertEqual(condition.liquidity_level, WeightedLiquidityLevel.GOOD.value)
        self.assertEqual(condition.session_phase, WeightedSessionPhase.MORNING.value)
        self.assertEqual(condition.event_risk, WeightedEventRiskLevel.NONE.value)
        self.assertEqual(condition.market_quality, WeightedMarketQuality.CLEAN.value)
        self.assertGreater(condition.confidence, 0.5)
        self.assertIn("trend_slope", condition.condition_inputs)
        self.assertNotIn("signal", type(condition).model_fields)
        self.assertNotIn("proposed_side", type(condition).model_fields)
        self.assertNotIn(condition.trend_direction, (WeightedSide.BUY.value, WeightedSide.SELL.value))

    def test_extreme_volatility_poor_liquidity_and_blocked_event_apply_immediately(self) -> None:
        clean = classify_market_condition(snapshot(candles=trend_candles(slope_per_candle=0.02), bid=100.0, ask=100.02))
        blocked = classify_market_condition(
            snapshot(candles=volatile_gap_candles(), bid=104.0, ask=104.50),
            previous_condition=clean,
        )

        self.assertEqual(blocked.volatility_level, WeightedVolatilityLevel.EXTREME.value)
        self.assertEqual(blocked.liquidity_level, WeightedLiquidityLevel.POOR.value)
        self.assertEqual(blocked.event_risk, WeightedEventRiskLevel.BLOCKED.value)
        self.assertEqual(blocked.market_quality, WeightedMarketQuality.UNSTABLE.value)
        self.assertIn("weighted_voting.market_condition.immediate_deterioration", blocked.reason_codes)

    def test_exposure_improvement_requires_completed_confirmations(self) -> None:
        config = WeightedVotingConfig(market_condition_hysteresis_confirmations=3)
        unstable = classify_market_condition(snapshot(candles=volatile_gap_candles(), bid=104.0, ask=104.50), config=config)

        first = classify_market_condition(snapshot(candles=trend_candles(slope_per_candle=0.02), bid=101.0, ask=101.02), config=config, previous_condition=unstable)
        second = classify_market_condition(snapshot(candles=trend_candles(slope_per_candle=0.02, end_timestamp=BASE_TS + timedelta(minutes=1)), bid=101.0, ask=101.02), config=config, previous_condition=first)
        third = classify_market_condition(snapshot(candles=trend_candles(slope_per_candle=0.02, end_timestamp=BASE_TS + timedelta(minutes=2)), bid=101.0, ask=101.02), config=config, previous_condition=second)

        self.assertEqual(first.market_quality, WeightedMarketQuality.UNSTABLE.value)
        self.assertIn("weighted_voting.market_condition.hysteresis_hold", first.reason_codes)
        self.assertEqual(first.pending_confirmation_count, 1)
        self.assertEqual(second.market_quality, WeightedMarketQuality.UNSTABLE.value)
        self.assertEqual(second.pending_confirmation_count, 2)
        self.assertEqual(third.market_quality, WeightedMarketQuality.CLEAN.value)
        self.assertIn("weighted_voting.market_condition.hysteresis_confirmed", third.reason_codes)
        self.assertEqual(third.pending_confirmation_count, 0)

    def test_sideways_reduced_liquidity_and_session_dimensions_are_stable(self) -> None:
        condition = classify_market_condition(snapshot(candles=sideways_candles(), bid=100.0, ask=100.18))

        self.assertEqual(condition.trend_direction, WeightedTrendDirection.SIDEWAYS.value)
        self.assertEqual(condition.liquidity_level, WeightedLiquidityLevel.REDUCED.value)
        self.assertEqual(condition.session_phase, WeightedSessionPhase.MORNING.value)
        self.assertIn(condition.volatility_level, (WeightedVolatilityLevel.LOW.value, WeightedVolatilityLevel.NORMAL.value))


def snapshot(*, candles: tuple[WeightedCandle, ...], bid: float, ask: float) -> WeightedMarketSnapshot:
    return WeightedMarketSnapshot(
        symbol="SPY",
        data_timestamp=candles[-1].timestamp,
        one_minute_candles=candles,
        bid=bid,
        ask=ask,
        explanation="Synthetic raw market snapshot.",
    )


def trend_candles(*, slope_per_candle: float, end_timestamp: datetime = BASE_TS) -> tuple[WeightedCandle, ...]:
    start = end_timestamp - timedelta(minutes=60)
    candles = [candle(start - timedelta(minutes=1), 100.0, 100.1, 99.9, 100.0, 100000.0)]
    price = 100.0
    for index in range(61):
        timestamp = start + timedelta(minutes=index)
        open_price = price
        close = price + slope_per_candle
        high = max(open_price, close) + 0.20
        low = min(open_price, close) - 0.20
        candles.append(candle(timestamp, open_price, high, low, close, 100000.0))
        price = close
    return tuple(candles)


def sideways_candles() -> tuple[WeightedCandle, ...]:
    start = BASE_TS - timedelta(minutes=60)
    candles = [candle(start - timedelta(minutes=1), 100.0, 100.1, 99.9, 100.0, 100000.0)]
    for index in range(61):
        timestamp = start + timedelta(minutes=index)
        close = 100.0 + (0.02 if index % 2 == 0 else -0.02)
        candles.append(candle(timestamp, 100.0, 100.12, 99.88, close, 50000.0 if index < 60 else 25000.0))
    return tuple(candles)


def volatile_gap_candles() -> tuple[WeightedCandle, ...]:
    start = BASE_TS - timedelta(minutes=60)
    candles = [candle(start - timedelta(minutes=1), 100.0, 100.1, 99.9, 100.0, 100000.0)]
    price = 104.5
    for index in range(61):
        timestamp = start + timedelta(minutes=index)
        open_price = price
        close = price + (1.5 if index % 2 == 0 else -1.4)
        high = max(open_price, close) + 2.0
        low = min(open_price, close) - 2.0
        candles.append(candle(timestamp, open_price, high, low, close, 100000.0 if index < 60 else 10000.0))
        price = close
    return tuple(candles)


def candle(timestamp: datetime, open_price: float, high: float, low: float, close: float, volume: float) -> WeightedCandle:
    return WeightedCandle(timestamp=timestamp, open=open_price, high=high, low=low, close=close, volume=volume)


if __name__ == "__main__":
    unittest.main()
