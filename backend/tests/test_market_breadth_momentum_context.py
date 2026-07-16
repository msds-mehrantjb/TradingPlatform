from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from backend.app.domain.feature_engine import (
    BidAskQuote,
    MarketCandle,
    PointInTimeFeatureEngine,
    PointInTimeFeatureRequest,
    PriorDayOHLC,
)
from backend.app.domain.models import Direction, Signal
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.context.market_breadth_momentum import (
    MarketBreadthMomentumConfig,
    MarketBreadthMomentumContext,
)
from backend.app.strategies.registry import StrategyCollection, resolve_strategy


SESSION_DATE = date(2026, 1, 5)
OPEN_UTC = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
EVALUATION = OPEN_UTC + timedelta(minutes=39)
DEFAULT_BASKET = ("XLK", "XLF", "XLY", "XLP", "XLV", "XLI", "XLE", "XLB", "XLU", "XLRE", "XLC")


def candle_at(minute: int, close: float, *, symbol: str, volume: float = 100000) -> MarketCandle:
    return MarketCandle(
        timestamp=OPEN_UTC + timedelta(minutes=minute),
        open=close - 0.02,
        high=close + 0.08,
        low=close - 0.08,
        close=close,
        volume=volume,
        tradeCount=1000 + minute,
        provider="fixture",
        symbol=symbol,
        timeframe="1Min",
    )


def component(symbol: str, *, drift: float, stale_shift_minutes: int = 0, count: int = 40) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        timestamp_minute = minute - stale_shift_minutes
        rows.append(candle_at(timestamp_minute, 100 + minute * drift, symbol=symbol, volume=100000 + minute * 100))
    return rows


def spy_candles() -> list[MarketCandle]:
    return [candle_at(minute, 100 + minute * 0.02, symbol="SPY") for minute in range(40)]


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


def request_for(
    *,
    breadth_components: dict[str, list[MarketCandle]] | None,
    external_feed: dict | None = None,
    max_auxiliary_age_seconds: int = 300,
) -> PointInTimeFeatureRequest:
    spy = spy_candles()
    return PointInTimeFeatureRequest(
        evaluationTimestamp=EVALUATION,
        sessionDate=SESSION_DATE,
        spy1mCandles=spy,
        spy5mCandles=timeframe_history(end=EVALUATION, step_minutes=5, timeframe="5Min"),
        spy15mCandles=timeframe_history(end=EVALUATION, step_minutes=15, timeframe="15Min"),
        sessionVwap=spy[-1].close,
        sessionVwapTimestamp=EVALUATION,
        qqqAlignedCandles=component("QQQ", drift=0.015),
        iwmAlignedCandles=component("IWM", drift=0.01),
        priorDayOHLC=PriorDayOHLC(sessionDate=date(2026, 1, 2), open=99, high=101, low=98, close=100),
        quote=BidAskQuote(bid=spy[-1].close - 0.01, ask=spy[-1].close + 0.01, timestamp=EVALUATION),
        breadthComponents=breadth_components or {},
        externalBreadthFeed=external_feed or {},
        maxAuxiliaryAgeSeconds=max_auxiliary_age_seconds,
    )


def proxy_basket(*, positive: bool = True, stale: bool = False) -> dict[str, list[MarketCandle]]:
    rows: dict[str, list[MarketCandle]] = {}
    for index, symbol in enumerate(DEFAULT_BASKET):
        drift = 0.04 if positive else -0.04
        if index > 7 and positive:
            drift = -0.01
        if index > 7 and not positive:
            drift = 0.01
        rows[symbol] = component(symbol, drift=drift, stale_shift_minutes=10 if stale else 0)
    return rows


def snapshot_for(*, breadth_components: dict[str, list[MarketCandle]] | None, external_feed: dict | None = None, max_age: int = 300):
    return PointInTimeFeatureEngine().compute(
        request_for(breadth_components=breadth_components, external_feed=external_feed, max_auxiliary_age_seconds=max_age)
    )


def evaluate_snapshot(snapshot, config: MarketBreadthMomentumConfig | None = None):
    context_module = MarketBreadthMomentumContext(config)
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("market_breadth_momentum"),
        featureSnapshot=snapshot,
        configurationHash=context_module.config.configurationHash,
    )
    return context_module.evaluate(context)


class MarketBreadthMomentumContextTest(unittest.TestCase):
    def test_registry_entry_is_context_not_directional(self) -> None:
        entry = resolve_strategy("market_breadth_momentum")

        self.assertEqual(entry.collection, StrategyCollection.CONTEXT.value)

    def test_empty_breadth_basket_is_not_valid_signal(self) -> None:
        result = evaluate_snapshot(snapshot_for(breadth_components={}))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertEqual(result.direction, Direction.FLAT)
        self.assertFalse(result.dataReady)
        self.assertEqual(result.features["breadthSourceKind"], "breadth_proxy")
        self.assertIn("market_breadth.proxy_unavailable_or_unready", result.features["reasonCodes"])

    def test_stale_proxy_basket_produces_data_not_ready(self) -> None:
        result = evaluate_snapshot(snapshot_for(breadth_components=proxy_basket(stale=True), max_age=60))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.dataReady)
        self.assertIn("proxy", result.features["breadthSourceLabel"])

    def test_positive_proxy_breadth_context_passes(self) -> None:
        result = evaluate_snapshot(snapshot_for(breadth_components=proxy_basket(positive=True)))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertTrue(result.dataReady)
        self.assertEqual(result.features["breadthSourceKind"], "breadth_proxy")
        self.assertGreater(result.features["percentagePositiveReturn"], 0.58)
        self.assertIn("long", result.features["contextEffect"])

    def test_negative_proxy_breadth_context_passes(self) -> None:
        result = evaluate_snapshot(snapshot_for(breadth_components=proxy_basket(positive=False)))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertTrue(result.dataReady)
        self.assertLess(result.features["percentagePositiveReturn"], 0.42)
        self.assertIn("short", result.features["contextEffect"])

    def test_external_feed_is_labeled_as_true_breadth_feed(self) -> None:
        feed = {
            "sourceTimestamp": EVALUATION.isoformat().replace("+00:00", "Z"),
            "componentCount": 500,
            "dataCoverage": 0.91,
            "percentagePositiveReturn": 0.64,
            "percentageAboveVwap": 0.61,
            "percentageAboveEma20": 0.58,
            "medianComponentReturn": 0.0012,
            "upDownVolumeRatio": 1.7,
            "dispersion": 0.006,
        }
        result = evaluate_snapshot(snapshot_for(breadth_components={}, external_feed=feed), MarketBreadthMomentumConfig(sourceMode="feed"))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertTrue(result.dataReady)
        self.assertEqual(result.features["breadthSourceKind"], "breadth_feed")
        self.assertIn("External market breadth feed", result.features["breadthSourceLabel"])

    def test_breadth_context_alone_cannot_create_buy_or_sell(self) -> None:
        result = evaluate_snapshot(snapshot_for(breadth_components=proxy_basket(positive=True)))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertEqual(result.direction, Direction.FLAT)


if __name__ == "__main__":
    unittest.main()
