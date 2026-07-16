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


SESSION_DATE = date(2026, 1, 5)
EVALUATION = datetime(2026, 1, 5, 15, 29, tzinfo=UTC)


def candles(
    *,
    symbol: str,
    timeframe: str,
    count: int,
    step_minutes: int,
    end: datetime = EVALUATION,
    provider: str = "fixture",
    drift: float = 0.04,
) -> list[MarketCandle]:
    start = end - timedelta(minutes=step_minutes * (count - 1))
    rows: list[MarketCandle] = []
    for index in range(count):
        timestamp = start + timedelta(minutes=step_minutes * index)
        base = 100 + index * drift
        open_price = base - 0.02
        close = base + 0.02
        rows.append(
            MarketCandle(
                timestamp=timestamp,
                open=open_price,
                high=base + 0.08,
                low=base - 0.08,
                close=close,
                volume=100000 + index * 100,
                tradeCount=1000 + index,
                provider=provider,
                symbol=symbol,
                timeframe=timeframe,  # type: ignore[arg-type]
            )
        )
    return rows


def request_with(
    *,
    evaluation: datetime = EVALUATION,
    provider: str = "fixture",
    qqq_offset_minutes: int = 0,
    execution_style: str = "live",
    for_training: bool = False,
    session_date: date = SESSION_DATE,
) -> PointInTimeFeatureRequest:
    spy_1m = candles(symbol="SPY", timeframe="1Min", count=70, step_minutes=1, provider=provider)
    spy_5m = candles(symbol="SPY", timeframe="5Min", count=70, step_minutes=5, provider=provider)
    spy_15m = candles(symbol="SPY", timeframe="15Min", count=70, step_minutes=15, provider=provider)
    qqq = candles(symbol="QQQ", timeframe="1Min", count=70, step_minutes=1, end=EVALUATION - timedelta(minutes=qqq_offset_minutes), drift=0.03)
    iwm = candles(symbol="IWM", timeframe="1Min", count=70, step_minutes=1, drift=0.02)
    breadth = {
        "XLK": candles(symbol="XLK", timeframe="1Min", count=70, step_minutes=1, drift=0.02),
        "XLF": candles(symbol="XLF", timeframe="1Min", count=70, step_minutes=1, drift=0.01),
    }
    return PointInTimeFeatureRequest(
        evaluationTimestamp=evaluation,
        sessionDate=session_date,
        spy1mCandles=spy_1m,
        spy5mCandles=spy_5m,
        spy15mCandles=spy_15m,
        sessionVwap=101.2,
        sessionVwapTimestamp=EVALUATION,
        qqqAlignedCandles=qqq,
        iwmAlignedCandles=iwm,
        priorDayOHLC=PriorDayOHLC(sessionDate=date(2026, 1, 2), open=99, high=101, low=98, close=100),
        premarket=PremarketLevels(high=101.5, low=99.5, sourceTimestamp=EVALUATION),
        openingRange=OpeningRangeLevels(
            high=101.1,
            low=99.9,
            startTimestamp=datetime(2026, 1, 5, 14, 30, tzinfo=UTC),
            endTimestamp=datetime(2026, 1, 5, 14, 45, tzinfo=UTC),
        ),
        quote=BidAskQuote(bid=102.38, ask=102.4, timestamp=EVALUATION),
        economicEventState={"active": False, "category": "none"},
        breadthComponents=breadth,
        maxAuxiliaryAgeSeconds=300,
        executionStyle=execution_style,  # type: ignore[arg-type]
        forModelTraining=for_training,
    )


class PointInTimeFeatureEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = PointInTimeFeatureEngine()

    def test_no_lookahead_future_candles_do_not_change_past_snapshot(self) -> None:
        request = request_with()
        base = self.engine.compute(request)

        future_spy = [
            *request.spy1mCandles,
            *candles(symbol="SPY", timeframe="1Min", count=10, step_minutes=1, end=EVALUATION + timedelta(minutes=10), drift=2.0),
        ]
        future_spy_5m = [
            *request.spy5mCandles,
            *candles(symbol="SPY", timeframe="5Min", count=5, step_minutes=5, end=EVALUATION + timedelta(minutes=25), drift=2.0),
        ]
        future_spy_15m = [
            *request.spy15mCandles,
            *candles(symbol="SPY", timeframe="15Min", count=5, step_minutes=15, end=EVALUATION + timedelta(minutes=75), drift=2.0),
        ]
        future_qqq = [
            *request.qqqAlignedCandles,
            *candles(symbol="QQQ", timeframe="1Min", count=10, step_minutes=1, end=EVALUATION + timedelta(minutes=10), drift=2.0),
        ]
        with_future = request.model_copy(
            update={
                "spy1mCandles": future_spy,
                "spy5mCandles": future_spy_5m,
                "spy15mCandles": future_spy_15m,
                "qqqAlignedCandles": future_qqq,
            }
        )
        future = self.engine.compute(with_future)

        self.assertEqual(future.anchorTimestamp, base.anchorTimestamp)
        self.assertEqual(future.features["spy1mEma20"].model_dump(mode="json"), base.features["spy1mEma20"].model_dump(mode="json"))
        self.assertEqual(future.features["spy1mMacd"].model_dump(mode="json"), base.features["spy1mMacd"].model_dump(mode="json"))
        self.assertEqual(future.features["spy5mEma20"].model_dump(mode="json"), base.features["spy5mEma20"].model_dump(mode="json"))
        self.assertEqual(future.features["spy15mEma20"].model_dump(mode="json"), base.features["spy15mEma20"].model_dump(mode="json"))
        self.assertEqual(future.features["qqqClose"].model_dump(mode="json"), base.features["qqqClose"].model_dump(mode="json"))
        self.assertEqual(future.rawInputs["spy1mCandles"], base.rawInputs["spy1mCandles"])

    def test_auxiliary_data_outside_freshness_limit_sets_data_not_ready(self) -> None:
        snapshot = self.engine.compute(request_with(qqq_offset_minutes=10))

        self.assertFalse(snapshot.dataReady)
        self.assertIn("qqq_stale", snapshot.reasonCodes)
        self.assertEqual(snapshot.features["qqqClose"].quality, FeatureQuality.STALE.value)

    def test_live_and_replay_styles_have_identical_features_for_same_prefix(self) -> None:
        live = self.engine.compute(request_with(execution_style="live"))
        replay = self.engine.compute(request_with(execution_style="replay"))

        self.assertTrue(live.dataReady)
        self.assertEqual(
            {key: value.model_dump(mode="json") for key, value in live.features.items()},
            {key: value.model_dump(mode="json") for key, value in replay.features.items()},
        )

    def test_timezone_market_session_boundaries(self) -> None:
        open_time = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
        snapshot = self.engine.compute(request_with(evaluation=open_time))

        self.assertEqual(snapshot.features["timeSinceMarketOpenMinutes"].value, 0)
        self.assertEqual(snapshot.features["timeUntilMarketCloseMinutes"].value, 390)

        mismatched = self.engine.compute(request_with(session_date=date(2026, 1, 4)))
        self.assertFalse(mismatched.dataReady)
        self.assertIn("session_date_mismatch", mismatched.reasonCodes)

    def test_demo_or_fallback_data_is_rejected_for_model_training(self) -> None:
        snapshot = self.engine.compute(request_with(provider="demo", for_training=True, execution_style="ml"))

        self.assertTrue(snapshot.dataReady)
        self.assertFalse(snapshot.eligibleForTraining)
        self.assertIn("demo_data_rejected_for_training", snapshot.reasonCodes)

    def test_required_feature_values_have_source_timestamps_and_quality(self) -> None:
        snapshot = self.engine.compute(request_with())

        for name in [
            "spy1mEma9",
            "spy1mEma20",
            "spy1mAtr14",
            "spy1mAdx14",
            "spy1mRsi14",
            "spy1mMacd",
            "sessionVwap",
            "distanceFromVwapAtr",
            "spreadDollars",
            "relativeStrengthQqq",
            "breadthProxyAverageReturn",
        ]:
            with self.subTest(feature=name):
                feature = snapshot.features[name]
                self.assertEqual(feature.quality, FeatureQuality.READY.value)
                self.assertIsNotNone(feature.sourceTimestamp)
                self.assertTrue(feature.explanation)


if __name__ == "__main__":
    unittest.main()
