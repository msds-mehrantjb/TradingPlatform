from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
PYTHON = sys.executable
NPM = shutil.which("npm") or "npm"


@dataclass(frozen=True)
class QualityGate:
    label: str
    command: list[str]
    cwd: Path = ROOT


QUALITY_GATES = (
    QualityGate(
        label="python-format-lint",
        command=[PYTHON, "scripts/python_static_quality.py"],
    ),
    QualityGate(
        label="python-type-check",
        command=[
            PYTHON,
            "-m",
            "mypy",
            "--no-sqlite-cache",
            "--ignore-missing-imports",
            "--follow-imports=skip",
            "--allow-untyped-defs",
            "--no-strict-optional",
            "--no-warn-unused-ignores",
            "backend/app/domain/models.py",
            "backend/app/api/v2.py",
            "backend/app/trading_policy/models.py",
            "backend/app/gates/models.py",
            "scripts/ci_quality_gates.py",
            "scripts/python_static_quality.py",
        ],
    ),
    QualityGate(
        label="pytest",
        command=[PYTHON, "-m", "pytest", "backend/tests", "-q"],
    ),
    QualityGate(
        label="regime-focused-tests",
        command=[
            PYTHON,
            "-m",
            "pytest",
            "backend/tests/regime",
            "-q",
            "--cov=backend/app/algorithms/regime",
            "--cov-branch",
        ],
    ),
    QualityGate(
        label="meta-strategy-dedicated-tests",
        command=[
            PYTHON,
            "-m",
            "pytest",
            "backend/tests/meta_strategy",
            "-q",
            "--cov=backend/app/algorithms/meta_strategy",
            "--cov-branch",
            "--cov-fail-under=85",
        ],
    ),
    QualityGate(
        label="typescript-type-check",
        command=[NPM, "run", "typecheck"],
        cwd=FRONTEND,
    ),
    QualityGate(
        label="frontend-tests",
        command=[NPM, "test"],
        cwd=FRONTEND,
    ),
    QualityGate(
        label="frontend-build",
        command=[NPM, "run", "build:ci"],
        cwd=FRONTEND,
    ),
    QualityGate(
        label="database-migration-test",
        command=[PYTHON, "-m", "pytest", "backend/tests/test_snapshot_persistence_migrations.py", "-q"],
    ),
    QualityGate(
        label="deterministic-replay-test",
        command=[
            PYTHON,
            "-m",
            "pytest",
            "backend/tests/test_event_driven_replay_engine.py",
            "backend/tests/test_v2_e2e_replay_fixtures.py",
            "-q",
        ],
    ),
    QualityGate(
        label="schema-compatibility-test",
        command=[
            PYTHON,
            "-m",
            "pytest",
            "backend/tests/test_domain_models_v2.py",
            "backend/tests/test_trading_settings_schema.py",
            "backend/tests/test_candidate_meta_features.py",
            "backend/tests/test_safe_ml_inference_modes.py",
            "backend/tests/test_frontend_v2_presentation.py",
            "-q",
        ],
    ),
    QualityGate(
        label="safety-critical-regression-tests",
        command=[
            PYTHON,
            "-m",
            "pytest",
            "backend/tests/test_phase12_comprehensive.py",
            "backend/tests/test_point_in_time_feature_engine.py",
            "backend/tests/test_decision_snapshot_v2_archive.py",
            "backend/tests/test_candidate_meta_features.py",
            "backend/tests/test_safe_ml_inference_modes.py",
            "backend/tests/test_meta_strategy_step6_architecture_isolation.py",
            "backend/tests/test_meta_strategy_step42_frontend_boundary.py",
            "backend/tests/test_global_gate_engine.py",
            "backend/tests/test_wca_step19_comprehensive.py",
            "backend/tests/test_wca_step21_final_acceptance.py",
            "backend/tests/test_regime_final_acceptance.py",
            "backend/tests/test_regime_phase17_rollout.py",
            "backend/tests/regime",
            "backend/tests/test_weighted_voting_final_acceptance.py",
            "backend/tests/test_weighted_voting_step32_comprehensive.py",
            "backend/tests/test_event_driven_replay_engine.py",
            "-q",
        ],
    ),
    QualityGate(
        label="regime-final-acceptance",
        command=[
            PYTHON,
            "-m",
            "pytest",
            "backend/tests/test_regime_final_acceptance.py",
            "backend/tests/test_regime_phase13_backtest_api.py",
            "backend/tests/test_regime_phase14_persistence.py",
            "backend/tests/test_regime_phase17_rollout.py",
            "backend/tests/test_global_account_risk_state.py",
            "backend/tests/test_global_portfolio_risk_manager_phase12.py",
            "-q",
        ],
    ),
    QualityGate(
        label="weighted-voting-final-acceptance",
        command=[
            PYTHON,
            "-m",
            "pytest",
            "backend/tests/test_weighted_voting_final_acceptance.py",
            "backend/tests/test_weighted_voting_ml_decoupling.py",
            "backend/tests/test_weighted_voting_algorithm_isolation.py",
            "backend/tests/test_weighted_voting_aggregation.py",
            "backend/tests/test_weighted_voting_decision_gates.py",
            "backend/tests/test_weighted_voting_settings.py",
            "backend/tests/test_weighted_voting_backtest_engine.py",
            "backend/tests/test_weighted_voting_walk_forward.py",
            "backend/tests/test_weighted_voting_paper_order_gateway.py",
            "backend/tests/test_weighted_voting_step32_comprehensive.py",
            "-q",
        ],
    ),
    QualityGate(
        label="v2-completion-readiness",
        command=[PYTHON, "scripts/v2_readiness_gate.py"],
    ),
)


def run_gate(gate: QualityGate) -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("PYTHONPATH", str(ROOT))

    print(f"::group::{gate.label}")
    print(f"$ {' '.join(gate.command)}")
    result = subprocess.run(gate.command, cwd=gate.cwd, env=env, check=False)
    print("::endgroup::")
    return result.returncode


def main() -> int:
    failures: list[str] = []
    for gate in QUALITY_GATES:
        returncode = run_gate(gate)
        if returncode:
            failures.append(f"{gate.label} exited with {returncode}")

    if failures:
        print("Quality gates failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("All quality gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
