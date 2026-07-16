from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.wca.contracts import WcaStrategyPerformanceRecord
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY
from backend.app.algorithms.wca.weights import WcaWeightEngineConfig, baseline_weight_snapshot, performance_weight_snapshot


UTC = timezone.utc


class WcaStep6PerformanceWeightsTest(unittest.TestCase):
    def test_replaying_same_cutoff_generates_same_weights(self) -> None:
        cutoff = datetime(2026, 1, 30, 21, 0, tzinfo=UTC)
        records = sample_records(cutoff)
        config = WcaWeightEngineConfig(family_cap=0.34)

        first = performance_weight_snapshot(records=records, cutoff=cutoff, config=config)
        second = performance_weight_snapshot(records=records, cutoff=cutoff, config=config)

        self.assertEqual(first.deterministic_json(), second.deterministic_json())
        self.assertAlmostEqual(sum(first.weights.values()), 1.0, places=8)
        self.assertTrue(first.details)
        self.assertTrue(all(detail.metrics_cutoff_timestamp == cutoff for detail in first.details))

    def test_future_trades_cannot_affect_earlier_weights(self) -> None:
        cutoff = datetime(2026, 1, 30, 21, 0, tzinfo=UTC)
        records = sample_records(cutoff)
        future = tuple(
            performance_record("C1", "trend", 5.0, cutoff + timedelta(days=1, minutes=index))
            for index in range(20)
        )

        baseline = performance_weight_snapshot(records=records, cutoff=cutoff)
        with_future = performance_weight_snapshot(records=(*records, *future), cutoff=cutoff)

        self.assertEqual(with_future.deterministic_json(), baseline.deterministic_json())

    def test_family_caps_are_enforced_and_auditable(self) -> None:
        cutoff = datetime(2026, 1, 30, 21, 0, tzinfo=UTC)
        records = sample_records(cutoff, trend_edge=2.0)
        config = WcaWeightEngineConfig(family_cap=0.30, strategy_cap=0.16, strategy_floor=0.015)

        snapshot = performance_weight_snapshot(records=records, cutoff=cutoff, config=config)

        family_by_strategy = {definition.strategy_id: definition.family for definition in WCA_STRATEGY_REGISTRY}
        family_totals: dict[str, float] = {}
        for strategy_id, weight in snapshot.weights.items():
            family = family_by_strategy[strategy_id]
            family_totals[family] = family_totals.get(family, 0) + weight
            self.assertLessEqual(weight, config.strategy_cap + 1e-8)
            self.assertGreaterEqual(weight, config.strategy_floor - 1e-8)
        self.assertTrue(all(total <= config.family_cap + 1e-8 for total in family_totals.values()))
        self.assertTrue(all(detail.weight_version == config.weight_version for detail in snapshot.details))

    def test_small_losing_sample_does_not_zero_strategy(self) -> None:
        cutoff = datetime(2026, 1, 30, 21, 0, tzinfo=UTC)
        records = (performance_record("C1", "trend", -1.0, cutoff - timedelta(days=1)),)
        config = WcaWeightEngineConfig(strategy_floor=0.02, minimum_trade_count_full_weight=40)

        snapshot = performance_weight_snapshot(records=records, cutoff=cutoff, config=config)

        self.assertGreaterEqual(snapshot.weights["C1"], config.strategy_floor)
        detail = next(row for row in snapshot.details if row.strategy_id == "C1")
        self.assertIn("wca.weights.shrunk_to_baseline", detail.reason_codes)

    def test_static_baseline_weights_remain_available(self) -> None:
        cutoff = datetime(2026, 1, 30, 21, 0, tzinfo=UTC)

        snapshot = baseline_weight_snapshot(cutoff=cutoff)

        expected = {definition.strategy_id: definition.base_weight for definition in WCA_STRATEGY_REGISTRY}
        self.assertEqual(snapshot.weights, expected)
        self.assertEqual(snapshot.metrics_cutoff_timestamp, cutoff)
        self.assertIn("wca.weights.static_baseline", snapshot.reason_codes)


def sample_records(cutoff: datetime, *, trend_edge: float = 1.0) -> tuple[WcaStrategyPerformanceRecord, ...]:
    family_by_strategy = {definition.strategy_id: definition.family for definition in WCA_STRATEGY_REGISTRY}
    records: list[WcaStrategyPerformanceRecord] = []
    for definition in WCA_STRATEGY_REGISTRY:
        for index in range(45):
            if definition.family == "trend":
                value = trend_edge if index % 3 != 0 else -0.35
            elif definition.family == "mean_reversion":
                value = 0.45 if index % 2 == 0 else -0.30
            elif definition.family == "breakout":
                value = 0.55 if index % 2 == 0 else -0.45
            else:
                value = 0.25 if index % 2 == 0 else -0.20
            records.append(performance_record(definition.strategy_id, family_by_strategy[definition.strategy_id], value, cutoff - timedelta(days=45 - index)))
    return tuple(records)


def performance_record(strategy_id: str, family: str, r_multiple: float, available_at: datetime) -> WcaStrategyPerformanceRecord:
    return WcaStrategyPerformanceRecord(
        strategy_id=strategy_id,
        strategy_version=f"wca_{strategy_id.lower()}_test_v1",
        family=family,
        decision_timestamp=available_at - timedelta(hours=1),
        outcome_available_at=available_at,
        r_multiple=r_multiple,
        pnl=r_multiple * 100,
        success=r_multiple > 0,
        regime="default",
    )


if __name__ == "__main__":
    unittest.main()
