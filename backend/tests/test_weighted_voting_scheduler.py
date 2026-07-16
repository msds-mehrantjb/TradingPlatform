from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingCandle, WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import WeightedDataQualityStatus, WeightedSide, WeightedStrategyFamily, WeightedVotingSignal
from backend.app.algorithms.weighted_voting.scheduler import (
    ACTIVE_WEIGHT_STATE_KEY,
    PUBLISHED_WEIGHT_PREFIX,
    UPDATE_RECORD_PREFIX,
    WeightedVotingDailySchedulerConfig,
    run_after_market_daily_weight_update,
)


SESSION_DATE = date(2026, 7, 14)
SESSION_OPEN = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)
AFTER_MARKET = datetime(2026, 7, 14, 21, 10, tzinfo=timezone.utc)
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


class WeightedVotingSchedulerTest(unittest.TestCase):
    def test_running_twice_for_one_date_creates_no_duplicate_update(self) -> None:
        store = MemoryStore()
        provider = StaticDatasetProvider(make_session())
        config = WeightedVotingDailySchedulerConfig(symbol="SPY")

        with patch("backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals", side_effect=synthetic_signals):
            first = run_after_market_daily_weight_update(session_date=SESSION_DATE, store=store, dataset_provider=provider, completed_at=AFTER_MARKET, config=config)
        with patch("backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals", side_effect=synthetic_signals):
            second = run_after_market_daily_weight_update(session_date=SESSION_DATE, store=store, dataset_provider=provider, completed_at=AFTER_MARKET, config=config)

        self.assertEqual(first.status, "published")
        self.assertEqual(second.status, "idempotent_noop")
        self.assertIsNone(second.replay_result)
        self.assertEqual(store.write_counts[f"{UPDATE_RECORD_PREFIX}{SESSION_DATE.isoformat()}"], 1)
        self.assertEqual(first.active_weight_version, second.active_weight_version)

    def test_other_algorithm_scheduler_failure_has_no_effect(self) -> None:
        store = MemoryStore()
        provider = StaticDatasetProvider(make_session())
        provider.other_algorithm_scheduler_failed = True

        with patch("backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals", side_effect=synthetic_signals):
            result = run_after_market_daily_weight_update(
                session_date=SESSION_DATE,
                store=store,
                dataset_provider=provider,
                completed_at=AFTER_MARKET,
                config=WeightedVotingDailySchedulerConfig(symbol="SPY"),
            )

        self.assertEqual(result.status, "published")
        self.assertIn("weighted_voting.scheduler.weights_published_for_next_session", result.reason_codes)
        self.assertTrue(provider.candles_requested)

    def test_failed_candidate_cannot_replace_active_weights(self) -> None:
        store = MemoryStore()
        provider = StaticDatasetProvider(make_session())
        bad_config = WeightedVotingConfig(weight_smoothing_previous=0.80, weight_smoothing_candidate=0.30)

        with patch("backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals", side_effect=synthetic_signals):
            result = run_after_market_daily_weight_update(
                session_date=SESSION_DATE,
                store=store,
                dataset_provider=provider,
                completed_at=AFTER_MARKET,
                config=WeightedVotingDailySchedulerConfig(symbol="SPY", weighted_config=bad_config),
            )

        active = store.read_snapshot(ACTIVE_WEIGHT_STATE_KEY)
        self.assertEqual(result.status, "failed_candidate_validation")
        self.assertEqual(active["weight_version"], result.previous_weight_version)
        self.assertEqual(result.active_weight_version, result.previous_weight_version)
        self.assertNotIn(f"{PUBLISHED_WEIGHT_PREFIX}2026-07-15", store.snapshots)

    def test_tomorrows_weights_are_published_before_next_session_open(self) -> None:
        store = MemoryStore()
        provider = StaticDatasetProvider(make_session())

        with patch("backend.app.algorithms.weighted_voting.backtest.engine.evaluate_signals", side_effect=synthetic_signals):
            result = run_after_market_daily_weight_update(
                session_date=SESSION_DATE,
                store=store,
                dataset_provider=provider,
                completed_at=AFTER_MARKET,
                config=WeightedVotingDailySchedulerConfig(symbol="SPY"),
            )

        self.assertEqual(result.status, "published")
        self.assertEqual(result.published_for_session_date, date(2026, 7, 15))
        self.assertIn(f"{PUBLISHED_WEIGHT_PREFIX}2026-07-15", store.snapshots)
        self.assertLess(AFTER_MARKET, datetime(2026, 7, 15, 13, 30, tzinfo=timezone.utc))


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}
        self.write_counts: dict[str, int] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot
        self.write_counts[key] = self.write_counts.get(key, 0) + 1


class StaticDatasetProvider:
    def __init__(self, candles: tuple[WeightedVotingCandle, ...]) -> None:
        self.candles = candles
        self.candles_requested = False
        self.other_algorithm_scheduler_failed = False

    def candles_for_session(self, session_date: date) -> tuple[WeightedVotingCandle, ...]:
        self.candles_requested = True
        return self.candles


def synthetic_signals(snapshot: WeightedVotingMarketSnapshot, _config=None) -> tuple[WeightedVotingSignal, ...]:
    side = WeightedSide.BUY
    signals = []
    for strategy_id in STRATEGY_IDS:
        confidence = 0.86 if FAMILY_BY_STRATEGY[strategy_id] == WeightedStrategyFamily.TREND else 0.72
        signals.append(
            WeightedVotingSignal(
                strategy_id=strategy_id,
                strategy_name=f"{strategy_id} synthetic scheduler signal",
                strategy_version="weighted_strategy_scheduler_test_v1",
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
                reason_codes=("weighted_voting.scheduler.synthetic_signal",),
                explanation="Synthetic full-quality signal used to exercise the after-market scheduler deterministically.",
            )
        )
    return tuple(signals)


def make_session() -> tuple[WeightedVotingCandle, ...]:
    candles = []
    for index in range(390):
        base = 100.0 + index * 0.03
        volume = 200_000 if index != 5 else 5_000
        candles.append(
            WeightedVotingCandle(
                timestamp=SESSION_OPEN + timedelta(minutes=index),
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
