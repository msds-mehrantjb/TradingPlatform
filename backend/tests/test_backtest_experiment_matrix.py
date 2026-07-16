from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from backend.app.backtesting import (
    BacktestMarketPeriod,
    BacktestVariantInput,
    ReplayDecisionSnapshot,
    ReplayResult,
    ReplayTrade,
    build_experiment_matrix_report,
    required_diagnostic_experiments,
    required_experiment_variants,
)
from backend.app.domain.models import Signal


START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
PERIOD = BacktestMarketPeriod(
    startUtc=START,
    endUtc=START + timedelta(days=5),
    symbols=["SPY"],
)
COSTS = {
    "spreadDollars": 0.02,
    "slippagePerShare": 0.01,
    "feesPerShare": 0.005,
    "latencySeconds": 1,
}


class BacktestExperimentMatrixTest(unittest.TestCase):
    def test_required_variants_keep_v1_reference_only_and_variant_b_primary_baseline(self) -> None:
        specs = {spec.variantId: spec for spec in required_experiment_variants()}

        self.assertEqual(["A", "B", "C", "D", "E"], list(specs))
        self.assertTrue(specs["A"].referenceOnly)
        self.assertTrue(specs["A"].usesV1SignalSchema)
        self.assertFalse(specs["A"].promotionEligible)
        self.assertTrue(specs["B"].primaryPromotionBaseline)
        self.assertTrue(specs["B"].promotionEligible)

    def test_report_includes_fold_and_aggregate_metrics_and_isolates_contributions(self) -> None:
        report = build_experiment_matrix_report(
            [
                variant_input("A", [2.0, -1.0]),
                variant_input("B", [5.0, -1.0]),
                variant_input("C", [6.0, -0.5]),
                variant_input("D", [8.0, -2.0]),
                variant_input("E", [9.0, -1.0]),
            ],
            generated_at=START,
        )

        by_id = {variant.spec.variantId: variant for variant in report.variants}
        self.assertEqual(report.primaryPromotionBaselineVariantId, "B")
        self.assertEqual(by_id["A"].promotionBaselineVariantId, None)
        self.assertEqual(by_id["B"].aggregateMetrics.tradeCount, 2)
        self.assertEqual(len(by_id["B"].foldMetrics), 2)
        self.assertEqual(by_id["B"].aggregateMetrics.netPnl, 4.0)
        deltas = {delta.comparisonId: delta for delta in report.contributionSummary}
        self.assertEqual(deltas["ml_filter_delta"].baselineVariantId, "B")
        self.assertEqual(deltas["ml_filter_delta"].comparisonVariantId, "C")
        self.assertEqual(deltas["ml_filter_delta"].netPnlDelta, 1.5)
        self.assertIn("variant_b_primary_ml_promotion_baseline", report.reasonCodes)

    def test_report_rejects_different_market_periods(self) -> None:
        changed_period = BacktestMarketPeriod(
            startUtc=START,
            endUtc=START + timedelta(days=6),
            symbols=["SPY"],
        )
        inputs = required_inputs()
        inputs[-1] = variant_input("E", [1.0], period=changed_period)

        with self.assertRaisesRegex(ValueError, "identical market periods"):
            build_experiment_matrix_report(inputs, generated_at=START)

    def test_report_rejects_different_cost_assumptions(self) -> None:
        inputs = required_inputs()
        inputs[-1] = variant_input("E", [1.0], costs={**COSTS, "slippagePerShare": 0.05})

        with self.assertRaisesRegex(ValueError, "identical cost assumptions"):
            build_experiment_matrix_report(inputs, generated_at=START)

    def test_report_rejects_different_decision_timestamp_universe(self) -> None:
        inputs = required_inputs()
        inputs[-1] = variant_input("E", [1.0], timestamp_offset=timedelta(minutes=5))

        with self.assertRaisesRegex(ValueError, "identical decision timestamp universe"):
            build_experiment_matrix_report(inputs, generated_at=START)

    def test_diagnostic_matrix_contains_required_ablations_and_gate_diagnostic_only(self) -> None:
        diagnostics = {item.diagnosticId: item for item in required_diagnostic_experiments()}

        self.assertIn("add_one_strategy_tests", diagnostics)
        self.assertIn("leave_one_out_strategy_tests", diagnostics)
        self.assertIn("context_ablations", diagnostics)
        self.assertIn("regime_filter_ablations", diagnostics)
        self.assertIn("family_normalization_ablation", diagnostics)
        self.assertIn("static_versus_dynamic_policy_comparison", diagnostics)
        self.assertTrue(diagnostics["global_gate_ablation"].diagnosticsOnly)
        self.assertFalse(diagnostics["global_gate_ablation"].promotionEligible)


def required_inputs() -> list[BacktestVariantInput]:
    return [
        variant_input("A", [1.0]),
        variant_input("B", [2.0]),
        variant_input("C", [3.0]),
        variant_input("D", [4.0]),
        variant_input("E", [5.0]),
    ]


def variant_input(
    variant_id: str,
    pnls: list[float],
    *,
    period: BacktestMarketPeriod = PERIOD,
    costs: dict = COSTS,
    timestamp_offset: timedelta = timedelta(0),
) -> BacktestVariantInput:
    fold_1_times = [START + timestamp_offset, START + timedelta(minutes=1) + timestamp_offset]
    fold_2_times = [START + timedelta(days=1) + timestamp_offset, START + timedelta(days=1, minutes=1) + timestamp_offset]
    return BacktestVariantInput(
        variantId=variant_id,
        marketPeriod=period,
        costAssumptions=costs,
        foldReplayResults={
            "fold_1": [replay_result(variant_id, "fold_1", fold_1_times, pnls[:1])],
            "fold_2": [replay_result(variant_id, "fold_2", fold_2_times, pnls[1:])],
        },
    )


def replay_result(variant_id: str, fold_id: str, timestamps: list[datetime], pnls: list[float]) -> ReplayResult:
    snapshots = [snapshot(timestamp) for timestamp in timestamps]
    trades = [trade(variant_id, fold_id, index, snapshots[index], pnl) for index, pnl in enumerate(pnls)]
    return ReplayResult(
        engineVersion=f"test_{variant_id}",
        symbol="SPY",
        sessionDate=timestamps[0].date(),
        decisionCount=len(snapshots),
        snapshots=snapshots,
        trades=trades,
        explanation="test replay result",
    )


def snapshot(timestamp: datetime) -> ReplayDecisionSnapshot:
    return ReplayDecisionSnapshot(
        snapshotId=f"snapshot-{timestamp.isoformat()}",
        symbol="SPY",
        decisionTimestampUtc=timestamp,
        sessionDate=timestamp.date(),
        maxInputTimestampUtc=timestamp,
        featureSnapshot={},
        strategyOutputs=[],
        contextOutputs=[],
        regimeState=None,
        gateDecision={"status": "PASS"},
        deterministicCandidate=None,
        ensembleDecision={"signal": "HOLD"},
        mlInference={"mode": "OFF"},
        effectivePolicy={},
        orderPlan=None,
        fill=None,
        exit=None,
        reasonCodes=[],
    )


def trade(variant_id: str, fold_id: str, index: int, source_snapshot: ReplayDecisionSnapshot, pnl: float) -> ReplayTrade:
    return ReplayTrade(
        tradeId=f"{variant_id}-{fold_id}-{index}",
        decisionSnapshotId=source_snapshot.snapshotId,
        symbol="SPY",
        side=Signal.BUY,
        quantity=10,
        submittedAt=source_snapshot.decisionTimestampUtc + timedelta(seconds=1),
        filledAt=source_snapshot.decisionTimestampUtc + timedelta(seconds=2),
        entryPrice=100.0,
        exitAt=source_snapshot.decisionTimestampUtc + timedelta(minutes=5),
        exitPrice=100.0 + (pnl / 10.0),
        pnl=pnl,
        costs={"total": 0.25},
        fillStatus="FILLED",
        exitStatus="TARGET" if pnl > 0 else "STOP",
        reasonCodes=[],
    )


if __name__ == "__main__":
    unittest.main()
