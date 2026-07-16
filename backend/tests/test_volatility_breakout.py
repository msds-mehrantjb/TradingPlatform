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
from backend.app.domain.models import Signal
from backend.app.ensemble.diagnostics import strategy_signal_correlation
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.directional.opening_range_breakout import OpeningRangeBreakoutStrategy
from backend.app.strategies.directional.volatility_breakout import VolatilityBreakoutStrategy
from backend.app.strategies.registry import resolve_strategy


SESSION_DATE = date(2026, 1, 5)
OPEN_UTC = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def candle_at(minute: int, open_price: float, high: float, low: float, close: float, volume: float, trade_count: int = 1000) -> MarketCandle:
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


def compressed_base(count: int = 60, *, center: float = 100.0, width: float = 0.05, volume: float = 100000) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        wobble = 0.01 if minute % 2 else -0.01
        open_price = center + wobble
        close = center - wobble
        rows.append(candle_at(minute, open_price, center + width, center - width, close, volume))
    return rows


def volatile_buy_sequence() -> list[MarketCandle]:
    rows = compressed_base()
    rows.append(candle_at(60, 100.02, 100.72, 99.98, 100.62, 240000, 2200))
    return rows


def volatile_sell_sequence() -> list[MarketCandle]:
    rows = compressed_base()
    rows.append(candle_at(60, 99.98, 100.02, 99.28, 99.38, 240000, 2200))
    return rows


def expansion_without_level_break_sequence() -> list[MarketCandle]:
    rows = compressed_base(width=0.08)
    rows.append(candle_at(60, 100.00, 100.07, 99.30, 100.02, 240000, 2200))
    return rows


def level_break_without_vol_expansion_sequence() -> list[MarketCandle]:
    rows = compressed_base(width=0.30)
    rows.append(candle_at(60, 100.22, 100.44, 100.20, 100.40, 240000, 2200))
    return rows


def opening_range_only_sequence() -> list[MarketCandle]:
    rows = []
    for minute in range(15):
        rows.append(candle_at(minute, 100.00, 100.20, 99.80, 100.00 + (0.02 if minute % 2 else -0.02), 100000))
    for minute in range(15, 60):
        rows.append(candle_at(minute, 100.00, 100.16, 99.86, 100.02 if minute % 2 else 99.98, 95000))
    rows.append(candle_at(60, 100.08, 100.48, 100.05, 100.42, 130000, 1800))
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
        rows.append(candle_at(minute, base, base + 0.08, base - 0.08, base + 0.01, 100000).model_copy(update={"symbol": symbol}))
    return rows


def request_for(candles: list[MarketCandle]) -> PointInTimeFeatureRequest:
    evaluation = candles[-1].timestamp
    return PointInTimeFeatureRequest(
        evaluationTimestamp=evaluation,
        sessionDate=SESSION_DATE,
        spy1mCandles=candles,
        spy5mCandles=timeframe_history(end=evaluation, step_minutes=5, timeframe="5Min"),
        spy15mCandles=timeframe_history(end=evaluation, step_minutes=15, timeframe="15Min"),
        sessionVwap=sum(c.close * c.volume for c in candles) / sum(c.volume for c in candles),
        sessionVwapTimestamp=evaluation,
        qqqAlignedCandles=auxiliary_candles("QQQ"),
        iwmAlignedCandles=auxiliary_candles("IWM"),
        priorDayOHLC=PriorDayOHLC(sessionDate=date(2026, 1, 2), open=99, high=101, low=98, close=100),
        quote=BidAskQuote(bid=candles[-1].close - 0.01, ask=candles[-1].close + 0.01, timestamp=evaluation),
        breadthComponents={"XLK": auxiliary_candles("XLK")},
    )


def snapshot_for(candles: list[MarketCandle]):
    return PointInTimeFeatureEngine().compute(request_for(candles))


def evaluate_volatility(snapshot):
    strategy = VolatilityBreakoutStrategy()
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("volatility_breakout"),
        featureSnapshot=snapshot,
        configurationHash=strategy.config.configurationHash,
    )
    return strategy.evaluate(context)


def evaluate_opening_range(snapshot):
    strategy = OpeningRangeBreakoutStrategy()
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("opening_range_breakout"),
        featureSnapshot=snapshot,
        configurationHash=strategy.config.configurationHash,
    )
    return strategy.evaluate(context)


class VolatilityBreakoutTest(unittest.TestCase):
    def test_expansion_without_level_break_produces_hold(self) -> None:
        result = evaluate_volatility(snapshot_for(expansion_without_level_break_sequence()))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("volatility_breakout.no_level_break", result.reasonCodes)

    def test_level_break_without_volatility_expansion_produces_hold(self) -> None:
        result = evaluate_volatility(snapshot_for(level_break_without_vol_expansion_sequence()))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("volatility_breakout.no_volatility_expansion", result.reasonCodes)

    def test_valid_bullish_volatility_breakout_produces_buy(self) -> None:
        result = evaluate_volatility(snapshot_for(volatile_buy_sequence()))

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("volatility_breakout.confirmed", result.reasonCodes)
        self.assertIn("spy1mBollingerWidthPercentile", result.features)

    def test_valid_bearish_volatility_breakout_produces_sell(self) -> None:
        result = evaluate_volatility(snapshot_for(volatile_sell_sequence()))

        self.assertEqual(result.signal, Signal.SELL.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("volatility_breakout.confirmed", result.reasonCodes)

    def test_opening_range_breakout_and_volatility_breakout_are_not_identical(self) -> None:
        snapshots = [
            snapshot_for(opening_range_only_sequence()),
            snapshot_for(volatile_buy_sequence()),
            snapshot_for(expansion_without_level_break_sequence()),
        ]
        observations = []
        for index, snapshot in enumerate(snapshots):
            orb = evaluate_opening_range(snapshot)
            vol = evaluate_volatility(snapshot)
            observations.extend(
                [
                    {"strategyId": "opening_range_breakout", "decisionKey": str(index), "signal": orb.signal},
                    {"strategyId": "volatility_breakout", "decisionKey": str(index), "signal": vol.signal},
                ]
            )

        diagnostics = strategy_signal_correlation(
            observations,
            strategy_a="opening_range_breakout",
            strategy_b="volatility_breakout",
        )

        self.assertLess(diagnostics.identicalSignalRate, 1.0)
        self.assertLess(diagnostics.entryOverlapRate, 1.0)

    def test_correlation_diagnostics_report_overlap_for_later_ensemble_evaluation(self) -> None:
        diagnostics = strategy_signal_correlation(
            [
                {"strategyId": "opening_range_breakout", "decisionKey": "a", "signal": "BUY"},
                {"strategyId": "volatility_breakout", "decisionKey": "a", "signal": "HOLD"},
                {"strategyId": "opening_range_breakout", "decisionKey": "b", "signal": "HOLD"},
                {"strategyId": "volatility_breakout", "decisionKey": "b", "signal": "BUY"},
                {"strategyId": "opening_range_breakout", "decisionKey": "c", "signal": "SELL"},
                {"strategyId": "volatility_breakout", "decisionKey": "c", "signal": "SELL"},
            ],
            strategy_a="opening_range_breakout",
            strategy_b="volatility_breakout",
        )

        self.assertEqual(diagnostics.version, "strategy_signal_correlation_v1")
        self.assertEqual(diagnostics.observations, 3)
        self.assertEqual(diagnostics.simultaneousEntries, 1)
        self.assertLess(diagnostics.identicalSignalRate, 1.0)

    def test_missing_required_data_generates_hold_not_eligible(self) -> None:
        snapshot = snapshot_for(volatile_buy_sequence())
        features = {
            **snapshot.features,
            "spy1mRealizedVolatilityPercentile": snapshot.features["spy1mRealizedVolatilityPercentile"].model_copy(update={"quality": FeatureQuality.MISSING}),
        }
        result = evaluate_volatility(snapshot.model_copy(update={"features": features}))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertFalse(result.dataReady)
        self.assertIn("required_data_unavailable", result.reasonCodes)

    def test_changing_only_event_direction_field_does_not_change_result(self) -> None:
        snapshot = snapshot_for(volatile_buy_sequence())
        base = evaluate_volatility(snapshot)
        features_with_event_direction = {
            **snapshot.features,
            "event.directionBias": snapshot.features["spreadDollars"].model_copy(update={"value": "short"}),
        }
        changed = evaluate_volatility(snapshot.model_copy(update={"features": features_with_event_direction}))

        self.assertEqual(changed.signal, base.signal)
        self.assertEqual(changed.confidence, base.confidence)
        self.assertEqual(changed.reasonCodes, base.reasonCodes)


if __name__ == "__main__":
    unittest.main()
