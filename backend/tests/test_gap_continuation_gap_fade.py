from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from typing import Any

from backend.app.domain.feature_engine import (
    BidAskQuote,
    FeatureQuality,
    MarketCandle,
    PointInTimeFeatureEngine,
    PointInTimeFeatureRequest,
    PremarketLevels,
    PriorDayOHLC,
)
from backend.app.domain.models import Signal
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.directional.gap_continuation_gap_fade import (
    GapContinuationFadeStrategy,
)
from backend.app.strategies.registry import resolve_strategy


SESSION_DATE = date(2026, 1, 5)
NEXT_SESSION_DATE = date(2026, 1, 6)
OPEN_UTC = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
NEXT_OPEN_UTC = datetime(2026, 1, 6, 14, 30, tzinfo=UTC)


def candle_at(
    open_utc: datetime,
    minute: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100000,
    trade_count: int = 1000,
) -> MarketCandle:
    return MarketCandle(
        timestamp=open_utc + timedelta(minutes=minute),
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


def gap_up_continuation_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    rows = [
        candle_at(open_utc, 0, 101.00, 101.20, 100.92, 101.12, 240000, 2200),
        candle_at(open_utc, 1, 101.12, 101.30, 101.02, 101.20, 230000, 2200),
        candle_at(open_utc, 2, 101.20, 101.36, 101.10, 101.28, 220000, 2200),
        candle_at(open_utc, 3, 101.28, 101.42, 101.20, 101.36, 210000, 2200),
        candle_at(open_utc, 4, 101.36, 101.48, 101.26, 101.42, 200000, 2200),
    ]
    for minute in range(5, 21):
        base = 101.42 + (minute - 4) * 0.015
        rows.append(candle_at(open_utc, minute, base - 0.02, base + 0.08, base - 0.06, base + 0.02, 100000, 1300))
    return rows


def gap_up_fade_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    rows = [
        candle_at(open_utc, 0, 101.00, 101.12, 100.80, 100.90, 210000, 2000),
        candle_at(open_utc, 1, 100.90, 100.98, 100.70, 100.78, 200000, 2000),
        candle_at(open_utc, 2, 100.78, 100.84, 100.55, 100.64, 190000, 2000),
        candle_at(open_utc, 3, 100.64, 100.70, 100.42, 100.50, 180000, 2000),
        candle_at(open_utc, 4, 100.50, 100.58, 100.30, 100.38, 170000, 2000),
    ]
    for minute in range(5, 21):
        base = 100.38 - (minute - 4) * 0.025
        rows.append(candle_at(open_utc, minute, base + 0.04, base + 0.08, base - 0.08, base - 0.02, 95000, 1200))
    return rows


def small_gap_sequence(open_utc: datetime = OPEN_UTC) -> list[MarketCandle]:
    rows = [
        candle_at(open_utc, 0, 100.08, 100.16, 99.98, 100.06, 180000, 1800),
        candle_at(open_utc, 1, 100.06, 100.12, 99.98, 100.02, 170000, 1800),
        candle_at(open_utc, 2, 100.02, 100.10, 99.94, 100.00, 160000, 1800),
        candle_at(open_utc, 3, 100.00, 100.08, 99.92, 99.98, 150000, 1800),
        candle_at(open_utc, 4, 99.98, 100.06, 99.90, 100.02, 140000, 1800),
    ]
    for minute in range(5, 21):
        rows.append(candle_at(open_utc, minute, 100.0, 100.08, 99.92, 100.0, 100000, 1200))
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


def auxiliary_candles(symbol: str, open_utc: datetime, count: int = 90, *, direction: str = "up") -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        base = 100 + (minute * 0.01 if direction == "up" else -minute * 0.01)
        rows.append(candle_at(open_utc, minute, base, base + 0.08, base - 0.08, base + (0.01 if direction == "up" else -0.01)).model_copy(update={"symbol": symbol}))
    return rows


def request_for(
    candles: list[MarketCandle],
    *,
    session_date: date = SESSION_DATE,
    open_utc: datetime = OPEN_UTC,
    prior_close: float = 100.0,
    market_direction: str = "up",
    event_state: dict[str, Any] | None = None,
) -> PointInTimeFeatureRequest:
    evaluation = candles[-1].timestamp
    return PointInTimeFeatureRequest(
        evaluationTimestamp=evaluation,
        sessionDate=session_date,
        spy1mCandles=candles,
        spy5mCandles=timeframe_history(end=evaluation, step_minutes=5, timeframe="5Min"),
        spy15mCandles=timeframe_history(end=evaluation, step_minutes=15, timeframe="15Min"),
        sessionVwap=100.0,
        sessionVwapTimestamp=evaluation,
        qqqAlignedCandles=auxiliary_candles("QQQ", open_utc, direction=market_direction),
        iwmAlignedCandles=auxiliary_candles("IWM", open_utc, direction=market_direction),
        priorDayOHLC=PriorDayOHLC(sessionDate=session_date - timedelta(days=3), open=99, high=101, low=98, close=prior_close),
        premarket=PremarketLevels(high=100.80, low=99.40, sourceTimestamp=open_utc - timedelta(minutes=1)),
        quote=BidAskQuote(bid=candles[-1].close - 0.01, ask=candles[-1].close + 0.01, timestamp=evaluation),
        economicEventState=event_state or {"impact": "low", "riskScore": 0.1},
        breadthComponents={"XLK": auxiliary_candles("XLK", open_utc, direction=market_direction)},
    )


def with_feature(snapshot, name: str, value: Any, quality: FeatureQuality = FeatureQuality.READY):
    features = {
        **snapshot.features,
        name: snapshot.features[name].model_copy(update={"value": value, "quality": quality}),
    }
    return snapshot.model_copy(update={"features": features})


def configured_snapshot(
    candles: list[MarketCandle],
    *,
    session_date: date = SESSION_DATE,
    open_utc: datetime = OPEN_UTC,
    prior_close: float = 100.0,
    market_direction: str = "up",
    event_state: dict[str, Any] | None = None,
):
    snapshot = PointInTimeFeatureEngine().compute(
        request_for(
            candles,
            session_date=session_date,
            open_utc=open_utc,
            prior_close=prior_close,
            market_direction=market_direction,
            event_state=event_state,
        )
    )
    snapshot = with_feature(snapshot, "spy1mAtr14", 0.55)
    snapshot = with_feature(snapshot, "spy1mAdx14", 18.0)
    snapshot = with_feature(snapshot, "spy1mRelativeVolume", 1.15)
    return snapshot


def evaluate_snapshot(snapshot):
    strategy = GapContinuationFadeStrategy()
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("gap_continuation_gap_fade"),
        featureSnapshot=snapshot,
        configurationHash=strategy.config.configurationHash,
    )
    return strategy.evaluate(context)


class GapContinuationFadeTest(unittest.TestCase):
    def test_no_meaningful_gap_produces_hold(self) -> None:
        result = evaluate_snapshot(configured_snapshot(small_gap_sequence()))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("gap.no_meaningful_gap", result.reasonCodes)

    def test_gap_continuation_can_produce_buy(self) -> None:
        result = evaluate_snapshot(configured_snapshot(gap_up_continuation_sequence(), market_direction="up"))

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("gap.gap_continuation", result.reasonCodes)
        self.assertNotIn("gap.gap_fade", result.reasonCodes)
        self.assertIn("gapPercent", result.features)

    def test_gap_fade_can_produce_sell(self) -> None:
        result = evaluate_snapshot(configured_snapshot(gap_up_fade_sequence(), market_direction="down"))

        self.assertEqual(result.signal, Signal.SELL.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("gap.gap_fade", result.reasonCodes)
        self.assertNotIn("gap.gap_continuation", result.reasonCodes)

    def test_strategy_resets_each_session(self) -> None:
        first_session = evaluate_snapshot(configured_snapshot(gap_up_continuation_sequence(), market_direction="up"))
        second_session = evaluate_snapshot(
            configured_snapshot(
                small_gap_sequence(NEXT_OPEN_UTC),
                session_date=NEXT_SESSION_DATE,
                open_utc=NEXT_OPEN_UTC,
                prior_close=100.0,
                market_direction="up",
            )
        )

        self.assertEqual(first_session.signal, Signal.BUY.value)
        self.assertIn("gap.gap_continuation", first_session.reasonCodes)
        self.assertEqual(second_session.signal, Signal.HOLD.value)
        self.assertFalse(second_session.eligible)
        self.assertIn("gap.no_meaningful_gap", second_session.reasonCodes)

    def test_outside_activation_window_holds(self) -> None:
        candles = gap_up_continuation_sequence()
        for minute in range(21, 91):
            base = 101.70 + minute * 0.002
            candles.append(candle_at(OPEN_UTC, minute, base, base + 0.04, base - 0.04, base + 0.01, 90000, 1000))
        result = evaluate_snapshot(configured_snapshot(candles, market_direction="up"))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("gap.outside_activation_window", result.reasonCodes)

    def test_missing_required_data_generates_hold_not_eligible(self) -> None:
        snapshot = configured_snapshot(gap_up_continuation_sequence(), market_direction="up")
        snapshot = with_feature(snapshot, "gapPercent", 1.0, FeatureQuality.MISSING)
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertFalse(result.dataReady)
        self.assertIn("required_data_unavailable", result.reasonCodes)

    def test_changing_only_event_direction_does_not_change_result(self) -> None:
        snapshot = configured_snapshot(
            gap_up_continuation_sequence(),
            market_direction="up",
            event_state={"impact": "low", "riskScore": 0.1, "directionBias": "short"},
        )
        base = evaluate_snapshot(snapshot)
        changed_event = snapshot.features["economicEventState"].model_copy(
            update={"value": {"impact": "low", "riskScore": 0.1, "directionBias": "long"}}
        )
        changed = evaluate_snapshot(snapshot.model_copy(update={"features": {**snapshot.features, "economicEventState": changed_event}}))

        self.assertEqual(changed.signal, base.signal)
        self.assertEqual(changed.confidence, base.confidence)
        self.assertEqual(changed.reasonCodes, base.reasonCodes)


if __name__ == "__main__":
    unittest.main()
