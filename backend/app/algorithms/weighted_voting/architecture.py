"""Authoritative architecture contract for Weighted Voting."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID


WEIGHTED_VOTING_ARCHITECTURE_VERSION: Final[str] = "weighted_voting_architecture_v1"
WEIGHTED_VOTING_AUTHORITATIVE_PACKAGE: Final[str] = "backend.app.algorithms.weighted_voting"
WEIGHTED_VOTING_DECISION_KERNEL: Final[str] = "backend.app.algorithms.weighted_voting.service.WeightedVotingService.evaluate_context"
WEIGHTED_VOTING_BACKTEST_KERNEL: Final[str] = "backend.app.algorithms.weighted_voting.backtest.engine.run_weighted_voting_backtest"
WEIGHTED_VOTING_STORAGE_NAMESPACE: Final[str] = "weighted_voting.*"
WEIGHTED_VOTING_FILESYSTEM_ROOT: Final[str] = "data/algorithms/weighted_voting"
WEIGHTED_VOTING_CAPITAL_PARTITION_ID: Final[str] = "weighted_voting.paper.default"


@dataclass(frozen=True)
class WeightedVotingPipelineBoundary:
    stage_id: str
    owner: str
    authoritative_module: str
    mutable_state_scope: str
    required_inputs: tuple[str, ...]
    fail_closed_rule: str


@dataclass(frozen=True)
class WeightedVotingReadOnlyPort:
    port_id: str
    provider: str
    access: str
    mutable_state_allowed: bool = False
    client_request_may_create_global_risk_decision: bool = False


@dataclass(frozen=True)
class WeightedVotingArchitectureContract:
    algorithm_id: str
    version: str
    authoritative_package: str
    authoritative_runtime: str
    api_role: str
    worker_role: str
    decision_kernel: str
    backtest_kernel: str
    supported_modes: tuple[str, ...]
    live_money_trading_allowed: bool
    machine_learning_allowed: bool
    broker_account_role: str
    inventory_owner: str
    capital_partition_id: str
    storage_namespace: str
    filesystem_root: str
    fail_closed_on_missing_safety_inputs: bool
    global_risk_decisions_are_external_inputs: bool
    shared_ports: tuple[WeightedVotingReadOnlyPort, ...]
    pipeline_boundaries: tuple[WeightedVotingPipelineBoundary, ...]
    owned_mutable_domains: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


WEIGHTED_VOTING_OWNED_MUTABLE_DOMAINS: Final[tuple[str, ...]] = (
    "strategy_catalogue",
    "strategy_implementations",
    "signal_state",
    "weight_state",
    "configuration",
    "dynamic_profiles",
    "inventory",
    "capital_partition",
    "orders",
    "fills",
    "positions",
    "trades",
    "pnl",
    "backtests",
    "performance_history",
    "execution_attribution",
)

WEIGHTED_VOTING_SHARED_READ_ONLY_PORTS: Final[tuple[WeightedVotingReadOnlyPort, ...]] = (
    WeightedVotingReadOnlyPort(
        port_id="market_data",
        provider="shared_market_data_service",
        access="completed_candles_quotes_and_read_only_market_observations",
    ),
    WeightedVotingReadOnlyPort(
        port_id="local_paper_account_observation",
        provider="weighted_voting_local_paper_inventory",
        access="read_only_equity_buying_power_positions_orders_fills_from_weighted_voting_inventory",
    ),
    WeightedVotingReadOnlyPort(
        port_id="clock",
        provider="shared_clock",
        access="read_only_exchange_and_wall_clock_time",
    ),
    WeightedVotingReadOnlyPort(
        port_id="logging",
        provider="shared_logging",
        access="append_only_algorithm_tagged_observability",
    ),
    WeightedVotingReadOnlyPort(
        port_id="global_risk",
        provider="central_risk_service",
        access="external_allow_reduce_or_reject_response_to_weighted_voting_proposal",
    ),
)

WEIGHTED_VOTING_PIPELINE_BOUNDARIES: Final[tuple[WeightedVotingPipelineBoundary, ...]] = (
    WeightedVotingPipelineBoundary(
        stage_id="market_data_input",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.market_snapshot",
        mutable_state_scope="none_read_only_port_input",
        required_inputs=("symbol", "completed_one_minute_candles", "quote_snapshot", "data_timestamp"),
        fail_closed_rule="missing_stale_malformed_or_foreign_algorithm_fields_are_ignored_or_hold",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="finalised_one_minute_bar_events",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.runtime.worker",
        mutable_state_scope="weighted_voting.runtime_status",
        required_inputs=("completed_one_minute_candle", "idempotency_key"),
        fail_closed_rule="incomplete_one_minute_candles_are_never_evaluated",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="five_minute_confirmation_data",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.decision_gates",
        mutable_state_scope="weighted_voting.local_gate_results",
        required_inputs=("last_five_completed_one_minute_candles", "proposed_side"),
        fail_closed_rule="unavailable_confirmation_blocks_or_reduces_entry",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="strategy_evaluation",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.signal_engine",
        mutable_state_scope="weighted_voting.strategy_signals",
        required_inputs=("market_snapshot", "weighted_voting_config", "weighted_voting_weight_state"),
        fail_closed_rule="strategy_missing_data_returns_hold_signal",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="market_condition_classification",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.market_condition",
        mutable_state_scope="weighted_voting.market_condition_statistics",
        required_inputs=("market_snapshot", "previous_weighted_voting_condition"),
        fail_closed_rule="conflicting_or_low_quality_condition_classifies_as_avoid_or_hold",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="dynamic_settings_resolution",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.dynamic_settings",
        mutable_state_scope="weighted_voting.dynamic_settings",
        required_inputs=("weighted_voting_defaults", "weighted_voting_dynamic_profile", "market_condition"),
        fail_closed_rule="invalid_dynamic_settings_are_clamped_or_rejected",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="weight_loading",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.weight_engine",
        mutable_state_scope="weighted_voting.weights",
        required_inputs=("active_weight_state", "weighted_voting_strategy_outcomes"),
        fail_closed_rule="foreign_or_invalid_weight_state_is_rejected",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="aggregation",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.aggregation",
        mutable_state_scope="weighted_voting.decisions",
        required_inputs=("weighted_voting_strategy_signals", "active_weight_state"),
        fail_closed_rule="insufficient_active_weight_or_tie_returns_hold",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="local_gates",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.decision_gates",
        mutable_state_scope="weighted_voting.local_gate_results",
        required_inputs=("decision", "signals", "market_snapshot", "five_minute_alignment", "costs"),
        fail_closed_rule="missing_safety_inputs_reject_new_entry_or_hold",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="algorithm_inventory",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.position_trade_state",
        mutable_state_scope="weighted_voting.positions",
        required_inputs=("weighted_voting_orders", "weighted_voting_fills", "weighted_voting_trades"),
        fail_closed_rule="foreign_position_or_missing_ownership_is_rejected",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="position_sizing",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.position_sizing",
        mutable_state_scope="weighted_voting.sizing_results",
        required_inputs=("decision", "effective_settings", "market_snapshot", "weighted_voting_inventory", "risk_budget"),
        fail_closed_rule="missing_local_inventory_capacity_rejects_new_entry",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="global_risk_request",
        owner="central_risk_service",
        authoritative_module="backend.app.algorithms.weighted_voting.global_interface",
        mutable_state_scope="weighted_voting.global_gate_applications",
        required_inputs=("weighted_voting_order_proposal", "central_risk_response"),
        fail_closed_rule="missing_or_client_created_global_risk_decision_is_rejected",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="paper_order_execution",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.execution_gateway",
        mutable_state_scope="weighted_voting.orders",
        required_inputs=("accepted_weighted_voting_order_proposal", "idempotency_key", "weighted_voting_local_paper_inventory"),
        fail_closed_rule="paper_submission_disabled_until_rollout_gates_pass",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="order_fill_reconciliation",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.local_paper_broker",
        mutable_state_scope="weighted_voting.fills",
        required_inputs=("local_paper_order", "local_paper_fill", "weighted_voting_client_order_id"),
        fail_closed_rule="foreign_or_unattributed_local_paper_activity_pauses_new_entries",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="position_lifecycle",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.position_manager",
        mutable_state_scope="weighted_voting.positions",
        required_inputs=("weighted_voting_fill", "weighted_voting_order_state"),
        fail_closed_rule="position_mutation_requires_weighted_voting_ownership",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="trade_closing",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.position_manager",
        mutable_state_scope="weighted_voting.trades",
        required_inputs=("weighted_voting_position", "exit_signal", "broker_fill"),
        fail_closed_rule="foreign_position_close_is_rejected",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="performance_attribution",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.performance_tracker",
        mutable_state_scope="weighted_voting.performance",
        required_inputs=("weighted_voting_trades", "weighted_voting_strategy_signals", "weight_version"),
        fail_closed_rule="foreign_trade_or_signal_attribution_is_rejected",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="after_market_weight_updates",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.scheduler",
        mutable_state_scope="weighted_voting.weights",
        required_inputs=("completed_session_dataset", "weighted_voting_outcomes", "previous_weight_state"),
        fail_closed_rule="intraday_or_incomplete_dataset_preserves_previous_weights",
    ),
    WeightedVotingPipelineBoundary(
        stage_id="backtesting_and_replay",
        owner=WEIGHTED_VOTING_ALGORITHM_ID,
        authoritative_module="backend.app.algorithms.weighted_voting.backtest.engine",
        mutable_state_scope="weighted_voting.backtests",
        required_inputs=("historical_completed_candles", "weighted_voting_config", "initial_weight_state"),
        fail_closed_rule="invalid_history_or_lookahead_risk_blocks_run",
    ),
)


def weighted_voting_architecture_contract() -> WeightedVotingArchitectureContract:
    return WeightedVotingArchitectureContract(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        version=WEIGHTED_VOTING_ARCHITECTURE_VERSION,
        authoritative_package=WEIGHTED_VOTING_AUTHORITATIVE_PACKAGE,
        authoritative_runtime="backend_python",
        api_role="configuration_status_inspection_pause_resume_and_manual_paper_testing_only",
        worker_role="background_workers_trigger_one_minute_evaluation_order_submission_exit_and_reconciliation",
        decision_kernel=WEIGHTED_VOTING_DECISION_KERNEL,
        backtest_kernel=WEIGHTED_VOTING_BACKTEST_KERNEL,
        supported_modes=("backtesting", "replay", "shadow_evaluation", "automatic_paper_trading"),
        live_money_trading_allowed=False,
        machine_learning_allowed=False,
        broker_account_role="not_used_for_local_paper; weighted_voting_inventory_is_account_authority",
        inventory_owner=WEIGHTED_VOTING_ALGORITHM_ID,
        capital_partition_id=WEIGHTED_VOTING_CAPITAL_PARTITION_ID,
        storage_namespace=WEIGHTED_VOTING_STORAGE_NAMESPACE,
        filesystem_root=WEIGHTED_VOTING_FILESYSTEM_ROOT,
        fail_closed_on_missing_safety_inputs=True,
        global_risk_decisions_are_external_inputs=True,
        shared_ports=WEIGHTED_VOTING_SHARED_READ_ONLY_PORTS,
        pipeline_boundaries=WEIGHTED_VOTING_PIPELINE_BOUNDARIES,
        owned_mutable_domains=WEIGHTED_VOTING_OWNED_MUTABLE_DOMAINS,
    )


def weighted_voting_architecture_status() -> dict[str, object]:
    contract = weighted_voting_architecture_contract()
    return {
        **contract.as_dict(),
        "status": "authoritative",
        "reasonCodes": ("weighted_voting.architecture.authoritative",),
    }
