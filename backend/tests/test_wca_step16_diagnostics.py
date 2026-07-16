from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.algorithms.wca.backtest.engine import run_wca_backtest
from backend.app.algorithms.wca.contracts import WcaBacktestSideMode, WcaSide
from backend.tests.test_wca_step14_15_backend_backtest import backtest_request, fake_voters, sample_candles


class WcaStep16DiagnosticsTests(unittest.TestCase):
    def test_aggregate_diagnostics_separate_gross_net_costs_and_drawdown(self) -> None:
        with patch("backend.app.algorithms.wca.backtest.engine.WCA_PRIMARY_VOTERS", fake_voters(WcaSide.BUY)):
            result = run_wca_backtest(backtest_request(side_mode=WcaBacktestSideMode.LONG_AND_SHORT))

        diagnostics = result.metrics["diagnostics"]
        aggregate = diagnostics["aggregate"]

        self.assertGreater(len(result.trades), 0)
        self.assertIn("netProfit", aggregate)
        self.assertIn("grossProfit", aggregate)
        self.assertIn("grossLoss", aggregate)
        self.assertIn("profitFactor", aggregate)
        self.assertIn("expectancy", aggregate)
        self.assertIn("averageR", aggregate)
        self.assertIn("maximumClosedEquityDrawdown", aggregate)
        self.assertIn("maximumMarkToMarketDrawdown", aggregate)
        self.assertTrue(aggregate["drawdownIncludesOpenPositions"])
        self.assertGreaterEqual(aggregate["totalEstimatedCosts"], 0)
        self.assertEqual(aggregate["netAfterCosts"], result.total_pnl)

    def test_diagnostics_include_required_breakdowns_and_lineage(self) -> None:
        with patch("backend.app.algorithms.wca.backtest.engine.WCA_PRIMARY_VOTERS", fake_voters(WcaSide.BUY)):
            result = run_wca_backtest(backtest_request(side_mode=WcaBacktestSideMode.LONG_AND_SHORT))

        diagnostics = result.metrics["diagnostics"]
        breakdowns = diagnostics["breakdowns"]
        expected_breakdowns = {
            "byStrategy",
            "byStrategyFamily",
            "bySide",
            "byMarketTrendStatus",
            "byVolatilityStatus",
            "byLiquidityStatus",
            "bySessionPhase",
            "byConfidenceBand",
            "byScoreBand",
            "byScoreEdgeBand",
            "byAgreementBand",
            "byDynamicProfile",
            "byActiveOverlay",
            "byExitReason",
            "byEntryRejectionReason",
        }

        self.assertTrue(expected_breakdowns.issubset(breakdowns.keys()))
        self.assertTrue(all(link["linked"] for link in diagnostics["lineage"]["tradeDecisionLinks"]))
        self.assertTrue(diagnostics["lineage"]["decisionsIncludeStrategyContributions"])
        self.assertTrue(diagnostics["lineage"]["decisionsIncludeSettings"])
        self.assertFalse(diagnostics["lineage"]["rejectedSignalsCountedAsExecutedTrades"])

    def test_local_rejected_signals_have_counterfactuals_without_trades(self) -> None:
        candles = sample_candles(25)
        with patch("backend.app.algorithms.wca.backtest.engine.WCA_PRIMARY_VOTERS", fake_voters(WcaSide.BUY)):
            result = run_wca_backtest(backtest_request(candles=candles, side_mode=WcaBacktestSideMode.LONG_AND_SHORT))

        local = result.metrics["diagnostics"]["counterfactuals"]["locallyRejectedSignals"]
        self.assertTrue(local)
        self.assertTrue(result.metrics["diagnostics"]["counterfactuals"]["rejectedSignalsAreNotExecutedTrades"])
        rejected_ids = {row["decisionId"] for row in local}
        trade_ids = {trade.decision_id for trade in result.trades}
        self.assertFalse(rejected_ids & trade_ids)

    def test_global_rejected_orders_have_counterfactuals_without_executed_trades(self) -> None:
        def reject_all_global_gates(*args, **kwargs):
            return SimpleNamespace(
                allow_new_entries=False,
                approved_quantity=0,
                proposed_quantity=kwargs.get("quantity", args[2] if len(args) > 2 else 0),
                reason_codes=("global_gate.test_reject",),
            )

        with (
            patch("backend.app.algorithms.wca.backtest.engine.WCA_PRIMARY_VOTERS", fake_voters(WcaSide.BUY)),
            patch("backend.app.algorithms.wca.backtest.engine._simulate_global_gate", reject_all_global_gates),
        ):
            result = run_wca_backtest(backtest_request(side_mode=WcaBacktestSideMode.LONG_AND_SHORT))

        diagnostics = result.metrics["diagnostics"]
        self.assertEqual(result.trades, ())
        self.assertGreater(diagnostics["aggregate"]["globalGateRejections"], 0)
        self.assertTrue(diagnostics["counterfactuals"]["globallyRejectedOrders"])
        self.assertIn("global_gate.test_reject", diagnostics["breakdowns"]["byEntryRejectionReason"])


if __name__ == "__main__":
    unittest.main()
