from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "quality-gates.yml"
QUALITY_SCRIPT = ROOT / "scripts" / "ci_quality_gates.py"
META_STRATEGY_PAPER_READINESS_SCRIPT = ROOT / "scripts" / "meta_strategy_paper_readiness_gate.py"
STATIC_QUALITY_SCRIPT = ROOT / "scripts" / "python_static_quality.py"
FRONTEND_PACKAGE = ROOT / "frontend" / "package.json"


class CIQualityGatesTest(unittest.TestCase):
    def test_workflow_installs_dependencies_without_secrets_and_runs_gate_script(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("backend/requirements-ci.txt", workflow)
        self.assertIn("npm ci", workflow)
        self.assertIn("node-version: \"24\"", workflow)
        self.assertIn("python scripts/ci_quality_gates.py", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("ALPACA", workflow.upper())

    def test_quality_gate_script_names_every_required_subsystem(self) -> None:
        source = QUALITY_SCRIPT.read_text(encoding="utf-8")

        for label in (
            "python-format-lint",
            "python-type-check",
            "pytest",
            "regime-focused-tests",
            "meta-strategy-dedicated-tests",
            "meta-strategy-paper-readiness",
            "session-dedicated-tests",
            "typescript-type-check",
            "frontend-tests",
            "frontend-build",
            "database-migration-test",
            "deterministic-replay-test",
            "schema-compatibility-test",
            "safety-critical-regression-tests",
            "wca-safety-critical-tests",
            "regime-final-acceptance",
        ):
            self.assertIn(label, source)

    def test_quality_gate_script_keeps_required_failure_criteria_in_ci(self) -> None:
        source = QUALITY_SCRIPT.read_text(encoding="utf-8")

        for test_file in (
            "test_phase12_comprehensive.py",
            "test_point_in_time_feature_engine.py",
            "test_snapshot_persistence_migrations.py",
            "test_event_driven_replay_engine.py",
            "test_v2_e2e_replay_fixtures.py",
            "test_domain_models_v2.py",
            "test_trading_settings_schema.py",
            "test_decision_snapshot_v2_archive.py",
            "test_candidate_meta_features.py",
            "test_safe_ml_inference_modes.py",
            "test_meta_strategy_step6_architecture_isolation.py",
            "test_meta_strategy_step42_frontend_boundary.py",
            "backend/tests/meta_strategy",
            "--cov=backend/app/algorithms/meta_strategy",
            "--cov-branch",
            "--cov-fail-under=85",
            "backend/tests/test_session_classifier.py",
            "backend/tests/test_session_runtime_parity.py",
            "backend/tests/test_session_frontend_contract.py",
            "backend/tests/test_session_research_calibration.py",
            "backend/tests/test_session_step19_rollout.py",
            "backend/tests/test_session_final_acceptance.py",
            "test_regime_final_acceptance.py",
            "test_regime_phase17_rollout.py",
            "backend/tests/regime",
            "test_wca_step16_safety_critical_ci.py",
            "test_wca_step7_background_runtime.py",
            "test_wca_step10_paper_broker_outbox.py",
            "test_wca_step15_api_frontend_control_surface.py",
        ):
            self.assertIn(test_file, source)

    def test_meta_strategy_paper_readiness_gate_covers_automatic_paper_failure_modes(self) -> None:
        ci_source = QUALITY_SCRIPT.read_text(encoding="utf-8")
        gate_source = META_STRATEGY_PAPER_READINESS_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("scripts/meta_strategy_paper_readiness_gate.py", ci_source)
        for category in (
            "architecture-and-ownership",
            "state-provider",
            "pipeline-stages",
            "local-risk-and-sizing",
            "paper-control-and-runtime-supervisor",
            "market-clock",
            "reconciliation-and-position-management",
            "readiness-and-observability",
            "automatic-paper-e2e",
        ):
            self.assertIn(category, gate_source)

        for test_file in (
            "test_meta_strategy_step6_architecture_isolation.py",
            "test_meta_strategy_authoritative_state_provider.py",
            "test_meta_strategy_step31_execution_pipeline.py",
            "test_meta_strategy_step27_local_gates.py",
            "test_meta_strategy_step29_position_sizing.py",
            "test_meta_strategy_phase9_paper_execution.py",
            "test_meta_strategy_market_clock.py",
            "test_meta_strategy_runtime_supervisor.py",
            "test_reconciliation.py",
            "test_meta_strategy_position_management_worker.py",
            "test_paper_readiness_acceptance.py",
            "test_required_paper_e2e.py",
        ):
            self.assertIn(test_file, gate_source)

        for criterion in (
            "toggle_off_blocks_new_entry_broker_call",
            "zero_financial_values_do_not_default",
            "market_closed_blocks_broker_call",
            "hard_gate_result_cannot_be_bypassed",
            "sibling_inventory_isolation",
            "duplicate_order_submission_blocked",
            "live_trading_never_enabled",
            "mandatory_pipeline_stages_are_concrete",
            "readiness_cannot_be_bypassed",
        ):
            self.assertIn(criterion, gate_source)

    def test_static_quality_script_checks_formatting_and_lint_parseability(self) -> None:
        source = STATIC_QUALITY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("ast.parse", source)
        self.assertIn("trailing whitespace", source)
        self.assertIn("tab indentation", source)
        self.assertIn("missing final newline", source)

    def test_frontend_package_exposes_standalone_typecheck_script(self) -> None:
        package_json = FRONTEND_PACKAGE.read_text(encoding="utf-8")

        self.assertIn("\"typecheck\"", package_json)
        self.assertIn("tsc --noEmit", package_json)


if __name__ == "__main__":
    unittest.main()
