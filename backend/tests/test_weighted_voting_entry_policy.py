from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.weighted_voting.aggregation import aggregate_weighted_signals
from backend.app.algorithms.weighted_voting.dynamic_settings import default_dynamic_envelope, default_hard_limits, default_weighted_settings, resolve_effective_settings
from backend.app.algorithms.weighted_voting.entry_policy import WeightedEntryType, evaluate_entry_policy
from backend.app.algorithms.weighted_voting.models import (
    WeightedCandle,
    WeightedDataQualityStatus,
    WeightedMarketSnapshot,
    WeightedSide,
    WeightedStrategyFamily,
    WeightedVotingSignal,
)


TS = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


class WeightedVotingEntryPolicyTest(unittest.TestCase):
    def test_entry_type_depends_on_dominant_setup_family(self) -> None:
        settings = effective_settings()
        trend = evaluate_entry_policy(decision=decision_for(WeightedSide.BUY, WeightedStrategyFamily.TREND), signals=signals_for(WeightedSide.BUY, WeightedStrategyFamily.TREND), snapshot=trend_snapshot(), effective_settings=settings)
        breakout = evaluate_entry_policy(decision=decision_for(WeightedSide.BUY, WeightedStrategyFamily.BREAKOUT), signals=signals_for(WeightedSide.BUY, WeightedStrategyFamily.BREAKOUT), snapshot=breakout_snapshot(), effective_settings=settings)
        mean = evaluate_entry_policy(decision=decision_for(WeightedSide.BUY, WeightedStrategyFamily.MEAN_REVERSION), signals=signals_for(WeightedSide.BUY, WeightedStrategyFamily.MEAN_REVERSION), snapshot=mean_reversion_snapshot(), effective_settings=settings)
        reversal = evaluate_entry_policy(decision=decision_for(WeightedSide.BUY, WeightedStrategyFamily.REVERSAL), signals=signals_for(WeightedSide.BUY, WeightedStrategyFamily.REVERSAL), snapshot=reversal_snapshot(), effective_settings=settings)

        self.assertEqual(trend.entry_type, WeightedEntryType.STOP_LIMIT.value)
        self.assertEqual(breakout.entry_type, WeightedEntryType.STOP_LIMIT.value)
        self.assertEqual(mean.entry_type, WeightedEntryType.LIMIT.value)
        self.assertEqual(reversal.entry_type, WeightedEntryType.CONFIRMATION_LIMIT.value)
        for result in (trend, breakout, mean, reversal):
            self.assertTrue(result.accepted)
            self.assertTrue(result.reason_codes)
            self.assertIsNotNone(result.trigger_price)
            self.assertIsNotNone(result.limit_price)
            self.assertGreater(result.entry_timeout_seconds, 0)
            self.assertGreater(result.entry_buffer, 0)
            self.assertTrue(result.cancellation_condition)

    def test_vwap_can_reject_but_never_change_side(self) -> None:
        result = evaluate_entry_policy(
            decision=decision_for(WeightedSide.BUY, WeightedStrategyFamily.TREND),
            signals=signals_for(WeightedSide.BUY, WeightedStrategyFamily.TREND),
            snapshot=trend_snapshot(price_below_vwap=True),
            effective_settings=effective_settings(),
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.side, WeightedSide.BUY.value)
        self.assertEqual(result.entry_type, WeightedEntryType.REJECTED.value)
        self.assertIn("weighted_voting.entry.trend.vwap_reject", result.reason_codes)

    def test_overextended_breakout_entries_are_rejected(self) -> None:
        result = evaluate_entry_policy(
            decision=decision_for(WeightedSide.BUY, WeightedStrategyFamily.BREAKOUT),
            signals=signals_for(WeightedSide.BUY, WeightedStrategyFamily.BREAKOUT),
            snapshot=breakout_snapshot(overextended=True),
            effective_settings=effective_settings(),
        )

        self.assertFalse(result.accepted)
        self.assertIn("weighted_voting.entry.breakout.overextended", result.reason_codes)

    def test_mean_reversion_rejects_chasing_after_reversion_is_mostly_complete(self) -> None:
        result = evaluate_entry_policy(
            decision=decision_for(WeightedSide.BUY, WeightedStrategyFamily.MEAN_REVERSION),
            signals=signals_for(WeightedSide.BUY, WeightedStrategyFamily.MEAN_REVERSION),
            snapshot=mean_reversion_snapshot(reversion_complete=True),
            effective_settings=effective_settings(),
        )

        self.assertFalse(result.accepted)
        self.assertIn("weighted_voting.entry.mean_reversion.chasing_complete", result.reason_codes)

    def test_entry_calculations_ignore_future_candles(self) -> None:
        base = breakout_snapshot()
        future = WeightedCandle(timestamp=TS + timedelta(minutes=5), open=120.0, high=125.0, low=119.0, close=124.0, volume=500000)
        with_future = base.model_copy(update={"one_minute_candles": base.one_minute_candles + (future,)})

        left = evaluate_entry_policy(decision=decision_for(WeightedSide.BUY, WeightedStrategyFamily.BREAKOUT), signals=signals_for(WeightedSide.BUY, WeightedStrategyFamily.BREAKOUT), snapshot=base, effective_settings=effective_settings())
        right = evaluate_entry_policy(decision=decision_for(WeightedSide.BUY, WeightedStrategyFamily.BREAKOUT), signals=signals_for(WeightedSide.BUY, WeightedStrategyFamily.BREAKOUT), snapshot=with_future, effective_settings=effective_settings())

        self.assertEqual(left.trigger_price, right.trigger_price)
        self.assertEqual(left.limit_price, right.limit_price)
        self.assertEqual(left.reason_codes, right.reason_codes)


def effective_settings():
    return resolve_effective_settings(
        default_settings=default_weighted_settings(timestamp=TS),
        dynamic_envelope=default_dynamic_envelope(timestamp=TS),
        hard_limits=default_hard_limits(timestamp=TS),
        timestamp=TS,
    )


def decision_for(side: WeightedSide, family: WeightedStrategyFamily):
    return aggregate_weighted_signals(list(signals_for(side, family)), decision_timestamp=TS)


def signals_for(side: WeightedSide, family: WeightedStrategyFamily) -> tuple[WeightedVotingSignal, ...]:
    p_buy, p_sell, p_hold = (0.82, 0.08, 0.10) if side == WeightedSide.BUY else (0.08, 0.82, 0.10)
    return tuple(
        WeightedVotingSignal(
            strategy_id=f"T{index}",
            strategy_name=f"{family.value} synthetic {index}",
            strategy_version="weighted_strategy_test_v1",
            family=family,
            signal=side,
            p_buy=p_buy,
            p_sell=p_sell,
            p_hold=p_hold,
            directional_confidence=0.8,
            signal_strength=0.8,
            expected_raw_movement=0.002,
            expected_return=0.002,
            expected_return_after_costs=0.0015,
            strength=0.8,
            final_weight=0.125,
            eligible=True,
            data_ready=True,
            data_quality_status=WeightedDataQualityStatus.FULL,
            data_timestamp=TS,
            explanation="Synthetic entry signal.",
        )
        for index in range(8)
    )


def trend_snapshot(*, price_below_vwap: bool = False) -> WeightedMarketSnapshot:
    candles = []
    start = TS - timedelta(minutes=30)
    price = 100.0
    for index in range(31):
        close = price + 0.10
        if price_below_vwap and index == 30:
            close = 99.0
        candles.append(candle(start + timedelta(minutes=index), price, max(price, close) + 0.05, min(price, close) - 0.05, close, 100000))
        price = close
    quote = candles[-1].close
    return snapshot(tuple(candles), quote - 0.01, quote + 0.01)


def breakout_snapshot(*, overextended: bool = False) -> WeightedMarketSnapshot:
    candles = []
    start = TS - timedelta(minutes=30)
    for index in range(30):
        candles.append(candle(start + timedelta(minutes=index), 100.0, 100.20, 99.80, 100.0, 80000))
    close = 100.35 if not overextended else 103.0
    candles.append(candle(TS, 100.20, close + 0.05, 100.10, close, 160000))
    return snapshot(tuple(candles), close - 0.01, close + 0.01)


def mean_reversion_snapshot(*, reversion_complete: bool = False) -> WeightedMarketSnapshot:
    candles = []
    start = TS - timedelta(minutes=30)
    for index in range(30):
        candles.append(candle(start + timedelta(minutes=index), 100.0, 100.10, 99.90, 100.0, 90000))
    close = 98.8 if not reversion_complete else 99.98
    candles.append(candle(TS, close - 0.20, close + 0.05, close - 0.30, close, 110000))
    return snapshot(tuple(candles), close - 0.01, close + 0.01)


def reversal_snapshot() -> WeightedMarketSnapshot:
    candles = []
    start = TS - timedelta(minutes=5)
    candles.append(candle(start, 100.0, 100.2, 99.8, 100.0, 80000))
    candles.append(candle(TS - timedelta(minutes=1), 100.0, 100.1, 99.5, 99.8, 100000))
    candles.append(candle(TS, 99.7, 99.95, 99.2, 99.75, 120000))
    return snapshot(tuple(candles), 99.74, 99.76)


def snapshot(candles: tuple[WeightedCandle, ...], bid: float, ask: float) -> WeightedMarketSnapshot:
    return WeightedMarketSnapshot(symbol="SPY", data_timestamp=TS, one_minute_candles=candles, bid=bid, ask=ask, explanation="Synthetic entry snapshot.")


def candle(timestamp: datetime, open_price: float, high: float, low: float, close: float, volume: float) -> WeightedCandle:
    return WeightedCandle(timestamp=timestamp, open=open_price, high=high, low=low, close=close, volume=volume)


if __name__ == "__main__":
    unittest.main()
