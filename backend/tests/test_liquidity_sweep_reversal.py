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
from backend.app.domain.models import Signal
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.directional.liquidity_sweep_reversal import (
    LiquiditySweepReversalConfig,
    LiquiditySweepReversalStrategy,
)
from backend.app.strategies.registry import resolve_strategy


SESSION_DATE = date(2026, 1, 5)
OPEN_UTC = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


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


def base_session(count: int = 38) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        base = 100.0 + (0.02 if minute % 2 else -0.02)
        rows.append(candle_at(minute, base, 100.16, 99.84, 100.0 - (0.01 if minute % 2 else -0.01)))
    return rows


def continued_upside_breakout_sequence() -> list[MarketCandle]:
    rows = base_session()
    rows.append(candle_at(38, 100.22, 100.74, 100.18, 100.58, 180000, 2200))
    rows.append(candle_at(39, 100.58, 100.82, 100.50, 100.70, 190000, 2300))
    return rows


def upside_sweep_sequence() -> list[MarketCandle]:
    rows = base_session()
    rows.append(candle_at(38, 100.08, 100.74, 100.02, 100.14, 170000, 2200))
    rows.append(candle_at(39, 100.14, 100.20, 99.94, 100.02, 190000, 2400))
    return rows


def downside_sweep_sequence() -> list[MarketCandle]:
    rows = base_session()
    rows.append(candle_at(38, 99.92, 99.98, 99.26, 99.86, 170000, 2200))
    rows.append(candle_at(39, 99.86, 100.06, 99.80, 99.98, 190000, 2400))
    return rows


def no_reference_sequence() -> list[MarketCandle]:
    return base_session(40)


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


def request_for(candles: list[MarketCandle], *, include_reference_levels: bool = True) -> PointInTimeFeatureRequest:
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
        priorDayOHLC=PriorDayOHLC(sessionDate=date(2026, 1, 2), open=100, high=100.65, low=99.35, close=100)
        if include_reference_levels
        else None,
        premarket=PremarketLevels(high=100.45, low=99.55, sourceTimestamp=OPEN_UTC - timedelta(minutes=1))
        if include_reference_levels
        else None,
        openingRange=OpeningRangeLevels(
            high=100.20,
            low=99.80,
            startTimestamp=OPEN_UTC,
            endTimestamp=OPEN_UTC + timedelta(minutes=14),
        )
        if include_reference_levels
        else None,
        quote=BidAskQuote(bid=candles[-1].close - 0.01, ask=candles[-1].close + 0.01, timestamp=evaluation),
        breadthComponents={"XLK": auxiliary_candles("XLK")},
    )


def evaluate(candles: list[MarketCandle], config: LiquiditySweepReversalConfig | None = None, *, include_reference_levels: bool = True):
    snapshot = PointInTimeFeatureEngine().compute(request_for(candles, include_reference_levels=include_reference_levels))
    return evaluate_snapshot(snapshot, config)


def evaluate_snapshot(snapshot, config: LiquiditySweepReversalConfig | None = None):
    strategy = LiquiditySweepReversalStrategy(config)
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("liquidity_sweep_reversal"),
        featureSnapshot=snapshot,
        configurationHash=strategy.config.configurationHash,
    )
    return strategy.evaluate(context)


class LiquiditySweepReversalTest(unittest.TestCase):
    def test_continued_move_beyond_level_is_not_sweep_reversal(self) -> None:
        result = evaluate(continued_upside_breakout_sequence())

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("liquidity_sweep.continued_beyond_level", result.reasonCodes)

    def test_upside_sweep_reclaim_can_produce_sell(self) -> None:
        result = evaluate(upside_sweep_sequence())

        self.assertEqual(result.signal, Signal.SELL.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("liquidity_sweep.reversal_confirmed", result.reasonCodes)

    def test_downside_sweep_reclaim_can_produce_buy(self) -> None:
        result = evaluate(downside_sweep_sequence())

        self.assertEqual(result.signal, Signal.BUY.value)
        self.assertTrue(result.eligible)
        self.assertTrue(result.dataReady)
        self.assertIn("liquidity_sweep.reversal_confirmed", result.reasonCodes)

    def test_missing_reference_levels_produce_ineligible_hold(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(no_reference_sequence()))
        raw_inputs = {
            **snapshot.rawInputs,
            "priorDayOHLC": None,
            "premarket": None,
            "openingRange": None,
        }
        result = evaluate_snapshot(
            snapshot.model_copy(update={"rawInputs": raw_inputs}),
            LiquiditySweepReversalConfig(includeDerivedSessionLevels=False),
        )

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertIn("liquidity_sweep.no_reference_levels", result.reasonCodes)

    def test_missing_required_data_generates_hold_not_eligible(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(upside_sweep_sequence()))
        features = {
            **snapshot.features,
            "spy1mRelativeVolume": snapshot.features["spy1mRelativeVolume"].model_copy(update={"quality": FeatureQuality.MISSING}),
        }
        result = evaluate_snapshot(snapshot.model_copy(update={"features": features}))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.eligible)
        self.assertFalse(result.dataReady)
        self.assertIn("required_data_unavailable", result.reasonCodes)

    def test_changing_only_event_direction_does_not_change_result(self) -> None:
        snapshot = PointInTimeFeatureEngine().compute(request_for(upside_sweep_sequence()))
        base = evaluate_snapshot(snapshot)
        features_with_event_direction = {
            **snapshot.features,
            "event.directionBias": snapshot.features["spreadDollars"].model_copy(update={"value": "long"}),
        }
        changed = evaluate_snapshot(snapshot.model_copy(update={"features": features_with_event_direction}))

        self.assertEqual(changed.signal, base.signal)
        self.assertEqual(changed.confidence, base.confidence)
        self.assertEqual(changed.reasonCodes, base.reasonCodes)


if __name__ == "__main__":
    unittest.main()
