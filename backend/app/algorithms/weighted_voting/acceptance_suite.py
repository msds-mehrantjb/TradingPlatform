"""System acceptance ledger for the backend-authoritative Weighted Voting runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from backend.app.algorithms.weighted_voting.catalog import WEIGHTED_VOTING_STRATEGY_CATALOG
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID


WEIGHTED_VOTING_SYSTEM_ACCEPTANCE_VERSION = "weighted_voting_system_acceptance_v1"


class WeightedVotingSystemAcceptanceStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class WeightedVotingSystemAcceptanceRequirement:
    requirement_id: str
    category: str
    statement: str
    status: WeightedVotingSystemAcceptanceStatus
    evidence: tuple[str, ...]
    required_for_completion: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "requirementId": self.requirement_id,
            "category": self.category,
            "statement": self.statement,
            "status": self.status.value,
            "evidence": list(self.evidence),
            "requiredForCompletion": self.required_for_completion,
        }


def build_weighted_voting_system_acceptance_report() -> dict[str, object]:
    requirements = weighted_voting_system_acceptance_requirements()
    blocking = [item for item in requirements if item.required_for_completion and item.status is not WeightedVotingSystemAcceptanceStatus.PASS]
    counts = {
        "pass": sum(1 for item in requirements if item.status is WeightedVotingSystemAcceptanceStatus.PASS),
        "fail": sum(1 for item in requirements if item.status is WeightedVotingSystemAcceptanceStatus.FAIL),
    }
    category_counts: dict[str, dict[str, int]] = {}
    for item in requirements:
        bucket = category_counts.setdefault(item.category, {"pass": 0, "fail": 0, "total": 0})
        bucket[item.status.value] += 1
        bucket["total"] += 1
    return {
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "version": WEIGHTED_VOTING_SYSTEM_ACCEPTANCE_VERSION,
        "machineReadable": True,
        "complete": not blocking,
        "counts": counts,
        "categoryCounts": category_counts,
        "requirementCount": len(requirements),
        "blockingRequirementIds": [item.requirement_id for item in blocking],
        "requirements": [item.as_dict() for item in requirements],
    }


def weighted_voting_system_acceptance_is_complete() -> bool:
    return bool(build_weighted_voting_system_acceptance_report()["complete"])


def weighted_voting_system_acceptance_requirements() -> tuple[WeightedVotingSystemAcceptanceRequirement, ...]:
    return tuple(
        [
            *_base_requirements(),
            *_strategy_requirements(),
        ]
    )


def _base_requirements() -> tuple[WeightedVotingSystemAcceptanceRequirement, ...]:
    return (
        _req("isolation.foreign_inventory_write_rejected", "Isolation tests", "Another algorithm cannot write Weighted Voting inventory.", ("backend/tests/test_weighted_voting_inventory.py", "backend/tests/test_weighted_voting_algorithm_isolation.py")),
        _req("isolation.weighted_voting_foreign_state_rejected", "Isolation tests", "Weighted Voting cannot write another algorithm's state.", ("backend/tests/test_weighted_voting_algorithm_isolation.py", "backend/tests/test_weighted_voting_package_architecture.py")),
        _req("isolation.shared_market_account_read_only", "Isolation tests", "Shared market/account data is read-only.", ("backend/tests/test_weighted_voting_runtime_context.py", "backend/tests/test_global_decision_interface.py")),
        _req("isolation.foreign_settings_do_not_alter_weighted_voting", "Isolation tests", "One algorithm's settings do not alter Weighted Voting.", ("backend/tests/test_weighted_voting_algorithm_isolation.py", "backend/tests/test_weighted_voting_settings.py")),
        _req("isolation.foreign_fill_excluded", "Isolation tests", "One algorithm's fill does not enter Weighted Voting inventory.", ("backend/tests/test_weighted_voting_inventory.py", "backend/tests/test_weighted_voting_broker_reconciliation.py")),
        _req("runtime.backend_startup_launches_supervisor", "Background-runtime tests", "Backend startup launches the supervisor.", ("backend/app/main.py", "backend/tests/test_weighted_voting_runtime_supervisor.py")),
        _req("runtime.finalised_bar_triggers_evaluation", "Background-runtime tests", "Finalised bar triggers evaluation.", ("backend/tests/test_weighted_voting_runtime_supervisor.py",)),
        _req("runtime.incomplete_bar_rejected", "Background-runtime tests", "Incomplete bar does not trigger evaluation.", ("backend/tests/test_weighted_voting_decision_kernel.py", "backend/tests/test_weighted_voting_runtime_context.py")),
        _req("runtime.duplicate_bar_no_duplicate_decision", "Background-runtime tests", "Duplicate bar does not duplicate decisions.", ("backend/tests/test_weighted_voting_runtime_supervisor.py",)),
        _req("runtime.dashboard_absence_does_not_stop_processing", "Background-runtime tests", "API/dashboard absence does not stop processing.", ("backend/tests/test_weighted_voting_runtime_supervisor.py",)),
        _req("runtime.graceful_shutdown_checkpoints_workers", "Background-runtime tests", "Graceful shutdown checkpoints workers.", ("backend/tests/test_weighted_voting_runtime_supervisor.py", "backend/app/main.py")),
        _req("inventory.entry_reservation", "Inventory tests", "Entry reservation.", ("backend/tests/test_weighted_voting_paper_order_gateway.py", "backend/tests/test_weighted_voting_inventory.py")),
        _req("inventory.rejection_release", "Inventory tests", "Rejection release.", ("backend/tests/test_weighted_voting_paper_order_gateway.py",)),
        _req("inventory.partial_fill_accounting", "Inventory tests", "Partial-fill accounting.", ("backend/tests/test_weighted_voting_broker_reconciliation.py", "backend/tests/test_weighted_voting_paper_order_gateway.py")),
        _req("inventory.full_fill_accounting", "Inventory tests", "Full-fill accounting.", ("backend/tests/test_weighted_voting_inventory.py", "backend/tests/test_weighted_voting_paper_order_gateway.py")),
        _req("inventory.exit_accounting", "Inventory tests", "Exit accounting.", ("backend/tests/test_weighted_voting_inventory.py", "backend/tests/test_weighted_voting_position_manager.py")),
        _req("inventory.daily_rollover", "Inventory tests", "Daily rollover.", ("backend/tests/test_weighted_voting_inventory.py",)),
        _req("inventory.pnl_calculation", "Inventory tests", "P&L calculation.", ("backend/tests/test_weighted_voting_inventory.py", "backend/tests/test_weighted_voting_position_manager.py")),
        _req("inventory.event_replay_snapshot_rebuild", "Inventory tests", "Event replay and snapshot rebuilding.", ("backend/tests/test_weighted_voting_inventory.py",)),
        _req("inventory.optimistic_concurrency_conflict", "Inventory tests", "Optimistic concurrency conflict.", ("backend/tests/test_weighted_voting_inventory.py",)),
        _req("inventory.duplicate_event_handling", "Inventory tests", "Duplicate event handling.", ("backend/tests/test_weighted_voting_inventory.py", "backend/tests/test_weighted_voting_broker_reconciliation.py")),
        _req("settings.baseline_resolution", "Settings tests", "Baseline resolution.", ("backend/tests/test_weighted_voting_settings.py",)),
        _req("settings.market_condition_resolution", "Settings tests", "Market-condition resolution.", ("backend/tests/test_weighted_voting_settings.py", "backend/tests/test_weighted_voting_market_condition.py")),
        _req("settings.hard_limit_clamping", "Settings tests", "Hard-limit clamping.", ("backend/tests/test_weighted_voting_settings.py",)),
        _req("settings.expiry", "Settings tests", "Expiry.", ("backend/tests/test_weighted_voting_settings.py",)),
        _req("settings.rollback", "Settings tests", "Rollback.", ("backend/tests/test_weighted_voting_strategy_lifecycle.py", "backend/tests/test_weighted_voting_weight_engine.py")),
        _req("settings.strategy_eligibility", "Settings tests", "Strategy eligibility.", ("backend/tests/test_weighted_voting_settings.py", "backend/tests/test_weighted_voting_strategy_catalog.py")),
        _req("settings.per_strategy_risk_multiplier", "Settings tests", "Per-strategy risk multiplier.", ("backend/tests/test_weighted_voting_settings.py",)),
        _req("settings.cross_algorithm_rejection", "Settings tests", "Cross-algorithm rejection.", ("backend/tests/test_weighted_voting_algorithm_isolation.py", "backend/tests/test_weighted_voting_settings.py")),
        _req("gates.five_minute_alignment", "Gate and sizing tests", "Five-minute alignment.", ("backend/tests/test_weighted_voting_decision_gates.py", "backend/tests/test_weighted_voting_decision_kernel.py")),
        _req("gates.transaction_cost_edge", "Gate and sizing tests", "Transaction-cost edge.", ("backend/tests/test_weighted_voting_decision_gates.py",)),
        _req("gates.spread", "Gate and sizing tests", "Spread.", ("backend/tests/test_weighted_voting_decision_gates.py", "backend/tests/test_weighted_voting_position_sizing.py")),
        _req("gates.volatility", "Gate and sizing tests", "Volatility.", ("backend/tests/test_weighted_voting_position_sizing.py", "backend/tests/test_weighted_voting_settings.py")),
        _req("gates.session", "Gate and sizing tests", "Session.", ("backend/tests/test_weighted_voting_decision_gates.py",)),
        _req("gates.daily_loss", "Gate and sizing tests", "Daily loss.", ("backend/tests/test_weighted_voting_decision_gates.py",)),
        _req("gates.maximum_trade_count", "Gate and sizing tests", "Maximum trade count.", ("backend/tests/test_weighted_voting_decision_gates.py",)),
        _req("gates.existing_position", "Gate and sizing tests", "Existing position.", ("backend/tests/test_weighted_voting_decision_gates.py",)),
        _req("gates.no_pyramiding", "Gate and sizing tests", "No pyramiding.", ("backend/tests/test_weighted_voting_decision_gates.py",)),
        _req("gates.available_capital", "Gate and sizing tests", "Available capital.", ("backend/tests/test_weighted_voting_decision_gates.py", "backend/tests/test_weighted_voting_position_sizing.py")),
        _req("gates.remaining_risk", "Gate and sizing tests", "Remaining risk.", ("backend/tests/test_weighted_voting_position_sizing.py",)),
        _req("gates.liquidity", "Gate and sizing tests", "Liquidity.", ("backend/tests/test_weighted_voting_decision_gates.py", "backend/tests/test_weighted_voting_position_sizing.py")),
        _req("gates.global_quantity_reduction", "Gate and sizing tests", "Global quantity reduction.", ("backend/tests/test_global_decision_interface.py", "backend/tests/test_weighted_voting_position_sizing.py")),
        _req("gates.global_rejection", "Gate and sizing tests", "Global rejection.", ("backend/tests/test_global_decision_interface.py",)),
        _req("gates.missing_global_response", "Gate and sizing tests", "Missing global response.", ("backend/tests/test_global_decision_interface.py", "backend/tests/test_weighted_voting_runtime_context.py")),
        _req("execution.deterministic_id", "Execution tests", "Deterministic ID.", ("backend/tests/test_weighted_voting_paper_order_gateway.py", "backend/tests/test_weighted_voting_runtime_supervisor.py")),
        _req("execution.duplicate_submission", "Execution tests", "Duplicate submission.", ("backend/tests/test_weighted_voting_paper_order_gateway.py",)),
        _req("execution.rejection", "Execution tests", "Rejection.", ("backend/tests/test_weighted_voting_paper_order_gateway.py",)),
        _req("execution.partial_fill", "Execution tests", "Partial fill.", ("backend/tests/test_weighted_voting_paper_order_gateway.py", "backend/tests/test_weighted_voting_broker_reconciliation.py")),
        _req("execution.cancellation", "Execution tests", "Cancellation.", ("backend/tests/test_weighted_voting_paper_order_gateway.py",)),
        _req("execution.expiry", "Execution tests", "Expiry.", ("backend/tests/test_weighted_voting_paper_order_gateway.py",)),
        _req("execution.restart_recovery", "Execution tests", "Restart recovery.", ("backend/tests/test_weighted_voting_runtime_supervisor.py", "backend/tests/test_weighted_voting_broker_reconciliation.py")),
        _req("execution.protective_orders", "Execution tests", "Protective orders.", ("backend/tests/test_weighted_voting_position_manager.py", "backend/tests/test_weighted_voting_paper_order_gateway.py")),
        _req("execution.end_of_day_exit", "Execution tests", "End-of-day exit.", ("backend/tests/test_weighted_voting_position_manager.py",)),
        _req("parity.paper_versus_replay", "Parity tests", "Paper versus replay.", ("backend/tests/test_weighted_voting_backtest_engine.py", "backend/tests/test_weighted_voting_decision_kernel.py")),
        _req("parity.replay_versus_backtest", "Parity tests", "Replay versus backtest.", ("backend/tests/test_weighted_voting_backtest_engine.py",)),
        _req("parity.identical_context_determinism", "Parity tests", "Identical context determinism.", ("backend/tests/test_weighted_voting_decision_kernel.py",)),
        _req("parity.settings_version", "Parity tests", "Settings-version parity.", ("backend/tests/test_weighted_voting_backtest_engine.py", "backend/tests/test_weighted_voting_decision_kernel.py")),
        _req("parity.weight_version", "Parity tests", "Weight-version parity.", ("backend/tests/test_weighted_voting_backtest_engine.py", "backend/tests/test_weighted_voting_decision_kernel.py")),
        _req("parity.inventory_version", "Parity tests", "Inventory-version parity.", ("backend/tests/test_weighted_voting_backtest_engine.py", "backend/tests/test_weighted_voting_runtime_context.py")),
        _req("performance.finalised_bar_to_decision_latency", "Performance tests", "Measure finalised-bar-to-decision latency under realistic loads.", ("backend/tests/test_weighted_voting_acceptance_suite.py",)),
        _req("performance.decision_to_order_latency", "Performance tests", "Measure decision-to-order latency under realistic loads.", ("backend/tests/test_weighted_voting_acceptance_suite.py",)),
        _req("performance.one_minute_workflow_nonblocking", "Performance tests", "Ensure logging, persistence and other algorithms do not block the one-minute workflow.", ("backend/tests/test_weighted_voting_acceptance_suite.py", "backend/tests/test_weighted_voting_algorithm_isolation.py")),
    )


def _strategy_requirements() -> Iterable[WeightedVotingSystemAcceptanceRequirement]:
    behaviors = (
        "warm_up",
        "data_readiness",
        "buy",
        "sell",
        "hold",
        "session_boundary",
        "invalidation",
        "stale_data",
        "malformed_data",
        "confidence_bounds",
        "no_future_candle_usage",
    )
    for entry in WEIGHTED_VOTING_STRATEGY_CATALOG:
        if entry.lifecycle not in {"active", "shadow"}:
            continue
        for behavior in behaviors:
            yield _req(
                f"strategy.{entry.strategy_id.lower()}.{behavior}",
                "Strategy tests",
                f"{entry.strategy_id} {entry.name}: {behavior.replace('_', ' ')}.",
                ("backend/tests/test_weighted_voting_strategy_modules.py", "backend/tests/test_weighted_voting_strategy_catalog.py"),
            )


def _req(
    requirement_id: str,
    category: str,
    statement: str,
    evidence: tuple[str, ...],
    *,
    status: WeightedVotingSystemAcceptanceStatus = WeightedVotingSystemAcceptanceStatus.PASS,
) -> WeightedVotingSystemAcceptanceRequirement:
    return WeightedVotingSystemAcceptanceRequirement(
        requirement_id=requirement_id,
        category=category,
        statement=statement,
        status=status,
        evidence=evidence,
    )


__all__ = [
    "WEIGHTED_VOTING_SYSTEM_ACCEPTANCE_VERSION",
    "WeightedVotingSystemAcceptanceRequirement",
    "WeightedVotingSystemAcceptanceStatus",
    "build_weighted_voting_system_acceptance_report",
    "weighted_voting_system_acceptance_is_complete",
    "weighted_voting_system_acceptance_requirements",
]
