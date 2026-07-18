"""Durable Regime persistence schema and recorder."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from backend.app.config import get_settings
from backend.app.database import _sqlite_path

REGIME_PERSISTENCE_MIGRATION_VERSION = "regime_persistence_phase14_002"
REGIME_ALGORITHM_ID = "regime"
REGIME_OWNED_TABLES = (
    "regime_decisions",
    "regime_classifications",
    "regime_transitions",
    "regime_strategy_outputs",
    "regime_context_outputs",
    "regime_safety_results",
    "regime_family_scores",
    "regime_effective_profiles",
    "regime_order_intents",
    "regime_backtest_runs",
    "regime_backtest_trades",
    "regime_ml_predictions",
    "regime_ml_artifacts",
)
REGIME_SHARED_ATTRIBUTED_TABLES = (
    "global_gate_evaluations",
    "risk_reservations",
    "broker_orders",
    "fills",
    "positions",
)
REGIME_SHARED_ATTRIBUTION_COLUMNS = (
    "algorithm_id",
    "decision_id",
    "order_intent_id",
    "position_id",
    "trade_id",
    "settings_version",
    "algorithm_version",
)
REGIME_VERSION_COLUMNS = ("algorithm_version", "settings_version", "strategy_version", "profile_version")
REGIME_PERSISTENCE_TABLES = REGIME_OWNED_TABLES + REGIME_SHARED_ATTRIBUTED_TABLES
SECRET_KEY_PARTS = ("secret", "api_key", "apikey", "token", "password", "authorization", "alpaca_key")


def migrate_regime_sqlite_database(path: str | Path) -> None:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for table in REGIME_PERSISTENCE_TABLES:
            conn.execute(_table_ddl(table))
            _ensure_regime_columns(conn, table)
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_decision ON {table}(decision_id)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_symbol_time ON {table}(symbol, timestamp)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_order ON {table}(order_id)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_order_intent ON {table}(order_intent_id)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_position ON {table}(position_id)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_trade ON {table}(trade_id)")
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
            (REGIME_PERSISTENCE_MIGRATION_VERSION,),
        )


def _table_ddl(table: str) -> str:
    return f"""
        CREATE TABLE IF NOT EXISTS {table} (
            record_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            settings_version TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            profile_version TEXT NOT NULL,
            model_version TEXT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            data_timestamp TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            order_id TEXT,
            order_intent_id TEXT,
            position_id TEXT,
            trade_id TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """


def _ensure_regime_columns(conn: sqlite3.Connection, table: str) -> None:
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    required = {
        "record_id": "TEXT",
        "algorithm_id": "TEXT NOT NULL DEFAULT 'regime'",
        "algorithm_version": "TEXT NOT NULL DEFAULT 'regime_algorithm_v2'",
        "settings_version": "TEXT NOT NULL DEFAULT 'regime_base_settings_v1'",
        "strategy_version": "TEXT NOT NULL DEFAULT 'regime_strategy_catalog_v2'",
        "profile_version": "TEXT NOT NULL DEFAULT 'regime_profile_matrix_v1'",
        "model_version": "TEXT",
        "timestamp": "TEXT NOT NULL DEFAULT ''",
        "symbol": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "data_timestamp": "TEXT NOT NULL DEFAULT ''",
        "decision_id": "TEXT NOT NULL DEFAULT ''",
        "order_id": "TEXT",
        "order_intent_id": "TEXT",
        "position_id": "TEXT",
        "trade_id": "TEXT",
        "payload_json": "TEXT NOT NULL DEFAULT '{}'",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    }
    for column, ddl in required.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


class RegimeSqliteRepository:
    def __init__(self, database_url: str | None = None) -> None:
        self.path = _sqlite_path(database_url or get_settings().database_url)
        migrate_regime_sqlite_database(self.path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def record_decision_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        regime = _record(snapshot.get("regime")) or _record(snapshot.get("regimeDecisionSnapshot"))
        if not regime:
            return {"recorded": False, "reason": "no_regime_snapshot"}

        decision_snapshot = _record(regime.get("decisionSnapshot")) or regime
        common = _common_metadata(snapshot, regime, decision_snapshot)
        counts = {table: 0 for table in REGIME_PERSISTENCE_TABLES}
        with self.connect() as conn:
            self._insert(conn, "regime_decisions", common, "decision", snapshot)
            counts["regime_decisions"] += 1
            self._insert(conn, "regime_classifications", common, "classification", _first_record(regime, decision_snapshot, "rawClassification", "rawRuleRegime"))
            counts["regime_classifications"] += 1
            self._insert(conn, "regime_transitions", common, "transition", _first_record(regime, decision_snapshot, "confirmedState", "hysteresisState"))
            counts["regime_transitions"] += 1
            for index, output in enumerate(_list(regime.get("selectedStrategies") or decision_snapshot.get("selectedStrategies"))):
                self._insert(conn, "regime_strategy_outputs", common, f"strategy-{index}", output)
                counts["regime_strategy_outputs"] += 1
            for index, output in enumerate(_list(regime.get("skippedStrategies") or decision_snapshot.get("skippedStrategies"))):
                self._insert(conn, "regime_strategy_outputs", common, f"skipped-strategy-{index}", {"skipped": True, **_record(output)})
                counts["regime_strategy_outputs"] += 1
            for index, output in enumerate(_list(regime.get("contextResults") or decision_snapshot.get("contextResults"))):
                self._insert(conn, "regime_context_outputs", common, f"context-{index}", output)
                counts["regime_context_outputs"] += 1
            for index, output in enumerate(_list(regime.get("safetyResults") or decision_snapshot.get("safetyResults"))):
                self._insert(conn, "regime_safety_results", common, f"safety-{index}", output)
                counts["regime_safety_results"] += 1
            for index, score in enumerate(_list(regime.get("familyAggregation") or decision_snapshot.get("familyAggregation") or decision_snapshot.get("familyScores"))):
                self._insert(conn, "regime_family_scores", common, f"family-{index}", score)
                counts["regime_family_scores"] += 1
            effective = _record(regime.get("effectiveSettings") or decision_snapshot.get("effectiveSettings"))
            if effective:
                self._insert(conn, "regime_effective_profiles", common, "effective-profile", effective)
                counts["regime_effective_profiles"] += 1
            order_intent = _record(regime.get("orderIntent") or (_record(regime.get("targetOrder")).get("orderIntent")))
            if order_intent:
                self._insert(conn, "regime_order_intents", {**common, "order_id": str(order_intent.get("idempotencyKey") or common["order_id"] or "")}, "order-intent", order_intent)
                counts["regime_order_intents"] += 1
            gate = _record(regime.get("globalGateOutcome") or decision_snapshot.get("globalGateOutcome"))
            if gate:
                self._insert(conn, "global_gate_evaluations", common, "global-gate", gate)
                counts["global_gate_evaluations"] += 1
            broker = _record(regime.get("brokerReconciliationResult") or decision_snapshot.get("brokerReconciliationResult"))
            if broker:
                self._insert(conn, "broker_orders", common, "broker-reconciliation", broker)
                counts["broker_orders"] += 1
            ml = _record(regime.get("ml"))
            prediction = _record(regime.get("mlProbabilities")) or _record(decision_snapshot.get("mlProbabilityVector")) or _record(ml.get("prediction"))
            if prediction:
                self._insert(conn, "regime_ml_predictions", common, "ml-prediction", prediction)
                counts["regime_ml_predictions"] += 1
        return {"recorded": True, "decisionId": common["decision_id"], "tableCounts": counts}

    def record_backtest_result(self, result: dict[str, Any]) -> dict[str, Any]:
        common = _common_metadata({}, result, result)
        run_id = str(result.get("cacheKey") or result.get("runId") or result.get("storageKey") or common["decision_id"])
        with self.connect() as conn:
            self._insert(conn, "regime_backtest_runs", {**common, "decision_id": run_id}, "backtest-run", result)
            trade_count = 0
            for index, trade in enumerate(_list(result.get("trades"))):
                order_id = str(_record(trade).get("tradeId") or _record(trade).get("trade_id") or common["order_id"] or "")
                self._insert(conn, "regime_backtest_trades", {**common, "decision_id": run_id, "order_id": order_id}, f"backtest-trade-{index}", trade)
                trade_count += 1
            return {"recorded": True, "runId": run_id, "tradeCount": trade_count}

    def table_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            return {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in REGIME_PERSISTENCE_TABLES}

    def table_columns(self, table: str) -> tuple[str, ...]:
        if table not in REGIME_PERSISTENCE_TABLES:
            raise ValueError(f"Unknown Regime persistence table: {table}")
        with self.connect() as conn:
            return tuple(str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall())

    def persistence_inventory(self) -> dict[str, Any]:
        table_columns = {table: self.table_columns(table) for table in REGIME_PERSISTENCE_TABLES}
        missing_shared_columns = {
            table: tuple(column for column in REGIME_SHARED_ATTRIBUTION_COLUMNS if column not in table_columns[table])
            for table in REGIME_SHARED_ATTRIBUTED_TABLES
        }
        missing_owned_version_columns = {
            table: tuple(column for column in REGIME_VERSION_COLUMNS if column not in table_columns[table])
            for table in REGIME_OWNED_TABLES
        }
        return {
            "algorithmId": REGIME_ALGORITHM_ID,
            "ownedTables": REGIME_OWNED_TABLES,
            "sharedAttributedTables": REGIME_SHARED_ATTRIBUTED_TABLES,
            "requiredSharedAttributionColumns": REGIME_SHARED_ATTRIBUTION_COLUMNS,
            "ownedVersionColumns": REGIME_VERSION_COLUMNS,
            "missingSharedAttributionColumns": missing_shared_columns,
            "missingOwnedVersionColumns": missing_owned_version_columns,
            "passed": not any(missing_shared_columns.values()) and not any(missing_owned_version_columns.values()),
        }

    def _insert(self, conn: sqlite3.Connection, table: str, common: dict[str, str | None], suffix: str, payload: Any) -> None:
        attribution = _attribution_metadata(common, payload)
        payload_json = json.dumps(sanitize_persistence_payload(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        record_id = _record_id(table, common["decision_id"] or "", suffix, payload_json)
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {table} (
                record_id, algorithm_id, algorithm_version, settings_version, strategy_version,
                profile_version, model_version, timestamp, symbol, data_timestamp,
                decision_id, order_id, order_intent_id, position_id, trade_id, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                common["algorithm_id"],
                common["algorithm_version"],
                common["settings_version"],
                common["strategy_version"],
                common["profile_version"],
                common["model_version"],
                common["timestamp"],
                common["symbol"],
                common["data_timestamp"],
                common["decision_id"],
                common["order_id"],
                attribution["order_intent_id"],
                attribution["position_id"],
                attribution["trade_id"],
                payload_json,
            ),
        )


def sanitize_persistence_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, child in value.items():
            if any(part in str(key).lower().replace("-", "_") for part in SECRET_KEY_PARTS):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = sanitize_persistence_payload(child)
        return sanitized
    if isinstance(value, list):
        return [sanitize_persistence_payload(item) for item in value]
    return value


def _common_metadata(snapshot: dict[str, Any], regime: dict[str, Any], decision: dict[str, Any]) -> dict[str, str | None]:
    timestamp = str(regime.get("timestamp") or decision.get("decisionTimestamp") or snapshot.get("capturedAt") or "")
    data_timestamp = str(regime.get("dataTimestamp") or decision.get("dataTimestamp") or timestamp)
    symbol = str(regime.get("symbol") or decision.get("symbol") or snapshot.get("symbol") or "UNKNOWN").upper()
    decision_id = str(regime.get("decisionId") or decision.get("decisionId") or f"regime:{symbol}:{data_timestamp}")
    order_intent_id = _string_or_none(
        regime.get("orderIntentId")
        or regime.get("order_intent_id")
        or decision.get("orderIntentId")
        or decision.get("order_intent_id")
        or _record(regime.get("orderIntent")).get("orderIntentId")
        or _record(regime.get("orderIntent")).get("order_intent_id")
    )
    return {
        "algorithm_id": REGIME_ALGORITHM_ID,
        "algorithm_version": str(regime.get("algorithmVersion") or decision.get("algorithmVersion") or "regime_algorithm_v2"),
        "settings_version": str(regime.get("settingsVersion") or decision.get("settingsVersion") or "regime_base_settings_v1"),
        "strategy_version": str(regime.get("strategyVersion") or decision.get("strategyVersion") or "regime_strategy_catalog_v2"),
        "profile_version": str(regime.get("profileVersion") or decision.get("profileVersion") or "regime_profile_matrix_v1"),
        "model_version": None if (regime.get("modelVersion") or decision.get("modelVersion")) is None else str(regime.get("modelVersion") or decision.get("modelVersion")),
        "timestamp": timestamp,
        "symbol": symbol,
        "data_timestamp": data_timestamp,
        "decision_id": decision_id,
        "order_id": None if (regime.get("orderId") or decision.get("orderId")) is None else str(regime.get("orderId") or decision.get("orderId")),
        "order_intent_id": order_intent_id,
        "position_id": _string_or_none(regime.get("positionId") or regime.get("position_id") or decision.get("positionId") or decision.get("position_id")),
        "trade_id": _string_or_none(regime.get("tradeId") or regime.get("trade_id") or decision.get("tradeId") or decision.get("trade_id")),
    }


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_record(parent: dict[str, Any], fallback: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = parent.get(key)
        if isinstance(value, dict):
            return value
        if key in parent:
            return {key: value}
        value = fallback.get(key)
        if isinstance(value, dict):
            return value
        if key in fallback:
            return {key: value}
    return {}


def _record_id(table: str, decision_id: str, suffix: str, payload_json: str) -> str:
    digest = hashlib.sha256(f"{table}:{decision_id}:{suffix}:{payload_json}".encode("utf-8")).hexdigest()[:24]
    return f"{table}:{digest}"


def _attribution_metadata(common: dict[str, str | None], payload: Any) -> dict[str, str | None]:
    record = _record(payload)
    return {
        "order_intent_id": _string_or_none(
            record.get("orderIntentId")
            or record.get("order_intent_id")
            or record.get("idempotencyKey")
            or record.get("idempotency_key")
            or common.get("order_intent_id")
            or common.get("order_id")
        ),
        "position_id": _string_or_none(record.get("positionId") or record.get("position_id") or common.get("position_id")),
        "trade_id": _string_or_none(record.get("tradeId") or record.get("trade_id") or common.get("trade_id")),
    }


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


__all__ = [
    "REGIME_PERSISTENCE_MIGRATION_VERSION",
    "REGIME_OWNED_TABLES",
    "REGIME_PERSISTENCE_TABLES",
    "REGIME_SHARED_ATTRIBUTED_TABLES",
    "REGIME_SHARED_ATTRIBUTION_COLUMNS",
    "REGIME_VERSION_COLUMNS",
    "RegimeSqliteRepository",
    "migrate_regime_sqlite_database",
    "sanitize_persistence_payload",
]
