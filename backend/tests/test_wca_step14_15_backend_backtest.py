from __future__ import annotations

import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.algorithms.wca.backtest import (
    WCA_BACKTEST_FILE_INVENTORY,
    WCA_BACKTEST_INVENTORY,
    WCA_BACKTEST_RESPONSIBILITY_IDS,
)
from backend.app.algorithms.wca.backtest.engine import run_wca_backtest, run_wca_backtest_modes
from backend.app.algorithms.wca.contracts import (
    BacktestRunConfiguration,
    WcaBacktestMode,
    WcaBacktestRequest,
    WcaBacktestSideMode,
    WcaCandle,
    WcaEvaluationStatus,
    WcaSide,
    WcaStrategyEvaluation,
)
from backend.app.main import app


class WcaBacktestInventoryTests(unittest.TestCase):
    def test_backtest_package_contains_only_the_dedicated_inventory_files(self) -> None:
        backtest_path = Path(__file__).resolve().parents[2] / "backend" / "app" / "algorithms" / "wca" / "backtest"
        files = tuple(sorted(path.name for path in backtest_path.glob("*.py")))

        self.assertEqual(files, tuple(sorted(WCA_BACKTEST_FILE_INVENTORY)))
        self.assertEqual(
            WCA_BACKTEST_FILE_INVENTORY,
            (
                "__init__.py",
                "engine.py",
                "execution.py",
                "ledger.py",
                "metrics.py",
                "reports.py",
                "walk_forward.py",
            ),
        )

    def test_backtest_inventory_records_every_backend_authoritative_responsibility(self) -> None:
        self.assertEqual(
            WCA_BACKTEST_RESPONSIBILITY_IDS,
            {
                "wca_replay_orchestration",
                "point_in_time_snapshots",
                "signal_generation",
                "next_bar_execution",
                "fill_simulation",
                "slippage_and_trading_costs",
                "partial_fill_simulation",
                "wca_position_ledger",
                "wca_trade_ledger",
                "wca_metrics",
                "rolling_diagnostics",
                "walk_forward_testing",
                "untouched_holdout_testing",
                "wca_reports",
                "baseline_comparison",
            },
        )
        self.assertEqual(len(WCA_BACKTEST_INVENTORY), 15)
        self.assertTrue(all(row.owner_file in WCA_BACKTEST_FILE_INVENTORY for row in WCA_BACKTEST_INVENTORY))

    def test_backtest_result_evidence_matches_inventory_contract(self) -> None:
        result = run_wca_backtest(backtest_request(candles=sample_candles(35)))
        suite = run_wca_backtest_modes(backtest_request(candles=multi_session_candles(28)))

        self.assertEqual(result.metrics["engineVersion"], "wca_backend_backtest_v1")
        self.assertEqual(result.metrics["fillRule"], "signal on bar t fills no earlier than bar t+1 open")
        self.assertIn("fill_no_earlier_than_bar_t_plus_1_open", result.metrics["eventOrder"])
        self.assertTrue(result.metrics["openPositionDrawdownIncluded"])
        self.assertIn("diagnostics", result.metrics)
        self.assertEqual(suite.walk_forward.label, "Walk-forward evaluation")
        self.assertEqual(suite.holdout.label, "Untouched holdout")
        self.assertTrue(all(comparison.metrics["identicalDataset"] for comparison in suite.comparisons))


class WcaStep14BackendBacktestTests(unittest.TestCase):
    def test_backend_backtest_uses_pipeline_and_next_bar_fills(self) -> None:
        request = backtest_request(side_mode=WcaBacktestSideMode.LONG_AND_SHORT)

        with patch("backend.app.algorithms.wca.backtest.engine.WCA_PRIMARY_VOTERS", fake_voters(WcaSide.BUY)):
            result = run_wca_backtest(request)

        self.assertIn("strategy_registry", result.metrics["calledProductionModules"])
        self.assertIn("confidence_calibration", result.metrics["calledProductionModules"])
        self.assertIn("weight_engine", result.metrics["calledProductionModules"])
        self.assertIn("market_status", result.metrics["calledProductionModules"])
        self.assertIn("dynamic_profile", result.metrics["calledProductionModules"])
        self.assertIn("aggregation", result.metrics["calledProductionModules"])
        self.assertIn("local_gates", result.metrics["calledProductionModules"])
        self.assertIn("sizing", result.metrics["calledProductionModules"])
        self.assertIn("exits", result.metrics["calledProductionModules"])
        self.assertGreater(len(result.trades), 0)
        first_trade = result.trades[0]
        source_decision = next(decision for decision in result.decisions if decision.decision_id == first_trade.decision_id)
        self.assertGreater(first_trade.entry_at, source_decision.data_timestamp)
        self.assertEqual(result.metrics["fillRule"], "signal on bar t fills no earlier than bar t+1 open")

    def test_long_only_default_does_not_silently_enable_short_selling(self) -> None:
        with patch("backend.app.algorithms.wca.backtest.engine.WCA_PRIMARY_VOTERS", fake_voters(WcaSide.SELL)):
            result = run_wca_backtest(backtest_request())

        self.assertEqual(result.run_configuration.side_mode, WcaBacktestSideMode.LONG_ONLY.value)
        self.assertEqual(result.trades, ())

    def test_early_session_strategies_are_evaluated_in_valid_window(self) -> None:
        result = run_wca_backtest(backtest_request(candles=opening_range_candles()))
        opening_decisions = [
            decision
            for decision in result.decisions
            if 13 * 60 + 45 <= decision.data_timestamp.hour * 60 + decision.data_timestamp.minute <= 14 * 60 + 30
        ]

        self.assertTrue(opening_decisions)
        self.assertTrue(
            any(
                row.strategy_id == "C7" and row.status != WcaEvaluationStatus.NOT_APPLICABLE.value
                for decision in opening_decisions
                for row in decision.aggregation.strategy_evaluations
            )
        )

    def test_results_are_reproducible_from_run_id_and_configuration_hash(self) -> None:
        request = backtest_request()

        with patch("backend.app.algorithms.wca.backtest.engine.WCA_PRIMARY_VOTERS", fake_voters(WcaSide.BUY)):
            first = run_wca_backtest(request)
            second = run_wca_backtest(request)

        self.assertEqual(first.run_configuration.configuration_hash, second.run_configuration.configuration_hash)
        self.assertEqual(first.total_pnl, second.total_pnl)
        self.assertEqual(first.metrics["dataManifestHash"], second.metrics["dataManifestHash"])
        self.assertTrue(first.metrics["openPositionDrawdownIncluded"])

    def test_api_submit_status_result_and_report_are_backend_authoritative(self) -> None:
        client = TestClient(app)
        payload = backtest_request(candles=sample_candles(35)).model_dump(mode="json")
        response = client.post("/api/wca/backtests", json=payload)

        self.assertEqual(response.status_code, 200, response.text)
        run_id = response.json()["run_configuration"]["run_id"]
        self.assertTrue(response.json()["metrics"]["usesBackendEngine"])
        self.assertEqual(client.get(f"/api/wca/backtests/{run_id}/status").json()["status"], "complete")
        self.assertEqual(client.get(f"/api/wca/backtests/{run_id}").status_code, 200)
        self.assertTrue(client.get(f"/api/wca/backtests/{run_id}/report").json()["backendAuthoritative"])

    def test_frontend_daily_wca_backtest_calls_backend_endpoint(self) -> None:
        frontend = Path(__file__).resolve().parents[2] / "frontend" / "src" / "main.ts"
        source = frontend.read_text(encoding="utf-8")

        self.assertIn("/api/wca/backtests", source)
        self.assertIn("await runBackendConfidenceBacktest(preparedOneMinuteCandles, latestSessionDate)", source)
        self.assertIn("await runBackendConfidenceBacktest(state.candles, latestSessionDate)", source)
        self.assertEqual(source.count("backtestConfidenceAggregation("), 1)


class WcaStep15BacktestModesTests(unittest.TestCase):
    def test_backtest_modes_are_labeled_and_smoke_is_not_production_validation(self) -> None:
        suite = run_wca_backtest_modes(backtest_request(candles=multi_session_candles(28)))

        self.assertEqual(suite.smoke.label, "Daily smoke test")
        self.assertFalse(suite.smoke.production_validation)
        self.assertIn("Rolling 20 sessions", {row.label for row in suite.rolling})
        self.assertEqual(suite.full_history.label, "Full historical replay")
        self.assertEqual(suite.walk_forward.label, "Walk-forward evaluation")
        self.assertEqual(suite.holdout.label, "Untouched holdout")
        self.assertFalse(suite.holdout.production_validation)

    def test_holdout_is_excluded_from_comparison_optimization(self) -> None:
        suite = run_wca_backtest_modes(backtest_request(candles=multi_session_candles(30)))

        self.assertIn("wca.backtest.holdout_excluded_from_optimization", suite.reason_codes)
        self.assertTrue(all(comparison.metrics["holdoutExcluded"] for comparison in suite.comparisons))

    def test_required_ab_comparisons_use_identical_dataset_and_execution_assumptions(self) -> None:
        suite = run_wca_backtest_modes(backtest_request(candles=multi_session_candles(30)))
        labels = {comparison.label for comparison in suite.comparisons}

        self.assertEqual(
            labels,
            {
                "legacy WCA versus new WCA",
                "static weights versus dynamic weights",
                "baseline settings versus dynamic profile",
                "without modifiers versus with modifiers",
                "without correlation control versus with correlation control",
                "old strategy catalog versus corrected catalog",
                "gross results versus net-after-cost results",
            },
        )
        self.assertTrue(all(comparison.metrics["identicalDataset"] for comparison in suite.comparisons))
        self.assertTrue(all(comparison.metrics["identicalExecutionAssumptions"] for comparison in suite.comparisons))


class FakeVoter:
    def __init__(self, strategy_id: str, name: str, family: str, weight: float, side: WcaSide) -> None:
        self.strategy_id = strategy_id
        self.name = name
        self.family = family
        self.version = f"fake_{strategy_id.lower()}_v1"
        self.base_weight = weight
        self.side = side

    def evaluate(self, market) -> WcaStrategyEvaluation:
        return WcaStrategyEvaluation(
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            name=self.name,
            status=WcaEvaluationStatus.ACTIVE,
            signal=self.side,
            confidence=0.9,
            raw_confidence=0.9,
            calibrated_confidence=0.9,
            direction=self.side,
            applicability=WcaEvaluationStatus.ACTIVE,
            evidence_strength=0.9,
            data_quality_status=WcaEvaluationStatus.ACTIVE,
            base_weight=self.base_weight,
            effective_weight=self.base_weight,
            contribution=self.base_weight * 0.9,
            reason_codes=("test.fake_voter",),
        )


def fake_voters(side: WcaSide) -> tuple[FakeVoter, ...]:
    return (
        FakeVoter("C1", "Fake Trend", "trend", 0.10, side),
        FakeVoter("C4", "Fake Mean Reversion", "mean_reversion", 0.08, side),
        FakeVoter("C7", "Fake Breakout", "breakout", 0.10, side),
    )


def backtest_request(
    *,
    candles: tuple[WcaCandle, ...] | None = None,
    side_mode: WcaBacktestSideMode = WcaBacktestSideMode.LONG_ONLY,
) -> WcaBacktestRequest:
    rows = candles or sample_candles(80)
    return WcaBacktestRequest(
        configuration=BacktestRunConfiguration(
            run_id="wca-backtest-test",
            mode=WcaBacktestMode.DAILY_SMOKE,
            symbol="SPY",
            start=rows[0].timestamp,
            end=rows[-1].timestamp,
            configuration_version="test-config-v1",
            data_manifest_hash="test-data-hash",
            side_mode=side_mode,
            starting_equity=100_000,
            slippage_per_share=0.01,
            fee_per_share=0.001,
            max_participation_percent=20,
        ),
        candles=rows,
    )


def sample_candles(count: int) -> tuple[WcaCandle, ...]:
    start = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)
    price = 100.0
    candles: list[WcaCandle] = []
    for index in range(count):
        open_price = price
        close = open_price + 0.04
        candles.append(
            WcaCandle(
                timestamp=start + timedelta(minutes=index),
                open=open_price,
                high=close + 0.12,
                low=open_price - 0.03,
                close=close,
                volume=100_000,
            )
        )
        price = close
    return tuple(candles)


def opening_range_candles() -> tuple[WcaCandle, ...]:
    rows = list(sample_candles(70))
    adjusted: list[WcaCandle] = []
    for index, candle in enumerate(rows):
        if index == 15:
            adjusted.append(candle.model_copy(update={"close": candle.close + 1.2, "high": candle.high + 1.3, "volume": 250_000}))
        else:
            adjusted.append(candle)
    return tuple(adjusted)


def multi_session_candles(session_count: int) -> tuple[WcaCandle, ...]:
    candles: list[WcaCandle] = []
    price = 100.0
    for session in range(session_count):
        start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc) + timedelta(days=session)
        for minute in range(30):
            open_price = price
            close = open_price + 0.03
            candles.append(
                WcaCandle(
                    timestamp=start + timedelta(minutes=minute),
                    open=open_price,
                    high=close + 0.08,
                    low=open_price - 0.02,
                    close=close,
                    volume=80_000,
                )
            )
            price = close
    return tuple(candles)


if __name__ == "__main__":
    unittest.main()
