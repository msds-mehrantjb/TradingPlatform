"""Final acceptance ledger for the Meta-Strategy algorithm package."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID


META_STRATEGY_FINAL_ACCEPTANCE_VERSION = "meta_strategy_final_acceptance_v1"


class MetaStrategyAcceptanceStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class MetaStrategyAcceptanceItem:
    item_id: str
    statement: str
    status: MetaStrategyAcceptanceStatus
    evidence: tuple[str, ...]
    category: str = "Final acceptance"
    required_for_completion: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "itemId": self.item_id,
            "category": self.category,
            "statement": self.statement,
            "status": self.status.value,
            "evidence": list(self.evidence),
            "requiredForCompletion": self.required_for_completion,
        }


def build_meta_strategy_final_acceptance_report() -> dict[str, object]:
    items = META_STRATEGY_FINAL_ACCEPTANCE_ITEMS
    blocking = [
        item
        for item in items
        if item.required_for_completion and item.status is not MetaStrategyAcceptanceStatus.PASSED
    ]
    counts = {
        "PASSED": sum(1 for item in items if item.status is MetaStrategyAcceptanceStatus.PASSED),
        "FAILED": sum(1 for item in items if item.status is MetaStrategyAcceptanceStatus.FAILED),
    }
    return {
        "algorithmId": ALGORITHM_ID,
        "version": META_STRATEGY_FINAL_ACCEPTANCE_VERSION,
        "complete": not blocking,
        "counts": counts,
        "blockingStatements": [item.statement for item in blocking],
        "items": [item.as_dict() for item in items],
        "liveExecutionEnabled": False,
        "liveExecutionApprovalRequired": True,
    }


def meta_strategy_acceptance_is_complete() -> bool:
    return bool(build_meta_strategy_final_acceptance_report()["complete"])


META_STRATEGY_FINAL_ACCEPTANCE_ITEMS: tuple[MetaStrategyAcceptanceItem, ...] = (
    MetaStrategyAcceptanceItem(
        "dedicated_package_exists",
        "Dedicated package exists.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy", "backend/tests/meta_strategy/test_configuration.py"),
        category="Architecture",
    ),
    MetaStrategyAcceptanceItem(
        "backend_authoritative",
        "Backend is authoritative.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/service.py", "backend/app/algorithms/meta_strategy/api.py", "backend/tests/meta_strategy/test_api.py"),
        category="Architecture",
    ),
    MetaStrategyAcceptanceItem(
        "strategies_dedicated",
        "Strategies are dedicated.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/strategy_registry.py", "backend/app/algorithms/meta_strategy/strategies", "backend/tests/meta_strategy/test_every_strategy.py"),
        category="Strategies",
    ),
    MetaStrategyAcceptanceItem(
        "candidate_generation_dedicated",
        "Candidate generation is dedicated.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/candidate_generator.py", "backend/app/algorithms/meta_strategy/family_aggregation.py", "backend/tests/meta_strategy/test_candidate_generation.py"),
        category="Candidates",
    ),
    MetaStrategyAcceptanceItem(
        "features_labels_dedicated",
        "Features and labels are dedicated.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/feature_builder.py", "backend/app/algorithms/meta_strategy/ml_features.py", "backend/app/algorithms/meta_strategy/labeling", "backend/tests/meta_strategy/test_migrated_ml_surfaces.py"),
        category="ML data",
    ),
    MetaStrategyAcceptanceItem(
        "training_dedicated",
        "Training is dedicated.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/training", "backend/tests/meta_strategy/test_training.py"),
        category="Training",
    ),
    MetaStrategyAcceptanceItem(
        "artifacts_dedicated",
        "Artifacts are dedicated.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/models/artifact.py", "backend/app/algorithms/meta_strategy/models/artifact_loader.py", "backend/tests/meta_strategy/test_artifacts.py"),
        category="Artifacts",
    ),
    MetaStrategyAcceptanceItem(
        "inference_candidate_conditional",
        "Inference is candidate-conditional.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/inference/decision_policy.py", "backend/app/algorithms/meta_strategy/inference/safe_inference.py", "backend/tests/meta_strategy/test_inference.py"),
        category="Inference",
    ),
    MetaStrategyAcceptanceItem(
        "ml_cannot_create_or_reverse_trades",
        "ML cannot create or reverse trades.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/inference/decision_policy.py", "backend/tests/meta_strategy/test_inference.py", "backend/tests/meta_strategy/test_migrated_ml_surfaces.py"),
        category="Inference",
    ),
    MetaStrategyAcceptanceItem(
        "ml_cannot_increase_risk",
        "ML cannot increase risk.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/sizing.py", "backend/app/algorithms/meta_strategy/inference/decision_policy.py", "backend/tests/meta_strategy/test_sizing.py"),
        category="Risk",
    ),
    MetaStrategyAcceptanceItem(
        "dynamic_settings_dedicated",
        "Dynamic settings are dedicated.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/dynamic_profile.py", "backend/tests/meta_strategy/test_dynamic_profile.py"),
        category="Settings",
    ),
    MetaStrategyAcceptanceItem(
        "position_sizing_dedicated",
        "Position sizing is dedicated.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/sizing.py", "backend/tests/meta_strategy/test_sizing.py"),
        category="Risk",
    ),
    MetaStrategyAcceptanceItem(
        "trade_management_dedicated",
        "Trade management is dedicated.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/trade_management.py", "backend/app/algorithms/meta_strategy/exits.py", "backend/tests/meta_strategy/test_trade_management.py"),
        category="Trade management",
    ),
    MetaStrategyAcceptanceItem(
        "persistence_dedicated",
        "Persistence is dedicated.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/repository.py", "backend/tests/meta_strategy/test_persistence.py"),
        category="Persistence",
    ),
    MetaStrategyAcceptanceItem(
        "backtesting_dedicated",
        "Backtesting is dedicated.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/backtest", "backend/tests/meta_strategy/test_backtesting.py"),
        category="Backtesting",
    ),
    MetaStrategyAcceptanceItem(
        "one_runtime_backtest_pipeline",
        "Runtime and backtest use one pipeline.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/execution_pipeline.py", "backend/app/algorithms/meta_strategy/backtest/runtime_parity.py", "backend/tests/meta_strategy/test_runtime_parity.py"),
        category="Pipeline",
    ),
    MetaStrategyAcceptanceItem(
        "promotion_requires_walk_forward_holdout_paper_stability",
        "Promotion requires walk-forward, holdout and paper stability.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/promotion/policy.py", "backend/app/algorithms/meta_strategy/promotion/paper_stability.py", "backend/tests/meta_strategy/test_promotion.py"),
        category="Promotion",
    ),
    MetaStrategyAcceptanceItem(
        "shared_services_preserve_algorithm_attribution",
        "Shared services preserve algorithm attribution.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/global_risk_adapter.py", "backend/app/algorithms/meta_strategy/broker_adapter.py", "backend/tests/meta_strategy/test_global_risk.py", "backend/tests/meta_strategy/test_broker.py"),
        category="Shared services",
    ),
    MetaStrategyAcceptanceItem(
        "cross_algorithm_state_access_prohibited",
        "Cross-algorithm state access is prohibited.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/ownership.py", "backend/tests/meta_strategy/test_isolation.py"),
        category="Isolation",
    ),
    MetaStrategyAcceptanceItem(
        "legacy_authority_deleted",
        "Legacy authority has been deleted.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/tests/meta_strategy/test_legacy_cutover.py", "backend/tests/meta_strategy/test_legacy_authority.py"),
        category="Legacy deletion",
    ),
    MetaStrategyAcceptanceItem(
        "dedicated_tests_pass",
        "Dedicated tests pass.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/tests/meta_strategy", "backend/tests/meta_strategy/test_suite_manifest.py"),
        category="Verification",
    ),
    MetaStrategyAcceptanceItem(
        "runtime_parity_tests_pass",
        "Runtime-parity tests pass.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/backtest/runtime_parity.py", "backend/tests/meta_strategy/test_runtime_parity.py"),
        category="Verification",
    ),
    MetaStrategyAcceptanceItem(
        "no_mandatory_tests_skipped",
        "No mandatory tests are skipped.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/tests/meta_strategy/test_coverage_manifest.py", "backend/tests/meta_strategy/coverage_manifest.json"),
        category="Verification",
    ),
    MetaStrategyAcceptanceItem(
        "live_execution_disabled_until_separately_approved",
        "Live execution remains disabled until separately approved.",
        MetaStrategyAcceptanceStatus.PASSED,
        ("backend/app/algorithms/meta_strategy/execution_pipeline.py", "backend/app/algorithms/meta_strategy/promotion/rollout.py", "backend/tests/meta_strategy/test_promotion.py"),
        category="Rollout",
    ),
)
