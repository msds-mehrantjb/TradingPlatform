"""WCA automated-test coverage metadata.

This module is intentionally static. It lets CI report which WCA Step 19
coverage categories are mandatory without importing or executing trading
logic.
"""

from __future__ import annotations

from dataclasses import dataclass


WCA_STEP19_COVERAGE_VERSION = "wca_step19_comprehensive_tests_v1"
WCA_STEP16_SAFETY_CRITICAL_TEST_VERSION = "wca_step16_safety_critical_tests_v2"
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


@dataclass(frozen=True)
class WcaSafetyCriticalTestArea:
    area_id: str
    requirement: str
    test_files: tuple[str, ...]
    mandatory_ci: bool = True


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


WCA_STEP16_SAFETY_CRITICAL_TEST_AREAS: tuple[WcaSafetyCriticalTestArea, ...] = (
    WcaSafetyCriticalTestArea("exact_inventory_11_11_7", "Exact 11 primary, 11 modifier, and seven hard-filter inventory.", ("test_wca_step3_strategy_catalog.py", "test_wca_modifier_inventory.py")),
    WcaSafetyCriticalTestArea("registry_to_class_parity", "Registry entries match executable classes and metadata.", ("test_wca_step3_strategy_catalog.py",)),
    WcaSafetyCriticalTestArea("strategy_isolation", "WCA strategies do not import sibling algorithm state or strategy implementations.", ("test_wca_step4_strategy_isolation.py",)),
    WcaSafetyCriticalTestArea("each_primary_strategy", "Each primary strategy has focused deterministic behavior coverage.", ("test_wca_step3_primary_strategy_validation.py",)),
    WcaSafetyCriticalTestArea("each_modifier", "Each contextual modifier is registered, bounded, and non-voting.", ("test_wca_step4_modifiers_hard_filters.py", "test_wca_modifier_inventory.py")),
    WcaSafetyCriticalTestArea("each_hard_filter", "Each hard filter independently gates entries without blocking protective exits.", ("test_wca_step4_modifiers_hard_filters.py", "test_wca_step10_local_gates.py")),
    WcaSafetyCriticalTestArea("canonical_configuration_and_migration", "Canonical configuration revisions, migrations, restart, and rollback.", ("test_wca_step2_configuration_system.py",)),
    WcaSafetyCriticalTestArea("per_strategy_settings", "Dedicated per-strategy settings are versioned and bound to registry entries.", ("test_wca_step2_configuration_system.py", "test_wca_step3_strategy_catalog.py")),
    WcaSafetyCriticalTestArea("dynamic_settings_and_hard_caps", "Dynamic settings start from baseline and cannot exceed hard caps.", ("test_wca_step8_dynamic_profile.py", "test_wca_step13_dynamic_cost_latency.py")),
    WcaSafetyCriticalTestArea("calibration_leakage", "Calibration cannot use outcomes unavailable at decision time.", ("test_wca_step12_calibration_weights.py", "test_wca_step5_confidence_calibration.py")),
    WcaSafetyCriticalTestArea("weight_leakage_and_normalisation", "Weights use prior data only and normalise active weights.", ("test_wca_step12_calibration_weights.py", "test_wca_step6_performance_weights.py")),
    WcaSafetyCriticalTestArea("family_and_correlation_caps", "Family concentration and correlation controls cap effective weights.", ("test_wca_step12_calibration_weights.py", "test_wca_step19_comprehensive.py")),
    WcaSafetyCriticalTestArea("one_production_pipeline", "Paper/manual/replay/backtest adapters route through one production pipeline.", ("test_wca_step5_production_pipeline.py", "test_wca_paper_execution_pipeline.py")),
    WcaSafetyCriticalTestArea("paper_replay_backtest_parity", "Paper, replay, runtime shadow, and backtest produce matching pre-broker decisions.", ("test_wca_step14_15_backend_backtest.py", "test_wca_step5_production_pipeline.py")),
    WcaSafetyCriticalTestArea("duplicate_finalised_bar_events", "Duplicate completed-bar events are rejected.", ("test_wca_step7_background_runtime.py", "test_wca_step6_inventory_persistence.py")),
    WcaSafetyCriticalTestArea("stale_and_out_of_order_events", "Stale and out-of-order completed-bar events fail closed.", ("test_wca_step7_background_runtime.py",)),
    WcaSafetyCriticalTestArea("queue_backpressure", "Durable queues enforce backpressure.", ("test_wca_step7_background_runtime.py", "test_wca_step8_research_worker.py")),
    WcaSafetyCriticalTestArea("worker_crash_and_restart", "Runtime/research workers recover leased or unfinished work after crash/restart.", ("test_wca_step7_background_runtime.py", "test_wca_step8_research_worker.py")),
    WcaSafetyCriticalTestArea("checkpoint_recovery", "Runtime checkpoints are durable and recovered only after committed work.", ("test_wca_step7_background_runtime.py", "test_wca_step6_inventory_persistence.py")),
    WcaSafetyCriticalTestArea("single_writer_guarantees", "Event claims, checkpoints, and state transitions use single-writer or CAS semantics.", ("test_wca_step6_inventory_persistence.py", "test_wca_step7_background_runtime.py")),
    WcaSafetyCriticalTestArea("global_risk_concurrency", "Shared global risk cannot over-allocate concurrent algorithm proposals.", ("test_wca_step9_global_risk_final_validation.py", "test_wca_step12_global_gate_engine.py")),
    WcaSafetyCriticalTestArea("final_validation_after_overrides", "Final order validation runs after sizing, overrides, caps, rounding, and price construction.", ("test_wca_step9_global_risk_final_validation.py", "test_wca_paper_execution_pipeline.py")),
    WcaSafetyCriticalTestArea("atomic_outbox_reservation", "Decision, order intent, and outbox reservation commit atomically.", ("test_wca_step10_paper_broker_outbox.py", "test_wca_paper_execution_pipeline.py")),
    WcaSafetyCriticalTestArea("broker_timeout_unknown_submission", "Broker timeout creates unknown-submission state and blocks unsafe retry.", ("test_wca_step10_paper_broker_outbox.py",)),
    WcaSafetyCriticalTestArea("duplicate_broker_order_prevention", "Duplicate broker orders are prevented by atomic idempotency.", ("test_wca_step10_paper_broker_outbox.py", "test_wca_step12_global_gate_engine.py")),
    WcaSafetyCriticalTestArea("partial_fills", "Partial and delayed fills are represented without duplicating inventory.", ("test_wca_step10_paper_broker_outbox.py", "test_wca_step14_15_backend_backtest.py")),
    WcaSafetyCriticalTestArea("virtual_inventory_attribution", "WCA virtual inventory and lots maintain algorithm attribution.", ("test_wca_step11_position_management_reconciliation.py", "test_wca_step6_inventory_persistence.py")),
    WcaSafetyCriticalTestArea("cross_algorithm_inventory_isolation", "WCA cannot mutate sibling algorithm inventory.", ("test_wca_step11_position_management_reconciliation.py", "test_wca_step6_inventory_persistence.py")),
    WcaSafetyCriticalTestArea("reconciliation", "Broker reconciliation blocks entries on unexplained discrepancies while preserving attribution.", ("test_wca_broker_reconciliation.py", "test_wca_step11_position_management_reconciliation.py")),
    WcaSafetyCriticalTestArea("unprotected_position_recovery", "Unprotected WCA positions trip circuit breaker and prioritise risk reduction.", ("test_wca_step11_position_management_reconciliation.py",)),
    WcaSafetyCriticalTestArea("protective_exits_during_entry_pauses", "Protective exits remain active while new entries are paused.", ("test_wca_step7_background_runtime.py", "test_wca_step11_position_management_reconciliation.py")),
    WcaSafetyCriticalTestArea("early_market_close", "End-of-day flattening uses official calendar including early closes.", ("test_wca_step11_position_management_reconciliation.py",)),
    WcaSafetyCriticalTestArea("cost_and_net_edge_gates", "Cost model and minimum net-edge gates block uneconomic entries.", ("test_wca_step13_dynamic_cost_latency.py", "test_wca_step14_15_backend_backtest.py")),
    WcaSafetyCriticalTestArea("latency_and_stale_quote_gates", "Latency, processing lag, and stale quotes are observed and gate entries.", ("test_wca_step13_dynamic_cost_latency.py", "test_wca_step9_global_risk_final_validation.py")),
    WcaSafetyCriticalTestArea("api_presentation_only_boundary", "FastAPI is a read/enqueue control surface only.", ("test_wca_step15_api_frontend_control_surface.py",)),
    WcaSafetyCriticalTestArea("frontend_presentation_only_boundary", "Frontend displays and enqueues only; no authoritative WCA calculations or local storage.", ("test_wca_step15_api_frontend_control_surface.py", "test_wca_step18_frontend_presentation.py")),
    WcaSafetyCriticalTestArea("wca_no_ml_dependency", "WCA remains deterministic and does not import ML dependencies.", ("test_wca_step13_ml_forecast_decoupling.py",)),
    WcaSafetyCriticalTestArea("paper_only_enforcement", "WCA real-money execution remains unavailable.", ("test_wca_step20_rollout.py", "test_wca_step21_final_acceptance.py")),
)

WCA_STEP16_SAFETY_CRITICAL_AREA_IDS = frozenset(row.area_id for row in WCA_STEP16_SAFETY_CRITICAL_TEST_AREAS)
WCA_STEP16_SAFETY_CRITICAL_TEST_FILES = tuple(sorted({test_file for row in WCA_STEP16_SAFETY_CRITICAL_TEST_AREAS for test_file in row.test_files}))


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


def wca_step16_safety_critical_test_report() -> dict[str, object]:
    return {
        "algorithm": "wca",
        "coverageVersion": WCA_STEP16_SAFETY_CRITICAL_TEST_VERSION,
        "areas": tuple(row.__dict__ for row in WCA_STEP16_SAFETY_CRITICAL_TEST_AREAS),
        "testFiles": WCA_STEP16_SAFETY_CRITICAL_TEST_FILES,
        "testPresenceProvesPassing": False,
        "passingRequiresPytestExecution": WCA_TEST_SUITE_PASS_REQUIRES_EXECUTION,
    }


__all__ = [
    "WCA_TEST_SUITE_COVERAGE_AREA_IDS",
    "WCA_TEST_SUITE_COVERAGE_INVENTORY",
    "WCA_TEST_SUITE_COVERAGE_VERSION",
    "WCA_TEST_SUITE_PASS_REQUIRES_EXECUTION",
    "WCA_STEP16_SAFETY_CRITICAL_AREA_IDS",
    "WCA_STEP16_SAFETY_CRITICAL_TEST_AREAS",
    "WCA_STEP16_SAFETY_CRITICAL_TEST_FILES",
    "WCA_STEP16_SAFETY_CRITICAL_TEST_VERSION",
    "WCA_STEP19_COVERAGE_CATEGORIES",
    "WCA_STEP19_COVERAGE_VERSION",
    "WCA_VALIDATION_ROLLOUT_FILE_INVENTORY",
    "WCA_VALIDATION_ROLLOUT_FILE_NAMES",
    "WCA_VALIDATION_ROLLOUT_INVENTORY_VERSION",
    "WcaCoverageCategory",
    "WcaSafetyCriticalTestArea",
    "WcaTestSuiteCoverageArea",
    "WcaValidationRolloutFile",
    "wca_step16_safety_critical_test_report",
    "wca_step19_coverage_report",
    "wca_validation_rollout_inventory_report",
]
