"""WCA automated-test coverage metadata.

This module is intentionally static. It lets CI report which WCA Step 19
coverage categories are mandatory without importing or executing trading
logic.
"""

from __future__ import annotations

from dataclasses import dataclass


WCA_STEP19_COVERAGE_VERSION = "wca_step19_comprehensive_tests_v1"
WCA_VALIDATION_ROLLOUT_INVENTORY_VERSION = "wca_validation_rollout_inventory_v1"
WCA_TEST_SUITE_COVERAGE_VERSION = "wca_dedicated_test_suite_inventory_v1"
WCA_TEST_SUITE_PASS_REQUIRES_EXECUTION = True


@dataclass(frozen=True)
class WcaCoverageCategory:
    category_id: str
    description: str
    mandatory_ci: bool = True


@dataclass(frozen=True)
class WcaValidationRolloutFile:
    file_name: str
    responsibility: str


@dataclass(frozen=True)
class WcaTestSuiteCoverageArea:
    area_id: str
    responsibility: str
    test_file: str


WCA_VALIDATION_ROLLOUT_FILE_INVENTORY: tuple[WcaValidationRolloutFile, ...] = (
    WcaValidationRolloutFile("shadow_comparison.py", "Legacy-versus-new WCA comparison."),
    WcaValidationRolloutFile("paper_stability.py", "Paper-run stability validation."),
    WcaValidationRolloutFile("rollout.py", "Controlled WCA rollout and rollback."),
    WcaValidationRolloutFile("final_acceptance.py", "WCA completion ledger."),
    WcaValidationRolloutFile("test_coverage.py", "WCA test coverage reporting."),
)

WCA_VALIDATION_ROLLOUT_FILE_NAMES = frozenset(row.file_name for row in WCA_VALIDATION_ROLLOUT_FILE_INVENTORY)

WCA_TEST_SUITE_COVERAGE_INVENTORY: tuple[WcaTestSuiteCoverageArea, ...] = (
    WcaTestSuiteCoverageArea("structure", "WCA package structure, ownership, imports, contracts, and API schema.", "test_wca_step1_backend_structure.py"),
    WcaTestSuiteCoverageArea("strategy_isolation", "WCA strategy file isolation and deterministic snapshot-only evaluation.", "test_wca_step4_strategy_isolation.py"),
    WcaTestSuiteCoverageArea("confidence", "WCA statistical confidence calibration behavior.", "test_wca_step5_confidence_calibration.py"),
    WcaTestSuiteCoverageArea("weights", "WCA baseline, performance, reliability, shrinkage, caps, and snapshot weights.", "test_wca_step6_performance_weights.py"),
    WcaTestSuiteCoverageArea("market_status", "WCA market-condition classification and hysteresis evidence.", "test_wca_step7_market_status.py"),
    WcaTestSuiteCoverageArea("dynamic_settings", "WCA baseline-preserving dynamic effective settings.", "test_wca_step8_dynamic_profile.py"),
    WcaTestSuiteCoverageArea("aggregation", "WCA weighted confidence aggregation and final directional scoring.", "test_wca_step9_aggregation.py"),
    WcaTestSuiteCoverageArea("gates", "WCA local gates plus shared account-level global gate separation.", "test_wca_step10_local_gates.py"),
    WcaTestSuiteCoverageArea("sizing", "WCA position sizing and order-proposal quantity constraints.", "test_wca_step11_sizing.py"),
    WcaTestSuiteCoverageArea("backtesting", "WCA backend-authoritative replay, next-bar fills, costs, modes, walk-forward, and holdout.", "test_wca_step14_15_backend_backtest.py"),
    WcaTestSuiteCoverageArea("persistence", "WCA-specific persistence schema and attributed records.", "test_wca_step17_persistence.py"),
    WcaTestSuiteCoverageArea("rollout", "WCA controlled rollout phases, paper/live permissions, and rollback.", "test_wca_step20_rollout.py"),
    WcaTestSuiteCoverageArea("paper_execution", "WCA paper execution proposal path and production pipeline sequence.", "test_wca_paper_execution_pipeline.py"),
    WcaTestSuiteCoverageArea("reconciliation", "WCA broker-state reconciliation and attribution boundaries.", "test_wca_broker_reconciliation.py"),
    WcaTestSuiteCoverageArea("stability", "WCA paper-run stability validation evidence.", "test_wca_paper_stability_validation.py"),
    WcaTestSuiteCoverageArea("final_acceptance", "WCA completion ledger and blocking acceptance statements.", "test_wca_step21_final_acceptance.py"),
)

WCA_TEST_SUITE_COVERAGE_AREA_IDS = frozenset(row.area_id for row in WCA_TEST_SUITE_COVERAGE_INVENTORY)


WCA_STEP19_COVERAGE_CATEGORIES: tuple[WcaCoverageCategory, ...] = (
    WcaCoverageCategory("strategy_unit", "Every WCA primary strategy has directional, hold, applicability, invalid, history, session, and boundary tests."),
    WcaCoverageCategory("modifiers", "WCA modifiers are bounded, neutral when auxiliary data is missing, and never cast independent votes."),
    WcaCoverageCategory("aggregation", "Aggregation covers normalization, caps, ties, edge, exclusions, calibration, and correlation penalties."),
    WcaCoverageCategory("dynamic_profile", "Dynamic profile covers unchanged baseline, defensive transitions, hysteresis, risk ceilings, blocks, and expiration."),
    WcaCoverageCategory("global_gate", "Global gates cover account, broker, data, duplicate, conflict, exposure, buying-power, session, and emergency cases."),
    WcaCoverageCategory("backtest_leakage", "Backtest tests prevent future bars, future outcomes, calibration leakage, same-bar fills, holdout access, and disorder."),
    WcaCoverageCategory("failure_injection", "Failure tests cover broker, market-data, persistence, retry, ordering, stale quote, and clock failures."),
    WcaCoverageCategory("ci_guardrails", "Critical risk tests are mandatory CI checks and production execution remains disabled without passing tests."),
    WcaCoverageCategory("golden_parity", "Golden parity fixtures remain available until legacy WCA removal."),
)


def wca_step19_coverage_report() -> dict[str, object]:
    return {
        "algorithm": "wca",
        "coverageVersion": WCA_STEP19_COVERAGE_VERSION,
        "categories": tuple(category.__dict__ for category in WCA_STEP19_COVERAGE_CATEGORIES),
    }


def wca_validation_rollout_inventory_report() -> dict[str, object]:
    return {
        "algorithm": "wca",
        "inventoryVersion": WCA_VALIDATION_ROLLOUT_INVENTORY_VERSION,
        "validationRolloutFiles": tuple(row.__dict__ for row in WCA_VALIDATION_ROLLOUT_FILE_INVENTORY),
        "testSuiteCoverageVersion": WCA_TEST_SUITE_COVERAGE_VERSION,
        "testSuiteCoverage": tuple(row.__dict__ for row in WCA_TEST_SUITE_COVERAGE_INVENTORY),
        "testPresenceProvesPassing": False,
        "passingRequiresPytestExecution": WCA_TEST_SUITE_PASS_REQUIRES_EXECUTION,
    }


__all__ = [
    "WCA_TEST_SUITE_COVERAGE_AREA_IDS",
    "WCA_TEST_SUITE_COVERAGE_INVENTORY",
    "WCA_TEST_SUITE_COVERAGE_VERSION",
    "WCA_TEST_SUITE_PASS_REQUIRES_EXECUTION",
    "WCA_STEP19_COVERAGE_CATEGORIES",
    "WCA_STEP19_COVERAGE_VERSION",
    "WCA_VALIDATION_ROLLOUT_FILE_INVENTORY",
    "WCA_VALIDATION_ROLLOUT_FILE_NAMES",
    "WCA_VALIDATION_ROLLOUT_INVENTORY_VERSION",
    "WcaCoverageCategory",
    "WcaTestSuiteCoverageArea",
    "WcaValidationRolloutFile",
    "wca_step19_coverage_report",
    "wca_validation_rollout_inventory_report",
]
