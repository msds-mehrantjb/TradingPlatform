"""Durable Meta-Strategy repository backed by SQLite."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.ownership import (
    META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
    META_STRATEGY_REQUIRED_IDENTITY_FIELDS,
    meta_strategy_ownership_violation,
)
from backend.app.algorithms.meta_strategy.versions import (
    META_STRATEGY_ALGORITHM_VERSION,
    META_STRATEGY_BACKTEST_ENGINE_VERSION,
    META_STRATEGY_CONFIGURATION_VERSION,
    META_STRATEGY_DYNAMIC_PROFILE_VERSION,
    META_STRATEGY_EXIT_POLICY_VERSION,
    META_STRATEGY_FEATURE_SCHEMA_VERSION,
    META_STRATEGY_LABEL_SPECIFICATION_VERSION,
    META_STRATEGY_MODEL_ARTIFACT_VERSION,
    META_STRATEGY_MODEL_VERSION,
    META_STRATEGY_POSITION_SIZING_VERSION,
    META_STRATEGY_STRATEGY_CATALOG_VERSION,
)
from backend.app.database import _sqlite_path


META_STRATEGY_PERSISTENCE_MIGRATION_VERSION = "meta_strategy_repository_002"


@dataclass(frozen=True)
class MetaStrategyPersistenceRecordDefinition:
    record_id: str
    table_name: str
    responsibility: str


META_STRATEGY_PERSISTENCE_RECORD_INVENTORY: tuple[MetaStrategyPersistenceRecordDefinition, ...] = (
    MetaStrategyPersistenceRecordDefinition("configurations", "meta_strategy_configurations", "Meta-Strategy configurations."),
    MetaStrategyPersistenceRecordDefinition("market_snapshots", "meta_strategy_market_snapshots", "Point-in-time market snapshots."),
    MetaStrategyPersistenceRecordDefinition("strategy_outputs", "meta_strategy_strategy_outputs", "Strategy, context, regime, and safety outputs."),
    MetaStrategyPersistenceRecordDefinition("family_scores", "meta_strategy_family_scores", "Family aggregation scores."),
    MetaStrategyPersistenceRecordDefinition("candidates", "meta_strategy_candidates", "Deterministic and final candidates."),
    MetaStrategyPersistenceRecordDefinition("feature_sets", "meta_strategy_feature_sets", "Feature vectors and schema hashes."),
    MetaStrategyPersistenceRecordDefinition("labels", "meta_strategy_labels", "Triple-barrier and execution labels."),
    MetaStrategyPersistenceRecordDefinition("training_runs", "meta_strategy_training_runs", "Training run manifests and reports."),
    MetaStrategyPersistenceRecordDefinition("validation_folds", "meta_strategy_validation_folds", "Chronological validation fold records."),
    MetaStrategyPersistenceRecordDefinition("model_artifacts", "meta_strategy_model_artifacts", "Model artifact manifests."),
    MetaStrategyPersistenceRecordDefinition("calibration_reports", "meta_strategy_calibration_reports", "Calibration and reliability reports."),
    MetaStrategyPersistenceRecordDefinition("predictions", "meta_strategy_predictions", "Runtime model predictions."),
    MetaStrategyPersistenceRecordDefinition("decisions", "meta_strategy_decisions", "Auditable Meta-Strategy decisions."),
    MetaStrategyPersistenceRecordDefinition("effective_profiles", "meta_strategy_effective_profiles", "Resolved dynamic profiles."),
    MetaStrategyPersistenceRecordDefinition("sizing_results", "meta_strategy_sizing_results", "Position sizing results and caps."),
    MetaStrategyPersistenceRecordDefinition("order_intents", "meta_strategy_order_intents", "Validated order intents."),
    MetaStrategyPersistenceRecordDefinition("trades", "meta_strategy_trades", "Trades, fills, positions, and reconciliation records."),
    MetaStrategyPersistenceRecordDefinition("backtests", "meta_strategy_backtests", "Backtest runs and results."),
    MetaStrategyPersistenceRecordDefinition("shadow_comparisons", "meta_strategy_shadow_comparisons", "Shadow-mode comparisons."),
    MetaStrategyPersistenceRecordDefinition("paper_stability", "meta_strategy_paper_stability", "Paper stability evidence."),
    MetaStrategyPersistenceRecordDefinition("promotions", "meta_strategy_promotions", "Promotion evidence and status."),
    MetaStrategyPersistenceRecordDefinition("rollbacks", "meta_strategy_rollbacks", "Rollback records."),
)
META_STRATEGY_PERSISTENCE_RECORD_IDS = frozenset(record.record_id for record in META_STRATEGY_PERSISTENCE_RECORD_INVENTORY)
META_STRATEGY_PERSISTENCE_TABLES = tuple(record.table_name for record in META_STRATEGY_PERSISTENCE_RECORD_INVENTORY)
META_STRATEGY_PERSISTENCE_TABLE_BY_RECORD_ID = {record.record_id: record.table_name for record in META_STRATEGY_PERSISTENCE_RECORD_INVENTORY}
META_STRATEGY_INVENTORY_TABLES = (
    "meta_strategy_inventory_positions",
    "meta_strategy_inventory_position_lots",
    "meta_strategy_inventory_order_intents",
    "meta_strategy_inventory_submitted_orders",
    "meta_strategy_inventory_order_status_history",
    "meta_strategy_inventory_fills",
    "meta_strategy_inventory_trades",
    "meta_strategy_inventory_realised_pnl",
    "meta_strategy_inventory_unrealised_pnl_snapshots",
    "meta_strategy_inventory_reserved_risk",
    "meta_strategy_inventory_allocated_capital",
    "meta_strategy_inventory_daily_statistics",
    "meta_strategy_inventory_strategy_exposure",
    "meta_strategy_inventory_symbol_exposure",
    "meta_strategy_inventory_family_exposure",
    "meta_strategy_inventory_position_lifecycle",
    "meta_strategy_inventory_quarantine",
    "meta_strategy_inventory_reconciliation_checkpoints",
    "meta_strategy_inventory_snapshots",
)
META_STRATEGY_INVENTORY_QUERY_TABLES: dict[str, str] = {
    "positions": "meta_strategy_inventory_positions",
    "position_lots": "meta_strategy_inventory_position_lots",
    "order_intents": "meta_strategy_inventory_order_intents",
    "orders": "meta_strategy_inventory_submitted_orders",
    "order_status_history": "meta_strategy_inventory_order_status_history",
    "fills": "meta_strategy_inventory_fills",
    "trades": "meta_strategy_inventory_trades",
    "realised_pnl": "meta_strategy_inventory_realised_pnl",
    "unrealised_pnl": "meta_strategy_inventory_unrealised_pnl_snapshots",
    "risk_reservations": "meta_strategy_inventory_reserved_risk",
    "allocated_capital": "meta_strategy_inventory_allocated_capital",
    "daily_statistics": "meta_strategy_inventory_daily_statistics",
    "strategy_exposure": "meta_strategy_inventory_strategy_exposure",
    "symbol_exposure": "meta_strategy_inventory_symbol_exposure",
    "family_exposure": "meta_strategy_inventory_family_exposure",
    "position_lifecycle": "meta_strategy_inventory_position_lifecycle",
    "quarantine": "meta_strategy_inventory_quarantine",
    "reconciliation_checkpoints": "meta_strategy_inventory_reconciliation_checkpoints",
    "snapshots": "meta_strategy_inventory_snapshots",
}

_RESERVATION_RELEASING_ORDER_STATUSES = frozenset(
    {"CANCELED", "CANCELLED", "REJECTED", "EXPIRED", "DONE_FOR_DAY", "DEAD_LETTER"}
)

META_STRATEGY_REQUIRED_ATTRIBUTION_COLUMNS = (
    "algorithm_id",
    "capital_partition_id",
    "algorithm_version",
    "configuration_version",
    "settings_version",
    "strategy_catalog_version",
    "timestamp",
    "symbol",
    "decision_id",
    "job_id",
    "event_id",
    "snapshot_id",
)
META_STRATEGY_REPOSITORY_IDENTITY_COLUMNS = META_STRATEGY_REQUIRED_IDENTITY_FIELDS
META_STRATEGY_VERSION_COLUMNS = (
    "algorithm_version",
    "configuration_version",
    "strategy_catalog_version",
    "feature_schema_version",
    "label_specification_version",
    "model_version",
    "model_artifact_version",
    "dynamic_profile_version",
    "position_sizing_version",
    "exit_policy_version",
    "backtest_engine_version",
)


class MetaStrategyRepositoryAttributionError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        reason_codes: tuple[str, ...] = (),
        observed_algorithm_id: str | None = None,
        observed_capital_partition_id: str | None = None,
        expected_algorithm_id: str = ALGORITHM_ID,
        expected_capital_partition_id: str = META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
    ) -> None:
        super().__init__(message)
        self.reason_codes = reason_codes
        self.observed_algorithm_id = observed_algorithm_id
        self.observed_capital_partition_id = observed_capital_partition_id
        self.expected_algorithm_id = expected_algorithm_id
        self.expected_capital_partition_id = expected_capital_partition_id


class MetaStrategyInventoryOwnershipConflict(ValueError):
    pass


@dataclass(frozen=True)
class MetaStrategyRepositoryRecord:
    table_name: str
    record_id: str
    artifact_type: str
    algorithm_id: str
    capital_partition_id: str
    decision_id: str
    settings_version: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class MetaStrategyPersistenceSummary:
    table_counts: dict[str, int]
    migration_version: str = META_STRATEGY_PERSISTENCE_MIGRATION_VERSION


@dataclass(frozen=True)
class MetaStrategyInventoryLot:
    lot_id: str
    symbol: str
    side: str
    quantity: float
    average_price: float
    opened_at: str
    order_intent_id: str
    broker_fill_id: str
    settings_version: str
    capital_partition_id: str
    correlation_id: str
    strategy_id: str = "meta_strategy"
    family: str = "UNKNOWN"


@dataclass(frozen=True)
class MetaStrategyInventoryPosition:
    position_id: str
    symbol: str
    side: str
    quantity: float
    average_price: float
    market_price: float
    unrealised_pnl: float
    capital_partition_id: str
    settings_version: str
    correlation_id: str


@dataclass(frozen=True)
class MetaStrategyInventorySnapshot:
    algorithm_id: str
    capital_partition_id: str
    settings_version: str
    snapshot_id: str
    rebuilt_from_ledger: bool
    open_positions: tuple[MetaStrategyInventoryPosition, ...]
    open_lots: tuple[MetaStrategyInventoryLot, ...]
    realised_pnl: float
    unrealised_pnl: float
    fees_and_slippage: float
    reserved_risk_dollars: float
    allocated_capital: float
    daily_trade_count: int
    daily_realised_pnl: float
    strategy_exposure: dict[str, float]
    family_exposure: dict[str, float]
    symbol_exposure: dict[str, float]
    reconciliation_checkpoint_id: str | None
    created_at: str


def migrate_meta_strategy_sqlite_database(path: str | Path) -> None:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        apply_meta_strategy_persistence_migrations(conn)


def apply_meta_strategy_persistence_migrations(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for table in META_STRATEGY_PERSISTENCE_TABLES:
        conn.execute(_table_ddl(table))
        _ensure_meta_strategy_columns(conn, table)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_decision ON {table}(decision_id)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_snapshot ON {table}(snapshot_id)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_run ON {table}(run_id)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_symbol_time ON {table}(symbol, timestamp)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_order_intent ON {table}(order_intent_id)")
    for table in META_STRATEGY_INVENTORY_TABLES:
        conn.execute(_inventory_table_ddl(table))
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_inventory_symbol_time ON {table}(symbol, timestamp)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_inventory_order_intent ON {table}(order_intent_id)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_inventory_client_order ON {table}(client_order_id)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_inventory_correlation ON {table}(correlation_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_meta_strategy_inventory_fills_broker_fill ON meta_strategy_inventory_fills(broker_fill_id)")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_meta_strategy_inventory_client_order_id
        ON meta_strategy_inventory_submitted_orders(client_order_id)
        WHERE algorithm_id = 'meta_strategy'
          AND capital_partition_id = 'meta_strategy.paper.default'
          AND client_order_id IS NOT NULL
          AND client_order_id <> ''
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_meta_strategy_inventory_order_intent_id
        ON meta_strategy_inventory_order_intents(order_intent_id)
        WHERE algorithm_id = 'meta_strategy'
          AND capital_partition_id = 'meta_strategy.paper.default'
          AND order_intent_id IS NOT NULL
          AND order_intent_id <> ''
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_meta_strategy_inventory_fill_event_id
        ON meta_strategy_inventory_fills(event_id)
        WHERE algorithm_id = 'meta_strategy'
          AND capital_partition_id = 'meta_strategy.paper.default'
          AND event_id IS NOT NULL
          AND event_id <> ''
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
        (META_STRATEGY_PERSISTENCE_MIGRATION_VERSION,),
    )


class MetaStrategySqliteRepository:
    def __init__(self, database_url: str | None = None, *, capital_partition_id: str = META_STRATEGY_DEFAULT_CAPITAL_PARTITION) -> None:
        self.capital_partition_id = str(capital_partition_id)
        self.path = _sqlite_path(database_url or os.getenv("DATABASE_URL", "sqlite:///./data/trading.db"))
        migrate_meta_strategy_sqlite_database(self.path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = self._open_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def inventory_transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._open_connection()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def persist(self, artifact_type: str, payload: Any, *, record_id: str | None = None) -> MetaStrategyRepositoryRecord:
        with self.connect() as conn:
            return persist_meta_strategy_projection_record(conn, artifact_type, payload, record_id=record_id)

    def persist_pipeline_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        record = self.persist("decisions", payload)
        return {
            "algorithmId": ALGORITHM_ID,
            "status": "PERSISTED",
            "recordId": record.record_id,
            "table": record.table_name,
            "reasonCodes": ("meta_strategy.repository.persisted",),
        }

    def load(self, artifact_type: str, record_id: str) -> MetaStrategyRepositoryRecord | None:
        table = _table_for_artifact(artifact_type)
        with self.connect() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE record_id = ?", (record_id,)).fetchone()
        return _row_to_record(table, artifact_type, row)

    def latest_for_decision(self, artifact_type: str, decision_id: str) -> MetaStrategyRepositoryRecord | None:
        table = _table_for_artifact(artifact_type)
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE decision_id = ? ORDER BY created_at DESC, record_id DESC LIMIT 1",
                (decision_id,),
            ).fetchone()
        return _row_to_record(table, artifact_type, row)

    def latest(self, artifact_type: str) -> MetaStrategyRepositoryRecord | None:
        table = _table_for_artifact(artifact_type)
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE algorithm_id = ? ORDER BY created_at DESC, record_id DESC LIMIT 1",
                (ALGORITHM_ID,),
            ).fetchone()
        return _row_to_record(table, artifact_type, row)

    def table_counts(self) -> MetaStrategyPersistenceSummary:
        with self.connect() as conn:
            counts = {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in META_STRATEGY_PERSISTENCE_TABLES}
        return MetaStrategyPersistenceSummary(table_counts=counts)

    def table_columns(self, table: str) -> tuple[str, ...]:
        if table not in META_STRATEGY_PERSISTENCE_TABLES:
            raise ValueError(f"Unknown Meta-Strategy persistence table: {table}")
        with self.connect() as conn:
            return tuple(str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall())

    def persistence_inventory(self) -> dict[str, Any]:
        columns = {table: self.table_columns(table) for table in META_STRATEGY_PERSISTENCE_TABLES}
        missing_attribution = {
            table: tuple(column for column in META_STRATEGY_REQUIRED_ATTRIBUTION_COLUMNS if column not in columns[table])
            for table in META_STRATEGY_PERSISTENCE_TABLES
        }
        missing_versions = {
            table: tuple(column for column in META_STRATEGY_VERSION_COLUMNS if column not in columns[table])
            for table in META_STRATEGY_PERSISTENCE_TABLES
        }
        return {
            "algorithmId": ALGORITHM_ID,
            "recordInventory": tuple(asdict(record) for record in META_STRATEGY_PERSISTENCE_RECORD_INVENTORY),
            "tables": META_STRATEGY_PERSISTENCE_TABLES,
            "requiredAttributionColumns": META_STRATEGY_REQUIRED_ATTRIBUTION_COLUMNS,
            "versionColumns": META_STRATEGY_VERSION_COLUMNS,
            "missingAttributionColumns": missing_attribution,
            "missingVersionColumns": missing_versions,
            "passed": not any(missing_attribution.values()) and not any(missing_versions.values()),
        }

    def record_order_intent(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _normalize_inventory_payload(payload)
        with self.inventory_transaction() as conn:
            self._insert_inventory_record(conn, "meta_strategy_inventory_order_intents", normalized)
            reserved = _float_value(normalized, "reservedRiskDollars", "reserved_risk_dollars")
            outstanding = self._reserved_risk_outstanding(conn, normalized)
            if reserved > 0.0 and outstanding <= 0.0:
                self._insert_inventory_record(
                    conn,
                    "meta_strategy_inventory_reserved_risk",
                    {**normalized, "reservedRiskDelta": reserved, "reservationStatus": "RESERVED"},
                )
            self._store_inventory_projection(conn, mark_prices={})
        return {"algorithmId": ALGORITHM_ID, "status": "RECORDED", "reasonCodes": ("meta_strategy.inventory.order_intent_recorded",)}

    def adjust_reserved_risk(self, payload: Mapping[str, Any], *, target_reserved_risk: float, reason: str) -> dict[str, Any]:
        normalized = _normalize_inventory_payload(payload)
        target = round(max(0.0, float(target_reserved_risk)), 10)
        with self.inventory_transaction() as conn:
            outstanding = self._reserved_risk_outstanding(conn, normalized)
            delta = round(target - outstanding, 10)
            if abs(delta) > 1e-9:
                self._insert_inventory_record(
                    conn,
                    "meta_strategy_inventory_reserved_risk",
                    {**normalized, "reservedRiskDelta": delta, "reservationStatus": reason},
                )
            self._store_inventory_projection(conn, mark_prices={})
        return {
            "algorithmId": ALGORITHM_ID,
            "status": "RECORDED",
            "targetReservedRiskDollars": target,
            "reasonCodes": ("meta_strategy.inventory.reserved_risk_adjusted",),
        }

    def record_submitted_order(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _normalize_inventory_payload(payload)
        with self.inventory_transaction() as conn:
            self._insert_inventory_record(conn, "meta_strategy_inventory_submitted_orders", normalized)
            self._insert_inventory_record(conn, "meta_strategy_inventory_order_status_history", normalized)
            self._store_inventory_projection(conn, mark_prices={})
        return {"algorithmId": ALGORITHM_ID, "status": "RECORDED", "reasonCodes": ("meta_strategy.inventory.order_recorded_without_position_change",)}

    def record_order_status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _normalize_inventory_payload(payload)
        status = _status(normalized)
        with self.inventory_transaction() as conn:
            self._insert_inventory_record(conn, "meta_strategy_inventory_order_status_history", normalized)
            if status in _RESERVATION_RELEASING_ORDER_STATUSES:
                self._release_reserved_risk(conn, normalized, reason=f"ORDER_{status}")
            if status in {"UNKNOWN", "TIMEOUT", "RECONCILIATION_REQUIRED"}:
                self._quarantine_inventory_record(conn, normalized, reason=f"ORDER_{status}")
            self._store_inventory_projection(conn, mark_prices={})
        return {"algorithmId": ALGORITHM_ID, "status": "RECORDED", "reasonCodes": ("meta_strategy.inventory.order_status_recorded",)}

    def ingest_broker_fill(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _normalize_inventory_payload(payload)
        _validate_required_fill_payload(normalized)
        broker_fill_id = _string_value(normalized, "", "brokerFillId", "broker_fill_id")
        with self.inventory_transaction() as conn:
            existing = conn.execute(
                """
                SELECT record_id
                FROM meta_strategy_inventory_fills
                WHERE algorithm_id=? AND capital_partition_id=? AND broker_fill_id=?
                """,
                (ALGORITHM_ID, self.capital_partition_id, broker_fill_id),
            ).fetchone()
            if existing is not None:
                return {"algorithmId": ALGORITHM_ID, "status": "DUPLICATE_IGNORED", "brokerFillId": broker_fill_id, "reasonCodes": ("meta_strategy.inventory.duplicate_fill_ignored",)}
            self._record_trade_for_exit_fill(conn, normalized)
            self._insert_inventory_record(conn, "meta_strategy_inventory_fills", normalized)
            self._release_reserved_risk_for_fill(conn, normalized)
            self._store_inventory_projection(conn, mark_prices={})
        return {"algorithmId": ALGORITHM_ID, "status": "INGESTED", "brokerFillId": broker_fill_id, "reasonCodes": ("meta_strategy.inventory.fill_ingested",)}

    def record_allocated_capital(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _normalize_inventory_payload(payload)
        with self.inventory_transaction() as conn:
            self._insert_inventory_record(conn, "meta_strategy_inventory_allocated_capital", normalized)
            self._store_inventory_projection(conn, mark_prices={})
        return {"algorithmId": ALGORITHM_ID, "status": "RECORDED", "reasonCodes": ("meta_strategy.inventory.allocated_capital_recorded",)}

    def record_reconciliation_checkpoint(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _normalize_inventory_payload(payload)
        with self.inventory_transaction() as conn:
            self._insert_inventory_record(conn, "meta_strategy_inventory_reconciliation_checkpoints", normalized)
            self._store_inventory_projection(conn, mark_prices={})
        return {"algorithmId": ALGORITHM_ID, "status": "RECORDED", "reasonCodes": ("meta_strategy.inventory.reconciliation_checkpoint_recorded",)}

    def record_position_lifecycle(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _normalize_inventory_payload(payload)
        with self.inventory_transaction() as conn:
            self._insert_inventory_record(conn, "meta_strategy_inventory_position_lifecycle", normalized)
        return {"algorithmId": ALGORITHM_ID, "status": "RECORDED", "reasonCodes": ("meta_strategy.inventory.position_lifecycle_recorded",)}

    def latest_position_lifecycle(self, *, position_id: str | None = None, symbol: str | None = None) -> dict[str, Any] | None:
        clauses = ["algorithm_id = ?", "capital_partition_id = ?"]
        params: list[Any] = [ALGORITHM_ID, self.capital_partition_id]
        if position_id:
            clauses.append("json_extract(payload_json, '$.positionId') = ?")
            params.append(position_id)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT record_id, algorithm_id, capital_partition_id, settings_version, correlation_id,
                       decision_id, order_intent_id, client_order_id, broker_order_id, broker_fill_id,
                       symbol, side, quantity, price, status, realised_pnl, timestamp, payload_json
                FROM meta_strategy_inventory_position_lifecycle
                WHERE {' AND '.join(clauses)}
                ORDER BY timestamp DESC, created_at DESC, record_id DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        if row is None:
            return None
        return _inventory_row_to_dict(row)

    def record_quarantine(self, payload: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
        normalized = _normalize_inventory_payload({**dict(payload), "quarantineReason": reason, "status": "QUARANTINED", "orderStatus": "QUARANTINED"})
        with self.inventory_transaction() as conn:
            self._quarantine_inventory_record(conn, normalized, reason=reason)
        return {"algorithmId": ALGORITHM_ID, "status": "QUARANTINED", "reasonCodes": ("meta_strategy.inventory.quarantined",)}

    def record_foreign_ownership_quarantine(self, payload: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
        observed_algorithm_id = _string_value(payload, "", "algorithmId", "algorithm_id")
        observed_capital_partition_id = _string_value(payload, "", "capitalPartitionId", "capital_partition_id")
        normalized = {
            **dict(payload),
            "algorithmId": ALGORITHM_ID,
            "algorithm_id": ALGORITHM_ID,
            "capitalPartitionId": self.capital_partition_id,
            "capital_partition_id": self.capital_partition_id,
            "observedAlgorithmId": observed_algorithm_id,
            "observedCapitalPartitionId": observed_capital_partition_id,
            "quarantineReason": reason,
            "status": "QUARANTINED",
            "orderStatus": "QUARANTINED",
        }
        with self.inventory_transaction() as conn:
            self._quarantine_inventory_record(conn, normalized, reason=reason)
        return {"algorithmId": ALGORITHM_ID, "status": "QUARANTINED", "reasonCodes": ("meta_strategy.inventory.ownership_conflict_quarantined",)}

    def current_inventory_snapshot(self, *, mark_prices: Mapping[str, float] | None = None, as_of: datetime | date | str | None = None) -> MetaStrategyInventorySnapshot:
        with self.connect() as conn:
            effective_mark_prices = self._effective_mark_prices(conn, mark_prices)
            snapshot = self._rebuild_inventory_from_ledger(conn, mark_prices=effective_mark_prices, session_date=_inventory_session_date(as_of))
            self._store_inventory_projection(conn, mark_prices=effective_mark_prices, snapshot=snapshot)
        return snapshot

    def rebuild_inventory_from_ledger(self, *, mark_prices: Mapping[str, float] | None = None, as_of: datetime | date | str | None = None) -> MetaStrategyInventorySnapshot:
        with self.connect() as conn:
            return self._rebuild_inventory_from_ledger(conn, mark_prices=self._effective_mark_prices(conn, mark_prices), session_date=_inventory_session_date(as_of))

    def check_inventory_consistency(self, *, mark_prices: Mapping[str, float] | None = None, as_of: datetime | date | str | None = None) -> dict[str, Any]:
        with self.connect() as conn:
            effective_mark_prices = self._effective_mark_prices(conn, mark_prices)
            derived = self._rebuild_inventory_from_ledger(conn, mark_prices=effective_mark_prices, session_date=_inventory_session_date(as_of))
            stored_row = conn.execute(
                """
                SELECT payload_json
                FROM meta_strategy_inventory_snapshots
                WHERE algorithm_id=? AND capital_partition_id=?
                ORDER BY created_at DESC, record_id DESC
                LIMIT 1
                """,
                (ALGORITHM_ID, self.capital_partition_id),
            ).fetchone()
            if stored_row is None:
                self._store_inventory_projection(conn, mark_prices=effective_mark_prices, snapshot=derived)
                stored = derived
            else:
                stored = _snapshot_from_payload(json.loads(str(stored_row["payload_json"])))
            consistent = stored.snapshot_id == derived.snapshot_id
            if not consistent:
                self._quarantine_inventory_record(
                    conn,
                    _snapshot_payload(derived),
                    reason="PROJECTION_MISMATCH",
                )
        return {
            "algorithmId": ALGORITHM_ID,
            "consistent": consistent,
            "reasonCodes": ("meta_strategy.inventory.consistent" if consistent else "meta_strategy.inventory.projection_mismatch",),
            "derivedSnapshotId": derived.snapshot_id,
            "storedSnapshotId": stored.snapshot_id,
        }

    def inventory_records(self, record_type: str, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        table = META_STRATEGY_INVENTORY_QUERY_TABLES.get(record_type)
        if table is None:
            raise ValueError(f"Unknown Meta-Strategy inventory query: {record_type}")
        bounded = max(1, min(int(limit), 500))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT record_id, algorithm_id, capital_partition_id, settings_version, correlation_id,
                       decision_id, order_intent_id, client_order_id, broker_order_id, broker_fill_id,
                       symbol, side, quantity, price, status, realised_pnl, timestamp, payload_json
                FROM {table}
                WHERE algorithm_id = ? AND capital_partition_id = ?
                ORDER BY timestamp DESC, record_id DESC
                LIMIT ?
                """,
                (ALGORITHM_ID, self.capital_partition_id, bounded),
            ).fetchall()
        return tuple(
            {
                "recordId": str(row["record_id"]),
                "algorithmId": str(row["algorithm_id"]),
                "capitalPartitionId": str(row["capital_partition_id"]),
                "settingsVersion": str(row["settings_version"]),
                "correlationId": str(row["correlation_id"]),
                "decisionId": str(row["decision_id"]),
                "orderIntentId": str(row["order_intent_id"]),
                "clientOrderId": str(row["client_order_id"]),
                "brokerOrderId": str(row["broker_order_id"]),
                "brokerFillId": str(row["broker_fill_id"]),
                "symbol": str(row["symbol"]),
                "side": str(row["side"]),
                "quantity": float(row["quantity"]),
                "price": float(row["price"]),
                "status": str(row["status"]),
                "realisedPnl": float(row["realised_pnl"]),
                "timestamp": str(row["timestamp"]),
                "payload": json.loads(str(row["payload_json"])),
            }
            for row in rows
        )

    def _record_trade_for_exit_fill(self, conn: sqlite3.Connection, fill: Mapping[str, Any]) -> None:
        if _string_value(fill, "", "side").upper() != "SELL":
            return
        symbol = _string_value(fill, "", "symbol", "ticker").upper()
        fill_qty = _fill_quantity(fill)
        if fill_qty <= 0.0:
            return
        exit_price = _float_value(fill, "fillPrice", "price", "averageFillPrice", "average_fill_price")
        before = self._rebuild_inventory_from_ledger(conn, mark_prices={})
        symbol_lots = [lot for lot in before.open_lots if lot.symbol == symbol]
        open_qty = round(sum(lot.quantity for lot in symbol_lots), 10)
        close_qty = min(fill_qty, open_qty)
        if close_qty <= 0.0:
            return

        remaining = close_qty
        realised = 0.0
        consumed_lots: list[dict[str, object]] = []
        for lot in symbol_lots:
            if remaining <= 0.0:
                break
            consumed = min(lot.quantity, remaining)
            realised += (exit_price - lot.average_price) * consumed
            consumed_lots.append(
                {
                    "lotId": lot.lot_id,
                    "quantity": round(consumed, 10),
                    "averagePrice": lot.average_price,
                    "openedAt": lot.opened_at,
                    "entryBrokerFillId": lot.broker_fill_id,
                }
            )
            remaining = round(remaining - consumed, 10)

        broker_fill_id = _string_value(fill, "", "brokerFillId", "broker_fill_id")
        trade_id = f"meta_strategy.trade.{self.capital_partition_id}.{broker_fill_id}"
        status = "CLOSED" if close_qty >= open_qty - 1e-9 else "PARTIALLY_CLOSED"
        self._insert_inventory_record(
            conn,
            "meta_strategy_inventory_trades",
            {
                **dict(fill),
                "eventId": f"trade-{broker_fill_id}",
                "tradeId": trade_id,
                "quantity": close_qty,
                "filledQuantity": close_qty,
                "realisedPnl": round(realised, 10),
                "realizedPnl": round(realised, 10),
                "status": status,
                "orderStatus": status,
                "exitPrice": exit_price,
                "closedQuantity": close_qty,
                "entryLots": consumed_lots,
            },
            record_id=trade_id,
        )
    def _insert_inventory_record(self, conn: sqlite3.Connection, table: str, payload: Mapping[str, Any], *, record_id: str | None = None) -> None:
        normalized = _normalize_inventory_payload(payload)
        metadata = _inventory_metadata(normalized)
        if metadata["capital_partition_id"] != self.capital_partition_id:
            raise MetaStrategyRepositoryAttributionError(
                f"Meta-Strategy inventory partition mismatch: {metadata['capital_partition_id']} != {self.capital_partition_id}",
                reason_codes=("meta_strategy.inventory.repository_capital_partition_mismatch",),
                observed_algorithm_id=ALGORITHM_ID,
                observed_capital_partition_id=str(metadata["capital_partition_id"]),
                expected_capital_partition_id=self.capital_partition_id,
            )
        payload_json = _json_dumps(normalized)
        persisted_record_id = record_id or _inventory_record_id(table, metadata, payload_json)
        self._assert_inventory_unique_identity(conn, table, metadata, persisted_record_id)
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {table} (
                record_id, algorithm_id, capital_partition_id, settings_version, correlation_id,
                decision_id, job_id, event_id, order_intent_id, client_order_id, broker_order_id,
                broker_fill_id, symbol, side, quantity, price, status, realised_pnl, timestamp, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                persisted_record_id,
                ALGORITHM_ID,
                metadata["capital_partition_id"],
                metadata["settings_version"],
                metadata["correlation_id"],
                metadata["decision_id"],
                metadata["job_id"],
                metadata["event_id"],
                metadata["order_intent_id"],
                metadata["client_order_id"],
                metadata["broker_order_id"],
                metadata["broker_fill_id"],
                metadata["symbol"],
                metadata["side"],
                metadata["quantity"],
                metadata["price"],
                metadata["status"],
                metadata["realised_pnl"],
                metadata["timestamp"],
                payload_json,
            ),
        )

    def _assert_inventory_unique_identity(
        self,
        conn: sqlite3.Connection,
        table: str,
        metadata: Mapping[str, Any],
        record_id: str,
    ) -> None:
        checks: tuple[tuple[str, str, str], ...]
        if table == "meta_strategy_inventory_order_intents":
            checks = (("order_intent_id", "order_intent_id", "meta_strategy.inventory.duplicate_order_intent_id"),)
        elif table == "meta_strategy_inventory_submitted_orders":
            checks = (("client_order_id", "client_order_id", "meta_strategy.inventory.duplicate_client_order_id"),)
        else:
            checks = ()
        for column, metadata_key, reason in checks:
            value = str(metadata.get(metadata_key) or "")
            if not value:
                continue
            row = conn.execute(
                f"""
                SELECT record_id, decision_id, order_intent_id
                FROM {table}
                WHERE algorithm_id=? AND capital_partition_id=? AND {column}=?
                LIMIT 1
                """,
                (ALGORITHM_ID, self.capital_partition_id, value),
            ).fetchone()
            if row is None or str(row["record_id"]) == record_id:
                continue
            same_decision = str(row["decision_id"]) == str(metadata.get("decision_id") or "")
            same_order_intent = str(row["order_intent_id"]) == str(metadata.get("order_intent_id") or "")
            if table == "meta_strategy_inventory_order_intents" and same_decision:
                continue
            if table == "meta_strategy_inventory_submitted_orders" and same_decision and same_order_intent:
                continue
            raise MetaStrategyInventoryOwnershipConflict(reason)

    def _quarantine_inventory_record(self, conn: sqlite3.Connection, payload: Mapping[str, Any], *, reason: str) -> None:
        normalized = {**dict(payload), "quarantineReason": reason, "status": "QUARANTINED", "orderStatus": "QUARANTINED"}
        self._insert_inventory_record(conn, "meta_strategy_inventory_quarantine", normalized)

    def _release_reserved_risk(self, conn: sqlite3.Connection, payload: Mapping[str, Any], *, reason: str) -> None:
        outstanding = self._reserved_risk_outstanding(conn, payload)
        if outstanding <= 0.0:
            return
        self._insert_inventory_record(
            conn,
            "meta_strategy_inventory_reserved_risk",
            {**payload, "reservedRiskDelta": -outstanding, "reservationStatus": reason},
        )

    def _release_reserved_risk_for_fill(self, conn: sqlite3.Connection, fill: Mapping[str, Any]) -> None:
        outstanding = self._reserved_risk_outstanding(conn, fill)
        if outstanding <= 0.0:
            return
        order_qty = self._order_intent_quantity(conn, fill)
        fill_qty = _fill_quantity(fill)
        original = self._reserved_risk_original(conn, fill)
        released = max(0.0, original - outstanding)
        cumulative = self._order_filled_quantity(conn, fill)
        target_release = original if order_qty <= 0.0 or cumulative >= order_qty else original * min(1.0, cumulative / order_qty)
        release = min(outstanding, max(0.0, target_release - released))
        if release <= 0.0:
            return
        self._insert_inventory_record(
            conn,
            "meta_strategy_inventory_reserved_risk",
            {**fill, "reservedRiskDelta": -release, "reservationStatus": "FILL_RELEASE"},
        )

    def _reserved_risk_outstanding(self, conn: sqlite3.Connection, payload: Mapping[str, Any]) -> float:
        order_intent_id = _string_value(payload, "", "orderIntentId", "order_intent_id")
        client_order_id = _string_value(payload, "", "clientOrderId", "client_order_id")
        row = conn.execute(
            """
            SELECT COALESCE(SUM(CAST(json_extract(payload_json, '$.reservedRiskDelta') AS REAL)), 0.0)
            FROM meta_strategy_inventory_reserved_risk
            WHERE algorithm_id=? AND capital_partition_id=? AND (order_intent_id=? OR client_order_id=?)
            """,
            (ALGORITHM_ID, self.capital_partition_id, order_intent_id, client_order_id),
        ).fetchone()
        return round(float(row[0] or 0.0), 10)

    def _reserved_risk_original(self, conn: sqlite3.Connection, payload: Mapping[str, Any]) -> float:
        order_intent_id = _string_value(payload, "", "orderIntentId", "order_intent_id")
        client_order_id = _string_value(payload, "", "clientOrderId", "client_order_id")
        row = conn.execute(
            """
            SELECT COALESCE(SUM(MAX(CAST(json_extract(payload_json, '$.reservedRiskDelta') AS REAL), 0.0)), 0.0)
            FROM meta_strategy_inventory_reserved_risk
            WHERE algorithm_id=? AND capital_partition_id=? AND (order_intent_id=? OR client_order_id=?)
            """,
            (ALGORITHM_ID, self.capital_partition_id, order_intent_id, client_order_id),
        ).fetchone()
        return round(float(row[0] or 0.0), 10)

    def _order_filled_quantity(self, conn: sqlite3.Connection, payload: Mapping[str, Any]) -> float:
        order_intent_id = _string_value(payload, "", "orderIntentId", "order_intent_id")
        client_order_id = _string_value(payload, "", "clientOrderId", "client_order_id")
        row = conn.execute(
            """
            SELECT COALESCE(SUM(quantity), 0.0)
            FROM meta_strategy_inventory_fills
            WHERE algorithm_id=? AND capital_partition_id=? AND (order_intent_id=? OR client_order_id=?)
            """,
            (ALGORITHM_ID, self.capital_partition_id, order_intent_id, client_order_id),
        ).fetchone()
        return round(float(row[0] or 0.0), 10)

    def _order_intent_quantity(self, conn: sqlite3.Connection, payload: Mapping[str, Any]) -> float:
        order_intent_id = _string_value(payload, "", "orderIntentId", "order_intent_id")
        row = conn.execute(
            "SELECT quantity FROM meta_strategy_inventory_order_intents WHERE algorithm_id=? AND capital_partition_id=? AND order_intent_id=? ORDER BY created_at DESC LIMIT 1",
            (ALGORITHM_ID, self.capital_partition_id, order_intent_id),
        ).fetchone()
        return float(row["quantity"]) if row is not None else 0.0

    def _reserved_risk_total(self, conn: sqlite3.Connection) -> float:
        row = conn.execute(
            "SELECT COALESCE(SUM(CAST(json_extract(payload_json, '$.reservedRiskDelta') AS REAL)), 0.0) FROM meta_strategy_inventory_reserved_risk WHERE algorithm_id=? AND capital_partition_id=?",
            (ALGORITHM_ID, self.capital_partition_id),
        ).fetchone()
        return round(max(0.0, float(row[0] or 0.0)), 10)

    def _allocated_capital(self, conn: sqlite3.Connection) -> float:
        row = conn.execute(
            "SELECT payload_json FROM meta_strategy_inventory_allocated_capital WHERE algorithm_id=? AND capital_partition_id=? ORDER BY timestamp DESC, created_at DESC LIMIT 1",
            (ALGORITHM_ID, self.capital_partition_id),
        ).fetchone()
        if row is None:
            return 0.0
        payload = json.loads(str(row["payload_json"]))
        return _float_value(payload, "allocatedCapital", "allocated_capital")

    def _latest_reconciliation_checkpoint(self, conn: sqlite3.Connection) -> str | None:
        row = conn.execute(
            "SELECT record_id FROM meta_strategy_inventory_reconciliation_checkpoints WHERE algorithm_id=? AND capital_partition_id=? ORDER BY timestamp DESC, created_at DESC LIMIT 1",
            (ALGORITHM_ID, self.capital_partition_id),
        ).fetchone()
        return None if row is None else str(row["record_id"])

    def _effective_mark_prices(self, conn: sqlite3.Connection, mark_prices: Mapping[str, float] | None) -> dict[str, float]:
        explicit = {str(symbol).upper(): float(price) for symbol, price in dict(mark_prices or {}).items() if price is not None}
        if explicit:
            return explicit
        rows = conn.execute(
            """
            SELECT symbol, payload_json
            FROM meta_strategy_inventory_positions
            WHERE algorithm_id=? AND capital_partition_id=?
            """,
            (ALGORITHM_ID, self.capital_partition_id),
        ).fetchall()
        recovered: dict[str, float] = {}
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                continue
            position_payload = payload.get("payload") if isinstance(payload, Mapping) else None
            if not isinstance(position_payload, Mapping):
                continue
            price = _float_value(position_payload, "market_price", "marketPrice")
            if price > 0.0:
                recovered[str(row["symbol"]).upper()] = price
        return recovered

    def _rebuild_inventory_from_ledger(self, conn: sqlite3.Connection, *, mark_prices: Mapping[str, float], session_date: date | None = None) -> MetaStrategyInventorySnapshot:
        fill_rows = conn.execute(
            "SELECT rowid, * FROM meta_strategy_inventory_fills WHERE algorithm_id=? AND capital_partition_id=? ORDER BY timestamp ASC, rowid ASC",
            (ALGORITHM_ID, self.capital_partition_id),
        ).fetchall()
        daily_session_date = session_date or _latest_inventory_session_date(fill_rows) or datetime.now(UTC).date()
        lots: list[dict[str, Any]] = []
        realised_pnl = 0.0
        daily_realised_pnl = 0.0
        fees_and_slippage = 0.0
        daily_trade_count = 0
        latest_settings_version = "meta_strategy_settings_v1"
        capital_partition_id = self.capital_partition_id
        for row in fill_rows:
            payload = json.loads(str(row["payload_json"]))
            latest_settings_version = str(row["settings_version"])
            capital_partition_id = str(row["capital_partition_id"])
            symbol = str(row["symbol"]).upper()
            side = str(row["side"]).upper()
            qty = abs(float(row["quantity"]))
            price = float(row["price"])
            fees_and_slippage += _fees_and_slippage(payload)
            if qty <= 0.0:
                continue
            if side == "BUY":
                lots.append(_lot_payload(row, payload, qty, price))
            elif side == "SELL":
                remaining = qty
                for lot in lots:
                    if remaining <= 0.0:
                        break
                    if lot["symbol"] != symbol or lot["quantity"] <= 0.0:
                        continue
                    consumed = min(float(lot["quantity"]), remaining)
                    realised_delta = (price - float(lot["average_price"])) * consumed
                    realised_pnl += realised_delta
                    if _inventory_timestamp_date(row["timestamp"]) == daily_session_date:
                        daily_realised_pnl += realised_delta
                    lot["quantity"] = round(float(lot["quantity"]) - consumed, 10)
                    remaining = round(remaining - consumed, 10)
                if remaining > 0.0:
                    lots.append(_lot_payload(row, payload, -remaining, price))
                if _inventory_timestamp_date(row["timestamp"]) == daily_session_date:
                    daily_trade_count += 1
        open_lots = tuple(
            MetaStrategyInventoryLot(
                lot_id=str(lot["lot_id"]),
                symbol=str(lot["symbol"]),
                side="LONG" if float(lot["quantity"]) > 0 else "SHORT",
                quantity=round(abs(float(lot["quantity"])), 10),
                average_price=round(float(lot["average_price"]), 10),
                opened_at=str(lot["opened_at"]),
                order_intent_id=str(lot["order_intent_id"]),
                broker_fill_id=str(lot["broker_fill_id"]),
                settings_version=str(lot["settings_version"]),
                capital_partition_id=str(lot["capital_partition_id"]),
                correlation_id=str(lot["correlation_id"]),
                strategy_id=str(lot.get("strategy_id") or "meta_strategy"),
                family=str(lot.get("family") or "UNKNOWN"),
            )
            for lot in lots
            if abs(float(lot["quantity"])) > 1e-9
        )
        positions = _positions_from_lots(open_lots, mark_prices)
        unrealised = round(sum(position.unrealised_pnl for position in positions), 10)
        reserved_risk = self._reserved_risk_total(conn)
        position_notional = round(sum(abs(position.quantity * position.market_price) for position in positions), 10)
        allocated_capital = max(self._allocated_capital(conn), position_notional)
        symbol_exposure = {position.symbol: round(position.quantity * position.market_price, 10) for position in positions}
        strategy_exposure = _exposure_by_key(open_lots, mark_prices, "strategyId", default="meta_strategy")
        family_exposure = _exposure_by_key(open_lots, mark_prices, "family", default="UNKNOWN")
        created_at = str(fill_rows[-1]["timestamp"]) if fill_rows else datetime.now(UTC).isoformat()
        checkpoint = self._latest_reconciliation_checkpoint(conn)
        return MetaStrategyInventorySnapshot(
            algorithm_id=ALGORITHM_ID,
            capital_partition_id=capital_partition_id,
            settings_version=latest_settings_version,
            snapshot_id=f"meta_strategy.inventory.snapshot.{_hash({'positions': [asdict(position) for position in positions], 'realised': realised_pnl, 'unrealised': unrealised, 'reserved': reserved_risk})}",
            rebuilt_from_ledger=True,
            open_positions=positions,
            open_lots=open_lots,
            realised_pnl=round(realised_pnl, 10),
            unrealised_pnl=unrealised,
            fees_and_slippage=round(fees_and_slippage, 10),
            reserved_risk_dollars=reserved_risk,
            allocated_capital=allocated_capital,
            daily_trade_count=daily_trade_count,
            daily_realised_pnl=round(daily_realised_pnl, 10),
            strategy_exposure=strategy_exposure or {"meta_strategy": round(sum(abs(value) for value in symbol_exposure.values()), 10)},
            family_exposure=family_exposure,
            symbol_exposure=symbol_exposure,
            reconciliation_checkpoint_id=checkpoint,
            created_at=created_at,
        )

    def _store_inventory_projection(
        self,
        conn: sqlite3.Connection,
        *,
        mark_prices: Mapping[str, float],
        snapshot: MetaStrategyInventorySnapshot | None = None,
    ) -> None:
        current = snapshot or self._rebuild_inventory_from_ledger(conn, mark_prices=self._effective_mark_prices(conn, mark_prices))
        for table in (
            "meta_strategy_inventory_positions",
            "meta_strategy_inventory_position_lots",
            "meta_strategy_inventory_realised_pnl",
            "meta_strategy_inventory_unrealised_pnl_snapshots",
            "meta_strategy_inventory_daily_statistics",
            "meta_strategy_inventory_strategy_exposure",
            "meta_strategy_inventory_symbol_exposure",
            "meta_strategy_inventory_family_exposure",
            "meta_strategy_inventory_snapshots",
        ):
            conn.execute(f"DELETE FROM {table} WHERE algorithm_id=? AND capital_partition_id=?", (ALGORITHM_ID, self.capital_partition_id))
        for position in current.open_positions:
            self._insert_inventory_record(conn, "meta_strategy_inventory_positions", _projection_payload(current, asdict(position), symbol=position.symbol))
        for lot in current.open_lots:
            self._insert_inventory_record(conn, "meta_strategy_inventory_position_lots", _projection_payload(current, asdict(lot), symbol=lot.symbol))
        self._insert_inventory_record(conn, "meta_strategy_inventory_realised_pnl", _projection_payload(current, {"realisedPnl": current.realised_pnl}))
        self._insert_inventory_record(conn, "meta_strategy_inventory_unrealised_pnl_snapshots", _projection_payload(current, {"unrealisedPnl": current.unrealised_pnl}))
        self._insert_inventory_record(conn, "meta_strategy_inventory_daily_statistics", _projection_payload(current, {"dailyTradeCount": current.daily_trade_count, "dailyRealisedPnl": current.daily_realised_pnl, "dailyRealizedPnl": current.daily_realised_pnl, "realisedPnl": current.realised_pnl, "unrealisedPnl": current.unrealised_pnl}))
        for strategy_id, value in current.strategy_exposure.items():
            self._insert_inventory_record(conn, "meta_strategy_inventory_strategy_exposure", _projection_payload(current, {"strategyId": strategy_id, "exposure": value}))
        for family, value in current.family_exposure.items():
            self._insert_inventory_record(conn, "meta_strategy_inventory_family_exposure", _projection_payload(current, {"family": family, "exposure": value}))
        for symbol, value in current.symbol_exposure.items():
            self._insert_inventory_record(conn, "meta_strategy_inventory_symbol_exposure", _projection_payload(current, {"symbolExposure": value}, symbol=symbol))
        self._insert_inventory_record(conn, "meta_strategy_inventory_snapshots", _snapshot_payload(current), record_id=current.snapshot_id)


class MetaStrategyRepositoryPersistenceAdapter:
    def __init__(self, repository: MetaStrategySqliteRepository) -> None:
        self.repository = repository

    def persist(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.repository.persist_pipeline_payload(payload)


def _table_ddl(table: str) -> str:
    return f"""
        CREATE TABLE IF NOT EXISTS {table} (
            record_id TEXT PRIMARY KEY,
            artifact_type TEXT NOT NULL,
            algorithm_id TEXT NOT NULL CHECK(algorithm_id = 'meta_strategy'),
            capital_partition_id TEXT NOT NULL CHECK(capital_partition_id = '{META_STRATEGY_DEFAULT_CAPITAL_PARTITION}'),
            algorithm_version TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            settings_version TEXT NOT NULL,
            strategy_catalog_version TEXT NOT NULL,
            feature_schema_version TEXT NOT NULL,
            label_specification_version TEXT NOT NULL,
            model_version TEXT NOT NULL,
            model_artifact_version TEXT NOT NULL,
            dynamic_profile_version TEXT NOT NULL,
            position_sizing_version TEXT NOT NULL,
            exit_policy_version TEXT NOT NULL,
            backtest_engine_version TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            bar_end TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            job_id TEXT,
            event_id TEXT,
            snapshot_id TEXT NOT NULL,
            order_intent_id TEXT,
            client_order_id TEXT,
            broker_order_id TEXT,
            trade_id TEXT,
            run_id TEXT,
            artifact_id TEXT,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """


def _inventory_table_ddl(table: str) -> str:
    return f"""
        CREATE TABLE IF NOT EXISTS {table} (
            record_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL CHECK(algorithm_id = 'meta_strategy'),
            capital_partition_id TEXT NOT NULL CHECK(capital_partition_id = '{META_STRATEGY_DEFAULT_CAPITAL_PARTITION}'),
            settings_version TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            job_id TEXT,
            event_id TEXT,
            order_intent_id TEXT,
            client_order_id TEXT,
            broker_order_id TEXT,
            broker_fill_id TEXT,
            symbol TEXT NOT NULL,
            side TEXT,
            quantity REAL NOT NULL DEFAULT 0,
            price REAL NOT NULL DEFAULT 0,
            status TEXT,
            realised_pnl REAL NOT NULL DEFAULT 0,
            timestamp TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """


def _ensure_meta_strategy_columns(conn: sqlite3.Connection, table: str) -> None:
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    required = {
        "record_id": "TEXT",
        "artifact_type": "TEXT NOT NULL DEFAULT ''",
        "algorithm_id": "TEXT NOT NULL DEFAULT 'meta_strategy'",
        "capital_partition_id": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_DEFAULT_CAPITAL_PARTITION}'",
        "algorithm_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_ALGORITHM_VERSION}'",
        "configuration_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_CONFIGURATION_VERSION}'",
        "settings_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_CONFIGURATION_VERSION}'",
        "strategy_catalog_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_STRATEGY_CATALOG_VERSION}'",
        "feature_schema_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_FEATURE_SCHEMA_VERSION}'",
        "label_specification_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_LABEL_SPECIFICATION_VERSION}'",
        "model_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_MODEL_VERSION}'",
        "model_artifact_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_MODEL_ARTIFACT_VERSION}'",
        "dynamic_profile_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_DYNAMIC_PROFILE_VERSION}'",
        "position_sizing_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_POSITION_SIZING_VERSION}'",
        "exit_policy_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_EXIT_POLICY_VERSION}'",
        "backtest_engine_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_BACKTEST_ENGINE_VERSION}'",
        "timestamp": "TEXT NOT NULL DEFAULT ''",
        "symbol": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "bar_end": "TEXT NOT NULL DEFAULT ''",
        "decision_id": "TEXT NOT NULL DEFAULT ''",
        "idempotency_key": "TEXT NOT NULL DEFAULT ''",
        "job_id": "TEXT",
        "event_id": "TEXT",
        "snapshot_id": "TEXT NOT NULL DEFAULT ''",
        "order_intent_id": "TEXT",
        "client_order_id": "TEXT",
        "broker_order_id": "TEXT",
        "trade_id": "TEXT",
        "run_id": "TEXT",
        "artifact_id": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'PERSISTED'",
        "payload_json": "TEXT NOT NULL DEFAULT '{}'",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    }
    for column, ddl in required.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _table_for_artifact(artifact_type: str) -> str:
    try:
        return META_STRATEGY_PERSISTENCE_TABLE_BY_RECORD_ID[artifact_type]
    except KeyError as exc:
        raise ValueError(f"Unknown Meta-Strategy artifact type: {artifact_type}") from exc


def persist_meta_strategy_projection_record(
    conn: sqlite3.Connection,
    artifact_type: str,
    payload: Any,
    *,
    record_id: str | None = None,
) -> MetaStrategyRepositoryRecord:
    table = _table_for_artifact(artifact_type)
    normalized = _normalize_payload(payload)
    metadata = _metadata(normalized)
    payload_json = _json_dumps(normalized)
    persisted_record_id = record_id or _record_id(table, metadata, payload_json)
    conn.execute(
        f"""
        INSERT OR REPLACE INTO {table} (
            record_id, artifact_type, algorithm_id, capital_partition_id, algorithm_version, configuration_version,
            settings_version,
            strategy_catalog_version, feature_schema_version, label_specification_version,
            model_version, model_artifact_version, dynamic_profile_version,
            position_sizing_version, exit_policy_version, backtest_engine_version,
            timestamp, symbol, bar_end, decision_id, idempotency_key, job_id, event_id, snapshot_id, order_intent_id,
            client_order_id, broker_order_id, trade_id, run_id, artifact_id, status, payload_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            persisted_record_id,
            artifact_type,
            ALGORITHM_ID,
            metadata["capital_partition_id"],
            metadata["algorithm_version"],
            metadata["configuration_version"],
            metadata["settings_version"],
            metadata["strategy_catalog_version"],
            metadata["feature_schema_version"],
            metadata["label_specification_version"],
            metadata["model_version"],
            metadata["model_artifact_version"],
            metadata["dynamic_profile_version"],
            metadata["position_sizing_version"],
            metadata["exit_policy_version"],
            metadata["backtest_engine_version"],
            metadata["timestamp"],
            metadata["symbol"],
            metadata["bar_end"],
            metadata["decision_id"],
            metadata["idempotency_key"],
            metadata["job_id"],
            metadata["event_id"],
            metadata["snapshot_id"],
            metadata["order_intent_id"],
            metadata["client_order_id"],
            metadata["broker_order_id"],
            metadata["trade_id"],
            metadata["run_id"],
            metadata["artifact_id"],
            metadata["status"],
            payload_json,
            metadata["created_at"],
            metadata["updated_at"],
        ),
    )
    return MetaStrategyRepositoryRecord(
        table_name=table,
        record_id=persisted_record_id,
        artifact_type=artifact_type,
        algorithm_id=ALGORITHM_ID,
        capital_partition_id=metadata["capital_partition_id"],
        decision_id=metadata["decision_id"],
        settings_version=metadata["settings_version"],
        payload=normalized,
    )


def _normalize_payload(payload: Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        normalized = payload.model_dump(mode="json")
    elif is_dataclass(payload):
        normalized = asdict(payload)
    elif isinstance(payload, Mapping):
        normalized = dict(payload)
    else:
        raise TypeError("Meta-Strategy repository payload must be a mapping, dataclass, or pydantic model")
    violation = meta_strategy_ownership_violation(normalized, require_capital_partition=False, scope="repository")
    if violation is not None:
        raise _attribution_error(
            "Meta-Strategy repository payloads must carry algorithm_id='meta_strategy'",
            violation,
        )
    return _jsonable(normalized)


def _normalize_inventory_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _jsonable(dict(payload))
    violation = meta_strategy_ownership_violation(normalized, require_capital_partition=True, scope="inventory")
    if violation is not None:
        raise _attribution_error(
            "Meta-Strategy inventory records must carry algorithm_id='meta_strategy' and capital_partition_id='meta_strategy.paper.default'",
            violation,
        )
    return normalized


def _attribution_error(message: str, violation: Mapping[str, Any]) -> MetaStrategyRepositoryAttributionError:
    return MetaStrategyRepositoryAttributionError(
        message,
        reason_codes=tuple(violation.get("reasonCodes") or ()),
        observed_algorithm_id=violation.get("observedAlgorithmId"),
        observed_capital_partition_id=violation.get("observedCapitalPartitionId"),
        expected_algorithm_id=str(violation.get("expectedAlgorithmId") or ALGORITHM_ID),
        expected_capital_partition_id=str(violation.get("expectedCapitalPartitionId") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION),
    )

def _metadata(payload: Mapping[str, Any]) -> dict[str, str]:
    return {
        "capital_partition_id": _string_value(payload, META_STRATEGY_DEFAULT_CAPITAL_PARTITION, "capitalPartitionId", "capital_partition_id"),
        "algorithm_version": _string_value(payload, META_STRATEGY_ALGORITHM_VERSION, "algorithmVersion", "algorithm_version"),
        "configuration_version": _string_value(payload, META_STRATEGY_CONFIGURATION_VERSION, "configurationVersion", "configuration_version", "settingsVersion", "settings_version"),
        "settings_version": _string_value(payload, META_STRATEGY_CONFIGURATION_VERSION, "settingsVersion", "settings_version", "configurationVersion", "configuration_version"),
        "strategy_catalog_version": _string_value(payload, META_STRATEGY_STRATEGY_CATALOG_VERSION, "strategyCatalogVersion", "strategy_catalog_version", "strategyVersion", "strategy_version"),
        "feature_schema_version": _string_value(payload, META_STRATEGY_FEATURE_SCHEMA_VERSION, "featureSchemaVersion", "feature_schema_version"),
        "label_specification_version": _string_value(payload, META_STRATEGY_LABEL_SPECIFICATION_VERSION, "labelSpecificationVersion", "label_specification_version"),
        "model_version": _string_value(payload, META_STRATEGY_MODEL_VERSION, "modelVersion", "model_version"),
        "model_artifact_version": _string_value(payload, META_STRATEGY_MODEL_ARTIFACT_VERSION, "modelArtifactVersion", "model_artifact_version"),
        "dynamic_profile_version": _string_value(payload, META_STRATEGY_DYNAMIC_PROFILE_VERSION, "dynamicProfileVersion", "dynamic_profile_version", "profileVersion", "profile_version"),
        "position_sizing_version": _string_value(payload, META_STRATEGY_POSITION_SIZING_VERSION, "positionSizingVersion", "position_sizing_version"),
        "exit_policy_version": _string_value(payload, META_STRATEGY_EXIT_POLICY_VERSION, "exitPolicyVersion", "exit_policy_version"),
        "backtest_engine_version": _string_value(payload, META_STRATEGY_BACKTEST_ENGINE_VERSION, "backtestEngineVersion", "backtest_engine_version"),
        "timestamp": _string_value(payload, datetime.now(tz=UTC).isoformat(), "timestamp", "capturedAt", "createdAt"),
        "symbol": _string_value(payload, "UNKNOWN", "symbol", "ticker"),
        "bar_end": _string_value(payload, "", "barEnd", "bar_end", "timestamp", "capturedAt"),
        "decision_id": _string_value(payload, "unknown-decision", "decisionId", "decision_id", "id"),
        "idempotency_key": _string_value(payload, "unknown-idempotency", "idempotencyKey", "idempotency_key", "decisionId", "decision_id"),
        "job_id": _string_value(payload, "", "jobId", "job_id"),
        "event_id": _string_value(payload, "", "eventId", "event_id"),
        "snapshot_id": _string_value(payload, "unknown-snapshot", "snapshotId", "snapshot_id", "marketSnapshotId", "market_snapshot_id"),
        "order_intent_id": _string_value(payload, "", "orderIntentId", "order_intent_id"),
        "client_order_id": _string_value(payload, "", "clientOrderId", "client_order_id"),
        "broker_order_id": _string_value(payload, "", "brokerOrderId", "broker_order_id"),
        "trade_id": _string_value(payload, "", "tradeId", "trade_id"),
        "run_id": _string_value(payload, "", "runId", "run_id"),
        "artifact_id": _string_value(payload, "", "artifactId", "artifact_id"),
        "status": _string_value(payload, "PERSISTED", "status", "decisionStatus", "decision_status"),
        "created_at": _string_value(payload, datetime.now(tz=UTC).isoformat(), "createdAt", "created_at", "timestamp"),
        "updated_at": _string_value(payload, datetime.now(tz=UTC).isoformat(), "updatedAt", "updated_at", "timestamp"),
    }


def _inventory_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "capital_partition_id": _string_value(payload, META_STRATEGY_DEFAULT_CAPITAL_PARTITION, "capitalPartitionId", "capital_partition_id"),
        "settings_version": _string_value(payload, "meta_strategy_settings_v1", "settingsVersion", "settings_version"),
        "correlation_id": _string_value(payload, "unknown-correlation", "correlationId", "correlation_id", "decisionId", "decision_id"),
        "decision_id": _string_value(payload, "unknown-decision", "decisionId", "decision_id"),
        "job_id": _string_value(payload, "", "jobId", "job_id"),
        "event_id": _string_value(payload, "", "eventId", "event_id"),
        "order_intent_id": _string_value(payload, "", "orderIntentId", "order_intent_id"),
        "client_order_id": _string_value(payload, "", "clientOrderId", "client_order_id"),
        "broker_order_id": _string_value(payload, "", "brokerOrderId", "broker_order_id"),
        "broker_fill_id": _string_value(payload, "", "brokerFillId", "broker_fill_id"),
        "symbol": _string_value(payload, "UNKNOWN", "symbol", "ticker").upper(),
        "side": _string_value(payload, "", "side").upper(),
        "quantity": _fill_quantity(payload) or _float_value(payload, "quantity", "orderQuantity"),
        "price": _float_value(payload, "fillPrice", "price", "averageFillPrice", "average_fill_price", "limitPrice"),
        "status": _status(payload),
        "realised_pnl": _float_value(payload, "realisedPnl", "realizedPnl"),
        "timestamp": _string_value(payload, datetime.now(tz=UTC).isoformat(), "timestamp", "filledAt", "filled_at", "createdAt"),
    }


def _validate_required_fill_payload(payload: Mapping[str, Any]) -> None:
    missing = []
    if not _string_value(payload, "", "orderIntentId", "order_intent_id"):
        missing.append("orderIntentId")
    if not _string_value(payload, "", "clientOrderId", "client_order_id"):
        missing.append("clientOrderId")
    if not _string_value(payload, "", "brokerOrderId", "broker_order_id"):
        missing.append("brokerOrderId")
    if not _string_value(payload, "", "brokerFillId", "broker_fill_id"):
        missing.append("brokerFillId")
    if not _string_value(payload, "", "symbol", "ticker"):
        missing.append("symbol")
    if _string_value(payload, "", "side").upper() not in {"BUY", "SELL"}:
        missing.append("side")
    try:
        quantity = _fill_quantity(payload)
    except (TypeError, ValueError):
        quantity = 0.0
    if quantity <= 0.0:
        missing.append("quantity")
    try:
        price = _float_value(payload, "fillPrice", "price", "averageFillPrice", "average_fill_price")
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0.0:
        missing.append("price")
    timestamp = _string_value(payload, "", "timestamp", "filledAt", "filled_at", "createdAt")
    if not timestamp or not _valid_inventory_timestamp(timestamp):
        missing.append("timestamp")
    if missing:
        raise ValueError("meta_strategy.inventory.fill_malformed.missing_" + "_".join(item.lower() for item in missing))


def _valid_inventory_timestamp(value: Any) -> bool:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return True

def _inventory_session_date(value: datetime | date | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return _inventory_timestamp_date(value)


def _inventory_timestamp_date(value: Any) -> date | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _latest_inventory_session_date(rows: tuple[sqlite3.Row, ...] | list[sqlite3.Row]) -> date | None:
    for row in reversed(rows):
        parsed = _inventory_timestamp_date(row["timestamp"])
        if parsed is not None:
            return parsed
    return None


def _float_value(payload: Mapping[str, Any], *keys: str) -> float:
    value = _first_value(payload, *keys)
    if value is None or value == "":
        return 0.0
    return float(value)


def _fill_quantity(payload: Mapping[str, Any]) -> float:
    return abs(_float_value(payload, "filledQuantity", "filled_quantity", "quantity"))


def _status(payload: Mapping[str, Any]) -> str:
    return _string_value(payload, "", "orderStatus", "order_status", "status", "fillStatus").upper()


def _first_value(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _string_value(payload: Mapping[str, Any], default: str, *keys: str) -> str:
    value = _first_value(payload, *keys)
    if value is None or value == "":
        return default
    return str(value)


def _record_id(table: str, metadata: Mapping[str, str], payload_json: str) -> str:
    digest = hashlib.sha256(f"{table}:{metadata['decision_id']}:{metadata['snapshot_id']}:{payload_json}".encode("utf-8")).hexdigest()[:16]
    return f"{table}.{metadata['decision_id']}.{digest}"


def _inventory_record_id(table: str, metadata: Mapping[str, Any], payload_json: str) -> str:
    stable = metadata.get("broker_fill_id") or metadata.get("event_id") or payload_json
    digest = hashlib.sha256(f"{table}:{metadata['capital_partition_id']}:{metadata['decision_id']}:{stable}:{payload_json}".encode("utf-8")).hexdigest()[:16]
    return f"{table}.{metadata['decision_id']}.{digest}"


def _lot_payload(row: sqlite3.Row, payload: Mapping[str, Any], quantity: float, price: float) -> dict[str, Any]:
    broker_fill_id = str(row["broker_fill_id"])
    return {
        "lot_id": f"meta_strategy.lot.{broker_fill_id}",
        "symbol": str(row["symbol"]).upper(),
        "quantity": float(quantity),
        "average_price": float(price),
        "opened_at": str(row["timestamp"]),
        "order_intent_id": str(row["order_intent_id"]),
        "broker_fill_id": broker_fill_id,
        "settings_version": str(row["settings_version"]),
        "capital_partition_id": str(row["capital_partition_id"]),
        "correlation_id": str(row["correlation_id"]),
        "strategy_id": _string_value(payload, "meta_strategy", "strategyId", "strategy_id"),
        "family": _string_value(payload, "UNKNOWN", "family", "strategyFamily", "strategy_family"),
    }


def _positions_from_lots(open_lots: tuple[MetaStrategyInventoryLot, ...], mark_prices: Mapping[str, float]) -> tuple[MetaStrategyInventoryPosition, ...]:
    grouped: dict[str, list[MetaStrategyInventoryLot]] = {}
    for lot in open_lots:
        grouped.setdefault(lot.symbol, []).append(lot)
    positions: list[MetaStrategyInventoryPosition] = []
    for symbol, lots in sorted(grouped.items()):
        signed_qty = sum(lot.quantity if lot.side == "LONG" else -lot.quantity for lot in lots)
        if abs(signed_qty) <= 1e-9:
            continue
        total_abs = sum(lot.quantity for lot in lots)
        average = sum(lot.quantity * lot.average_price for lot in lots) / total_abs if total_abs else 0.0
        mark = float(mark_prices.get(symbol, average))
        side = "LONG" if signed_qty > 0 else "SHORT"
        unrealised = (mark - average) * abs(signed_qty) if side == "LONG" else (average - mark) * abs(signed_qty)
        first = lots[0]
        positions.append(
            MetaStrategyInventoryPosition(
                position_id=f"meta_strategy.position.{first.capital_partition_id}.{symbol}",
                symbol=symbol,
                side=side,
                quantity=round(abs(signed_qty), 10),
                average_price=round(average, 10),
                market_price=round(mark, 10),
                unrealised_pnl=round(unrealised, 10),
                capital_partition_id=first.capital_partition_id,
                settings_version=first.settings_version,
                correlation_id=first.correlation_id,
            )
        )
    return tuple(positions)


def _fees_and_slippage(payload: Mapping[str, Any]) -> float:
    return round(
        _float_value(payload, "commission", "fees", "fee")
        + _float_value(payload, "estimatedSlippage", "estimated_slippage", "slippage"),
        10,
    )


def _exposure_by_key(open_lots: tuple[MetaStrategyInventoryLot, ...], mark_prices: Mapping[str, float], key: str, *, default: str) -> dict[str, float]:
    exposure: dict[str, float] = {}
    for lot in open_lots:
        value = str(getattr(lot, "strategy_id" if key == "strategyId" else "family", default) or default)
        mark = float(mark_prices.get(lot.symbol, lot.average_price))
        exposure[value] = round(exposure.get(value, 0.0) + abs(float(lot.quantity) * mark), 10)
    return exposure


def _projection_payload(snapshot: MetaStrategyInventorySnapshot, payload: Mapping[str, Any], *, symbol: str = "PORTFOLIO") -> dict[str, Any]:
    return {
        "algorithmId": ALGORITHM_ID,
        "capitalPartitionId": snapshot.capital_partition_id,
        "settingsVersion": snapshot.settings_version,
        "correlationId": snapshot.snapshot_id,
        "decisionId": snapshot.snapshot_id,
        "jobId": "",
        "eventId": snapshot.snapshot_id,
        "orderIntentId": "",
        "clientOrderId": "",
        "brokerOrderId": "",
        "brokerFillId": "",
        "symbol": symbol,
        "side": "",
        "quantity": 0.0,
        "price": 0.0,
        "status": "CURRENT",
        "timestamp": snapshot.created_at,
        "payload": dict(payload),
    }


def _snapshot_payload(snapshot: MetaStrategyInventorySnapshot) -> dict[str, Any]:
    payload = _jsonable(asdict(snapshot))
    return {**_projection_payload(snapshot, payload), "payload": payload}


def _snapshot_from_payload(payload: Mapping[str, Any]) -> MetaStrategyInventorySnapshot:
    data = dict(payload.get("payload") or payload)
    return MetaStrategyInventorySnapshot(
        algorithm_id=str(data["algorithm_id"]),
        capital_partition_id=str(data["capital_partition_id"]),
        settings_version=str(data["settings_version"]),
        snapshot_id=str(data["snapshot_id"]),
        rebuilt_from_ledger=bool(data["rebuilt_from_ledger"]),
        open_positions=tuple(MetaStrategyInventoryPosition(**item) for item in data.get("open_positions", ())),
        open_lots=tuple(MetaStrategyInventoryLot(**item) for item in data.get("open_lots", ())),
        realised_pnl=float(data["realised_pnl"]),
        unrealised_pnl=float(data["unrealised_pnl"]),
        fees_and_slippage=float(data.get("fees_and_slippage", 0.0)),
        reserved_risk_dollars=float(data["reserved_risk_dollars"]),
        allocated_capital=float(data["allocated_capital"]),
        daily_trade_count=int(data["daily_trade_count"]),
        daily_realised_pnl=float(data.get("daily_realised_pnl", data.get("daily_realized_pnl", data.get("dailyRealisedPnl", data.get("dailyRealizedPnl", data["realised_pnl"]))))),
        strategy_exposure={str(key): float(value) for key, value in dict(data.get("strategy_exposure", {})).items()},
        family_exposure={str(key): float(value) for key, value in dict(data.get("family_exposure", {})).items()},
        symbol_exposure={str(key): float(value) for key, value in dict(data.get("symbol_exposure", {})).items()},
        reconciliation_checkpoint_id=data.get("reconciliation_checkpoint_id"),
        created_at=str(data["created_at"]),
    )


def _inventory_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "recordId": str(row["record_id"]),
        "algorithmId": str(row["algorithm_id"]),
        "capitalPartitionId": str(row["capital_partition_id"]),
        "settingsVersion": str(row["settings_version"]),
        "correlationId": str(row["correlation_id"]),
        "decisionId": str(row["decision_id"]),
        "orderIntentId": str(row["order_intent_id"]),
        "clientOrderId": str(row["client_order_id"]),
        "brokerOrderId": str(row["broker_order_id"]),
        "brokerFillId": str(row["broker_fill_id"]),
        "symbol": str(row["symbol"]),
        "side": str(row["side"]),
        "quantity": float(row["quantity"]),
        "price": float(row["price"]),
        "status": str(row["status"]),
        "realisedPnl": float(row["realised_pnl"]),
        "timestamp": str(row["timestamp"]),
        "payload": json.loads(str(row["payload_json"])),
    }


def _hash(value: Any) -> str:
    return hashlib.sha256(_json_dumps(value).encode("utf-8")).hexdigest()[:16]


def _row_to_record(table: str, artifact_type: str, row: sqlite3.Row | None) -> MetaStrategyRepositoryRecord | None:
    if row is None:
        return None
    violation = meta_strategy_ownership_violation(
        {"algorithmId": str(row["algorithm_id"]), "capitalPartitionId": str(row["capital_partition_id"])},
        require_capital_partition=True,
        scope="repository",
    )
    if violation is not None:
        raise _attribution_error(
            f"Meta-Strategy repository refused {artifact_type} record {row['record_id']} owned by {row['algorithm_id']} in partition {row['capital_partition_id']}",
            violation,
        )
    return MetaStrategyRepositoryRecord(
        table_name=table,
        record_id=str(row["record_id"]),
        artifact_type=artifact_type,
        algorithm_id=str(row["algorithm_id"]),
        capital_partition_id=str(row["capital_partition_id"]),
        decision_id=str(row["decision_id"]),
        settings_version=str(row["settings_version"]),
        payload=json.loads(str(row["payload_json"])),
    )


def _json_dumps(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(child) for child in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


__all__ = [
    "META_STRATEGY_INVENTORY_TABLES",
    "META_STRATEGY_PERSISTENCE_MIGRATION_VERSION",
    "META_STRATEGY_PERSISTENCE_RECORD_IDS",
    "META_STRATEGY_PERSISTENCE_RECORD_INVENTORY",
    "META_STRATEGY_PERSISTENCE_TABLES",
    "META_STRATEGY_REQUIRED_ATTRIBUTION_COLUMNS",
    "META_STRATEGY_REPOSITORY_IDENTITY_COLUMNS",
    "META_STRATEGY_VERSION_COLUMNS",
    "MetaStrategyPersistenceRecordDefinition",
    "MetaStrategyPersistenceSummary",
    "MetaStrategyInventoryLot",
    "MetaStrategyInventoryOwnershipConflict",
    "MetaStrategyInventoryPosition",
    "MetaStrategyInventorySnapshot",
    "MetaStrategyRepositoryAttributionError",
    "MetaStrategyRepositoryPersistenceAdapter",
    "MetaStrategyRepositoryRecord",
    "MetaStrategySqliteRepository",
    "apply_meta_strategy_persistence_migrations",
    "migrate_meta_strategy_sqlite_database",
    "persist_meta_strategy_projection_record",
]
