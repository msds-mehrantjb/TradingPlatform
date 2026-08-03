"""Durable WCA repository backed by the existing SQLite database."""

from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol

from pydantic import ValidationError

from backend.app.algorithms.wca.contracts import (
    WCA_ALGORITHM_ID,
    BacktestResult,
    ProposedOrder,
    WcaBrokerReconciliationResult,
    WcaBaselineSettings,
    WcaConfidenceCalibrationTable,
    WcaDecision,
    WcaEffectiveSettings,
    WcaMarketStatus,
    WcaOrderStatus,
    WcaOrderValidationContext,
    WcaPaperStabilityValidationResult,
    WcaShadowComparisonEvidence,
    WcaStrategyPerformanceRecord,
    WcaWeightSnapshot,
    coerce_wca_order_status,
    validate_wca_order_state_transition,
)
from backend.app.algorithms.wca.configuration import (
    WcaConfiguration,
    WcaConfigurationLifecycle,
    canonical_configuration_from_legacy,
    default_wca_configuration,
    validate_wca_configuration,
)
from backend.app.algorithms.wca.position_management import WcaManagedPosition
from backend.app.algorithms.wca.runtime_control import WcaRuntimeControl, default_wca_runtime_control
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY
from backend.app.algorithms.wca.weights import adapt_v1_weight_snapshot_to_multipliers
from backend.app.config import get_settings
from backend.app.database import _sqlite_path
from backend.app.gates import BrokerAccountSnapshot

WCA_PERSISTENCE_MIGRATION_VERSION = "wca_authoritative_persistence_005"
WCA_IGNORED_LOCAL_STORAGE_KEYS = frozenset(
    {
        "weighted-confidence-decision-settings-v1",
        "weighted-confidence-trading-settings-v1",
        "weighted-confidence-target-order-overrides-v1",
        "confidence-backtest-result-v1",
        "wca-backtest-result-v1",
        "confidence-trade-history-v1",
        "confidence-order-control-modes-v1",
        "confidence-order-control-overrides-v1",
        "confidence-auto-submitted-order-keys-v1",
    }
)
WCA_ALLOWED_LOCAL_STORAGE_PREFIXES = ("ui-", "display-", "chart-", "panel-", "tab-")
WCA_INVENTORY_EVENT_TYPES = frozenset(
    {
        "ORDER_INTENT_RESERVED",
        "ORDER_SUBMITTED",
        "ORDER_ACKNOWLEDGED",
        "ORDER_REJECTED",
        "ORDER_CANCELLED",
        "PARTIAL_FILL_RECEIVED",
        "FILL_RECEIVED",
        "POSITION_OPENED",
        "POSITION_INCREASED",
        "POSITION_REDUCED",
        "POSITION_CLOSED",
        "PROTECTIVE_ORDER_CREATED",
        "PROTECTIVE_ORDER_REPLACED",
        "RISK_RESERVED",
        "RISK_RELEASED",
        "DAILY_STATE_RESET",
        "RECONCILIATION_CORRECTION",
        "END_OF_SESSION_FLATTEN",
    }
)


@dataclass(frozen=True)
class WcaPersistenceRecordDefinition:
    record_id: str
    table_name: str
    responsibility: str


WCA_PERSISTENCE_RECORD_INVENTORY: tuple[WcaPersistenceRecordDefinition, ...] = (
    WcaPersistenceRecordDefinition("configuration_versions", "wca_configuration_versions", "Versioned WCA configuration records."),
    WcaPersistenceRecordDefinition("active_configuration", "wca_active_configuration", "Single active WCA configuration pointer."),
    WcaPersistenceRecordDefinition("strategy_settings_versions", "wca_strategy_settings_versions", "Versioned WCA per-strategy settings records."),
    WcaPersistenceRecordDefinition("calibration_tables", "wca_confidence_calibrations", "Versioned WCA calibration table records."),
    WcaPersistenceRecordDefinition("weight_snapshots", "wca_weight_snapshots", "Versioned WCA strategy-weight snapshots."),
    WcaPersistenceRecordDefinition("finalized_bar_event_receipts", "wca_finalized_bar_event_receipts", "WCA finalised-bar event claim receipts."),
    WcaPersistenceRecordDefinition("runtime_checkpoints", "wca_runtime_checkpoints", "WCA runtime checkpoint state."),
    WcaPersistenceRecordDefinition("runtime_event_queue", "wca_runtime_event_queue", "WCA durable finalized-bar event queue."),
    WcaPersistenceRecordDefinition("runtime_command_queue", "wca_runtime_command_queue", "WCA durable runtime command queue."),
    WcaPersistenceRecordDefinition("runtime_symbol_leases", "wca_runtime_symbol_leases", "WCA per-symbol single-writer runtime leases."),
    WcaPersistenceRecordDefinition("market_status_history", "wca_market_status_snapshots", "WCA market-status history."),
    WcaPersistenceRecordDefinition("effective_settings", "wca_effective_setting_snapshots", "WCA dynamic-profile and effective-settings history."),
    WcaPersistenceRecordDefinition("strategy_evaluations", "wca_strategy_evaluations", "WCA strategy evaluation records."),
    WcaPersistenceRecordDefinition("modifier_evaluations", "wca_modifier_evaluations", "WCA modifier evaluation records."),
    WcaPersistenceRecordDefinition("local_gate_results", "wca_local_gate_evaluations", "WCA local-gate and hard-filter result records."),
    WcaPersistenceRecordDefinition("global_risk_responses", "wca_global_risk_responses", "WCA-attributed shared global-risk response records."),
    WcaPersistenceRecordDefinition("decisions", "wca_decisions", "WCA decision snapshots."),
    WcaPersistenceRecordDefinition("order_intents", "wca_order_intents", "WCA order-intent reservations."),
    WcaPersistenceRecordDefinition("execution_outbox_records", "wca_execution_outbox", "WCA execution outbox records."),
    WcaPersistenceRecordDefinition("broker_orders", "wca_broker_orders", "WCA-attributed broker order records."),
    WcaPersistenceRecordDefinition("wca_attributed_orders", "wca_attributed_orders", "WCA-attributed order records."),
    WcaPersistenceRecordDefinition("wca_attributed_fills", "wca_attributed_fills", "WCA-attributed fill records."),
    WcaPersistenceRecordDefinition("wca_owned_lots", "wca_owned_lots", "WCA-owned lot records."),
    WcaPersistenceRecordDefinition("wca_positions", "wca_positions", "WCA-attributed position records."),
    WcaPersistenceRecordDefinition("wca_virtual_positions", "wca_virtual_positions", "WCA virtual position records."),
    WcaPersistenceRecordDefinition("wca_trades", "wca_trade_ledger", "WCA trade ledger records."),
    WcaPersistenceRecordDefinition("inventory_event_ledger", "wca_inventory_ledger", "Append-only WCA inventory event ledger."),
    WcaPersistenceRecordDefinition("inventory_projection", "wca_inventory_projection", "Restartable WCA inventory projection by account and symbol."),
    WcaPersistenceRecordDefinition("daily_state_projection", "wca_daily_state", "Authoritative WCA daily-state projection by account, symbol, and session."),
    WcaPersistenceRecordDefinition("broker_account_snapshots", "wca_broker_account_snapshots", "WCA-owned broker account observations for runtime decisions."),
    WcaPersistenceRecordDefinition("exit_state", "wca_exit_state", "WCA exit-state records."),
    WcaPersistenceRecordDefinition("reconciliation_results", "wca_broker_reconciliations", "WCA reconciliation result records."),
    WcaPersistenceRecordDefinition("runtime_health", "wca_runtime_health", "WCA runtime health records."),
    WcaPersistenceRecordDefinition("runtime_control", "wca_runtime_control", "WCA backend-authoritative paper runtime control."),
    WcaPersistenceRecordDefinition("runtime_latency_observations", "wca_runtime_latency_observations", "WCA durable component latency measurements."),
    WcaPersistenceRecordDefinition("runtime_latency_summaries", "wca_runtime_latency_summaries", "WCA persisted latency percentiles and failure counts."),
    WcaPersistenceRecordDefinition("background_jobs", "wca_background_jobs", "WCA background job records."),
    WcaPersistenceRecordDefinition("research_candidates", "wca_research_candidates", "WCA research-generated candidate records awaiting promotion."),
    WcaPersistenceRecordDefinition("backtest_runs", "wca_backtest_runs", "WCA backtest run records."),
    WcaPersistenceRecordDefinition("backtest_results", "wca_backtest_results", "WCA backtest result payloads."),
    WcaPersistenceRecordDefinition("shadow_comparison_records", "wca_shadow_comparison_evidence", "WCA shadow-comparison evidence records."),
    WcaPersistenceRecordDefinition("paper_stability_evidence", "wca_paper_stability_validations", "WCA paper-stability validation evidence."),
    WcaPersistenceRecordDefinition("rollout_evidence", "wca_rollout_evidence", "WCA rollout evidence records."),
    WcaPersistenceRecordDefinition("rollout_status", "wca_rollout_status", "WCA rollout status records."),
)

WCA_PERSISTENCE_RECORD_IDS = frozenset(record.record_id for record in WCA_PERSISTENCE_RECORD_INVENTORY)
WCA_PERSISTENCE_TABLES = tuple(record.table_name for record in WCA_PERSISTENCE_RECORD_INVENTORY)


class WcaRepository(Protocol):
    def initialize_defaults(self, *, symbol: str, configuration: dict[str, Any], weight_snapshot: WcaWeightSnapshot, engine_version: str) -> None:
        ...

    def read_runtime_control(self, *, broker_account_id: str = "paper", symbol: str = "SPY") -> WcaRuntimeControl:
        ...

    def write_runtime_control(self, control: WcaRuntimeControl) -> WcaRuntimeControl:
        ...

    def save_configuration(self, payload: dict[str, Any], *, symbol: str, timestamp: str | None = None, engine_version: str) -> None:
        ...

    def save_candidate_configuration(self, configuration: WcaConfiguration, *, symbol: str = "SPY", engine_version: str) -> WcaConfiguration:
        ...

    def validate_configuration_revision(self, configuration: WcaConfiguration | dict[str, Any]) -> WcaConfiguration:
        ...

    def activate_configuration_version(self, configuration_version: str) -> WcaConfiguration:
        ...

    def activate_configuration_version_at_candle_boundary(self, configuration_version: str, *, candle_timestamp: datetime) -> WcaConfiguration:
        ...

    def read_active_configuration(self) -> WcaConfiguration | None:
        ...

    def read_configuration_by_version(self, configuration_version: str) -> WcaConfiguration | None:
        ...

    def rollback_configuration(self, configuration_version: str) -> WcaConfiguration:
        ...

    def read_active_weights(self, *, as_of: datetime | None = None) -> WcaWeightSnapshot | None:
        ...

    def save_weight_snapshot(self, snapshot: WcaWeightSnapshot, *, symbol: str, configuration_version: str, engine_version: str, run_id: str = "wca-active-weights") -> None:
        ...

    def read_active_confidence_calibrations(self, *, symbol: str = "SPY", as_of: datetime | None = None, max_age_days: int | None = None) -> tuple[WcaConfidenceCalibrationTable, ...]:
        ...

    def save_confidence_calibration(self, calibration: WcaConfidenceCalibrationTable, *, symbol: str, configuration_version: str, engine_version: str) -> None:
        ...

    def save_strategy_performance_records(self, records: tuple[WcaStrategyPerformanceRecord, ...], *, symbol: str, configuration_version: str, engine_version: str, run_id: str = "wca-performance-records") -> None:
        ...

    def load_strategy_performance_records(self, *, symbol: str = "SPY", as_of: datetime | None = None) -> tuple[WcaStrategyPerformanceRecord, ...]:
        ...

    def write_decision_snapshot(self, decision: WcaDecision, *, run_id: str | None = None) -> None:
        ...

    def claim_finalized_bar_event(self, *, event_id: str, account_id: str, symbol: str, event_timestamp: datetime, payload: dict[str, Any], configuration_version: str = "", run_id: str = "") -> bool:
        ...

    def compare_and_swap_runtime_checkpoint(self, *, checkpoint_key: str, expected_version: int | None, payload: dict[str, Any], account_id: str = "paper", symbol: str = "SPY", configuration_version: str = "", run_id: str = "") -> bool:
        ...

    def create_execution_outbox_record(self, decision: WcaDecision, *, account_id: str, idempotency_key: str, payload: dict[str, Any] | None = None, final_validation_context: WcaOrderValidationContext | None = None) -> bool:
        ...

    def reserve_decision_order_and_outbox(
        self,
        decision: WcaDecision,
        *,
        run_id: str,
        account_id: str,
        idempotency_key: str,
        client_order_id: str,
        request_payload: dict[str, Any],
        final_validation_context: WcaOrderValidationContext,
        global_risk_approval: WcaGlobalRiskApprovalRecord | None = None,
        authoritative_state_hash: str = "",
    ) -> WcaOutboxReservation:
        ...

    def claim_next_execution_outbox(self, *, owner_id: str) -> WcaExecutionOutboxRecord | None:
        ...

    def list_execution_outbox_records(self, *, account_id: str | None = None) -> tuple[WcaExecutionOutboxRecord, ...]:
        ...

    def update_execution_outbox_state(self, *, outbox_id: str, status: WcaOrderStatus | str, response_payload: dict[str, Any] | None = None, error_payload: dict[str, Any] | None = None) -> bool:
        ...

    def read_global_risk_approval(self, *, decision_id: str) -> WcaGlobalRiskApprovalRecord | None:
        ...

    def record_broker_order(self, decision: WcaDecision, *, broker_order_id: str, account_id: str, idempotency_key: str, status: str, payload: dict[str, Any] | None = None) -> bool:
        ...

    def apply_fill_and_update_position(self, decision: WcaDecision, *, fill_id: str, account_id: str, quantity: int, broker_order_id: str | None = None, payload: dict[str, Any] | None = None) -> bool:
        ...

    def record_inventory_event(self, event: WcaInventoryLedgerEvent) -> bool:
        ...

    def read_inventory_projection(self, *, algorithm_id: str, broker_account_id: str, symbol: str) -> WcaInventoryProjection:
        ...

    def read_daily_state_projection(self, *, algorithm_id: str, broker_account_id: str, symbol: str, session_date: str) -> WcaDailyStateProjection:
        ...

    def list_inventory_ledger_events(self, *, algorithm_id: str, broker_account_id: str, symbol: str) -> tuple[WcaInventoryLedgerEvent, ...]:
        ...

    def rebuild_inventory_projections(self, *, algorithm_id: str, broker_account_id: str, symbol: str) -> None:
        ...

    def write_broker_account_snapshot(self, snapshot: BrokerAccountSnapshot, *, symbol: str = "SPY", cash: float | None = None, account_status: str = "active", pattern_day_trading_restrictions: str | None = None, trading_restrictions: tuple[str, ...] = (), configuration_version: str = "", decision_id: str = "", run_id: str = "") -> str:
        ...

    def read_latest_broker_account_snapshot(self, *, algorithm_id: str, broker_account_id: str) -> BrokerAccountSnapshot | None:
        ...

    def authorize_wca_lot_reduction(self, *, lot_id: str, account_id: str, symbol: str, quantity: int) -> WcaInventoryOwnershipDecision:
        ...

    def list_open_wca_lots(self, *, account_id: str, symbol: str) -> tuple[dict[str, Any], ...]:
        ...

    def write_position_management_snapshot(self, position: WcaManagedPosition, *, evaluated_at: datetime) -> None:
        ...

    def close_wca_attributed_position_quantity(
        self,
        *,
        account_id: str,
        symbol: str,
        quantity: int,
        exit_price: float,
        exit_reason: str,
        evaluated_at: datetime,
        client_order_id: str | None = None,
        broker_order_id: str | None = None,
        fill_id: str | None = None,
        payload: dict[str, Any] | None = None,
        record_inventory_event: bool = True,
    ) -> bool:
        ...

    def realized_pnl_for_wca_position(self, *, account_id: str, symbol: str) -> float:
        ...

    def open_wca_position_quantity(self, *, account_id: str, symbol: str) -> int:
        ...

    def wca_position_circuit_breaker_open(self, *, account_id: str, symbol: str) -> bool:
        ...

    def reconciliation_blocks_new_entries(self, *, account_id: str = "paper", symbol: str = "SPY") -> bool:
        ...

    def reserve_order_intent(self, decision: WcaDecision, *, run_id: str, account_id: str, idempotency_key: str) -> WcaOrderIntentReservation:
        ...

    def list_order_intents(self, *, account_id: str | None = None) -> tuple[ProposedOrder, ...]:
        ...

    def has_order_fill(self, order_intent_id: str) -> bool:
        ...

    def write_broker_reconciliation(self, result: WcaBrokerReconciliationResult) -> None:
        ...

    def write_shadow_comparison_evidence(self, evidence: WcaShadowComparisonEvidence) -> None:
        ...

    def write_paper_stability_validation(self, result: WcaPaperStabilityValidationResult) -> None:
        ...

    def save_backtest_result(self, result: BacktestResult) -> None:
        ...

    def load_backtest_result(self, run_id: str) -> BacktestResult | None:
        ...

    def table_counts(self) -> WcaPersistenceSummary:
        ...


@dataclass(frozen=True)
class WcaPersistenceSummary:
    table_counts: dict[str, int]
    migration_version: str = WCA_PERSISTENCE_MIGRATION_VERSION


@dataclass(frozen=True)
class WcaOrderIntentReservation:
    created: bool
    proposed_order: ProposedOrder
    idempotency_key: str


@dataclass(frozen=True)
class WcaOutboxReservation:
    created: bool
    outbox_id: str
    proposed_order: ProposedOrder
    idempotency_key: str
    client_order_id: str


@dataclass(frozen=True)
class WcaExecutionOutboxRecord:
    outbox_id: str
    account_id: str
    symbol: str
    decision_id: str
    run_id: str
    order_intent_id: str
    idempotency_key: str
    client_order_id: str
    status: str
    version: int
    decision: WcaDecision
    proposed_order: ProposedOrder
    request_payload: dict[str, Any]
    response_payload: dict[str, Any] | None = None
    runtime_control_revision: int | None = None
    runtime_control_hash: str = ""
    global_risk_decision_id: str = ""
    global_risk_state_hash: str = ""
    global_risk_state_revision: str = ""
    global_risk_approval_expires_at: datetime | None = None
    authoritative_state_hash: str = ""


@dataclass(frozen=True)
class WcaGlobalRiskApprovalRecord:
    decision_id: str
    account_id: str
    symbol: str
    status: str
    global_risk_decision_id: str
    evaluated_at: datetime | None
    expires_at: datetime | None
    entry_permitted: bool
    risk_reducing_exit_permitted: bool
    requested_quantity: int
    allowed_quantity: int
    approved_risk_dollars: float
    reason_codes: tuple[str, ...]
    global_state_hash: str
    global_state_revision: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class WcaInventoryLedgerEvent:
    inventory_event_id: str
    event_type: str
    broker_account_id: str
    symbol: str
    event_timestamp: datetime | str
    trade_date: str
    algorithm_id: str = WCA_ALGORITHM_ID
    order_intent_id: str | None = None
    client_order_id: str | None = None
    broker_order_id: str | None = None
    fill_id: str | None = None
    side: str | None = None
    quantity: int = 0
    filled_quantity: int = 0
    remaining_quantity: int = 0
    average_entry_price: float = 0.0
    fill_price: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    reserved_risk: float = 0.0
    source_authority: str = "wca_repository"
    reconciliation_watermark: str | None = None
    configuration_version: str = ""
    decision_id: str = ""
    run_id: str = ""
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class WcaInventoryProjection:
    algorithm_id: str
    broker_account_id: str
    symbol: str
    open_quantity: int
    average_entry_price: float
    realized_pnl: float
    unrealized_pnl: float
    reserved_risk: float
    last_event_timestamp: str | None
    reconciliation_watermark: str | None
    configuration_version: str
    decision_id: str
    run_id: str


@dataclass(frozen=True)
class WcaDailyStateProjection:
    algorithm_id: str
    broker_account_id: str
    symbol: str
    session_date: str
    trades_completed_today: int
    entries_attempted_today: int
    realized_pnl_today: float
    daily_loss: float
    consecutive_losses: int
    current_reserved_risk: float
    maximum_intraday_exposure: float
    cooldown_until: str | None
    last_entry_timestamp: str | None
    last_exit_timestamp: str | None
    circuit_breaker_state: str
    last_successful_reconciliation: str | None
    isolation_enforced: bool


@dataclass(frozen=True)
class WcaInventoryOwnershipDecision:
    allowed: bool
    reason_codes: tuple[str, ...]


def classify_wca_local_storage_key(key: str) -> str:
    """Classify legacy browser storage handling for WCA.

    Authoritative WCA state now lives in SQLite. Old localStorage keys that
    carried settings, orders, trades, or backtest artifacts are safely ignored.
    """

    if key in WCA_IGNORED_LOCAL_STORAGE_KEYS:
        return "ignored_authoritative_backend_state"
    if key.startswith(WCA_ALLOWED_LOCAL_STORAGE_PREFIXES) or "expanded" in key or "collapsed" in key or "tab" in key:
        return "allowed_visual_preference"
    if "confidence" in key or "wca" in key:
        return "ignored_unknown_wca_local_storage"
    return "not_wca"


def migrate_wca_sqlite_database(path: str | Path) -> None:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        apply_wca_persistence_migrations(conn)


def apply_wca_persistence_migrations(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wca_configuration_versions (
            configuration_version TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_active_configuration (
            algorithm_id TEXT PRIMARY KEY,
            configuration_version TEXT NOT NULL,
            activated_at TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_strategy_versions (
            strategy_id TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            family TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (strategy_id, strategy_version)
        );

        CREATE TABLE IF NOT EXISTS wca_strategy_settings_versions (
            settings_key TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            settings_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (strategy_id, settings_version, configuration_version)
        );

        CREATE TABLE IF NOT EXISTS wca_weight_snapshots (
            weight_version TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_finalized_bar_event_receipts (
            event_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            event_timestamp TEXT NOT NULL,
            event_source TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_runtime_checkpoints (
            checkpoint_key TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_runtime_event_queue (
            event_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            algorithm_subscription_id TEXT NOT NULL,
            finalized_candle_timestamp TEXT NOT NULL,
            data_manifest_hash TEXT NOT NULL,
            publication_timestamp TEXT NOT NULL,
            source TEXT NOT NULL,
            replay_or_recovery INTEGER NOT NULL,
            status TEXT NOT NULL,
            lease_owner TEXT,
            lease_expires_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_runtime_command_queue (
            command_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            event_id TEXT,
            command_type TEXT NOT NULL,
            priority INTEGER NOT NULL,
            status TEXT NOT NULL,
            lease_owner TEXT,
            lease_expires_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_runtime_symbol_leases (
            lease_key TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            lease_expires_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_confidence_calibrations (
            calibration_version TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_market_status_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            trend TEXT NOT NULL,
            volatility TEXT NOT NULL,
            liquidity TEXT NOT NULL,
            session TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (decision_id)
        );

        CREATE TABLE IF NOT EXISTS wca_effective_setting_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            settings_version TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (decision_id)
        );

        CREATE TABLE IF NOT EXISTS wca_decisions (
            decision_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            side TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_strategy_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            family TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (decision_id, strategy_id)
        );

        CREATE TABLE IF NOT EXISTS wca_modifier_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            modifier_id TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (decision_id, modifier_id)
        );

        CREATE TABLE IF NOT EXISTS wca_local_gate_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            gate_id TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (decision_id, gate_id)
        );

        CREATE TABLE IF NOT EXISTS global_gate_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (decision_id)
        );

        CREATE TABLE IF NOT EXISTS wca_global_risk_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            proposed_quantity INTEGER NOT NULL,
            allowed_quantity INTEGER NOT NULL,
            global_risk_decision_id TEXT NOT NULL DEFAULT '',
            evaluated_at TEXT,
            expires_at TEXT,
            entry_permitted INTEGER NOT NULL DEFAULT 0,
            risk_reducing_exit_permitted INTEGER NOT NULL DEFAULT 1,
            approved_risk_dollars REAL NOT NULL DEFAULT 0,
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            global_state_hash TEXT NOT NULL DEFAULT '',
            global_state_revision TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (decision_id)
        );

        CREATE TABLE IF NOT EXISTS wca_proposed_orders (
            order_intent_id TEXT PRIMARY KEY,
            idempotency_key TEXT,
            account_id TEXT NOT NULL DEFAULT 'paper',
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_order_intents (
            order_intent_id TEXT PRIMARY KEY,
            idempotency_key TEXT,
            account_id TEXT NOT NULL DEFAULT 'paper',
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_attributed_orders (
            order_intent_id TEXT PRIMARY KEY,
            idempotency_key TEXT,
            account_id TEXT NOT NULL DEFAULT 'paper',
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_execution_outbox (
            outbox_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            order_intent_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS wca_broker_orders (
            broker_order_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            order_intent_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS wca_execution_results (
            execution_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_attributed_fills (
            fill_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_positions (
            position_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_owned_lots (
            lot_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            position_id TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            status TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_virtual_positions (
            virtual_position_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_trade_ledger (
            trade_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            pnl REAL NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_inventory_ledger (
            inventory_event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL CHECK (event_type IN (
                'ORDER_INTENT_RESERVED',
                'ORDER_SUBMITTED',
                'ORDER_ACKNOWLEDGED',
                'ORDER_REJECTED',
                'ORDER_CANCELLED',
                'PARTIAL_FILL_RECEIVED',
                'FILL_RECEIVED',
                'POSITION_OPENED',
                'POSITION_INCREASED',
                'POSITION_REDUCED',
                'POSITION_CLOSED',
                'PROTECTIVE_ORDER_CREATED',
                'PROTECTIVE_ORDER_REPLACED',
                'RISK_RESERVED',
                'RISK_RELEASED',
                'DAILY_STATE_RESET',
                'RECONCILIATION_CORRECTION',
                'END_OF_SESSION_FLATTEN'
            )),
            algorithm_id TEXT NOT NULL CHECK (algorithm_id = 'wca'),
            broker_account_id TEXT NOT NULL CHECK (broker_account_id <> ''),
            symbol TEXT NOT NULL CHECK (symbol <> ''),
            order_intent_id TEXT,
            client_order_id TEXT,
            broker_order_id TEXT,
            fill_id TEXT,
            side TEXT,
            quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
            filled_quantity INTEGER NOT NULL DEFAULT 0 CHECK (filled_quantity >= 0),
            remaining_quantity INTEGER NOT NULL DEFAULT 0 CHECK (remaining_quantity >= 0),
            average_entry_price REAL NOT NULL DEFAULT 0 CHECK (average_entry_price >= 0),
            fill_price REAL NOT NULL DEFAULT 0 CHECK (fill_price >= 0),
            realized_pnl REAL NOT NULL DEFAULT 0,
            unrealized_pnl REAL NOT NULL DEFAULT 0,
            reserved_risk REAL NOT NULL DEFAULT 0 CHECK (reserved_risk >= 0),
            trade_date TEXT NOT NULL CHECK (trade_date <> ''),
            event_timestamp TEXT NOT NULL CHECK (event_timestamp <> ''),
            source_authority TEXT NOT NULL CHECK (source_authority <> ''),
            reconciliation_watermark TEXT,
            configuration_version TEXT NOT NULL DEFAULT '',
            decision_id TEXT NOT NULL DEFAULT '',
            run_id TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_inventory_projection (
            algorithm_id TEXT NOT NULL CHECK (algorithm_id = 'wca'),
            broker_account_id TEXT NOT NULL CHECK (broker_account_id <> ''),
            symbol TEXT NOT NULL CHECK (symbol <> ''),
            open_quantity INTEGER NOT NULL DEFAULT 0 CHECK (open_quantity >= 0),
            average_entry_price REAL NOT NULL DEFAULT 0 CHECK (average_entry_price >= 0),
            realized_pnl REAL NOT NULL DEFAULT 0,
            unrealized_pnl REAL NOT NULL DEFAULT 0,
            reserved_risk REAL NOT NULL DEFAULT 0 CHECK (reserved_risk >= 0),
            last_event_timestamp TEXT,
            reconciliation_watermark TEXT,
            configuration_version TEXT NOT NULL DEFAULT '',
            decision_id TEXT NOT NULL DEFAULT '',
            run_id TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (algorithm_id, broker_account_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS wca_daily_state (
            algorithm_id TEXT NOT NULL CHECK (algorithm_id = 'wca'),
            broker_account_id TEXT NOT NULL CHECK (broker_account_id <> ''),
            symbol TEXT NOT NULL CHECK (symbol <> ''),
            session_date TEXT NOT NULL CHECK (session_date <> ''),
            trades_completed_today INTEGER NOT NULL DEFAULT 0 CHECK (trades_completed_today >= 0),
            entries_attempted_today INTEGER NOT NULL DEFAULT 0 CHECK (entries_attempted_today >= 0),
            realized_pnl_today REAL NOT NULL DEFAULT 0,
            daily_loss REAL NOT NULL DEFAULT 0 CHECK (daily_loss >= 0),
            consecutive_losses INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_losses >= 0),
            current_reserved_risk REAL NOT NULL DEFAULT 0 CHECK (current_reserved_risk >= 0),
            maximum_intraday_exposure REAL NOT NULL DEFAULT 0 CHECK (maximum_intraday_exposure >= 0),
            cooldown_until TEXT,
            last_entry_timestamp TEXT,
            last_exit_timestamp TEXT,
            circuit_breaker_state TEXT NOT NULL DEFAULT 'closed',
            last_successful_reconciliation TEXT,
            isolation_enforced INTEGER NOT NULL DEFAULT 1 CHECK (isolation_enforced IN (0, 1)),
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (algorithm_id, broker_account_id, symbol, session_date)
        );

        CREATE TABLE IF NOT EXISTS wca_broker_account_snapshots (
            broker_snapshot_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL CHECK (algorithm_id = 'wca'),
            broker_account_id TEXT NOT NULL CHECK (broker_account_id <> ''),
            symbol TEXT NOT NULL DEFAULT 'SPY' CHECK (symbol <> ''),
            timestamp TEXT NOT NULL CHECK (timestamp <> ''),
            configuration_version TEXT NOT NULL DEFAULT '',
            engine_version TEXT NOT NULL DEFAULT 'wca_authoritative_runtime_state_v1',
            market_snapshot_id TEXT NOT NULL DEFAULT '',
            decision_id TEXT NOT NULL DEFAULT '',
            run_id TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL CHECK (observed_at <> ''),
            session_date TEXT NOT NULL CHECK (session_date <> ''),
            source_authority TEXT NOT NULL CHECK (source_authority <> ''),
            equity REAL NOT NULL CHECK (equity >= 0),
            buying_power REAL NOT NULL CHECK (buying_power >= 0),
            cash REAL CHECK (cash IS NULL OR cash >= 0),
            account_status TEXT NOT NULL DEFAULT 'unknown',
            pattern_day_trading_restrictions TEXT,
            trading_restrictions_json TEXT NOT NULL DEFAULT '[]',
            positions_json TEXT NOT NULL DEFAULT '[]',
            pending_orders_json TEXT NOT NULL DEFAULT '[]',
            partially_filled_orders_json TEXT NOT NULL DEFAULT '[]',
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_broker_reconciliations (
            reconciliation_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            discrepancy_count INTEGER NOT NULL,
            hard_operational_warning INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_exit_state (
            exit_state_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            position_id TEXT NOT NULL,
            status TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_runtime_health (
            health_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            block_new_entries INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_runtime_control (
            control_key TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            broker_account_id TEXT NOT NULL DEFAULT 'paper',
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            configuration_hash TEXT NOT NULL DEFAULT '',
            weight_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            order_intent_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            paper_trading_requested INTEGER NOT NULL DEFAULT 0,
            automatic_entries_requested INTEGER NOT NULL DEFAULT 0,
            pause_new_entries INTEGER NOT NULL DEFAULT 1,
            kill_switch_open INTEGER NOT NULL DEFAULT 0,
            effective_paper_trading_enabled INTEGER NOT NULL DEFAULT 0,
            effective_automatic_entries_enabled INTEGER NOT NULL DEFAULT 0,
            control_revision INTEGER NOT NULL DEFAULT 1,
            control_hash TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            reason TEXT NOT NULL,
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (algorithm_id = 'wca')
        );

        CREATE TABLE IF NOT EXISTS wca_runtime_latency_observations (
            latency_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL CHECK (algorithm_id = 'wca'),
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL DEFAULT 'SPY',
            timestamp TEXT NOT NULL CHECK (timestamp <> ''),
            component TEXT NOT NULL CHECK (component <> ''),
            value_seconds REAL CHECK (value_seconds IS NULL OR value_seconds >= 0),
            failed INTEGER NOT NULL DEFAULT 0 CHECK (failed IN (0, 1)),
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_runtime_latency_summaries (
            summary_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL CHECK (algorithm_id = 'wca'),
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL DEFAULT 'SPY',
            component TEXT NOT NULL CHECK (component <> ''),
            sample_count INTEGER NOT NULL DEFAULT 0 CHECK (sample_count >= 0),
            failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
            p50_seconds REAL CHECK (p50_seconds IS NULL OR p50_seconds >= 0),
            p95_seconds REAL CHECK (p95_seconds IS NULL OR p95_seconds >= 0),
            p99_seconds REAL CHECK (p99_seconds IS NULL OR p99_seconds >= 0),
            max_seconds REAL CHECK (max_seconds IS NULL OR max_seconds >= 0),
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (algorithm_id, account_id, symbol, component)
        );

        CREATE TABLE IF NOT EXISTS wca_background_jobs (
            job_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_research_candidates (
            candidate_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            candidate_type TEXT NOT NULL,
            candidate_version TEXT NOT NULL,
            validation_status TEXT NOT NULL,
            promotion_status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (candidate_type, candidate_version)
        );

        CREATE TABLE IF NOT EXISTS wca_shadow_comparison_evidence (
            evidence_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            rollout_phase TEXT NOT NULL,
            within_tolerance INTEGER NOT NULL,
            mismatch_count INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_paper_stability_validations (
            validation_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            validation_days REAL NOT NULL,
            market_condition_count INTEGER NOT NULL,
            paper_trading_stable INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_backtest_runs (
            run_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            total_pnl REAL NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_backtest_trades (
            trade_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            pnl REAL NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_backtest_results (
            run_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_strategy_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            family TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (run_id, strategy_id)
        );

        CREATE TABLE IF NOT EXISTS wca_rollout_status (
            status_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            rollout_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wca_rollout_evidence (
            evidence_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'paper',
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            market_snapshot_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            rollout_phase TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_wca_decisions_symbol_time
            ON wca_decisions(symbol, timestamp);
        CREATE INDEX IF NOT EXISTS idx_wca_backtest_runs_symbol_time
            ON wca_backtest_runs(symbol, timestamp);
        CREATE INDEX IF NOT EXISTS idx_wca_trade_ledger_decision
            ON wca_trade_ledger(decision_id);
        CREATE INDEX IF NOT EXISTS idx_wca_owned_lots_symbol_account
            ON wca_owned_lots(account_id, symbol, status);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wca_inventory_ledger_fill_id
            ON wca_inventory_ledger(fill_id)
            WHERE fill_id IS NOT NULL AND fill_id <> '';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wca_inventory_ledger_client_order_submission
            ON wca_inventory_ledger(client_order_id)
            WHERE client_order_id IS NOT NULL AND client_order_id <> '' AND event_type = 'ORDER_SUBMITTED';
        CREATE INDEX IF NOT EXISTS idx_wca_inventory_ledger_scope
            ON wca_inventory_ledger(algorithm_id, broker_account_id, symbol, trade_date, event_timestamp);
        CREATE INDEX IF NOT EXISTS idx_wca_broker_account_snapshots_scope
            ON wca_broker_account_snapshots(algorithm_id, broker_account_id, symbol, observed_at);
        CREATE INDEX IF NOT EXISTS idx_wca_runtime_event_queue_symbol_status
            ON wca_runtime_event_queue(symbol, status, finalized_candle_timestamp);
        CREATE INDEX IF NOT EXISTS idx_wca_runtime_command_queue_type_status
            ON wca_runtime_command_queue(command_type, status, priority, created_at);
        CREATE INDEX IF NOT EXISTS idx_wca_background_jobs_status_priority
            ON wca_background_jobs(status, job_type, created_at);
        """
    )
    _ensure_column(conn, "wca_proposed_orders", "idempotency_key", "TEXT")
    _ensure_column(conn, "wca_proposed_orders", "account_id", "TEXT NOT NULL DEFAULT 'paper'")
    _ensure_column(conn, "wca_configuration_versions", "configuration_id", "TEXT NOT NULL DEFAULT 'wca-config-legacy'")
    _ensure_column(conn, "wca_configuration_versions", "schema_version", "TEXT NOT NULL DEFAULT 'wca_legacy_configuration_schema_v1'")
    _ensure_column(conn, "wca_configuration_versions", "content_hash", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "wca_configuration_versions", "lifecycle", "TEXT NOT NULL DEFAULT 'candidate'")
    _ensure_column(conn, "wca_configuration_versions", "creator", "TEXT NOT NULL DEFAULT 'legacy'")
    _ensure_column(conn, "wca_configuration_versions", "source", "TEXT NOT NULL DEFAULT 'legacy'")
    _ensure_column(conn, "wca_configuration_versions", "activated_at", "TEXT")
    _ensure_column(conn, "wca_background_jobs", "lease_owner", "TEXT")
    _ensure_column(conn, "wca_background_jobs", "lease_expires_at", "TEXT")
    _ensure_column(conn, "wca_background_jobs", "attempt_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "wca_background_jobs", "max_attempts", "INTEGER NOT NULL DEFAULT 3")
    _ensure_column(conn, "wca_background_jobs", "progress_percent", "REAL NOT NULL DEFAULT 0")
    _ensure_column(conn, "wca_background_jobs", "cancel_requested", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "wca_background_jobs", "logs_json", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "wca_background_jobs", "result_reference_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "wca_background_jobs", "error_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "wca_background_jobs", "expires_at", "TEXT")
    _ensure_column(conn, "wca_execution_outbox", "client_order_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "wca_execution_outbox", "request_payload_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "wca_execution_outbox", "response_payload_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "wca_execution_outbox", "error_payload_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "wca_execution_outbox", "claim_owner", "TEXT")
    _ensure_column(conn, "wca_execution_outbox", "claimed_at", "TEXT")
    _ensure_column(conn, "wca_execution_outbox", "submitted_at", "TEXT")
    _ensure_column(conn, "wca_execution_outbox", "acknowledged_at", "TEXT")
    _ensure_column(conn, "wca_execution_outbox", "global_risk_decision_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "wca_execution_outbox", "global_risk_state_hash", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "wca_execution_outbox", "global_risk_state_revision", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "wca_execution_outbox", "global_risk_approval_expires_at", "TEXT")
    _ensure_column(conn, "wca_execution_outbox", "authoritative_state_hash", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "wca_global_risk_responses", "global_risk_decision_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "wca_global_risk_responses", "evaluated_at", "TEXT")
    _ensure_column(conn, "wca_global_risk_responses", "expires_at", "TEXT")
    _ensure_column(conn, "wca_global_risk_responses", "entry_permitted", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "wca_global_risk_responses", "risk_reducing_exit_permitted", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "wca_global_risk_responses", "approved_risk_dollars", "REAL NOT NULL DEFAULT 0")
    _ensure_column(conn, "wca_global_risk_responses", "reason_codes_json", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "wca_global_risk_responses", "global_state_hash", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "wca_global_risk_responses", "global_state_revision", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "wca_broker_orders", "client_order_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "wca_broker_orders", "request_payload_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "wca_broker_orders", "response_payload_json", "TEXT NOT NULL DEFAULT '{}'")
    for table in ("wca_decisions", "wca_proposed_orders", "wca_order_intents", "wca_attributed_orders", "wca_execution_outbox"):
        _ensure_column(conn, table, "runtime_control_revision", "INTEGER")
        _ensure_column(conn, table, "runtime_control_hash", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, table, "rollout_stage", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, table, "rollout_evidence_revision", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, table, "rollout_evidence_hash", "TEXT NOT NULL DEFAULT ''")
    for table in ("wca_execution_outbox", "wca_attributed_orders", "wca_broker_orders"):
        conn.execute(f"UPDATE {table} SET status = 'RESERVED' WHERE status = 'OUTBOX_RESERVED'")
        conn.execute(f"UPDATE {table} SET status = 'ACKNOWLEDGED' WHERE status = 'BROKER_ACKNOWLEDGED'")
        conn.execute(f"UPDATE {table} SET status = 'UNKNOWN' WHERE status = 'SUBMISSION_UNKNOWN'")
        conn.execute(f"UPDATE {table} SET status = 'RECONCILING' WHERE status = 'RECONCILIATION_REQUIRED'")
    for table in (
        "wca_strategy_settings_versions",
        "wca_weight_snapshots",
        "wca_confidence_calibrations",
        "wca_market_status_snapshots",
        "wca_effective_setting_snapshots",
        "wca_decisions",
        "wca_strategy_evaluations",
        "wca_modifier_evaluations",
        "wca_local_gate_evaluations",
        "global_gate_evaluations",
        "wca_attributed_fills",
        "wca_positions",
        "wca_trade_ledger",
        "wca_shadow_comparison_evidence",
        "wca_backtest_runs",
        "wca_backtest_results",
        "wca_backtest_trades",
        "wca_strategy_performance",
        "wca_rollout_status",
    ):
        _ensure_column(conn, table, "account_id", "TEXT NOT NULL DEFAULT 'paper'")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wca_proposed_orders_idempotency
            ON wca_proposed_orders(idempotency_key)
            WHERE idempotency_key IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wca_order_intents_idempotency
            ON wca_order_intents(idempotency_key)
            WHERE idempotency_key IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wca_attributed_orders_idempotency
            ON wca_attributed_orders(idempotency_key)
            WHERE idempotency_key IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wca_execution_outbox_idempotency
            ON wca_execution_outbox(idempotency_key)
            WHERE idempotency_key IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_wca_broker_orders_idempotency
            ON wca_broker_orders(idempotency_key)
            WHERE idempotency_key IS NOT NULL
        """
    )
    _install_wca_table_integrity_triggers(conn)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
        (WCA_PERSISTENCE_MIGRATION_VERSION,),
    )


class WcaSqliteRepository:
    def __init__(self, database_url: str | None = None) -> None:
        self.path = _sqlite_path(database_url or get_settings().database_url)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        migrate_wca_sqlite_database(self.path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize_defaults(self, *, symbol: str, configuration: dict[str, Any], weight_snapshot: WcaWeightSnapshot, engine_version: str) -> None:
        timestamp = _utc_now()
        active = self.read_active_configuration()
        if active is None:
            seeded = _configuration_from_payload(configuration).with_lifecycle(
                WcaConfigurationLifecycle.ACTIVE,
                activation_timestamp=datetime.now(timezone.utc),
            )
            self.save_candidate_configuration(seeded, symbol=symbol, engine_version=engine_version)
            active = self.activate_configuration_version(seeded.configuration_version)
        self.save_strategy_versions(symbol=symbol, timestamp=timestamp, configuration_version=active.configuration_version, engine_version=engine_version)
        self.save_weight_snapshot(weight_snapshot, symbol=symbol, configuration_version=active.configuration_version, engine_version=engine_version)
        self.read_runtime_control(broker_account_id="paper", symbol=symbol)

    def read_runtime_control(self, *, broker_account_id: str = "paper", symbol: str = "SPY") -> WcaRuntimeControl:
        key = _runtime_control_key(broker_account_id, symbol)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json, control_revision
                FROM wca_runtime_control
                WHERE control_key = ? AND algorithm_id = ?
                """,
                (key, WCA_ALGORITHM_ID),
            ).fetchone()
        if row is None:
            control = default_wca_runtime_control(
                broker_account_id=broker_account_id,
                symbol=symbol,
                reason="wca.runtime_control.missing_default_fail_closed",
                reason_codes=("wca.runtime_control.missing_default_fail_closed",),
            )
            return self.write_runtime_control(control)
        try:
            return WcaRuntimeControl.model_validate_json(row["payload_json"]).with_hash()
        except (ValueError, TypeError, ValidationError):
            control = default_wca_runtime_control(
                broker_account_id=broker_account_id,
                symbol=symbol,
                control_revision=int(row["control_revision"] or 1) + 1,
                reason="wca.runtime_control.corrupted_default_fail_closed",
                reason_codes=("wca.runtime_control.corrupted_default_fail_closed",),
            )
            return self.write_runtime_control(control)

    def write_runtime_control(self, control: WcaRuntimeControl) -> WcaRuntimeControl:
        saved = control.with_hash()
        common = _common_row(
            symbol=saved.symbol,
            timestamp=_dt(saved.timestamp),
            configuration_version=saved.configuration_version,
            engine_version=saved.schema_version,
            market_snapshot_id="wca-runtime-control",
            decision_id=saved.decision_id,
            run_id=saved.run_id,
            account_id=saved.broker_account_id,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_runtime_control (
                    control_key, algorithm_id, broker_account_id, account_id, symbol, timestamp,
                    configuration_version, configuration_hash, weight_version, engine_version,
                    market_snapshot_id, decision_id, order_intent_id, run_id,
                    paper_trading_requested, automatic_entries_requested, pause_new_entries,
                    kill_switch_open, effective_paper_trading_enabled, effective_automatic_entries_enabled,
                    control_revision, control_hash, updated_at, updated_by, reason,
                    reason_codes_json, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _runtime_control_key(saved.broker_account_id, saved.symbol),
                    WCA_ALGORITHM_ID,
                    saved.broker_account_id,
                    saved.broker_account_id,
                    saved.symbol,
                    common["timestamp"],
                    saved.configuration_version,
                    saved.configuration_hash,
                    saved.weight_version,
                    saved.schema_version,
                    common["market_snapshot_id"],
                    saved.decision_id,
                    saved.order_intent_id,
                    saved.run_id,
                    int(saved.paper_trading_requested),
                    int(saved.automatic_entries_requested),
                    int(saved.pause_new_entries),
                    int(saved.kill_switch_open),
                    int(saved.effective_paper_trading_enabled),
                    int(saved.effective_automatic_entries_enabled),
                    saved.control_revision,
                    saved.control_hash,
                    _dt(saved.updated_at),
                    saved.updated_by,
                    saved.reason,
                    _json(saved.reason_codes),
                    saved.model_dump_json(),
                ),
            )
        return saved

    def save_configuration(self, payload: dict[str, Any], *, symbol: str, timestamp: str | None = None, engine_version: str) -> None:
        configuration = _configuration_from_payload(payload)
        self.save_candidate_configuration(configuration, symbol=symbol, engine_version=engine_version)
        if configuration.lifecycle == WcaConfigurationLifecycle.ACTIVE.value:
            self.activate_configuration_version(configuration.configuration_version)

    def save_candidate_configuration(self, configuration: WcaConfiguration, *, symbol: str = "SPY", engine_version: str) -> WcaConfiguration:
        revision = validate_wca_configuration(configuration)
        configuration_version = revision.configuration_version
        saved_at = _utc_now()
        row = _common_row(
            symbol=symbol,
            timestamp=saved_at,
            configuration_version=configuration_version,
            engine_version=engine_version,
            market_snapshot_id=f"wca-config-{configuration_version}",
            decision_id=f"wca-config-{configuration_version}",
            run_id=f"wca-config-{configuration_version}",
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_configuration_versions (
                    configuration_version, algorithm_id, symbol, timestamp, engine_version,
                    market_snapshot_id, decision_id, run_id, payload_json, configuration_id,
                    schema_version, content_hash, lifecycle, creator, source, activated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    configuration_version,
                    row["algorithm_id"],
                    symbol,
                    row["timestamp"],
                    engine_version,
                    row["market_snapshot_id"],
                    row["decision_id"],
                    row["run_id"],
                    revision.model_dump_json(),
                    revision.configuration_id,
                    revision.schema_version,
                    revision.content_hash,
                    _value(revision.lifecycle),
                    revision.creator,
                    revision.source,
                    _dt(revision.activation_timestamp) if revision.activation_timestamp is not None else None,
                ),
            )
        return revision

    def validate_configuration_revision(self, configuration: WcaConfiguration | dict[str, Any]) -> WcaConfiguration:
        return validate_wca_configuration(configuration)

    def activate_configuration_version(self, configuration_version: str) -> WcaConfiguration:
        active = self.read_configuration_by_version(configuration_version)
        if active is None:
            raise ValueError(f"unknown WCA configuration version: {configuration_version}")
        activated = active.with_lifecycle(WcaConfigurationLifecycle.ACTIVE, activation_timestamp=datetime.now(timezone.utc))
        with self.connect() as conn:
            self._insert_configuration_revision(conn, activated, symbol="SPY", engine_version="wca_configuration_repository")
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_active_configuration (
                    algorithm_id, configuration_version, activated_at, content_hash, payload_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    WCA_ALGORITHM_ID,
                    activated.configuration_version,
                    _dt(activated.activation_timestamp),
                    activated.content_hash,
                    activated.model_dump_json(),
                ),
            )
        return activated

    def activate_configuration_version_at_candle_boundary(self, configuration_version: str, *, candle_timestamp: datetime) -> WcaConfiguration:
        active = self.read_configuration_by_version(configuration_version)
        if active is None:
            raise ValueError(f"unknown WCA configuration version: {configuration_version}")
        boundary = candle_timestamp.astimezone(timezone.utc)
        activated = active.with_lifecycle(WcaConfigurationLifecycle.ACTIVE, activation_timestamp=boundary)
        with self.connect() as conn:
            self._insert_configuration_revision(conn, activated, symbol="SPY", engine_version="wca_configuration_repository")
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_active_configuration (
                    algorithm_id, configuration_version, activated_at, content_hash, payload_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    WCA_ALGORITHM_ID,
                    activated.configuration_version,
                    _dt(boundary),
                    activated.content_hash,
                    activated.model_dump_json(),
                ),
            )
        return activated

    def read_active_configuration(self) -> WcaConfiguration | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM wca_active_configuration WHERE algorithm_id = ?",
                (WCA_ALGORITHM_ID,),
            ).fetchone()
        return _configuration_from_json(row["payload_json"]) if row else None

    def read_configuration_by_version(self, configuration_version: str) -> WcaConfiguration | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM wca_configuration_versions WHERE configuration_version = ?",
                (configuration_version,),
            ).fetchone()
        return _configuration_from_json(row["payload_json"]) if row else None

    def rollback_configuration(self, configuration_version: str) -> WcaConfiguration:
        prior = self.read_configuration_by_version(configuration_version)
        if prior is None:
            raise ValueError(f"unknown WCA configuration version: {configuration_version}")
        if prior.lifecycle not in {WcaConfigurationLifecycle.APPROVED.value, WcaConfigurationLifecycle.ACTIVE.value, WcaConfigurationLifecycle.CANDIDATE.value}:
            raise ValueError("WCA rollback target must be a complete saved revision")
        return self.activate_configuration_version(configuration_version)

    def _insert_configuration_revision(self, conn: sqlite3.Connection, configuration: WcaConfiguration, *, symbol: str, engine_version: str) -> None:
        row = _common_row(
            symbol=symbol,
            timestamp=_dt(configuration.activation_timestamp or configuration.created_at),
            configuration_version=configuration.configuration_version,
            engine_version=engine_version,
            market_snapshot_id=f"wca-config-{configuration.configuration_version}",
            decision_id=f"wca-config-{configuration.configuration_version}",
            run_id=f"wca-config-{configuration.configuration_version}",
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_configuration_versions (
                configuration_version, algorithm_id, symbol, timestamp, engine_version,
                market_snapshot_id, decision_id, run_id, payload_json, configuration_id,
                schema_version, content_hash, lifecycle, creator, source, activated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                configuration.configuration_version,
                row["algorithm_id"],
                symbol,
                row["timestamp"],
                engine_version,
                row["market_snapshot_id"],
                row["decision_id"],
                row["run_id"],
                configuration.model_dump_json(),
                configuration.configuration_id,
                configuration.schema_version,
                configuration.content_hash,
                _value(configuration.lifecycle),
                configuration.creator,
                configuration.source,
                _dt(configuration.activation_timestamp) if configuration.activation_timestamp is not None else None,
            ),
        )

    def save_strategy_versions(self, *, symbol: str, timestamp: str, configuration_version: str, engine_version: str) -> None:
        row = _common_row(
            symbol=symbol,
            timestamp=timestamp,
            configuration_version=configuration_version,
            engine_version=engine_version,
            market_snapshot_id=f"wca-strategies-{configuration_version}",
            decision_id=f"wca-strategies-{configuration_version}",
            run_id=f"wca-strategies-{configuration_version}",
        )
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO wca_strategy_versions (
                    strategy_id, strategy_version, algorithm_id, symbol, timestamp,
                    configuration_version, engine_version, market_snapshot_id, decision_id,
                    run_id, family, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        definition.strategy_id,
                        f"{definition.slug}_catalog_v1",
                        row["algorithm_id"],
                        symbol,
                        row["timestamp"],
                        configuration_version,
                        engine_version,
                        row["market_snapshot_id"],
                        row["decision_id"],
                        row["run_id"],
                        definition.family,
                        _json(
                            {
                                "strategy_id": definition.strategy_id,
                                "slug": definition.slug,
                                "name": definition.name,
                                "family": definition.family,
                                "base_weight": definition.base_weight,
                                "role": _value(definition.role),
                            }
                        ),
                    )
                    for definition in WCA_STRATEGY_REGISTRY
                ],
            )

    def save_weight_snapshot(self, snapshot: WcaWeightSnapshot, *, symbol: str, configuration_version: str, engine_version: str, run_id: str = "wca-active-weights") -> None:
        timestamp = _dt(snapshot.created_at)
        row = _common_row(
            symbol=symbol,
            timestamp=timestamp,
            configuration_version=configuration_version,
            engine_version=engine_version,
            market_snapshot_id=f"{run_id}-weights",
            decision_id=f"{run_id}-weights",
            run_id=run_id,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO wca_weight_snapshots (
                    weight_version, algorithm_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (snapshot.weight_version, row["algorithm_id"], symbol, timestamp, configuration_version, engine_version, row["market_snapshot_id"], row["decision_id"], run_id, snapshot.model_dump_json()),
            )

    def read_active_weights(self, *, as_of: datetime | None = None) -> WcaWeightSnapshot | None:
        with self.connect() as conn:
            rows = conn.execute("SELECT payload_json FROM wca_weight_snapshots ORDER BY timestamp DESC, created_at DESC").fetchall()
        cutoff = as_of.astimezone(timezone.utc) if as_of is not None else None
        for row in rows:
            snapshot = WcaWeightSnapshot.model_validate_json(row["payload_json"])
            status = snapshot.status.value if hasattr(snapshot.status, "value") else str(snapshot.status)
            if status not in {"ACTIVE", "WcaEvaluationStatus.ACTIVE"}:
                continue
            if cutoff is None or (
                snapshot.created_at.astimezone(timezone.utc) <= cutoff
                and (snapshot.metrics_cutoff_timestamp is None or snapshot.metrics_cutoff_timestamp.astimezone(timezone.utc) <= cutoff)
            ):
                return adapt_v1_weight_snapshot_to_multipliers(snapshot)
        return None

    def save_confidence_calibration(self, calibration: WcaConfidenceCalibrationTable, *, symbol: str, configuration_version: str, engine_version: str) -> None:
        row = _common_row(
            symbol=symbol,
            timestamp=_dt(calibration.created_at),
            configuration_version=configuration_version,
            engine_version=engine_version,
            market_snapshot_id=f"wca-calibration-{calibration.calibration_version}",
            decision_id=f"wca-calibration-{calibration.calibration_version}",
            run_id=f"wca-calibration-{calibration.calibration_version}",
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_confidence_calibrations (
                    calibration_version, algorithm_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, strategy_id, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (calibration.calibration_version, row["algorithm_id"], symbol, row["timestamp"], configuration_version, engine_version, row["market_snapshot_id"], row["decision_id"], row["run_id"], calibration.strategy_id, calibration.model_dump_json()),
            )

    def read_active_confidence_calibrations(self, *, symbol: str = "SPY", as_of: datetime | None = None, max_age_days: int | None = None) -> tuple[WcaConfidenceCalibrationTable, ...]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM wca_confidence_calibrations
                WHERE algorithm_id = ? AND symbol = ?
                ORDER BY timestamp ASC, created_at ASC, calibration_version ASC
                """,
                (WCA_ALGORITHM_ID, symbol),
            ).fetchall()
        cutoff = as_of.astimezone(timezone.utc) if as_of is not None else None
        selected: dict[tuple[str, str, str | None, str | None], WcaConfidenceCalibrationTable] = {}
        for row in rows:
            table = WcaConfidenceCalibrationTable.model_validate_json(row["payload_json"])
            if cutoff is not None:
                created = table.created_at.astimezone(timezone.utc)
                metrics_cutoff = table.outcome_cutoff_timestamp.astimezone(timezone.utc)
                if created > cutoff or metrics_cutoff > cutoff:
                    continue
                if max_age_days is not None and (cutoff - created).days > max_age_days:
                    continue
            selected[(table.strategy_id, table.strategy_version, _optional_value(table.direction), table.regime)] = table
        return tuple(selected.values())

    def save_strategy_performance_records(self, records: tuple[WcaStrategyPerformanceRecord, ...], *, symbol: str, configuration_version: str, engine_version: str, run_id: str = "wca-performance-records") -> None:
        with self.connect() as conn:
            for index, record in enumerate(records):
                row = _common_row(
                    symbol=symbol,
                    timestamp=_dt(record.outcome_available_at),
                    configuration_version=configuration_version,
                    engine_version=engine_version,
                    market_snapshot_id=f"{run_id}-{record.strategy_id}-{index}",
                    decision_id=f"{run_id}-{record.strategy_id}-{index}",
                    run_id=f"{run_id}-{index}",
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO wca_strategy_performance (
                        algorithm_id, account_id, symbol, timestamp, configuration_version, engine_version,
                        market_snapshot_id, decision_id, run_id, strategy_id, family, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (row["algorithm_id"], row["account_id"], row["symbol"], row["timestamp"], row["configuration_version"], row["engine_version"], row["market_snapshot_id"], row["decision_id"], row["run_id"], record.strategy_id, record.family, record.model_dump_json()),
                )

    def load_strategy_performance_records(self, *, symbol: str = "SPY", as_of: datetime | None = None) -> tuple[WcaStrategyPerformanceRecord, ...]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM wca_strategy_performance
                WHERE algorithm_id = ? AND symbol = ?
                ORDER BY timestamp ASC, id ASC
                """,
                (WCA_ALGORITHM_ID, symbol),
            ).fetchall()
        cutoff = as_of.astimezone(timezone.utc) if as_of is not None else None
        records: list[WcaStrategyPerformanceRecord] = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            if {"strategy_id", "strategy_version", "decision_timestamp", "outcome_available_at", "r_multiple", "success"}.issubset(payload):
                record = WcaStrategyPerformanceRecord.model_validate(payload)
                if cutoff is None or (record.outcome_available_at.astimezone(timezone.utc) < cutoff and record.decision_timestamp.astimezone(timezone.utc) < cutoff):
                    records.append(record)
        return tuple(records)

    def write_decision_snapshot(self, decision: WcaDecision, *, run_id: str | None = None) -> None:
        run = run_id or decision.decision_id
        common = _decision_common(decision, run)
        with self.connect() as conn:
            self._insert_decision(conn, decision, common)

    def claim_finalized_bar_event(self, *, event_id: str, account_id: str, symbol: str, event_timestamp: datetime, payload: dict[str, Any], configuration_version: str = "", run_id: str = "") -> bool:
        run = run_id or event_id
        common = _common_row(
            symbol=symbol,
            timestamp=_dt(event_timestamp),
            configuration_version=configuration_version or "wca_event_unconfigured",
            engine_version="wca_finalized_bar_event_receipt_v1",
            market_snapshot_id=event_id,
            decision_id=event_id,
            run_id=run,
            account_id=account_id,
        )
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO wca_finalized_bar_event_receipts (
                    event_id, algorithm_id, account_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, event_timestamp,
                    event_source, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, common["algorithm_id"], account_id, symbol, common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], _dt(event_timestamp), str(payload.get("source") or "neutral_market_data"), _json(payload)),
            )
        return cursor.rowcount == 1

    def compare_and_swap_runtime_checkpoint(self, *, checkpoint_key: str, expected_version: int | None, payload: dict[str, Any], account_id: str = "paper", symbol: str = "SPY", configuration_version: str = "", run_id: str = "") -> bool:
        now = _dt(datetime.now(timezone.utc))
        run = run_id or checkpoint_key
        common = _common_row(
            symbol=symbol,
            timestamp=now,
            configuration_version=configuration_version or "wca_runtime_checkpoint",
            engine_version="wca_runtime_checkpoint_v1",
            market_snapshot_id=checkpoint_key,
            decision_id=checkpoint_key,
            run_id=run,
            account_id=account_id,
        )
        with self.connect() as conn:
            if expected_version is None:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO wca_runtime_checkpoints (
                        checkpoint_key, algorithm_id, account_id, symbol, timestamp,
                        configuration_version, engine_version, market_snapshot_id,
                        decision_id, run_id, version, payload_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (checkpoint_key, common["algorithm_id"], account_id, symbol, common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], 1, _json(payload), now),
                )
                return cursor.rowcount == 1
            cursor = conn.execute(
                """
                UPDATE wca_runtime_checkpoints
                SET version = version + 1, payload_json = ?, timestamp = ?, updated_at = ?
                WHERE checkpoint_key = ? AND algorithm_id = ? AND version = ?
                """,
                (_json(payload), now, now, checkpoint_key, WCA_ALGORITHM_ID, expected_version),
            )
        return cursor.rowcount == 1

    def create_execution_outbox_record(self, decision: WcaDecision, *, account_id: str, idempotency_key: str, payload: dict[str, Any] | None = None, final_validation_context: WcaOrderValidationContext | None = None) -> bool:
        if decision.proposed_order is None:
            raise ValueError("cannot create WCA execution outbox without an order intent")
        if final_validation_context is not None:
            from backend.app.algorithms.wca.order_validation import assert_wca_final_pre_outbox_validation

            decision = assert_wca_final_pre_outbox_validation(
                decision.model_copy(
                    update={
                        "proposed_order": decision.proposed_order.model_copy(
                            update={"status": WcaOrderStatus.OUTBOX_RESERVED, "account_id": account_id, "idempotency_key": idempotency_key}
                        )
                    }
                ),
                final_validation_context,
            )
        elif "wca.order_validation.final_pre_outbox.passed" not in decision.proposed_order.reason_codes:
            raise ValueError("WCA execution outbox requires final pre-outbox validation")
        common = _decision_common(decision, decision.decision_id)
        proposed = decision.proposed_order.model_copy(update={"status": WcaOrderStatus.OUTBOX_RESERVED, "account_id": account_id, "idempotency_key": idempotency_key})
        outbox_id = f"wca-outbox-{proposed.order_intent_id}"
        record = payload or proposed.model_dump(mode="json")
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO wca_execution_outbox (
                    outbox_id, algorithm_id, account_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, order_intent_id,
                    idempotency_key, status, client_order_id, request_payload_json, payload_json,
                    runtime_control_revision, runtime_control_hash, rollout_stage,
                    rollout_evidence_revision, rollout_evidence_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outbox_id,
                    common["algorithm_id"],
                    account_id,
                    common["symbol"],
                    common["timestamp"],
                    common["configuration_version"],
                    common["engine_version"],
                    common["market_snapshot_id"],
                    common["decision_id"],
                    common["run_id"],
                    proposed.order_intent_id,
                    idempotency_key,
                    WcaOrderStatus.OUTBOX_RESERVED.value,
                    str(record.get("client_order_id", "")) if isinstance(record, dict) else "",
                    _json(record),
                    _json(record),
                    proposed.runtime_control_revision,
                    proposed.runtime_control_hash,
                    proposed.rollout_stage,
                    proposed.rollout_evidence_revision,
                    proposed.rollout_evidence_hash,
                ),
            )
        return cursor.rowcount == 1

    def reserve_decision_order_and_outbox(
        self,
        decision: WcaDecision,
        *,
        run_id: str,
        account_id: str,
        idempotency_key: str,
        client_order_id: str,
        request_payload: dict[str, Any],
        final_validation_context: WcaOrderValidationContext,
        global_risk_approval: WcaGlobalRiskApprovalRecord | None = None,
        authoritative_state_hash: str = "",
    ) -> WcaOutboxReservation:
        if decision.proposed_order is None:
            raise ValueError("cannot reserve WCA outbox without a proposed order")
        proposed = decision.proposed_order.model_copy(
            update={
                "idempotency_key": idempotency_key,
                "account_id": account_id,
                "status": WcaOrderStatus.OUTBOX_RESERVED,
                "reason_codes": (*decision.proposed_order.reason_codes, "wca.order_state.outbox_reserved"),
            }
        )
        decision_to_persist = decision.model_copy(
            update={
                "proposed_order": proposed,
                "reason_codes": (*decision.reason_codes, "wca.outbox.atomic_decision_intent_outbox"),
            }
        )
        from backend.app.algorithms.wca.order_validation import assert_wca_final_pre_outbox_validation

        decision_to_persist = assert_wca_final_pre_outbox_validation(decision_to_persist, final_validation_context)
        proposed = decision_to_persist.proposed_order
        if proposed is None:
            raise ValueError("cannot reserve WCA outbox without a validated proposed order")
        common = _decision_common(decision_to_persist, run_id)
        outbox_id = f"wca-outbox-{proposed.order_intent_id}"
        payload = {
            "decision": decision_to_persist.model_dump(mode="json"),
            "proposed_order": proposed.model_dump(mode="json"),
            "request": request_payload,
            "client_order_id": client_order_id,
            "idempotency_key": idempotency_key,
            "runtime_control_revision": proposed.runtime_control_revision,
            "runtime_control_hash": proposed.runtime_control_hash,
            "global_risk_decision_id": global_risk_approval.global_risk_decision_id if global_risk_approval is not None else "",
            "global_risk_state_hash": global_risk_approval.global_state_hash if global_risk_approval is not None else "",
            "global_risk_state_revision": global_risk_approval.global_state_revision if global_risk_approval is not None else "",
            "global_risk_approval_expires_at": global_risk_approval.expires_at.isoformat() if global_risk_approval is not None and global_risk_approval.expires_at is not None else None,
            "authoritative_state_hash": authoritative_state_hash,
        }
        with self.connect() as conn:
            order_cursor = self._insert_order_records(conn, proposed, common, ignore_duplicates=True)
            if order_cursor.rowcount != 1:
                existing = self._outbox_by_idempotency_key(conn, idempotency_key)
                if existing is not None:
                    return WcaOutboxReservation(False, existing.outbox_id, existing.proposed_order, existing.idempotency_key, existing.client_order_id)
                row = conn.execute("SELECT payload_json FROM wca_proposed_orders WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
                if row is None:
                    raise RuntimeError("failed to read existing WCA order intent")
                existing_order = ProposedOrder.model_validate_json(row["payload_json"])
                validated_order = decision_to_persist.proposed_order
                validated_reasons = validated_order.reason_codes if validated_order is not None else ()
                proposed = existing_order.model_copy(
                    update={
                        "status": WcaOrderStatus.OUTBOX_RESERVED,
                        "account_id": account_id,
                        "idempotency_key": idempotency_key,
                        "reason_codes": tuple(dict.fromkeys((*existing_order.reason_codes, *validated_reasons))),
                    }
                )
                decision_to_persist = decision_to_persist.model_copy(update={"proposed_order": proposed})
                outbox_id = f"wca-outbox-{proposed.order_intent_id}"
                payload = {
                    "decision": decision_to_persist.model_dump(mode="json"),
                    "proposed_order": proposed.model_dump(mode="json"),
                    "request": request_payload,
                    "client_order_id": client_order_id,
                    "idempotency_key": idempotency_key,
                    "runtime_control_revision": proposed.runtime_control_revision,
                    "runtime_control_hash": proposed.runtime_control_hash,
                    "global_risk_decision_id": global_risk_approval.global_risk_decision_id if global_risk_approval is not None else "",
                    "global_risk_state_hash": global_risk_approval.global_state_hash if global_risk_approval is not None else "",
                    "global_risk_state_revision": global_risk_approval.global_state_revision if global_risk_approval is not None else "",
                    "global_risk_approval_expires_at": global_risk_approval.expires_at.isoformat() if global_risk_approval is not None and global_risk_approval.expires_at is not None else None,
                    "authoritative_state_hash": authoritative_state_hash,
                }
            self._insert_decision(conn, decision_to_persist, common)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO wca_execution_outbox (
                    outbox_id, algorithm_id, account_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, order_intent_id,
                    idempotency_key, status, client_order_id, request_payload_json, payload_json,
                    runtime_control_revision, runtime_control_hash, rollout_stage,
                    rollout_evidence_revision, rollout_evidence_hash,
                    global_risk_decision_id, global_risk_state_hash, global_risk_state_revision,
                    global_risk_approval_expires_at, authoritative_state_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outbox_id,
                    common["algorithm_id"],
                    account_id,
                    common["symbol"],
                    common["timestamp"],
                    common["configuration_version"],
                    common["engine_version"],
                    common["market_snapshot_id"],
                    common["decision_id"],
                    common["run_id"],
                    proposed.order_intent_id,
                    idempotency_key,
                    WcaOrderStatus.OUTBOX_RESERVED.value,
                    client_order_id,
                    _json(request_payload),
                    _json(payload),
                    proposed.runtime_control_revision,
                    proposed.runtime_control_hash,
                    proposed.rollout_stage,
                    proposed.rollout_evidence_revision,
                    proposed.rollout_evidence_hash,
                    global_risk_approval.global_risk_decision_id if global_risk_approval is not None else "",
                    global_risk_approval.global_state_hash if global_risk_approval is not None else "",
                    global_risk_approval.global_state_revision if global_risk_approval is not None else "",
                    _dt(global_risk_approval.expires_at) if global_risk_approval is not None and global_risk_approval.expires_at is not None else None,
                    authoritative_state_hash,
                ),
            )
            if cursor.rowcount != 1:
                existing = self._outbox_by_idempotency_key(conn, idempotency_key)
                if existing is not None:
                    return WcaOutboxReservation(False, existing.outbox_id, existing.proposed_order, existing.idempotency_key, existing.client_order_id)
                raise RuntimeError("failed to reserve WCA execution outbox")
            self._record_inventory_event_in_conn(
                conn,
                WcaInventoryLedgerEvent(
                    inventory_event_id=f"wca-order-intent-event-{proposed.order_intent_id}",
                    event_type="ORDER_INTENT_RESERVED",
                    broker_account_id=account_id,
                    symbol=proposed.symbol,
                    event_timestamp=common["timestamp"],
                    trade_date=common["timestamp"][:10],
                    order_intent_id=proposed.order_intent_id,
                    client_order_id=client_order_id,
                    side=_value(proposed.side),
                    quantity=proposed.quantity,
                    remaining_quantity=proposed.quantity,
                    configuration_version=common["configuration_version"],
                    decision_id=common["decision_id"],
                    run_id=common["run_id"],
                    source_authority="wca_repository",
                    payload=payload,
                ),
            )
            reserved_risk = _reserved_risk_from_decision(decision_to_persist)
            if reserved_risk > 0:
                self._record_inventory_event_in_conn(
                    conn,
                    WcaInventoryLedgerEvent(
                        inventory_event_id=f"wca-risk-reserved-event-{proposed.order_intent_id}",
                        event_type="RISK_RESERVED",
                        broker_account_id=account_id,
                        symbol=proposed.symbol,
                        event_timestamp=common["timestamp"],
                        trade_date=common["timestamp"][:10],
                        order_intent_id=proposed.order_intent_id,
                        client_order_id=client_order_id,
                        side=_value(proposed.side),
                        quantity=proposed.quantity,
                        remaining_quantity=proposed.quantity,
                        reserved_risk=reserved_risk,
                        configuration_version=common["configuration_version"],
                        decision_id=common["decision_id"],
                        run_id=common["run_id"],
                        source_authority="wca_repository",
                        payload=payload,
                    ),
                )
        return WcaOutboxReservation(True, outbox_id, proposed, idempotency_key, client_order_id)

    def claim_next_execution_outbox(self, *, owner_id: str) -> WcaExecutionOutboxRecord | None:
        now = _utc_now()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wca_execution_outbox
                WHERE algorithm_id = ? AND status = ?
                ORDER BY created_at
                LIMIT 1
                """,
                (WCA_ALGORITHM_ID, WcaOrderStatus.OUTBOX_RESERVED.value),
            ).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                """
                UPDATE wca_execution_outbox
                SET status = ?, version = version + 1, claim_owner = ?, claimed_at = ?, updated_at = ?
                WHERE outbox_id = ? AND status = ?
                """,
                (
                    validate_wca_order_state_transition(row["status"], WcaOrderStatus.SUBMITTING),
                    owner_id,
                    now,
                    now,
                    row["outbox_id"],
                    coerce_wca_order_status(row["status"]),
                ),
            )
            if cursor.rowcount != 1:
                return None
            conn.execute(
                "UPDATE wca_attributed_orders SET status = ? WHERE order_intent_id = ? AND algorithm_id = ?",
                (WcaOrderStatus.SUBMITTING.value, row["order_intent_id"], WCA_ALGORITHM_ID),
            )
            claimed = conn.execute("SELECT * FROM wca_execution_outbox WHERE outbox_id = ?", (row["outbox_id"],)).fetchone()
            return _outbox_record_from_row(claimed)

    def list_execution_outbox_records(self, *, account_id: str | None = None) -> tuple[WcaExecutionOutboxRecord, ...]:
        sql = "SELECT * FROM wca_execution_outbox WHERE algorithm_id = ?"
        params: tuple[str, ...] = (WCA_ALGORITHM_ID,)
        if account_id is not None:
            sql += " AND account_id = ?"
            params = (WCA_ALGORITHM_ID, account_id)
        sql += " ORDER BY created_at"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return tuple(_outbox_record_from_row(row) for row in rows)

    def read_global_risk_approval(self, *, decision_id: str) -> WcaGlobalRiskApprovalRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wca_global_risk_responses
                WHERE algorithm_id = ? AND decision_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (WCA_ALGORITHM_ID, decision_id),
            ).fetchone()
        return _global_risk_approval_from_row(row) if row is not None else None

    def update_execution_outbox_state(self, *, outbox_id: str, status: WcaOrderStatus | str, response_payload: dict[str, Any] | None = None, error_payload: dict[str, Any] | None = None) -> bool:
        now = _utc_now()
        target_status = coerce_wca_order_status(status)
        response = response_payload or {}
        error = error_payload or {}
        with self.connect() as conn:
            row = conn.execute("SELECT order_intent_id, status FROM wca_execution_outbox WHERE outbox_id = ? AND algorithm_id = ?", (outbox_id, WCA_ALGORITHM_ID)).fetchone()
            if row is None:
                return False
            status_value = validate_wca_order_state_transition(row["status"], target_status)
            cursor = conn.execute(
                """
                UPDATE wca_execution_outbox
                SET status = ?, version = version + 1, response_payload_json = ?,
                    error_payload_json = ?,
                    submitted_at = CASE WHEN ? IN (?, ?, ?, ?, ?, ?) THEN COALESCE(submitted_at, ?) ELSE submitted_at END,
                    acknowledged_at = CASE WHEN ? IN (?, ?, ?, ?, ?) THEN COALESCE(acknowledged_at, ?) ELSE acknowledged_at END,
                    updated_at = ?
                WHERE outbox_id = ? AND algorithm_id = ? AND status = ?
                """,
                (
                    status_value,
                    _json(response),
                    _json(error),
                    status_value,
                    WcaOrderStatus.SUBMISSION_UNKNOWN.value,
                    WcaOrderStatus.BROKER_ACKNOWLEDGED.value,
                    WcaOrderStatus.PARTIALLY_FILLED.value,
                    WcaOrderStatus.FILLED.value,
                    WcaOrderStatus.REJECTED.value,
                    WcaOrderStatus.RECONCILIATION_REQUIRED.value,
                    now,
                    status_value,
                    WcaOrderStatus.BROKER_ACKNOWLEDGED.value,
                    WcaOrderStatus.PARTIALLY_FILLED.value,
                    WcaOrderStatus.FILLED.value,
                    WcaOrderStatus.REJECTED.value,
                    WcaOrderStatus.RECONCILIATION_REQUIRED.value,
                    now,
                    now,
                    outbox_id,
                    WCA_ALGORITHM_ID,
                    coerce_wca_order_status(row["status"]),
                ),
            )
            conn.execute(
                "UPDATE wca_attributed_orders SET status = ? WHERE order_intent_id = ? AND algorithm_id = ?",
                (status_value, row["order_intent_id"], WCA_ALGORITHM_ID),
            )
        return cursor.rowcount == 1

    def record_broker_order(self, decision: WcaDecision, *, broker_order_id: str, account_id: str, idempotency_key: str, status: str, payload: dict[str, Any] | None = None) -> bool:
        if decision.proposed_order is None:
            raise ValueError("cannot record WCA broker order without an order intent")
        common = _decision_common(decision, decision.decision_id)
        proposed = decision.proposed_order
        record = payload or proposed.model_dump(mode="json")
        request_payload = record.get("request", {}) if isinstance(record, dict) else {}
        response_payload = record.get("response", {}) if isinstance(record, dict) else {}
        client_order_id = str(record.get("client_order_id", "")) if isinstance(record, dict) else ""
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO wca_broker_orders (
                    broker_order_id, algorithm_id, account_id, symbol, timestamp,
                    configuration_version, engine_version, market_snapshot_id,
                    decision_id, run_id, order_intent_id, idempotency_key, side,
                    quantity, status, client_order_id, request_payload_json, response_payload_json, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (broker_order_id, common["algorithm_id"], account_id, common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], proposed.order_intent_id, idempotency_key, _value(proposed.side), proposed.quantity, coerce_wca_order_status(status), client_order_id, _json(request_payload), _json(response_payload), _json(record)),
            )
        return cursor.rowcount == 1

    def apply_fill_and_update_position(self, decision: WcaDecision, *, fill_id: str, account_id: str, quantity: int, broker_order_id: str | None = None, payload: dict[str, Any] | None = None) -> bool:
        if decision.proposed_order is None:
            raise ValueError("cannot apply WCA fill without an order intent")
        if quantity < 0:
            raise ValueError("WCA fill quantity cannot be negative")
        common = _decision_common(decision, decision.decision_id)
        proposed = decision.proposed_order
        position_id = f"wca-position-{account_id}-{proposed.symbol}-{proposed.order_intent_id}"
        lot_id = f"wca-lot-{fill_id}"
        virtual_position_id = f"wca-virtual-{account_id}-{proposed.symbol}"
        record = dict(payload or {})
        record.setdefault("fill_id", fill_id)
        record.setdefault("broker_order_id", broker_order_id)
        record.setdefault("order_intent_id", proposed.order_intent_id)
        record.setdefault("decision_id", proposed.decision_id)
        record.setdefault("account_id", account_id)
        record.setdefault("symbol", proposed.symbol)
        record.setdefault("side", _value(proposed.side))
        record.setdefault("entry_price", _entry_price_from_payload(record, proposed))
        record.setdefault("stop_price", proposed.stop_price)
        record.setdefault("target_price", proposed.target_price)
        record.setdefault("opened_at", _dt(_fill_timestamp_from_payload(record) or decision.decision_timestamp))
        record.setdefault("position_effect", "entry")
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO wca_attributed_fills (
                    fill_id, algorithm_id, account_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, side,
                    quantity, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (fill_id, common["algorithm_id"], account_id, common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], _value(proposed.side), quantity, _json(record)),
            )
            if cursor.rowcount != 1:
                return False
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_positions (
                    position_id, algorithm_id, account_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, side,
                    quantity, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (position_id, common["algorithm_id"], account_id, common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], _value(proposed.side), quantity, _json(record)),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_owned_lots (
                    lot_id, algorithm_id, account_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, position_id,
                    side, quantity, status, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (lot_id, common["algorithm_id"], account_id, common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], position_id, _value(proposed.side), quantity, "open", _json(record)),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_virtual_positions (
                    virtual_position_id, algorithm_id, account_id, symbol, timestamp,
                    configuration_version, engine_version, market_snapshot_id,
                    decision_id, run_id, side, quantity, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (virtual_position_id, common["algorithm_id"], account_id, common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], _value(proposed.side), quantity, _json(record)),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_exit_state (
                    exit_state_id, algorithm_id, account_id, symbol, timestamp,
                    configuration_version, engine_version, market_snapshot_id,
                    decision_id, run_id, position_id, status, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"wca-exit-{position_id}", common["algorithm_id"], account_id, common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], position_id, "monitoring", _json(record)),
            )
        remaining_quantity = max(0, int(record.get("remaining_quantity") or proposed.quantity - quantity))
        fill_event_type = "PARTIAL_FILL_RECEIVED" if quantity < proposed.quantity or remaining_quantity > 0 else "FILL_RECEIVED"
        self.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id=f"wca-fill-event-{fill_id}",
                event_type=fill_event_type,
                broker_account_id=account_id,
                symbol=proposed.symbol,
                event_timestamp=record["opened_at"],
                trade_date=str(record["opened_at"])[:10],
                order_intent_id=proposed.order_intent_id,
                client_order_id=str(record.get("client_order_id") or ""),
                broker_order_id=broker_order_id,
                fill_id=fill_id,
                side=_value(proposed.side),
                quantity=proposed.quantity,
                filled_quantity=quantity,
                remaining_quantity=remaining_quantity,
                average_entry_price=float(record["entry_price"]),
                fill_price=float(record["entry_price"]),
                configuration_version=common["configuration_version"],
                decision_id=common["decision_id"],
                run_id=common["run_id"],
                source_authority="wca_repository",
                payload=record,
            )
        )
        released_risk = _reserved_risk_release_for_fill(self, decision, account_id=account_id, quantity=quantity)
        if released_risk > 0:
            self.record_inventory_event(
                WcaInventoryLedgerEvent(
                    inventory_event_id=f"wca-risk-released-fill-event-{fill_id}",
                    event_type="RISK_RELEASED",
                    broker_account_id=account_id,
                    symbol=proposed.symbol,
                    event_timestamp=record["opened_at"],
                    trade_date=str(record["opened_at"])[:10],
                    order_intent_id=proposed.order_intent_id,
                    client_order_id=str(record.get("client_order_id") or ""),
                    broker_order_id=broker_order_id,
                    fill_id=None,
                    side=_value(proposed.side),
                    quantity=proposed.quantity,
                    filled_quantity=quantity,
                    remaining_quantity=remaining_quantity,
                    reserved_risk=released_risk,
                    configuration_version=common["configuration_version"],
                    decision_id=common["decision_id"],
                    run_id=common["run_id"],
                    source_authority="wca_repository",
                    payload={**record, "reserved_risk_released": released_risk},
                )
            )
        self.record_protective_order_created(
            decision.model_copy(update={"proposed_order": proposed}),
            account_id=account_id,
            client_order_id=str(record.get("client_order_id") or ""),
            broker_order_id=broker_order_id,
            source_fill_id=fill_id,
            protected_quantity=quantity,
            event_timestamp=record["opened_at"],
            payload=record,
        )
        return True

    def record_inventory_event(self, event: WcaInventoryLedgerEvent) -> bool:
        _require_wca_identity(event.algorithm_id)
        _require_broker_account_identity(event.broker_account_id)
        _validate_inventory_event(event)
        with self.connect() as conn:
            return self._record_inventory_event_in_conn(conn, event)

    def record_protective_order_created(
        self,
        decision: WcaDecision,
        *,
        account_id: str,
        client_order_id: str = "",
        broker_order_id: str | None = None,
        source_fill_id: str | None = None,
        protected_quantity: int | None = None,
        event_timestamp: datetime | str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        proposed = decision.proposed_order
        if proposed is None:
            raise ValueError("cannot record WCA protective order without an order intent")
        if proposed.stop_price is None and proposed.target_price is None:
            return False
        timestamp = _dt(event_timestamp or decision.decision_timestamp)
        event_suffix = source_fill_id or client_order_id or broker_order_id or proposed.order_intent_id
        protective_order_id = f"wca-protection-{account_id}-{proposed.symbol}-{proposed.order_intent_id}"
        event_payload = {
            **(payload or {}),
            "protective_order_id": protective_order_id,
            "protective_state": "created",
            "source_fill_id": source_fill_id,
            "stop_price": proposed.stop_price,
            "target_price": proposed.target_price,
            "protected_quantity": int(protected_quantity or proposed.quantity),
        }
        return self.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id=f"wca-protective-order-event-{proposed.order_intent_id}-{event_suffix}",
                event_type="PROTECTIVE_ORDER_CREATED",
                broker_account_id=account_id,
                symbol=proposed.symbol,
                event_timestamp=timestamp,
                trade_date=timestamp[:10],
                order_intent_id=proposed.order_intent_id,
                client_order_id=client_order_id,
                broker_order_id=broker_order_id,
                side=_value(proposed.side),
                quantity=int(protected_quantity or proposed.quantity),
                remaining_quantity=0,
                average_entry_price=float(proposed.limit_price or proposed.trigger_price or 0),
                source_authority="wca_position_management",
                configuration_version=decision.configuration_version,
                decision_id=decision.decision_id,
                run_id=decision.decision_id,
                payload=event_payload,
            )
        )

    def record_order_terminal_inventory_event(
        self,
        decision: WcaDecision,
        *,
        account_id: str,
        client_order_id: str = "",
        broker_order_id: str | None = None,
        event_type: str,
        event_timestamp: datetime | str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        if event_type not in {"ORDER_REJECTED", "ORDER_CANCELLED"}:
            raise ValueError("WCA terminal order inventory event must be ORDER_REJECTED or ORDER_CANCELLED")
        proposed = decision.proposed_order
        if proposed is None:
            raise ValueError("cannot record WCA terminal order event without an order intent")
        timestamp = _dt(event_timestamp or decision.decision_timestamp)
        reserved_risk = _reserved_risk_from_decision(decision)
        return self.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id=f"wca-{event_type.lower()}-event-{proposed.order_intent_id}",
                event_type=event_type,
                broker_account_id=account_id,
                symbol=proposed.symbol,
                event_timestamp=timestamp,
                trade_date=timestamp[:10],
                order_intent_id=proposed.order_intent_id,
                client_order_id=client_order_id,
                broker_order_id=broker_order_id,
                side=_value(proposed.side),
                quantity=proposed.quantity,
                remaining_quantity=proposed.quantity,
                reserved_risk=reserved_risk,
                source_authority="wca_order_lifecycle",
                configuration_version=decision.configuration_version,
                decision_id=decision.decision_id,
                run_id=decision.decision_id,
                payload=payload or {},
            )
        )

    def record_position_management_critical_event(self, position: WcaManagedPosition, *, evaluated_at: datetime) -> bool:
        timestamp = _dt(evaluated_at)
        return self.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id=f"wca-position-critical-event-{position.account_id}-{position.symbol}-{timestamp}",
                event_type="RECONCILIATION_CORRECTION",
                broker_account_id=position.account_id,
                symbol=position.symbol,
                event_timestamp=timestamp,
                trade_date=timestamp[:10],
                side=_value(position.side),
                quantity=position.open_quantity,
                average_entry_price=position.average_entry_price,
                unrealized_pnl=position.unrealized_pnl,
                source_authority="wca_position_management",
                configuration_version="wca_position_management",
                decision_id=f"wca-position-management-{position.account_id}-{position.symbol}",
                run_id="wca-position-management",
                payload={
                    "critical": True,
                    "circuit_breaker_state": "open",
                    "protective_exit_required": True,
                    "pending_exit_orders": [order.model_dump(mode="json") for order in position.pending_exit_orders],
                    "reason_codes": position.reason_codes,
                },
            )
        )

    def read_inventory_projection(self, *, algorithm_id: str, broker_account_id: str, symbol: str) -> WcaInventoryProjection:
        _require_wca_identity(algorithm_id)
        _require_broker_account_identity(broker_account_id)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wca_inventory_projection
                WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?
                """,
                (algorithm_id, broker_account_id, symbol),
            ).fetchone()
        if row is None:
            return WcaInventoryProjection(algorithm_id, broker_account_id, symbol, 0, 0.0, 0.0, 0.0, 0.0, None, None, "", "", "")
        return WcaInventoryProjection(
            row["algorithm_id"],
            row["broker_account_id"],
            row["symbol"],
            int(row["open_quantity"]),
            float(row["average_entry_price"]),
            float(row["realized_pnl"]),
            float(row["unrealized_pnl"]),
            float(row["reserved_risk"]),
            row["last_event_timestamp"],
            row["reconciliation_watermark"],
            row["configuration_version"],
            row["decision_id"],
            row["run_id"],
        )

    def read_daily_state_projection(self, *, algorithm_id: str, broker_account_id: str, symbol: str, session_date: str) -> WcaDailyStateProjection:
        _require_wca_identity(algorithm_id)
        _require_broker_account_identity(broker_account_id)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wca_daily_state
                WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ? AND session_date = ?
                """,
                (algorithm_id, broker_account_id, symbol, session_date),
            ).fetchone()
        if row is None:
            return WcaDailyStateProjection(algorithm_id, broker_account_id, symbol, session_date, 0, 0, 0.0, 0.0, 0, 0.0, 0.0, None, None, None, "closed", None, True)
        return WcaDailyStateProjection(
            row["algorithm_id"],
            row["broker_account_id"],
            row["symbol"],
            row["session_date"],
            int(row["trades_completed_today"]),
            int(row["entries_attempted_today"]),
            float(row["realized_pnl_today"]),
            float(row["daily_loss"]),
            int(row["consecutive_losses"]),
            float(row["current_reserved_risk"]),
            float(row["maximum_intraday_exposure"]),
            row["cooldown_until"],
            row["last_entry_timestamp"],
            row["last_exit_timestamp"],
            row["circuit_breaker_state"],
            row["last_successful_reconciliation"],
            bool(row["isolation_enforced"]),
        )

    def list_inventory_ledger_events(self, *, algorithm_id: str, broker_account_id: str, symbol: str) -> tuple[WcaInventoryLedgerEvent, ...]:
        _require_wca_identity(algorithm_id)
        _require_broker_account_identity(broker_account_id)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM wca_inventory_ledger
                WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?
                ORDER BY event_timestamp, inventory_event_id
                """,
                (algorithm_id, broker_account_id, symbol),
            ).fetchall()
        return tuple(_inventory_event_from_row(row) for row in rows)

    def rebuild_inventory_projections(self, *, algorithm_id: str, broker_account_id: str, symbol: str) -> None:
        _require_wca_identity(algorithm_id)
        _require_broker_account_identity(broker_account_id)
        with self.connect() as conn:
            self._rebuild_inventory_projections_in_conn(conn, algorithm_id=algorithm_id, broker_account_id=broker_account_id, symbol=symbol)

    def write_broker_account_snapshot(
        self,
        snapshot: BrokerAccountSnapshot,
        *,
        symbol: str = "SPY",
        cash: float | None = None,
        account_status: str = "active",
        pattern_day_trading_restrictions: str | None = None,
        trading_restrictions: tuple[str, ...] = (),
        configuration_version: str = "",
        decision_id: str = "",
        run_id: str = "",
    ) -> str:
        _require_broker_account_identity(snapshot.accountId)
        if cash is not None and cash < 0:
            raise ValueError("WCA broker snapshot cash cannot be negative")
        observed_at = _dt(snapshot.observedAt)
        broker_snapshot_id = f"wca-broker-snapshot-{snapshot.accountId}-{observed_at}-{_hash_json(snapshot.model_dump(mode='json'))[:12]}"
        payload = {
            "snapshot": snapshot.model_dump(mode="json"),
            "cash": cash,
            "account_status": account_status,
            "pattern_day_trading_restrictions": pattern_day_trading_restrictions,
            "trading_restrictions": list(trading_restrictions),
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_broker_account_snapshots (
                    broker_snapshot_id, algorithm_id, broker_account_id, symbol, timestamp,
                    configuration_version, engine_version, market_snapshot_id, decision_id,
                    run_id, observed_at, session_date, source_authority, equity, buying_power,
                    cash, account_status, pattern_day_trading_restrictions,
                    trading_restrictions_json, positions_json, pending_orders_json,
                    partially_filled_orders_json, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    broker_snapshot_id,
                    WCA_ALGORITHM_ID,
                    snapshot.accountId,
                    symbol,
                    observed_at,
                    configuration_version,
                    "wca_authoritative_runtime_state_v1",
                    f"wca-broker-{snapshot.accountId}-{observed_at}",
                    decision_id,
                    run_id,
                    observed_at,
                    snapshot.sessionDate.isoformat(),
                    snapshot.sourceAuthority,
                    float(snapshot.equity),
                    float(snapshot.buyingPower),
                    float(cash) if cash is not None else None,
                    account_status,
                    pattern_day_trading_restrictions,
                    _json(trading_restrictions),
                    _json([position.model_dump(mode="json") for position in snapshot.positions]),
                    _json([order.model_dump(mode="json") for order in snapshot.pendingOrders]),
                    _json([order.model_dump(mode="json") for order in snapshot.partiallyFilledOrders]),
                    _json(payload),
                ),
            )
        return broker_snapshot_id

    def read_latest_broker_account_snapshot(self, *, algorithm_id: str, broker_account_id: str) -> BrokerAccountSnapshot | None:
        _require_wca_identity(algorithm_id)
        _require_broker_account_identity(broker_account_id)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM wca_broker_account_snapshots
                WHERE algorithm_id = ? AND broker_account_id = ?
                ORDER BY observed_at DESC, created_at DESC
                LIMIT 1
                """,
                (algorithm_id, broker_account_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"] or "{}")
        return BrokerAccountSnapshot.model_validate(payload["snapshot"])

    def authorize_wca_lot_reduction(self, *, lot_id: str, account_id: str, symbol: str, quantity: int) -> WcaInventoryOwnershipDecision:
        if quantity <= 0:
            return WcaInventoryOwnershipDecision(False, ("wca.invalid_reduction_quantity",))
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT algorithm_id, account_id, symbol, quantity, status
                FROM wca_owned_lots
                WHERE lot_id = ?
                """,
                (lot_id,),
            ).fetchone()
        if row is None:
            return WcaInventoryOwnershipDecision(False, ("wca.lot_not_owned",))
        reasons: list[str] = []
        if row["algorithm_id"] != WCA_ALGORITHM_ID:
            reasons.append("wca.lot_algorithm_mismatch")
        if row["account_id"] != account_id:
            reasons.append("wca.lot_account_mismatch")
        if row["symbol"] != symbol:
            reasons.append("wca.lot_symbol_mismatch")
        if row["status"] != "open":
            reasons.append("wca.lot_not_open")
        if int(row["quantity"]) < quantity:
            reasons.append("wca.lot_quantity_exceeded")
        if reasons:
            return WcaInventoryOwnershipDecision(False, tuple(reasons))
        return WcaInventoryOwnershipDecision(True, ("wca.lot_owned",))

    def list_open_wca_lots(self, *, account_id: str, symbol: str) -> tuple[dict[str, Any], ...]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT lot_id, account_id, symbol, timestamp, configuration_version, engine_version,
                       market_snapshot_id, decision_id, run_id, position_id, side, quantity, payload_json
                FROM wca_owned_lots
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND status = 'open' AND quantity > 0
                ORDER BY created_at, lot_id
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        lots: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            fill = payload.get("fill") if isinstance(payload.get("fill"), dict) else {}
            lots.append(
                {
                    "lot_id": row["lot_id"],
                    "account_id": row["account_id"],
                    "symbol": row["symbol"],
                    "timestamp": row["timestamp"],
                    "configuration_version": row["configuration_version"],
                    "engine_version": row["engine_version"],
                    "market_snapshot_id": row["market_snapshot_id"],
                    "decision_id": row["decision_id"],
                    "run_id": row["run_id"],
                    "position_id": row["position_id"],
                    "side": row["side"],
                    "quantity": int(row["quantity"]),
                    "entry_price": _optional_positive_float(payload.get("entry_price") or fill.get("average_fill_price") or fill.get("averageFillPrice")),
                    "stop_price": payload.get("stop_price"),
                    "target_price": payload.get("target_price"),
                    "opened_at": payload.get("opened_at") or row["timestamp"],
                    "payload": payload,
                }
            )
        return tuple(lots)

    def write_position_management_snapshot(self, position: WcaManagedPosition, *, evaluated_at: datetime) -> None:
        common = _common_row(
            symbol=position.symbol,
            timestamp=_dt(evaluated_at),
            configuration_version="wca_position_management",
            engine_version="wca_position_manager_v1",
            market_snapshot_id=f"wca-position-management-{position.account_id}-{position.symbol}",
            decision_id=f"wca-position-management-{position.account_id}-{position.symbol}",
            run_id="wca-position-management",
            account_id=position.account_id,
        )
        virtual_position_id = f"wca-virtual-{position.account_id}-{position.symbol}"
        exit_state_id = f"wca-exit-state-{position.account_id}-{position.symbol}"
        status = "flat"
        if position.open_quantity > 0:
            status = "circuit_breaker_open" if position.circuit_breaker_open else ("protective_exit_pending" if position.pending_exit_orders else "monitoring")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO wca_virtual_positions (
                    virtual_position_id, algorithm_id, account_id, symbol, timestamp,
                    configuration_version, engine_version, market_snapshot_id,
                    decision_id, run_id, side, quantity, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(virtual_position_id) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    side = excluded.side,
                    quantity = excluded.quantity,
                    payload_json = excluded.payload_json,
                    version = wca_virtual_positions.version + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (virtual_position_id, common["algorithm_id"], common["account_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], _value(position.side), position.open_quantity, position.model_dump_json()),
            )
            conn.execute(
                """
                INSERT INTO wca_exit_state (
                    exit_state_id, algorithm_id, account_id, symbol, timestamp,
                    configuration_version, engine_version, market_snapshot_id,
                    decision_id, run_id, position_id, status, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exit_state_id) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    status = excluded.status,
                    payload_json = excluded.payload_json,
                    version = wca_exit_state.version + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (exit_state_id, common["algorithm_id"], common["account_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], virtual_position_id, status, position.model_dump_json()),
            )

    def close_wca_attributed_position_quantity(
        self,
        *,
        account_id: str,
        symbol: str,
        quantity: int,
        exit_price: float,
        exit_reason: str,
        evaluated_at: datetime,
        client_order_id: str | None = None,
        broker_order_id: str | None = None,
        fill_id: str | None = None,
        payload: dict[str, Any] | None = None,
        record_inventory_event: bool = True,
    ) -> bool:
        if quantity <= 0 or exit_price <= 0:
            return False
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT lot_id, position_id, timestamp, configuration_version, engine_version, market_snapshot_id,
                       decision_id, run_id, side, quantity, version, payload_json
                FROM wca_owned_lots
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND status = 'open' AND quantity > 0
                ORDER BY created_at, lot_id
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
            available = sum(int(row["quantity"]) for row in rows)
            if available < quantity:
                return False
            first = rows[0]
            common = _common_row(
                symbol=symbol,
                timestamp=_dt(evaluated_at),
                configuration_version=first["configuration_version"],
                engine_version=first["engine_version"],
                market_snapshot_id=first["market_snapshot_id"],
                decision_id=first["decision_id"],
                run_id=first["run_id"],
                account_id=account_id,
            )
            exit_payload = {
                **(payload or {}),
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "position_effect": "exit",
                "client_order_id": client_order_id,
                "broker_order_id": broker_order_id,
                "fill_id": fill_id,
            }
            if fill_id:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO wca_attributed_fills (
                        fill_id, algorithm_id, account_id, symbol, timestamp, configuration_version,
                        engine_version, market_snapshot_id, decision_id, run_id, side,
                        quantity, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fill_id,
                        common["algorithm_id"],
                        common["account_id"],
                        common["symbol"],
                        common["timestamp"],
                        common["configuration_version"],
                        common["engine_version"],
                        common["market_snapshot_id"],
                        common["decision_id"],
                        common["run_id"],
                        "EXIT",
                        quantity,
                        _json(exit_payload),
                    ),
                )
                if cursor.rowcount != 1:
                    return False
            remaining = quantity
            total_realized_pnl = 0.0
            close_side = "SELL"
            for row in rows:
                if remaining <= 0:
                    break
                open_qty = int(row["quantity"])
                close_qty = min(open_qty, remaining)
                payload = json.loads(row["payload_json"] or "{}")
                entry_price = _optional_positive_float(payload.get("entry_price"))
                if entry_price is None:
                    raise ValueError("WCA cannot close a lot with missing authoritative entry price")
                side = _value(row["side"])
                pnl = _realized_pnl(side, entry_price, exit_price, close_qty)
                total_realized_pnl = round(total_realized_pnl + pnl, 10)
                new_qty = open_qty - close_qty
                new_status = "open" if new_qty > 0 else "closed"
                new_payload = {**payload, "last_exit_price": exit_price, "last_exit_reason": exit_reason, "closed_quantity": int(payload.get("closed_quantity") or 0) + close_qty}
                updated = conn.execute(
                    """
                    UPDATE wca_owned_lots
                    SET quantity = ?, status = ?, version = version + 1, payload_json = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE lot_id = ? AND version = ? AND algorithm_id = ? AND account_id = ? AND symbol = ?
                    """,
                    (new_qty, new_status, _json(new_payload), row["lot_id"], int(row["version"]), WCA_ALGORITHM_ID, account_id, symbol),
                )
                if updated.rowcount != 1:
                    raise RuntimeError("WCA lot close compare-and-swap failed")
                close_side = "SELL" if side == "BUY" else "BUY"
                trade_payload = {
                    **new_payload,
                    **exit_payload,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "lot_id": row["lot_id"],
                }
                conn.execute(
                    """
                    INSERT OR REPLACE INTO wca_trade_ledger (
                        trade_id, algorithm_id, account_id, symbol, timestamp, configuration_version,
                        engine_version, market_snapshot_id, decision_id, run_id, side,
                        quantity, pnl, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (f"wca-trade-close-{row['lot_id']}-{int(row['version'])}", common["algorithm_id"], common["account_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], close_side, close_qty, pnl, _json(trade_payload)),
                )
                remaining -= close_qty
            event_type = "POSITION_CLOSED" if available == quantity else "POSITION_REDUCED"
            if record_inventory_event:
                self._record_inventory_event_in_conn(
                    conn,
                    WcaInventoryLedgerEvent(
                        inventory_event_id=f"wca-exit-fill-event-{fill_id}" if fill_id else f"wca-position-exit-event-{account_id}-{symbol}-{_dt(evaluated_at)}-{abs(hash((quantity, exit_price, exit_reason))) }",
                        event_type=event_type,
                        broker_account_id=account_id,
                        symbol=symbol,
                        event_timestamp=evaluated_at,
                        trade_date=_dt(evaluated_at)[:10],
                        client_order_id=client_order_id,
                        broker_order_id=broker_order_id,
                        fill_id=fill_id,
                        side=close_side,
                        quantity=quantity,
                        filled_quantity=quantity,
                        remaining_quantity=0,
                        fill_price=exit_price,
                        realized_pnl=total_realized_pnl,
                        source_authority="wca_broker_fill_polling" if fill_id else "wca_position_management",
                        configuration_version=common["configuration_version"],
                        decision_id=common["decision_id"],
                        run_id=common["run_id"],
                        payload=exit_payload,
                    ),
                )
        return True

    def realized_pnl_for_wca_position(self, *, account_id: str, symbol: str) -> float:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(pnl), 0)
                FROM wca_trade_ledger
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchone()
        return float(row[0] or 0.0)

    def open_wca_position_quantity(self, *, account_id: str, symbol: str) -> int:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT side, quantity
                FROM wca_owned_lots
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND status = 'open' AND quantity > 0
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        return sum(_signed_quantity(row["side"], int(row["quantity"])) for row in rows)

    def wca_position_circuit_breaker_open(self, *, account_id: str, symbol: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT status
                FROM wca_exit_state
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                ORDER BY timestamp DESC, updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchone()
        return bool(row and row["status"] == "circuit_breaker_open")

    def reconciliation_blocks_new_entries(self, *, account_id: str = "paper", symbol: str = "SPY") -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT hard_operational_warning, discrepancy_count
                FROM wca_broker_reconciliations
                WHERE account_id = ? AND symbol = ?
                ORDER BY timestamp DESC, created_at DESC
                LIMIT 1
                """,
                (account_id, symbol),
            ).fetchone()
        if row is None:
            return True
        return bool(int(row["hard_operational_warning"]) or int(row["discrepancy_count"]) > 0)

    def reserve_order_intent(self, decision: WcaDecision, *, run_id: str, account_id: str, idempotency_key: str) -> WcaOrderIntentReservation:
        if decision.proposed_order is None:
            raise ValueError("cannot reserve a missing WCA order intent")
        proposed = decision.proposed_order.model_copy(update={"idempotency_key": idempotency_key, "account_id": account_id})
        common = _decision_common(decision.model_copy(update={"proposed_order": proposed}), run_id)
        with self.connect() as conn:
            cursor = self._insert_order_records(conn, proposed, common, ignore_duplicates=True)
            row = conn.execute(
                "SELECT payload_json FROM wca_proposed_orders WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to read reserved WCA order intent")
        created = cursor.rowcount > 0
        if created:
            self.record_inventory_event(
                WcaInventoryLedgerEvent(
                    inventory_event_id=f"wca-order-intent-event-{proposed.order_intent_id}",
                    event_type="ORDER_INTENT_RESERVED",
                    broker_account_id=account_id,
                    symbol=proposed.symbol,
                    event_timestamp=common["timestamp"],
                    trade_date=common["timestamp"][:10],
                    order_intent_id=proposed.order_intent_id,
                    side=_value(proposed.side),
                    quantity=proposed.quantity,
                    remaining_quantity=proposed.quantity,
                    configuration_version=common["configuration_version"],
                    decision_id=common["decision_id"],
                    run_id=common["run_id"],
                    source_authority="wca_repository",
                    payload=proposed.model_dump(mode="json"),
                )
            )
        return WcaOrderIntentReservation(
            created=created,
            proposed_order=ProposedOrder.model_validate_json(row["payload_json"]),
            idempotency_key=idempotency_key,
        )

    def list_order_intents(self, *, account_id: str | None = None) -> tuple[ProposedOrder, ...]:
        sql = "SELECT payload_json FROM wca_proposed_orders"
        params: tuple[str, ...] = ()
        if account_id is not None:
            sql += " WHERE account_id = ?"
            params = (account_id,)
        sql += " ORDER BY created_at"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return tuple(ProposedOrder.model_validate_json(row["payload_json"]) for row in rows)

    def has_order_fill(self, order_intent_id: str) -> bool:
        pattern = f"%{order_intent_id}%"
        with self.connect() as conn:
            count = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM wca_execution_results WHERE payload_json LIKE ?)
                    + (SELECT COUNT(*) FROM wca_trade_ledger WHERE payload_json LIKE ?)
                    + (SELECT COUNT(*) FROM wca_attributed_fills WHERE payload_json LIKE ?)
                """,
                (pattern, pattern, pattern),
            ).fetchone()[0]
        return int(count) > 0

    def write_broker_reconciliation(self, result: WcaBrokerReconciliationResult) -> None:
        first = result.discrepancies[0] if result.discrepancies else None
        common = _common_row(
            symbol=first.symbol if first is not None else "SPY",
            timestamp=_dt(result.evaluated_at),
            configuration_version="wca_broker_reconciliation",
            engine_version=result.reconciliation_version,
            market_snapshot_id=result.reconciliation_id,
            decision_id=first.decision_id if first is not None and first.decision_id else result.reconciliation_id,
            run_id=result.reconciliation_id,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_broker_reconciliations (
                    reconciliation_id, algorithm_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, account_id,
                    discrepancy_count, hard_operational_warning, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.reconciliation_id,
                    common["algorithm_id"],
                    common["symbol"],
                    common["timestamp"],
                    common["configuration_version"],
                    common["engine_version"],
                    common["market_snapshot_id"],
                    common["decision_id"],
                    common["run_id"],
                    result.account_id,
                    len(result.discrepancies),
                    1 if result.hard_operational_warning else 0,
                    result.model_dump_json(),
                ),
            )
            if result.discrepancies:
                affected_symbols = sorted({row.symbol for row in result.discrepancies} or {common["symbol"]})
                for symbol in affected_symbols:
                    exit_state_id = f"wca-exit-state-{result.account_id}-{symbol}"
                    conn.execute(
                        """
                        INSERT INTO wca_exit_state (
                            exit_state_id, algorithm_id, account_id, symbol, timestamp,
                            configuration_version, engine_version, market_snapshot_id,
                            decision_id, run_id, position_id, status, payload_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(exit_state_id) DO UPDATE SET
                            timestamp = excluded.timestamp,
                            status = excluded.status,
                            payload_json = excluded.payload_json,
                            version = wca_exit_state.version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            exit_state_id,
                            WCA_ALGORITHM_ID,
                            result.account_id,
                            symbol,
                            common["timestamp"],
                            common["configuration_version"],
                            common["engine_version"],
                            result.reconciliation_id,
                            common["decision_id"],
                            common["run_id"],
                            f"wca-virtual-{result.account_id}-{symbol}",
                            "circuit_breaker_open",
                            result.model_dump_json(),
                        ),
                    )

    def write_shadow_comparison_evidence(self, evidence: WcaShadowComparisonEvidence) -> None:
        common = _common_row(
            symbol=evidence.symbol,
            timestamp=_dt(evidence.evaluated_at),
            configuration_version=evidence.evidence_version,
            engine_version=evidence.evidence_version,
            market_snapshot_id=evidence.snapshot_id,
            decision_id=evidence.snapshot_id,
            run_id=evidence.evidence_id,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_shadow_comparison_evidence (
                    evidence_id, algorithm_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, snapshot_id,
                    rollout_phase, within_tolerance, mismatch_count, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    common["algorithm_id"],
                    common["symbol"],
                    common["timestamp"],
                    common["configuration_version"],
                    common["engine_version"],
                    common["market_snapshot_id"],
                    common["decision_id"],
                    common["run_id"],
                    evidence.snapshot_id,
                    evidence.rollout_phase,
                    1 if evidence.within_tolerance else 0,
                    len(evidence.mismatched_fields),
                    evidence.model_dump_json(),
                ),
            )

    def write_paper_stability_validation(self, result: WcaPaperStabilityValidationResult) -> None:
        common = _common_row(
            symbol="SPY",
            timestamp=_dt(result.ended_at),
            configuration_version=result.validation_version,
            engine_version=result.validation_version,
            market_snapshot_id=result.validation_id,
            decision_id=result.validation_id,
            run_id=result.validation_id,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_paper_stability_validations (
                    validation_id, algorithm_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, account_id,
                    validation_days, market_condition_count, paper_trading_stable, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.validation_id,
                    common["algorithm_id"],
                    common["symbol"],
                    common["timestamp"],
                    common["configuration_version"],
                    common["engine_version"],
                    common["market_snapshot_id"],
                    common["decision_id"],
                    common["run_id"],
                    result.account_id,
                    result.validation_days,
                    len(result.market_conditions),
                    1 if result.paper_trading_stable else 0,
                    result.model_dump_json(),
                ),
            )

    def save_backtest_result(self, result: BacktestResult) -> None:
        config = result.run_configuration
        timestamp = _dt(config.end)
        market_snapshot_id = f"{config.run_id}-market"
        decision_id = f"{config.run_id}-run"
        common = _common_row(
            symbol=config.symbol,
            timestamp=timestamp,
            configuration_version=config.configuration_version,
            engine_version=str(result.metrics.get("engineVersion") or "wca_backtest_engine"),
            market_snapshot_id=market_snapshot_id,
            decision_id=decision_id,
            run_id=config.run_id,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_backtest_runs (
                    run_id, algorithm_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, total_pnl, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (config.run_id, common["algorithm_id"], config.symbol, timestamp, config.configuration_version, common["engine_version"], market_snapshot_id, decision_id, result.total_pnl, result.model_dump_json()),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_backtest_results (
                    run_id, algorithm_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (config.run_id, common["algorithm_id"], config.symbol, timestamp, config.configuration_version, common["engine_version"], market_snapshot_id, decision_id, result.model_dump_json()),
            )
            for decision in result.decisions:
                self._insert_decision(conn, decision, _decision_common(decision, config.run_id, engine_version=common["engine_version"]))
            for trade in result.trades:
                self._insert_trade(conn, trade, result, common)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO wca_execution_results (
                        execution_id, algorithm_id, symbol, timestamp, configuration_version,
                        engine_version, market_snapshot_id, decision_id, run_id, status, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{trade.trade_id}-execution",
                        common["algorithm_id"],
                        trade.symbol,
                        _dt(trade.exit_at or trade.entry_at),
                        config.configuration_version,
                        common["engine_version"],
                        f"{trade.decision_id}-market",
                        trade.decision_id,
                        config.run_id,
                        "filled",
                        trade.model_dump_json(),
                    ),
                )
            self._insert_strategy_performance(conn, result, common)

    def load_backtest_result(self, run_id: str) -> BacktestResult | None:
        with self.connect() as conn:
            row = conn.execute("SELECT payload_json FROM wca_backtest_runs WHERE run_id = ?", (run_id,)).fetchone()
        return BacktestResult.model_validate_json(row["payload_json"]) if row else None

    def table_counts(self) -> WcaPersistenceSummary:
        tables = (
            *WCA_PERSISTENCE_TABLES,
            "wca_strategy_versions",
            "wca_confidence_calibrations",
            "global_gate_evaluations",
            "wca_proposed_orders",
            "wca_execution_results",
            "wca_broker_reconciliations",
            "wca_backtest_trades",
            "wca_strategy_performance",
            "wca_local_gate_evaluations",
        )
        with self.connect() as conn:
            counts = {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}
        return WcaPersistenceSummary(table_counts=counts)

    def _insert_decision(self, conn: sqlite3.Connection, decision: WcaDecision, common: dict[str, str]) -> None:
        side = _value(decision.aggregation.post_local_gate_decision)
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_decisions (
                decision_id, algorithm_id, account_id, symbol, timestamp, configuration_version,
                engine_version, market_snapshot_id, run_id, side, payload_json,
                runtime_control_revision, runtime_control_hash, rollout_stage,
                rollout_evidence_revision, rollout_evidence_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.decision_id,
                common["algorithm_id"],
                common["account_id"],
                common["symbol"],
                common["timestamp"],
                common["configuration_version"],
                common["engine_version"],
                common["market_snapshot_id"],
                common["run_id"],
                side,
                decision.model_dump_json(),
                decision.runtime_control_revision,
                decision.runtime_control_hash,
                decision.rollout_stage,
                decision.rollout_evidence_revision,
                decision.rollout_evidence_hash,
            ),
        )
        self._insert_market_status(conn, decision.market_status, common)
        if decision.effective_settings is not None:
            self._insert_effective_settings(conn, decision.effective_settings, common)
        self._insert_strategy_settings_versions(conn, decision, common)
        self._insert_modifier_evaluations(conn, decision, common)
        for gate in _unique_gate_results(decision):
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_local_gate_evaluations (
                    algorithm_id, account_id, symbol, timestamp, configuration_version, engine_version,
                    market_snapshot_id, decision_id, run_id, gate_id, status, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (common["algorithm_id"], common["account_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], decision.decision_id, common["run_id"], gate.gate_id, _value(gate.status), gate.model_dump_json()),
            )
        if decision.global_gate_result is not None:
            self._insert_global_risk_response(conn, decision, common)
            conn.execute(
                """
                INSERT OR REPLACE INTO global_gate_evaluations (
                    algorithm_id, account_id, symbol, timestamp, configuration_version, engine_version,
                    market_snapshot_id, decision_id, run_id, status, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (common["algorithm_id"], common["account_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], decision.decision_id, common["run_id"], _value(decision.global_gate_result.status), decision.global_gate_result.model_dump_json()),
            )
        if decision.proposed_order is not None:
            self._insert_order_records(conn, decision.proposed_order, common)
        self._insert_strategy_evaluations(conn, decision, common)

    def _insert_trade(self, conn: sqlite3.Connection, trade, result: BacktestResult, common: dict[str, str]) -> None:
        config = result.run_configuration
        payload = trade.model_dump_json()
        values = (
            trade.trade_id,
            common["algorithm_id"],
            trade.symbol,
            _dt(trade.exit_at or trade.entry_at),
            config.configuration_version,
            common["engine_version"],
            f"{trade.decision_id}-market",
            trade.decision_id,
            config.run_id,
            _value(trade.side),
            trade.quantity,
            trade.pnl,
            payload,
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_trade_ledger (
                trade_id, algorithm_id, symbol, timestamp, configuration_version,
                engine_version, market_snapshot_id, decision_id, run_id, side,
                quantity, pnl, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_backtest_trades (
                trade_id, algorithm_id, symbol, timestamp, configuration_version,
                engine_version, market_snapshot_id, decision_id, run_id, side,
                quantity, pnl, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_attributed_fills (
                fill_id, algorithm_id, symbol, timestamp, configuration_version,
                engine_version, market_snapshot_id, decision_id, run_id, side,
                quantity, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{trade.trade_id}-fill",
                common["algorithm_id"],
                trade.symbol,
                _dt(trade.entry_at),
                config.configuration_version,
                common["engine_version"],
                f"{trade.decision_id}-market",
                trade.decision_id,
                config.run_id,
                _value(trade.side),
                trade.quantity,
                payload,
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_positions (
                position_id, algorithm_id, symbol, timestamp, configuration_version,
                engine_version, market_snapshot_id, decision_id, run_id, side,
                quantity, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{trade.trade_id}-position",
                common["algorithm_id"],
                trade.symbol,
                _dt(trade.exit_at or trade.entry_at),
                config.configuration_version,
                common["engine_version"],
                f"{trade.decision_id}-market",
                trade.decision_id,
                config.run_id,
                _value(trade.side),
                0 if trade.exit_at is not None else trade.quantity,
                payload,
            ),
        )

    def _insert_strategy_performance(self, conn: sqlite3.Connection, result: BacktestResult, common: dict[str, str]) -> None:
        by_strategy = result.metrics.get("diagnostics", {}).get("breakdowns", {}).get("byStrategy", {})
        for strategy_id, payload in by_strategy.items():
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_strategy_performance (
                    algorithm_id, symbol, timestamp, configuration_version, engine_version,
                    market_snapshot_id, decision_id, run_id, strategy_id, family, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (common["algorithm_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], strategy_id, str(payload.get("family", "unknown")), _json(payload)),
            )

    def _insert_strategy_evaluations(self, conn: sqlite3.Connection, decision: WcaDecision, common: dict[str, str]) -> None:
        for evaluation in decision.aggregation.strategy_evaluations:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_strategy_evaluations (
                    algorithm_id, account_id, symbol, timestamp, configuration_version, engine_version,
                    market_snapshot_id, decision_id, run_id, strategy_id, family, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    common["algorithm_id"],
                    common["account_id"],
                    common["symbol"],
                    common["timestamp"],
                    common["configuration_version"],
                    common["engine_version"],
                    common["market_snapshot_id"],
                    decision.decision_id,
                    common["run_id"],
                    evaluation.strategy_id,
                    next((row.family for row in decision.aggregation.strategy_contributions if row.strategy_id == evaluation.strategy_id), "unknown"),
                    evaluation.model_dump_json(),
                ),
            )

    def _insert_strategy_settings_versions(self, conn: sqlite3.Connection, decision: WcaDecision, common: dict[str, str]) -> None:
        definitions = {entry.strategy_id: entry for entry in WCA_STRATEGY_REGISTRY}
        for evaluation in decision.aggregation.strategy_evaluations:
            definition = definitions.get(evaluation.strategy_id)
            settings_version = definition.settings_version if definition is not None else "wca_strategy_settings_unknown"
            payload = {
                "algorithm_id": WCA_ALGORITHM_ID,
                "strategy_id": evaluation.strategy_id,
                "settings_model": definition.settings_model if definition is not None else "",
                "settings_version": settings_version,
                "strategy_version": evaluation.strategy_version,
                "configuration_version": decision.configuration_version,
                "configuration_hash": decision.configuration_hash,
                "called_module_version": decision.called_module_versions.get(f"strategy.{evaluation.strategy_id}") or decision.called_module_versions.get(evaluation.strategy_id),
            }
            conn.execute(
                """
                INSERT OR IGNORE INTO wca_strategy_settings_versions (
                    settings_key, algorithm_id, account_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id,
                    strategy_id, settings_version, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{decision.configuration_version}:{evaluation.strategy_id}:{settings_version}",
                    common["algorithm_id"],
                    common["account_id"],
                    common["symbol"],
                    common["timestamp"],
                    common["configuration_version"],
                    common["engine_version"],
                    common["market_snapshot_id"],
                    common["decision_id"],
                    common["run_id"],
                    evaluation.strategy_id,
                    settings_version,
                    _json(payload),
                ),
            )

    def _insert_modifier_evaluations(self, conn: sqlite3.Connection, decision: WcaDecision, common: dict[str, str]) -> None:
        for evaluation in decision.modifier_evaluations:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_modifier_evaluations (
                    algorithm_id, account_id, symbol, timestamp, configuration_version, engine_version,
                    market_snapshot_id, decision_id, run_id, modifier_id, status, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    common["algorithm_id"],
                    common["account_id"],
                    common["symbol"],
                    common["timestamp"],
                    common["configuration_version"],
                    common["engine_version"],
                    common["market_snapshot_id"],
                    common["decision_id"],
                    common["run_id"],
                    evaluation.modifier_id,
                    _value(evaluation.status),
                    evaluation.model_dump_json(),
                ),
            )

    def _insert_global_risk_response(self, conn: sqlite3.Connection, decision: WcaDecision, common: dict[str, str]) -> None:
        if decision.global_gate_result is None:
            return
        account_id = decision.proposed_order.account_id if decision.proposed_order is not None else "paper"
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_global_risk_responses (
                algorithm_id, account_id, symbol, timestamp, configuration_version,
                engine_version, market_snapshot_id, decision_id, run_id, status,
                proposed_quantity, allowed_quantity, global_risk_decision_id,
                evaluated_at, expires_at, entry_permitted,
                risk_reducing_exit_permitted, approved_risk_dollars,
                reason_codes_json, global_state_hash, global_state_revision,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                common["algorithm_id"],
                account_id,
                common["symbol"],
                common["timestamp"],
                common["configuration_version"],
                common["engine_version"],
                common["market_snapshot_id"],
                decision.decision_id,
                common["run_id"],
                _value(decision.global_gate_result.status),
                decision.global_gate_result.proposed_quantity,
                decision.global_gate_result.allowed_quantity,
                decision.global_gate_result.global_risk_decision_id,
                _dt(decision.global_gate_result.evaluated_at) if decision.global_gate_result.evaluated_at else None,
                _dt(decision.global_gate_result.expires_at) if decision.global_gate_result.expires_at else None,
                int(decision.global_gate_result.entry_permitted),
                int(decision.global_gate_result.risk_reducing_exit_permitted),
                float(decision.global_gate_result.approved_risk),
                _json(list(decision.global_gate_result.reason_codes)),
                decision.global_gate_result.global_state_hash,
                decision.global_gate_result.global_state_revision,
                decision.global_gate_result.model_dump_json(),
            ),
        )

    def _outbox_by_idempotency_key(self, conn: sqlite3.Connection, idempotency_key: str) -> WcaExecutionOutboxRecord | None:
        row = conn.execute("SELECT * FROM wca_execution_outbox WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        return _outbox_record_from_row(row) if row is not None else None

    def _insert_order_records(
        self,
        conn: sqlite3.Connection,
        proposed: ProposedOrder,
        common: dict[str, str],
        *,
        ignore_duplicates: bool = False,
    ) -> sqlite3.Cursor:
        verb = "INSERT OR IGNORE" if ignore_duplicates else "INSERT OR REPLACE"
        values = (
            proposed.order_intent_id,
            proposed.idempotency_key,
            proposed.account_id,
            common["algorithm_id"],
            common["symbol"],
            common["timestamp"],
            common["configuration_version"],
            common["engine_version"],
            common["market_snapshot_id"],
            common["decision_id"],
            common["run_id"],
            _value(proposed.side),
            proposed.quantity,
            proposed.model_dump_json(),
            proposed.runtime_control_revision,
            proposed.runtime_control_hash,
            proposed.rollout_stage,
            proposed.rollout_evidence_revision,
            proposed.rollout_evidence_hash,
        )
        cursor = conn.execute(
            f"""
            {verb} INTO wca_proposed_orders (
                order_intent_id, idempotency_key, account_id, algorithm_id, symbol,
                timestamp, configuration_version, engine_version, market_snapshot_id,
                decision_id, run_id, side, quantity, payload_json,
                runtime_control_revision, runtime_control_hash, rollout_stage,
                rollout_evidence_revision, rollout_evidence_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        conn.execute(
            f"""
            {verb} INTO wca_order_intents (
                order_intent_id, idempotency_key, account_id, algorithm_id, symbol,
                timestamp, configuration_version, engine_version, market_snapshot_id,
                decision_id, run_id, side, quantity, payload_json,
                runtime_control_revision, runtime_control_hash, rollout_stage,
                rollout_evidence_revision, rollout_evidence_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        conn.execute(
            f"""
            {verb} INTO wca_attributed_orders (
                order_intent_id, idempotency_key, account_id, algorithm_id, symbol,
                timestamp, configuration_version, engine_version, market_snapshot_id,
                decision_id, run_id, side, quantity, status, payload_json,
                runtime_control_revision, runtime_control_hash, rollout_stage,
                rollout_evidence_revision, rollout_evidence_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*values[:-6], _value(proposed.status), values[-6], values[-5], values[-4], values[-3], values[-2], values[-1]),
        )
        return cursor

    def _record_inventory_event_in_conn(self, conn: sqlite3.Connection, event: WcaInventoryLedgerEvent) -> bool:
        _validate_inventory_event(event)
        existing = conn.execute(
            "SELECT 1 FROM wca_inventory_ledger WHERE inventory_event_id = ?",
            (event.inventory_event_id,),
        ).fetchone()
        if existing is not None:
            return False
        conn.execute(
            """
            INSERT INTO wca_inventory_ledger (
                inventory_event_id, event_type, algorithm_id, broker_account_id, symbol,
                order_intent_id, client_order_id, broker_order_id, fill_id, side,
                quantity, filled_quantity, remaining_quantity, average_entry_price,
                fill_price, realized_pnl, unrealized_pnl, reserved_risk, trade_date,
                event_timestamp, source_authority, reconciliation_watermark,
                configuration_version, decision_id, run_id, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.inventory_event_id,
                event.event_type,
                event.algorithm_id,
                event.broker_account_id,
                event.symbol,
                event.order_intent_id,
                event.client_order_id,
                event.broker_order_id,
                event.fill_id,
                event.side,
                int(event.quantity),
                int(event.filled_quantity),
                int(event.remaining_quantity),
                float(event.average_entry_price),
                float(event.fill_price),
                float(event.realized_pnl),
                float(event.unrealized_pnl),
                float(event.reserved_risk),
                event.trade_date,
                _dt(event.event_timestamp),
                event.source_authority,
                event.reconciliation_watermark,
                event.configuration_version,
                event.decision_id,
                event.run_id,
                _json(event.payload or {}),
            ),
        )
        self._rebuild_inventory_projections_in_conn(
            conn,
            algorithm_id=event.algorithm_id,
            broker_account_id=event.broker_account_id,
            symbol=event.symbol,
        )
        return True

    def _rebuild_inventory_projections_in_conn(self, conn: sqlite3.Connection, *, algorithm_id: str, broker_account_id: str, symbol: str) -> None:
        rows = conn.execute(
            """
            SELECT *
            FROM wca_inventory_ledger
            WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?
            ORDER BY event_timestamp, inventory_event_id
            """,
            (algorithm_id, broker_account_id, symbol),
        ).fetchall()
        conn.execute(
            "DELETE FROM wca_inventory_projection WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?",
            (algorithm_id, broker_account_id, symbol),
        )
        conn.execute(
            "DELETE FROM wca_daily_state WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?",
            (algorithm_id, broker_account_id, symbol),
        )
        if not rows:
            return

        open_quantity = 0
        average_entry_price = 0.0
        realized_pnl = 0.0
        unrealized_pnl = 0.0
        reserved_risk = 0.0
        last_event_timestamp: str | None = None
        reconciliation_watermark: str | None = None
        configuration_version = ""
        decision_id = ""
        run_id = ""
        daily: dict[str, dict[str, Any]] = {}

        def daily_state(session_date: str) -> dict[str, Any]:
            if session_date not in daily:
                daily[session_date] = {
                    "trades_completed_today": 0,
                    "entries_attempted_today": 0,
                    "realized_pnl_today": 0.0,
                    "daily_loss": 0.0,
                    "consecutive_losses": 0,
                    "current_reserved_risk": 0.0,
                    "maximum_intraday_exposure": 0.0,
                    "cooldown_until": None,
                    "last_entry_timestamp": None,
                    "last_exit_timestamp": None,
                    "circuit_breaker_state": "closed",
                    "last_successful_reconciliation": None,
                }
            return daily[session_date]

        def increase(quantity: int, price: float) -> None:
            nonlocal open_quantity, average_entry_price
            if quantity <= 0:
                return
            total_cost = (open_quantity * average_entry_price) + (quantity * price)
            open_quantity += quantity
            average_entry_price = round(total_cost / open_quantity, 10) if open_quantity else 0.0

        def reduce(quantity: int, event: WcaInventoryLedgerEvent) -> float:
            nonlocal open_quantity, average_entry_price
            if quantity <= 0:
                return 0.0
            if quantity > open_quantity:
                duplicate_eos_flatten = (
                    str((event.payload or {}).get("exit_reason") or (event.payload or {}).get("reason") or "").lower() == "end_of_session_flatten"
                    or str((event.payload or {}).get("flatten") or "").lower() == "end_of_session"
                )
                duplicate_eos_fill_exit = event.event_type in {"FILL_RECEIVED", "PARTIAL_FILL_RECEIVED"} and str(
                    (event.payload or {}).get("position_effect") or ""
                ).lower() == "exit"
                if open_quantity == 0 and (event.event_type in {"POSITION_CLOSED", "END_OF_SESSION_FLATTEN"} or duplicate_eos_fill_exit) and duplicate_eos_flatten:
                    return 0.0
                raise ValueError("WCA inventory event reduces more quantity than WCA owns")
            computed_pnl = 0.0
            if event.fill_price > 0:
                computed_pnl = round((event.fill_price - average_entry_price) * quantity, 10)
            open_quantity -= quantity
            if open_quantity == 0:
                average_entry_price = 0.0
            return event.realized_pnl if event.realized_pnl != 0 else computed_pnl

        for row in rows:
            event = _inventory_event_from_row(row)
            payload = event.payload or {}
            state = daily_state(event.trade_date)
            quantity = int(event.filled_quantity or event.quantity)
            price = float(event.fill_price or event.average_entry_price)
            event_realized = float(event.realized_pnl)

            if event.event_type == "DAILY_STATE_RESET":
                daily.pop(event.trade_date, None)
                daily[event.trade_date] = daily_state(event.trade_date)
                state = daily[event.trade_date]
            elif event.event_type == "ORDER_INTENT_RESERVED":
                state["entries_attempted_today"] += 1
            elif event.event_type == "RISK_RESERVED":
                reserved_risk = round(reserved_risk + float(event.reserved_risk), 10)
            elif event.event_type in {"RISK_RELEASED", "ORDER_REJECTED", "ORDER_CANCELLED"} and event.reserved_risk:
                if float(event.reserved_risk) > reserved_risk:
                    raise ValueError("WCA inventory event releases more risk than is reserved")
                reserved_risk = round(reserved_risk - float(event.reserved_risk), 10)
            elif event.event_type in {"FILL_RECEIVED", "PARTIAL_FILL_RECEIVED"}:
                position_effect = str(payload.get("position_effect") or "").lower()
                if position_effect == "entry":
                    increase(quantity, price)
                    state["last_entry_timestamp"] = _dt(event.event_timestamp)
                elif position_effect == "exit":
                    event_realized = reduce(quantity, event)
                    state["last_exit_timestamp"] = _dt(event.event_timestamp)
                    state["trades_completed_today"] += 1 if open_quantity == 0 else 0
                elif _value(event.side).upper() == "BUY":
                    increase(quantity, price)
                    state["last_entry_timestamp"] = _dt(event.event_timestamp)
                elif _value(event.side).upper() == "SELL":
                    event_realized = reduce(quantity, event)
                    state["last_exit_timestamp"] = _dt(event.event_timestamp)
                    state["trades_completed_today"] += 1 if open_quantity == 0 else 0
            elif event.event_type in {"POSITION_OPENED", "POSITION_INCREASED"}:
                increase(quantity, float(event.average_entry_price or event.fill_price))
                state["last_entry_timestamp"] = _dt(event.event_timestamp)
            elif event.event_type == "POSITION_REDUCED":
                event_realized = reduce(quantity, event)
                state["last_exit_timestamp"] = _dt(event.event_timestamp)
            elif event.event_type in {"POSITION_CLOSED", "END_OF_SESSION_FLATTEN"}:
                close_quantity = quantity or open_quantity
                event_realized = reduce(close_quantity, event)
                state["last_exit_timestamp"] = _dt(event.event_timestamp)
                state["trades_completed_today"] += 1
            elif event.event_type == "RECONCILIATION_CORRECTION":
                if "open_quantity" in payload:
                    corrected_quantity = int(payload["open_quantity"])
                    if corrected_quantity < 0:
                        raise ValueError("WCA reconciliation correction cannot set negative quantity")
                    open_quantity = corrected_quantity
                    average_entry_price = float(payload.get("average_entry_price") or event.average_entry_price or average_entry_price)
                state["last_successful_reconciliation"] = _dt(event.event_timestamp)

            if event_realized:
                realized_pnl = round(realized_pnl + event_realized, 10)
                state["realized_pnl_today"] = round(float(state["realized_pnl_today"]) + event_realized, 10)
                state["consecutive_losses"] = int(state["consecutive_losses"]) + 1 if event_realized < 0 else 0
            unrealized_pnl = float(event.unrealized_pnl)
            exposure = abs(open_quantity * average_entry_price)
            state["daily_loss"] = max(0.0, -float(state["realized_pnl_today"]))
            state["current_reserved_risk"] = reserved_risk
            state["maximum_intraday_exposure"] = max(float(state["maximum_intraday_exposure"]), exposure)
            state["cooldown_until"] = payload.get("cooldown_until", state["cooldown_until"])
            state["circuit_breaker_state"] = str(payload.get("circuit_breaker_state") or state["circuit_breaker_state"])
            last_event_timestamp = _dt(event.event_timestamp)
            reconciliation_watermark = event.reconciliation_watermark or reconciliation_watermark
            configuration_version = event.configuration_version
            decision_id = event.decision_id
            run_id = event.run_id

        conn.execute(
            """
            INSERT INTO wca_inventory_projection (
                algorithm_id, broker_account_id, symbol, open_quantity, average_entry_price,
                realized_pnl, unrealized_pnl, reserved_risk, last_event_timestamp,
                reconciliation_watermark, configuration_version, decision_id, run_id, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                algorithm_id,
                broker_account_id,
                symbol,
                open_quantity,
                average_entry_price,
                realized_pnl,
                unrealized_pnl,
                reserved_risk,
                last_event_timestamp,
                reconciliation_watermark,
                configuration_version,
                decision_id,
                run_id,
                _json({"rebuilt_from_event_count": len(rows)}),
            ),
        )
        for session_date, state in daily.items():
            conn.execute(
                """
                INSERT INTO wca_daily_state (
                    algorithm_id, broker_account_id, symbol, session_date,
                    trades_completed_today, entries_attempted_today, realized_pnl_today,
                    daily_loss, consecutive_losses, current_reserved_risk,
                    maximum_intraday_exposure, cooldown_until, last_entry_timestamp,
                    last_exit_timestamp, circuit_breaker_state, last_successful_reconciliation,
                    isolation_enforced, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    algorithm_id,
                    broker_account_id,
                    symbol,
                    session_date,
                    int(state["trades_completed_today"]),
                    int(state["entries_attempted_today"]),
                    float(state["realized_pnl_today"]),
                    float(state["daily_loss"]),
                    int(state["consecutive_losses"]),
                    float(state["current_reserved_risk"]),
                    float(state["maximum_intraday_exposure"]),
                    state["cooldown_until"],
                    state["last_entry_timestamp"],
                    state["last_exit_timestamp"],
                    state["circuit_breaker_state"],
                    state["last_successful_reconciliation"],
                    1,
                    _json({"rebuilt_from_ledger": True}),
                ),
            )

    def _insert_rollout_status(self, conn: sqlite3.Connection, payload: dict[str, Any], common: dict[str, str]) -> None:
        rollout_version = str(payload.get("rollout_version") or payload.get("rolloutVersion") or "wca_rollout_unversioned")
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_rollout_status (
                status_id, algorithm_id, symbol, timestamp, configuration_version,
                engine_version, market_snapshot_id, decision_id, run_id, rollout_version,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{common['run_id']}-rollout",
                common["algorithm_id"],
                common["symbol"],
                common["timestamp"],
                common["configuration_version"],
                common["engine_version"],
                common["market_snapshot_id"],
                common["decision_id"],
                common["run_id"],
                rollout_version,
                _json(payload),
            ),
        )

    def _insert_market_status(self, conn: sqlite3.Connection, status: WcaMarketStatus, common: dict[str, str]) -> None:  # type: ignore[no-redef]
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_market_status_snapshots (
                algorithm_id, account_id, symbol, timestamp, configuration_version, engine_version,
                market_snapshot_id, decision_id, run_id, trend, volatility, liquidity,
                session, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (common["algorithm_id"], common["account_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], _value(status.trend), _value(status.volatility), _value(status.liquidity), _value(status.session), status.model_dump_json()),
        )

    def _insert_effective_settings(self, conn: sqlite3.Connection, settings: WcaEffectiveSettings, common: dict[str, str]) -> None:  # type: ignore[no-redef]
        conn.execute(
            """
            INSERT OR REPLACE INTO wca_effective_setting_snapshots (
                algorithm_id, account_id, symbol, timestamp, configuration_version, engine_version,
                market_snapshot_id, decision_id, run_id, settings_version, profile_id,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (common["algorithm_id"], common["account_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], settings.settings_version, settings.profile_id, settings.model_dump_json()),
        )


def _common_row(*, symbol: str, timestamp: str, configuration_version: str, engine_version: str, market_snapshot_id: str, decision_id: str, run_id: str, account_id: str = "paper") -> dict[str, str]:
    return {
        "algorithm_id": WCA_ALGORITHM_ID,
        "account_id": account_id,
        "symbol": symbol,
        "timestamp": timestamp,
        "configuration_version": configuration_version,
        "engine_version": engine_version,
        "market_snapshot_id": market_snapshot_id,
        "decision_id": decision_id,
        "run_id": run_id,
    }


def _runtime_control_key(broker_account_id: str, symbol: str) -> str:
    return f"wca:runtime-control:{broker_account_id}:{symbol.upper()}"


def _decision_common(decision: WcaDecision, run_id: str, *, engine_version: str | None = None) -> dict[str, str]:
    return _common_row(
        symbol=decision.market_snapshot.symbol,
        timestamp=_dt(decision.decision_timestamp),
        configuration_version=decision.configuration_version,
        engine_version=engine_version or next((code for code in decision.reason_codes if code.startswith("wca_")), "wca_engine"),
        market_snapshot_id=f"{decision.decision_id}-market",
        decision_id=decision.decision_id,
        run_id=run_id,
        account_id=getattr(decision.market_snapshot, "account_id", "paper"),
    )


def _outbox_record_from_row(row: sqlite3.Row) -> WcaExecutionOutboxRecord:
    payload = json.loads(row["payload_json"] or "{}")
    request_payload = json.loads(row["request_payload_json"] or "{}")
    response_payload = json.loads(row["response_payload_json"] or "{}")
    decision_payload = payload.get("decision") or {}
    proposed_payload = payload.get("proposed_order") or {}
    if not decision_payload:
        proposed_payload = proposed_payload or request_payload.get("proposed_order") or {}
        decision_payload = request_payload.get("decision") or {}
    if not proposed_payload and "order_intent_id" in payload:
        proposed_payload = payload
    if not decision_payload:
        raise RuntimeError("WCA execution outbox row is missing the reserved decision payload")
    decision = WcaDecision.model_validate(decision_payload)
    proposed = ProposedOrder.model_validate(proposed_payload or decision.proposed_order)
    return WcaExecutionOutboxRecord(
        outbox_id=row["outbox_id"],
        account_id=row["account_id"],
        symbol=row["symbol"],
        decision_id=row["decision_id"],
        run_id=row["run_id"],
        order_intent_id=row["order_intent_id"],
        idempotency_key=row["idempotency_key"],
        client_order_id=row["client_order_id"],
        status=row["status"],
        version=int(row["version"]),
        runtime_control_revision=int(row["runtime_control_revision"]) if row["runtime_control_revision"] is not None else decision.runtime_control_revision or proposed.runtime_control_revision,
        runtime_control_hash=str(row["runtime_control_hash"] or decision.runtime_control_hash or proposed.runtime_control_hash),
        global_risk_decision_id=str(row["global_risk_decision_id"] or payload.get("global_risk_decision_id") or ""),
        global_risk_state_hash=str(row["global_risk_state_hash"] or payload.get("global_risk_state_hash") or ""),
        global_risk_state_revision=str(row["global_risk_state_revision"] or payload.get("global_risk_state_revision") or ""),
        global_risk_approval_expires_at=_parse_datetime_optional(row["global_risk_approval_expires_at"] or payload.get("global_risk_approval_expires_at")),
        authoritative_state_hash=str(row["authoritative_state_hash"] or payload.get("authoritative_state_hash") or decision.authoritative_state_hash),
        decision=decision,
        proposed_order=proposed,
        request_payload=request_payload or payload.get("request") or {},
        response_payload=response_payload or None,
    )


def _global_risk_approval_from_row(row: sqlite3.Row) -> WcaGlobalRiskApprovalRecord:
    payload = json.loads(row["payload_json"] or "{}")
    reason_codes = tuple(json.loads(row["reason_codes_json"] or "[]") or payload.get("reason_codes") or payload.get("reasonCodes") or ())
    return WcaGlobalRiskApprovalRecord(
        decision_id=row["decision_id"],
        account_id=row["account_id"],
        symbol=row["symbol"],
        status=row["status"],
        global_risk_decision_id=str(row["global_risk_decision_id"] or payload.get("global_risk_decision_id") or payload.get("globalRiskDecisionId") or ""),
        evaluated_at=_parse_datetime_optional(row["evaluated_at"] or payload.get("evaluated_at") or payload.get("evaluatedAt")),
        expires_at=_parse_datetime_optional(row["expires_at"] or payload.get("expires_at") or payload.get("expiresAt")),
        entry_permitted=bool(int(row["entry_permitted"] or 0)),
        risk_reducing_exit_permitted=bool(int(row["risk_reducing_exit_permitted"] or 0)),
        requested_quantity=int(row["proposed_quantity"]),
        allowed_quantity=int(row["allowed_quantity"]),
        approved_risk_dollars=float(row["approved_risk_dollars"] or payload.get("approved_risk") or payload.get("approvedRisk") or 0),
        reason_codes=reason_codes,
        global_state_hash=str(row["global_state_hash"] or payload.get("global_state_hash") or payload.get("globalStateHash") or ""),
        global_state_revision=str(row["global_state_revision"] or payload.get("global_state_revision") or payload.get("globalStateRevision") or ""),
        payload=payload,
    )


def _unique_gate_results(decision: WcaDecision):
    seen: set[str] = set()
    for gate in (*decision.local_gates, *decision.hard_filter_results):
        if gate.gate_id in seen:
            continue
        seen.add(gate.gate_id)
        yield gate


def _json(payload: Any) -> str:
    if hasattr(payload, "model_dump_json"):
        return payload.model_dump_json()
    if hasattr(payload, "__dict__"):
        import json

        return json.dumps(payload.__dict__, sort_keys=True, separators=(",", ":"))
    import json

    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _hash_json(payload: Any) -> str:
    import hashlib

    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def _dt(value: datetime | str | None) -> str:
    if value is None:
        return _utc_now()
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime_optional(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _require_wca_identity(algorithm_id: str) -> None:
    if algorithm_id != WCA_ALGORITHM_ID:
        raise ValueError("WCA repository requires algorithm_id='wca'")


def _require_broker_account_identity(broker_account_id: str) -> None:
    if not broker_account_id:
        raise ValueError("WCA repository requires a broker account identity")


def _inventory_event_from_row(row: sqlite3.Row) -> WcaInventoryLedgerEvent:
    return WcaInventoryLedgerEvent(
        inventory_event_id=row["inventory_event_id"],
        event_type=row["event_type"],
        algorithm_id=row["algorithm_id"],
        broker_account_id=row["broker_account_id"],
        symbol=row["symbol"],
        order_intent_id=row["order_intent_id"],
        client_order_id=row["client_order_id"],
        broker_order_id=row["broker_order_id"],
        fill_id=row["fill_id"],
        side=row["side"],
        quantity=int(row["quantity"]),
        filled_quantity=int(row["filled_quantity"]),
        remaining_quantity=int(row["remaining_quantity"]),
        average_entry_price=float(row["average_entry_price"]),
        fill_price=float(row["fill_price"]),
        realized_pnl=float(row["realized_pnl"]),
        unrealized_pnl=float(row["unrealized_pnl"]),
        reserved_risk=float(row["reserved_risk"]),
        trade_date=row["trade_date"],
        event_timestamp=row["event_timestamp"],
        source_authority=row["source_authority"],
        reconciliation_watermark=row["reconciliation_watermark"],
        configuration_version=row["configuration_version"],
        decision_id=row["decision_id"],
        run_id=row["run_id"],
        payload=json.loads(row["payload_json"] or "{}"),
    )


def _optional_value(value: Any) -> str | None:
    if value is None:
        return None
    return _value(value)


def _signed_quantity(side: Any, quantity: int) -> int:
    return quantity if _value(side) == "BUY" else -quantity


def _realized_pnl(side: Any, entry_price: float, exit_price: float, quantity: int) -> float:
    return round((exit_price - entry_price) * quantity if _value(side) == "BUY" else (entry_price - exit_price) * quantity, 10)


def _entry_price_from_payload(record: dict[str, Any], proposed: ProposedOrder) -> float:
    fill = record.get("fill") if isinstance(record.get("fill"), dict) else {}
    for value in (record.get("entry_price"), fill.get("average_fill_price"), fill.get("averageFillPrice"), proposed.limit_price, proposed.trigger_price):
        if value is not None:
            return float(value)
    return 0.01


def _fill_timestamp_from_payload(record: dict[str, Any]) -> str | None:
    fill = record.get("fill") if isinstance(record.get("fill"), dict) else {}
    value = record.get("opened_at") or fill.get("filled_at") or fill.get("filledAt") or fill.get("updatedAt")
    return str(value) if value is not None else None


def _validate_inventory_event(event: WcaInventoryLedgerEvent) -> None:
    if not event.symbol:
        raise ValueError("WCA inventory event requires a symbol")
    if event.event_type not in WCA_INVENTORY_EVENT_TYPES:
        raise ValueError(f"unsupported WCA inventory event type: {event.event_type}")
    for field_name in ("quantity", "filled_quantity", "remaining_quantity"):
        if int(getattr(event, field_name)) < 0:
            raise ValueError(f"WCA inventory event {field_name} cannot be negative")
    for field_name in ("average_entry_price", "fill_price", "reserved_risk"):
        if float(getattr(event, field_name)) < 0:
            raise ValueError(f"WCA inventory event {field_name} cannot be negative")
    if event.quantity and event.filled_quantity > event.quantity:
        raise ValueError("WCA inventory event filled quantity cannot exceed quantity")
    if event.quantity and event.remaining_quantity > event.quantity:
        raise ValueError("WCA inventory event remaining quantity cannot exceed quantity")


def _reserved_risk_from_decision(decision: WcaDecision) -> float:
    if decision.global_gate_result is not None and decision.global_gate_result.approved_risk > 0:
        return float(decision.global_gate_result.approved_risk)
    if decision.sizing.stop_risk_dollars > 0:
        return float(decision.sizing.stop_risk_dollars)
    if decision.sizing.risk_dollars > 0:
        return float(decision.sizing.risk_dollars)
    return 0.0


def _reserved_risk_release_for_fill(repository: WcaSqliteRepository, decision: WcaDecision, *, account_id: str, quantity: int) -> float:
    order = decision.proposed_order
    total_risk = _reserved_risk_from_decision(decision)
    if order is None or order.quantity <= 0 or quantity <= 0 or total_risk <= 0:
        return 0.0
    intended_release = total_risk * min(int(quantity), int(order.quantity)) / int(order.quantity)
    try:
        projection = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=account_id, symbol=order.symbol)
        currently_reserved = max(0.0, float(projection.reserved_risk))
    except Exception:
        currently_reserved = intended_release
    return round(max(0.0, min(intended_release, currently_reserved)), 10)


def _optional_positive_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _configuration_from_payload(payload: dict[str, Any]) -> WcaConfiguration:
    if "aggregation" in payload and "primary_strategy_settings" in payload:
        return WcaConfiguration.model_validate(payload)
    if "configuration_version" in payload and "configurationVersion" not in payload:
        return WcaConfiguration.model_validate(payload)
    decision = payload.get("decisionSettings") or payload.get("decision_settings") or {}
    trading = payload.get("tradingSettings") or payload.get("trading_settings") or {}
    version = str(payload.get("configurationVersion") or payload.get("configuration_version") or default_wca_configuration().configuration_version)
    return canonical_configuration_from_legacy(
        decision,
        trading,
        configuration_version=version,
        creator=str(payload.get("creator") or "legacy_api"),
        source="legacy_configuration_payload",
    )


def _configuration_from_json(payload_json: str) -> WcaConfiguration:
    try:
        return WcaConfiguration.model_validate_json(payload_json)
    except ValidationError:
        payload = json.loads(payload_json)
        payload["content_hash"] = ""
        return WcaConfiguration.model_validate(payload)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _install_wca_table_integrity_triggers(conn: sqlite3.Connection) -> None:
    for table in WCA_PERSISTENCE_TABLES:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "algorithm_id" in columns:
            trigger_prefix = table.replace("-", "_")
            conn.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS trg_{trigger_prefix}_algorithm_insert
                BEFORE INSERT ON {table}
                WHEN NEW.algorithm_id <> 'wca'
                BEGIN
                    SELECT RAISE(ABORT, 'non-WCA algorithm_id rejected');
                END
                """
            )
            conn.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS trg_{trigger_prefix}_algorithm_update
                BEFORE UPDATE ON {table}
                WHEN NEW.algorithm_id <> 'wca'
                BEGIN
                    SELECT RAISE(ABORT, 'non-WCA algorithm_id rejected');
                END
                """
            )
        for column in ("quantity", "filled_quantity", "remaining_quantity", "reserved_risk", "open_quantity", "daily_loss", "current_reserved_risk", "maximum_intraday_exposure", "trades_completed_today", "entries_attempted_today", "consecutive_losses"):
            if column not in columns:
                continue
            trigger_prefix = f"{table}_{column}".replace("-", "_")
            conn.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS trg_{trigger_prefix}_nonnegative_insert
                BEFORE INSERT ON {table}
                WHEN NEW.{column} < 0
                BEGIN
                    SELECT RAISE(ABORT, 'negative WCA quantity rejected');
                END
                """
            )
            conn.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS trg_{trigger_prefix}_nonnegative_update
                BEFORE UPDATE ON {table}
                WHEN NEW.{column} < 0
                BEGIN
                    SELECT RAISE(ABORT, 'negative WCA quantity rejected');
                END
                """
            )


__all__ = [
    "WCA_IGNORED_LOCAL_STORAGE_KEYS",
    "WCA_INVENTORY_EVENT_TYPES",
    "WCA_PERSISTENCE_MIGRATION_VERSION",
    "WCA_PERSISTENCE_RECORD_IDS",
    "WCA_PERSISTENCE_RECORD_INVENTORY",
    "WCA_PERSISTENCE_TABLES",
    "WcaDailyStateProjection",
    "WcaInventoryLedgerEvent",
    "WcaInventoryProjection",
    "WcaOrderIntentReservation",
    "WcaPersistenceRecordDefinition",
    "WcaPersistenceSummary",
    "WcaRepository",
    "WcaSqliteRepository",
    "apply_wca_persistence_migrations",
    "classify_wca_local_storage_key",
    "migrate_wca_sqlite_database",
]
