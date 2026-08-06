from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class MetaStrategyPaperReadinessCategory:
    name: str
    paths: tuple[str, ...]


META_STRATEGY_PAPER_READINESS_CATEGORIES: tuple[MetaStrategyPaperReadinessCategory, ...] = (
    MetaStrategyPaperReadinessCategory(
        "architecture-and-ownership",
        (
            "backend/tests/test_meta_strategy_phase1_architecture_contracts.py",
            "backend/tests/test_meta_strategy_step6_architecture_isolation.py",
            "backend/tests/test_meta_strategy_phase5_inventory.py",
            "backend/tests/meta_strategy/test_isolation.py",
        ),
    ),
    MetaStrategyPaperReadinessCategory(
        "state-provider",
        (
            "backend/tests/test_meta_strategy_authoritative_state_provider.py",
            "backend/tests/test_meta_strategy_phase7_decision_worker.py",
        ),
    ),
    MetaStrategyPaperReadinessCategory(
        "pipeline-stages",
        (
            "backend/tests/test_meta_strategy_step31_execution_pipeline.py",
            "backend/tests/meta_strategy/test_execution_pipeline.py",
            "backend/tests/meta_strategy/test_family_aggregation.py",
        ),
    ),
    MetaStrategyPaperReadinessCategory(
        "local-risk-and-sizing",
        (
            "backend/tests/test_meta_strategy_step27_local_gates.py",
            "backend/tests/test_meta_strategy_step29_position_sizing.py",
            "backend/tests/meta_strategy/test_sizing.py",
        ),
    ),
    MetaStrategyPaperReadinessCategory(
        "paper-control-and-runtime-supervisor",
        (
            "backend/tests/test_meta_strategy_phase9_paper_execution.py",
            "backend/tests/test_meta_strategy_runtime_supervisor.py",
            "backend/tests/test_meta_strategy_one_minute_runtime_activation.py",
        ),
    ),
    MetaStrategyPaperReadinessCategory(
        "market-clock",
        (
            "backend/tests/test_meta_strategy_market_clock.py",
        ),
    ),
    MetaStrategyPaperReadinessCategory(
        "reconciliation-and-position-management",
        (
            "backend/tests/meta_strategy/test_reconciliation.py",
            "backend/tests/test_meta_strategy_position_management_worker.py",
        ),
    ),
    MetaStrategyPaperReadinessCategory(
        "readiness-and-observability",
        (
            "backend/tests/meta_strategy/test_paper_readiness_acceptance.py",
            "backend/tests/test_meta_strategy_phase12_observability_acceptance.py",
        ),
    ),
    MetaStrategyPaperReadinessCategory(
        "automatic-paper-e2e",
        (
            "backend/tests/meta_strategy/test_required_paper_e2e.py",
        ),
    ),
)


META_STRATEGY_PAPER_READINESS_FAILURE_CRITERIA: tuple[str, ...] = (
    "toggle_off_blocks_new_entry_broker_call",
    "zero_financial_values_do_not_default",
    "market_closed_blocks_broker_call",
    "hard_gate_result_cannot_be_bypassed",
    "sibling_inventory_isolation",
    "duplicate_order_submission_blocked",
    "live_trading_never_enabled",
    "mandatory_pipeline_stages_are_concrete",
    "readiness_cannot_be_bypassed",
)


def gate_paths() -> list[str]:
    paths: list[str] = []
    for category in META_STRATEGY_PAPER_READINESS_CATEGORIES:
        paths.extend(category.paths)
    return paths


def validate_gate_manifest() -> tuple[str, ...]:
    missing = []
    for path in gate_paths():
        if not (ROOT / path).is_file():
            missing.append(path)
    return tuple(missing)


def main() -> int:
    missing = validate_gate_manifest()
    if missing:
        print("Meta-Strategy paper-readiness gate references missing tests:", file=sys.stderr)
        for path in missing:
            print(f"  - {path}", file=sys.stderr)
        return 2

    command = [
        sys.executable,
        "-m",
        "pytest",
        *gate_paths(),
        "-q",
    ]
    print("Meta-Strategy automatic paper-readiness gate")
    print("Categories:")
    for category in META_STRATEGY_PAPER_READINESS_CATEGORIES:
        print(f"  - {category.name}: {len(category.paths)} file(s)")
    print("Failure criteria:")
    for criterion in META_STRATEGY_PAPER_READINESS_FAILURE_CRITERIA:
        print(f"  - {criterion}")
    print("$ " + " ".join(command))
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
