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
from backend.app.domain.models import Direction, Signal
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.context import (
    EconomicEventContext,
    MarketStructureContext,
    VolumeConfirmationContext,
    VwapPositionContext,
)
from backend.app.strategies.registry import directional_voters_from, resolve_strategy


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


def trend_candles(count: int = 42, *, drift: float = 0.04, last_volume: float = 190000) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        base = 100 + minute * drift
        volume = 100000 if minute < count - 1 else last_volume
        rows.append(candle_at(minute, base - 0.02, base + 0.08, base - 0.08, base + 0.03, volume))
    return rows


def range_candles(count: int = 42) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        base = 100 + (0.05 if minute % 2 else -0.05)
        rows.append(candle_at(minute, base - 0.01, base + 0.08, base - 0.08, base + 0.01, 100000))
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
    return [
        candle_at(minute, 100 + minute * 0.01, 100 + minute * 0.01 + 0.08, 100 + minute * 0.01 - 0.08, 100 + minute * 0.01 + 0.01).model_copy(update={"symbol": symbol})
        for minute in range(count)
    ]


def request_for(candles: list[MarketCandle], *, economic_event_state: dict | None = None, session_vwap: float = 100.0) -> PointInTimeFeatureRequest:
    evaluation = candles[-1].timestamp + timedelta(minutes=1, seconds=1)
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
        quote=BidAskQuote(bid=candles[-1].close - 0.03, ask=candles[-1].close + 0.03, timestamp=evaluation),
        economicEventState=economic_event_state or {"active": False, "category": "none"},
        breadthComponents={"XLK": auxiliary_candles("XLK")},
    )


def snapshot_for(candles: list[MarketCandle], *, event: dict | None = None, session_vwap: float = 100.0):
    return PointInTimeFeatureEngine().compute(request_for(candles, economic_event_state=event, session_vwap=session_vwap))


def with_feature(snapshot, name: str, value, quality: FeatureQuality = FeatureQuality.READY):
    features = {
        **snapshot.features,
        name: snapshot.features[name].model_copy(update={"value": value, "quality": quality}),
    }
    return snapshot.model_copy(update={"features": features})


def evaluate(module, strategy_id: str, snapshot):
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy(strategy_id),
        featureSnapshot=snapshot,
        configurationHash=module.config.configurationHash,
    )
    return module.evaluate(context)


class RemainingContextModulesTest(unittest.TestCase):
    def test_remaining_context_modules_are_not_directional_voters(self) -> None:
        for name in ["Economic Event Context", "Market Structure Context", "Volume Confirmation", "VWAP Position Context"]:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "not a directional voter"):
                    directional_voters_from([name])

    def test_economic_event_context_reports_risk_without_replacing_candidate_side(self) -> None:
        event_time = (OPEN_UTC + timedelta(minutes=35)).isoformat().replace("+00:00", "Z")
        provider_time = (OPEN_UTC + timedelta(minutes=41, seconds=1)).isoformat().replace("+00:00", "Z")
        event = {
            "event_id": "econ-cpi-1",
            "event_type": "CPI",
            "event_category": "inflation",
            "active": True,
            "importance": "high",
            "scheduled_at": event_time,
            "provider_timestamp": provider_time,
            "received_at": provider_time,
            "provider": "fixture_calendar",
            "status": "active",
            "actual": 0.3,
            "forecast": 0.2,
            "previous": 0.1,
            "revised_previous": 0.15,
            "surprise_raw": 0.1,
            "surprise_pct": 50.0,
            "surprise_zscore": 1.8,
            "affected_symbols": ["SPY", "QQQ"],
            "feed_health": "healthy",
            "directionBias": "BUY",
        }
        snapshot = snapshot_for(trend_candles(drift=-0.02), event=event)
        result = evaluate(EconomicEventContext(), "economic_event_context", snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertEqual(result.direction, Direction.FLAT)
        self.assertTrue(result.dataReady)
        self.assertEqual(result.features["eventId"], "econ-cpi-1")
        self.assertEqual(result.features["provider"], "fixture_calendar")
        self.assertEqual(result.features["feedHealth"], "healthy")
        self.assertEqual(result.features["surpriseZscore"], 1.8)
        self.assertEqual(result.features["eventImportance"], "high")
        self.assertLess(result.features["recommendedRiskCap"], 1.0)
        self.assertIn("candidate side is not replaced", result.explanation)

    def test_missing_economic_event_context_is_visible(self) -> None:
        snapshot = snapshot_for(range_candles())
        snapshot = with_feature(snapshot, "economicEventState", {}, FeatureQuality.READY)
        result = evaluate(EconomicEventContext(), "economic_event_context", snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.dataReady)
        self.assertIn("economic_event.empty_event_state", result.features["reasonCodes"])
        self.assertEqual(result.features["contextEffect"], "reduce_risk")
        self.assertEqual(result.features["recommendedRiskCap"], 0.0)
        self.assertFalse(result.features["allowNewEntries"])
        self.assertFalse(result.features["eventReactionAllowed"])
        self.assertEqual(result.features["eventPhase"], "FEED_UNAVAILABLE")

    def test_confirmed_no_event_with_timestamp_stays_normal(self) -> None:
        provider_time = (OPEN_UTC + timedelta(minutes=41, seconds=1)).isoformat().replace("+00:00", "Z")
        event = {
            "event_id": "no-event-1",
            "event_type": "none",
            "event_category": "none",
            "importance": "low",
            "scheduled_at": (OPEN_UTC + timedelta(minutes=45)).isoformat().replace("+00:00", "Z"),
            "provider_timestamp": provider_time,
            "received_at": provider_time,
            "provider": "fixture_calendar",
            "status": "none",
            "affected_symbols": ["SPY"],
            "feed_health": "healthy",
        }
        result = evaluate(EconomicEventContext(), "economic_event_context", snapshot_for(range_candles(), event=event))

        self.assertTrue(result.dataReady)
        self.assertEqual(result.features["eventState"], "none")
        self.assertEqual(result.features["eventPhase"], "NORMAL")
        self.assertEqual(result.features["recommendedRiskCap"], 1.0)
        self.assertTrue(result.features["allowNewEntries"])

    def test_unknown_importance_is_treated_as_high_risk(self) -> None:
        provider_time = (OPEN_UTC + timedelta(minutes=41, seconds=1)).isoformat().replace("+00:00", "Z")
        event = {
            "event_id": "econ-unknown-1",
            "event_type": "unspecified",
            "event_category": "macro",
            "scheduled_at": (OPEN_UTC + timedelta(minutes=45)).isoformat().replace("+00:00", "Z"),
            "provider_timestamp": provider_time,
            "received_at": provider_time,
            "provider": "fixture_calendar",
            "status": "scheduled",
            "affected_symbols": ["SPY"],
            "feed_health": "healthy",
        }
        result = evaluate(EconomicEventContext(), "economic_event_context", snapshot_for(range_candles(), event=event))

        self.assertEqual(result.features["eventImportance"], "unknown")
        self.assertEqual(result.features["eventPhase"], "PRE_EVENT_BLACKOUT")
        self.assertEqual(result.features["recommendedRiskCap"], 0.0)
        self.assertFalse(result.features["allowNewEntries"])

    def test_economic_event_shock_emits_enforceable_controls_even_on_hold(self) -> None:
        provider_time = (OPEN_UTC + timedelta(minutes=41, seconds=1)).isoformat().replace("+00:00", "Z")
        event = {
            "event_id": "econ-low-1",
            "event_type": "minor_release",
            "event_category": "minor",
            "importance": "low",
            "scheduled_at": (OPEN_UTC + timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
            "provider_timestamp": provider_time,
            "received_at": provider_time,
            "provider": "fixture_calendar",
            "status": "scheduled",
            "affected_symbols": ["SPY"],
            "feed_health": "healthy",
        }
        snapshot = snapshot_for(trend_candles(), event=event)
        snapshot = with_feature(snapshot, "spreadBasisPoints", 20.0)
        result = evaluate(EconomicEventContext(), "economic_event_context", snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertTrue(result.dataReady)
        self.assertEqual(result.features["contextEffect"], "reduce_risk")
        self.assertLess(result.features["recommendedRiskCap"], 1.0)
        self.assertLess(result.features["recommendedSizeMultiplier"], 1.0)
        self.assertGreater(result.features["minimumEdgeMultiplier"], 1.0)
        self.assertEqual(result.features["executionMode"], "event_spread_limit_exceeded")
        self.assertFalse(result.features["allowNewEntries"])
        self.assertFalse(result.features["allowPositionIncrease"])
        self.assertIsNotNone(result.features["cooldownUntil"])
        self.assertIn("economic_event.enforceable_shock_controls", result.features["reasonCodes"])

    def test_event_economics_block_when_net_edge_is_below_margin(self) -> None:
        provider_time = (OPEN_UTC + timedelta(minutes=41, seconds=1)).isoformat().replace("+00:00", "Z")
        event = {
            "event_id": "econ-edge-1",
            "event_type": "minor_release",
            "event_category": "minor",
            "importance": "low",
            "scheduled_at": (OPEN_UTC + timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
            "provider_timestamp": provider_time,
            "received_at": provider_time,
            "provider": "fixture_calendar",
            "status": "scheduled",
            "affected_symbols": ["SPY"],
            "feed_health": "healthy",
            "execution_economics": {
                "predicted_gross_edge_bps": 5.0,
                "expected_total_cost_bps": 4.5,
                "predicted_net_edge_bps": 0.5,
                "edge_to_cost_ratio": 1.1,
            },
        }
        result = evaluate(EconomicEventContext(), "economic_event_context", snapshot_for(trend_candles(), event=event))

        self.assertEqual(result.features["executionMode"], "net_edge_insufficient")
        self.assertFalse(result.features["allowNewEntries"])
        self.assertFalse(result.features["eventReactionAllowed"])
        self.assertEqual(result.features["predicted_net_edge_bps"], 0.5)
        self.assertEqual(result.features["required_safety_margin_bps"], 8.0)

    def test_spy_event_taxonomy_has_dedicated_policy_for_minimum_event_set(self) -> None:
        context = EconomicEventContext()
        expected_keys = {
            "fomc_statement",
            "rate_decision",
            "press_conference",
            "minutes",
            "chair_speech",
            "governor_speech",
            "cpi",
            "core_cpi",
            "pce",
            "ppi",
            "nonfarm_payrolls",
            "unemployment_rate",
            "average_hourly_earnings",
            "initial_jobless_claims",
            "gdp",
            "retail_sales",
            "ism_manufacturing",
            "ism_services",
            "consumer_sentiment",
            "trading_halt",
            "luld_event",
            "circuit_breaker",
            "early_close",
            "options_expiration",
            "index_rebalance",
            "moc_imbalance",
            "geopolitical_news",
            "emergency_central_bank_announcement",
            "financial_system_incident",
            "exchange_outage",
            "data_feed_outage",
            "unknown",
        }

        self.assertTrue(expected_keys.issubset(context.config.eventPolicies))
        for key in expected_keys:
            with self.subTest(key=key):
                policy = context.config.eventPolicies[key]
                if policy.preEventCautionWindowMinutes > 0:
                    self.assertGreaterEqual(policy.preEventCautionWindowMinutes, policy.preEventBlackoutWindowMinutes)
                self.assertGreaterEqual(policy.releaseFreezeDurationMinutes, 0)
                self.assertGreaterEqual(policy.postEventStabilizationWindowMinutes, 0)
                self.assertGreaterEqual(policy.minimumNetEdgeBps, 0)
                self.assertGreaterEqual(policy.maximumPositionRiskMultiplier, 0)
                self.assertLessEqual(policy.maximumPositionRiskMultiplier, 1)

    def test_market_structure_context_returns_structure_fields(self) -> None:
        result = evaluate(MarketStructureContext(), "market_structure_context", snapshot_for(trend_candles()))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertTrue(result.dataReady)
        self.assertIn("higherHighsHigherLows", result.features)
        self.assertIn("rangeStructure", result.features)
        self.assertIn("breakOfStructure", result.features)
        self.assertIn("contextEffect", result.features)

    def test_volume_confirmation_returns_bounded_effect_fields(self) -> None:
        result = evaluate(VolumeConfirmationContext(), "volume_confirmation", snapshot_for(trend_candles(last_volume=260000)))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertTrue(result.dataReady)
        self.assertIn("relativeVolume", result.features)
        self.assertIn("breakoutVolumeConfirmation", result.features)
        self.assertLessEqual(result.features["maxConfidenceAdjustment"], 0.25)

    def test_vwap_position_context_reports_reclaim_or_position(self) -> None:
        candles = range_candles()
        candles[-3] = candle_at(39, 99.90, 99.96, 99.82, 99.88, 100000)
        candles[-2] = candle_at(40, 99.88, 100.04, 99.84, 99.96, 100000)
        candles[-1] = candle_at(41, 99.96, 100.22, 99.94, 100.18, 120000)
        snapshot = snapshot_for(candles, session_vwap=100.0)
        snapshot = with_feature(snapshot, "sessionVwapSlope", 0.0002)
        result = evaluate(VwapPositionContext(), "vwap_position_context", snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertTrue(result.dataReady)
        self.assertEqual(result.features["pricePosition"], "above_vwap")
        self.assertIn(result.features["reclaimRejectionState"], {"bullish_reclaim", "above_rising_vwap"})
        self.assertLessEqual(result.features["maxConfidenceAdjustment"], 0.25)

    def test_missing_context_does_not_fabricate_agreement(self) -> None:
        snapshot = snapshot_for(trend_candles())
        snapshot = with_feature(snapshot, "sessionVwap", 100.0, FeatureQuality.MISSING)
        result = evaluate(VwapPositionContext(), "vwap_position_context", snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.dataReady)
        self.assertEqual(result.features["contextEffect"], "neutral")


if __name__ == "__main__":
    unittest.main()
