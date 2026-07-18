from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.weighted_voting.performance_tracker import (
    WEIGHTED_VOTING_PERFORMANCE_TRACKER_NAMESPACE,
    WeightedVotingSignalObservation,
    WeightedVotingTrackedTrade,
    WeightedVotingWeightVersionSnapshot,
    build_weighted_voting_performance_report,
    performance_tracker_status,
    persist_weighted_voting_performance_report,
)


NOW = datetime(2026, 7, 14, 15, 30, tzinfo=UTC)


class WeightedVotingPerformanceTrackerTest(unittest.TestCase):
    def test_algorithm_strategy_market_and_weight_version_levels_are_tracked(self) -> None:
        trades = (
            trade("t1", "d1", 120.0, 100.0, 20.0, ("wv_orb", "wv_vwap_trend"), "weights-v1", trend="trending_up", volatility="high_volatility", session="open", side="BUY"),
            trade("t2", "d2", -60.0, -70.0, 10.0, ("wv_failed_breakout",), "weights-v1", trend="range_bound", volatility="low_volatility", session="midday", side="SELL"),
            trade("t3", "d3", 80.0, 70.0, 10.0, ("wv_orb",), "weights-v2", trend="trending_up", volatility="high_volatility", session="open", side="BUY"),
        )
        observations = (
            observation("wv_orb", "d1", True, True, 0.72, 0.22, "weights-v1"),
            observation("wv_orb", "d3", True, True, 0.68, 0.25, "weights-v2"),
            observation("wv_vwap_trend", "d1", True, True, 0.64, 0.18, "weights-v1"),
            observation("wv_failed_breakout", "d2", True, True, 0.58, 0.16, "weights-v1"),
            observation("wv_vwap_mean_reversion", "d4", True, False, 0.31, 0.12, "weights-v2"),
        )
        snapshots = (
            WeightedVotingWeightVersionSnapshot(
                algorithm_id="weighted_voting",
                weight_version="weights-v2",
                previous_weight_version="weights-v1",
                effective_at=NOW,
                strategy_weights={"wv_orb": 0.25, "wv_failed_breakout": 0.12},
                previous_strategy_weights={"wv_orb": 0.20, "wv_failed_breakout": 0.17},
            ),
        )

        report = build_weighted_voting_performance_report(
            trades=trades,
            signal_observations=observations,
            weight_snapshots=snapshots,
            evaluated_at=NOW,
            starting_equity=10_000.0,
        )

        self.assertEqual(report.algorithm_id, "weighted_voting")
        self.assertEqual(report.algorithm_level.trade_count, 3)
        self.assertEqual(report.algorithm_level.net_return, 0.01)
        self.assertEqual(report.algorithm_level.gross_return, 0.014)
        self.assertEqual(report.algorithm_level.total_costs, 40.0)
        self.assertAlmostEqual(report.algorithm_level.win_rate, 2 / 3)
        self.assertAlmostEqual(report.algorithm_level.expectancy, 100 / 3)
        self.assertAlmostEqual(report.algorithm_level.profit_factor, 170 / 70)
        self.assertEqual(report.algorithm_level.daily_loss, 0.0)
        self.assertEqual(report.strategy_level["wv_orb"].eligible_signal_count, 2)
        self.assertEqual(report.strategy_level["wv_orb"].directional_signal_count, 2)
        self.assertEqual(report.strategy_level["wv_orb"].contributing_trade_count, 2)
        self.assertEqual(report.strategy_level["wv_orb"].weight_contribution, 0.47)
        self.assertEqual(report.strategy_level["wv_vwap_mean_reversion"].directional_signal_count, 0)
        self.assertIn("trending_up", report.market_condition_level.by_trend_or_range_condition)
        self.assertIn("high_volatility", report.market_condition_level.by_volatility_condition)
        self.assertIn("open", report.market_condition_level.by_session_period)
        self.assertIn("short", report.market_condition_level.by_long_short_direction)
        self.assertEqual(report.market_condition_level.by_trend_or_range_condition["trending_up"].performance_after_costs, 170.0)
        self.assertIsNone(report.weight_version_level["weights-v1"].performance_before_update)
        self.assertEqual(report.weight_version_level["weights-v2"].previous_weight_version, "weights-v1")
        self.assertGreater(report.weight_version_level["weights-v2"].attribution_of_improvement_or_degradation, 0)
        self.assertEqual(report.weight_version_level["weights-v2"].stability_of_weight_changes, 0.95)

    def test_performance_tracker_rejects_foreign_algorithm_inputs(self) -> None:
        with self.assertRaises(ValueError):
            replace(trade("t1", "d1", 10.0, 9.0, 1.0, ("wv_orb",), "weights-v1"), algorithm_id="wca")
        with self.assertRaises(ValueError):
            replace(observation("wv_orb", "d1", True, True, 0.6, 0.2, "weights-v1"), algorithm_id="regime")
        with self.assertRaises(ValueError):
            WeightedVotingWeightVersionSnapshot(
                algorithm_id="meta_model",
                weight_version="foreign",
                effective_at=NOW,
                strategy_weights={"foreign": 1.0},
            )

    def test_performance_report_persists_under_weighted_voting_namespace(self) -> None:
        store = MemoryStore()
        report = build_weighted_voting_performance_report(
            trades=(trade("t1", "d1", 30.0, 25.0, 5.0, ("wv_orb",), "weights-v1"),),
            evaluated_at=NOW,
            starting_equity=10_000.0,
        )

        persist_weighted_voting_performance_report(store, report)

        key = f"{WEIGHTED_VOTING_PERFORMANCE_TRACKER_NAMESPACE}.latest"
        self.assertIn(key, store.snapshots)
        self.assertEqual(store.snapshots[key]["algorithmId"], "weighted_voting")
        self.assertIn("algorithmLevel", store.snapshots[key])
        self.assertIn("strategyLevel", store.snapshots[key])
        self.assertIn("marketConditionLevel", store.snapshots[key])
        self.assertIn("weightVersionLevel", store.snapshots[key])

    def test_status_inventory_declares_four_tracking_levels_and_metrics(self) -> None:
        status = performance_tracker_status()

        self.assertEqual(status["algorithmId"], "weighted_voting")
        self.assertEqual(status["trackingLevels"], ["algorithm", "strategy", "market_condition", "weight_version"])
        self.assertIn("net_return", status["algorithmMetrics"])
        self.assertIn("confidence_calibration", status["strategyMetrics"])
        self.assertIn("performance_after_costs", status["marketConditionMetrics"])
        self.assertIn("stability_of_weight_changes", status["weightVersionMetrics"])
        self.assertEqual(status["ownershipRule"], "only_weighted_voting_attributed_trades_signals_and_weights")


def trade(
    trade_id: str,
    decision_id: str,
    gross_pnl: float,
    net_pnl: float,
    total_costs: float,
    strategies: tuple[str, ...],
    weight_version: str,
    *,
    trend: str = "trending_up",
    volatility: str = "high_volatility",
    session: str = "open",
    side: str = "BUY",
) -> WeightedVotingTrackedTrade:
    return WeightedVotingTrackedTrade(
        algorithm_id="weighted_voting",
        trade_id=trade_id,
        decision_id=decision_id,
        symbol="SPY",
        side=side,
        quantity=10,
        entry_time=NOW,
        exit_time=NOW + timedelta(minutes=15),
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        total_costs=total_costs,
        stop=99.0,
        target=102.0,
        maximum_favorable_excursion=max(net_pnl, 0.0),
        maximum_adverse_excursion=min(net_pnl, 0.0),
        exit_reason="target_hit" if net_pnl > 0 else "stop_hit",
        owning_strategy_ids=strategies,
        weight_version=weight_version,
        settings_version="settings-v1",
        trend_condition=trend,
        volatility_condition=volatility,
        session_period=session,
        confidence_by_strategy={strategy_id: 0.6 for strategy_id in strategies},
        weight_by_strategy={strategy_id: 0.2 for strategy_id in strategies},
    )


def observation(
    strategy_id: str,
    decision_id: str,
    eligible: bool,
    directional: bool,
    confidence: float,
    active_weight: float,
    weight_version: str,
) -> WeightedVotingSignalObservation:
    return WeightedVotingSignalObservation(
        algorithm_id="weighted_voting",
        strategy_id=strategy_id,
        decision_id=decision_id,
        eligible=eligible,
        directional=directional,
        confidence=confidence,
        active_weight=active_weight,
        weight_version=weight_version,
        observed_at=NOW,
    )


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


if __name__ == "__main__":
    unittest.main()
