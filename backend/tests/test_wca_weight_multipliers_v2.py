from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.algorithms.wca.contracts import WcaStrategyPerformanceRecord, WcaWeightMaturityStage, WcaWeightSnapshot
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY
from backend.app.algorithms.wca.weights import (
    WCA_WEIGHT_MULTIPLIER_VERSION,
    WcaMultiplierCandidate,
    WcaWeightEngineConfig,
    adapt_v1_weight_snapshot_to_multipliers,
    baseline_weight_snapshot,
    bounded_mean_one_normalize,
    performance_weight_snapshot,
)


UTC = timezone.utc
CUTOFF = datetime(2026, 7, 30, 20, 0, tzinfo=UTC)


class WcaWeightMultipliersV2Test(unittest.TestCase):
    def test_neutral_initialization_uses_exact_mean_one_multipliers(self) -> None:
        snapshot = baseline_weight_snapshot(cutoff=CUTOFF)

        self.assertEqual(snapshot.weight_schema_version, "wca_weight_snapshot_v2")
        self.assertEqual(set(snapshot.weights), {definition.strategy_id for definition in WCA_STRATEGY_REGISTRY})
        self.assertTrue(all(value == 1.0 for value in snapshot.weights.values()))
        self.assertAlmostEqual(sum(snapshot.weights.values()) / len(snapshot.weights), 1.0, places=9)
        self.assertAlmostEqual(sum(detail.normalized_share for detail in snapshot.details), 1.0, places=9)

    def test_maturity_boundaries_are_deterministic(self) -> None:
        expected = {
            0: WcaWeightMaturityStage.UNTESTED,
            1: WcaWeightMaturityStage.LOW_SAMPLE,
            99: WcaWeightMaturityStage.LOW_SAMPLE,
            100: WcaWeightMaturityStage.LIMITED_ADJUSTMENT,
            299: WcaWeightMaturityStage.LIMITED_ADJUSTMENT,
            300: WcaWeightMaturityStage.FULL_ADJUSTMENT,
            499: WcaWeightMaturityStage.FULL_ADJUSTMENT,
            500: WcaWeightMaturityStage.FULL_ADJUSTMENT,
        }
        for count, stage in expected.items():
            with self.subTest(count=count):
                records = _records("C1", count, holdout=True, windows=4, months=3, regimes=2)
                snapshot = performance_weight_snapshot(records=records, cutoff=CUTOFF)
                detail = _detail(snapshot, "C1")
                self.assertEqual(detail.maturity_stage, stage)

    def test_lacking_multi_period_or_holdout_evidence_stays_limited(self) -> None:
        records = _records("C1", 500, holdout=False, windows=1, months=1, regimes=1, value=1.8)

        snapshot = performance_weight_snapshot(records=records, cutoff=CUTOFF)
        detail = _detail(snapshot, "C1")

        self.assertEqual(detail.maturity_stage, WcaWeightMaturityStage.LIMITED_ADJUSTMENT)
        self.assertGreaterEqual(detail.sample_adjusted_multiplier, 0.65)
        self.assertLessEqual(detail.sample_adjusted_multiplier, 1.35)
        self.assertLessEqual(detail.final_multiplier, 1.35)

    def test_low_sample_exceptional_performance_remains_near_neutral(self) -> None:
        records = _records("C1", 25, value=5.0)

        snapshot = performance_weight_snapshot(records=records, cutoff=CUTOFF)
        detail = _detail(snapshot, "C1")

        self.assertEqual(detail.maturity_stage, WcaWeightMaturityStage.LOW_SAMPLE)
        self.assertGreaterEqual(detail.sample_adjusted_multiplier, 0.90)
        self.assertLessEqual(detail.sample_adjusted_multiplier, 1.10)

    def test_transaction_costs_lower_quality_score(self) -> None:
        clean = performance_weight_snapshot(records=_records("C1", 150, value=0.8, total_cost=0.0), cutoff=CUTOFF)
        costly = performance_weight_snapshot(records=_records("C1", 150, value=0.8, total_cost=0.7), cutoff=CUTOFF)

        self.assertLess(_detail(costly, "C1").quality_score, _detail(clean, "C1").quality_score)
        self.assertLessEqual(_detail(costly, "C1").cost_adjusted_multiplier, _detail(clean, "C1").cost_adjusted_multiplier)

    def test_correlation_uses_aligned_observation_keys_only(self) -> None:
        config = WcaWeightEngineConfig(high_correlation_threshold=0.50, minimum_correlation_overlap=30)
        unaligned = tuple(
            [
                *_records("C1", 40, value=1.0, key_prefix="left"),
                *_records("C2", 40, value=1.0, key_prefix="right"),
            ]
        )
        aligned = []
        for index in range(40):
            value = 1.0 if index % 2 == 0 else -0.5
            aligned.append(_record("C1", index, value=value, evaluation_id=f"shared-{index}"))
            aligned.append(_record("C2", index, value=value, evaluation_id=f"shared-{index}"))

        unaligned_snapshot = performance_weight_snapshot(records=unaligned, cutoff=CUTOFF, config=config)
        aligned_snapshot = performance_weight_snapshot(records=tuple(aligned), cutoff=CUTOFF, config=config)

        self.assertEqual(_detail(unaligned_snapshot, "C1").correlation_factor, 1.0)
        self.assertLess(_detail(aligned_snapshot, "C1").correlation_factor, 1.0)
        self.assertGreaterEqual(_detail(aligned_snapshot, "C1").aligned_overlap_count, 30)

    def test_normalization_invariants_hold_and_shares_sum_to_one(self) -> None:
        candidates = tuple(
            WcaMultiplierCandidate(definition.strategy_id, definition.family, 2.0 if index % 2 == 0 else 0.25, 0.25, 2.0)
            for index, definition in enumerate(WCA_STRATEGY_REGISTRY)
        )
        multipliers = bounded_mean_one_normalize(candidates)

        self.assertTrue(all(0.25 <= value <= 2.0 for value in multipliers.values()))
        self.assertAlmostEqual(sum(multipliers.values()) / len(multipliers), 1.0, places=9)

    def test_future_or_in_sample_outcomes_do_not_affect_candidate(self) -> None:
        past = _records("C1", 20, value=0.2)
        future = (_record("C1", 999, value=9.0, available_at=CUTOFF),)
        in_sample = (_record("C1", 998, value=9.0, in_sample=True),)

        baseline = performance_weight_snapshot(records=past, cutoff=CUTOFF)
        with_forbidden = performance_weight_snapshot(records=(*past, *future, *in_sample), cutoff=CUTOFF)

        self.assertEqual(baseline.deterministic_json(), with_forbidden.deterministic_json())

    def test_v1_normalized_share_snapshot_is_explicitly_adapted(self) -> None:
        old_share = 1.0 / len(WCA_STRATEGY_REGISTRY)
        old = WcaWeightSnapshot(weight_version="legacy_v1", weights={definition.strategy_id: old_share for definition in WCA_STRATEGY_REGISTRY}, metrics_cutoff_timestamp=CUTOFF)

        adapted = adapt_v1_weight_snapshot_to_multipliers(old)

        self.assertNotEqual(adapted.weight_version, old.weight_version)
        self.assertIn("wca.weights.v1_share_snapshot_adapted", adapted.reason_codes)
        self.assertTrue(all(value == 1.0 for value in adapted.weights.values()))
        self.assertAlmostEqual(sum(detail.normalized_share for detail in adapted.details), 1.0, places=9)


def _detail(snapshot, strategy_id: str):
    return next(detail for detail in snapshot.details if detail.strategy_id == strategy_id)


def _records(
    strategy_id: str,
    count: int,
    *,
    value: float = 1.0,
    holdout: bool = True,
    windows: int = 4,
    months: int = 3,
    regimes: int = 2,
    total_cost: float = 0.0,
    key_prefix: str = "obs",
) -> tuple[WcaStrategyPerformanceRecord, ...]:
    return tuple(
        _record(
            strategy_id,
            index,
            value=value if index % 4 else -abs(value) * 0.25,
            holdout=holdout,
            window=f"wf-{index % max(1, windows)}",
            month_offset=index % max(1, months),
            regime=f"regime-{index % max(1, regimes)}",
            total_cost=total_cost,
            evaluation_id=f"{key_prefix}-{index}",
        )
        for index in range(count)
    )


def _record(
    strategy_id: str,
    index: int,
    *,
    value: float,
    holdout: bool = True,
    window: str = "wf-0",
    month_offset: int = 0,
    regime: str = "regime-0",
    total_cost: float = 0.0,
    evaluation_id: str | None = None,
    available_at: datetime | None = None,
    in_sample: bool = False,
) -> WcaStrategyPerformanceRecord:
    definition = next(item for item in WCA_STRATEGY_REGISTRY if item.strategy_id == strategy_id)
    decision_time = CUTOFF - timedelta(days=90 - month_offset * 31, minutes=index)
    return WcaStrategyPerformanceRecord(
        strategy_id=strategy_id,
        strategy_version=definition.strategy_version,
        family=definition.family,
        signal_id=evaluation_id or f"signal-{index}",
        evaluation_id=evaluation_id or f"eval-{index}",
        decision_bar_timestamp=decision_time,
        decision_timestamp=decision_time,
        outcome_available_at=available_at or CUTOFF - timedelta(days=1, minutes=index),
        dataset_id="dataset-v2",
        replay_run_id="replay-v2",
        walk_forward_window_id=window,
        holdout_partition_id="holdout-v2" if holdout else "",
        market_regime=regime,
        session_period="midday",
        gross_r_multiple=value + total_cost,
        gross_return=value + total_cost,
        total_transaction_cost=total_cost,
        net_r_multiple=value,
        net_return=value,
        confidence=0.75 if value > 0 else 0.35,
        predicted_direction="BUY",
        realized_direction="BUY" if value > 0 else "SELL",
        out_of_sample=not in_sample,
        in_sample=in_sample,
        holdout_evaluation_passed=holdout,
        timestamp_integrity_passed=True,
        data_leakage_detected=False,
        r_multiple=value,
        pnl=value * 100,
        success=value > 0,
        regime=regime,
    )


if __name__ == "__main__":
    unittest.main()
