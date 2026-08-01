from __future__ import annotations

import ast
import unittest
from datetime import timedelta
from pathlib import Path

from backend.app.algorithms.meta_strategy import (
    AUTHORITATIVE_PARITY_COMPONENTS,
    BACKTEST_REPLACES_ONLY_RUNTIME_BOUNDARIES,
    BACKTEST_RUNTIME_PIPELINE_ENTRYPOINT,
    META_STRATEGY_EXECUTION_PIPELINE_STAGES,
    MetaStrategyBacktestRequest,
    MetaStrategySimulatedBrokerAdapter,
    MetaStrategySimulationConfig,
    assert_backtest_runtime_parity,
    build_meta_strategy_market_snapshot,
    build_meta_strategy_order_intent,
    run_meta_strategy_backtest,
    select_point_in_time_artifact,
)
from backend.app.algorithms.meta_strategy.execution_pipeline import run_meta_strategy_execution_pipeline
from backend.tests.test_meta_strategy_step7_market_snapshot import DECISION_TIMESTAMP, request_with


ROOT = Path(__file__).resolve().parents[2]
BACKTEST_DIR = ROOT / "backend" / "app" / "algorithms" / "meta_strategy" / "backtest"
EXPECTED_BACKTEST_FILES = (
    "__init__.py",
    "engine.py",
    "execution_simulator.py",
    "ledger.py",
    "metrics.py",
    "diagnostics.py",
    "walk_forward.py",
    "holdout.py",
    "reports.py",
    "runtime_parity.py",
)


class MetaStrategyStep35BacktestPackageTest(unittest.TestCase):
    def test_backtest_package_files_exist_and_import(self) -> None:
        for filename in EXPECTED_BACKTEST_FILES:
            self.assertTrue((BACKTEST_DIR / filename).is_file(), filename)

    def test_backtester_uses_runtime_execution_pipeline_and_declares_only_simulated_replacements(self) -> None:
        result = run_meta_strategy_backtest(MetaStrategyBacktestRequest(decision_requests=(request_with(),)))
        parity = assert_backtest_runtime_parity()

        self.assertIs(BACKTEST_RUNTIME_PIPELINE_ENTRYPOINT, run_meta_strategy_execution_pipeline)
        self.assertEqual(result.decisions[0].mode, "BACKTEST")
        self.assertEqual(result.decisions[0].stage_sequence, META_STRATEGY_EXECUTION_PIPELINE_STAGES)
        self.assertEqual(parity.replaced_boundaries, ("data_adapter", "clock_adapter", "persistence_adapter", "execution_adapter"))
        self.assertEqual(BACKTEST_REPLACES_ONLY_RUNTIME_BOUNDARIES, parity.replaced_boundaries)
        self.assertFalse(parity.decision_logic_duplicated)
        self.assertTrue(parity.passed)

    def test_runtime_parity_lists_authoritative_components_shared_by_paper_replay_and_backtest(self) -> None:
        parity = assert_backtest_runtime_parity()

        expected_components = {
            "market_snapshot_builder",
            "indicator_functions",
            "strategy_registry",
            "strategy_implementations",
            "regime_classifier",
            "context_modules",
            "candidate_aggregator",
            "ml_feature_builder",
            "model_policy",
            "local_risk_gates",
            "sizing",
            "order_construction",
            "position_management_policy",
            "exit_policy",
        }
        self.assertEqual(set(AUTHORITATIVE_PARITY_COMPONENTS), expected_components)
        self.assertEqual(set(parity.authoritative_components), expected_components)
        for name, implementation in parity.authoritative_components.items():
            self.assertIn(AUTHORITATIVE_PARITY_COMPONENTS[name].__name__, implementation)

    def test_engine_does_not_import_or_call_deterministic_decision_modules_directly(self) -> None:
        tree = ast.parse((BACKTEST_DIR / "engine.py").read_text(encoding="utf-8"))
        imported = imported_module_names(tree)
        self.assertIn("backend.app.algorithms.meta_strategy.execution_pipeline", imported)
        self.assertNotIn("backend.app.algorithms.meta_strategy.candidate_generator", imported)
        self.assertNotIn("backend.app.algorithms.meta_strategy.family_aggregation", imported)
        self.assertNotIn("backend.app.algorithms.meta_strategy.inference.predictor", imported)

    def test_same_candle_lookahead_is_prohibited(self) -> None:
        request = request_with(one_minute_end=DECISION_TIMESTAMP)

        with self.assertRaisesRegex(ValueError, "same-candle lookahead"):
            run_meta_strategy_backtest(MetaStrategyBacktestRequest(decision_requests=(request,)))

    def test_backtest_must_be_driven_by_chronological_finalized_one_minute_events(self) -> None:
        later = request_with(decision_timestamp=DECISION_TIMESTAMP + timedelta(minutes=1))
        earlier = request_with(decision_timestamp=DECISION_TIMESTAMP)

        with self.assertRaisesRegex(ValueError, "finalized one-minute events must be chronological"):
            run_meta_strategy_backtest(MetaStrategyBacktestRequest(decision_requests=(later, earlier)))

    def test_backtest_derives_higher_timeframes_from_one_minute_events_when_omitted(self) -> None:
        request = request_with().model_copy(update={"five_minute_candles": (), "fifteen_minute_candles": ()})

        result = run_meta_strategy_backtest(MetaStrategyBacktestRequest(decision_requests=(request,)))
        candles = result.decisions[0].snapshot.candles

        self.assertGreaterEqual(len(candles["5m"]), 1)
        self.assertGreaterEqual(len(candles["15m"]), 1)
        self.assertTrue(all(row["timeframe"] == "5Min" for row in candles["5m"]))
        self.assertTrue(all(row["timeframe"] == "15Min" for row in candles["15m"]))
        self.assertEqual(result.diagnostics.event_policy["drivenBy"], "finalized_one_minute_events")
        self.assertEqual(result.diagnostics.event_policy["higherTimeframes"], "derived_from_one_minute")
        self.assertTrue(result.diagnostics.event_policy["lookaheadPrevention"])

    def test_point_in_time_model_artifacts_are_selected_without_future_leakage(self) -> None:
        old_artifact = {"artifactId": "old", "availableAt": (DECISION_TIMESTAMP - timedelta(days=2)).isoformat()}
        latest_valid = {"artifactId": "valid", "availableAt": (DECISION_TIMESTAMP - timedelta(minutes=1)).isoformat()}
        future_artifact = {"artifactId": "future", "availableAt": (DECISION_TIMESTAMP + timedelta(seconds=1)).isoformat()}

        selected = select_point_in_time_artifact((old_artifact, future_artifact, latest_valid), DECISION_TIMESTAMP)

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["artifactId"], "valid")

    def test_spread_slippage_fees_order_delay_and_partial_fills_are_modeled(self) -> None:
        snapshot = build_meta_strategy_market_snapshot(request_with())
        intent_result = build_meta_strategy_order_intent(
            snapshot=snapshot,
            side="BUY",
            quantity=10,
            stop_price=snapshot.last_price - 1.0,
        )
        assert intent_result.intent is not None
        broker = MetaStrategySimulatedBrokerAdapter(
            MetaStrategySimulationConfig(
                spread_bps=3.0,
                slippage_bps=4.0,
                fee_per_share=0.02,
                regulatory_fee_per_share=0.001,
                order_delay_seconds=2.5,
                partial_fill_ratio=0.4,
            )
        )

        result = broker.submit(intent_result.intent, mode="BACKTEST")

        self.assertEqual(result["algorithmId"], "meta_strategy")
        self.assertEqual(result["status"], "SIMULATED_PARTIAL_FILL")
        self.assertEqual(result["requestedQuantity"], 10)
        self.assertEqual(result["filledQuantity"], 4)
        self.assertEqual(result["spreadBps"], 3.0)
        self.assertEqual(result["slippageBps"], 4.0)
        self.assertEqual(result["feePerShare"], 0.02)
        self.assertEqual(result["regulatoryFeePerShare"], 0.001)
        self.assertEqual(result["orderDelaySeconds"], 2.5)
        self.assertIn("meta_strategy.backtest.order_delay_modeled", result["reasonCodes"])

    def test_limit_order_non_fill_and_no_trade_reasons_are_reported(self) -> None:
        snapshot = build_meta_strategy_market_snapshot(request_with())
        intent_result = build_meta_strategy_order_intent(
            snapshot=snapshot,
            side="BUY",
            quantity=10,
            stop_price=snapshot.last_price - 1.0,
        )
        assert intent_result.intent is not None
        broker = MetaStrategySimulatedBrokerAdapter(MetaStrategySimulationConfig(limit_order_fill_probability=0.0))

        broker_result = broker.submit(intent_result.intent, mode="BACKTEST")
        backtest_result = run_meta_strategy_backtest(MetaStrategyBacktestRequest(decision_requests=(request_with(),)))

        self.assertEqual(broker_result["status"], "SIMULATED_UNFILLED")
        self.assertEqual(broker_result["filledQuantity"], 0)
        self.assertIn("meta_strategy.backtest.limit_order_non_fill_modeled", broker_result["reasonCodes"])
        self.assertEqual(backtest_result.metrics.no_trade_count, 1)
        self.assertEqual(backtest_result.ledger.no_trades[0].status, "HOLD")
        self.assertIn("meta_strategy.backtest.no_trade_recorded", backtest_result.ledger.no_trades[0].reason_codes)

    def test_report_breaks_results_out_by_required_backtest_dimensions(self) -> None:
        result = run_meta_strategy_backtest(MetaStrategyBacktestRequest(decision_requests=(request_with(),)))

        self.assertEqual(set(result.report.breakdowns), {"strategy", "family", "regime", "session", "marketCondition"})
        self.assertIn("multi_timeframe_trend_alignment", result.report.breakdowns["strategy"])
        self.assertIn("MORNING", result.report.breakdowns["session"])
        self.assertGreaterEqual(result.report.breakdowns["strategy"]["multi_timeframe_trend_alignment"]["noTrades"], 1)

    def test_backtest_engine_does_not_import_promotion_workflows(self) -> None:
        tree = ast.parse((BACKTEST_DIR / "engine.py").read_text(encoding="utf-8"))
        imported = imported_module_names(tree)

        self.assertNotIn("backend.app.algorithms.meta_strategy.promotion", imported)
        self.assertNotIn("backend.app.algorithms.meta_strategy.settings_store", imported)


def imported_module_names(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


if __name__ == "__main__":
    unittest.main()
