from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from backend.app.algorithms.weighted_voting.backtest.engine import WeightedBacktestEngineConfig, run_weighted_voting_backtest
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingCandle, WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedDataQualityStatus, WeightedSide, WeightedStrategyFamily, WeightedVotingSignal


SESSION_OPEN = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)
CREATED_AT = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
STRATEGY_IDS = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")
FAMILY_BY_STRATEGY = {
    "S1": WeightedStrategyFamily.BREAKOUT,
    "S8": WeightedStrategyFamily.BREAKOUT,
    "S2": WeightedStrategyFamily.TREND,
    "S3": WeightedStrategyFamily.TREND,
    "S4": WeightedStrategyFamily.MEAN_REVERSION,
    "S7": WeightedStrategyFamily.MEAN_REVERSION,
    "S5": WeightedStrategyFamily.REVERSAL,
    "S6": WeightedStrategyFamily.REVERSAL,
}


class WeightedVotingBacktestEngineTest(unittest.TestCase):
    def test_backtest_uses_production_path_and_simulates_complete_trades_with_costs(self) -> None:
        candles = trending_session()
        seen_snapshots: list[WeightedVotingMarketSnapshot] = []

        def evaluator(snapshot: WeightedVotingMarketSnapshot, _config=None):
            seen_snapshots.append(snapshot)
            return synthetic_signals(snapshot, WeightedSide.BUY)

        with patch("backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals", side_effect=evaluator):
            result = run_weighted_voting_backtest(candles=candles, config=WeightedBacktestEngineConfig(symbol="SPY"), created_at=CREATED_AT)

        self.assertEqual(result.run.data_manifest_hash, result.manifest.manifest_hash)
        self.assertGreater(len(result.trades), 0)
        self.assertTrue(any(trade.partial_fill for trade in result.trades))
        self.assertGreater(result.algorithm_results.turnover, 0.0)
        self.assertGreater(result.algorithm_results.cost_ratio, 0.0)
        self.assertNotEqual(result.trades[0].gross_pnl, result.trades[0].net_pnl)
        self.assertIn("evaluate_signals", result.production_function_calls)
        self.assertIn("classify_market_condition", result.production_function_calls)
        self.assertIn("aggregate_weighted_signals", result.production_function_calls)
        self.assertIn("evaluate_local_decision_gates", result.production_function_calls)
        self.assertIn("calculate_weighted_voting_position_size", result.production_function_calls)
        self.assertIn("evaluate_entry_policy", result.production_function_calls)
        self.assertIn("evaluate_exit_lifecycle", result.production_function_calls)
        self.assertTrue(all(max(candle.timestamp for candle in snapshot.one_minute_candles) <= snapshot.data_timestamp for snapshot in seen_snapshots))

    def test_no_lookahead_and_next_candle_entry_are_enforced(self) -> None:
        candles = trending_session()
        first_signal_timestamp: datetime | None = None

        def evaluator(snapshot: WeightedVotingMarketSnapshot, _config=None):
            nonlocal first_signal_timestamp
            if first_signal_timestamp is None:
                first_signal_timestamp = snapshot.data_timestamp
            return synthetic_signals(snapshot, WeightedSide.BUY)

        with patch("backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals", side_effect=evaluator):
            result = run_weighted_voting_backtest(candles=candles, config=WeightedBacktestEngineConfig(symbol="SPY"), created_at=CREATED_AT)

        self.assertTrue(result.decisions)
        self.assertTrue(all(trace.completed_candle_count == trace.candle_index + 1 for trace in result.decisions))
        self.assertIsNotNone(first_signal_timestamp)
        self.assertGreater(result.trades[0].entry_timestamp, first_signal_timestamp)
        self.assertIn("weighted_voting.backtest.no_lookahead_next_candle_entry", result.reason_codes)

    def test_stop_wins_when_stop_and_target_are_both_touched_in_one_candle(self) -> None:
        candles = ambiguous_stop_target_session()

        with patch(
            "backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals",
            side_effect=lambda snapshot, _config=None: synthetic_signals(snapshot, WeightedSide.BUY),
        ):
            result = run_weighted_voting_backtest(candles=candles, config=WeightedBacktestEngineConfig(symbol="SPY"), created_at=CREATED_AT)

        self.assertGreater(len(result.trades), 0)
        self.assertEqual(result.trades[0].exit_reason, "stop_hit")
        self.assertLess(result.trades[0].net_pnl, 0.0)

    def test_session_cutoff_forces_end_of_day_close(self) -> None:
        candles = quiet_late_entry_session()

        def evaluator(snapshot: WeightedVotingMarketSnapshot, _config=None):
            side = WeightedSide.BUY if len(snapshot.one_minute_candles) >= 387 else WeightedSide.HOLD
            return synthetic_signals(snapshot, side)

        config = WeightedBacktestEngineConfig(symbol="SPY", session_cutoff_eastern_minutes=958, force_close_eastern_minutes=959)
        with patch("backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals", side_effect=evaluator):
            result = run_weighted_voting_backtest(candles=candles, config=config, created_at=CREATED_AT)

        self.assertGreater(len(result.trades), 0)
        self.assertEqual(result.trades[-1].exit_reason, "end_of_day")


def synthetic_signals(snapshot: WeightedVotingMarketSnapshot, side: WeightedSide) -> tuple[WeightedVotingSignal, ...]:
    signals = []
    for strategy_id in STRATEGY_IDS:
        directional = side in (WeightedSide.BUY, WeightedSide.SELL)
        confidence = 0.86 if FAMILY_BY_STRATEGY[strategy_id] == WeightedStrategyFamily.TREND else 0.72
        signals.append(
            WeightedVotingSignal(
                strategy_id=strategy_id,
                strategy_name=f"{strategy_id} synthetic production-path signal",
                strategy_version="weighted_strategy_backtest_test_v1",
                family=FAMILY_BY_STRATEGY[strategy_id],
                signal=side,
                p_buy=confidence if side == WeightedSide.BUY else 0.05 if side == WeightedSide.SELL else 0.0,
                p_sell=confidence if side == WeightedSide.SELL else 0.05 if side == WeightedSide.BUY else 0.0,
                p_hold=round(1.0 - confidence - 0.05, 6) if directional else 1.0,
                directional_confidence=confidence if directional else 0.0,
                signal_strength=confidence if directional else 0.0,
                expected_raw_movement=0.02 if directional else 0.0,
                expected_return=0.02 if directional else 0.0,
                expected_return_after_costs=0.018 if directional else 0.0,
                strength=confidence if directional else 0.0,
                final_weight=0.125,
                eligible=True,
                data_ready=True,
                required_data_freshness_seconds=300,
                actual_data_freshness_seconds=0,
                data_quality_status=WeightedDataQualityStatus.FULL,
                invalidation_level=snapshot.one_minute_candles[-1].low - 0.20 if side == WeightedSide.BUY else snapshot.one_minute_candles[-1].high + 0.20 if side == WeightedSide.SELL else None,
                data_timestamp=snapshot.data_timestamp,
                reason_codes=("weighted_voting.backtest.synthetic_signal",),
                explanation="Synthetic full-quality signal used to exercise the production backtest path deterministically.",
            )
        )
    return tuple(signals)


def trending_session() -> tuple[WeightedVotingCandle, ...]:
    candles = []
    for index in range(390):
        base = 100.0 + index * 0.03
        volume = 200_000 if index != 5 else 5_000
        candles.append(make_candle(index, base, base + 0.45, base - 0.18, base + 0.08, volume))
    return tuple(candles)


def ambiguous_stop_target_session() -> tuple[WeightedVotingCandle, ...]:
    candles = list(trending_session())
    candles[5] = make_candle(5, 100.10, 103.50, 97.00, 100.20, 60_000)
    return tuple(candles)


def quiet_late_entry_session() -> tuple[WeightedVotingCandle, ...]:
    candles = []
    for index in range(390):
        base = 100.0 + min(index, 386) * 0.002
        if index >= 386:
            base = 100.8 + (index - 386) * 0.03
        candles.append(make_candle(index, base, base + 0.20, base - 0.15, base + 0.04, 200_000))
    return tuple(candles)


def make_candle(index: int, open_: float, high: float, low: float, close: float, volume: float) -> WeightedVotingCandle:
    return WeightedVotingCandle(
        timestamp=SESSION_OPEN + timedelta(minutes=index),
        open=open_,
        high=max(high, open_, close),
        low=min(low, open_, close),
        close=close,
        volume=volume,
    )


if __name__ == "__main__":
    unittest.main()
