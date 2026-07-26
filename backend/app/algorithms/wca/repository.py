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
    WcaPaperStabilityValidationResult,
    WcaShadowComparisonEvidence,
    WcaStrategyPerformanceRecord,
    WcaWeightSnapshot,
)
from backend.app.algorithms.wca.configuration import (
    WcaConfiguration,
    WcaConfigurationLifecycle,
    canonical_configuration_from_legacy,
    default_wca_configuration,
    validate_wca_configuration,
)
from backend.app.algorithms.wca.position_management import WcaManagedPosition
from backend.app.algorithms.wca.strategy_registry import WCA_STRATEGY_REGISTRY
from backend.app.config import get_settings
from backend.app.database import _sqlite_path

WCA_PERSISTENCE_MIGRATION_VERSION = "wca_authoritative_persistence_002"
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
    WcaPersistenceRecordDefinition("exit_state", "wca_exit_state", "WCA exit-state records."),
    WcaPersistenceRecordDefinition("reconciliation_results", "wca_broker_reconciliations", "WCA reconciliation result records."),
    WcaPersistenceRecordDefinition("runtime_health", "wca_runtime_health", "WCA runtime health records."),
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

    def save_configuration(self, payload: dict[str, Any], *, symbol: str, timestamp: str | None = None, engine_version: str) -> None:
        ...

    def save_candidate_configuration(self, configuration: WcaConfiguration, *, symbol: str = "SPY", engine_version: str) -> WcaConfiguration:
        ...

    def validate_configuration_revision(self, configuration: WcaConfiguration | dict[str, Any]) -> WcaConfiguration:
        ...

    def activate_configuration_version(self, configuration_version: str) -> WcaConfiguration:
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

    def create_execution_outbox_record(self, decision: WcaDecision, *, account_id: str, idempotency_key: str, payload: dict[str, Any] | None = None) -> bool:
        ...

    def reserve_decision_order_and_outbox(self, decision: WcaDecision, *, run_id: str, account_id: str, idempotency_key: str, client_order_id: str, request_payload: dict[str, Any]) -> WcaOutboxReservation:
        ...

    def claim_next_execution_outbox(self, *, owner_id: str) -> WcaExecutionOutboxRecord | None:
        ...

    def update_execution_outbox_state(self, *, outbox_id: str, status: WcaOrderStatus | str, response_payload: dict[str, Any] | None = None, error_payload: dict[str, Any] | None = None) -> bool:
        ...

    def record_broker_order(self, decision: WcaDecision, *, broker_order_id: str, account_id: str, idempotency_key: str, status: str, payload: dict[str, Any] | None = None) -> bool:
        ...

    def apply_fill_and_update_position(self, decision: WcaDecision, *, fill_id: str, account_id: str, quantity: int, broker_order_id: str | None = None, payload: dict[str, Any] | None = None) -> bool:
        ...

    def authorize_wca_lot_reduction(self, *, lot_id: str, account_id: str, symbol: str, quantity: int) -> WcaInventoryOwnershipDecision:
        ...

    def list_open_wca_lots(self, *, account_id: str, symbol: str) -> tuple[dict[str, Any], ...]:
        ...

    def write_position_management_snapshot(self, position: WcaManagedPosition, *, evaluated_at: datetime) -> None:
        ...

    def close_wca_attributed_position_quantity(self, *, account_id: str, symbol: str, quantity: int, exit_price: float, exit_reason: str, evaluated_at: datetime) -> bool:
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
    _ensure_column(conn, "wca_broker_orders", "client_order_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "wca_broker_orders", "request_payload_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "wca_broker_orders", "response_payload_json", "TEXT NOT NULL DEFAULT '{}'")
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
                INSERT OR REPLACE INTO wca_weight_snapshots (
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
            if cutoff is None or (
                snapshot.created_at.astimezone(timezone.utc) <= cutoff
                and (snapshot.metrics_cutoff_timestamp is None or snapshot.metrics_cutoff_timestamp.astimezone(timezone.utc) <= cutoff)
            ):
                return snapshot
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

    def create_execution_outbox_record(self, decision: WcaDecision, *, account_id: str, idempotency_key: str, payload: dict[str, Any] | None = None) -> bool:
        if decision.proposed_order is None:
            raise ValueError("cannot create WCA execution outbox without an order intent")
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
                    idempotency_key, status, client_order_id, request_payload_json, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (outbox_id, common["algorithm_id"], account_id, common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], proposed.order_intent_id, idempotency_key, WcaOrderStatus.OUTBOX_RESERVED.value, str(record.get("client_order_id", "")) if isinstance(record, dict) else "", _json(record), _json(record)),
            )
        return cursor.rowcount == 1

    def reserve_decision_order_and_outbox(self, decision: WcaDecision, *, run_id: str, account_id: str, idempotency_key: str, client_order_id: str, request_payload: dict[str, Any]) -> WcaOutboxReservation:
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
        common = _decision_common(decision_to_persist, run_id)
        outbox_id = f"wca-outbox-{proposed.order_intent_id}"
        payload = {
            "decision": decision_to_persist.model_dump(mode="json"),
            "proposed_order": proposed.model_dump(mode="json"),
            "request": request_payload,
            "client_order_id": client_order_id,
            "idempotency_key": idempotency_key,
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
                proposed = existing_order.model_copy(update={"status": WcaOrderStatus.OUTBOX_RESERVED, "account_id": account_id, "idempotency_key": idempotency_key})
                decision_to_persist = decision_to_persist.model_copy(update={"proposed_order": proposed})
                outbox_id = f"wca-outbox-{proposed.order_intent_id}"
                payload = {
                    "decision": decision_to_persist.model_dump(mode="json"),
                    "proposed_order": proposed.model_dump(mode="json"),
                    "request": request_payload,
                    "client_order_id": client_order_id,
                    "idempotency_key": idempotency_key,
                }
            self._insert_decision(conn, decision_to_persist, common)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO wca_execution_outbox (
                    outbox_id, algorithm_id, account_id, symbol, timestamp, configuration_version,
                    engine_version, market_snapshot_id, decision_id, run_id, order_intent_id,
                    idempotency_key, status, client_order_id, request_payload_json, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            if cursor.rowcount != 1:
                existing = self._outbox_by_idempotency_key(conn, idempotency_key)
                if existing is not None:
                    return WcaOutboxReservation(False, existing.outbox_id, existing.proposed_order, existing.idempotency_key, existing.client_order_id)
                raise RuntimeError("failed to reserve WCA execution outbox")
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
                (WcaOrderStatus.SUBMITTING.value, owner_id, now, now, row["outbox_id"], WcaOrderStatus.OUTBOX_RESERVED.value),
            )
            if cursor.rowcount != 1:
                return None
            conn.execute(
                "UPDATE wca_attributed_orders SET status = ? WHERE order_intent_id = ? AND algorithm_id = ?",
                (WcaOrderStatus.SUBMITTING.value, row["order_intent_id"], WCA_ALGORITHM_ID),
            )
            claimed = conn.execute("SELECT * FROM wca_execution_outbox WHERE outbox_id = ?", (row["outbox_id"],)).fetchone()
            return _outbox_record_from_row(claimed)

    def update_execution_outbox_state(self, *, outbox_id: str, status: WcaOrderStatus | str, response_payload: dict[str, Any] | None = None, error_payload: dict[str, Any] | None = None) -> bool:
        now = _utc_now()
        status_value = _value(status)
        response = response_payload or {}
        error = error_payload or {}
        with self.connect() as conn:
            row = conn.execute("SELECT order_intent_id FROM wca_execution_outbox WHERE outbox_id = ? AND algorithm_id = ?", (outbox_id, WCA_ALGORITHM_ID)).fetchone()
            if row is None:
                return False
            cursor = conn.execute(
                """
                UPDATE wca_execution_outbox
                SET status = ?, version = version + 1, response_payload_json = ?,
                    error_payload_json = ?,
                    submitted_at = CASE WHEN ? IN (?, ?, ?, ?, ?, ?) THEN COALESCE(submitted_at, ?) ELSE submitted_at END,
                    acknowledged_at = CASE WHEN ? IN (?, ?, ?, ?, ?) THEN COALESCE(acknowledged_at, ?) ELSE acknowledged_at END,
                    updated_at = ?
                WHERE outbox_id = ? AND algorithm_id = ?
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
                (broker_order_id, common["algorithm_id"], account_id, common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["decision_id"], common["run_id"], proposed.order_intent_id, idempotency_key, _value(proposed.side), proposed.quantity, status, client_order_id, _json(request_payload), _json(response_payload), _json(record)),
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
        return True

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
                    "entry_price": float(payload.get("entry_price") or fill.get("average_fill_price") or fill.get("averageFillPrice") or 0.01),
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

    def close_wca_attributed_position_quantity(self, *, account_id: str, symbol: str, quantity: int, exit_price: float, exit_reason: str, evaluated_at: datetime) -> bool:
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
            remaining = quantity
            for row in rows:
                if remaining <= 0:
                    break
                open_qty = int(row["quantity"])
                close_qty = min(open_qty, remaining)
                payload = json.loads(row["payload_json"] or "{}")
                entry_price = float(payload.get("entry_price") or 0.01)
                side = _value(row["side"])
                pnl = _realized_pnl(side, entry_price, exit_price, close_qty)
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
                common = _common_row(
                    symbol=symbol,
                    timestamp=_dt(evaluated_at),
                    configuration_version=row["configuration_version"],
                    engine_version=row["engine_version"],
                    market_snapshot_id=row["market_snapshot_id"],
                    decision_id=row["decision_id"],
                    run_id=row["run_id"],
                    account_id=account_id,
                )
                close_side = "SELL" if side == "BUY" else "BUY"
                trade_payload = {**new_payload, "entry_price": entry_price, "exit_price": exit_price, "exit_reason": exit_reason, "lot_id": row["lot_id"]}
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
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (account_id, symbol),
            ).fetchone()
        return bool(row and (int(row["hard_operational_warning"]) or int(row["discrepancy_count"]) > 0))

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
        return WcaOrderIntentReservation(
            created=cursor.rowcount > 0,
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
                engine_version, market_snapshot_id, run_id, side, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (decision.decision_id, common["algorithm_id"], common["account_id"], common["symbol"], common["timestamp"], common["configuration_version"], common["engine_version"], common["market_snapshot_id"], common["run_id"], side, decision.model_dump_json()),
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
                proposed_quantity, allowed_quantity, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        )
        cursor = conn.execute(
            f"""
            {verb} INTO wca_proposed_orders (
                order_intent_id, idempotency_key, account_id, algorithm_id, symbol,
                timestamp, configuration_version, engine_version, market_snapshot_id,
                decision_id, run_id, side, quantity, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        conn.execute(
            f"""
            {verb} INTO wca_order_intents (
                order_intent_id, idempotency_key, account_id, algorithm_id, symbol,
                timestamp, configuration_version, engine_version, market_snapshot_id,
                decision_id, run_id, side, quantity, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        conn.execute(
            f"""
            {verb} INTO wca_attributed_orders (
                order_intent_id, idempotency_key, account_id, algorithm_id, symbol,
                timestamp, configuration_version, engine_version, market_snapshot_id,
                decision_id, run_id, side, quantity, status, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*values[:-1], _value(proposed.status), values[-1]),
        )
        return cursor

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
        decision=decision,
        proposed_order=proposed,
        request_payload=request_payload or payload.get("request") or {},
        response_payload=response_payload or None,
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


def _dt(value: datetime | str | None) -> str:
    if value is None:
        return _utc_now()
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).isoformat()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


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


__all__ = [
    "WCA_IGNORED_LOCAL_STORAGE_KEYS",
    "WCA_PERSISTENCE_MIGRATION_VERSION",
    "WCA_PERSISTENCE_RECORD_IDS",
    "WCA_PERSISTENCE_RECORD_INVENTORY",
    "WCA_PERSISTENCE_TABLES",
    "WcaOrderIntentReservation",
    "WcaPersistenceRecordDefinition",
    "WcaPersistenceSummary",
    "WcaRepository",
    "WcaSqliteRepository",
    "apply_wca_persistence_migrations",
    "classify_wca_local_storage_key",
    "migrate_wca_sqlite_database",
]
