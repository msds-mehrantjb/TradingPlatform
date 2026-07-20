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


META_STRATEGY_PERSISTENCE_MIGRATION_VERSION = "meta_strategy_repository_001"


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

META_STRATEGY_REQUIRED_ATTRIBUTION_COLUMNS = (
    "algorithm_id",
    "algorithm_version",
    "configuration_version",
    "strategy_catalog_version",
    "timestamp",
    "symbol",
    "decision_id",
    "snapshot_id",
)
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
    pass


@dataclass(frozen=True)
class MetaStrategyRepositoryRecord:
    table_name: str
    record_id: str
    artifact_type: str
    algorithm_id: str
    decision_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class MetaStrategyPersistenceSummary:
    table_counts: dict[str, int]
    migration_version: str = META_STRATEGY_PERSISTENCE_MIGRATION_VERSION


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
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
        (META_STRATEGY_PERSISTENCE_MIGRATION_VERSION,),
    )


class MetaStrategySqliteRepository:
    def __init__(self, database_url: str | None = None) -> None:
        self.path = _sqlite_path(database_url or os.getenv("DATABASE_URL", "sqlite:///./data/trading.db"))
        migrate_meta_strategy_sqlite_database(self.path)

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

    def persist(self, artifact_type: str, payload: Any, *, record_id: str | None = None) -> MetaStrategyRepositoryRecord:
        table = _table_for_artifact(artifact_type)
        normalized = _normalize_payload(payload)
        metadata = _metadata(normalized)
        payload_json = _json_dumps(normalized)
        persisted_record_id = record_id or _record_id(table, metadata, payload_json)
        with self.connect() as conn:
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {table} (
                    record_id, artifact_type, algorithm_id, algorithm_version, configuration_version,
                    strategy_catalog_version, feature_schema_version, label_specification_version,
                    model_version, model_artifact_version, dynamic_profile_version,
                    position_sizing_version, exit_policy_version, backtest_engine_version,
                    timestamp, symbol, decision_id, snapshot_id, order_intent_id,
                    trade_id, run_id, artifact_id, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    persisted_record_id,
                    artifact_type,
                    ALGORITHM_ID,
                    metadata["algorithm_version"],
                    metadata["configuration_version"],
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
                    metadata["decision_id"],
                    metadata["snapshot_id"],
                    metadata["order_intent_id"],
                    metadata["trade_id"],
                    metadata["run_id"],
                    metadata["artifact_id"],
                    payload_json,
                ),
            )
        return MetaStrategyRepositoryRecord(
            table_name=table,
            record_id=persisted_record_id,
            artifact_type=artifact_type,
            algorithm_id=ALGORITHM_ID,
            decision_id=metadata["decision_id"],
            payload=normalized,
        )

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
            algorithm_version TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
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
            decision_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            order_intent_id TEXT,
            trade_id TEXT,
            run_id TEXT,
            artifact_id TEXT,
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
        "algorithm_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_ALGORITHM_VERSION}'",
        "configuration_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_CONFIGURATION_VERSION}'",
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
        "decision_id": "TEXT NOT NULL DEFAULT ''",
        "snapshot_id": "TEXT NOT NULL DEFAULT ''",
        "order_intent_id": "TEXT",
        "trade_id": "TEXT",
        "run_id": "TEXT",
        "artifact_id": "TEXT",
        "payload_json": "TEXT NOT NULL DEFAULT '{}'",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    }
    for column, ddl in required.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _table_for_artifact(artifact_type: str) -> str:
    try:
        return META_STRATEGY_PERSISTENCE_TABLE_BY_RECORD_ID[artifact_type]
    except KeyError as exc:
        raise ValueError(f"Unknown Meta-Strategy artifact type: {artifact_type}") from exc


def _normalize_payload(payload: Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        normalized = payload.model_dump(mode="json")
    elif is_dataclass(payload):
        normalized = asdict(payload)
    elif isinstance(payload, Mapping):
        normalized = dict(payload)
    else:
        raise TypeError("Meta-Strategy repository payload must be a mapping, dataclass, or pydantic model")
    algorithm_id = _first_value(normalized, "algorithmId", "algorithm_id")
    if algorithm_id != ALGORITHM_ID:
        raise MetaStrategyRepositoryAttributionError("Meta-Strategy repository payloads must carry algorithm_id='meta_strategy'")
    return _jsonable(normalized)


def _metadata(payload: Mapping[str, Any]) -> dict[str, str]:
    return {
        "algorithm_version": _string_value(payload, META_STRATEGY_ALGORITHM_VERSION, "algorithmVersion", "algorithm_version"),
        "configuration_version": _string_value(payload, META_STRATEGY_CONFIGURATION_VERSION, "configurationVersion", "configuration_version", "settingsVersion", "settings_version"),
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
        "decision_id": _string_value(payload, "unknown-decision", "decisionId", "decision_id", "id"),
        "snapshot_id": _string_value(payload, "unknown-snapshot", "snapshotId", "snapshot_id", "marketSnapshotId", "market_snapshot_id"),
        "order_intent_id": _string_value(payload, "", "orderIntentId", "order_intent_id"),
        "trade_id": _string_value(payload, "", "tradeId", "trade_id"),
        "run_id": _string_value(payload, "", "runId", "run_id"),
        "artifact_id": _string_value(payload, "", "artifactId", "artifact_id"),
    }


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


def _row_to_record(table: str, artifact_type: str, row: sqlite3.Row | None) -> MetaStrategyRepositoryRecord | None:
    if row is None:
        return None
    return MetaStrategyRepositoryRecord(
        table_name=table,
        record_id=str(row["record_id"]),
        artifact_type=artifact_type,
        algorithm_id=str(row["algorithm_id"]),
        decision_id=str(row["decision_id"]),
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
    "META_STRATEGY_PERSISTENCE_MIGRATION_VERSION",
    "META_STRATEGY_PERSISTENCE_RECORD_IDS",
    "META_STRATEGY_PERSISTENCE_RECORD_INVENTORY",
    "META_STRATEGY_PERSISTENCE_TABLES",
    "META_STRATEGY_REQUIRED_ATTRIBUTION_COLUMNS",
    "META_STRATEGY_VERSION_COLUMNS",
    "MetaStrategyPersistenceRecordDefinition",
    "MetaStrategyPersistenceSummary",
    "MetaStrategyRepositoryAttributionError",
    "MetaStrategyRepositoryPersistenceAdapter",
    "MetaStrategyRepositoryRecord",
    "MetaStrategySqliteRepository",
    "apply_meta_strategy_persistence_migrations",
    "migrate_meta_strategy_sqlite_database",
]
