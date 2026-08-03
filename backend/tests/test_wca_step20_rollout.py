from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend.app.algorithms.wca.configuration import WcaConfiguration, default_wca_configuration
from backend.app.algorithms.wca.rollout import (
    GLOBAL_GATE_ENGINE_ENABLED,
    WCA_BACKEND_BACKTEST_ENABLED,
    WCA_BACKEND_ENGINE_ENABLED,
    WCA_CORRECTED_STRATEGY_CATALOG_ENABLED,
    WCA_DYNAMIC_PROFILE_ENABLED,
    WCA_DYNAMIC_WEIGHTS_ENABLED,
    WCA_PAPER_EXECUTION_ENABLED,
    WCA_REQUIRED_ROLLOUT_EVIDENCE,
    WCA_ROLLBACK_STATE_KEY,
    WCA_ROLLOUT_PHASES,
    WCA_ROLLOUT_STAGES,
    WCA_ROLLOUT_STATE_KEY,
    WCA_SHADOW_COMPARISON_FIELDS,
    WcaLimitedAutomaticPaperCaps,
    WcaRolloutEvidence,
    WcaRolloutEvidenceThresholds,
    WcaRolloutFlags,
    WcaRolloutValidation,
    compare_shadow_results,
    critical_failure_action,
    evaluate_wca_rollout_phase,
    evaluate_wca_rollout_stage,
    highest_wca_rollout_stage,
    limited_automatic_paper_allowed,
    manual_paper_allowed,
    paper_execution_allowed,
    paper_recommendation_allowed,
    record_valid_wca_rollout_state,
    rollback_configuration,
    rollback_wca_rollout,
    wca_rollout_feature_flags,
    wca_rollout_status,
)
from backend.app.algorithms.wca.service import WcaService
from backend.app.algorithms.wca.test_coverage import (
    WCA_TEST_SUITE_COVERAGE_AREA_IDS,
    WCA_TEST_SUITE_COVERAGE_INVENTORY,
    WCA_TEST_SUITE_PASS_REQUIRES_EXECUTION,
    WCA_VALIDATION_ROLLOUT_FILE_INVENTORY,
    WCA_VALIDATION_ROLLOUT_FILE_NAMES,
    wca_validation_rollout_inventory_report,
)
from backend.app.config import ApplicationConfig


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


class WcaStep20RolloutTest(unittest.TestCase):
    def test_validation_and_rollout_file_inventory_is_dedicated(self) -> None:
        self.assertEqual(
            WCA_VALIDATION_ROLLOUT_FILE_NAMES,
            {"shadow_comparison.py", "paper_stability.py", "rollout.py", "final_acceptance.py", "test_coverage.py"},
        )
        for file_name in WCA_VALIDATION_ROLLOUT_FILE_NAMES:
            self.assertTrue((ROOT / "backend" / "app" / "algorithms" / "wca" / file_name).is_file(), file_name)

    def test_dedicated_wca_test_suite_inventory_covers_requested_areas_without_claiming_passage(self) -> None:
        self.assertIn("rollout", WCA_TEST_SUITE_COVERAGE_AREA_IDS)
        self.assertIn("paper_execution", WCA_TEST_SUITE_COVERAGE_AREA_IDS)
        self.assertIn("reconciliation", WCA_TEST_SUITE_COVERAGE_AREA_IDS)
        self.assertIn("stability", WCA_TEST_SUITE_COVERAGE_AREA_IDS)
        self.assertTrue(WCA_TEST_SUITE_PASS_REQUIRES_EXECUTION)
        for area in WCA_TEST_SUITE_COVERAGE_INVENTORY:
            self.assertTrue((ROOT / "backend" / "tests" / area.test_file).is_file(), area.area_id)

        report = wca_validation_rollout_inventory_report()
        self.assertFalse(report["testPresenceProvesPassing"])
        self.assertTrue(report["passingRequiresPytestExecution"])

    def test_rollout_stages_match_requested_ladder(self) -> None:
        self.assertEqual(
            WCA_ROLLOUT_STAGES,
            (
                "DISABLED",
                "HISTORICAL_REPLAY",
                "SHADOW",
                "PAPER_RECOMMENDATION",
                "MANUAL_PAPER",
                "LIMITED_AUTOMATIC_PAPER",
                "AUTOMATIC_PAPER",
            ),
        )
        self.assertEqual(WCA_ROLLOUT_PHASES, WCA_ROLLOUT_STAGES)

    def test_feature_flags_default_to_active_v2_with_automatic_paper_disabled(self) -> None:
        flags = wca_rollout_feature_flags({})

        self.assertTrue(flags.backend_engine_enabled)
        self.assertTrue(flags.corrected_strategy_catalog_enabled)
        self.assertTrue(flags.dynamic_weights_enabled)
        self.assertTrue(flags.dynamic_profile_enabled)
        self.assertTrue(flags.global_gate_engine_enabled)
        self.assertTrue(flags.backend_backtest_enabled)
        self.assertFalse(flags.paper_execution_enabled)
        self.assertFalse(paper_execution_allowed(flags=flags, evidence=complete_evidence()))

    def test_feature_flags_parse_environment_independently(self) -> None:
        flags = wca_rollout_feature_flags(
            {
                WCA_BACKEND_ENGINE_ENABLED: "true",
                WCA_CORRECTED_STRATEGY_CATALOG_ENABLED: "true",
                WCA_DYNAMIC_WEIGHTS_ENABLED: "false",
                WCA_DYNAMIC_PROFILE_ENABLED: "true",
                GLOBAL_GATE_ENGINE_ENABLED: "true",
                WCA_BACKEND_BACKTEST_ENABLED: "true",
                WCA_PAPER_EXECUTION_ENABLED: "false",
            }
        )

        self.assertFalse(flags.dynamic_weights_enabled)
        self.assertFalse(flags.paper_execution_enabled)

    def test_promotion_requires_persisted_evidence_not_hard_coded_validation_pass(self) -> None:
        flags = WcaRolloutFlags()
        validation_only = WcaRolloutValidation(
            full_history_backtest_passed=True,
            walk_forward_passed=True,
            untouched_holdout_passed=True,
            paper_trading_stable=True,
            tests_passed=True,
        )
        blocked = evaluate_wca_rollout_stage("HISTORICAL_REPLAY", flags=flags, evidence=WcaRolloutEvidence.from_validation(validation_only))

        self.assertFalse(blocked.enabled)
        self.assertIn("wca.rollout.missing_persisted_evidence.successful_restart_recovery", blocked.reason_codes)

    def test_historical_shadow_recommendation_and_manual_paper_use_incremental_evidence(self) -> None:
        flags = WcaRolloutFlags()
        historical = historical_replay_evidence()
        shadow = shadow_evidence()
        recommendation = recommendation_evidence()
        manual = manual_paper_evidence()

        self.assertTrue(evaluate_wca_rollout_stage("HISTORICAL_REPLAY", flags=flags, evidence=historical).enabled)
        self.assertFalse(evaluate_wca_rollout_stage("SHADOW", flags=flags, evidence=historical).enabled)
        self.assertTrue(evaluate_wca_rollout_stage("SHADOW", flags=flags, evidence=shadow).enabled)
        self.assertTrue(paper_recommendation_allowed(flags=flags, evidence=recommendation))
        self.assertTrue(manual_paper_allowed(flags=flags, evidence=manual))
        self.assertFalse(paper_execution_allowed(flags=flags, evidence=manual))

    def test_legacy_phase_aliases_route_to_new_stages_without_enabling_submission(self) -> None:
        status = evaluate_wca_rollout_phase("legacy_parity", flags=WcaRolloutFlags(), validation=fully_validated_rollout())
        comparison = compare_shadow_results(shadow_payload(quantity=10), shadow_payload(quantity=10.00001))

        self.assertEqual(status.phase, "HISTORICAL_REPLAY")
        self.assertFalse(comparison.submission_allowed)
        self.assertEqual(comparison.compared_fields, WCA_SHADOW_COMPARISON_FIELDS)

    def test_shadow_comparison_reports_field_level_mismatches(self) -> None:
        comparison = compare_shadow_results(shadow_payload(decision="BUY"), shadow_payload(decision="SELL"))

        self.assertFalse(comparison.within_tolerance)
        self.assertEqual(comparison.mismatched_fields, ("decision",))
        self.assertIn("wca.rollout.shadow_comparison.no_submission", comparison.reason_codes)

    def test_limited_automatic_paper_requires_all_minimum_persisted_evidence_and_flag(self) -> None:
        flags_off = WcaRolloutFlags(paper_execution_enabled=False)
        flags_on = WcaRolloutFlags(paper_execution_enabled=True, global_gate_engine_enabled=True)
        evidence = complete_evidence()

        blocked = evaluate_wca_rollout_stage("LIMITED_AUTOMATIC_PAPER", flags=flags_off, evidence=evidence)
        enabled = evaluate_wca_rollout_stage("LIMITED_AUTOMATIC_PAPER", flags=flags_on, evidence=evidence)

        self.assertFalse(blocked.enabled)
        self.assertIn("wca.rollout.automatic_paper_flag_disabled", blocked.reason_codes)
        self.assertTrue(enabled.enabled)
        self.assertTrue(limited_automatic_paper_allowed(flags=flags_on, evidence=evidence))
        self.assertTrue(paper_execution_allowed(flags=flags_on, evidence=evidence))

    def test_limited_automatic_paper_caps_are_conservative_and_explicit(self) -> None:
        caps = WcaLimitedAutomaticPaperCaps()

        self.assertEqual(caps.symbols, ("SPY",))
        self.assertLessEqual(caps.max_quantity, 10)
        self.assertLessEqual(caps.max_daily_trades, 3)
        self.assertLessEqual(caps.max_daily_loss_dollars, 100)
        self.assertTrue(caps.session_windows)
        self.assertTrue(set(caps.allowed_strategies).issubset({f"C{index}" for index in range(1, 12)}))

    def test_missing_market_session_latency_or_slippage_evidence_blocks_automation(self) -> None:
        evidence = complete_evidence(
            persisted_evidence_ids=WCA_REQUIRED_ROLLOUT_EVIDENCE - {"closing_session_evidence"},
            session_periods=("opening", "midday"),
            max_decision_latency_seconds=10,
            average_realised_slippage_per_share=0.10,
        )
        status = evaluate_wca_rollout_stage(
            "LIMITED_AUTOMATIC_PAPER",
            flags=WcaRolloutFlags(paper_execution_enabled=True),
            evidence=evidence,
            thresholds=WcaRolloutEvidenceThresholds(max_decision_latency_seconds=2, max_average_realised_slippage_per_share=0.05),
        )

        self.assertFalse(status.enabled)
        self.assertIn("wca.rollout.missing_persisted_evidence.closing_session_evidence", status.reason_codes)
        self.assertIn("wca.rollout.session_periods_not_validated", status.reason_codes)
        self.assertIn("wca.rollout.decision_latency_unacceptable", status.reason_codes)
        self.assertIn("wca.rollout.realised_slippage_unacceptable", status.reason_codes)

    def test_critical_failure_stops_entries_preserves_exits_and_requires_recovery(self) -> None:
        action = critical_failure_action("broker-timeout")
        evidence = complete_evidence(critical_failure_open=True, reconciliation_after_failure_passed=False, healthy_state_validation_passed=False)
        status = evaluate_wca_rollout_stage("PAPER_RECOMMENDATION", flags=WcaRolloutFlags(), evidence=evidence)

        self.assertTrue(action.stop_new_entries)
        self.assertTrue(action.continue_protective_exits)
        self.assertTrue(action.preserve_evidence)
        self.assertTrue(action.circuit_breaker_open)
        self.assertIn("wca.rollout.critical_failure_circuit_breaker_open", status.reason_codes)
        self.assertIn("wca.rollout.reconciliation_required_after_failure", status.reason_codes)
        self.assertIn("wca.rollout.healthy_state_validation_required_after_failure", status.reason_codes)

    def test_real_money_execution_is_outside_rollout_even_when_automatic_paper_is_enabled(self) -> None:
        status = wca_rollout_status(flags=WcaRolloutFlags(paper_execution_enabled=True), evidence=complete_evidence())

        self.assertEqual(status["current_stage"], "AUTOMATIC_PAPER")
        self.assertFalse(status["live_trading_allowed"])
        self.assertIn("limited_automatic_paper_caps", status)

    def test_rollout_status_and_service_status_report_evidence_based_stages(self) -> None:
        status = wca_rollout_status(flags=WcaRolloutFlags(), evidence=WcaRolloutEvidence())
        service_status = WcaService(repository=MemoryWcaRepository()).status()

        self.assertEqual(status["current_stage"], "DISABLED")
        self.assertEqual(len(status["stages"]), len(WCA_ROLLOUT_STAGES))
        self.assertIn("rollout", service_status)
        self.assertIn(service_status["status"], {"OFF", "STARTING", "BLOCKED", "PROTECTIVE_ONLY", "LIMITED_AUTOMATIC_PAPER_READY", "AUTOMATIC_PAPER_READY", "CRITICAL"})
        self.assertNotEqual(service_status["status"], "ready")
        self.assertFalse(service_status["rollout"]["paper_execution_allowed"])
        self.assertFalse(service_status["rollout"]["live_trading_allowed"])
        self.assertIn("finalAcceptance", service_status)

    def test_rollback_single_configuration_restores_safe_posture_without_deleting_history(self) -> None:
        rollback = rollback_configuration()

        self.assertFalse(rollback[WCA_BACKEND_ENGINE_ENABLED])
        self.assertFalse(rollback["automated_paper_submission"])
        self.assertTrue(rollback["new_entries_stopped"])
        self.assertTrue(rollback["protective_exits_continue"])
        self.assertTrue(rollback["circuit_breaker_open"])
        self.assertFalse(rollback["delete_historical_records"])

    def test_rollback_restores_previous_valid_state_or_baseline_without_record_deletion(self) -> None:
        store = MemoryStore()
        first = record_valid_wca_rollout_state(store, {"phase": "PAPER_RECOMMENDATION", "state_version": "valid-1"}, recorded_at=NOW)
        second = record_valid_wca_rollout_state(store, {"phase": "MANUAL_PAPER", "state_version": "valid-2"}, recorded_at=NOW)

        restored = rollback_wca_rollout(store, rolled_back_at=NOW)

        self.assertEqual(first["state_version"], "valid-1")
        self.assertEqual(second["state_version"], "valid-2")
        self.assertEqual(store.snapshots[WCA_ROLLBACK_STATE_KEY]["state_version"], "valid-1")
        self.assertEqual(restored["state_version"], "valid-1")
        self.assertFalse(restored["historical_records_deleted"])
        self.assertEqual(store.snapshots[WCA_ROLLOUT_STATE_KEY]["state_version"], "valid-1")

    def test_application_config_exposes_active_wca_v2_flags_with_paper_execution_disabled(self) -> None:
        flags = ApplicationConfig().featureFlags

        self.assertTrue(flags.wcaBackendEngineEnabled)
        self.assertTrue(flags.wcaCorrectedStrategyCatalogEnabled)
        self.assertTrue(flags.wcaDynamicWeightsEnabled)
        self.assertTrue(flags.wcaDynamicProfileEnabled)
        self.assertTrue(flags.wcaBackendBacktestEnabled)
        self.assertFalse(flags.wcaPaperExecutionEnabled)
        self.assertTrue(flags.globalGateEngineEnabled)


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


class MemoryWcaRepository:
    def __init__(self) -> None:
        self.configuration = default_wca_configuration()

    def initialize_defaults(self, **kwargs) -> None:
        configuration = kwargs.get("configuration")
        if configuration is not None:
            self.configuration = WcaConfiguration.model_validate(configuration)

    def read_active_configuration(self) -> WcaConfiguration:
        return self.configuration

    def table_counts(self):
        class Counts:
            migration_version = "memory"
            table_counts = {}

        return Counts()

    def save_configuration(self, *_args, **_kwargs) -> None:
        return None

    def save_backtest_result(self, *_args, **_kwargs) -> None:
        return None

    def load_backtest_result(self, *_args, **_kwargs):
        return None


def shadow_payload(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "strategy_outputs": {"C1": "BUY", "C2": "HOLD"},
        "scores": {"buy": 0.7, "sell": 0.1},
        "decision": "BUY",
        "quantity": 10,
        "stop": 99.0,
        "target": 103.0,
        "gate_results": {"local": "PASS", "global": "NOT_EVALUATED"},
    }
    payload.update(overrides)
    return payload


def evidence_with(**overrides) -> WcaRolloutEvidence:
    payload = {
        "persisted_evidence_ids": frozenset(),
        "prior_steps_passed": True,
    }
    payload.update(overrides)
    return WcaRolloutEvidence(**payload)


def historical_replay_evidence(**overrides) -> WcaRolloutEvidence:
    payload = {
        "persisted_evidence_ids": frozenset({"deterministic_replay_parity", "successful_restart_recovery"}),
        "prior_steps_passed": True,
        "deterministic_replay_parity": True,
        "restart_recovery_passed": True,
    }
    payload.update(overrides)
    return evidence_with(**payload)


def shadow_evidence(**overrides) -> WcaRolloutEvidence:
    payload = {
        "persisted_evidence_ids": frozenset(
            {
                "deterministic_replay_parity",
                "successful_restart_recovery",
                "no_unexplained_decision_mismatches",
                "acceptable_event_lag",
                "acceptable_decision_latency",
            }
        ),
        "unexplained_decision_mismatches": 0,
        "max_event_lag_seconds": 1,
        "max_decision_latency_seconds": 1,
    }
    payload.update(overrides)
    return historical_replay_evidence(**payload)


def recommendation_evidence(**overrides) -> WcaRolloutEvidence:
    payload = {
        "persisted_evidence_ids": frozenset(
            {
                "deterministic_replay_parity",
                "successful_restart_recovery",
                "no_unexplained_decision_mismatches",
                "no_duplicate_broker_orders",
                "no_cross_algorithm_inventory_mutations",
                "successful_reconciliation",
                "no_unprotected_positions",
                "acceptable_event_lag",
                "acceptable_decision_latency",
                "acceptable_broker_latency",
            }
        ),
        "duplicate_broker_orders": 0,
        "cross_algorithm_inventory_mutations": 0,
        "reconciliation_passed": True,
        "unprotected_positions": 0,
        "max_broker_latency_seconds": 1,
    }
    payload.update(overrides)
    return shadow_evidence(**payload)


def manual_paper_evidence(**overrides) -> WcaRolloutEvidence:
    payload = {
        "persisted_evidence_ids": frozenset(
            {
                "deterministic_replay_parity",
                "successful_restart_recovery",
                "no_unexplained_decision_mismatches",
                "no_duplicate_broker_orders",
                "no_cross_algorithm_inventory_mutations",
                "successful_reconciliation",
                "no_unprotected_positions",
                "acceptable_event_lag",
                "acceptable_decision_latency",
                "acceptable_broker_latency",
                "acceptable_realised_slippage",
            }
        ),
        "average_realised_slippage_per_share": 0.01,
    }
    payload.update(overrides)
    return recommendation_evidence(**payload)


def complete_evidence(**overrides) -> WcaRolloutEvidence:
    payload = {
        "persisted_evidence_ids": frozenset(WCA_REQUIRED_ROLLOUT_EVIDENCE),
        "market_conditions": ("trend", "range", "volatile"),
        "session_periods": ("opening", "midday", "closing"),
        "high_volatility_sessions": 1,
        "economic_event_sessions": 1,
        "paper_observation_days": 15,
        "paper_trade_count": 10,
        "rollback_tested": True,
        "rollback_restored_safe_state": True,
        "reconciliation_after_failure_passed": True,
        "healthy_state_validation_passed": True,
    }
    payload.update(overrides)
    return manual_paper_evidence(**payload)


def fully_validated_rollout(
    *,
    paper_trading_stable: bool = True,
    tests_passed: bool = True,
    live_trading_enabled: bool = False,
) -> WcaRolloutValidation:
    return WcaRolloutValidation(
        legacy_parity_passed=True,
        corrected_catalog_shadow_passed=True,
        full_history_backtest_passed=True,
        walk_forward_passed=True,
        untouched_holdout_passed=True,
        paper_recommendation_passed=True,
        paper_execution_passed=True,
        paper_trading_stable=paper_trading_stable,
        multiple_market_conditions_passed=paper_trading_stable,
        multi_week_paper_validation_passed=paper_trading_stable,
        legacy_removal_accepted=paper_trading_stable,
        tests_passed=tests_passed,
        live_trading_enabled=live_trading_enabled,
    )


if __name__ == "__main__":
    unittest.main()
