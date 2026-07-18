from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend.app.algorithms.wca.rollout import (
    GLOBAL_GATE_ENGINE_ENABLED,
    WCA_BACKEND_BACKTEST_ENABLED,
    WCA_BACKEND_ENGINE_ENABLED,
    WCA_CORRECTED_STRATEGY_CATALOG_ENABLED,
    WCA_DYNAMIC_PROFILE_ENABLED,
    WCA_DYNAMIC_WEIGHTS_ENABLED,
    WCA_PAPER_EXECUTION_ENABLED,
    WCA_ROLLBACK_STATE_KEY,
    WCA_ROLLOUT_PHASES,
    WCA_ROLLOUT_STATE_KEY,
    WCA_SHADOW_COMPARISON_FIELDS,
    WcaRolloutFlags,
    WcaRolloutValidation,
    compare_shadow_results,
    evaluate_wca_rollout_phase,
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
            {
                "shadow_comparison.py",
                "paper_stability.py",
                "rollout.py",
                "final_acceptance.py",
                "test_coverage.py",
            },
        )
        self.assertEqual(
            {row.file_name: row.responsibility for row in WCA_VALIDATION_ROLLOUT_FILE_INVENTORY},
            {
                "shadow_comparison.py": "Legacy-versus-new WCA comparison.",
                "paper_stability.py": "Paper-run stability validation.",
                "rollout.py": "Controlled WCA rollout and rollback.",
                "final_acceptance.py": "WCA completion ledger.",
                "test_coverage.py": "WCA test coverage reporting.",
            },
        )
        for file_name in WCA_VALIDATION_ROLLOUT_FILE_NAMES:
            self.assertTrue((ROOT / "backend" / "app" / "algorithms" / "wca" / file_name).is_file(), file_name)

    def test_dedicated_wca_test_suite_inventory_covers_requested_areas_without_claiming_passage(self) -> None:
        self.assertEqual(
            WCA_TEST_SUITE_COVERAGE_AREA_IDS,
            {
                "structure",
                "strategy_isolation",
                "confidence",
                "weights",
                "market_status",
                "dynamic_settings",
                "aggregation",
                "gates",
                "sizing",
                "backtesting",
                "persistence",
                "rollout",
                "paper_execution",
                "reconciliation",
                "stability",
                "final_acceptance",
            },
        )
        self.assertTrue(WCA_TEST_SUITE_PASS_REQUIRES_EXECUTION)
        for area in WCA_TEST_SUITE_COVERAGE_INVENTORY:
            self.assertTrue((ROOT / "backend" / "tests" / area.test_file).is_file(), area.area_id)

        report = wca_validation_rollout_inventory_report()
        self.assertFalse(report["testPresenceProvesPassing"])
        self.assertTrue(report["passingRequiresPytestExecution"])

    def test_feature_flags_default_to_active_v2_with_paper_execution_disabled(self) -> None:
        flags = wca_rollout_feature_flags({})

        self.assertTrue(flags.backend_engine_enabled)
        self.assertTrue(flags.corrected_strategy_catalog_enabled)
        self.assertTrue(flags.dynamic_weights_enabled)
        self.assertTrue(flags.dynamic_profile_enabled)
        self.assertTrue(flags.global_gate_engine_enabled)
        self.assertTrue(flags.backend_backtest_enabled)
        self.assertFalse(flags.paper_execution_enabled)
        self.assertFalse(paper_execution_allowed(flags=flags, validation=fully_validated_rollout()))

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

        self.assertTrue(flags.backend_engine_enabled)
        self.assertTrue(flags.corrected_strategy_catalog_enabled)
        self.assertFalse(flags.dynamic_weights_enabled)
        self.assertTrue(flags.dynamic_profile_enabled)
        self.assertTrue(flags.global_gate_engine_enabled)
        self.assertTrue(flags.backend_backtest_enabled)
        self.assertFalse(flags.paper_execution_enabled)

    def test_legacy_parity_runs_shadow_without_submission(self) -> None:
        flags = WcaRolloutFlags(backend_engine_enabled=True)
        phase = evaluate_wca_rollout_phase("legacy_parity", flags=flags, validation=WcaRolloutValidation())
        comparison = compare_shadow_results(shadow_payload(quantity=10), shadow_payload(quantity=10.00001))

        self.assertTrue(phase.enabled)
        self.assertTrue(comparison.within_tolerance)
        self.assertFalse(comparison.submission_allowed)
        self.assertEqual(comparison.compared_fields, WCA_SHADOW_COMPARISON_FIELDS)

    def test_shadow_comparison_reports_field_level_mismatches(self) -> None:
        comparison = compare_shadow_results(shadow_payload(decision="BUY"), shadow_payload(decision="SELL"))

        self.assertFalse(comparison.within_tolerance)
        self.assertEqual(comparison.mismatched_fields, ("decision",))
        self.assertIn("wca.rollout.shadow_comparison.no_submission", comparison.reason_codes)

    def test_rollout_phases_require_prior_acceptance_criteria(self) -> None:
        flags = WcaRolloutFlags(
            backend_engine_enabled=True,
            corrected_strategy_catalog_enabled=True,
            backend_backtest_enabled=True,
        )
        validation = WcaRolloutValidation(legacy_parity_passed=True)

        corrected = evaluate_wca_rollout_phase("corrected_catalog_shadow", flags=flags, validation=validation)
        backtest = evaluate_wca_rollout_phase("backend_backtest", flags=flags, validation=validation)

        self.assertTrue(corrected.enabled)
        self.assertFalse(backtest.enabled)
        self.assertIn("wca.rollout.corrected_catalog_shadow_not_validated", backtest.reason_codes)

    def test_corrected_catalog_dynamic_flags_do_not_enable_paper_execution(self) -> None:
        flags = WcaRolloutFlags(
            backend_engine_enabled=True,
            corrected_strategy_catalog_enabled=True,
            dynamic_weights_enabled=True,
            dynamic_profile_enabled=True,
            backend_backtest_enabled=True,
            global_gate_engine_enabled=False,
            paper_execution_enabled=False,
        )
        validation = fully_validated_rollout()

        self.assertTrue(paper_recommendation_allowed(flags=flags, validation=validation))
        self.assertFalse(paper_execution_allowed(flags=flags, validation=validation))

    def test_backend_backtest_phase_requires_full_history_walk_forward_and_holdout_before_recommendations(self) -> None:
        flags = WcaRolloutFlags(
            backend_engine_enabled=True,
            corrected_strategy_catalog_enabled=True,
            backend_backtest_enabled=True,
        )
        validation = WcaRolloutValidation(
            legacy_parity_passed=True,
            corrected_catalog_shadow_passed=True,
            full_history_backtest_passed=True,
            walk_forward_passed=False,
            untouched_holdout_passed=True,
        )

        backtest = evaluate_wca_rollout_phase("backend_backtest", flags=flags, validation=validation)
        recommendation = evaluate_wca_rollout_phase("paper_recommendation", flags=flags, validation=validation)

        self.assertTrue(backtest.enabled)
        self.assertFalse(recommendation.enabled)
        self.assertIn("wca.rollout.walk_forward_not_validated", recommendation.reason_codes)

    def test_paper_recommendation_mode_blocks_submission_when_execution_flag_is_enabled_too_early(self) -> None:
        flags = WcaRolloutFlags(
            backend_engine_enabled=True,
            corrected_strategy_catalog_enabled=True,
            backend_backtest_enabled=True,
            paper_execution_enabled=True,
        )
        validation = fully_validated_rollout(paper_recommendation_passed=False)

        recommendation = evaluate_wca_rollout_phase("paper_recommendation", flags=flags, validation=validation)

        self.assertFalse(recommendation.enabled)
        self.assertIn("wca.rollout.paper_execution_must_remain_disabled_for_recommendations", recommendation.reason_codes)

    def test_paper_execution_requires_backend_global_gates_tests_and_recommendation_validation(self) -> None:
        flags = WcaRolloutFlags(
            backend_engine_enabled=True,
            corrected_strategy_catalog_enabled=True,
            backend_backtest_enabled=True,
            paper_execution_enabled=True,
            global_gate_engine_enabled=False,
        )
        validation = fully_validated_rollout(tests_passed=False)

        blocked = evaluate_wca_rollout_phase("paper_execution", flags=flags, validation=validation)
        enabled = evaluate_wca_rollout_phase(
            "paper_execution",
            flags=WcaRolloutFlags(
                backend_engine_enabled=True,
                corrected_strategy_catalog_enabled=True,
                backend_backtest_enabled=True,
                paper_execution_enabled=True,
                global_gate_engine_enabled=True,
            ),
            validation=fully_validated_rollout(),
        )

        self.assertFalse(blocked.enabled)
        self.assertIn("wca.rollout.global_gate_engine_required", blocked.reason_codes)
        self.assertIn("wca.rollout.tests_not_passed", blocked.reason_codes)
        self.assertTrue(enabled.enabled)

    def test_extended_paper_validation_and_legacy_removal_are_separate_final_gates(self) -> None:
        flags = WcaRolloutFlags(backend_engine_enabled=True, corrected_strategy_catalog_enabled=True, backend_backtest_enabled=True)
        validation = fully_validated_rollout(
            paper_execution_passed=True,
            paper_trading_stable=True,
            multiple_market_conditions_passed=False,
            multi_week_paper_validation_passed=True,
            legacy_removal_accepted=True,
        )

        extended = evaluate_wca_rollout_phase("extended_paper_validation", flags=flags, validation=validation)
        removal = evaluate_wca_rollout_phase("legacy_removal", flags=flags, validation=validation)

        self.assertTrue(extended.enabled)
        self.assertFalse(removal.enabled)
        self.assertIn("wca.rollout.market_conditions_not_validated", removal.reason_codes)

    def test_extended_paper_validation_requires_stable_paper_evidence(self) -> None:
        flags = WcaRolloutFlags(backend_engine_enabled=True, corrected_strategy_catalog_enabled=True, backend_backtest_enabled=True)
        validation = fully_validated_rollout(paper_execution_passed=True, paper_trading_stable=False)

        blocked = evaluate_wca_rollout_phase("extended_paper_validation", flags=flags, validation=validation)
        enabled = evaluate_wca_rollout_phase(
            "extended_paper_validation",
            flags=flags,
            validation=fully_validated_rollout(paper_execution_passed=True, paper_trading_stable=True),
        )

        self.assertFalse(blocked.enabled)
        self.assertIn("wca.rollout.paper_trading_not_stable", blocked.reason_codes)
        self.assertTrue(enabled.enabled)

    def test_live_trading_blocks_every_phase_even_when_flags_and_metrics_pass(self) -> None:
        flags = WcaRolloutFlags(
            backend_engine_enabled=True,
            corrected_strategy_catalog_enabled=True,
            dynamic_weights_enabled=True,
            dynamic_profile_enabled=True,
            backend_backtest_enabled=True,
            global_gate_engine_enabled=True,
            paper_execution_enabled=True,
        )
        validation = fully_validated_rollout(live_trading_enabled=True)

        for phase in WCA_ROLLOUT_PHASES:
            with self.subTest(phase=phase):
                status = evaluate_wca_rollout_phase(phase, flags=flags, validation=validation)
                self.assertFalse(status.enabled)
                self.assertIn("wca.rollout.live_trading_never_allowed", status.reason_codes)

    def test_rollback_single_configuration_restores_safe_legacy_posture_without_deleting_history(self) -> None:
        rollback = rollback_configuration()

        self.assertFalse(rollback[WCA_BACKEND_ENGINE_ENABLED])
        self.assertEqual(rollback["display"], "legacy_wca")
        self.assertEqual(rollback["weights"], "static_baseline")
        self.assertEqual(rollback["settings"], "baseline_trading_settings")
        self.assertEqual(rollback["dynamic_profile"], "disabled")
        self.assertFalse(rollback["automated_paper_submission"])
        self.assertFalse(rollback["delete_historical_records"])

    def test_rollback_restores_previous_valid_state_or_baseline_without_record_deletion(self) -> None:
        store = MemoryStore()
        first = record_valid_wca_rollout_state(store, {"phase": "paper_recommendation", "state_version": "valid-1"}, recorded_at=NOW)
        second = record_valid_wca_rollout_state(store, {"phase": "paper_execution", "state_version": "valid-2"}, recorded_at=NOW)

        restored = rollback_wca_rollout(store, rolled_back_at=NOW)

        self.assertEqual(first["state_version"], "valid-1")
        self.assertEqual(second["state_version"], "valid-2")
        self.assertEqual(store.snapshots[WCA_ROLLBACK_STATE_KEY]["state_version"], "valid-1")
        self.assertEqual(restored["state_version"], "valid-1")
        self.assertFalse(restored["historical_records_deleted"])
        self.assertEqual(store.snapshots[WCA_ROLLOUT_STATE_KEY]["state_version"], "valid-1")

    def test_rollout_status_and_service_status_report_all_phases(self) -> None:
        flags = WcaRolloutFlags(backend_engine_enabled=True)
        status = wca_rollout_status(flags=flags, validation=WcaRolloutValidation())
        service_status = WcaService(repository=MemoryWcaRepository()).status()

        self.assertEqual(status["algorithm_id"], "wca")
        self.assertEqual(len(status["phases"]), len(WCA_ROLLOUT_PHASES))
        self.assertFalse(status["live_trading_allowed"])
        self.assertIn("rollback_plan", status)
        self.assertIn("rollout", service_status)
        self.assertEqual(service_status["status"], "ready")
        self.assertEqual(service_status["mode"], "backend_v2_active_paper_recommendation_only")
        self.assertFalse(service_status["rollout"]["paper_execution_allowed"])
        self.assertFalse(service_status["rollout"]["live_trading_allowed"])
        self.assertIn("finalAcceptance", service_status)

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
    def initialize_defaults(self, **_kwargs) -> None:
        return None

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


def fully_validated_rollout(
    *,
    paper_recommendation_passed: bool = True,
    paper_execution_passed: bool = True,
    paper_trading_stable: bool = True,
    multiple_market_conditions_passed: bool = True,
    multi_week_paper_validation_passed: bool = True,
    tests_passed: bool = True,
    legacy_removal_accepted: bool = True,
    live_trading_enabled: bool = False,
) -> WcaRolloutValidation:
    return WcaRolloutValidation(
        legacy_parity_passed=True,
        corrected_catalog_shadow_passed=True,
        full_history_backtest_passed=True,
        walk_forward_passed=True,
        untouched_holdout_passed=True,
        paper_recommendation_passed=paper_recommendation_passed,
        paper_execution_passed=paper_execution_passed,
        paper_trading_stable=paper_trading_stable,
        multiple_market_conditions_passed=multiple_market_conditions_passed,
        multi_week_paper_validation_passed=multi_week_paper_validation_passed,
        legacy_removal_accepted=legacy_removal_accepted,
        tests_passed=tests_passed,
        live_trading_enabled=live_trading_enabled,
    )


if __name__ == "__main__":
    unittest.main()
