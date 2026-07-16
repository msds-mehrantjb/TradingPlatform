from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.domain.models import Direction, Signal, StrategyFamily
from backend.app.ensemble import strategy_diversity_diagnostics


START = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def row(
    decision: int,
    strategy_id: str,
    family: StrategyFamily,
    signal: Signal,
    outcome_r: float | None,
    *,
    setup_id: str | None = None,
    out_of_sample: bool = True,
) -> dict:
    direction = {Signal.BUY: Direction.LONG, Signal.SELL: Direction.SHORT, Signal.HOLD: Direction.FLAT}[signal]
    return {
        "decisionKey": f"d{decision}",
        "decisionTimestamp": START + timedelta(minutes=decision),
        "walkForwardFold": "fold-2026-01",
        "isOutOfSample": out_of_sample,
        "strategyId": strategy_id,
        "strategyName": strategy_id.replace("_", " ").title(),
        "family": family,
        "signal": signal,
        "direction": direction,
        "eligible": True,
        "setupId": setup_id,
        "outcomeR": outcome_r,
    }


def fixture_rows() -> list[dict]:
    rows: list[dict] = []
    duplicate_pattern = [
        (Signal.BUY, 1.0, "trend-a"),
        (Signal.BUY, -0.4, "trend-b"),
        (Signal.HOLD, None, None),
        (Signal.SELL, 0.8, "trend-c"),
        (Signal.BUY, 0.6, "trend-d"),
        (Signal.SELL, -0.5, "trend-e"),
    ]
    breakout_pattern = [
        (Signal.BUY, 0.5, "breakout-a"),
        (Signal.HOLD, None, None),
        (Signal.SELL, -0.3, "breakout-b"),
        (Signal.SELL, 0.9, "breakout-c"),
        (Signal.HOLD, None, None),
        (Signal.BUY, -0.2, "breakout-d"),
    ]
    reversal_pattern = [
        (Signal.SELL, -0.6, "reversal-a"),
        (Signal.SELL, 0.7, "reversal-b"),
        (Signal.HOLD, None, None),
        (Signal.BUY, -0.4, "reversal-c"),
        (Signal.BUY, 0.9, "reversal-d"),
        (Signal.HOLD, None, None),
    ]
    mean_reversion_pattern = [
        (Signal.HOLD, None, None),
        (Signal.BUY, 0.4, "mean-a"),
        (Signal.BUY, -0.2, "mean-b"),
        (Signal.HOLD, None, None),
        (Signal.SELL, 0.3, "mean-c"),
        (Signal.SELL, 0.5, "mean-d"),
    ]
    gap_pattern = [
        (Signal.BUY, 0.2, "gap-a"),
        (Signal.HOLD, None, None),
        (Signal.HOLD, None, None),
        (Signal.SELL, -0.6, "gap-b"),
        (Signal.BUY, 0.7, "gap-c"),
        (Signal.HOLD, None, None),
    ]
    for index, (signal, outcome, setup) in enumerate(duplicate_pattern, start=1):
        rows.append(row(index, "trend_primary", StrategyFamily.TREND, signal, outcome, setup_id=setup))
        rows.append(row(index, "trend_clone", StrategyFamily.TREND, signal, outcome, setup_id=setup))
    for index, (signal, outcome, setup) in enumerate(breakout_pattern, start=1):
        rows.append(row(index, "opening_range_breakout", StrategyFamily.BREAKOUT, signal, outcome, setup_id=setup))
    for index, (signal, outcome, setup) in enumerate(reversal_pattern, start=1):
        rows.append(row(index, "failed_breakout_reversal", StrategyFamily.REVERSAL, signal, outcome, setup_id=setup))
    for index, (signal, outcome, setup) in enumerate(mean_reversion_pattern, start=1):
        rows.append(row(index, "vwap_mean_reversion", StrategyFamily.MEAN_REVERSION, signal, outcome, setup_id=setup))
    for index, (signal, outcome, setup) in enumerate(gap_pattern, start=1):
        rows.append(row(index, "gap_continuation_gap_fade", StrategyFamily.GAP_SESSION, signal, outcome, setup_id=setup))
    rows.append(row(1, "training_only_strategy", StrategyFamily.SAFETY, Signal.BUY, 9.9, setup_id="training", out_of_sample=False))
    return rows


class StrategyDiversityDiagnosticsTest(unittest.TestCase):
    def test_report_is_out_of_sample_and_covers_every_strategy_and_family(self) -> None:
        report = strategy_diversity_diagnostics(fixture_rows(), generated_at=START)

        self.assertEqual(report.version, "strategy_diversity_diagnostics_v1")
        self.assertTrue(report.outOfSampleOnly)
        self.assertNotIn("training_only_strategy", {row.subjectId for row in report.strategyDiagnostics})
        self.assertEqual(
            {row.subjectId for row in report.strategyDiagnostics},
            {
                "trend_primary",
                "trend_clone",
                "opening_range_breakout",
                "failed_breakout_reversal",
                "vwap_mean_reversion",
                "gap_continuation_gap_fade",
            },
        )
        self.assertEqual(
            {row.subjectId for row in report.familyDiagnostics},
            {"TREND", "BREAKOUT", "REVERSAL", "MEAN_REVERSION", "GAP_SESSION"},
        )

    def test_nearly_identical_strategy_pair_is_reported_without_auto_removal(self) -> None:
        report = strategy_diversity_diagnostics(fixture_rows(), generated_at=START)
        pair = next(
            row
            for row in report.nearlyIdenticalPairs
            if {row.strategyA, row.strategyB} == {"trend_primary", "trend_clone"}
        )

        self.assertTrue(pair.nearlyIdentical)
        self.assertTrue(pair.inclusionTestingOnly)
        self.assertEqual(pair.signalCorrelation, 1.0)
        self.assertEqual(pair.directionalAgreementRate, 1.0)
        self.assertIn("does not automatically remove", pair.explanation)

    def test_pairwise_and_family_correlation_matrices_are_generated(self) -> None:
        report = strategy_diversity_diagnostics(fixture_rows(), generated_at=START)

        self.assertEqual(report.pairwiseCorrelationMatrix["trend_primary"]["trend_clone"], 1.0)
        self.assertIn("TREND", report.familyCorrelationMatrix)
        self.assertIn("BREAKOUT", report.familyCorrelationMatrix["TREND"])

    def test_add_one_and_leave_one_out_compare_ensemble_performance(self) -> None:
        report = strategy_diversity_diagnostics(fixture_rows(), generated_at=START)
        trend = next(row for row in report.strategyDiagnostics if row.subjectId == "trend_primary")

        self.assertGreater(trend.outOfSampleTrades, 0)
        self.assertIsInstance(trend.addOneExpectancy, float)
        self.assertIsInstance(trend.leaveOneOutExpectancy, float)
        self.assertAlmostEqual(
            trend.incrementalExpectancy,
            trend.ensembleExpectancyWithSubject - trend.ensembleExpectancyWithoutSubject,
            places=4,
        )
        self.assertIn("with subject", trend.explanation)

    def test_requires_out_of_sample_walk_forward_rows(self) -> None:
        rows = [row(1, "trend_primary", StrategyFamily.TREND, Signal.BUY, 1.0, out_of_sample=False)]

        with self.assertRaisesRegex(ValueError, "out-of-sample walk-forward"):
            strategy_diversity_diagnostics(rows, generated_at=START)


if __name__ == "__main__":
    unittest.main()
