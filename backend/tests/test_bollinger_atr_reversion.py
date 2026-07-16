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
    PriorDayOHLC,
)
from backend.app.domain.models import Signal
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.directional.bollinger_atr_reversion import (
    BollingerAtrReversionStrategy,
)
from backend.app.strategies.registry import directional_voters_from, resolve_strategy, resolve_strategy_list


SESSION_DATE = date(2026, 1, 5)
OPEN_UTC = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
TEST_BANDS = {"upper": 100.50, "middle": 100.00, "lower": 99.50}


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
        center = 100.0 + (0.03 if minute % 2 else -0.03)
        rows.append(candle_at(minute, center - 0.01, center + 0.10, center - 0.10, center + 0.01))
    return rows


def buy_reentry_sequence() -> list[MarketCandle]:
    rows = range_base()
    rows.extend(
        [
            candle_at(34, 99.70, 99.76, 99.30, 99.38, 112000, 1300),
            candle_at(35, 99.38, 99.62, 99.20, 99.56, 90000, 1300),
        ]
    )
    return rows


def sell_reentry_sequence() -> list[MarketCandle]:
    rows = range_base()
    rows.extend(
        [
            candle_at(34, 100.30, 100.70, 100.24, 100.62, 112000, 1300),
            candle_at(35, 100.62, 100.80, 100.38, 100.44, 90000, 1300),
        ]
    )
    return rows


def no_reentry_sequence() -> list[MarketCandle]:
    rows = range_base()
    rows.extend(
        [
            candle_at(34, 99.70, 99.76, 99.30, 99.38, 112000, 1300),
            candle_at(35, 99.38, 99.48, 99.12, 99.30, 90000, 1300),
        ]
    )
    return rows


def band_walk_sequence() -> list[MarketCandle]:
    rows = range_base()
    rows.extend(
        [
            candle_at(34, 100.45, 100.78, 100.38, 100.66, 160000, 2100),
            candle_at(35, 100.66, 100.92, 100.58, 100.74, 170000, 2200),
            candle_at(36, 100.74, 101.02, 100.68, 100.86, 180000, 2300),
            candle_at(37, 100.86, 101.10, 100.80, 100.98, 190000, 2400),
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
        sessionVwap=100.0,
        sessionVwapTimestamp=evaluation,
        qqqAlignedCandles=auxiliary_candles("QQQ"),
        iwmAlignedCandles=auxiliary_candles("IWM"),
        priorDayOHLC=PriorDayOHLC(sessionDate=date(2026, 1, 2), open=99, high=101, low=98, close=100),
        quote=BidAskQuote(bid=candles[-1].close - 0.01, ask=candles[-1].close + 0.01, timestamp=evaluation),
        breadthComponents={"XLK": auxiliary_candles("XLK")},
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
    distance_atr: float,
    adx: float = 16.0,
    width_percentile: float = 0.45,
    relative_volume: float = 1.0,
):
    snapshot = PointInTimeFeatureEngine().compute(request_for(candles))
    snapshot = with_feature(snapshot, "spy1mBollingerBands", TEST_BANDS)
    snapshot = with_feature(snapshot, "spy1mBollingerWidthPercentile", width_percentile)
    snapshot = with_feature(snapshot, "spy1mAtr14", 0.25)
    snapshot = with_feature(snapshot, "spy1mAdx14", adx)
    snapshot = with_feature(snapshot, "distanceFromEma20Atr", distance_atr)
    snapshot = with_feature(snapshot, "spy1mRelativeVolume", relative_volume)
    return snapshot


def evaluate_snapshot(snapshot):
    strategy = BollingerAtrReversionStrategy()
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("bollinger_atr_reversion"),
        featureSnapshot=snapshot,
        configurationHash=strategy.config.configurationHash,
    )
    return strategy.evaluate(context)


class BollingerAtrReversionTest(unittest.TestCase):
    def test_old_aliases_resolve_to_one_canonical_strategy(self) -> None:
        resolved = resolve_strategy_list(
            [
                "Bollinger Band Reversion",
                "ATR Overextension Reversion",
                "Bollinger/ATR Reversion",
                "bollinger_atr_reversion",
            ]
        )

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].strategyId, "bollinger_atr_reversion")

    def test_old_aliases_cannot_cast_two_directional_votes(self) -> None:
        voters = directional_voters_from(["Bollinger Band Reversion", "ATR Overextension Reversion"])

        self.assertEqual(len(voters), 1)
        self.assertEqual(voters[0].strategyId, "bollinger_atr_reversion")

    def test_band_walk_in_strong_trend_is_suppressed(self) -> None:
        snapshot = configured_snapshot(
            band_walk_sequence(),
            distance_atr=2.0,
            adx=39.0,
            width_percentile=0.92,
            relative_volume=1.4,
        )
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("bollinger_atr_reversion.sustained_trend_expansion", result.reasonCodes)

    def test_lower_band_reentry_after_overextension_can_produce_buy(self) -> None:
        result = evaluate_snapshot(configured_snapshot(buy_reentry_sequence(), distance_atr=-1.6))

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("bollinger_atr_reversion.confirmed", result.reasonCodes)
        self.assertIn("spy1mBollingerBands", result.features)

    def test_upper_band_reentry_after_overextension_can_produce_sell(self) -> None:
        result = evaluate_snapshot(configured_snapshot(sell_reentry_sequence(), distance_atr=1.6))

        self.assertEqual(result.signal, Signal.SELL.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("bollinger_atr_reversion.confirmed", result.reasonCodes)

    def test_temporary_overextension_without_reentry_holds(self) -> None:
        result = evaluate_snapshot(configured_snapshot(no_reentry_sequence(), distance_atr=-1.6))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("bollinger_atr_reversion.no_band_reentry", result.reasonCodes)

    def test_missing_required_data_generates_hold_not_eligible(self) -> None:
        snapshot = configured_snapshot(buy_reentry_sequence(), distance_atr=-1.6)
        snapshot = with_feature(snapshot, "spy1mBollingerBands", TEST_BANDS, FeatureQuality.MISSING)
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertFalse(result.dataReady)
        self.assertIn("required_data_unavailable", result.reasonCodes)

    def test_changing_only_event_direction_does_not_change_result(self) -> None:
        snapshot = configured_snapshot(buy_reentry_sequence(), distance_atr=-1.6)
        base = evaluate_snapshot(snapshot)
        features_with_event_direction = {
            **snapshot.features,
            "event.directionBias": snapshot.features["spy1mAtr14"].model_copy(update={"value": "short"}),
        }
        changed = evaluate_snapshot(snapshot.model_copy(update={"features": features_with_event_direction}))

        self.assertEqual(changed.signal, base.signal)
        self.assertEqual(changed.confidence, base.confidence)
        self.assertEqual(changed.reasonCodes, base.reasonCodes)


if __name__ == "__main__":
    unittest.main()
