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
from backend.app.algorithms.wca.backtest.engine import prove_wca_production_parity, run_wca_backtest, run_wca_backtest_modes
from backend.app.algorithms.wca.backtest.execution import WCA_BACKTEST_EXECUTION_SIMULATION_VERSION, simulate_wca_backtest_execution
from backend.app.algorithms.wca.configuration import default_wca_configuration
from backend.app.algorithms.wca.contracts import (
    BacktestRunConfiguration,
    ProposedOrder,
    WcaBacktestMode,
    WcaBacktestRequest,
    WcaBacktestSideMode,
    WcaCandle,
    WcaEvaluationStatus,
    WcaMarketSnapshot,
    WcaQuote,
    WcaSide,
    WcaStrategyEvaluation,
)
from backend.app.algorithms.wca.cost_model import WCA_COST_MODEL_ADAPTER_VERSION
from backend.app.algorithms.wca.runtime_events import WcaFinalizedBarEvent
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
        configuration = default_wca_configuration()
        result = run_wca_backtest(backtest_request(candles=sample_candles(35)), configuration=configuration)
        suite = run_wca_backtest_modes(backtest_request(candles=multi_session_candles(28)), configuration=configuration)

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
        request = backtest_request(candles=execution_candles(), side_mode=WcaBacktestSideMode.LONG_AND_SHORT)

        with patch("backend.app.algorithms.wca.backtest.engine.WCA_PRIMARY_VOTERS", fake_voters(WcaSide.BUY)):
            result = run_wca_backtest(request, configuration=default_wca_configuration())

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
            result = run_wca_backtest(backtest_request(), configuration=default_wca_configuration())

        self.assertEqual(result.run_configuration.side_mode, WcaBacktestSideMode.LONG_ONLY.value)
        self.assertEqual(result.trades, ())

    def test_early_session_strategies_are_evaluated_in_valid_window(self) -> None:
        result = run_wca_backtest(backtest_request(candles=opening_range_candles()), configuration=default_wca_configuration())
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
        configuration = default_wca_configuration()

        with patch("backend.app.algorithms.wca.backtest.engine.WCA_PRIMARY_VOTERS", fake_voters(WcaSide.BUY)):
            first = run_wca_backtest(request, configuration=configuration)
            second = run_wca_backtest(request, configuration=configuration)

        self.assertEqual(first.run_configuration.configuration_hash, second.run_configuration.configuration_hash)
        self.assertEqual(first.total_pnl, second.total_pnl)
        self.assertEqual(first.metrics["dataManifestHash"], second.metrics["dataManifestHash"])
        self.assertTrue(first.metrics["openPositionDrawdownIncluded"])

    def test_backtest_pins_versions_and_cost_diagnostics(self) -> None:
        request = backtest_request()

        with patch("backend.app.algorithms.wca.backtest.engine.WCA_PRIMARY_VOTERS", fake_voters(WcaSide.BUY)):
            result = run_wca_backtest(request, configuration=default_wca_configuration())

        pinned = result.metrics["pinnedVersions"]
        self.assertEqual(pinned["configurationVersion"], result.run_configuration.configuration_version)
        self.assertEqual(pinned["marketDataManifest"], result.run_configuration.data_manifest_hash)
        self.assertEqual(pinned["costModelVersion"], WCA_COST_MODEL_ADAPTER_VERSION)
        self.assertEqual(pinned["executionSimulationVersion"], WCA_BACKTEST_EXECUTION_SIMULATION_VERSION)
        self.assertIn("C1", pinned["strategyVersions"])
        diagnostics = result.metrics["diagnostics"]
        self.assertIn("costDiagnostics", diagnostics["breakdowns"])
        self.assertIn("transactionCostSensitivity", diagnostics["breakdowns"])

    def test_parity_fixture_matches_runtime_shadow_replay_and_backtest_decisions(self) -> None:
        configuration = default_wca_configuration()
        candles = sample_candles(72)
        events = tuple(parity_event(index, candles[: 70 + index]) for index in range(1, 3))

        proof = prove_wca_production_parity(events, configuration=configuration)

        self.assertTrue(proof["identical"])
        self.assertEqual(proof["eventCount"], len(events))
        self.assertTrue(all(row["decisionHash"] for row in proof["rows"]))
        self.assertIn("wca.backtest.production_parity.proven", proof["reasonCodes"])

    def test_execution_simulator_models_filled_partial_cancelled_and_expired_orders(self) -> None:
        config = backtest_request().configuration.model_copy(update={"max_participation_percent": 1, "allow_partial_fills": True})
        bar = WcaCandle(timestamp=datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc), open=100, high=101, low=99, close=100.5, volume=100)
        order = ProposedOrder(decision_id="d", order_intent_id="i", symbol="SPY", side=WcaSide.BUY, quantity=200, trigger_price=100, limit_price=100, stop_price=99, target_price=102)

        partial = simulate_wca_backtest_execution(order=order, next_bar=bar, config=config, side_allowed=True)
        expired = simulate_wca_backtest_execution(order=order.model_copy(update={"limit_price": 90, "trigger_price": 90}), next_bar=bar, config=config, side_allowed=True)
        cancelled = simulate_wca_backtest_execution(order=order, next_bar=bar, config=config, side_allowed=False)
        filled = simulate_wca_backtest_execution(order=order.model_copy(update={"quantity": 1}), next_bar=bar, config=config, side_allowed=True)

        self.assertEqual(partial.status, "PARTIALLY_FILLED")
        self.assertEqual(expired.status, "EXPIRED")
        self.assertEqual(cancelled.status, "CANCELLED")
        self.assertEqual(filled.status, "FILLED")

    def test_api_submit_enqueues_backend_research_job(self) -> None:
        client = TestClient(app)
        payload = backtest_request(candles=sample_candles(35)).model_dump(mode="json")
        response = client.post("/api/wca/backtests", json=payload)

        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["job_id"]
        self.assertEqual(response.json()["status"], "QUEUED")
        self.assertTrue(response.json()["queued"])
        self.assertEqual(client.get(f"/api/wca/backtests/{job_id}/status").json()["status"], "queued")

    def test_frontend_daily_wca_backtest_calls_backend_endpoint(self) -> None:
        frontend = Path(__file__).resolve().parents[2] / "frontend" / "src" / "main.ts"
        source = frontend.read_text(encoding="utf-8")

        self.assertIn("/api/wca/backtests", source)
        self.assertIn("await runBackendConfidenceBacktest(preparedOneMinuteCandles, latestSessionDate)", source)
        self.assertIn("await runBackendConfidenceBacktest(state.candles, latestSessionDate)", source)
        self.assertEqual(source.count("backtestConfidenceAggregation("), 1)


class WcaStep15BacktestModesTests(unittest.TestCase):
    def test_backtest_modes_are_labeled_and_smoke_is_not_production_validation(self) -> None:
        suite = run_wca_backtest_modes(backtest_request(candles=multi_session_candles(28)), configuration=default_wca_configuration())

        self.assertEqual(suite.smoke.label, "Daily smoke test")
        self.assertFalse(suite.smoke.production_validation)
        self.assertIn("Rolling 20 sessions", {row.label for row in suite.rolling})
        self.assertEqual(suite.full_history.label, "Full historical replay")
        self.assertEqual(suite.walk_forward.label, "Walk-forward evaluation")
        self.assertEqual(suite.holdout.label, "Untouched holdout")
        self.assertFalse(suite.holdout.production_validation)

    def test_holdout_is_excluded_from_comparison_optimization(self) -> None:
        suite = run_wca_backtest_modes(backtest_request(candles=multi_session_candles(30)), configuration=default_wca_configuration())

        self.assertIn("wca.backtest.holdout_excluded_from_optimization", suite.reason_codes)
        self.assertTrue(all(comparison.metrics["holdoutExcluded"] for comparison in suite.comparisons))

    def test_required_ab_comparisons_use_identical_dataset_and_execution_assumptions(self) -> None:
        suite = run_wca_backtest_modes(backtest_request(candles=multi_session_candles(30)), configuration=default_wca_configuration())
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


def execution_candles(count: int = 80) -> tuple[WcaCandle, ...]:
    start = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)
    price = 100.0
    candles: list[WcaCandle] = []
    for index in range(count):
        open_price = price
        close = open_price + 0.08
        candles.append(
            WcaCandle(
                timestamp=start + timedelta(minutes=index),
                open=open_price,
                high=close + 0.45,
                low=open_price - 0.45,
                close=close,
                volume=200_000,
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


def parity_event(index: int, candles: tuple[WcaCandle, ...]) -> WcaFinalizedBarEvent:
    latest = candles[-1]
    quote = WcaQuote(timestamp=latest.timestamp, bid=latest.close - 0.01, ask=latest.close + 0.01)
    snapshot = WcaMarketSnapshot(
        symbol="SPY",
        data_timestamp=latest.timestamp,
        decision_timestamp=latest.timestamp,
        candles=candles,
        quote=quote,
        source="parity_fixture",
        reason_codes=("test.completed_bar",),
    )
    return WcaFinalizedBarEvent(
        event_id=f"parity-event-{index}",
        symbol="SPY",
        finalized_candle_timestamp=latest.timestamp,
        data_manifest_hash=f"manifest-{index}",
        publication_timestamp=latest.timestamp + timedelta(seconds=1),
        source="test",
        snapshot=snapshot,
    )


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
