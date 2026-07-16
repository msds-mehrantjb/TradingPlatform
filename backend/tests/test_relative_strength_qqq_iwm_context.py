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
from backend.app.strategies.context.relative_strength_qqq_iwm import (
    RelativeStrengthQqqIwmContext,
)
from backend.app.strategies.registry import StrategyCollection, resolve_strategy


SESSION_DATE = date(2026, 1, 5)
OPEN_UTC = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def candle_at(minute: int, close: float, *, symbol: str = "SPY", volume: float = 100000) -> MarketCandle:
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


def candles(symbol: str, *, start: float, step: float, count: int = 40, stale_shift_minutes: int = 0) -> list[MarketCandle]:
    rows: list[MarketCandle] = []
    for minute in range(count):
        row = candle_at(minute - stale_shift_minutes, start + minute * step, symbol=symbol)
        rows.append(row)
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


def request_for(
    *,
    spy: list[MarketCandle],
    qqq: list[MarketCandle],
    iwm: list[MarketCandle],
    max_auxiliary_age_seconds: int = 300,
) -> PointInTimeFeatureRequest:
    evaluation = spy[-1].timestamp
    return PointInTimeFeatureRequest(
        evaluationTimestamp=evaluation,
        sessionDate=SESSION_DATE,
        spy1mCandles=spy,
        spy5mCandles=timeframe_history(end=evaluation, step_minutes=5, timeframe="5Min"),
        spy15mCandles=timeframe_history(end=evaluation, step_minutes=15, timeframe="15Min"),
        sessionVwap=spy[-1].close,
        sessionVwapTimestamp=evaluation,
        qqqAlignedCandles=qqq,
        iwmAlignedCandles=iwm,
        priorDayOHLC=PriorDayOHLC(sessionDate=date(2026, 1, 2), open=99, high=101, low=98, close=100),
        quote=BidAskQuote(bid=spy[-1].close - 0.01, ask=spy[-1].close + 0.01, timestamp=evaluation),
        breadthComponents={"XLK": candles("XLK", start=100, step=0.01)},
        maxAuxiliaryAgeSeconds=max_auxiliary_age_seconds,
    )


def evaluate_snapshot(snapshot):
    context_module = RelativeStrengthQqqIwmContext()
    context = StrategyEvaluationContext(
        registryEntry=resolve_strategy("relative_strength_qqq_iwm"),
        featureSnapshot=snapshot,
        configurationHash=context_module.config.configurationHash,
    )
    return context_module.evaluate(context)


def snapshot_for(spy_step: float, qqq_step: float, iwm_step: float, *, stale_shift_minutes: int = 0):
    spy = candles("SPY", start=100, step=spy_step)
    qqq = candles("QQQ", start=100, step=qqq_step, stale_shift_minutes=stale_shift_minutes)
    iwm = candles("IWM", start=100, step=iwm_step, stale_shift_minutes=stale_shift_minutes)
    return PointInTimeFeatureEngine().compute(
        request_for(spy=spy, qqq=qqq, iwm=iwm, max_auxiliary_age_seconds=60 if stale_shift_minutes else 300)
    )


def ensemble_with_context_only(context_signal) -> Signal:
    if context_signal.signal != Signal.HOLD.value:
        return Signal(context_signal.signal)
    return Signal.HOLD


class RelativeStrengthQqqIwmContextTest(unittest.TestCase):
    def test_registry_entry_is_context_not_directional(self) -> None:
        entry = resolve_strategy("relative_strength_qqq_iwm")

        self.assertEqual(entry.collection, StrategyCollection.CONTEXT.value)

    def test_positive_relative_strength_uses_actual_qqq_iwm_data(self) -> None:
        result = evaluate_snapshot(snapshot_for(spy_step=0.08, qqq_step=0.02, iwm_step=0.01))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertEqual(result.direction, Direction.FLAT)
        self.assertTrue(result.dataReady)
        self.assertGreater(result.features["relativeReturns"]["5"], 0)
        self.assertIn("long", result.features["contextEffect"])

    def test_negative_relative_strength_uses_actual_qqq_iwm_data(self) -> None:
        result = evaluate_snapshot(snapshot_for(spy_step=0.01, qqq_step=0.08, iwm_step=0.07))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertEqual(result.direction, Direction.FLAT)
        self.assertTrue(result.dataReady)
        self.assertLess(result.features["relativeReturns"]["5"], 0)
        self.assertIn("short", result.features["contextEffect"])

    def test_stale_auxiliary_data_cannot_fall_back_to_spy_session_direction(self) -> None:
        result = evaluate_snapshot(snapshot_for(spy_step=0.08, qqq_step=0.02, iwm_step=0.01, stale_shift_minutes=10))

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertEqual(result.direction, Direction.FLAT)
        self.assertFalse(result.dataReady)
        self.assertIn("relative_strength.feature_snapshot_not_ready", result.features["reasonCodes"])

    def test_context_alone_cannot_turn_hold_ensemble_into_buy_or_sell(self) -> None:
        context_signal = evaluate_snapshot(snapshot_for(spy_step=0.08, qqq_step=0.02, iwm_step=0.01))

        self.assertEqual(context_signal.signal, Signal.HOLD.value)
        self.assertEqual(ensemble_with_context_only(context_signal), Signal.HOLD)

    def test_missing_horizon_history_is_not_ready(self) -> None:
        spy = candles("SPY", start=100, step=0.08, count=10)
        qqq = candles("QQQ", start=100, step=0.02, count=10)
        iwm = candles("IWM", start=100, step=0.01, count=10)
        snapshot = PointInTimeFeatureEngine().compute(request_for(spy=spy, qqq=qqq, iwm=iwm))
        result = evaluate_snapshot(snapshot)

        self.assertEqual(result.signal, Signal.HOLD.value)
        self.assertFalse(result.dataReady)
        self.assertIn("relative_strength.missing_horizon:15", result.features["reasonCodes"])


if __name__ == "__main__":
    unittest.main()
