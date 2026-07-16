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
from backend.app.domain.models import Direction
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.regime import AdxAtrRegimeClassifier, AdxAtrRegimeConfig
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


def trend_candles(count: int = 90, *, drift: float = 0.08) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        base = 100 + minute * drift
        rows.append(candle_at(minute, base - 0.02, base + 0.10, base - 0.06, base + 0.04, 120000 + minute * 200))
    return rows


def flat_range_candles(count: int = 90) -> list[MarketCandle]:
    return [candle_at(minute, 100.0, 100.08, 99.92, 100.0, 100000) for minute in range(count)]


def low_volatility_candles(count: int = 90) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        width = 0.22 if minute < count - 15 else 0.04
        rows.append(candle_at(minute, 100.0, 100.0 + width, 100.0 - width, 100.0, 90000))
    return rows


def event_shock_candles(count: int = 90) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        width = 0.08 if minute < 70 else 0.38
        close = 100.0 + max(0, minute - 70) * 0.06
        rows.append(candle_at(minute, close - 0.02, close + width, close - width, close + 0.02, 200000))
    return rows


def auxiliary_candles(symbol: str, count: int = 100) -> list[MarketCandle]:
    return [
        candle_at(
            minute,
            100 + minute * 0.01,
            100 + minute * 0.01 + 0.08,
            100 + minute * 0.01 - 0.08,
            100 + minute * 0.01 + 0.01,
        ).model_copy(update={"symbol": symbol})
        for minute in range(count)
    ]


def timeframe_history(*, end: datetime, step_minutes: int, timeframe: str, count: int = 90) -> list[MarketCandle]:
    start = end - timedelta(minutes=step_minutes * (count - 1))
    rows: list[MarketCandle] = []
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
                volume=150000,
                tradeCount=5000 + index,
                provider="fixture",
                symbol="SPY",
                timeframe=timeframe,  # type: ignore[arg-type]
            )
        )
    return rows


def request_for(candles: list[MarketCandle], *, event: dict | None = None) -> PointInTimeFeatureRequest:
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
        quote=BidAskQuote(bid=candles[-1].close - 0.02, ask=candles[-1].close + 0.02, timestamp=evaluation),
        economicEventState=event or {"active": False, "category": "none"},
        breadthComponents={"XLK": auxiliary_candles("XLK")},
    )


def snapshot_for(candles: list[MarketCandle], *, event: dict | None = None):
    return PointInTimeFeatureEngine().compute(request_for(candles, event=event))


def with_feature(snapshot, name: str, *, value=None, quality: FeatureQuality | None = None, source_timestamp=None):
    feature = snapshot.features[name]
    updates = {}
    if value is not None:
        updates["value"] = value
    if quality is not None:
        updates["quality"] = quality
    if source_timestamp is not None:
        updates["sourceTimestamp"] = source_timestamp
    return snapshot.model_copy(update={"features": {**snapshot.features, name: feature.model_copy(update=updates)}})


def evaluate(snapshot, config: AdxAtrRegimeConfig | None = None, strategy_id: str = "adx_trend_strength_regime"):
    module = AdxAtrRegimeClassifier(config)
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy(strategy_id),
        featureSnapshot=snapshot,
        configurationHash=module.config.configurationHash,
    )
    return module.evaluate(context)


class AdxAtrRegimeClassifierTest(unittest.TestCase):
    def test_phase0_characterizes_current_regime_outputs_for_deterministic_scenarios(self) -> None:
        event = {"active": True, "importance": "high", "eventTimestamp": (OPEN_UTC + timedelta(minutes=89)).isoformat()}
        scenarios = {
            "strong_uptrend": evaluate(snapshot_for(trend_candles())),
            "strong_downtrend": evaluate(snapshot_for(trend_candles(drift=-0.08))),
            "range": evaluate(snapshot_for(flat_range_candles())),
            "low_volatility": evaluate(snapshot_for(low_volatility_candles())),
            "event_shock": evaluate(
                snapshot_for(event_shock_candles(), event=event),
                AdxAtrRegimeConfig(highAtrPercentile=0.60),
            ),
        }
        expected = {
            "strong_uptrend": {
                "label": "strong_trend",
                "direction": 1,
                "volatility": "NORMAL",
                "confidence": 0.9486,
                "trendFit": 0.9254,
                "breakoutFit": 0.7761,
                "reversalFit": 0.0887,
                "meanReversionFit": 0.2239,
                "gapSessionFit": 0.4387,
                "rangeTrendClassification": "trend",
                "volatilityExpansionContraction": "stable",
                "reasonCodes": ["regime.strong_trend", "regime.volatility_stable"],
            },
            "strong_downtrend": {
                "label": "strong_trend",
                "direction": -1,
                "volatility": "HIGH",
                "confidence": 0.9802,
                "trendFit": 0.9825,
                "breakoutFit": 0.9476,
                "reversalFit": 0.2889,
                "meanReversionFit": 0.0524,
                "gapSessionFit": 0.6389,
                "rangeTrendClassification": "trend",
                "volatilityExpansionContraction": "stable",
                "reasonCodes": ["regime.strong_trend", "regime.volatility_stable"],
            },
            "range": {
                "label": "range",
                "direction": 0,
                "volatility": "NORMAL",
                "confidence": 0.7,
                "trendFit": 0.125,
                "breakoutFit": 0.2125,
                "reversalFit": 0.775,
                "meanReversionFit": 0.8125,
                "gapSessionFit": 0.375,
                "rangeTrendClassification": "range",
                "volatilityExpansionContraction": "stable",
                "reasonCodes": ["regime.range", "regime.volatility_stable"],
            },
            "low_volatility": {
                "label": "low_volatility",
                "direction": 0,
                "volatility": "LOW",
                "confidence": 0.7974,
                "trendFit": 0.1007,
                "breakoutFit": 0.0395,
                "reversalFit": 0.6898,
                "meanReversionFit": 0.8855,
                "gapSessionFit": 0.2898,
                "rangeTrendClassification": "range",
                "volatilityExpansionContraction": "contraction",
                "reasonCodes": ["regime.low_volatility", "regime.volatility_contraction"],
            },
            "event_shock": {
                "label": "event_shock",
                "direction": 1,
                "volatility": "EXTREME",
                "confidence": 1.0,
                "trendFit": 0.9973,
                "breakoutFit": 1.0,
                "reversalFit": 0.3407,
                "meanReversionFit": 0.008,
                "gapSessionFit": 0.7907,
                "rangeTrendClassification": "unstable",
                "volatilityExpansionContraction": "expansion",
                "reasonCodes": ["regime.event_shock", "regime.volatility_expansion"],
            },
        }

        for name, result in scenarios.items():
            with self.subTest(name=name):
                self.assertEqual(
                    {
                        "label": result.label,
                        "direction": int(result.direction),
                        "volatility": result.volatility,
                        "confidence": result.confidence,
                        "trendFit": result.features["trendFit"],
                        "breakoutFit": result.features["breakoutFit"],
                        "reversalFit": result.features["reversalFit"],
                        "meanReversionFit": result.features["meanReversionFit"],
                        "gapSessionFit": result.features["gapSessionFit"],
                        "rangeTrendClassification": result.features["rangeTrendClassification"],
                        "volatilityExpansionContraction": result.features["volatilityExpansionContraction"],
                        "reasonCodes": result.features["reasonCodes"],
                    },
                    expected[name],
                )

    def test_regime_modules_are_not_directional_voters(self) -> None:
        for name in ["ADX Trend Strength Regime", "ATR Volatility Regime"]:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "not a directional voter"):
                    directional_voters_from([name])

    def test_strong_trend_decreases_mean_reversion_fit(self) -> None:
        result = evaluate(snapshot_for(trend_candles()))

        self.assertEqual(result.label, "strong_trend")
        self.assertEqual(result.direction, Direction.LONG)
        self.assertTrue(result.features["dataReady"])
        self.assertLessEqual(result.features["meanReversionFit"], 0.30)
        self.assertGreater(result.features["trendFit"], result.features["meanReversionFit"])
        self.assertNotEqual(result.features["trendFit"], result.confidence)

    def test_range_lowers_trend_and_breakout_fit(self) -> None:
        result = evaluate(snapshot_for(flat_range_candles()))

        self.assertEqual(result.label, "range")
        self.assertEqual(result.direction, Direction.FLAT)
        self.assertLessEqual(result.features["trendFit"], 0.35)
        self.assertLessEqual(result.features["breakoutFit"], 0.35)
        self.assertGreater(result.features["meanReversionFit"], result.features["trendFit"])

    def test_low_volatility_contraction_is_explicit(self) -> None:
        result = evaluate(snapshot_for(low_volatility_candles()))

        self.assertEqual(result.label, "low_volatility")
        self.assertEqual(result.volatility, "LOW")
        self.assertEqual(result.features["volatilityExpansionContraction"], "contraction")
        self.assertLessEqual(result.features["breakoutFit"], 0.35)

    def test_event_shock_is_context_only_and_does_not_create_strategy_output(self) -> None:
        event = {"active": True, "importance": "high", "eventTimestamp": (OPEN_UTC + timedelta(minutes=89)).isoformat()}
        result = evaluate(snapshot_for(event_shock_candles(), event=event), AdxAtrRegimeConfig(highAtrPercentile=0.60))

        self.assertEqual(result.label, "event_shock")
        self.assertEqual(result.volatility, "EXTREME")
        self.assertTrue(result.features["directionalBiasContextOnly"])
        self.assertTrue(result.features["directionMustNotSubstituteStrategySignal"])
        self.assertNotIn("signal", result.model_dump())

    def test_unknown_or_stale_regime_data_is_explicit(self) -> None:
        snapshot = snapshot_for(trend_candles())
        stale_at = snapshot.evaluationTimestamp - timedelta(minutes=5)
        snapshot = with_feature(snapshot, "spy1mAdx14", source_timestamp=stale_at)
        result = evaluate(snapshot, AdxAtrRegimeConfig(maxFeatureAgeSeconds=60))

        self.assertEqual(result.label, "unknown")
        self.assertEqual(result.confidence, 0.0)
        self.assertFalse(result.features["dataReady"])
        self.assertIn("regime.stale:spy1mAdx14", result.features["reasonCodes"])

    def test_missing_regime_input_is_not_fabricated(self) -> None:
        snapshot = with_feature(snapshot_for(trend_candles()), "spy1mAtr14", quality=FeatureQuality.MISSING)
        result = evaluate(snapshot)

        self.assertEqual(result.label, "unknown")
        self.assertEqual(result.direction, Direction.FLAT)
        self.assertFalse(result.features["dataReady"])
        self.assertIn("regime.missing_or_unready:spy1mAtr14", result.features["reasonCodes"])

    def test_atr_registry_entry_uses_same_single_regime_state(self) -> None:
        result = evaluate(snapshot_for(trend_candles(drift=-0.08)), strategy_id="atr_volatility_regime")

        self.assertEqual(result.regimeId, "adx_atr_regime")
        self.assertEqual(result.direction, Direction.SHORT)
        self.assertIn(result.label, {"strong_trend", "weak_trend", "high_volatility"})


if __name__ == "__main__":
    unittest.main()
