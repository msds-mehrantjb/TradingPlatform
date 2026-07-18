from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "quality-gates.yml"
QUALITY_SCRIPT = ROOT / "scripts" / "ci_quality_gates.py"
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
            "typescript-type-check",
            "frontend-tests",
            "frontend-build",
            "database-migration-test",
            "deterministic-replay-test",
            "schema-compatibility-test",
            "safety-critical-regression-tests",
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
            "test_regime_final_acceptance.py",
            "test_regime_phase17_rollout.py",
            "backend/tests/regime",
        ):
            self.assertIn(test_file, source)

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
