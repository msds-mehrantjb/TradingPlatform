from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from backend.app.domain.exchange_calendar import ExchangeCalendarService, ExchangeSession
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


def single_candle(*, timestamp: datetime, timeframe: str) -> MarketCandle:
    return MarketCandle(
        timestamp=timestamp,
        open=100,
        high=101,
        low=99,
        close=100.5,
        volume=100000,
        tradeCount=1000,
        provider="fixture",
        symbol="SPY",
        timeframe=timeframe,  # type: ignore[arg-type]
    )


def aligned_candles(*, timeframe: str, count: int, step_minutes: int, end: datetime) -> list[MarketCandle]:
    return candles(symbol="SPY", timeframe=timeframe, count=count, step_minutes=step_minutes, end=end)


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
        self.assertFalse(snapshot.globalSnapshotReady)
        self.assertTrue(snapshot.strategyRequiredDataReady)
        self.assertFalse(snapshot.auxiliaryContextReady)
        self.assertTrue(snapshot.executionDataReady)
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

    def test_excludes_incomplete_five_minute_candle_by_bar_end(self) -> None:
        snapshot = self.engine.compute(
            request_with(evaluation=datetime(2026, 1, 5, 10, 7, tzinfo=UTC)).model_copy(
                update={
                    "spy5mCandles": [
                        single_candle(timestamp=datetime(2026, 1, 5, 10, 0, tzinfo=UTC), timeframe="5Min"),
                        single_candle(timestamp=datetime(2026, 1, 5, 10, 5, tzinfo=UTC), timeframe="5Min"),
                    ],
                    "finalizationLagSeconds": 0,
                }
            )
        )

        self.assertEqual([row["timestamp"] for row in snapshot.rawInputs["spy5mCandles"]], ["2026-01-05T10:00:00Z"])
        self.assertEqual(snapshot.rawInputs["spy5mBarWindows"][-1]["barEndTimestamp"], "2026-01-05T10:05:00Z")

    def test_excludes_incomplete_fifteen_minute_candle_by_bar_end(self) -> None:
        snapshot = self.engine.compute(
            request_with(evaluation=datetime(2026, 1, 5, 10, 29, tzinfo=UTC)).model_copy(
                update={
                    "spy15mCandles": [
                        single_candle(timestamp=datetime(2026, 1, 5, 10, 0, tzinfo=UTC), timeframe="15Min"),
                        single_candle(timestamp=datetime(2026, 1, 5, 10, 15, tzinfo=UTC), timeframe="15Min"),
                    ],
                    "finalizationLagSeconds": 0,
                }
            )
        )

        self.assertEqual([row["timestamp"] for row in snapshot.rawInputs["spy15mCandles"]], ["2026-01-05T10:00:00Z"])
        self.assertEqual(snapshot.rawInputs["spy15mBarWindows"][-1]["barEndTimestamp"], "2026-01-05T10:15:00Z")

    def test_candle_becomes_available_at_close_plus_finalization_lag(self) -> None:
        unavailable = self.engine.compute(
            request_with(evaluation=datetime(2026, 1, 5, 10, 10, 1, tzinfo=UTC)).model_copy(
                update={
                    "spy5mCandles": [single_candle(timestamp=datetime(2026, 1, 5, 10, 5, tzinfo=UTC), timeframe="5Min")],
                    "finalizationLagSeconds": 2,
                }
            )
        )
        available = self.engine.compute(
            request_with(evaluation=datetime(2026, 1, 5, 10, 10, 2, tzinfo=UTC)).model_copy(
                update={
                    "spy5mCandles": [single_candle(timestamp=datetime(2026, 1, 5, 10, 5, tzinfo=UTC), timeframe="5Min")],
                    "finalizationLagSeconds": 2,
                }
            )
        )

        self.assertEqual(unavailable.rawInputs["spy5mCandles"], [])
        self.assertEqual([row["timestamp"] for row in available.rawInputs["spy5mCandles"]], ["2026-01-05T10:05:00Z"])

    def test_live_and_historical_replay_choose_same_completed_candles(self) -> None:
        evaluation = datetime(2026, 1, 5, 10, 29, 2, tzinfo=UTC)
        shared_updates = {
            "evaluationTimestamp": evaluation,
            "spy5mCandles": [
                single_candle(timestamp=datetime(2026, 1, 5, 10, 20, tzinfo=UTC), timeframe="5Min"),
                single_candle(timestamp=datetime(2026, 1, 5, 10, 25, tzinfo=UTC), timeframe="5Min"),
            ],
            "spy15mCandles": [
                single_candle(timestamp=datetime(2026, 1, 5, 10, 0, tzinfo=UTC), timeframe="15Min"),
                single_candle(timestamp=datetime(2026, 1, 5, 10, 15, tzinfo=UTC), timeframe="15Min"),
            ],
            "finalizationLagSeconds": 2,
        }
        live = self.engine.compute(request_with(evaluation=evaluation, execution_style="live").model_copy(update=shared_updates))
        replay = self.engine.compute(request_with(evaluation=evaluation, execution_style="replay").model_copy(update=shared_updates))

        self.assertEqual(live.rawInputs["spy5mCandles"], replay.rawInputs["spy5mCandles"])
        self.assertEqual(live.rawInputs["spy15mCandles"], replay.rawInputs["spy15mCandles"])
        self.assertEqual([row["timestamp"] for row in live.rawInputs["spy5mCandles"]], ["2026-01-05T10:20:00Z"])
        self.assertEqual([row["timestamp"] for row in live.rawInputs["spy15mCandles"]], ["2026-01-05T10:00:00Z"])

    def test_timeframe_quality_reports_duplicates_gaps_stale_and_misalignment(self) -> None:
        evaluation = datetime(2026, 1, 5, 20, 55, tzinfo=UTC)
        good_5m = aligned_candles(timeframe="5Min", count=35, step_minutes=5, end=datetime(2026, 1, 5, 20, 45, tzinfo=UTC))
        duplicate_5m = [*good_5m, good_5m[-1]]
        gap_5m = [*good_5m[:10], *good_5m[11:]]
        misaligned = [
            *good_5m[:-1],
            good_5m[-1].model_copy(update={"timestamp": good_5m[-1].timestamp + timedelta(minutes=1)}),
        ]
        out_of_order = [*good_5m[:-2], good_5m[-1], good_5m[-2]]
        stale = aligned_candles(timeframe="5Min", count=35, step_minutes=5, end=datetime(2026, 1, 5, 20, 25, tzinfo=UTC))

        duplicate_snapshot = self.engine.compute(request_with(evaluation=evaluation).model_copy(update={"spy5mCandles": duplicate_5m, "allowExtendedHours": True}))
        gap_snapshot = self.engine.compute(request_with(evaluation=evaluation).model_copy(update={"spy5mCandles": gap_5m, "allowExtendedHours": True}))
        misaligned_snapshot = self.engine.compute(request_with(evaluation=evaluation).model_copy(update={"spy5mCandles": misaligned, "allowExtendedHours": True}))
        out_of_order_snapshot = self.engine.compute(request_with(evaluation=evaluation).model_copy(update={"spy5mCandles": out_of_order, "allowExtendedHours": True}))
        stale_snapshot = self.engine.compute(request_with(evaluation=evaluation).model_copy(update={"spy5mCandles": stale, "allowExtendedHours": True}))

        self.assertTrue(duplicate_snapshot.rawInputs["timeframeQuality"]["5m"]["has_duplicates"])
        self.assertIn("has_duplicates", duplicate_snapshot.rawInputs["timeframeQuality"]["5m"]["reason_codes"])
        self.assertTrue(gap_snapshot.rawInputs["timeframeQuality"]["5m"]["has_gaps"])
        self.assertIn("has_gaps", gap_snapshot.rawInputs["timeframeQuality"]["5m"]["reason_codes"])
        self.assertFalse(misaligned_snapshot.rawInputs["timeframeQuality"]["5m"]["is_boundary_aligned"])
        self.assertIn("misaligned_boundary", misaligned_snapshot.rawInputs["timeframeQuality"]["5m"]["reason_codes"])
        self.assertFalse(out_of_order_snapshot.rawInputs["timeframeQuality"]["5m"]["is_ordered"])
        self.assertIn("out_of_order", out_of_order_snapshot.rawInputs["timeframeQuality"]["5m"]["reason_codes"])
        self.assertFalse(stale_snapshot.rawInputs["timeframeQuality"]["5m"]["is_fresh"])
        self.assertIn("stale_or_missing_recent", stale_snapshot.rawInputs["timeframeQuality"]["5m"]["reason_codes"])

    def test_timeframe_quality_reports_insufficient_history_and_extended_hours_policy(self) -> None:
        evaluation = datetime(2026, 1, 5, 20, 55, tzinfo=UTC)
        short_history = aligned_candles(timeframe="5Min", count=5, step_minutes=5, end=datetime(2026, 1, 5, 20, 45, tzinfo=UTC))
        mixed_hours = aligned_candles(timeframe="5Min", count=100, step_minutes=5, end=datetime(2026, 1, 5, 20, 45, tzinfo=UTC))

        short_snapshot = self.engine.compute(request_with(evaluation=evaluation).model_copy(update={"spy5mCandles": short_history, "allowExtendedHours": True}))
        mixed_rejected = self.engine.compute(request_with(evaluation=evaluation).model_copy(update={"spy5mCandles": mixed_hours, "allowExtendedHours": False}))
        mixed_allowed = self.engine.compute(request_with(evaluation=evaluation).model_copy(update={"spy5mCandles": mixed_hours, "allowExtendedHours": True}))

        self.assertFalse(short_snapshot.rawInputs["timeframeQuality"]["5m"]["has_required_history"])
        self.assertIn("insufficient_history", short_snapshot.rawInputs["timeframeQuality"]["5m"]["reason_codes"])
        self.assertIn("extended_hours_not_allowed", mixed_rejected.rawInputs["timeframeQuality"]["5m"]["reason_codes"])
        self.assertNotIn("extended_hours_not_allowed", mixed_allowed.rawInputs["timeframeQuality"]["5m"]["reason_codes"])

    def test_timezone_market_session_boundaries(self) -> None:
        open_time = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
        snapshot = self.engine.compute(request_with(evaluation=open_time))

        self.assertEqual(snapshot.features["timeSinceMarketOpenMinutes"].value, 0)
        self.assertEqual(snapshot.features["timeUntilMarketCloseMinutes"].value, 390)
        self.assertEqual(snapshot.rawInputs["exchangeSession"]["sessionId"], "XNYS:2026-01-05")
        self.assertEqual(snapshot.rawInputs["exchangeSession"]["openTimestamp"], "2026-01-05T14:30:00Z")

        mismatched = self.engine.compute(request_with(session_date=date(2026, 1, 4)))
        self.assertFalse(mismatched.dataReady)
        self.assertIn("session_date_mismatch", mismatched.reasonCodes)

    def test_exchange_calendar_handles_early_close_holiday_and_dst(self) -> None:
        calendar = ExchangeCalendarService()
        early_close = calendar.session_for_date(date(2026, 11, 27))
        holiday = calendar.session_for_date(date(2026, 12, 25))
        summer = calendar.session_for_date(date(2026, 7, 6))

        self.assertTrue(early_close.isEarlyClose)
        self.assertEqual(early_close.closeTimestamp, datetime(2026, 11, 27, 18, 0, tzinfo=UTC))
        self.assertFalse(holiday.can_trade)
        self.assertTrue(holiday.isHoliday)
        self.assertEqual(summer.openTimestamp, datetime(2026, 7, 6, 13, 30, tzinfo=UTC))

    def test_feature_engine_accepts_provider_exchange_session(self) -> None:
        provider_session = ExchangeSession(
            exchange="XNYS",
            sessionId="provider-session-early-close",
            sessionDate=SESSION_DATE,
            openTimestamp=datetime(2026, 1, 5, 14, 30, tzinfo=UTC),
            closeTimestamp=datetime(2026, 1, 5, 18, 0, tzinfo=UTC),
            isTradingSession=True,
            isEarlyClose=True,
            provider="provider-calendar",
        )
        snapshot = self.engine.compute(
            request_with(evaluation=datetime(2026, 1, 5, 17, 0, tzinfo=UTC)).model_copy(
                update={"exchangeSession": provider_session}
            )
        )

        self.assertEqual(snapshot.rawInputs["exchangeSession"]["sessionId"], "provider-session-early-close")
        self.assertEqual(snapshot.features["timeUntilMarketCloseMinutes"].value, 60)

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
