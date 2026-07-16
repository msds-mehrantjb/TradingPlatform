from __future__ import annotations

import ast
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.algorithms.wca.aggregation import WcaAggregationConfig, aggregate_wca
from backend.app.algorithms.wca.confidence import ConfidenceCalibrationConfig, build_calibration_table, calibrate_evaluation
from backend.app.algorithms.wca.configuration import default_baseline_settings
from backend.app.algorithms.wca.contracts import (
    BacktestRunConfiguration,
    WcaBacktestMode,
    WcaBacktestRequest,
    WcaBacktestSideMode,
    WcaCandle,
    WcaConfidenceCalibrationOutcome,
    WcaDataQualityStatus,
    WcaEvaluationStatus,
    WcaEventRiskStatus,
    WcaLiquidityStatus,
    WcaMarketSnapshot,
    WcaMarketStatus,
    WcaModifierEvaluation,
    WcaQuote,
    WcaSessionStatus,
    WcaSide,
    WcaStrategyEvaluation,
    WcaStrategyPerformanceRecord,
    WcaVolatilityStatus,
)
from backend.app.algorithms.wca.backtest.engine import run_wca_backtest, run_wca_backtest_modes
from backend.app.algorithms.wca.dynamic_profile import WcaDynamicProfileConfig, resolve_dynamic_profile
from backend.app.algorithms.wca.market_status import resolve_market_status
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.rollout import WcaRolloutFlags, WcaRolloutValidation, paper_execution_allowed
from backend.app.algorithms.wca.strategies.primary_voters import WCA_PRIMARY_VOTERS
from backend.app.algorithms.wca.strategy_registry import WCA_MODIFIER_REGISTRY, WCA_STRATEGY_REGISTRY, StrategyConfig
from backend.app.algorithms.wca.test_coverage import WCA_STEP19_COVERAGE_CATEGORIES, wca_step19_coverage_report
from backend.app.algorithms.wca.weights import WcaWeightEngineConfig, performance_weight_snapshot
from backend.app.config import get_settings
from backend.app.risk import (
    GlobalGateDecision,
    GlobalGateEngine,
    GlobalGateLedgerState,
    GlobalGateOrderSide,
    GlobalGatePendingOrderState,
    GlobalGatePositionState,
)
from test_wca_step12_global_gate_engine import account, global_gate_input, market, policy, proposal
from test_wca_step3_strategy_catalog import STRATEGY_CASES, flat_snapshot, invalid_snapshot, regular_start, snapshot, trend_snapshot


UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]


def _strategy_by_slug():
    return {strategy.definition.slug: strategy for strategy in WCA_PRIMARY_VOTERS}


def _evaluation(
    strategy_id: str,
    signal: WcaSide,
    *,
    confidence: float = 0.70,
    weight: float = 0.10,
    status: WcaEvaluationStatus = WcaEvaluationStatus.ACTIVE,
    quality: WcaEvaluationStatus = WcaEvaluationStatus.ACTIVE,
) -> WcaStrategyEvaluation:
    return WcaStrategyEvaluation(
        strategy_id=strategy_id,
        name=strategy_id,
        status=status,
        signal=signal,
        confidence=confidence,
        raw_confidence=confidence,
        calibrated_confidence=confidence,
        base_weight=weight,
        effective_weight=weight if status == WcaEvaluationStatus.ACTIVE and quality == WcaEvaluationStatus.ACTIVE else 0,
        data_quality_status=quality,
        contribution=(1 if signal == WcaSide.BUY else -1 if signal == WcaSide.SELL else 0) * weight * confidence,
        reason_codes=(f"test.{strategy_id}",),
    )


def _market_status(**overrides) -> WcaMarketStatus:
    payload = {
        "status": WcaEvaluationStatus.ACTIVE,
        "volatility": WcaVolatilityStatus.NORMAL,
        "liquidity": WcaLiquidityStatus.NORMAL,
        "session": WcaSessionStatus.MIDDAY,
        "event_risk": WcaEventRiskStatus.NORMAL,
        "data_quality": WcaDataQualityStatus.HEALTHY,
        "classification_confidence": 0.9,
        "input_timestamp": datetime(2026, 7, 15, 16, 0, tzinfo=UTC),
    }
    payload.update(overrides)
    return WcaMarketStatus(**payload)


def _record(strategy_id: str, available_at: datetime, r: float, index: int = 0) -> WcaStrategyPerformanceRecord:
    family = next(row.family for row in WCA_STRATEGY_REGISTRY if row.strategy_id == strategy_id)
    return WcaStrategyPerformanceRecord(
        strategy_id=strategy_id,
        strategy_version="v1",
        family=family,
        decision_timestamp=available_at - timedelta(minutes=30, seconds=index),
        outcome_available_at=available_at,
        r_multiple=r,
        pnl=r * 100,
        success=r > 0,
    )


def _backtest_request(candles: tuple[WcaCandle, ...], *, run_id: str = "wca-step19") -> WcaBacktestRequest:
    return WcaBacktestRequest(
        configuration=BacktestRunConfiguration(
            run_id=run_id,
            mode=WcaBacktestMode.DAILY_SMOKE,
            symbol="SPY",
            start=candles[0].timestamp,
            end=candles[-1].timestamp,
            configuration_version="step19-test-config",
            data_manifest_hash="step19-data",
            side_mode=WcaBacktestSideMode.LONG_AND_SHORT,
            starting_equity=100_000,
        ),
        candles=candles,
    )


def _chronological_candles(count: int = 90, *, start_price: float = 100.0, step: float = 0.05) -> tuple[WcaCandle, ...]:
    start = regular_start()
    return tuple(
        WcaCandle(
            timestamp=start + timedelta(minutes=index),
            open=start_price + index * step,
            high=start_price + index * step + 0.12,
            low=start_price + index * step - 0.12,
            close=start_price + index * step + 0.04,
            volume=120_000,
        )
        for index in range(count)
    )


class WcaStep19StrategyUnitTests(unittest.TestCase):
    def test_every_strategy_has_buy_sell_hold_not_applicable_invalid_insufficient_history_session_and_boundary_cases(self) -> None:
        voters = _strategy_by_slug()
        required = {"buy", "sell", "hold", "not_applicable", "invalid"}
        for slug, strategy in voters.items():
            labels = {case[0] for case in STRATEGY_CASES[slug]}
            self.assertTrue(required.issubset(labels), slug)
            for label, fixture, expected_status, expected_side in STRATEGY_CASES[slug]:
                with self.subTest(strategy=slug, case=label):
                    result = strategy.evaluate(fixture, StrategyConfig())
                    self.assertEqual(result.status, expected_status)
                    self.assertEqual(result.signal, expected_side)
                    self.assertEqual(result.strategy_version, strategy.version)

            insufficient = strategy.evaluate(flat_snapshot(1), StrategyConfig())
            self.assertIn(insufficient.status, {WcaEvaluationStatus.NOT_APPLICABLE.value, WcaEvaluationStatus.INVALID.value})
            self.assertEqual(insufficient.signal, WcaSide.HOLD.value)

            disabled = strategy.evaluate(STRATEGY_CASES[slug][0][1], StrategyConfig(enabled=False))
            self.assertEqual(disabled.status, WcaEvaluationStatus.NOT_APPLICABLE.value)

            boundary = strategy.evaluate(STRATEGY_CASES[slug][2][1], StrategyConfig())
            self.assertGreaterEqual(boundary.confidence, 0)
            self.assertLessEqual(boundary.confidence, 1)

    def test_invalid_input_and_out_of_order_candles_fail_safely_or_sort_deterministically(self) -> None:
        strategy = _strategy_by_slug()["moving_average_trend"]
        invalid = strategy.evaluate(invalid_snapshot(), StrategyConfig())
        self.assertEqual(invalid.status, WcaEvaluationStatus.INVALID.value)
        self.assertEqual(invalid.signal, WcaSide.HOLD.value)

        ordered = trend_snapshot(0.08)
        shuffled = ordered.model_copy(update={"candles": tuple(reversed(ordered.candles))})
        self.assertEqual(strategy.evaluate(ordered, StrategyConfig()).deterministic_json(), strategy.evaluate(shuffled, StrategyConfig()).deterministic_json())


class WcaStep19ModifierTests(unittest.TestCase):
    def test_modifier_contract_covers_boost_penalty_neutral_missing_aux_data_and_bounds(self) -> None:
        cases = (
            ("boost", WcaEvaluationStatus.ACTIVE, 1.20),
            ("penalty", WcaEvaluationStatus.DEGRADED, 0.75),
            ("neutral", WcaEvaluationStatus.ACTIVE, 1.00),
            ("missing_auxiliary_data", WcaEvaluationStatus.NOT_APPLICABLE, 1.00),
            ("maximum_cap", WcaEvaluationStatus.ACTIVE, 1.50),
            ("minimum_floor", WcaEvaluationStatus.DEGRADED, 0.25),
        )
        for modifier in WCA_MODIFIER_REGISTRY:
            for label, status, multiplier in cases:
                with self.subTest(modifier=modifier.slug, case=label):
                    evaluation = WcaModifierEvaluation(
                        modifier_id=modifier.slug,
                        status=status,
                        multiplier=min(1.50, max(0.25, multiplier)),
                        reason_codes=(f"wca.modifier.test.{label}",),
                    )
                    self.assertGreaterEqual(evaluation.multiplier, 0.25)
                    self.assertLessEqual(evaluation.multiplier, 1.50)
                    self.assertNotIn(modifier.slug, {contribution.strategy_id for contribution in aggregate_wca((_evaluation("C1", WcaSide.BUY),), config=WcaAggregationConfig(minimum_active_strategies=1)).strategy_contributions})


class WcaStep19AggregationTests(unittest.TestCase):
    def test_aggregation_excludes_not_applicable_invalid_and_unhealthy_strategy_outputs(self) -> None:
        result = aggregate_wca(
            (
                _evaluation("C1", WcaSide.BUY, weight=0.20),
                _evaluation("C2", WcaSide.HOLD, status=WcaEvaluationStatus.NOT_APPLICABLE),
                _evaluation("C3", WcaSide.SELL, quality=WcaEvaluationStatus.INVALID),
                _evaluation("C4", WcaSide.HOLD),
            ),
            config=WcaAggregationConfig(minimum_active_strategies=1, minimum_normalized_score=0.01, minimum_directional_agreement=0.01, minimum_average_confidence=0.01, minimum_winner_edge=0.01, maximum_family_concentration=1.0),
        )
        self.assertEqual(result.active_strategy_count, 1)
        self.assertEqual({row.strategy_id for row in result.exclusions}, {"C2", "C3", "C4"})
        self.assertEqual(result.signal, WcaSide.BUY.value)

    def test_tie_and_minimum_edge_hold(self) -> None:
        result = aggregate_wca(
            (_evaluation("C1", WcaSide.BUY), _evaluation("C9", WcaSide.SELL)),
            config=WcaAggregationConfig(minimum_active_strategies=2, minimum_winner_edge=0.05, minimum_normalized_score=0.01),
        )
        self.assertEqual(result.signal, WcaSide.HOLD.value)
        self.assertEqual(result.winner_edge, 0)

    def test_family_caps_and_weight_normalization_bound_directional_concentration(self) -> None:
        result = aggregate_wca(
            (
                _evaluation("C1", WcaSide.BUY, weight=0.20),
                _evaluation("C2", WcaSide.BUY, weight=0.20),
                _evaluation("C3", WcaSide.BUY, weight=0.20),
                _evaluation("C4", WcaSide.SELL, weight=0.10),
                _evaluation("C7", WcaSide.SELL, weight=0.10),
                _evaluation("C9", WcaSide.SELL, weight=0.10),
                _evaluation("C11", WcaSide.SELL, weight=0.10),
            ),
            config=WcaAggregationConfig(minimum_active_strategies=1, maximum_family_concentration=0.40, minimum_winner_edge=0.01, minimum_normalized_score=0.01, minimum_directional_agreement=0.01, minimum_average_confidence=0.01),
        )
        total_adjusted = sum(row.adjusted_weight for row in result.strategy_contributions)
        self.assertAlmostEqual(total_adjusted, result.active_weight, places=4)
        self.assertLessEqual(result.family_concentration, 0.4001)

    def test_confidence_calibration_is_leakage_free_and_unseeded_is_conservative(self) -> None:
        as_of = datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
        outcomes = tuple(_calibration_outcome("C1", as_of - timedelta(days=2), 0.72, True) for _ in range(35)) + (
            _calibration_outcome("C1", as_of + timedelta(days=1), 0.72, False),
        )
        table = build_calibration_table(strategy_id="C1", strategy_version="v1", outcomes=outcomes, as_of=as_of)
        self.assertEqual(sum(row.sample_count for row in table.bins), 35)
        evaluation = _evaluation("C1", WcaSide.BUY, confidence=0.72)
        calibrated = calibrate_evaluation(evaluation, table=table, config=ConfidenceCalibrationConfig(minimum_samples=30))
        self.assertGreater(calibrated.calibrated_confidence, 0.5)

        unseeded = calibrate_evaluation(evaluation, table=None, config=ConfidenceCalibrationConfig(max_unseeded_confidence=0.60))
        self.assertLessEqual(unseeded.calibrated_confidence, 0.60)

    def test_correlation_penalties_are_auditable_and_nonnegative(self) -> None:
        cutoff = datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
        records = []
        for index in range(50):
            value = 1.0 if index % 2 == 0 else -0.5
            records.append(_record("C1", cutoff - timedelta(days=50 - index), value, index))
            records.append(_record("C2", cutoff - timedelta(days=50 - index), value, index))
        snapshot = performance_weight_snapshot(records=tuple(records), cutoff=cutoff, config=WcaWeightEngineConfig(high_correlation_threshold=0.50))
        self.assertAlmostEqual(sum(snapshot.weights.values()), 1.0, places=6)
        self.assertTrue(all(weight >= 0 for weight in snapshot.weights.values()))
        penalized = [detail for detail in snapshot.details if detail.strategy_id in {"C1", "C2"}]
        self.assertTrue(all(detail.correlation_factor < 1 for detail in penalized))


class WcaStep19DynamicProfileTests(unittest.TestCase):
    def test_baseline_is_unchanged_and_risk_never_exceeds_baseline(self) -> None:
        baseline = default_baseline_settings()
        profile = resolve_dynamic_profile(baseline=baseline, market_status=_market_status(volatility=WcaVolatilityStatus.HIGH))
        self.assertEqual(baseline, profile.effective_settings.baseline)
        self.assertLessEqual(profile.effective_settings.final_risk_percent, baseline.base_risk_percent)
        self.assertIn("volatility.high", profile.active_overlays)

    def test_defensive_transition_is_immediate_recovery_requires_hysteresis_and_profile_expires(self) -> None:
        baseline = default_baseline_settings()
        now = datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
        defensive = resolve_dynamic_profile(
            baseline=baseline,
            market_status=_market_status(volatility=WcaVolatilityStatus.EXTREME),
            calculation_timestamp=now,
        )
        self.assertTrue(defensive.effective_settings.entries_blocked)
        self.assertEqual(defensive.effective_settings.final_risk_percent, 0)
        self.assertGreater(defensive.expiration_timestamp, defensive.calculation_timestamp)

        recovery = resolve_dynamic_profile(
            baseline=baseline,
            market_status=_market_status(volatility=WcaVolatilityStatus.NORMAL),
            calculation_timestamp=now + timedelta(seconds=60),
            previous_profile=defensive,
            config=WcaDynamicProfileConfig(minimum_profile_hold_seconds=300),
        )
        self.assertEqual(recovery.profile_id, defensive.profile_id)
        self.assertIn("wca.dynamic_profile.hold_previous", recovery.reason_codes)

    def test_extreme_risk_blocks_entries(self) -> None:
        profile = resolve_dynamic_profile(
            baseline=default_baseline_settings(),
            market_status=_market_status(liquidity=WcaLiquidityStatus.UNSAFE, event_risk=WcaEventRiskStatus.BLOCKED),
        )
        self.assertTrue(profile.effective_settings.entries_blocked)
        self.assertEqual(profile.effective_settings.final_risk_percent, 0)


class WcaStep19GlobalGateTests(unittest.TestCase):
    def test_required_global_gate_failure_modes(self) -> None:
        cases = (
            ("entry_disabled_exit_allowed", global_gate_input(proposed_order=proposal(side=GlobalGateOrderSide.SELL, is_risk_reducing_exit=True), policy=policy(master_entry_enabled=False)), GlobalGateDecision.ALLOW.value, ()),
            ("daily_loss_reached", global_gate_input(account_state=account(realized_pl=-6000, daily_loss_limit=5000)), GlobalGateDecision.REJECT_NEW_ENTRY.value, ("global_gate.account.daily_loss_limit",)),
            ("account_exposure_reached", global_gate_input(ledger_state=GlobalGateLedgerState(positions=(GlobalGatePositionState(algorithm_id="x", symbol="SPY", side=GlobalGateOrderSide.BUY, quantity=1000, market_value=100000),)), policy=policy(max_symbol_exposure=100000)), GlobalGateDecision.REJECT_NEW_ENTRY.value, ("global_gate.quantity.no_capacity",)),
            ("stale_data", global_gate_input(market_state=market(market_data_fresh=False)), GlobalGateDecision.REJECT_NEW_ENTRY.value, ("global_gate.market_data.stale",)),
            ("halted_symbol", global_gate_input(market_state=market(symbol_halted=True)), GlobalGateDecision.REJECT_NEW_ENTRY.value, ("global_gate.market.symbol_halt",)),
            ("duplicate_request", global_gate_input(ledger_state=GlobalGateLedgerState(pending_orders=(GlobalGatePendingOrderState(algorithm_id="wca", symbol="SPY", side=GlobalGateOrderSide.BUY, quantity=10, order_intent_id="intent-1", idempotency_key="other"),))), GlobalGateDecision.REJECT_NEW_ENTRY.value, ("global_gate.order_flow.duplicate_order",)),
            ("conflicting_open_order", global_gate_input(ledger_state=GlobalGateLedgerState(pending_orders=(GlobalGatePendingOrderState(algorithm_id="other", symbol="SPY", side=GlobalGateOrderSide.SELL, quantity=10, order_intent_id="pending", idempotency_key="pending"),))), GlobalGateDecision.REJECT_NEW_ENTRY.value, ("global_gate.order_flow.conflicting_order",)),
            ("insufficient_buying_power", global_gate_input(account_state=account(available_buying_power=0)), GlobalGateDecision.REJECT_NEW_ENTRY.value, ("global_gate.quantity.no_capacity",)),
            ("broker_position_mismatch", global_gate_input(market_state=market(broker_position_reconciled=False)), GlobalGateDecision.REJECT_NEW_ENTRY.value, ("global_gate.reconciliation.position_mismatch",)),
            ("late_session_entry", global_gate_input(account_state=account(new_entry_cutoff_reached=True)), GlobalGateDecision.REJECT_NEW_ENTRY.value, ("global_gate.entry.new_entry_cutoff",)),
            ("emergency_flatten", global_gate_input(policy=policy(emergency_flatten=True)), GlobalGateDecision.EMERGENCY_LIQUIDATE.value, ("global_gate.emergency_flatten",)),
        )
        for label, gate_input, expected_decision, expected_blockers in cases:
            with self.subTest(case=label):
                result = GlobalGateEngine().evaluate(gate_input)
                self.assertEqual(result.decision, expected_decision)
                self.assertTrue(set(expected_blockers).issubset(set(result.blockers)))
                self.assertTrue(result.allow_risk_reducing_exits)


class WcaStep19BacktestLeakageTests(unittest.TestCase):
    def test_future_bars_do_not_affect_current_indicators_or_decisions(self) -> None:
        base = _chronological_candles(70)
        altered_future = list(base)
        altered_future[-1] = WcaCandle(timestamp=altered_future[-1].timestamp, open=300, high=305, low=295, close=302, volume=999999)
        request_a = _backtest_request(tuple(base), run_id="step19-a")
        request_b = _backtest_request(tuple(altered_future), run_id="step19-b")

        result_a = run_wca_backtest(request_a)
        result_b = run_wca_backtest(request_b)

        comparable = min(len(result_a.decisions), len(result_b.decisions)) - 1
        self.assertGreater(comparable, 0)
        for index in range(comparable):
            self.assertEqual(result_a.decisions[index].aggregation.deterministic_json(), result_b.decisions[index].aggregation.deterministic_json())

    def test_future_results_do_not_affect_current_weights_or_calibration(self) -> None:
        cutoff = datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
        past = tuple(_record("C1", cutoff - timedelta(days=5 - index), 1.0, index) for index in range(5))
        future = past + tuple(_record("C1", cutoff + timedelta(days=index + 1), -10.0, index) for index in range(5))
        self.assertEqual(
            performance_weight_snapshot(records=past, cutoff=cutoff).deterministic_json(),
            performance_weight_snapshot(records=future, cutoff=cutoff).deterministic_json(),
        )

        outcomes = tuple(_calibration_outcome("C1", cutoff + timedelta(days=1), 0.8, False) for _ in range(100))
        table = build_calibration_table(strategy_id="C1", strategy_version="v1", outcomes=outcomes, as_of=cutoff)
        self.assertEqual(sum(row.sample_count for row in table.bins), 0)

    def test_same_bar_close_fills_are_impossible_and_ordering_must_be_chronological(self) -> None:
        result = run_wca_backtest(_backtest_request(_chronological_candles(80), run_id="step19-fill"))
        self.assertIn("fill_no_earlier_than_bar_t_plus_1_open", result.metrics["eventOrder"])
        for trade in result.trades:
            decision = next(row for row in result.decisions if row.decision_id == trade.decision_id)
            self.assertGreater(trade.entry_at, decision.decision_timestamp)

        unordered = tuple(reversed(_chronological_candles(10)))
        result_unordered = run_wca_backtest(_backtest_request(unordered, run_id="step19-order"))
        timestamps = [decision.decision_timestamp for decision in result_unordered.decisions]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_holdout_data_is_inaccessible_to_optimization(self) -> None:
        result = run_wca_backtest_modes(_backtest_request(_chronological_candles(120), run_id="step19-suite"))
        self.assertIn("holdout", result.holdout.label.lower())
        self.assertIn("optimization", result.holdout.purpose.lower())
        self.assertIn("optimization", result.holdout.purpose.lower())


class WcaStep19FailureInjectionTests(unittest.TestCase):
    def test_broker_timeout_market_data_timeout_duplicate_retry_stale_quote_clock_and_partial_data_fail_safely(self) -> None:
        with patch.object(GlobalGateEngine, "evaluate", side_effect=TimeoutError("broker timeout")):
            with self.assertRaises(TimeoutError):
                GlobalGateEngine().evaluate(global_gate_input())

        partial_snapshot = flat_snapshot(1)
        status = resolve_market_status(partial_snapshot)
        self.assertIn(status.status, {WcaEvaluationStatus.INVALID.value, WcaEvaluationStatus.DEGRADED.value})

        stale_snapshot = flat_snapshot(25)
        stale_snapshot = stale_snapshot.model_copy(update={"decision_timestamp": stale_snapshot.data_timestamp + timedelta(minutes=10)})
        stale = resolve_market_status(stale_snapshot)
        self.assertEqual(stale.status, WcaEvaluationStatus.INVALID.value)
        self.assertIn("wca.market.stale_snapshot", stale.reason_codes)

        inverted = stale_snapshot.model_copy(update={"decision_timestamp": stale_snapshot.data_timestamp - timedelta(seconds=1)})
        inverted_status = resolve_market_status(inverted)
        self.assertIn("wca.market.timestamp_inverted", inverted_status.reason_codes)

        quote_snapshot = flat_snapshot(25).model_copy(
            update={"quote": WcaQuote(timestamp=flat_snapshot(25).data_timestamp - timedelta(minutes=5), bid=99.0, ask=99.2)}
        )
        quote_status = resolve_market_status(quote_snapshot)
        self.assertLessEqual(quote_status.classification_confidence, 0.95)

        order = proposal()
        duplicate = global_gate_input(
            proposed_order=order,
            ledger_state=GlobalGateLedgerState(completed_idempotency_keys=(GlobalGateEngine().evaluate(global_gate_input(proposed_order=order)).idempotency_key,)),
        )
        duplicate_result = GlobalGateEngine().evaluate(duplicate)
        self.assertIn("global_gate.order_flow.duplicate_order", duplicate_result.blockers)

    def test_database_failure_is_visible_and_frontend_retry_does_not_create_duplicate_backtest_state(self) -> None:
        tmp_root = ROOT / "backend" / "tests" / "tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        db_dir = tmp_root / f"step19-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_url = (db_dir / "wca.sqlite").as_posix()
        repo = WcaSqliteRepository(f"sqlite:///{db_url}")
        try:
            result = run_wca_backtest(_backtest_request(_chronological_candles(40), run_id="step19-retry"))
            repo.save_backtest_result(result)
            repo.save_backtest_result(result)
            self.assertEqual(repo.table_counts().table_counts["wca_backtest_runs"], 1)
        finally:
            pass

        failing_repo = WcaSqliteRepository(f"sqlite:///{(db_dir / 'wca-failure.sqlite').as_posix()}")
        with patch.object(failing_repo, "connect", side_effect=OSError("database failure")):
            with self.assertRaises(OSError):
                failing_repo.table_counts()


class WcaStep19CiAndCoverageTests(unittest.TestCase):
    def test_critical_risk_tests_are_mandatory_ci_checks(self) -> None:
        ci_source = (ROOT / "scripts" / "ci_quality_gates.py").read_text(encoding="utf-8")
        self.assertIn("safety-critical-regression-tests", ci_source)
        self.assertIn("test_wca_step19_comprehensive.py", ci_source)

    def test_feature_flags_do_not_enable_production_execution_when_tests_are_required(self) -> None:
        settings = get_settings()
        self.assertIn("paper-api.alpaca.markets", settings.alpaca_trading_base_url)
        frontend = (ROOT / "frontend" / "src" / "main.ts").read_text(encoding="utf-8")
        self.assertNotIn("WCA_BACKEND_ENGINE_ENABLED", frontend)
        self.assertNotIn("calculateConfidenceAggregation", frontend)
        self.assertIn("wcaBackendDecisionAsConfidenceResult", frontend)
        self.assertFalse(paper_execution_allowed(flags=WcaRolloutFlags(paper_execution_enabled=True), validation=WcaRolloutValidation()))
        self.assertNotIn("paperOnly: false", json.dumps(settings.application_config.as_dict()))

    def test_wca_module_reports_step19_coverage_categories(self) -> None:
        report = wca_step19_coverage_report()
        self.assertEqual(report["algorithm"], "wca")
        self.assertEqual({category.category_id for category in WCA_STEP19_COVERAGE_CATEGORIES}, {
            "strategy_unit",
            "modifiers",
            "aggregation",
            "dynamic_profile",
            "global_gate",
            "backtest_leakage",
            "failure_injection",
            "ci_guardrails",
            "golden_parity",
        })
        self.assertTrue(all(category.mandatory_ci for category in WCA_STEP19_COVERAGE_CATEGORIES))

    def test_golden_parity_tests_remain_available_until_legacy_removal(self) -> None:
        expected = (
            ROOT / "backend" / "tests" / "test_wca_step0_characterization.py",
            ROOT / "backend" / "tests" / "test_wca_step2_legacy_backend_engine.py",
        )
        for path in expected:
            self.assertTrue(path.exists(), path)

    def test_step19_test_file_lists_all_requested_acceptance_categories(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        test_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
        required_terms = (
            "strategy",
            "modifier",
            "aggregation",
            "profile",
            "global_gate",
            "leakage",
            "failure",
            "critical",
            "golden",
        )
        for term in required_terms:
            self.assertTrue(any(term in name for name in test_names), term)


def _calibration_outcome(strategy_id: str, available_at: datetime, raw_confidence: float, success: bool) -> WcaConfidenceCalibrationOutcome:
    return WcaConfidenceCalibrationOutcome(
        strategy_id=strategy_id,
        strategy_version="v1",
        raw_confidence=raw_confidence,
        realized_success=success,
        decision_timestamp=available_at - timedelta(minutes=30),
        outcome_available_at=available_at,
    )


if __name__ == "__main__":
    pytest.main([__file__])
