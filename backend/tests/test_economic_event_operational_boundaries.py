from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from backend.app.domain.feature_engine import (
    BidAskQuote,
    EconomicEventState,
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
    EconomicEventContextConfig,
    MarketStructureContext,
    VolumeConfirmationContext,
    VwapPositionContext,
)
from backend.app.strategies.context.economic_event_context import DEFAULT_EVENT_POLICIES
from backend.app.strategies.registry import StrategyCollection, resolve_strategy


SESSION_DATE = date(2026, 7, 24)
OPEN_UTC = datetime(2026, 7, 24, 13, 30, tzinfo=UTC)


def candle_at(minute: int, *, close: float | None = None, volume: float = 100_000) -> MarketCandle:
    price = close if close is not None else 100 + minute * 0.02
    return MarketCandle(
        timestamp=OPEN_UTC + timedelta(minutes=minute),
        open=price - 0.02,
        high=price + 0.08,
        low=price - 0.08,
        close=price,
        volume=volume,
        tradeCount=1_000 + minute,
        provider="fixture",
        symbol="SPY",
        timeframe="1Min",
    )


def candles(count: int = 45) -> list[MarketCandle]:
    return [candle_at(index) for index in range(count)]


def auxiliary(symbol: str, count: int = 80) -> list[MarketCandle]:
    return [candle_at(index).model_copy(update={"symbol": symbol}) for index in range(count)]


def event_payload(
    *,
    event_type: str = "cpi",
    status: str = "scheduled",
    importance: str = "high",
    scheduled_at: datetime | None = None,
    released_at: datetime | None = None,
    provider_timestamp: datetime | None = None,
    feed_health: str = "healthy",
    **extra,
) -> dict:
    provider_timestamp = provider_timestamp or OPEN_UTC + timedelta(minutes=45)
    payload = {
        "event_id": f"event-{event_type}",
        "event_type": event_type,
        "event_category": extra.pop("event_category", "inflation"),
        "importance": importance,
        "scheduled_at": scheduled_at or OPEN_UTC + timedelta(minutes=60),
        "released_at": released_at,
        "provider_timestamp": provider_timestamp,
        "received_at": extra.pop("received_at", provider_timestamp),
        "provider": "fixture_calendar",
        "status": status,
        "forecast": 0.2,
        "previous": 0.1,
        "affected_symbols": ["SPY"],
        "feed_health": feed_health,
        "market_reaction": {
            "available_depth": 150_000,
            "quote_update_rate": 50,
            **extra.pop("market_reaction", {}),
        },
        "execution_economics": {
            "expected_spread_cost_bps": 1.0,
            "expected_slippage_bps": 1.0,
            "fees_bps": 0.2,
            "market_impact_bps": 0.5,
            "predicted_gross_edge_bps": 18.0,
            "expected_total_cost_bps": 2.7,
            "predicted_net_edge_bps": 15.3,
            "edge_to_cost_ratio": 6.67,
            "fillable_quantity": 150_000,
            **extra.pop("execution_economics", {}),
        },
        **extra,
    }
    return payload


def request_for(rows: list[MarketCandle], *, event: dict | EconomicEventState | None, execution_style: str = "live", quote: BidAskQuote | None = None) -> PointInTimeFeatureRequest:
    evaluation = rows[-1].timestamp + timedelta(minutes=1, seconds=1)
    return PointInTimeFeatureRequest(
        evaluationTimestamp=evaluation,
        sessionDate=SESSION_DATE,
        spy1mCandles=rows,
        spy5mCandles=rows,
        spy15mCandles=rows,
        sessionVwap=100.0,
        sessionVwapTimestamp=evaluation,
        qqqAlignedCandles=auxiliary("QQQ"),
        iwmAlignedCandles=auxiliary("IWM"),
        priorDayOHLC=PriorDayOHLC(sessionDate=date(2026, 7, 23), open=99, high=101, low=98, close=100),
        quote=quote or BidAskQuote(bid=100.0, ask=100.02, timestamp=evaluation),
        economicEventState=event or event_payload(status="none", event_type="none", event_category="none", importance="low", scheduled_at=evaluation),
        breadthComponents={"XLK": auxiliary("XLK")},
        executionStyle=execution_style,  # type: ignore[arg-type]
    )


def evaluate_event(event: dict | EconomicEventState | None, *, rows: list[MarketCandle] | None = None, execution_style: str = "live", quote: BidAskQuote | None = None):
    snapshot = PointInTimeFeatureEngine().compute(request_for(rows or candles(), event=event, execution_style=execution_style, quote=quote))
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("economic_event_context"),
        featureSnapshot=snapshot,
        configurationHash=EconomicEventContextConfig().configurationHash,
    )
    return EconomicEventContext().evaluate(context), snapshot


class EconomicEventOperationalBoundariesTest(unittest.TestCase):
    def test_registry_and_package_imports_are_consistent(self) -> None:
        entry = resolve_strategy("economic_event_context")

        self.assertEqual(entry.collection, StrategyCollection.CONTEXT.value)
        self.assertFalse(entry.enabled)
        self.assertIs(EconomicEventContext.registryEntry, entry)
        for cls in (EconomicEventContext, MarketStructureContext, VolumeConfirmationContext, VwapPositionContext):
            self.assertTrue(callable(cls))

    def test_every_event_type_policy_is_resolvable(self) -> None:
        for key in DEFAULT_EVENT_POLICIES:
            with self.subTest(key=key):
                result, _ = evaluate_event(
                    event_payload(
                        event_type=key,
                        scheduled_at=OPEN_UTC + timedelta(minutes=90),
                        provider_timestamp=OPEN_UTC + timedelta(minutes=45),
                        importance="medium" if key != "unknown" else "unknown",
                    )
                )
                self.assertEqual(result.features["eventPolicyKey"], key)
                self.assertIn("maximumSpreadBps", result.features["eventPolicy"])

    def test_exact_policy_window_boundaries(self) -> None:
        evaluation = OPEN_UTC + timedelta(minutes=45, seconds=1)
        cases = [
            ("outside_caution", evaluation + timedelta(minutes=45, seconds=1), "NORMAL"),
            ("caution_edge", evaluation + timedelta(minutes=45), "PRE_EVENT_CAUTION"),
            ("blackout_edge", evaluation + timedelta(minutes=15), "PRE_EVENT_BLACKOUT"),
            ("release_edge", evaluation, "PRE_EVENT_BLACKOUT"),
            ("freeze_edge", evaluation - timedelta(minutes=3), "RELEASE_FREEZE"),
            ("discovery_edge", evaluation - timedelta(minutes=5), "POST_EVENT_DISCOVERY"),
            ("stabilization_edge", evaluation - timedelta(minutes=25), "POST_EVENT_STABILIZATION"),
            ("recovered_after_stabilization", evaluation - timedelta(minutes=25, seconds=1), "NORMALIZED"),
        ]
        for name, event_at, expected_phase in cases:
            with self.subTest(name=name):
                result, _ = evaluate_event(
                    event_payload(
                        event_type="cpi",
                        status="released" if event_at <= evaluation else "scheduled",
                        scheduled_at=event_at,
                        released_at=event_at if event_at <= evaluation else None,
                        provider_timestamp=evaluation,
                        actual=0.2 if event_at <= evaluation else None,
                    ),
                    rows=[candle_at(index) for index in range(45)],
                )
                self.assertEqual(result.features["eventPhase"], expected_phase)

    def test_stale_feed_and_missing_provider_timestamp_fail_closed(self) -> None:
        stale, stale_snapshot = evaluate_event(event_payload(feed_health="stale", provider_timestamp=OPEN_UTC))
        missing, missing_snapshot = evaluate_event(EconomicEventState(event_type="cpi", event_category="inflation", importance="high", status="scheduled", scheduled_at=OPEN_UTC + timedelta(minutes=60), feed_health="healthy"))

        self.assertFalse(stale.dataReady)
        self.assertEqual(stale.features["executionMode"], "feed_unavailable")
        self.assertIn("economic_event_feed_stale", stale_snapshot.reasonCodes)
        self.assertFalse(missing.dataReady)
        self.assertIn("economic_event_provider_timestamp_missing", missing_snapshot.reasonCodes)

    def test_malformed_importance_and_unknown_event_type_are_defensive(self) -> None:
        unknown_importance = EconomicEventState.model_validate(
            event_payload(event_type="mystery_release", importance="critical-ish", provider_timestamp=OPEN_UTC + timedelta(minutes=45))
        )
        result, _ = evaluate_event(unknown_importance)

        self.assertEqual(result.features["eventImportance"], "unknown")
        self.assertEqual(result.features["eventPolicyKey"], "unknown")
        self.assertFalse(result.features["allowNewEntries"])

    def test_simultaneous_events_select_dominant_risk_event(self) -> None:
        base = event_payload(event_type="retail_sales", importance="medium", scheduled_at=OPEN_UTC + timedelta(minutes=120))
        base["simultaneous_events"] = [
            event_payload(event_type="cpi", importance="high", scheduled_at=OPEN_UTC + timedelta(minutes=55)),
            event_payload(event_type="consumer_sentiment", importance="low", scheduled_at=OPEN_UTC + timedelta(minutes=90)),
        ]

        result, _ = evaluate_event(base)

        self.assertEqual(result.features["eventPolicyKey"], "cpi")
        self.assertEqual(result.features["eventId"], "event-cpi")
        self.assertIn("economic_event.simultaneous_events_selected_dominant", result.features["reasonCodes"])
        self.assertIn("economic_event.simultaneous_events_most_restrictive_controls", result.features["reasonCodes"])
        self.assertEqual(result.features["simultaneous_event_count"], 3)
        self.assertIn("cpi", result.features["simultaneous_event_policy_keys"])

    def test_simultaneous_events_apply_most_restrictive_non_dominant_control(self) -> None:
        base = event_payload(
            event_type="retail_sales",
            importance="medium",
            scheduled_at=OPEN_UTC + timedelta(minutes=180),
        )
        base["simultaneous_events"] = [
            event_payload(
                event_type="consumer_sentiment",
                importance="low",
                scheduled_at=OPEN_UTC + timedelta(minutes=180),
                execution_economics={
                    "predicted_gross_edge_bps": 4.0,
                    "expected_total_cost_bps": 3.5,
                    "predicted_net_edge_bps": 0.5,
                    "edge_to_cost_ratio": 1.14,
                },
            )
        ]

        result, _ = evaluate_event(base)

        self.assertEqual(result.features["eventPolicyKey"], "retail_sales")
        self.assertEqual(result.features["executionMode"], "net_edge_insufficient")
        self.assertFalse(result.features["allowNewEntries"])
        self.assertEqual(result.features["recommendedRiskCap"], 0.0)
        self.assertEqual(result.features["required_safety_margin_bps"], 4.0)

    def test_revised_duplicate_cancelled_and_postponed_events_are_explicit(self) -> None:
        revised, _ = evaluate_event(event_payload(status="released", released_at=OPEN_UTC + timedelta(minutes=35), revision_number=2, duplicate_of="event-cpi-v1", actual=0.4, revised_previous=0.15))
        cancelled, _ = evaluate_event(event_payload(status="cancelled"))
        postponed, _ = evaluate_event(event_payload(status="postponed"))

        self.assertIn("economic_event.duplicate_or_revision_recomputed", revised.features["reasonCodes"])
        self.assertEqual(revised.features["prior_revision"], 0.05)
        self.assertEqual(cancelled.features["eventPhase"], "NORMALIZED")
        self.assertFalse(cancelled.features["eventReactionAllowed"])
        self.assertEqual(postponed.features["eventPhase"], "NORMALIZED")
        self.assertFalse(postponed.features["eventReactionAllowed"])

    def test_standardized_surprise_is_calculated_from_baseline_when_provider_omits_zscore(self) -> None:
        result, _ = evaluate_event(
            event_payload(
                status="released",
                released_at=OPEN_UTC + timedelta(minutes=35),
                scheduled_at=OPEN_UTC + timedelta(minutes=35),
                actual=0.42,
                forecast=0.30,
                surprise_mean=0.02,
                surprise_stddev=0.05,
                surprise_sample_count=120,
            )
        )

        self.assertAlmostEqual(result.features["actual_minus_forecast"], 0.12)
        self.assertAlmostEqual(result.features["standardized_surprise_zscore"], 2.0)
        self.assertEqual(result.features["surprise_baseline_sample_count"], 120)

    def test_clock_skew_and_ack_latency_are_reported_during_shock(self) -> None:
        provider = OPEN_UTC + timedelta(minutes=45)
        rows = candles()
        rows[-1] = rows[-1].model_copy(update={"high": rows[-1].high + 1.2, "low": rows[-1].low - 1.2})
        result, snapshot = evaluate_event(
            event_payload(
                provider_timestamp=provider,
                received_at=provider + timedelta(minutes=10),
                latency={"acknowledgment_latency_ms": 850, "decision_age_ms": 250},
            ),
            rows=rows,
        )

        self.assertFalse(result.dataReady)
        self.assertIn("economic_event_provider_clock_disagreement", snapshot.reasonCodes)
        self.assertEqual(result.features["acknowledgment_latency_ms"], 850)
        self.assertEqual(result.features["decision_age_ms"], 250)

    def test_dst_summer_and_winter_timestamps_use_absolute_provider_time(self) -> None:
        cases = [
            (date(2026, 7, 24), datetime(2026, 7, 24, 13, 45, tzinfo=UTC)),
            (date(2026, 1, 5), datetime(2026, 1, 5, 14, 45, tzinfo=UTC)),
        ]
        for session_date, evaluation in cases:
            rows = [
                MarketCandle(timestamp=evaluation - timedelta(minutes=45 - index), open=100 + index * 0.01, high=100 + index * 0.01 + 0.1, low=100 + index * 0.01 - 0.1, close=100 + index * 0.01, volume=100_000, symbol="SPY", timeframe="1Min")
                for index in range(45)
            ]
            request = request_for(rows, event=event_payload(scheduled_at=evaluation + timedelta(minutes=15), provider_timestamp=evaluation), quote=BidAskQuote(bid=100, ask=100.02, timestamp=evaluation))
            request = request.model_copy(update={"sessionDate": session_date, "evaluationTimestamp": evaluation})
            snapshot = PointInTimeFeatureEngine().compute(request)
            context = StrategyEvaluationContext(registryEntry=resolve_strategy("economic_event_context"), featureSnapshot=snapshot, configurationHash=EconomicEventContextConfig().configurationHash)
            result = EconomicEventContext().evaluate(context)

            self.assertEqual(result.features["eventPhase"], "PRE_EVENT_BLACKOUT")

    def test_spread_normalization_and_cost_gate_enforcement(self) -> None:
        quote = BidAskQuote(bid=99.95, ask=100.05, timestamp=OPEN_UTC + timedelta(minutes=45, seconds=1))
        result, snapshot = evaluate_event(
            event_payload(
                event_type="cpi",
                scheduled_at=OPEN_UTC + timedelta(minutes=180),
                execution_economics={"predicted_gross_edge_bps": 5.0, "expected_total_cost_bps": 4.0, "predicted_net_edge_bps": 1.0, "edge_to_cost_ratio": 1.25},
            ),
            quote=quote,
        )

        self.assertAlmostEqual(snapshot.features["spreadBasisPoints"].value, 10.0)
        self.assertEqual(result.features["executionMode"], "net_edge_insufficient")
        self.assertFalse(result.features["allowNewEntries"])

    def test_event_blackout_propagates_explicit_controls(self) -> None:
        result, _ = evaluate_event(event_payload(event_type="fomc_statement", scheduled_at=OPEN_UTC + timedelta(minutes=55)))

        self.assertEqual(result.features["eventPhase"], "PRE_EVENT_BLACKOUT")
        self.assertTrue(result.features["eventBlackout"])
        self.assertEqual(result.features["recommendedRiskCap"], 0.0)
        self.assertFalse(result.features["allowNewEntries"])

    def test_recovery_after_stabilization_restores_normal_controls_when_costs_and_feeds_are_clean(self) -> None:
        result, _ = evaluate_event(
            event_payload(
                event_type="cpi",
                status="released",
                scheduled_at=OPEN_UTC + timedelta(minutes=10),
                released_at=OPEN_UTC + timedelta(minutes=10),
                provider_timestamp=OPEN_UTC + timedelta(minutes=45),
                actual=0.2,
                surprise_raw=0.0,
            )
        )

        self.assertEqual(result.features["eventPhase"], "NORMALIZED")
        self.assertEqual(result.features["recommendedRiskCap"], 1.0)
        self.assertTrue(result.features["allowNewEntries"])

    def test_backtest_live_replay_context_parity(self) -> None:
        event = event_payload(event_type="pce", importance="medium", scheduled_at=OPEN_UTC + timedelta(minutes=90))
        outputs = {}
        for mode in ("live", "replay", "backtest"):
            result, _ = evaluate_event(event, execution_style=mode)
            outputs[mode] = {
                key: result.features[key]
                for key in ("eventPolicyKey", "eventPhase", "executionMode", "recommendedRiskCap", "allowNewEntries", "required_safety_margin_bps")
            }

        self.assertEqual(outputs["live"], outputs["replay"])
        self.assertEqual(outputs["live"], outputs["backtest"])

    def test_no_future_release_values_are_visible_before_release_time(self) -> None:
        future_release = OPEN_UTC + timedelta(minutes=60)
        result, snapshot = evaluate_event(
            event_payload(
                status="scheduled",
                scheduled_at=future_release,
                released_at=future_release,
                provider_timestamp=OPEN_UTC + timedelta(minutes=45),
                actual=0.5,
                revised_previous=0.2,
                surprise_raw=0.3,
                surprise_pct=150.0,
                surprise_zscore=3.0,
            )
        )

        self.assertIsNone(result.features["actual"])
        self.assertIsNone(result.features["surpriseRaw"])
        self.assertIsNone(snapshot.rawInputs["economicEventState"]["actual"])
        self.assertIsNone(snapshot.rawInputs["economicEventState"]["surpriseRaw"])

    def test_feed_disconnection_during_open_position_blocks_increases(self) -> None:
        result, _ = evaluate_event(event_payload(feed_health="unavailable", provider_timestamp=OPEN_UTC + timedelta(minutes=45)))

        self.assertFalse(result.features["allowNewEntries"])
        self.assertFalse(result.features["allowPositionIncrease"])
        self.assertEqual(result.features["executionMode"], "feed_unavailable")


if __name__ == "__main__":
    unittest.main()
