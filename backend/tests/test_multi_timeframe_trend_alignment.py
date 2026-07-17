from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from pydantic import ValidationError

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
    MultiTimeframeTrendAlignmentConfig,
    MultiTimeframeTrendAlignmentStrategy,
    TimeframeTrendParameters,
    _slope_agreement,
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
        spy5mCandles=synthetic_candles(symbol="SPY", timeframe="5Min", step_minutes=5, drift=drift_5m, end=datetime(2026, 1, 5, 15, 20, tzinfo=UTC)),
        spy15mCandles=synthetic_candles(symbol="SPY", timeframe="15Min", step_minutes=15, drift=drift_15m, end=datetime(2026, 1, 5, 15, 0, tzinfo=UTC)),
        sessionVwap=session_vwap,
        sessionVwapTimestamp=EVALUATION,
        qqqAlignedCandles=synthetic_candles(symbol="QQQ", timeframe="1Min", step_minutes=1, drift=0.02),
        iwmAlignedCandles=synthetic_candles(symbol="IWM", timeframe="1Min", step_minutes=1, drift=0.02),
        priorDayOHLC=PriorDayOHLC(sessionDate=date(2026, 1, 2), open=99, high=101, low=98, close=100),
        quote=BidAskQuote(bid=103, ask=103.02, timestamp=EVALUATION),
        allowExtendedHours=True,
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


def with_feature(snapshot, name: str, value):
    return snapshot.model_copy(
        update={
            "features": {
                **snapshot.features,
                name: snapshot.features[name].model_copy(update={"value": value, "quality": FeatureQuality.READY}),
            }
        }
    )


def evaluate_snapshot(snapshot):
    strategy = MultiTimeframeTrendAlignmentStrategy()
    return strategy.evaluate(
        StrategyEvaluationContext(
            registryEntry=resolve_strategy("multi_timeframe_trend_alignment"),
            featureSnapshot=snapshot,
            configurationHash=strategy.config.configurationHash,
        )
    )


def evaluate_snapshot_with_config(snapshot, config):
    strategy = MultiTimeframeTrendAlignmentStrategy(config)
    return strategy.evaluate(
        StrategyEvaluationContext(
            registryEntry=resolve_strategy("multi_timeframe_trend_alignment"),
            featureSnapshot=snapshot,
            configurationHash=strategy.config.configurationHash,
        )
    )


def with_ema_reclaim_trigger(snapshot, direction: str):
    raw_inputs = dict(snapshot.rawInputs)
    one_minute = [dict(candle) for candle in raw_inputs["spy1mCandles"]]
    ema9 = float(snapshot.features["spy1mEma9"].value)
    if direction == "long":
        previous_close = ema9 - 0.02
        previous_high = ema9 + 0.01
        current_close = previous_high + 0.08
        one_minute[-2].update(
            {
                "open": ema9 - 0.01,
                "high": previous_high,
                "low": ema9 - 0.05,
                "close": previous_close,
            }
        )
        one_minute[-1].update(
            {
                "open": ema9 - 0.01,
                "high": current_close + 0.03,
                "low": ema9 - 0.04,
                "close": current_close,
            }
        )
    else:
        previous_close = ema9 + 0.02
        previous_low = ema9 - 0.01
        current_close = previous_low - 0.08
        one_minute[-2].update(
            {
                "open": ema9 + 0.01,
                "high": ema9 + 0.05,
                "low": previous_low,
                "close": previous_close,
            }
        )
        one_minute[-1].update(
            {
                "open": ema9 + 0.01,
                "high": ema9 + 0.04,
                "low": current_close - 0.03,
                "close": current_close,
            }
        )
    raw_inputs["spy1mCandles"] = one_minute
    return snapshot.model_copy(update={"rawInputs": raw_inputs})


def with_latest_1m_timestamp(snapshot, timestamp: datetime):
    raw_inputs = dict(snapshot.rawInputs)
    one_minute = [dict(candle) for candle in raw_inputs["spy1mCandles"]]
    windows = [dict(window) for window in raw_inputs["spy1mBarWindows"]]
    one_minute[-1]["timestamp"] = timestamp.isoformat().replace("+00:00", "Z")
    windows[-1]["barStartTimestamp"] = timestamp.isoformat().replace("+00:00", "Z")
    windows[-1]["barEndTimestamp"] = (timestamp + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    raw_inputs["spy1mCandles"] = one_minute
    raw_inputs["spy1mBarWindows"] = windows
    return snapshot.model_copy(update={"rawInputs": raw_inputs})


def with_timeframe_quality_reason(snapshot, timeframe: str, reason: str):
    raw_inputs = dict(snapshot.rawInputs)
    quality = {key: dict(value) for key, value in raw_inputs["timeframeQuality"].items()}
    item = quality[timeframe]
    item["reason_codes"] = [*item.get("reason_codes", []), reason]
    if reason == "has_duplicates":
        item["has_duplicates"] = True
    elif reason == "has_gaps":
        item["has_gaps"] = True
    elif reason == "misaligned_boundary":
        item["is_boundary_aligned"] = False
    elif reason == "out_of_order":
        item["is_ordered"] = False
    elif reason == "stale_or_missing_recent":
        item["is_fresh"] = False
    elif reason == "insufficient_history":
        item["has_required_history"] = False
    raw_inputs["timeframeQuality"] = quality
    return snapshot.model_copy(update={"rawInputs": raw_inputs})


class MultiTimeframeTrendAlignmentTest(unittest.TestCase):
    def test_three_bullish_timeframes_generate_buy(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        result = evaluate_snapshot(with_ema_reclaim_trigger(snapshot, "long"))

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertGreater(result.confidence, 0.6)
        self.assertIsNotNone(result.structuralInvalidationPrice)
        self.assertIn("multi_timeframe.bullish_alignment", result.reasonCodes)
        self.assertIn("spy1mEma9", result.features)
        self.assertIn("sessionVwap", result.features)
        evidence = result.features["multiTimeframeBarEvidence"]
        self.assertEqual(evidence["finalizationLagSeconds"], 1)
        self.assertEqual(evidence["hierarchy"], "15m_permission_5m_confirmation_1m_trigger_session_vwap_context")
        self.assertTrue(evidence["sessionVwapContext"]["countedOnce"])
        self.assertIn("vwapDistanceScore", evidence["sessionVwapContext"])
        self.assertNotIn("priceVwap", evidence["timeframes"]["1m"])
        self.assertNotIn("vwapSlope", evidence["timeframes"]["1m"])
        self.assertIn("emaSpreadScore", evidence["timeframes"]["1m"])
        self.assertIn("ema9Slope", evidence["timeframes"]["1m"])
        self.assertIn("ema9SlopeScore", evidence["timeframes"]["1m"])
        self.assertIn("ema20SlopeScore", evidence["timeframes"]["1m"])
        self.assertIn("slopeAgreement", evidence["timeframes"]["1m"])
        self.assertIn("momentumScore", evidence["timeframes"]["1m"])
        self.assertIn("barStartTimestamp", evidence["timeframes"]["5m"])
        self.assertIn("barEndTimestamp", evidence["timeframes"]["5m"])
        self.assertGreater(evidence["timeframes"]["5m"]["barEndTimestamp"], evidence["timeframes"]["5m"]["barStartTimestamp"])
        self.assertEqual(evidence["roles"]["longSetup"]["setupState"], "TRIGGERED")
        self.assertEqual(evidence["roles"]["longSetup"]["triggerType"], "ema_reclaim")
        self.assertIsNotNone(evidence["roles"]["longSetup"]["setupId"])
        self.assertEqual(evidence["invalidationLevels"]["initialStopReference"], "entry_invalidation")
        self.assertEqual(evidence["invalidationLevels"]["entryInvalidation"]["timeframe"], "1m")
        self.assertEqual(evidence["invalidationLevels"]["confirmationInvalidation"]["timeframe"], "5m")
        self.assertEqual(evidence["invalidationLevels"]["permissionInvalidation"]["timeframe"], "15m")
        self.assertEqual(result.structuralInvalidationPrice, evidence["invalidationLevels"]["entryInvalidation"]["level"])
        self.assertGreater(
            evidence["invalidationLevels"]["entryInvalidation"]["level"],
            evidence["invalidationLevels"]["confirmationInvalidation"]["level"],
        )
        trace = evidence["decisionTrace"]
        self.assertEqual(trace["permission15m"]["status"], "PASS")
        self.assertIn("adx", trace["permission15m"])
        self.assertIn("emaRelationship", trace["permission15m"])
        self.assertIn("ema20Slope", trace["permission15m"])
        self.assertIn("ema9Slope", trace["permission15m"])
        self.assertIn("structure", trace["permission15m"])
        self.assertIn("barStartTimestamp", trace["permission15m"])
        self.assertIn("barEndTimestamp", trace["permission15m"])
        self.assertEqual(trace["confirmation5m"]["status"], "PASS")
        self.assertIn("ageSeconds", trace["confirmation5m"])
        self.assertIn("barStartTimestamp", trace["confirmation5m"])
        self.assertIn("barEndTimestamp", trace["confirmation5m"])
        self.assertEqual(trace["trigger1m"]["triggerType"], "ema_reclaim")
        self.assertIsNotNone(trace["trigger1m"]["triggerLevel"])
        self.assertIsNotNone(trace["trigger1m"]["triggerTimestamp"])
        self.assertIn("triggerCandle", trace["trigger1m"])
        self.assertIn("pullbackDepth", trace["trigger1m"])
        self.assertIn("entryLocationQuality", trace["trigger1m"])
        self.assertFalse(trace["trigger1m"]["consumed"])
        self.assertEqual(trace["final"]["signal"], Signal.BUY.value)
        self.assertGreater(trace["final"]["confidence"], 0.6)
        self.assertIn("oppositionPenalty", trace["final"])
        self.assertIn("overextensionPenalty", trace["final"])
        self.assertEqual(trace["final"]["invalidationLevels"]["entryInvalidation"]["timeframe"], "1m")
        self.assertEqual(trace["final"]["setupId"], evidence["roles"]["longSetup"]["setupId"])

    def test_trend_components_are_continuous_with_hysteresis_states(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        result = evaluate_snapshot(with_ema_reclaim_trigger(snapshot, "long"))
        one_minute = result.features["multiTimeframeBarEvidence"]["timeframes"]["1m"]

        self.assertGreater(one_minute["emaSpreadScore"], 0.20)
        self.assertGreater(one_minute["ema20SlopeScore"], 0.20)
        self.assertGreater(one_minute["momentumScore"], 0.20)
        self.assertEqual(one_minute["emaRelation"], 1)
        self.assertEqual(one_minute["emaSlope"], 1)
        self.assertEqual(one_minute["ema9Slope"], 1)
        self.assertEqual(one_minute["slopeAgreement"], "strong_bullish_agreement")
        self.assertEqual(one_minute["momentum"], 1)
        self.assertNotEqual(one_minute["momentumScore"], one_minute["momentum"])

    def test_regime_result_is_separate_from_directional_alignment(self) -> None:
        buy_snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        sell_snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=-0.05, drift_5m=-0.04, drift_15m=-0.03, session_vwap=101))
        buy = evaluate_snapshot(with_ema_reclaim_trigger(buy_snapshot, "long"))
        sell = evaluate_snapshot(with_ema_reclaim_trigger(sell_snapshot, "short"))
        buy_regime = buy.features["multiTimeframeBarEvidence"]["regime"]
        sell_regime = sell.features["multiTimeframeBarEvidence"]["regime"]

        self.assertEqual(buy_regime["regime"], "TRENDING")
        self.assertEqual(sell_regime["regime"], "TRENDING")
        self.assertEqual(buy.regimeFit, buy_regime["regimeSuitability"])
        self.assertEqual(sell.regimeFit, sell_regime["regimeSuitability"])
        for metric in [
            "adxQuality",
            "directionalEfficiencyRatio",
            "choppiness",
            "atrPercentile",
            "vwapCrossingFrequency",
            "emaSeparationStability",
            "structureConsistency",
            "trendDuration",
        ]:
            self.assertIn(metric, buy_regime["metrics"])

    def test_choppy_vwap_crossing_market_classifies_as_range(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        strategy = MultiTimeframeTrendAlignmentStrategy()
        context = StrategyEvaluationContext(
            registryEntry=resolve_strategy("multi_timeframe_trend_alignment"),
            featureSnapshot=snapshot,
            configurationHash=strategy.config.configurationHash,
        )
        states = [strategy._timeframe_state(context, timeframe) for timeframe in ("1m", "5m", "15m")]
        choppy = []
        for index in range(80):
            close = 101.2 if index % 2 == 0 else 100.8
            choppy.append({"open": 101.0, "high": 101.35, "low": 100.65, "close": close, "volume": 1000.0})
        choppy_context = StrategyEvaluationContext(
            registryEntry=resolve_strategy("multi_timeframe_trend_alignment"),
            featureSnapshot=snapshot.model_copy(update={"rawInputs": {**snapshot.rawInputs, "spy1mCandles": choppy, "spy5mCandles": choppy, "spy15mCandles": choppy}}),
            configurationHash=strategy.config.configurationHash,
        )
        regime = strategy._trend_regime_evidence(
            choppy_context,
            [state for state in states if state is not None],
            strategy._session_vwap_context(context),
        )

        self.assertEqual(regime["regime"], "RANGE")
        self.assertLessEqual(regime["regimeSuitability"], 0.45)
        self.assertGreater(regime["metrics"]["vwapCrossingFrequency"], 0.5)

    def test_ema9_and_ema20_slope_agreement_is_classified_separately(self) -> None:
        self.assertEqual(_slope_agreement(1, 1), "strong_bullish_agreement")
        self.assertEqual(_slope_agreement(1, -1), "bullish_trend_under_pullback")
        self.assertEqual(_slope_agreement(-1, 1), "possible_countertrend_bounce")
        self.assertEqual(_slope_agreement(-1, -1), "bearish_agreement")

    def test_adx_adjusts_permission_and_confirmation_quality_without_direction(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        strong = evaluate_snapshot(with_ema_reclaim_trigger(snapshot, "long"))
        low_adx_snapshot = with_feature(snapshot, "spy15mAdx14", 8.0)
        low_adx_snapshot = with_feature(low_adx_snapshot, "spy5mAdx14", 8.0)
        low_adx = evaluate_snapshot(with_ema_reclaim_trigger(low_adx_snapshot, "long"))

        strong_model = strong.features["multiTimeframeBarEvidence"]["confidenceModel"]
        low_model = low_adx.features["multiTimeframeBarEvidence"]["confidenceModel"]

        self.assertEqual(low_adx.signal, Signal.BUY.value)
        self.assertEqual(low_adx.features["multiTimeframeBarEvidence"]["timeframes"]["15m"]["adxRegime"], "very_low")
        self.assertLess(low_model["permissionQuality15m"], strong_model["permissionQuality15m"])
        self.assertLess(low_model["confirmationQuality5m"], strong_model["confirmationQuality5m"])
        self.assertLess(low_adx.confidence, strong.confidence)
        self.assertLess(low_adx.reliability, strong.reliability)

    def test_extreme_adx_marks_late_entry_risk(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        extreme_snapshot = with_feature(snapshot, "spy15mAdx14", 55.0)
        extreme_snapshot = with_feature(extreme_snapshot, "spy5mAdx14", 55.0)
        result = evaluate_snapshot(with_ema_reclaim_trigger(extreme_snapshot, "long"))
        evidence = result.features["multiTimeframeBarEvidence"]

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertEqual(evidence["timeframes"]["15m"]["adxRegime"], "extreme")
        self.assertTrue(evidence["timeframes"]["15m"]["lateEntryRisk"])
        self.assertTrue(evidence["confidenceModel"]["lateEntryRisk"])

    def test_entry_location_gate_blocks_timing_not_trend_classification(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        config = MultiTimeframeTrendAlignmentStrategy().config.model_copy(update={"maxVwapDistanceAtr": 1.0})
        strategy = MultiTimeframeTrendAlignmentStrategy(config)
        result = strategy.evaluate(
            StrategyEvaluationContext(
                registryEntry=resolve_strategy("multi_timeframe_trend_alignment"),
                featureSnapshot=with_ema_reclaim_trigger(snapshot, "long"),
                configurationHash=strategy.config.configurationHash,
            )
        )
        evidence = result.features["multiTimeframeBarEvidence"]

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertIn("1m_entry_location_long_failed", result.reasonCodes)
        self.assertIn("entry_location_long:vwap_overextension", result.reasonCodes)
        self.assertGreater(evidence["timeframes"]["1m"]["score"], config.oneMinuteTriggerThreshold)
        self.assertTrue(evidence["timeframes"]["1m"]["longTrigger"]["active"])
        self.assertFalse(evidence["timeframes"]["1m"]["longEntryLocation"]["allowed"])

    def test_entry_location_gate_checks_range_structure_candles_close_and_volume(self) -> None:
        config = MultiTimeframeTrendAlignmentStrategy().config.model_copy(
            update={"maxEma20DistanceAtr": 2.0, "maxVwapDistanceAtr": 3.0}
        )
        strategy = MultiTimeframeTrendAlignmentStrategy(config)
        candles = [
            {"open": 100.0 + index * 0.01, "high": 100.2 + index * 0.01, "low": 99.9 + index * 0.01, "close": 100.1 + index * 0.01, "volume": 1000.0}
            for index in range(20)
        ]
        candles.append({"open": 100.4, "high": 103.0, "low": 100.0, "close": 100.8, "volume": 4000.0})

        gate = strategy._entry_location_gate("long", candles, ema20=95.0, session_vwap=94.0, atr14=1.0, rolling_high=100.9, rolling_low=98.0)

        self.assertFalse(gate["allowed"])
        self.assertIn("ema20_overextension", gate["reasonCodes"])
        self.assertIn("vwap_overextension", gate["reasonCodes"])
        self.assertIn("trigger_range_too_large", gate["reasonCodes"])
        self.assertIn("opposing_level_too_close", gate["reasonCodes"])
        self.assertIn("late_after_consecutive_directional_candles", gate["reasonCodes"])
        self.assertIn("weak_trigger_close_location", gate["reasonCodes"])
        self.assertIn("volume_exhaustion", gate["reasonCodes"])

    def test_entry_invalidation_applies_atr_min_max_and_buffer_policy(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        config = MultiTimeframeTrendAlignmentStrategy().config.model_copy(
            update={"minInitialStopDistanceAtr": 1.0, "maxInitialStopDistanceAtr": 1.5, "spreadBufferAtr": 0.1}
        )
        strategy = MultiTimeframeTrendAlignmentStrategy(config)
        result = strategy.evaluate(
            StrategyEvaluationContext(
                registryEntry=resolve_strategy("multi_timeframe_trend_alignment"),
                featureSnapshot=with_ema_reclaim_trigger(snapshot, "long"),
                configurationHash=strategy.config.configurationHash,
            )
        )
        entry = result.features["multiTimeframeBarEvidence"]["invalidationLevels"]["entryInvalidation"]

        self.assertEqual(result.structuralInvalidationPrice, entry["level"])
        self.assertLessEqual(entry["distanceAtr"], config.maxInitialStopDistanceAtr + config.spreadBufferAtr)
        self.assertIn("spread_buffer_applied", entry["reasonCodes"])

    def test_hysteresis_uses_entry_and_return_to_neutral_thresholds(self) -> None:
        strategy = MultiTimeframeTrendAlignmentStrategy()

        self.assertEqual(strategy._hysteresis_state(0.21, None), 1)
        self.assertEqual(strategy._hysteresis_state(0.09, 0.25), 1)
        self.assertEqual(strategy._hysteresis_state(0.07, 0.25), 0)
        self.assertEqual(strategy._hysteresis_state(-0.21, None), -1)
        self.assertEqual(strategy._hysteresis_state(-0.09, -0.25), -1)
        self.assertEqual(strategy._hysteresis_state(-0.07, -0.25), 0)

    def test_configuration_relationships_are_validated(self) -> None:
        config = MultiTimeframeTrendAlignmentStrategy().config

        self.assertEqual(config.configVersion, "multi_timeframe_trend_alignment_v2")
        self.assertAlmostEqual(config.totalWeight, 1.0, places=6)
        self.assertLess(config.fifteenMinutePermissionThreshold, config.bullishThreshold)
        self.assertGreater(config.fiveMinuteConfirmationThreshold, config.neutralThreshold)
        self.assertGreater(config.oneMinuteTriggerThreshold, config.neutralThreshold)

        payload = config.model_dump()
        with self.assertRaises(ValidationError):
            MultiTimeframeTrendAlignmentConfig(**{**payload, "strongBullishThreshold": 0.10})
        with self.assertRaises(ValidationError):
            MultiTimeframeTrendAlignmentConfig(**{**payload, "emaRelationWeight": 0.10})
        with self.assertRaises(ValidationError):
            MultiTimeframeTrendAlignmentConfig(**{**payload, "timeframeParameters": {"1m": payload["timeframeParameters"]["1m"]}})
        with self.assertRaises(ValidationError):
            TimeframeTrendParameters(momentumLookback=3, minimumNormalizedMomentum=0.05, momentumReturnToNeutral=0.08)

    def test_timeframe_specific_momentum_and_slope_parameters_are_applied(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        config = MultiTimeframeTrendAlignmentStrategy().config.model_copy(
            update={
                "normalizedEma20SlopeMaximumAtr": 1.0,
                "normalizedMomentumMaximumAtr": 5.0,
                "timeframeParameters": {
                    "1m": TimeframeTrendParameters(momentumLookback=6, minimumNormalizedMomentum=0.95, momentumReturnToNeutral=0.20, slopeLookback=5, minimumNormalizedSlope=0.95, slopeReturnToNeutral=0.20),
                    "5m": TimeframeTrendParameters(momentumLookback=2, minimumNormalizedMomentum=0.05, momentumReturnToNeutral=0.02, slopeLookback=2, minimumNormalizedSlope=0.01, slopeReturnToNeutral=0.005),
                    "15m": TimeframeTrendParameters(momentumLookback=2, minimumNormalizedMomentum=0.05, momentumReturnToNeutral=0.02, slopeLookback=2, minimumNormalizedSlope=0.01, slopeReturnToNeutral=0.005),
                }
            }
        )
        strategy = MultiTimeframeTrendAlignmentStrategy(config)
        result = strategy.evaluate(
            StrategyEvaluationContext(
                registryEntry=resolve_strategy("multi_timeframe_trend_alignment"),
                featureSnapshot=with_ema_reclaim_trigger(snapshot, "long"),
                configurationHash=strategy.config.configurationHash,
            )
        )
        one_minute = result.features["multiTimeframeBarEvidence"]["timeframes"]["1m"]
        five_minute = result.features["multiTimeframeBarEvidence"]["timeframes"]["5m"]

        self.assertEqual(one_minute["parameters"]["momentumLookback"], 6)
        self.assertEqual(one_minute["parameters"]["slopeLookback"], 5)
        self.assertEqual(one_minute["momentum"], 0)
        self.assertEqual(one_minute["emaSlope"], 0)
        self.assertEqual(five_minute["parameters"]["momentumLookback"], 2)
        self.assertEqual(five_minute["momentum"], 1)
        self.assertEqual(five_minute["emaSlope"], 1)

    def test_session_vwap_slope_is_not_counted_inside_each_timeframe_score(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        bullish_vwap = evaluate_snapshot(with_ema_reclaim_trigger(snapshot, "long"))
        adverse_snapshot = with_feature(snapshot, "sessionVwapSlope", -0.5)
        adverse_vwap = evaluate_snapshot(with_ema_reclaim_trigger(adverse_snapshot, "long"))

        bullish_evidence = bullish_vwap.features["multiTimeframeBarEvidence"]
        adverse_evidence = adverse_vwap.features["multiTimeframeBarEvidence"]

        for timeframe in ("1m", "5m", "15m"):
            self.assertEqual(
                adverse_evidence["timeframes"][timeframe]["score"],
                bullish_evidence["timeframes"][timeframe]["score"],
            )
        self.assertEqual(bullish_evidence["sessionVwapContext"]["vwapSlope"], 1)
        self.assertEqual(adverse_evidence["sessionVwapContext"]["vwapSlope"], -1)
        self.assertLess(
            adverse_evidence["confidenceModel"]["sessionVwapContextQuality"],
            bullish_evidence["confidenceModel"]["sessionVwapContextQuality"],
        )
        self.assertLess(adverse_vwap.confidence, bullish_vwap.confidence)

    def test_auxiliary_context_outage_does_not_disable_spy_trend_alignment(self) -> None:
        request = feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101)
        stale_qqq = [candle.model_copy(update={"timestamp": candle.timestamp - timedelta(minutes=10)}) for candle in request.qqqAlignedCandles]
        snapshot = PointInTimeFeatureEngine().compute(
            request.model_copy(update={"qqqAlignedCandles": stale_qqq, "breadthComponents": {}})
        )

        self.assertFalse(snapshot.dataReady)
        self.assertTrue(snapshot.corePriceDataReady)
        self.assertTrue(snapshot.strategyRequiredFeaturesReady)
        self.assertTrue(snapshot.strategyRequiredDataReady)
        self.assertFalse(snapshot.auxiliaryMarketContextReady)
        self.assertFalse(snapshot.auxiliaryContextReady)
        self.assertTrue(snapshot.executionDataReady)
        self.assertFalse(snapshot.globalSnapshotReady)
        self.assertEqual(
            snapshot.rawInputs["readiness"],
            {
                "corePriceDataReady": True,
                "strategyRequiredFeaturesReady": True,
                "strategyRequiredDataReady": True,
                "auxiliaryMarketContextReady": False,
                "auxiliaryContextReady": False,
                "executionDataReady": True,
                "globalSnapshotReady": False,
                "aggregateDataReady": False,
            },
        )

        result = evaluate_snapshot(with_ema_reclaim_trigger(snapshot, "long"))

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertNotIn("required_data_unavailable", result.reasonCodes)

    def test_three_bearish_timeframes_generate_sell(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=-0.05, drift_5m=-0.04, drift_15m=-0.03, session_vwap=101))
        result = evaluate_snapshot(with_ema_reclaim_trigger(snapshot, "short"))

        self.assertEqual(result.signal, Signal.SELL.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertGreater(result.confidence, 0.6)
        self.assertIsNotNone(result.structuralInvalidationPrice)
        self.assertIn("multi_timeframe.bearish_alignment", result.reasonCodes)

    def test_timeframe_roles_require_15m_permission_for_sell(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=-0.05, drift_5m=-0.04, drift_15m=0.03, session_vwap=101))
        result = evaluate_snapshot(with_ema_reclaim_trigger(snapshot, "short"))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertIn("15m_permission_short_failed", result.reasonCodes)
        self.assertTrue(result.features["multiTimeframeBarEvidence"]["roles"]["shortConfirmation"])

    def test_timeframe_roles_require_5m_confirmation_for_sell(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=-0.05, drift_5m=0.04, drift_15m=-0.03, session_vwap=101))
        result = evaluate_snapshot(with_ema_reclaim_trigger(snapshot, "short"))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertIn("5m_confirmation_short_failed", result.reasonCodes)
        self.assertTrue(result.features["multiTimeframeBarEvidence"]["roles"]["shortPermission"])

    def test_timeframe_roles_require_1m_trigger_for_sell(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=-0.05, drift_5m=-0.04, drift_15m=-0.03, session_vwap=101))
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertIn("1m_trigger_short_missing", result.reasonCodes)
        self.assertTrue(result.features["multiTimeframeBarEvidence"]["roles"]["shortPermission"])
        self.assertTrue(result.features["multiTimeframeBarEvidence"]["roles"]["shortConfirmation"])

    def test_consumed_setup_id_does_not_emit_second_trade(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        triggered_snapshot = with_ema_reclaim_trigger(snapshot, "long")
        first = evaluate_snapshot(triggered_snapshot)
        setup_id = first.features["multiTimeframeBarEvidence"]["roles"]["longSetup"]["setupId"]

        raw_inputs = dict(triggered_snapshot.rawInputs)
        raw_inputs["consumedTrendTriggerIds"] = [setup_id]
        consumed = evaluate_snapshot(triggered_snapshot.model_copy(update={"rawInputs": raw_inputs}))

        self.assertEqual(consumed.signal, Signal.HOLD.value)
        self.assertIn("1m_trigger_long_consumed", consumed.reasonCodes)
        evidence = consumed.features["multiTimeframeBarEvidence"]
        trace = evidence["decisionTrace"]
        self.assertEqual(evidence["roles"]["longSetup"]["setupState"], "SIGNAL_EMITTED")
        self.assertEqual(trace["final"]["signal"], Signal.HOLD.value)
        self.assertEqual(trace["final"]["setupState"], "SIGNAL_EMITTED")
        self.assertEqual(trace["final"]["setupId"], setup_id)
        self.assertTrue(trace["trigger1m"]["consumed"])
        self.assertIn("oppositionPenalty", trace["final"])
        self.assertIn("overextensionPenalty", trace["final"])
        self.assertEqual(trace["final"]["invalidationLevels"]["entryInvalidation"]["timeframe"], "1m")

    def test_repeated_evaluation_keeps_same_setup_id_until_consumed(self) -> None:
        snapshot = with_ema_reclaim_trigger(
            PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101)),
            "long",
        )

        first = evaluate_snapshot(snapshot)
        second = evaluate_snapshot(snapshot)

        self.assertEqual(first.signal, Signal.BUY.value)
        self.assertEqual(second.signal, Signal.BUY.value)
        self.assertEqual(
            first.features["multiTimeframeBarEvidence"]["roles"]["longSetup"]["setupId"],
            second.features["multiTimeframeBarEvidence"]["roles"]["longSetup"]["setupId"],
        )

    def test_new_trigger_timestamp_creates_new_setup_only_after_reset(self) -> None:
        snapshot = with_ema_reclaim_trigger(
            PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101)),
            "long",
        )
        first = evaluate_snapshot(snapshot)
        setup_id = first.features["multiTimeframeBarEvidence"]["roles"]["longSetup"]["setupId"]
        consumed_inputs = dict(snapshot.rawInputs)
        consumed_inputs["consumedTrendTriggerIds"] = [setup_id]
        consumed = evaluate_snapshot(snapshot.model_copy(update={"rawInputs": consumed_inputs}))
        reset_snapshot = with_latest_1m_timestamp(snapshot, EVALUATION + timedelta(minutes=1))
        reset_result = evaluate_snapshot(reset_snapshot)

        self.assertEqual(consumed.signal, Signal.HOLD.value)
        self.assertEqual(reset_result.signal, Signal.BUY.value)
        self.assertNotEqual(reset_result.features["multiTimeframeBarEvidence"]["roles"]["longSetup"]["setupId"], setup_id)

    def test_buy_confidence_rewards_only_directional_support(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        strong = evaluate_snapshot(with_ema_reclaim_trigger(snapshot, "long"))
        weaker_snapshot = with_feature(snapshot, "spy15mEma9", 100.0)
        weaker_snapshot = with_feature(weaker_snapshot, "spy15mEma20", 100.0)
        weaker_snapshot = with_feature(weaker_snapshot, "spy15mEma9Slope", 0.0)
        weaker_snapshot = with_feature(weaker_snapshot, "spy15mEma20Slope", 0.0)
        weaker_snapshot = with_feature(weaker_snapshot, "spy15mHigherHighHigherLow", False)
        weaker_snapshot = with_feature(weaker_snapshot, "spy15mLowerHighLowerLow", False)

        weaker = evaluate_snapshot(with_ema_reclaim_trigger(weaker_snapshot, "long"))

        self.assertEqual(weaker.signal, Signal.BUY.value)
        strong_model = strong.features["multiTimeframeBarEvidence"]["confidenceModel"]
        weaker_model = weaker.features["multiTimeframeBarEvidence"]["confidenceModel"]
        self.assertLess(weaker_model["permissionQuality15m"], strong_model["permissionQuality15m"])
        self.assertLess(weaker.confidence, strong.confidence)

    def test_opposing_15m_evidence_lowers_buy_confidence(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        strategy = MultiTimeframeTrendAlignmentStrategy()
        context = StrategyEvaluationContext(
            registryEntry=resolve_strategy("multi_timeframe_trend_alignment"),
            featureSnapshot=with_ema_reclaim_trigger(snapshot, "long"),
            configurationHash=strategy.config.configurationHash,
        )
        one_minute = strategy._timeframe_state(context, "1m")
        five_minute = strategy._timeframe_state(context, "5m")
        fifteen_minute = strategy._timeframe_state(context, "15m")
        session_vwap = strategy._session_vwap_context(context)
        assert one_minute and five_minute and fifteen_minute and session_vwap

        base = strategy._directional_confidence(Signal.BUY, one_minute, five_minute, fifteen_minute, session_vwap, 1.0)
        opposed = strategy._directional_confidence(
            Signal.BUY,
            one_minute,
            five_minute,
            replace(fifteen_minute, score=-0.2, emaSlope=0, structure=0),
            session_vwap,
            1.0,
        )

        self.assertLess(opposed, base)

    def test_negative_slope_agreement_never_improves_buy_confidence(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        strategy = MultiTimeframeTrendAlignmentStrategy()
        context = StrategyEvaluationContext(
            registryEntry=resolve_strategy("multi_timeframe_trend_alignment"),
            featureSnapshot=with_ema_reclaim_trigger(snapshot, "long"),
            configurationHash=strategy.config.configurationHash,
        )
        one_minute = strategy._timeframe_state(context, "1m")
        five_minute = strategy._timeframe_state(context, "5m")
        fifteen_minute = strategy._timeframe_state(context, "15m")
        session_vwap = strategy._session_vwap_context(context)
        assert one_minute and five_minute and fifteen_minute and session_vwap

        strong = strategy._directional_confidence(Signal.BUY, one_minute, five_minute, fifteen_minute, session_vwap, 1.0)
        negative_agreement = strategy._directional_confidence(
            Signal.BUY,
            replace(one_minute, emaSlope=1, ema9Slope=-1, slopeAgreement="bullish_trend_under_pullback"),
            five_minute,
            fifteen_minute,
            session_vwap,
            1.0,
        )

        self.assertLessEqual(negative_agreement, strong)

    def test_positive_slope_agreement_never_improves_sell_confidence(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=-0.05, drift_5m=-0.04, drift_15m=-0.03, session_vwap=101))
        strategy = MultiTimeframeTrendAlignmentStrategy()
        context = StrategyEvaluationContext(
            registryEntry=resolve_strategy("multi_timeframe_trend_alignment"),
            featureSnapshot=with_ema_reclaim_trigger(snapshot, "short"),
            configurationHash=strategy.config.configurationHash,
        )
        one_minute = strategy._timeframe_state(context, "1m")
        five_minute = strategy._timeframe_state(context, "5m")
        fifteen_minute = strategy._timeframe_state(context, "15m")
        session_vwap = strategy._session_vwap_context(context)
        assert one_minute and five_minute and fifteen_minute and session_vwap

        strong = strategy._directional_confidence(Signal.SELL, one_minute, five_minute, fifteen_minute, session_vwap, 1.0)
        positive_agreement = strategy._directional_confidence(
            Signal.SELL,
            replace(one_minute, emaSlope=-1, ema9Slope=1, slopeAgreement="possible_countertrend_bounce"),
            five_minute,
            fifteen_minute,
            session_vwap,
            1.0,
        )

        self.assertLessEqual(positive_agreement, strong)

    def test_stronger_aligned_evidence_increases_confidence_monotonically(self) -> None:
        weaker_snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.03, drift_5m=0.025, drift_15m=0.02, session_vwap=101))
        stronger_snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))

        weaker = evaluate_snapshot(with_ema_reclaim_trigger(weaker_snapshot, "long"))
        stronger = evaluate_snapshot(with_ema_reclaim_trigger(stronger_snapshot, "long"))

        self.assertEqual(weaker.signal, Signal.BUY.value)
        self.assertEqual(stronger.signal, Signal.BUY.value)
        self.assertGreaterEqual(stronger.confidence, weaker.confidence)

    def test_cooldown_state_blocks_valid_trigger(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        triggered_snapshot = with_ema_reclaim_trigger(snapshot, "long")
        raw_inputs = dict(triggered_snapshot.rawInputs)
        raw_inputs["trendTriggerCooldownUntil"] = (EVALUATION + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")

        result = evaluate_snapshot(triggered_snapshot.model_copy(update={"rawInputs": raw_inputs}))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertIn("1m_trigger_long_cooldown", result.reasonCodes)
        self.assertEqual(result.features["multiTimeframeBarEvidence"]["roles"]["longSetup"]["setupState"], "COOLDOWN")

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

    def test_duplicate_timeframe_bars_hold_before_scoring(self) -> None:
        request = feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101)
        request = request.model_copy(update={"spy5mCandles": [*request.spy5mCandles, request.spy5mCandles[-1]]})
        snapshot = PointInTimeFeatureEngine().compute(request)
        result = evaluate_snapshot(with_ema_reclaim_trigger(snapshot, "long"))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("multi_timeframe.timeframe_quality_unavailable", result.reasonCodes)
        self.assertIn("5m:has_duplicates", result.reasonCodes)

    def test_strategy_rejects_data_quality_failures_before_scoring(self) -> None:
        base = with_ema_reclaim_trigger(
            PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101)),
            "long",
        )
        cases = [
            ("5m", "stale_or_missing_recent", "5m:stale_or_missing_recent"),
            ("5m", "out_of_order", "5m:out_of_order"),
            ("5m", "misaligned_boundary", "5m:misaligned_boundary"),
            ("5m", "insufficient_history", "5m:insufficient_history"),
            ("15m", "stale_or_missing_recent", "15m:stale_or_missing_recent"),
        ]

        for timeframe, reason, expected_code in cases:
            with self.subTest(timeframe=timeframe, reason=reason):
                result = evaluate_snapshot(with_timeframe_quality_reason(base, timeframe, reason))
                self.assertEqual(result.signal, Signal.HOLD.value)
                self.assertFalse(result.eligible)
                self.assertIn("multi_timeframe.timeframe_quality_unavailable", result.reasonCodes)
                self.assertIn(expected_code, result.reasonCodes)

    def test_incorrect_session_boundary_makes_strategy_unavailable(self) -> None:
        request = feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101)
        snapshot = PointInTimeFeatureEngine().compute(request.model_copy(update={"sessionDate": date(2026, 1, 4)}))
        result = evaluate_snapshot(with_ema_reclaim_trigger(snapshot, "long"))

        self.assertIn("session_date_mismatch", snapshot.reasonCodes)
        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("required_data_unavailable", result.reasonCodes)

    def test_stale_confirmation_expires_even_with_valid_permission_and_trigger(self) -> None:
        snapshot = with_ema_reclaim_trigger(
            PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101)),
            "long",
        )
        config = MultiTimeframeTrendAlignmentStrategy().config.model_copy(update={"maxConfirmationAgeSeconds": 10})
        result = evaluate_snapshot_with_config(snapshot, config)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertIn("5m_confirmation_stale", result.reasonCodes)

    def test_stale_permission_expires_even_with_valid_confirmation_and_trigger(self) -> None:
        snapshot = with_ema_reclaim_trigger(
            PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101)),
            "long",
        )
        config = MultiTimeframeTrendAlignmentStrategy().config.model_copy(update={"maxPermissionAgeSeconds": 10})
        result = evaluate_snapshot_with_config(snapshot, config)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertIn("15m_permission_stale", result.reasonCodes)

    def test_two_bullish_without_fifteen_minute_permission_holds(self) -> None:
        result = evaluate_request(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=-0.02, session_vwap=101))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("15m_permission_long_failed", result.reasonCodes)
        self.assertIn("aligned_timeframes_buy:2", result.reasonCodes)

    def test_one_and_five_bullish_but_fifteen_mildly_bearish_cannot_buy(self) -> None:
        result = evaluate_request(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=-0.02, session_vwap=101))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertIn("15m_permission_long_failed", result.reasonCodes)
        self.assertIn("aligned_timeframes_buy:2", result.reasonCodes)

    def test_one_and_fifteen_bullish_without_five_confirmation_cannot_buy(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        snapshot = with_feature(snapshot, "spy5mEma9", 99.5)
        snapshot = with_feature(snapshot, "spy5mEma20", 100.0)
        snapshot = with_feature(snapshot, "spy5mEma9Slope", -0.001)
        snapshot = with_feature(snapshot, "spy5mEma20Slope", -0.001)

        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertIn("5m_confirmation_long_failed", result.reasonCodes)

    def test_five_and_fifteen_bullish_without_fresh_one_minute_trigger_cannot_buy(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101))
        raw_inputs = dict(snapshot.rawInputs)
        one_minute = [dict(candle) for candle in raw_inputs["spy1mCandles"]]
        prior_high_close = max(float(candle["close"]) for candle in one_minute[-4:-1])
        one_minute[-1]["close"] = prior_high_close
        one_minute[-1]["high"] = max(float(one_minute[-1]["high"]), prior_high_close)
        raw_inputs["spy1mCandles"] = one_minute
        snapshot = snapshot.model_copy(update={"rawInputs": raw_inputs})

        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertIn("1m_trigger_long_missing", result.reasonCodes)

    def test_invalidation_uses_nearest_1m_trigger_structure_not_higher_timeframe(self) -> None:
        result = evaluate_snapshot(
            with_ema_reclaim_trigger(
                PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101)),
                "long",
            )
        )
        evidence = result.features["multiTimeframeBarEvidence"]
        trigger = evidence["timeframes"]["1m"]["longTrigger"]
        entry = evidence["invalidationLevels"]["entryInvalidation"]

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertEqual(entry["timeframe"], "1m")
        self.assertEqual(entry["rawLevel"], trigger["invalidationLevel"])
        self.assertGreater(entry["level"], evidence["invalidationLevels"]["confirmationInvalidation"]["level"])

    def test_maximum_allowed_stop_distance_caps_entry_invalidation(self) -> None:
        snapshot = with_ema_reclaim_trigger(
            PointInTimeFeatureEngine().compute(feature_request(drift_1m=0.05, drift_5m=0.04, drift_15m=0.03, session_vwap=101)),
            "long",
        )
        config = MultiTimeframeTrendAlignmentStrategy().config.model_copy(
            update={"minInitialStopDistanceAtr": 0.01, "maxInitialStopDistanceAtr": 0.05, "spreadBufferAtr": 0.0}
        )
        result = evaluate_snapshot_with_config(snapshot, config)
        entry = result.features["multiTimeframeBarEvidence"]["invalidationLevels"]["entryInvalidation"]

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertIn("capped_to_maximum_atr_distance", entry["reasonCodes"])
        self.assertLessEqual(entry["distanceAtr"], config.maxInitialStopDistanceAtr)

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
