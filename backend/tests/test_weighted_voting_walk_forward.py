from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from backend.app.algorithms.weighted_voting.backtest.walk_forward import (
    MODE_PERFORMANCE_DYNAMIC,
    MODE_PERFORMANCE_STATIC,
    MODE_STATIC_EQUAL,
    WALK_FORWARD_MODES,
    WeightedWalkForwardConfig,
    run_chronological_walk_forward,
)
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingCandle, WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedDataQualityStatus, WeightedSide, WeightedStrategyFamily, WeightedVotingSignal
from backend.app.algorithms.weighted_voting.strategies.common import eastern_datetime


SESSION_OPEN = datetime(2026, 7, 13, 13, 30, tzinfo=timezone.utc)
CREATED_AT = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
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


class WeightedVotingWalkForwardTest(unittest.TestCase):
    def test_walk_forward_stores_boundaries_and_reports_modes_separately(self) -> None:
        with patch("backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals", side_effect=synthetic_signals):
            result = run_chronological_walk_forward(candles=make_sessions(5), config=small_config("wf-boundaries"), created_at=CREATED_AT)

        self.assertEqual(result.run_id, "wf-boundaries")
        self.assertEqual(set(result.mode_results), set(WALK_FORWARD_MODES))
        self.assertEqual(set(result.mode_summaries), set(WALK_FORWARD_MODES))
        self.assertEqual(len(result.fold_boundaries), 1)
        boundary = result.fold_boundaries[0]
        self.assertLess(boundary.warmup_end, boundary.calibration_start)
        self.assertLess(boundary.calibration_end, boundary.validation_start)
        self.assertLess(boundary.validation_end, boundary.test_start)
        self.assertEqual(boundary.manifest_hash, result.manifest.manifest_hash)

        for mode in WALK_FORWARD_MODES:
            self.assertEqual(len(result.mode_results[mode]), 1)
            mode_result = result.mode_results[mode][0]
            self.assertEqual(mode_result.mode, mode)
            self.assertEqual(mode_result.weight_data_end, boundary.calibration_end)
            self.assertLess(mode_result.weight_data_end, boundary.test_start)
            self.assertEqual(mode_result.settings_available_before, boundary.test_start)
            self.assertIn(mode, mode_result.test_result.run.run_id)

    def test_no_test_data_enters_calibration_window(self) -> None:
        with patch("backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals", side_effect=synthetic_signals):
            result = run_chronological_walk_forward(candles=make_sessions(5), config=small_config("wf-no-leakage"), created_at=CREATED_AT)

        boundary = result.fold_boundaries[0]
        for mode_result in result.mode_results[MODE_PERFORMANCE_STATIC] + result.mode_results[MODE_PERFORMANCE_DYNAMIC]:
            test_start = eastern_datetime(mode_result.test_result.manifest.start_timestamp).date()
            self.assertEqual(test_start, boundary.test_start)
            self.assertLess(mode_result.weight_data_end, test_start)
            self.assertGreaterEqual(mode_result.calibration_outcome_count, 0)
            self.assertIn("weighted_voting.walk_forward.weights_use_data_through_d_minus_1", mode_result.reason_codes)

    def test_reproducible_from_run_id_and_manifest(self) -> None:
        with patch("backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals", side_effect=synthetic_signals):
            first = run_chronological_walk_forward(candles=make_sessions(5), config=small_config("wf-repro"), created_at=CREATED_AT)
        with patch("backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals", side_effect=synthetic_signals):
            second = run_chronological_walk_forward(candles=make_sessions(5), config=small_config("wf-repro"), created_at=CREATED_AT)

        self.assertEqual(first.manifest.manifest_hash, second.manifest.manifest_hash)
        self.assertEqual(first.reproducibility_key, second.reproducibility_key)
        self.assertEqual(first.fold_boundaries, second.fold_boundaries)
        self.assertEqual(first.mode_summaries, second.mode_summaries)

    def test_dynamic_promotion_is_guarded_by_out_of_sample_metrics(self) -> None:
        with patch("backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals", side_effect=synthetic_signals):
            result = run_chronological_walk_forward(candles=make_sessions(5), config=small_config("wf-promotion"), created_at=CREATED_AT)

        dynamic = result.mode_summaries[MODE_PERFORMANCE_DYNAMIC]
        static = result.mode_summaries[MODE_PERFORMANCE_STATIC]
        equal = result.mode_summaries[MODE_STATIC_EQUAL]
        if result.dynamic_promoted:
            self.assertGreater(dynamic.sharpe, max(static.sharpe, equal.sharpe))
            self.assertGreaterEqual(dynamic.sortino, static.sortino)
            self.assertLessEqual(dynamic.maximum_drawdown, max(static.maximum_drawdown, 1.0) * 1.10)
            self.assertLessEqual(dynamic.regime_dependence_score, max(static.regime_dependence_score, 1.0) * 1.25)
        else:
            self.assertIn("weighted_voting.walk_forward.dynamic_promotion_guarded", result.reason_codes)


def small_config(run_id: str) -> WeightedWalkForwardConfig:
    return WeightedWalkForwardConfig(
        run_id=run_id,
        symbol="SPY",
        indicator_warmup_sessions=1,
        weight_calibration_sessions=2,
        validation_sessions=1,
        unseen_test_sessions=1,
        step_forward_sessions=1,
    )


def synthetic_signals(snapshot: WeightedVotingMarketSnapshot, _config=None) -> tuple[WeightedVotingSignal, ...]:
    side = WeightedSide.BUY
    signals = []
    for strategy_id in STRATEGY_IDS:
        confidence = 0.86 if FAMILY_BY_STRATEGY[strategy_id] == WeightedStrategyFamily.TREND else 0.72
        signals.append(
            WeightedVotingSignal(
                strategy_id=strategy_id,
                strategy_name=f"{strategy_id} synthetic walk-forward signal",
                strategy_version="weighted_strategy_walk_forward_test_v1",
                family=FAMILY_BY_STRATEGY[strategy_id],
                signal=side,
                p_buy=confidence,
                p_sell=0.05,
                p_hold=round(1.0 - confidence - 0.05, 6),
                directional_confidence=confidence,
                signal_strength=confidence,
                expected_raw_movement=0.02,
                expected_return=0.02,
                expected_return_after_costs=0.018,
                strength=confidence,
                final_weight=0.125,
                eligible=True,
                data_ready=True,
                required_data_freshness_seconds=300,
                actual_data_freshness_seconds=0,
                data_quality_status=WeightedDataQualityStatus.FULL,
                invalidation_level=snapshot.one_minute_candles[-1].low - 0.20,
                data_timestamp=snapshot.data_timestamp,
                reason_codes=("weighted_voting.walk_forward.synthetic_signal",),
                explanation="Synthetic full-quality signal used to exercise chronological walk-forward behavior.",
            )
        )
    return tuple(signals)


def make_sessions(session_count: int) -> tuple[WeightedVotingCandle, ...]:
    candles = []
    for session_index in range(session_count):
        start = SESSION_OPEN + timedelta(days=session_index)
        for minute_index in range(390):
            base = 100.0 + session_index * 0.8 + minute_index * 0.03
            volume = 200_000 if minute_index != 5 else 5_000
            candles.append(
                WeightedVotingCandle(
                    timestamp=start + timedelta(minutes=minute_index),
                    open=base,
                    high=base + 0.45,
                    low=base - 0.18,
                    close=base + 0.08,
                    volume=volume,
                )
            )
    return tuple(candles)


if __name__ == "__main__":
    unittest.main()
