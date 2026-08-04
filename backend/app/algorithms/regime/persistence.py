"""Durable Regime persistence schema and recorder."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from backend.app.algorithms.regime.configuration import (
    REGIME_SETTINGS_AUTHORITATIVE_SOURCE,
    flatten_regime_trading_settings,
    regime_settings_identity_from_payload,
    regime_trading_settings_to_dict,
    validate_regime_trading_settings_snapshot,
)
from backend.app.algorithms.regime.runtime_idempotency import REGIME_RUNTIME_STAGES
from backend.app.config import get_settings
from backend.app.database import _sqlite_path

REGIME_PERSISTENCE_MIGRATION_VERSION = "regime_persistence_phase21_rollout_001"
REGIME_LEGACY_MIGRATION_VERSION = "regime_persistence_step2_003"
REGIME_ALGORITHM_ID = "regime"
REGIME_ML_TRUSTED_BACKEND_EVIDENCE_SOURCES = frozenset(
    {
        "backend_worker",
        "regime_backtest_worker",
        "regime_replay_worker",
        "regime_paper_stability_worker",
        "regime_ml_promotion_worker",
    }
)
REGIME_OWNED_TABLES = (
    "regime_settings_versions",
    "regime_active_settings",
    "regime_strategy_settings",
    "regime_runtime_instances",
    "regime_runtime_commands",
    "regime_runtime_events",
    "regime_runtime_checkpoints",
    "regime_hysteresis_state",
    "regime_daily_counters",
    "regime_strategy_performance",
    "regime_decisions",
    "regime_classifications",
    "regime_transitions",
    "regime_strategy_outputs",
    "regime_context_outputs",
    "regime_confirmation_outputs",
    "regime_safety_results",
    "regime_family_scores",
    "regime_effective_profiles",
    "regime_local_risk_results",
    "regime_order_intents",
    "regime_execution_outbox",
    "regime_orders",
    "regime_fills",
    "regime_hypothetical_fills",
    "regime_positions",
    "regime_trades",
    "regime_reconciliation_events",
    "regime_backtest_jobs",
    "regime_backtest_runs",
    "regime_backtest_trades",
    "regime_rollout_evidence",
    "regime_ml_predictions",
    "regime_ml_artifacts",
    "regime_runtime_state",
    "regime_bar_processing",
    "regime_inventory_events",
    "regime_inventory_snapshots",
    "regime_daily_risk_state",
    "regime_reconciliation_runs",
    "regime_runtime_alerts",
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
    "algorithm_instance_id",
    "account_id",
    "runtime_mode",
    "symbol",
    "decision_id",
    "order_intent_id",
    "broker_order_id",
    "position_id",
    "trade_id",
    "settings_version",
    "algorithm_version",
)
REGIME_VERSION_COLUMNS = ("algorithm_version", "settings_version", "strategy_version", "profile_version")
REGIME_OWNERSHIP_KEY_COLUMNS = ("algorithm_id", "algorithm_instance_id", "account_id", "runtime_mode", "symbol")
REGIME_PERSISTENCE_TABLES = REGIME_OWNED_TABLES + REGIME_SHARED_ATTRIBUTED_TABLES
REGIME_MUTABLE_STATE_TABLES = (
    "regime_active_settings",
    "regime_runtime_instances",
    "regime_runtime_commands",
    "regime_runtime_checkpoints",
    "regime_hysteresis_state",
    "regime_daily_counters",
    "regime_daily_risk_state",
    "regime_strategy_performance",
    "regime_positions",
    "regime_runtime_state",
    "regime_inventory_snapshots",
)
REGIME_PROCESSING_STATUS_TABLES = (
    "regime_runtime_commands",
    "regime_runtime_events",
    "regime_bar_processing",
    "regime_execution_outbox",
    "regime_orders",
    "regime_fills",
    "regime_hypothetical_fills",
    "regime_inventory_events",
    "regime_reconciliation_events",
    "regime_reconciliation_runs",
    "regime_backtest_jobs",
    "regime_runtime_alerts",
)
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
            _ensure_regime_indexes(conn, table)
        conn.execute("DROP INDEX IF EXISTS idx_regime_order_intents_unique_intent")
        _create_unique_index_if_possible(conn,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_order_intents_unique_intent
            ON regime_order_intents(algorithm_instance_id, account_id, runtime_mode, symbol, order_intent_id)
            WHERE order_intent_id IS NOT NULL AND order_intent_id <> ''
            """
        )
        _create_unique_index_if_possible(conn,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_decisions_unique_decision
            ON regime_decisions(algorithm_instance_id, account_id, runtime_mode, symbol, decision_id)
            WHERE decision_id IS NOT NULL AND decision_id <> ''
            """
        )
        _create_unique_index_if_possible(conn,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_decisions_unique_finalized_bar
            ON regime_decisions(algorithm_instance_id, account_id, runtime_mode, symbol, data_timestamp, algorithm_version, settings_version)
            WHERE data_timestamp IS NOT NULL AND data_timestamp <> ''
            """
        )
        _create_unique_index_if_possible(conn,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_order_intents_unique_entry_bar
            ON regime_order_intents(algorithm_instance_id, account_id, runtime_mode, symbol, data_timestamp, algorithm_version, settings_version)
            WHERE order_intent_id LIKE 'regime-intent-%'
              AND data_timestamp IS NOT NULL
              AND data_timestamp <> ''
            """
        )
        _create_unique_index_if_possible(conn,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_local_risk_results_unique_result
            ON regime_local_risk_results(algorithm_instance_id, account_id, runtime_mode, symbol, decision_id, record_id)
            WHERE decision_id IS NOT NULL AND decision_id <> ''
            """
        )
        _create_unique_index_if_possible(conn,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_execution_outbox_unique_status
            ON regime_execution_outbox(algorithm_instance_id, account_id, runtime_mode, symbol, order_intent_id, processing_status)
            WHERE order_intent_id IS NOT NULL AND order_intent_id <> ''
            """
        )
        _create_unique_index_if_possible(conn,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_orders_unique_order_status
            ON regime_orders(algorithm_instance_id, account_id, runtime_mode, symbol, order_id, processing_status)
            WHERE order_id IS NOT NULL AND order_id <> ''
            """
        )
        _create_unique_index_if_possible(conn,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_fills_unique_fill_record
            ON regime_fills(algorithm_instance_id, account_id, runtime_mode, symbol, broker_order_id, trade_id, processing_status)
            WHERE broker_order_id IS NOT NULL AND broker_order_id <> ''
            """
        )
        _create_unique_index_if_possible(conn,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_positions_unique_position_version
            ON regime_positions(algorithm_instance_id, account_id, runtime_mode, symbol, position_id, sequence_version)
            WHERE position_id IS NOT NULL AND position_id <> ''
            """
        )
        _create_unique_index_if_possible(conn,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_trades_unique_trade_version
            ON regime_trades(algorithm_instance_id, account_id, runtime_mode, symbol, trade_id, sequence_version)
            WHERE trade_id IS NOT NULL AND trade_id <> ''
            """
        )
        _create_unique_index_if_possible(conn,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_backtest_jobs_unique_status
            ON regime_backtest_jobs(algorithm_instance_id, account_id, runtime_mode, symbol, decision_id, processing_status, record_id)
            WHERE decision_id IS NOT NULL AND decision_id <> ''
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_regime_runtime_events_event_status
            ON regime_runtime_events(decision_id, processing_status)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_regime_runtime_checkpoints_event_status
            ON regime_runtime_checkpoints(decision_id, processing_status)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_regime_inventory_events_fill_trace
            ON regime_inventory_events(algorithm_instance_id, account_id, runtime_mode, symbol, broker_order_id, trade_id, order_intent_id, processing_status)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_regime_inventory_snapshots_current
            ON regime_inventory_snapshots(algorithm_instance_id, account_id, runtime_mode, symbol, sequence_version)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_regime_reconciliation_runs_status
            ON regime_reconciliation_runs(algorithm_instance_id, account_id, runtime_mode, symbol, processing_status, event_timestamp)
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
            (REGIME_LEGACY_MIGRATION_VERSION,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
            (REGIME_PERSISTENCE_MIGRATION_VERSION,),
        )


def _table_ddl(table: str) -> str:
    return f"""
        CREATE TABLE IF NOT EXISTS {table} (
            record_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL CHECK (algorithm_id = 'regime'),
            algorithm_instance_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            runtime_mode TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            settings_version TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            profile_version TEXT NOT NULL,
            model_version TEXT,
            timestamp TEXT NOT NULL,
            event_timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            data_timestamp TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            order_id TEXT,
            order_intent_id TEXT,
            broker_order_id TEXT,
            position_id TEXT,
            trade_id TEXT,
            processing_status TEXT NOT NULL,
            sequence_version INTEGER NOT NULL DEFAULT 1,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """


def _ensure_regime_columns(conn: sqlite3.Connection, table: str) -> None:
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    required = {
        "record_id": "TEXT",
        "algorithm_id": "TEXT NOT NULL DEFAULT 'regime'",
        "algorithm_instance_id": "TEXT NOT NULL DEFAULT 'regime-default'",
        "account_id": "TEXT NOT NULL DEFAULT 'default'",
        "runtime_mode": "TEXT NOT NULL DEFAULT 'shadow'",
        "algorithm_version": "TEXT NOT NULL DEFAULT 'regime_algorithm_v2'",
        "settings_version": "TEXT NOT NULL DEFAULT 'regime_base_settings_v1'",
        "strategy_version": "TEXT NOT NULL DEFAULT 'regime_strategy_catalog_v2'",
        "profile_version": "TEXT NOT NULL DEFAULT 'regime_profile_matrix_v1'",
        "model_version": "TEXT",
        "timestamp": "TEXT NOT NULL DEFAULT ''",
        "event_timestamp": "TEXT NOT NULL DEFAULT ''",
        "symbol": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "data_timestamp": "TEXT NOT NULL DEFAULT ''",
        "decision_id": "TEXT NOT NULL DEFAULT ''",
        "order_id": "TEXT",
        "order_intent_id": "TEXT",
        "broker_order_id": "TEXT",
        "position_id": "TEXT",
        "trade_id": "TEXT",
        "processing_status": "TEXT NOT NULL DEFAULT 'recorded'",
        "sequence_version": "INTEGER NOT NULL DEFAULT 1",
        "payload_json": "TEXT NOT NULL DEFAULT '{}'",
        "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    }
    for column, ddl in required.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _ensure_regime_indexes(conn: sqlite3.Connection, table: str) -> None:
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_instance_symbol ON {table}(algorithm_instance_id, symbol)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_event_timestamp ON {table}(event_timestamp)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_decision ON {table}(decision_id)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_symbol_time ON {table}(symbol, timestamp)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_order ON {table}(order_id)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_order_intent ON {table}(order_intent_id)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_broker_order ON {table}(broker_order_id)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_position ON {table}(position_id)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_trade ON {table}(trade_id)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_settings_version ON {table}(settings_version)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_processing_status ON {table}(processing_status)")


def _create_unique_index_if_possible(conn: sqlite3.Connection, ddl: str) -> None:
    try:
        conn.execute(ddl)
    except sqlite3.IntegrityError:
        # Existing deployments may already contain duplicate historical rows.
        # Fresh databases still receive the unique constraint, while migrations
        # preserve old records instead of failing startup.
        return


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
        regime = _record(snapshot.get("regime")) or _record(snapshot.get("regimeDecisionSnapshot")) or _backend_result_as_regime(snapshot)
        if not regime:
            return {"recorded": False, "reason": "no_regime_snapshot"}

        decision_snapshot = _record(regime.get("decisionSnapshot")) or regime
        common = _common_metadata(snapshot, regime, decision_snapshot)
        _validate_common_metadata(common)
        counts = {table: 0 for table in REGIME_PERSISTENCE_TABLES}
        with self.connect() as conn:
            duplicate = conn.execute(
                """
                SELECT record_id
                FROM regime_decisions
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND decision_id = ?
                LIMIT 1
                """,
                (
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                    common["decision_id"],
                ),
            ).fetchone()
            if duplicate:
                return {"recorded": False, "reason": "duplicate_decision", "decisionId": common["decision_id"], "tableCounts": counts}
            duplicate_bar = conn.execute(
                """
                SELECT decision_id
                FROM regime_decisions
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND data_timestamp = ?
                  AND algorithm_version = ?
                  AND settings_version = ?
                LIMIT 1
                """,
                (
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                    common["data_timestamp"],
                    common["algorithm_version"],
                    common["settings_version"],
                ),
            ).fetchone()
            if duplicate_bar:
                return {
                    "recorded": False,
                    "reason": "duplicate_finalized_bar_decision",
                    "decisionId": str(duplicate_bar["decision_id"]),
                    "tableCounts": counts,
                }
            self._insert(conn, "regime_decisions", common, "decision", snapshot)
            counts["regime_decisions"] += 1
            self._insert(conn, "regime_classifications", common, "classification", _first_record(regime, decision_snapshot, "rawClassification", "rawRuleRegime"))
            counts["regime_classifications"] += 1
            transition = _first_record(regime, decision_snapshot, "confirmedState", "hysteresisState")
            self._insert(conn, "regime_transitions", common, "transition", transition)
            counts["regime_transitions"] += 1
            if transition:
                self._insert(conn, "regime_hysteresis_state", common, "hysteresis-state", transition, sequence_version=_sequence_version(transition))
                counts["regime_hysteresis_state"] += 1
            for index, output in enumerate(_list(regime.get("selectedStrategies") or decision_snapshot.get("selectedStrategies"))):
                self._insert(conn, "regime_strategy_outputs", common, f"strategy-{index}", output)
                counts["regime_strategy_outputs"] += 1
            for index, output in enumerate(_list(regime.get("skippedStrategies") or decision_snapshot.get("skippedStrategies"))):
                self._insert(conn, "regime_strategy_outputs", common, f"skipped-strategy-{index}", {"skipped": True, **_record(output)})
                counts["regime_strategy_outputs"] += 1
            for index, output in enumerate(_list(regime.get("strategyOutputs") or decision_snapshot.get("strategyOutputs"))):
                row = _record(output)
                role = str(row.get("role") or "")
                if role == "confirmation":
                    self._insert(conn, "regime_confirmation_outputs", common, f"confirmation-{index}", row)
                    counts["regime_confirmation_outputs"] += 1
                elif role == "regime_context":
                    self._insert(conn, "regime_context_outputs", common, f"context-strategy-{index}", row)
                    counts["regime_context_outputs"] += 1
                elif role == "safety_gate":
                    self._insert(conn, "regime_safety_results", common, f"safety-strategy-{index}", row)
                    counts["regime_safety_results"] += 1
                else:
                    self._insert(conn, "regime_strategy_outputs", common, f"strategy-output-{index}", row)
                    counts["regime_strategy_outputs"] += 1
            for index, output in enumerate(_list(regime.get("contextResults") or decision_snapshot.get("contextResults"))):
                self._insert(conn, "regime_context_outputs", common, f"context-{index}", output)
                counts["regime_context_outputs"] += 1
            for index, output in enumerate(_list(regime.get("confirmationResults") or decision_snapshot.get("confirmationResults"))):
                self._insert(conn, "regime_confirmation_outputs", common, f"confirmation-result-{index}", output)
                counts["regime_confirmation_outputs"] += 1
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
            local_risk = (
                _record(regime.get("localRiskResult"))
                or _record(regime.get("localRiskResults"))
                or _record(decision_snapshot.get("localRiskResult"))
                or _record(decision_snapshot.get("localRiskResults"))
                or _record(snapshot.get("orderValidation"))
            )
            if not local_risk and (regime.get("tradeBlockers") or decision_snapshot.get("tradeBlockers")):
                local_risk = {"tradeBlockers": _list(regime.get("tradeBlockers") or decision_snapshot.get("tradeBlockers"))}
            if local_risk:
                local_risk_id = _string_or_none(local_risk.get("localRiskResultId") or local_risk.get("local_risk_result_id")) or _stable_id("regime-local-risk", common, local_risk)
                self._insert(
                    conn,
                    "regime_local_risk_results",
                    common,
                    local_risk_id,
                    {**local_risk, "localRiskResultId": local_risk_id},
                )
                counts["regime_local_risk_results"] += 1
            order_intent = _record(regime.get("orderIntent") or (_record(regime.get("targetOrder")).get("orderIntent")))
            if order_intent:
                intent_common = {**common, "order_id": str(order_intent.get("idempotencyKey") or common["order_id"] or "")}
                self._insert(conn, "regime_order_intents", intent_common, "order-intent", order_intent)
                counts["regime_order_intents"] += 1
                gate = _record(regime.get("globalGateOutcome") or decision_snapshot.get("globalGateOutcome"))
                if gate:
                    self._insert(conn, "global_gate_evaluations", common, "global-gate", gate)
                    counts["global_gate_evaluations"] += 1
                self._insert_execution_outbox_in_transaction(conn, intent_common, {**order_intent, "globalRiskApproval": gate})
                counts["regime_execution_outbox"] += 1
            else:
                gate = _record(regime.get("globalGateOutcome") or decision_snapshot.get("globalGateOutcome"))
                if gate:
                    self._insert(conn, "global_gate_evaluations", common, "global-gate", gate)
                    counts["global_gate_evaluations"] += 1
            for index, hypothetical in enumerate(_list(regime.get("hypotheticalFills") or decision_snapshot.get("hypotheticalFills"))):
                self._insert(conn, "regime_hypothetical_fills", common, f"hypothetical-fill-{index}", hypothetical, processing_status="hypothetical")
                counts["regime_hypothetical_fills"] += 1
            broker = _record(regime.get("brokerReconciliationResult") or decision_snapshot.get("brokerReconciliationResult"))
            if broker:
                self._insert(conn, "broker_orders", common, "broker-reconciliation", broker)
                counts["broker_orders"] += 1
                self._copy_broker_observation_in_transaction(conn, broker, common)
                for table in ("regime_orders", "regime_fills", "regime_positions", "regime_trades", "regime_reconciliation_events"):
                    counts[table] += int(self._latest_inserted_table == table)
            ml = _record(regime.get("ml"))
            prediction = _record(regime.get("mlProbabilities")) or _record(decision_snapshot.get("mlProbabilityVector")) or _record(ml.get("prediction"))
            if prediction:
                self._insert(conn, "regime_ml_predictions", common, "ml-prediction", prediction)
                counts["regime_ml_predictions"] += 1
            next_state = _record(snapshot.get("nextRuntimeState") or regime.get("nextRuntimeState") or decision_snapshot.get("nextRuntimeState"))
            if next_state:
                self._insert(conn, "regime_runtime_checkpoints", common, "runtime-state", next_state, sequence_version=_sequence_version(next_state))
                self._insert(conn, "regime_runtime_state", common, "runtime-state", next_state, sequence_version=_sequence_version(next_state))
                counts["regime_runtime_checkpoints"] += 1
                counts["regime_runtime_state"] += 1
        return {"recorded": True, "decisionId": common["decision_id"], "tableCounts": counts}

    def record_stateful_bar_result(self, result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result.get("nextRuntimeState"), dict):
            raise ValueError("Regime stateful bar result requires nextRuntimeState")
        if not isinstance(result.get("decision"), dict):
            raise ValueError("Regime stateful bar result requires decision")
        return self.record_decision_snapshot(result)

    def record_backtest_result(self, result: dict[str, Any]) -> dict[str, Any]:
        common = {**_common_metadata({}, result, result), "runtime_mode": "backtest"}
        _validate_common_metadata(common)
        run_id = str(result.get("cacheKey") or result.get("runId") or result.get("storageKey") or common["decision_id"])
        with self.connect() as conn:
            self._insert(conn, "regime_backtest_jobs", {**common, "decision_id": run_id}, "backtest-job", {"processingStatus": "completed", "resultRef": run_id}, processing_status="completed")
            self._insert(conn, "regime_backtest_runs", {**common, "decision_id": run_id}, "backtest-run", result)
            trade_count = 0
            for index, trade in enumerate(_list(result.get("trades"))):
                order_id = str(_record(trade).get("tradeId") or _record(trade).get("trade_id") or common["order_id"] or "")
                trade_id = _stable_regime_trade_id(_record(trade), run_id, index)
                self._insert(conn, "regime_backtest_trades", {**common, "decision_id": run_id, "order_id": order_id, "trade_id": trade_id}, f"backtest-trade-{index}", {**_record(trade), "tradeId": trade_id})
                trade_count += 1
            return {"recorded": True, "runId": run_id, "tradeCount": trade_count}

    def enqueue_backtest_job(self, job: dict[str, Any]) -> dict[str, Any]:
        common = _backtest_job_common(job)
        payload = {
            **job,
            "algorithmId": REGIME_ALGORITHM_ID,
            "runtimeMode": "backtest",
            "status": str(job.get("status") or "queued"),
            "progress": float(job.get("progress") or 0.0),
            "heartbeatAt": str(job.get("heartbeatAt") or _utc_now()),
        }
        with self.connect() as conn:
            self._insert(conn, "regime_backtest_jobs", common, f"backtest-job-{payload['status']}", payload, processing_status=payload["status"])
        return {"recorded": True, "jobId": common["decision_id"], "status": payload["status"]}

    def update_backtest_job_status(
        self,
        job_id: str,
        *,
        status: str,
        details: dict[str, Any] | None = None,
        progress: float | None = None,
        failure_message: str | None = None,
        reason_codes: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        existing = self.read_backtest_job(job_id)
        base = existing if existing is not None else {"jobId": job_id, "algorithmId": REGIME_ALGORITHM_ID, "runtimeMode": "backtest", "symbol": "SPY"}
        payload = {
            **base,
            **(details or {}),
            "algorithmId": REGIME_ALGORITHM_ID,
            "runtimeMode": "backtest",
            "jobId": job_id,
            "status": status,
            "progress": float(progress if progress is not None else base.get("progress") or 0.0),
            "failureMessage": failure_message,
            "reasonCodes": list(reason_codes or base.get("reasonCodes") or ()),
            "heartbeatAt": _utc_now(),
        }
        common = _backtest_job_common(payload)
        with self.connect() as conn:
            self._insert(conn, "regime_backtest_jobs", common, f"backtest-job-{status}-{_stable_snapshot_key(payload['heartbeatAt'])}", payload, processing_status=status)
        return {"recorded": True, "jobId": job_id, "status": status}

    def read_backtest_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json, processing_status, created_at, sequence_version
                FROM regime_backtest_jobs
                WHERE algorithm_id = 'regime'
                  AND runtime_mode = 'backtest'
                  AND decision_id = ?
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"]))
        if isinstance(payload, dict):
            payload["status"] = str(row["processing_status"])
            payload["processingStatus"] = str(row["processing_status"])
            payload["sequenceVersion"] = int(row["sequence_version"])
            payload["updatedAt"] = str(row["created_at"])
            return payload
        return {"jobId": job_id, "status": str(row["processing_status"]), "payload": payload}

    def claim_next_backtest_job(self, owner_id: str, *, max_running: int = 1) -> dict[str, Any] | None:
        with self.connect() as conn:
            running = conn.execute(
                """
                SELECT COUNT(1) AS count
                FROM regime_backtest_jobs latest
                WHERE latest.algorithm_id = 'regime'
                  AND latest.runtime_mode = 'backtest'
                  AND latest.processing_status = 'running'
                  AND latest.rowid IN (
                      SELECT MAX(rowid)
                      FROM regime_backtest_jobs
                      WHERE algorithm_id = 'regime' AND runtime_mode = 'backtest'
                      GROUP BY decision_id
                  )
                """
            ).fetchone()
            if int(running["count"] if running else 0) >= max_running:
                return None
            row = conn.execute(
                """
                SELECT payload_json, decision_id
                FROM regime_backtest_jobs
                WHERE algorithm_id = 'regime'
                  AND runtime_mode = 'backtest'
                  AND processing_status = 'queued'
                  AND rowid IN (
                      SELECT MAX(rowid)
                      FROM regime_backtest_jobs
                      WHERE algorithm_id = 'regime' AND runtime_mode = 'backtest'
                      GROUP BY decision_id
                  )
                ORDER BY created_at ASC, rowid ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict):
                payload = {"payload": payload}
            payload["jobId"] = str(row["decision_id"])
            common = _backtest_job_common({**payload, "status": "running"})
            claimed = {**payload, "status": "running", "workerOwner": owner_id, "heartbeatAt": _utc_now(), "progress": max(5.0, float(payload.get("progress") or 0.0))}
            self._insert(conn, "regime_backtest_jobs", common, f"backtest-job-running-{owner_id}", claimed, processing_status="running")
            return claimed

    def recover_abandoned_backtest_jobs(self, *, owner_id: str, stale_after_seconds: int = 120) -> dict[str, Any]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
        recovered: list[str] = []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT decision_id, payload_json
                FROM regime_backtest_jobs
                WHERE algorithm_id = 'regime'
                  AND runtime_mode = 'backtest'
                  AND processing_status = 'running'
                  AND rowid IN (
                      SELECT MAX(rowid)
                      FROM regime_backtest_jobs
                      WHERE algorithm_id = 'regime' AND runtime_mode = 'backtest'
                      GROUP BY decision_id
                  )
                """
            ).fetchall()
            for row in rows:
                payload = json.loads(str(row["payload_json"]))
                if not isinstance(payload, dict):
                    continue
                heartbeat = _parse_utc(payload.get("heartbeatAt"))
                if heartbeat is not None and heartbeat >= cutoff:
                    continue
                job_id = str(row["decision_id"])
                recovered.append(job_id)
                updated = {**payload, "jobId": job_id, "status": "queued", "recoveredFromOwner": payload.get("workerOwner"), "workerOwner": owner_id, "heartbeatAt": _utc_now(), "reasonCodes": ["regime.backtest.job.recovered_abandoned"]}
                self._insert(conn, "regime_backtest_jobs", _backtest_job_common(updated), f"backtest-job-recovered-{owner_id}", updated, processing_status="queued")
        return {"algorithmId": REGIME_ALGORITHM_ID, "recoveredJobIds": recovered, "recoveredCount": len(recovered)}

    def record_regime_ml_promotion_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        artifact_id = str(evidence.get("artifact_id") or evidence.get("artifactId") or "")
        if not artifact_id:
            return {"recorded": False, "reason": "missing_artifact_id"}
        trusted, reason = _validate_regime_ml_backend_evidence_source(evidence)
        if not trusted:
            return {"recorded": False, "reason": reason, "artifactId": artifact_id}
        payload = {
            **evidence,
            "trusted_backend_record": True,
            "backend_evidence_source": str(evidence.get("backend_evidence_source") or evidence.get("backendEvidenceSource") or evidence.get("source") or ""),
            "authority": "regime_backend_recorded_ml_evidence",
            "maximumAutomaticPromotionMode": "confirm_only",
            "mayCreateDirection": False,
            "mayIncreaseQuantity": False,
            "mayLoosenGate": False,
        }
        common = _common_metadata(
            {},
            {
                "symbol": "REGIME_ML",
                "decisionId": f"regime-ml-promotion:{artifact_id}",
                "modelVersion": payload.get("model_version") or payload.get("modelVersion"),
                "timestamp": payload.get("evidence_generated_at") or payload.get("evidenceGeneratedAt") or "",
                "algorithmVersion": payload.get("deterministic_baseline_version") or payload.get("deterministicBaselineVersion") or "regime_algorithm_v3_backend_authoritative",
            },
            {},
        )
        with self.connect() as conn:
            self._insert(conn, "regime_ml_artifacts", common, f"promotion-evidence-{artifact_id}", payload)
            self._insert(conn, "regime_rollout_evidence", common, f"promotion-evidence-{artifact_id}", payload)
        return {"recorded": True, "artifactId": artifact_id}

    def record_regime_rollout_promotion_evidence(self, identity: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
        source = str(evidence.get("backendEvidenceSource") or evidence.get("backend_evidence_source") or evidence.get("source") or "")
        if source not in {
            "regime_backend_rollout_worker",
            "regime_replay_worker",
            "regime_backtest_worker",
            "regime_paper_stability_worker",
            "regime_runtime_supervisor",
        }:
            return {"recorded": False, "reason": "regime.rollout.frontend_or_untrusted_evidence_rejected"}
        evidence_id = str(evidence.get("evidenceId") or evidence.get("evidence_id") or evidence.get("artifactId") or evidence.get("artifact_id") or "")
        if not evidence_id:
            evidence_id = _stable_snapshot_key(json.dumps(evidence, sort_keys=True, default=str))
        payload = {
            **evidence,
            "algorithmId": REGIME_ALGORITHM_ID,
            "evidenceId": evidence_id,
            "trustedBackendRecord": True,
            "backendEvidenceSource": source,
            "liveTradingEnabled": False,
            "recordedAt": evidence.get("recordedAt") or _utc_now(),
        }
        common = _common_metadata(
            {},
            {
                **identity,
                **payload,
                "decisionId": f"regime-rollout-evidence:{evidence_id}",
                "timestamp": payload["recordedAt"],
            },
            {},
        )
        _validate_common_metadata(common)
        with self.connect() as conn:
            self._insert(conn, "regime_rollout_evidence", common, f"paper-promotion-evidence-{evidence_id}", payload)
        return {"recorded": True, "evidenceId": evidence_id}

    def read_regime_rollout_promotion_evidence(self, identity: dict[str, Any]) -> dict[str, Any]:
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM regime_rollout_evidence
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                ORDER BY rowid ASC
                """,
                (
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                ),
            ).fetchall()
        aggregate: dict[str, Any] = {
            "algorithmId": REGIME_ALGORITHM_ID,
            "persistedEvidenceIds": set(),
            "evidenceRecords": [],
        }
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict) or payload.get("trustedBackendRecord") is not True:
                continue
            aggregate["evidenceRecords"].append(payload)
            evidence_id = str(payload.get("evidenceId") or "")
            if evidence_id:
                aggregate["persistedEvidenceIds"].add(evidence_id)
            for key, value in payload.items():
                if isinstance(value, bool) and value:
                    aggregate[key] = True
                    aggregate["persistedEvidenceIds"].add(key)
                elif isinstance(value, int) and value and key.endswith(("Alerts", "Mismatches", "Orders")):
                    aggregate[key] = int(aggregate.get(key) or 0) + int(value)
        aggregate["persistedEvidenceIds"] = tuple(sorted(aggregate["persistedEvidenceIds"]))
        return aggregate

    def insert_order_intent(self, intent: dict[str, Any]) -> dict[str, Any]:
        common = _common_metadata({}, intent, intent)
        _validate_common_metadata(common)
        order_intent_id = common.get("order_intent_id")
        if not order_intent_id:
            return {"inserted": False, "reason": "missing_order_intent_id"}
        with self.connect() as conn:
            duplicate = conn.execute(
                """
                SELECT record_id
                FROM regime_order_intents
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND order_intent_id = ?
                """,
                (
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                    order_intent_id,
                ),
            ).fetchone()
            if duplicate:
                return {"inserted": False, "reason": "duplicate_order_intent", "orderIntentId": order_intent_id}
            if str(order_intent_id).startswith("regime-intent-"):
                duplicate_entry_bar = conn.execute(
                    """
                    SELECT order_intent_id
                    FROM regime_order_intents
                    WHERE algorithm_id = 'regime'
                      AND algorithm_instance_id = ?
                      AND account_id = ?
                      AND runtime_mode = ?
                      AND symbol = ?
                      AND data_timestamp = ?
                      AND algorithm_version = ?
                      AND settings_version = ?
                      AND order_intent_id LIKE 'regime-intent-%'
                    LIMIT 1
                    """,
                    (
                        common["algorithm_instance_id"],
                        common["account_id"],
                        common["runtime_mode"],
                        common["symbol"],
                        common["data_timestamp"],
                        common["algorithm_version"],
                        common["settings_version"],
                    ),
                ).fetchone()
                if duplicate_entry_bar:
                    return {
                        "inserted": False,
                        "reason": "duplicate_finalized_bar_entry_intent",
                        "orderIntentId": str(duplicate_entry_bar["order_intent_id"]),
                    }
            self._insert(conn, "regime_order_intents", common, "order-intent", intent)
            self._insert_execution_outbox_in_transaction(conn, common, intent)
        return {"inserted": True, "orderIntentId": order_intent_id}

    def insert_execution_outbox_record(self, identity: dict[str, Any], outbox_record: dict[str, Any]) -> dict[str, Any]:
        payload = {**outbox_record, "algorithmId": REGIME_ALGORITHM_ID}
        common = _common_metadata({}, {**identity, **payload}, {**identity, **payload})
        _validate_common_metadata(common)
        order_intent_id = _string_or_none(common.get("order_intent_id") or payload.get("orderIntentId") or payload.get("order_intent_id"))
        if not order_intent_id:
            return {"inserted": False, "reason": "missing_order_intent_id"}
        with self.connect() as conn:
            duplicate = conn.execute(
                """
                SELECT record_id
                FROM regime_execution_outbox
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND order_intent_id = ?
                  AND processing_status IN ('created', 'pending')
                LIMIT 1
                """,
                (
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                    order_intent_id,
                ),
            ).fetchone()
            if duplicate:
                return {"inserted": False, "reason": "duplicate_execution_outbox", "orderIntentId": order_intent_id}
            self._insert_execution_outbox_in_transaction(conn, common, payload)
        return {"inserted": True, "orderIntentId": order_intent_id}

    def record_runtime_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event = {"algorithmId": REGIME_ALGORITHM_ID, **event}
        if isinstance(event.get("payload"), dict):
            event["payload"] = {"algorithmId": REGIME_ALGORITHM_ID, **event["payload"]}
        common = _common_metadata({}, event, event)
        _validate_common_metadata(common)
        event_id = str(event.get("eventId") or event.get("event_id") or common["decision_id"])
        event["eventId"] = event_id
        with self.connect() as conn:
            self._insert(conn, "regime_runtime_events", {**common, "decision_id": event_id}, event_id, event, processing_status=str(event.get("processingStatus") or event.get("processing_status") or "recorded"))
        return {"recorded": True, "eventId": event_id}

    def read_runtime_event(self, identity: dict[str, Any], event_id: str) -> dict[str, Any] | None:
        common = _common_metadata({}, {**identity, "decisionId": event_id}, {**identity, "decisionId": event_id})
        _validate_common_metadata(common)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json, processing_status, created_at
                FROM regime_runtime_events
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND decision_id = ?
                  AND json_extract(payload_json, '$.eventType') = 'finalised_bar'
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                    event_id,
                ),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"]))
        if isinstance(payload, dict):
            payload["processingStatus"] = str(row["processing_status"])
            payload["createdAt"] = str(row["created_at"])
            return payload
        return {"eventId": event_id, "processingStatus": str(row["processing_status"]), "payload": payload}

    def recover_unprocessed_finalized_bar_events(self, identity: dict[str, Any], *, limit: int = 100) -> list[dict[str, Any]]:
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT events.decision_id, events.payload_json, events.processing_status, events.created_at
                FROM regime_runtime_events events
                WHERE events.algorithm_id = 'regime'
                  AND events.algorithm_instance_id = ?
                  AND events.account_id = ?
                  AND events.runtime_mode = ?
                  AND events.symbol = ?
                  AND json_extract(events.payload_json, '$.eventType') = 'finalised_bar'
                  AND events.processing_status IN ('queued', 'processing', 'failed')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM regime_runtime_checkpoints checkpoints
                      WHERE checkpoints.algorithm_id = 'regime'
                        AND checkpoints.algorithm_instance_id = events.algorithm_instance_id
                        AND checkpoints.account_id = events.account_id
                        AND checkpoints.runtime_mode = events.runtime_mode
                        AND checkpoints.symbol = events.symbol
                        AND checkpoints.decision_id = events.decision_id
                        AND checkpoints.processing_status = 'completed'
                        AND json_extract(checkpoints.payload_json, '$.stage') = 'decision_persisted'
                  )
                GROUP BY events.decision_id
                ORDER BY events.event_timestamp ASC, events.created_at ASC
                LIMIT ?
                """,
                (
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                    int(limit),
                ),
            ).fetchall()
        recovered: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict):
                continue
            payload["processingStatus"] = str(row["processing_status"])
            payload["createdAt"] = str(row["created_at"])
            recovered.append(payload)
        return recovered

    def record_stage_checkpoint(
        self,
        event_identity: dict[str, Any],
        stage: str,
        *,
        status: str = "completed",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if stage not in REGIME_RUNTIME_STAGES:
            raise ValueError(f"Unknown Regime runtime checkpoint stage: {stage}")
        event_id = str(event_identity.get("eventId") or event_identity.get("event_id") or "")
        if not event_id:
            raise ValueError("Regime stage checkpoints require eventId")
        common = _common_metadata({}, {**event_identity, "decisionId": event_id}, {**event_identity, "decisionId": event_id})
        _validate_common_metadata(common)
        checkpoint_payload = {
            "algorithmId": REGIME_ALGORITHM_ID,
            "eventId": event_id,
            "stage": stage,
            "status": status,
            "timestamp": _utc_now(),
            "details": sanitize_persistence_payload(payload or {}),
        }
        with self.connect() as conn:
            self._insert(
                conn,
                "regime_runtime_checkpoints",
                common,
                f"stage-{stage}",
                checkpoint_payload,
                processing_status=status,
            )
            self._insert(
                conn,
                "regime_bar_processing",
                common,
                f"bar-processing-{event_id}-{stage}",
                {"eventType": "bar_processing_stage", **checkpoint_payload},
                processing_status=status,
            )
            self._insert(
                conn,
                "regime_runtime_events",
                common,
                f"stage-{stage}",
                {"eventType": "runtime_stage_checkpoint", **checkpoint_payload},
                processing_status=status,
            )
        return {"recorded": True, "eventId": event_id, "stage": stage, "status": status}

    def event_stage_exists(self, identity: dict[str, Any], event_id: str, stage: str, *, status: str | None = "completed") -> bool:
        return self.read_event_stage(identity, event_id, stage, status=status) is not None

    def read_event_stage(self, identity: dict[str, Any], event_id: str, stage: str, *, status: str | None = None) -> dict[str, Any] | None:
        common = _common_metadata({}, {**identity, "decisionId": event_id}, {**identity, "decisionId": event_id})
        _validate_common_metadata(common)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json, processing_status, created_at
                FROM regime_runtime_checkpoints
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND decision_id = ?
                ORDER BY created_at DESC
                """,
                (
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                    event_id,
                ),
            ).fetchall()
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict) or payload.get("stage") != stage:
                continue
            if status is not None and str(row["processing_status"]) != status and str(payload.get("status")) != status:
                continue
            payload["processingStatus"] = str(row["processing_status"])
            return payload
        return None

    def event_checkpoint_summary(self, identity: dict[str, Any], event_id: str) -> dict[str, Any]:
        common = _common_metadata({}, {**identity, "decisionId": event_id}, {**identity, "decisionId": event_id})
        _validate_common_metadata(common)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json, processing_status
                FROM regime_runtime_checkpoints
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND decision_id = ?
                ORDER BY created_at
                """,
                (
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                    event_id,
                ),
            ).fetchall()
        stages: dict[str, str] = {}
        corrupted = False
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                corrupted = True
                continue
            if isinstance(payload, dict) and payload.get("stage"):
                stages[str(payload["stage"])] = str(row["processing_status"])
        return {"algorithmId": REGIME_ALGORITHM_ID, "eventId": event_id, "stages": stages, "corrupted": corrupted}

    def write_runtime_checkpoint(self, checkpoint: dict[str, Any], *, expected_sequence_version: int | None = None) -> dict[str, Any]:
        _require_full_ownership_identity(checkpoint)
        common = _common_metadata({}, checkpoint, checkpoint)
        _validate_common_metadata(common)
        with self.connect() as conn:
            latest = self._latest_mutable_row(conn, "regime_runtime_checkpoints", common)
            current_version = int(latest["sequence_version"]) if latest else 0
            if expected_sequence_version is not None and expected_sequence_version != current_version:
                return {
                    "updated": False,
                    "reason": "stale_state_version",
                    "currentSequenceVersion": current_version,
                    "expectedSequenceVersion": expected_sequence_version,
                }
            next_version = current_version + 1
            self._insert(conn, "regime_runtime_checkpoints", common, f"checkpoint-{next_version}", checkpoint, sequence_version=next_version)
            self._insert(conn, "regime_runtime_state", common, f"runtime-state-{next_version}", checkpoint, sequence_version=next_version)
        return {"updated": True, "sequenceVersion": next_version}

    def read_runtime_checkpoint(self, identity: dict[str, Any]) -> dict[str, Any] | None:
        _require_full_ownership_identity(identity)
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        with self.connect() as conn:
            row = self._latest_mutable_row(conn, "regime_runtime_checkpoints", common)
            return _row_payload(row) if row else None

    def quarantine_runtime_state(self, identity: dict[str, Any], *, reason: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        quarantine_id = f"regime-quarantine:{common['algorithm_instance_id']}:{common['account_id']}:{common['runtime_mode']}:{common['symbol']}"
        quarantine_payload = {
            "algorithmId": REGIME_ALGORITHM_ID,
            "eventType": "runtime_state_quarantine",
            "reason": reason,
            "payload": sanitize_persistence_payload(payload or {}),
            "timestamp": _utc_now(),
            "newEntriesPaused": True,
        }
        with self.connect() as conn:
            self._insert(
                conn,
                "regime_runtime_events",
                {**common, "decision_id": quarantine_id},
                f"quarantine-{_utc_now()}",
                quarantine_payload,
                processing_status="quarantined",
            )
        return {"quarantined": True, "reason": reason}

    def record_worker_heartbeat(self, identity: dict[str, Any], *, worker_id: str, owner_id: str, lease_expires_at: str) -> dict[str, Any]:
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        now = _utc_now()
        payload = {
            "algorithmId": REGIME_ALGORITHM_ID,
            "eventType": "worker_heartbeat",
            "workerId": worker_id,
            "leaseOwner": owner_id,
            "heartbeatAt": now,
            "leaseExpiresAt": lease_expires_at,
        }
        with self.connect() as conn:
            self._insert(conn, "regime_runtime_instances", {**common, "decision_id": f"runtime-lease:{worker_id}"}, worker_id, payload, processing_status="leased")
            self._insert(conn, "regime_runtime_events", {**common, "decision_id": f"runtime-heartbeat:{worker_id}"}, f"{worker_id}:{now}", payload, processing_status="heartbeat")
        return {"recorded": True, "workerId": worker_id, "leaseOwner": owner_id, "leaseExpiresAt": lease_expires_at}

    def detect_abandoned_leases(self, identity: dict[str, Any], *, now: str) -> dict[str, Any]:
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        abandoned: list[dict[str, Any]] = []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM regime_runtime_instances
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND processing_status = 'leased'
                ORDER BY created_at DESC
                """,
                (common["algorithm_instance_id"], common["account_id"], common["runtime_mode"], common["symbol"]),
            ).fetchall()
            seen: set[str] = set()
            for row in rows:
                payload = json.loads(str(row["payload_json"]))
                worker_id = str(payload.get("workerId") or "")
                if not worker_id or worker_id in seen:
                    continue
                seen.add(worker_id)
                if str(payload.get("leaseExpiresAt") or "") < now:
                    abandoned.append(payload)
            if abandoned:
                self._insert(
                    conn,
                    "regime_runtime_events",
                    {**common, "decision_id": f"abandoned-leases:{now}"},
                    "abandoned-leases",
                    {"eventType": "abandoned_lease_detection", "abandonedLeases": abandoned, "timestamp": now},
                    processing_status="abandoned_leases_detected",
                )
        return {"algorithmId": REGIME_ALGORITHM_ID, "abandonedLeaseCount": len(abandoned), "abandonedLeases": abandoned}

    def recover_unfinished_outbox_records(self, identity: dict[str, Any]) -> dict[str, Any]:
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        recoverable_statuses = {"created", "risk_approved", "queued", "retry_scheduled", "pending", "reserved", "risk_reserved", "submitting", "submitted", "broker_pending", "acknowledged", "partially_filled", "reconciliation_required"}
        recovered: list[str] = []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT order_intent_id, payload_json, processing_status
                FROM regime_execution_outbox
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                ORDER BY created_at
                """,
                (common["algorithm_instance_id"], common["account_id"], common["runtime_mode"], common["symbol"]),
            ).fetchall()
            latest_status_by_intent: dict[str, str] = {}
            for row in rows:
                order_intent_id = str(row["order_intent_id"] or "")
                if order_intent_id:
                    latest_status_by_intent[order_intent_id] = str(row["processing_status"])
            recovered.extend(order_intent_id for order_intent_id, status in latest_status_by_intent.items() if status in recoverable_statuses)
            if recovered:
                self._insert(
                    conn,
                    "regime_runtime_events",
                    {**common, "decision_id": f"outbox-recovery:{_utc_now()}"},
                    "outbox-recovery",
                    {"eventType": "unfinished_outbox_recovery", "orderIntentIds": sorted(set(recovered)), "timestamp": _utc_now()},
                    processing_status="recovered",
                )
        return {"algorithmId": REGIME_ALGORITHM_ID, "recoveredOutboxCount": len(set(recovered)), "orderIntentIds": sorted(set(recovered))}

    def pending_execution_outbox_records(
        self,
        identity: dict[str, Any],
        *,
            statuses: tuple[str, ...] = ("created", "risk_approved", "queued", "retry_scheduled", "pending", "risk_reserved", "submitting", "submitted", "acknowledged", "partially_filled", "cancel_pending", "cancel_requested", "reconciliation_required"),
    ) -> list[dict[str, Any]]:
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        latest_by_intent: dict[str, dict[str, Any]] = {}
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT order_intent_id, processing_status, payload_json, created_at
                FROM regime_execution_outbox
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND order_intent_id IS NOT NULL
                  AND order_intent_id <> ''
                ORDER BY rowid
                """,
                (common["algorithm_instance_id"], common["account_id"], common["runtime_mode"], common["symbol"]),
            ).fetchall()
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if isinstance(payload, dict):
                payload["processingStatus"] = str(row["processing_status"])
                payload["orderIntentId"] = str(row["order_intent_id"])
                latest_by_intent[str(row["order_intent_id"])] = payload
        return [payload for payload in latest_by_intent.values() if payload.get("processingStatus") in set(statuses)]

    def active_execution_outbox_identities(self, fallback_identity: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        terminal_statuses = {"filled", "cancelled", "canceled", "rejected", "expired", "dead_letter"}
        identities: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        if fallback_identity:
            fallback = regime_settings_identity_from_payload(fallback_identity)
            if fallback["runtimeMode"] == "paper" and fallback["algorithmInstanceId"] != "regime-default":
                identities[(fallback["algorithmInstanceId"], fallback["accountId"], fallback["runtimeMode"], fallback["symbol"])] = fallback
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT algorithm_instance_id, account_id, runtime_mode, symbol, processing_status, order_intent_id
                FROM regime_execution_outbox
                WHERE algorithm_id = 'regime'
                  AND order_intent_id IS NOT NULL
                  AND order_intent_id <> ''
                ORDER BY rowid
                """
            ).fetchall()
        latest: dict[str, sqlite3.Row] = {}
        for row in rows:
            latest[str(row["order_intent_id"])] = row
        for row in latest.values():
            status = str(row["processing_status"] or "")
            if status in terminal_statuses:
                continue
            runtime_mode = str(row["runtime_mode"] or "")
            instance_id = str(row["algorithm_instance_id"] or "")
            account_id = str(row["account_id"] or "")
            symbol = str(row["symbol"] or "SPY").upper()
            if runtime_mode != "paper" or instance_id == "regime-default":
                continue
            key = (instance_id, account_id, runtime_mode, symbol)
            identities[key] = {
                "algorithmId": REGIME_ALGORITHM_ID,
                "algorithmInstanceId": instance_id,
                "accountId": account_id,
                "runtimeMode": runtime_mode,
                "symbol": symbol,
            }
        return list(identities.values())

    def claim_next_execution_outbox_record(
        self,
        identity: dict[str, Any],
        *,
        owner_id: str,
        lease_seconds: int,
        now: str | None = None,
        statuses: tuple[str, ...] = ("created", "risk_approved", "queued", "retry_scheduled", "pending", "risk_reserved"),
    ) -> dict[str, Any] | None:
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        claimed_at = now or _utc_now()
        try:
            claimed_dt = datetime.fromisoformat(str(claimed_at).replace("Z", "+00:00"))
        except ValueError:
            claimed_dt = datetime.now(timezone.utc)
        if claimed_dt.tzinfo is None:
            claimed_dt = claimed_dt.replace(tzinfo=timezone.utc)
        lease_expires_at = (claimed_dt.astimezone(timezone.utc) + timedelta(seconds=max(1, int(lease_seconds)))).isoformat().replace("+00:00", "Z")
        status_placeholders = ",".join("?" for _ in statuses)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"""
                SELECT latest.order_intent_id, latest.processing_status, latest.payload_json, latest.created_at
                FROM regime_execution_outbox latest
                WHERE latest.algorithm_id = 'regime'
                  AND latest.algorithm_instance_id = ?
                  AND latest.account_id = ?
                  AND latest.runtime_mode = ?
                  AND latest.symbol = ?
                  AND latest.order_intent_id IS NOT NULL
                  AND latest.order_intent_id <> ''
                  AND latest.rowid IN (
                      SELECT MAX(rowid)
                      FROM regime_execution_outbox
                      WHERE algorithm_id = 'regime'
                        AND algorithm_instance_id = ?
                        AND account_id = ?
                        AND runtime_mode = ?
                        AND symbol = ?
                      GROUP BY order_intent_id
                  )
                  AND (
                      latest.processing_status IN ({status_placeholders})
                      OR latest.processing_status LIKE 'retry_scheduled:%'
                      OR latest.processing_status LIKE 'blocked:%'
                  )
                ORDER BY latest.created_at, latest.rowid
                LIMIT 1
                """,
                (
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                    *statuses,
                ),
            ).fetchone()
            if row is None:
                return None
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict):
                return None
            order_intent_id = str(row["order_intent_id"])
            previous_status = str(row["processing_status"])
            lease_id = _stable_snapshot_key(f"{owner_id}:{order_intent_id}:{claimed_at}")
            claimed_status = f"processing:{lease_id}"
            claimed = {
                **payload,
                "algorithmId": REGIME_ALGORITHM_ID,
                "algorithmInstanceId": common["algorithm_instance_id"],
                "accountId": common["account_id"],
                "runtimeMode": common["runtime_mode"],
                "symbol": common["symbol"],
                "orderIntentId": order_intent_id,
                "processingStatus": claimed_status,
                "previousProcessingStatus": previous_status,
                "claimedBy": owner_id,
                "claimedAt": claimed_at,
                "leaseId": lease_id,
                "leaseExpiresAt": lease_expires_at,
                "reasonCodes": list(dict.fromkeys([*(_list(payload.get("reasonCodes"))), "regime.execution.outbox_claimed"])),
            }
            self._insert(
                conn,
                "regime_execution_outbox",
                {**common, "order_intent_id": order_intent_id},
                f"execution-outbox-{order_intent_id}-{claimed_status}",
                claimed,
                processing_status=claimed_status,
            )
        return claimed

    def read_execution_outbox_record(self, identity: dict[str, Any], order_intent_id: str) -> dict[str, Any] | None:
        common = _common_metadata({}, {**identity, "orderIntentId": order_intent_id}, {**identity, "orderIntentId": order_intent_id})
        _validate_common_metadata(common)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json, processing_status
                FROM regime_execution_outbox
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND order_intent_id = ?
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (common["algorithm_instance_id"], common["account_id"], common["runtime_mode"], common["symbol"], order_intent_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"]))
        if isinstance(payload, dict):
            payload["processingStatus"] = str(row["processing_status"])
            payload["orderIntentId"] = order_intent_id
            return payload
        return {"payload": payload, "processingStatus": str(row["processing_status"]), "orderIntentId": order_intent_id}

    def record_local_risk_result(self, identity: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        payload = {**result, "algorithmId": REGIME_ALGORITHM_ID}
        local_risk_result_id = _string_or_none(payload.get("localRiskResultId") or payload.get("local_risk_result_id"))
        if not local_risk_result_id:
            return {"recorded": False, "reason": "missing_local_risk_result_id"}
        common = _common_metadata({}, {**identity, **payload}, {**identity, **payload})
        _validate_common_metadata(common)
        status = "approved" if payload.get("passed") else "blocked"
        with self.connect() as conn:
            self._insert(
                conn,
                "regime_local_risk_results",
                common,
                local_risk_result_id,
                payload,
                processing_status=status,
            )
        return {
            "recorded": True,
            "localRiskResultId": local_risk_result_id,
            "decisionId": common["decision_id"],
            "orderIntentId": common["order_intent_id"],
            "status": status,
        }

    def read_latest_local_risk_result(self, identity: dict[str, Any], *, decision_id: str, order_intent_id: str) -> dict[str, Any] | None:
        common = _common_metadata({}, {**identity, "decisionId": decision_id, "orderIntentId": order_intent_id}, {**identity, "decisionId": decision_id, "orderIntentId": order_intent_id})
        _validate_common_metadata(common)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json, processing_status, created_at
                FROM regime_local_risk_results
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND decision_id = ?
                  AND order_intent_id = ?
                ORDER BY rowid DESC
                """,
                (
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                    decision_id,
                    order_intent_id,
                ),
            ).fetchall()
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if isinstance(payload, dict):
                payload["processingStatus"] = str(row["processing_status"])
                payload["createdAt"] = str(row["created_at"])
                return payload
        return None

    def update_execution_outbox_status(
        self,
        identity: dict[str, Any],
        order_intent_id: str,
        *,
        status: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        existing = self.read_execution_outbox_record(identity, order_intent_id) or {}
        common = _common_metadata({}, {**identity, **existing, **payload, "orderIntentId": order_intent_id}, {**identity, **existing, **payload, "orderIntentId": order_intent_id})
        _validate_common_metadata(common)
        merged = {
            **existing,
            **payload,
            "algorithmId": REGIME_ALGORITHM_ID,
            "orderIntentId": order_intent_id,
            "processingStatus": status,
            "updatedAt": _utc_now(),
        }
        with self.connect() as conn:
            duplicate_status = conn.execute(
                """
                SELECT record_id
                FROM regime_execution_outbox
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND order_intent_id = ?
                  AND processing_status = ?
                LIMIT 1
                """,
                (
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                    order_intent_id,
                    status,
                ),
            ).fetchone()
            if duplicate_status and not payload.get("allowDuplicateStatusUpdate"):
                return {"updated": False, "orderIntentId": order_intent_id, "status": status, "reason": "duplicate_execution_outbox_status"}
            if duplicate_status and payload.get("allowDuplicateStatusUpdate"):
                return {"updated": True, "orderIntentId": order_intent_id, "status": status, "duplicateStatusUpdate": True}
            try:
                self._insert(conn, "regime_execution_outbox", common, f"execution-outbox-{order_intent_id}-{status}-{_stable_snapshot_key(_utc_now())}", merged, processing_status=status)
            except sqlite3.IntegrityError:
                duplicate_status = conn.execute(
                    """
                    SELECT record_id
                    FROM regime_execution_outbox
                    WHERE algorithm_id = 'regime'
                      AND algorithm_instance_id = ?
                      AND account_id = ?
                      AND runtime_mode = ?
                      AND symbol = ?
                      AND order_intent_id = ?
                      AND processing_status = ?
                    LIMIT 1
                    """,
                    (
                        common["algorithm_instance_id"],
                        common["account_id"],
                        common["runtime_mode"],
                        common["symbol"],
                        order_intent_id,
                        status,
                    ),
                ).fetchone()
                if duplicate_status and payload.get("allowDuplicateStatusUpdate"):
                    return {"updated": True, "orderIntentId": order_intent_id, "status": status, "duplicateStatusUpdate": True}
                if duplicate_status:
                    return {"updated": False, "orderIntentId": order_intent_id, "status": status, "reason": "duplicate_execution_outbox_status"}
                raise
        return {"updated": True, "orderIntentId": order_intent_id, "status": status}

    def _insert_execution_outbox_in_transaction(self, conn: sqlite3.Connection, common: dict[str, str | None], payload: dict[str, Any]) -> str:
        order_intent_id = _string_or_none(common.get("order_intent_id") or payload.get("orderIntentId") or payload.get("order_intent_id"))
        if not order_intent_id:
            raise ValueError("Regime execution outbox requires orderIntentId")
        execution_outbox_id = _string_or_none(payload.get("executionOutboxId") or payload.get("execution_outbox_id")) or _stable_id("regime-outbox", common, payload)
        idempotency_key = _string_or_none(payload.get("idempotencyKey") or payload.get("idempotency_key")) or _stable_id(
            "regime-execution-idempotency",
            {**common, "decision_id": common.get("decision_id"), "order_intent_id": order_intent_id},
            {
                "decisionId": common.get("decision_id"),
                "orderIntentId": order_intent_id,
                "settingsVersion": common.get("settings_version") or payload.get("settingsVersion") or payload.get("settings_version"),
            },
        )
        outbox_payload = {
            "algorithmId": REGIME_ALGORITHM_ID,
            "executionOutboxId": execution_outbox_id,
            "idempotencyKey": idempotency_key,
            "orderIntentId": order_intent_id,
            "processingStatus": str(payload.get("processingStatus") or payload.get("processing_status") or "created"),
            "orderIntent": _record(payload.get("orderIntent")) or payload,
            "runtimeMode": payload.get("runtimeMode") or payload.get("runtime_mode") or common.get("runtime_mode"),
            "algorithmInstanceId": payload.get("algorithmInstanceId") or payload.get("algorithm_instance_id") or common.get("algorithm_instance_id"),
            "accountId": payload.get("accountId") or payload.get("account_id") or common.get("account_id"),
            "symbol": payload.get("symbol") or common.get("symbol"),
            "quantity": payload.get("quantity"),
            "positionEffect": payload.get("positionEffect") or payload.get("position_effect"),
            "completedBarFinalized": payload.get("completedBarFinalized"),
            "marketDataValidation": _record(payload.get("marketDataValidation") or payload.get("market_data_validation")),
            "stateMachine": {
                "version": "regime_execution_outbox_state_machine_v2",
                "allowedStates": [
                    "created",
                    "risk_approved",
                    "queued",
                    "retry_scheduled",
                    "submitting",
                    "acknowledged",
                    "partially_filled",
                    "filled",
                    "cancel_pending",
                    "cancelled",
                    "rejected",
                    "expired",
                    "reconciliation_required",
                    "dead_letter",
                ],
            },
            "retryPolicy": {
                "maxAttempts": int(payload.get("maxRetryAttempts") or payload.get("max_retry_attempts") or 3),
                "backoffSeconds": [5, 15, 45],
                "retryOnlySafeOperations": True,
            },
            "retryCount": int(payload.get("retryCount") or payload.get("retry_count") or 0),
            "nextRetryAt": payload.get("nextRetryAt") or payload.get("next_retry_at"),
            "expiresAt": payload.get("expiresAt") or payload.get("expires_at"),
            "globalRiskApproval": _record(payload.get("globalRiskApproval") or payload.get("global_risk_approval")),
            "globalRiskReservationId": payload.get("globalRiskReservationId") or payload.get("reservationId"),
            "orderReplacementPolicy": "cancel_stale_unfilled_orders_replace_requires_new_intent",
        }
        self._insert(
            conn,
            "regime_execution_outbox",
            {**common, "order_intent_id": order_intent_id},
            execution_outbox_id,
            outbox_payload,
            processing_status=outbox_payload["processingStatus"],
        )
        return execution_outbox_id

    def write_runtime_snapshot(self, identity: dict[str, Any], key: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        snapshot_id = f"runtime-snapshot:{_stable_snapshot_key(key)}"
        snapshot = {"algorithmId": REGIME_ALGORITHM_ID, **snapshot}
        with self.connect() as conn:
            self._insert(
                conn,
                "regime_runtime_events",
                {**common, "decision_id": snapshot_id},
                snapshot_id,
                {"eventType": "runtime_snapshot", "snapshotKey": key, "snapshot": sanitize_persistence_payload(snapshot), "timestamp": _utc_now()},
                processing_status="recorded",
            )
        return {"recorded": True, "snapshotKey": key}

    def read_runtime_snapshot(self, identity: dict[str, Any], key: str) -> dict[str, Any] | None:
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        snapshot_id = f"runtime-snapshot:{_stable_snapshot_key(key)}"
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM regime_runtime_events
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND decision_id = ?
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (common["algorithm_instance_id"], common["account_id"], common["runtime_mode"], common["symbol"], snapshot_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"]))
        return _record(payload.get("snapshot"))

    def read_decision_snapshot_by_id(self, identity: dict[str, Any], decision_id: str) -> dict[str, Any] | None:
        common = _common_metadata({}, {**identity, "decisionId": decision_id}, {**identity, "decisionId": decision_id})
        _validate_common_metadata(common)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM regime_decisions
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND decision_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                    decision_id,
                ),
            ).fetchone()
        return json.loads(str(row["payload_json"])) if row else None

    def copy_broker_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        _require_full_ownership_identity(observation)
        common = _common_metadata({}, observation, observation)
        _validate_common_metadata(common)
        target_table = _regime_ledger_table_for_observation(observation)
        with self.connect() as conn:
            inserted = self._copy_broker_observation_in_transaction(conn, observation, common)
        if inserted == "regime_orders":
            self.record_inventory_order_status(observation)
        elif inserted == "regime_reconciliation_events":
            self.record_reconciliation_run(observation, status=str(observation.get("processingStatus") or "observed"))
        return {"copied": bool(inserted), "table": inserted or target_table, "duplicate": target_table is not None and not inserted}

    def current_inventory_snapshot(self, identity: dict[str, Any]) -> dict[str, Any]:
        _require_full_ownership_identity(identity)
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        with self.connect() as conn:
            row = self._latest_mutable_row(conn, "regime_inventory_snapshots", common)
        return _row_payload(row) if row else _base_inventory_snapshot(identity, common)

    def apply_inventory_fill(
        self,
        identity: dict[str, Any],
        fill: dict[str, Any],
        *,
        settings_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if str(fill.get("algorithmId") or fill.get("algorithm_id") or "") != REGIME_ALGORITHM_ID:
            raise ValueError("Regime inventory rejects cross-algorithm fill observations")
        _require_full_ownership_identity(identity)
        _require_matching_ownership_identity(identity, fill)
        common = _common_metadata({}, {**identity, **fill}, {**identity, **fill})
        _validate_common_metadata(common)
        event = _inventory_event_from_fill(identity, fill, settings_snapshot=settings_snapshot)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._inventory_event_exists(conn, common, event["inventoryEventId"]):
                snapshot = self._latest_inventory_snapshot(conn, identity, common)
                return {
                    "updated": False,
                    "duplicate": True,
                    "reason": "regime.inventory.duplicate_fill_ignored",
                    "inventoryEventId": event["inventoryEventId"],
                    "snapshot": snapshot,
                }
            previous = self._latest_inventory_snapshot(conn, identity, common)
            fill_signed_quantity = _int(event.get("signedQuantity"))
            previous_quantity = _int(previous.get("quantity"))
            if (
                previous_quantity != 0
                and fill_signed_quantity != 0
                and ((previous_quantity > 0 and fill_signed_quantity < 0) or (previous_quantity < 0 and fill_signed_quantity > 0))
                and abs(fill_signed_quantity) > abs(previous_quantity)
            ):
                raise ValueError("Regime inventory exit quantity exceeds owned position quantity")
            next_snapshot = _apply_fill_to_inventory_snapshot(previous, event)
            self._insert_inventory_event_and_snapshot(conn, common, event, next_snapshot, processing_status="fill_applied")
        return {"updated": True, "inventoryEventId": event["inventoryEventId"], "snapshot": next_snapshot}

    def record_inventory_order_status(self, observation: dict[str, Any]) -> dict[str, Any]:
        if str(observation.get("algorithmId") or observation.get("algorithm_id") or "") != REGIME_ALGORITHM_ID:
            raise ValueError("Regime inventory rejects cross-algorithm order observations")
        _require_full_ownership_identity(observation)
        common = _common_metadata({}, observation, observation)
        _validate_common_metadata(common)
        event = _inventory_event_from_order_status(observation)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._inventory_event_exists(conn, common, event["inventoryEventId"]):
                return {"recorded": False, "duplicate": True, "inventoryEventId": event["inventoryEventId"]}
            previous = self._latest_inventory_snapshot(conn, observation, common)
            next_snapshot = _apply_order_status_to_inventory_snapshot(previous, event)
            self._insert_inventory_event_and_snapshot(conn, common, event, next_snapshot, processing_status=str(event["orderStatus"]))
        return {"recorded": True, "inventoryEventId": event["inventoryEventId"], "snapshot": next_snapshot}

    def record_inventory_broker_correction(self, identity: dict[str, Any], correction: dict[str, Any]) -> dict[str, Any]:
        payload = {"algorithmId": REGIME_ALGORITHM_ID, **correction, "type": "broker_correction"}
        _require_full_ownership_identity(identity)
        _require_matching_ownership_identity(identity, payload)
        common = _common_metadata({}, {**identity, **payload}, {**identity, **payload})
        _validate_common_metadata(common)
        event = _inventory_event_from_broker_correction(identity, payload)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._inventory_event_exists(conn, common, event["inventoryEventId"]):
                return {"recorded": False, "duplicate": True, "inventoryEventId": event["inventoryEventId"]}
            previous = self._latest_inventory_snapshot(conn, identity, common)
            next_snapshot = _apply_fill_to_inventory_snapshot(previous, event)
            self._insert_inventory_event_and_snapshot(conn, common, event, next_snapshot, processing_status="broker_correction_applied")
        return {"recorded": True, "inventoryEventId": event["inventoryEventId"], "snapshot": next_snapshot}

    def verify_or_rebuild_inventory_snapshot(
        self,
        identity: dict[str, Any],
        *,
        broker_positions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        _require_full_ownership_identity(identity)
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rebuilt = self._rebuild_inventory_snapshot_from_events(conn, identity, common)
            current = self._latest_inventory_snapshot(conn, identity, common)
            snapshot_matches = _inventory_snapshots_equivalent(current, rebuilt)
            if not snapshot_matches:
                rebuilt = {**rebuilt, "rebuildReason": "ledger_verification_mismatch"}
                self._insert(
                    conn,
                    "regime_inventory_snapshots",
                    {**common, "position_id": rebuilt.get("positionId"), "trade_id": rebuilt.get("tradeId")},
                    f"inventory-rebuild-{int(current.get('stateVersion') or 0) + 1}",
                    rebuilt,
                    processing_status="rebuilt",
                    sequence_version=int(current.get("stateVersion") or 0) + 1,
                )
                current = rebuilt
            discrepancies = _broker_inventory_discrepancies(current, broker_positions or [])
            reconciled_at = _utc_now()
            current = {**current, "lastBrokerReconciliationTime": reconciled_at}
            self._insert(
                conn,
                "regime_inventory_snapshots",
                {**common, "position_id": current.get("positionId"), "trade_id": current.get("tradeId")},
                f"inventory-reconciliation-{_stable_snapshot_key(reconciled_at)}",
                current,
                processing_status="reconciled" if not discrepancies else "unresolved_discrepancy",
                sequence_version=int(current.get("stateVersion") or 0),
            )
            run = {
                "algorithmId": REGIME_ALGORITHM_ID,
                "eventType": "inventory_startup_verification",
                "snapshotMatchesLedger": snapshot_matches,
                "snapshot": current,
                "brokerPositionCount": len(broker_positions or []),
                "discrepancies": discrepancies,
                "timestamp": reconciled_at,
            }
            self._insert(
                conn,
                "regime_reconciliation_runs",
                common,
                f"inventory-startup-verification-{_stable_snapshot_key(json.dumps(discrepancies, sort_keys=True, default=str))}",
                run,
                processing_status="reconciled" if not discrepancies else "unresolved_discrepancy",
            )
        return {
            "algorithmId": REGIME_ALGORITHM_ID,
            "verified": snapshot_matches,
            "rebuilt": not snapshot_matches,
            "reconciled": not discrepancies,
            "snapshot": current,
            "discrepancies": discrepancies,
        }

    def record_reconciliation_run(self, observation: dict[str, Any], *, status: str = "observed") -> dict[str, Any]:
        common = _common_metadata({}, observation, observation)
        _validate_common_metadata(common)
        payload = {
            "algorithmId": REGIME_ALGORITHM_ID,
            "eventType": "broker_reconciliation_run",
            "timestamp": observation.get("timestamp") or _utc_now(),
            "observation": sanitize_persistence_payload(observation),
        }
        with self.connect() as conn:
            self._insert(conn, "regime_reconciliation_runs", common, f"reconciliation-run-{_stable_snapshot_key(str(payload))}", payload, processing_status=status)
        return {"recorded": True, "status": status}

    def record_runtime_alert(self, identity: dict[str, Any], alert: dict[str, Any], *, status: str = "active") -> dict[str, Any]:
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        payload = {
            "algorithmId": REGIME_ALGORITHM_ID,
            "eventType": "runtime_alert",
            "status": status,
            "timestamp": alert.get("timestamp") or _utc_now(),
            "alert": sanitize_persistence_payload(alert),
        }
        with self.connect() as conn:
            self._insert(conn, "regime_runtime_alerts", common, f"runtime-alert-{_stable_snapshot_key(str(payload))}", payload, processing_status=status)
        return {"recorded": True, "status": status}

    def record_position_state(self, identity: dict[str, Any], position: dict[str, Any]) -> dict[str, Any]:
        payload = {**position, "algorithmId": REGIME_ALGORITHM_ID}
        _require_full_ownership_identity(identity)
        _require_matching_ownership_identity(identity, payload)
        common = _common_metadata({}, {**identity, **payload}, {**identity, **payload})
        _validate_common_metadata(common)
        position_id = _string_or_none(payload.get("positionId") or payload.get("position_id")) or _stable_id("regime-position", common, payload)
        payload["positionId"] = position_id
        status = str(payload.get("positionStatus") or payload.get("status") or "open").lower()
        with self.connect() as conn:
            sequence_version = _next_sequence_version_for_id(conn, "regime_positions", common, "position_id", position_id)
            payload["sequenceVersion"] = sequence_version
            self._insert(conn, "regime_positions", {**common, "position_id": position_id}, f"position-state-{position_id}-{sequence_version}", payload, processing_status=status, sequence_version=sequence_version)
        return {"recorded": True, "positionId": position_id, "status": status}

    def record_trade_state(self, identity: dict[str, Any], trade: dict[str, Any]) -> dict[str, Any]:
        payload = {**trade, "algorithmId": REGIME_ALGORITHM_ID}
        _require_full_ownership_identity(identity)
        _require_matching_ownership_identity(identity, payload)
        common = _common_metadata({}, {**identity, **payload}, {**identity, **payload})
        _validate_common_metadata(common)
        trade_id = _string_or_none(payload.get("tradeId") or payload.get("trade_id")) or _stable_id("regime-trade", common, payload)
        payload["tradeId"] = trade_id
        status = str(payload.get("tradeStatus") or payload.get("status") or "open").lower()
        with self.connect() as conn:
            sequence_version = _next_sequence_version_for_id(conn, "regime_trades", common, "trade_id", trade_id)
            payload["sequenceVersion"] = sequence_version
            self._insert(conn, "regime_trades", {**common, "trade_id": trade_id}, f"trade-state-{trade_id}-{sequence_version}", payload, processing_status=status, sequence_version=sequence_version)
        return {"recorded": True, "tradeId": trade_id, "status": status}

    def latest_regime_positions(self, identity: dict[str, Any]) -> list[dict[str, Any]]:
        common = _common_metadata({}, identity, identity)
        _validate_common_metadata(common)
        latest: dict[str, dict[str, Any]] = {}
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT position_id, processing_status, payload_json
                FROM regime_positions
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                ORDER BY rowid
                """,
                (common["algorithm_instance_id"], common["account_id"], common["runtime_mode"], common["symbol"]),
            ).fetchall()
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict):
                continue
            position_id = str(payload.get("positionId") or payload.get("position_id") or row["position_id"] or "")
            if not position_id:
                continue
            payload["positionId"] = position_id
            payload["processingStatus"] = str(row["processing_status"])
            latest[position_id] = payload
        return list(latest.values())

    def latest_open_regime_positions(self, identity: dict[str, Any]) -> list[dict[str, Any]]:
        terminal = {"closed", "cancelled", "canceled", "flat"}
        return [
            position
            for position in self.latest_regime_positions(identity)
            if str(position.get("positionStatus") or position.get("processingStatus") or "open").lower() not in terminal
            and int(position.get("filledQuantity") or position.get("quantity") or 0) != 0
        ]

    def read_owned_records(self, table: str, identity: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if table not in REGIME_OWNED_TABLES:
            raise ValueError(f"Regime repository cannot read non-owned table: {table}")
        _require_full_ownership_identity(identity)
        common = _common_metadata({}, identity or {}, identity or {})
        _validate_common_metadata(common)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM {table}
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                ORDER BY event_timestamp, created_at
                """,
                (common["algorithm_instance_id"], common["account_id"], common["runtime_mode"], common["symbol"]),
            ).fetchall()
        return [json.loads(str(row["payload_json"])) for row in rows]

    def ensure_active_settings_snapshot(self, identity: dict[str, Any] | None = None) -> dict[str, Any]:
        resolved_identity = regime_settings_identity_from_payload(identity or {})
        existing = self.active_settings_snapshot(resolved_identity, create_default=False)
        if existing is not None:
            return existing
        snapshot = regime_trading_settings_to_dict(
            validate_regime_trading_settings_snapshot(
                {"identity": resolved_identity},
                actor="system",
                previous_settings_version=None,
            )
        )
        return self._persist_settings_snapshot(snapshot, actor="system", previous_settings_version=None)

    def active_settings_snapshot(self, identity: dict[str, Any] | None = None, *, create_default: bool = True) -> dict[str, Any] | None:
        resolved_identity = regime_settings_identity_from_payload(identity or {})
        common = _settings_common(resolved_identity, "active-settings")
        with self.connect() as conn:
            row = self._latest_mutable_row(conn, "regime_active_settings", common)
        if row is None:
            return self.ensure_active_settings_snapshot(resolved_identity) if create_default else None
        payload = _row_payload(row)
        settings_snapshot = _record(payload.get("settingsSnapshot"))
        return {
            "algorithmId": REGIME_ALGORITHM_ID,
            "identity": resolved_identity,
            "settingsVersion": settings_snapshot.get("settingsVersion") or payload.get("activeSettingsVersion"),
            "settingsSnapshot": settings_snapshot,
            "flatSettings": flatten_regime_trading_settings(settings_snapshot),
            "settingsSource": REGIME_SETTINGS_AUTHORITATIVE_SOURCE,
            "sequenceVersion": payload.get("sequenceVersion"),
        }

    def activate_settings_snapshot(self, command: dict[str, Any]) -> dict[str, Any]:
        actor = str(command.get("actor") or command.get("submittedBy") or command.get("createdBy") or "unknown")
        payload = _record(command.get("settings")) or _record(command.get("settingsSnapshot")) or command
        identity = regime_settings_identity_from_payload(payload or command)
        previous = self.active_settings_snapshot(identity, create_default=False)
        previous_version = str(previous.get("settingsVersion") or "") if previous else None
        settings = validate_regime_trading_settings_snapshot(
            {**payload, "identity": identity},
            actor=actor,
            previous_settings_version=previous_version,
        )
        snapshot = regime_trading_settings_to_dict(settings)
        return self._persist_settings_snapshot(
            snapshot,
            actor=actor,
            previous_settings_version=previous_version,
            activation_reason=str(command.get("activationReason") or command.get("reason") or "activate_version"),
        )

    def validate_settings_snapshot_command(self, command: dict[str, Any]) -> dict[str, Any]:
        actor = str(command.get("actor") or command.get("submittedBy") or command.get("createdBy") or "unknown")
        payload = _record(command.get("settings")) or _record(command.get("settingsSnapshot")) or command
        identity = regime_settings_identity_from_payload(payload or command)
        previous = self.active_settings_snapshot(identity, create_default=True)
        previous_version = str(previous.get("settingsVersion") or "") if previous else None
        settings = validate_regime_trading_settings_snapshot(
            {**payload, "identity": identity},
            actor=actor,
            previous_settings_version=previous_version,
        )
        snapshot = regime_trading_settings_to_dict(settings)
        return {
            "validated": True,
            "algorithmId": REGIME_ALGORITHM_ID,
            "identity": identity,
            "previousSettingsVersion": previous_version,
            "settingsVersion": snapshot["settingsVersion"],
            "settingsHash": snapshot.get("settingsHash") or snapshot.get("configurationHash"),
            "settingsSnapshot": snapshot,
            "flatSettings": flatten_regime_trading_settings(snapshot),
            "settingsSource": REGIME_SETTINGS_AUTHORITATIVE_SOURCE,
        }

    def create_settings_version(self, command: dict[str, Any]) -> dict[str, Any]:
        validated = self.validate_settings_snapshot_command(command)
        snapshot = _record(validated.get("settingsSnapshot"))
        actor = str(command.get("actor") or command.get("submittedBy") or command.get("createdBy") or "unknown")
        snapshot = _settings_snapshot_with_activation_metadata(
            snapshot,
            activation_status="inactive",
            activated_at=None,
            reason=str(command.get("activationReason") or command.get("reason") or "create_version"),
            source=str(command.get("source") or actor),
        )
        identity = _record(validated.get("identity"))
        settings_version = str(snapshot["settingsVersion"])
        common = _settings_common(identity, settings_version)
        audit_id = f"settings-create-audit:{settings_version}"
        with self.connect() as conn:
            self._insert(conn, "regime_settings_versions", common, settings_version, snapshot)
            for strategy_id, strategy_settings in _record(snapshot.get("strategy_settings")).items():
                self._insert(
                    conn,
                    "regime_strategy_settings",
                    common,
                    f"strategy-settings-{strategy_id}",
                    {"strategyId": strategy_id, "settingsVersion": settings_version, "settings": strategy_settings},
                )
            self._insert(
                conn,
                "regime_runtime_events",
                {**common, "decision_id": audit_id},
                audit_id,
                {
                    "eventType": "settings_version_created_audit",
                    "actor": actor,
                    "timestamp": snapshot.get("createdAt"),
                    "previousSettingsVersion": validated.get("previousSettingsVersion"),
                    "newSettingsVersion": settings_version,
                    "settingsSnapshot": snapshot,
                },
            )
        return {
            **validated,
            "settingsSnapshot": snapshot,
            "settingsHash": snapshot.get("settingsHash") or snapshot.get("contentHash"),
            "created": True,
            "activated": False,
        }

    def rollback_settings_snapshot(self, command: dict[str, Any]) -> dict[str, Any]:
        actor = str(command.get("actor") or command.get("submittedBy") or command.get("createdBy") or "unknown")
        identity = regime_settings_identity_from_payload(command)
        current = self.active_settings_snapshot(identity, create_default=True)
        current_version = str(current.get("settingsVersion") or "") if current else None
        target_version = str(command.get("targetSettingsVersion") or command.get("rollbackToSettingsVersion") or command.get("settingsVersion") or "")
        if not target_version and current:
            target_version = str(_record(current.get("settingsSnapshot")).get("previousSettingsVersion") or "")
        if not target_version:
            raise ValueError("Regime settings rollback requires a target settings version")
        target = self.settings_version_snapshot(identity, target_version)
        if target is None:
            raise ValueError(f"Regime settings rollback target not found: {target_version}")
        snapshot = {
            **target,
            "previousSettingsVersion": current_version,
            "createdAt": _utc_now(),
            "createdBy": actor,
        }
        return self._persist_settings_snapshot(
            snapshot,
            actor=actor,
            previous_settings_version=current_version,
            activation_reason=str(command.get("rollbackReason") or command.get("reason") or f"rollback_to:{target_version}"),
        )

    def settings_version_snapshot(self, identity: dict[str, Any], settings_version: str) -> dict[str, Any] | None:
        resolved_identity = regime_settings_identity_from_payload(identity)
        common = _settings_common(resolved_identity, settings_version)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json, sequence_version
                FROM regime_settings_versions
                WHERE algorithm_id = 'regime'
                  AND algorithm_instance_id = ?
                  AND account_id = ?
                  AND runtime_mode = ?
                  AND symbol = ?
                  AND settings_version = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (
                    common["algorithm_instance_id"],
                    common["account_id"],
                    common["runtime_mode"],
                    common["symbol"],
                    settings_version,
                ),
            ).fetchone()
        return _row_payload(row) if row else None

    def _persist_settings_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        actor: str,
        previous_settings_version: str | None,
        activation_reason: str = "activate_version",
    ) -> dict[str, Any]:
        identity = regime_settings_identity_from_payload(snapshot)
        settings_version = str(snapshot["settingsVersion"])
        common = _settings_common(identity, settings_version)
        audit_id = f"settings-audit:{settings_version}"
        activated_at = _utc_now()
        snapshot = _settings_snapshot_with_activation_metadata(
            snapshot,
            activation_status="active",
            activated_at=activated_at,
            reason=activation_reason,
            source=actor,
        )
        with self.connect() as conn:
            active_row = self._latest_mutable_row(conn, "regime_active_settings", common)
            sequence_version = int(active_row["sequence_version"]) + 1 if active_row else 1
            self._insert(conn, "regime_settings_versions", common, settings_version, snapshot)
            for strategy_id, strategy_settings in _record(snapshot.get("strategy_settings")).items():
                self._insert(
                    conn,
                    "regime_strategy_settings",
                    common,
                    f"strategy-settings-{strategy_id}",
                    {"strategyId": strategy_id, "settingsVersion": settings_version, "settings": strategy_settings},
                )
            active_payload = {
                "activeSettingsVersion": settings_version,
                "previousSettingsVersion": previous_settings_version,
                "activationStatus": "active",
                "activationTimestamp": activated_at,
                "reasonForActivationOrRollback": activation_reason,
                "settingsSnapshot": snapshot,
                "settingsSource": REGIME_SETTINGS_AUTHORITATIVE_SOURCE,
            }
            self._insert(conn, "regime_active_settings", common, "active-settings", active_payload, sequence_version=sequence_version)
            self._insert(
                conn,
                "regime_runtime_events",
                {**common, "decision_id": audit_id},
                audit_id,
                {
                    "eventType": "settings_activation_audit",
                    "actor": actor,
                    "timestamp": activated_at,
                    "previousSettingsVersion": previous_settings_version,
                    "newSettingsVersion": settings_version,
                    "activationStatus": "active",
                    "activationTimestamp": activated_at,
                    "reasonForActivationOrRollback": activation_reason,
                    "settingsSnapshot": snapshot,
                },
            )
        return {
            "activated": True,
            "algorithmId": REGIME_ALGORITHM_ID,
            "identity": identity,
            "previousSettingsVersion": previous_settings_version,
            "settingsVersion": settings_version,
            "settingsSnapshot": snapshot,
            "flatSettings": flatten_regime_trading_settings(snapshot),
            "settingsSource": REGIME_SETTINGS_AUTHORITATIVE_SOURCE,
        }

    def latest_regime_ml_promotion_evidence(self, artifact_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM regime_ml_artifacts
                WHERE algorithm_id = 'regime'
                ORDER BY created_at DESC
                """
            ).fetchall()
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if str(payload.get("artifact_id") or payload.get("artifactId") or "") == artifact_id:
                payload["trusted_backend_record"] = bool(payload.get("trusted_backend_record"))
                return payload
        return None

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
            "ownershipKeyColumns": REGIME_OWNERSHIP_KEY_COLUMNS,
            "ownedVersionColumns": REGIME_VERSION_COLUMNS,
            "mutableStateTables": REGIME_MUTABLE_STATE_TABLES,
            "processingStatusTables": REGIME_PROCESSING_STATUS_TABLES,
            "missingSharedAttributionColumns": missing_shared_columns,
            "missingOwnedVersionColumns": missing_owned_version_columns,
            "passed": not any(missing_shared_columns.values()) and not any(missing_owned_version_columns.values()),
        }

    def _insert(
        self,
        conn: sqlite3.Connection,
        table: str,
        common: dict[str, str | None],
        suffix: str,
        payload: Any,
        *,
        processing_status: str = "recorded",
        sequence_version: int | None = None,
    ) -> None:
        if table not in REGIME_PERSISTENCE_TABLES:
            raise ValueError(f"Unknown Regime persistence table: {table}")
        _validate_common_metadata(common)
        attribution = _attribution_metadata(common, payload)
        if table == "regime_runtime_events" and isinstance(payload, dict):
            payload = {"algorithmId": REGIME_ALGORITHM_ID, **payload}
            if isinstance(payload.get("payload"), dict):
                payload["payload"] = {"algorithmId": REGIME_ALGORITHM_ID, **payload["payload"]}
        payload_json = json.dumps(sanitize_persistence_payload(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        record_id = _record_id(table, common["decision_id"] or "", suffix, payload_json)
        conn.execute(
            f"""
            INSERT INTO {table} (
                record_id, algorithm_id, algorithm_instance_id, account_id, runtime_mode,
                algorithm_version, settings_version, strategy_version, profile_version,
                model_version, timestamp, event_timestamp, symbol, data_timestamp,
                decision_id, order_id, order_intent_id, broker_order_id, position_id,
                trade_id, processing_status, sequence_version, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO NOTHING
            """,
            (
                record_id,
                common["algorithm_id"],
                common["algorithm_instance_id"],
                common["account_id"],
                common["runtime_mode"],
                common["algorithm_version"],
                common["settings_version"],
                common["strategy_version"],
                common["profile_version"],
                common["model_version"],
                common["timestamp"],
                common["event_timestamp"],
                common["symbol"],
                common["data_timestamp"],
                common["decision_id"],
                common["order_id"],
                attribution["order_intent_id"],
                attribution["broker_order_id"],
                attribution["position_id"],
                attribution["trade_id"],
                processing_status,
                sequence_version or int(common.get("sequence_version") or 1),
                payload_json,
            ),
        )
        self._latest_inserted_table = table

    def _latest_mutable_row(self, conn: sqlite3.Connection, table: str, common: dict[str, str | None]) -> sqlite3.Row | None:
        if table not in REGIME_MUTABLE_STATE_TABLES:
            raise ValueError(f"Regime table is not mutable state: {table}")
        return conn.execute(
            f"""
            SELECT sequence_version, payload_json
            FROM {table}
            WHERE algorithm_id = 'regime'
              AND algorithm_instance_id = ?
              AND account_id = ?
              AND runtime_mode = ?
              AND symbol = ?
            ORDER BY sequence_version DESC, created_at DESC
            LIMIT 1
            """,
            (common["algorithm_instance_id"], common["account_id"], common["runtime_mode"], common["symbol"]),
        ).fetchone()

    def _latest_inventory_snapshot(self, conn: sqlite3.Connection, identity: dict[str, Any], common: dict[str, str | None]) -> dict[str, Any]:
        row = self._latest_mutable_row(conn, "regime_inventory_snapshots", common)
        return _row_payload(row) if row else _base_inventory_snapshot(identity, common)

    def _inventory_event_exists(self, conn: sqlite3.Connection, common: dict[str, str | None], inventory_event_id: str) -> bool:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM regime_inventory_events
            WHERE algorithm_id = 'regime'
              AND algorithm_instance_id = ?
              AND account_id = ?
              AND runtime_mode = ?
              AND symbol = ?
            ORDER BY rowid DESC
            """,
            (common["algorithm_instance_id"], common["account_id"], common["runtime_mode"], common["symbol"]),
        ).fetchall()
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if isinstance(payload, dict) and str(payload.get("inventoryEventId") or "") == inventory_event_id:
                return True
        return False

    def _insert_inventory_event_and_snapshot(
        self,
        conn: sqlite3.Connection,
        common: dict[str, str | None],
        event: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        processing_status: str,
    ) -> None:
        event_version = int(event.get("stateVersion") or snapshot.get("stateVersion") or 1)
        event_common = {
            **common,
            "decision_id": str(event.get("decisionId") or common.get("decision_id") or event.get("inventoryEventId")),
            "order_id": _string_or_none(event.get("orderId") or common.get("order_id")),
            "order_intent_id": _string_or_none(event.get("orderIntentId") or common.get("order_intent_id")),
            "broker_order_id": _string_or_none(event.get("brokerOrderId") or common.get("broker_order_id")),
            "position_id": _string_or_none(event.get("positionId") or snapshot.get("positionId")),
            "trade_id": _string_or_none(event.get("tradeId") or snapshot.get("tradeId")),
        }
        self._insert(
            conn,
            "regime_inventory_events",
            event_common,
            str(event["inventoryEventId"]),
            event,
            processing_status=processing_status,
            sequence_version=event_version,
        )
        self._insert(
            conn,
            "regime_inventory_snapshots",
            event_common,
            f"inventory-snapshot-{snapshot['stateVersion']}",
            snapshot,
            processing_status=str(snapshot.get("inventoryStatus") or "current"),
            sequence_version=int(snapshot["stateVersion"]),
        )

    def _rebuild_inventory_snapshot_from_events(
        self,
        conn: sqlite3.Connection,
        identity: dict[str, Any],
        common: dict[str, str | None],
    ) -> dict[str, Any]:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM regime_inventory_events
            WHERE algorithm_id = 'regime'
              AND algorithm_instance_id = ?
              AND account_id = ?
              AND runtime_mode = ?
              AND symbol = ?
            ORDER BY sequence_version ASC, rowid ASC
            """,
            (common["algorithm_instance_id"], common["account_id"], common["runtime_mode"], common["symbol"]),
        ).fetchall()
        snapshot = _base_inventory_snapshot(identity, common)
        for row in rows:
            event = json.loads(str(row["payload_json"]))
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("eventType") or "")
            if event_type in {"broker_fill", "broker_correction"}:
                snapshot = _apply_fill_to_inventory_snapshot(snapshot, event)
            elif event_type == "broker_order_status":
                snapshot = _apply_order_status_to_inventory_snapshot(snapshot, event)
        return snapshot

    def _copy_broker_observation_in_transaction(self, conn: sqlite3.Connection, observation: dict[str, Any], common: dict[str, str | None]) -> str | None:
        table = _regime_ledger_table_for_observation(observation)
        if table is None:
            self._insert(conn, "regime_reconciliation_events", common, "reconciliation-observation", observation, processing_status=str(observation.get("processingStatus") or "observed"))
            return "regime_reconciliation_events"
        enriched = _stable_regime_ledger_payload(table, observation, common)
        processing_status = str(observation.get("processingStatus") or "observed")
        if _broker_observation_duplicate_exists(conn, table, common, enriched, processing_status):
            return None
        try:
            self._insert(conn, table, common, str(enriched.get("stableId") or table), enriched, processing_status=processing_status)
        except sqlite3.IntegrityError:
            if _broker_observation_duplicate_exists(conn, table, common, enriched, processing_status):
                return None
            raise
        return table


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
    supplied_algorithm_ids = _supplied_algorithm_ids(snapshot, regime, decision)
    if any(algorithm_id != REGIME_ALGORITHM_ID for algorithm_id in supplied_algorithm_ids):
        return {"algorithm_id": "__invalid__", "algorithm_instance_id": "", "account_id": "", "runtime_mode": "", "symbol": ""}
    algorithm_id = str(
        regime.get("algorithmId")
        or regime.get("algorithm_id")
        or decision.get("algorithmId")
        or decision.get("algorithm_id")
        or snapshot.get("algorithmId")
        or snapshot.get("algorithm_id")
        or REGIME_ALGORITHM_ID
    )
    timestamp = str(regime.get("timestamp") or regime.get("event_timestamp") or decision.get("decisionTimestamp") or decision.get("timestamp") or snapshot.get("capturedAt") or "")
    data_timestamp = str(regime.get("dataTimestamp") or regime.get("data_timestamp") or decision.get("dataTimestamp") or decision.get("data_timestamp") or timestamp)
    symbol = str(regime.get("symbol") or decision.get("symbol") or snapshot.get("symbol") or "SPY").upper()
    decision_id = str(regime.get("decisionId") or regime.get("decision_id") or decision.get("decisionId") or decision.get("decision_id") or f"regime:{symbol}:{data_timestamp}")
    order_intent_id = _string_or_none(
        regime.get("orderIntentId")
        or regime.get("order_intent_id")
        or decision.get("orderIntentId")
        or decision.get("order_intent_id")
        or _record(regime.get("orderIntent")).get("orderIntentId")
        or _record(regime.get("orderIntent")).get("order_intent_id")
        or _record(decision.get("orderIntent")).get("orderIntentId")
        or _record(decision.get("orderIntent")).get("order_intent_id")
    )
    position_id = _string_or_none(regime.get("positionId") or regime.get("position_id") or decision.get("positionId") or decision.get("position_id"))
    trade_id = _string_or_none(regime.get("tradeId") or regime.get("trade_id") or decision.get("tradeId") or decision.get("trade_id"))
    return {
        "algorithm_id": algorithm_id,
        "algorithm_instance_id": str(regime.get("algorithmInstanceId") or regime.get("algorithm_instance_id") or decision.get("algorithmInstanceId") or decision.get("algorithm_instance_id") or "regime-default"),
        "account_id": str(regime.get("accountId") or regime.get("account_id") or decision.get("accountId") or decision.get("account_id") or "default"),
        "runtime_mode": str(regime.get("runtimeMode") or regime.get("runtime_mode") or decision.get("runtimeMode") or decision.get("runtime_mode") or "shadow"),
        "algorithm_version": str(regime.get("algorithmVersion") or regime.get("algorithm_version") or decision.get("algorithmVersion") or decision.get("algorithm_version") or "regime_algorithm_v3_backend_authoritative"),
        "settings_version": str(regime.get("settingsVersion") or regime.get("settings_version") or decision.get("settingsVersion") or decision.get("settings_version") or "regime_base_settings_v2"),
        "strategy_version": str(regime.get("strategyVersion") or regime.get("strategy_version") or decision.get("strategyVersion") or decision.get("strategy_version") or "regime_strategy_catalog_v3_backend"),
        "profile_version": str(regime.get("profileVersion") or regime.get("profile_version") or decision.get("profileVersion") or decision.get("profile_version") or "regime_profile_matrix_v3_backend"),
        "model_version": None if (regime.get("modelVersion") or regime.get("model_version") or decision.get("modelVersion") or decision.get("model_version")) is None else str(regime.get("modelVersion") or regime.get("model_version") or decision.get("modelVersion") or decision.get("model_version")),
        "timestamp": timestamp,
        "event_timestamp": str(regime.get("eventTimestamp") or regime.get("event_timestamp") or decision.get("eventTimestamp") or decision.get("event_timestamp") or timestamp),
        "symbol": symbol,
        "data_timestamp": data_timestamp,
        "decision_id": decision_id,
        "order_id": None if (regime.get("orderId") or regime.get("order_id") or decision.get("orderId") or decision.get("order_id")) is None else str(regime.get("orderId") or regime.get("order_id") or decision.get("orderId") or decision.get("order_id")),
        "order_intent_id": order_intent_id,
        "broker_order_id": _string_or_none(regime.get("brokerOrderId") or regime.get("broker_order_id") or decision.get("brokerOrderId") or decision.get("broker_order_id")),
        "position_id": position_id,
        "trade_id": trade_id,
        "sequence_version": str(regime.get("sequenceVersion") or regime.get("sequence_version") or decision.get("sequenceVersion") or decision.get("sequence_version") or 1),
    }


def _settings_common(identity: dict[str, Any], settings_version: str) -> dict[str, str | None]:
    resolved = regime_settings_identity_from_payload(identity)
    timestamp = _utc_now()
    return {
        "algorithm_id": REGIME_ALGORITHM_ID,
        "algorithm_instance_id": resolved["algorithmInstanceId"],
        "account_id": resolved["accountId"],
        "runtime_mode": resolved["runtimeMode"],
        "algorithm_version": "regime_algorithm_v3_backend_authoritative",
        "settings_version": settings_version,
        "strategy_version": "regime_strategy_catalog_v3_backend",
        "profile_version": "regime_profile_matrix_v3_backend",
        "model_version": None,
        "timestamp": timestamp,
        "event_timestamp": timestamp,
        "symbol": resolved["symbol"],
        "data_timestamp": timestamp,
        "decision_id": f"regime-settings:{settings_version}",
        "order_id": None,
        "order_intent_id": None,
        "broker_order_id": None,
        "position_id": None,
        "trade_id": None,
        "sequence_version": "1",
    }


def _settings_snapshot_with_activation_metadata(
    snapshot: dict[str, Any],
    *,
    activation_status: str,
    activated_at: str | None,
    reason: str,
    source: str,
) -> dict[str, Any]:
    content_hash = str(snapshot.get("contentHash") or snapshot.get("settingsHash") or snapshot.get("configurationHash") or "")
    settings_version = str(snapshot.get("settingsVersion") or snapshot.get("immutableVersionId") or "")
    return {
        **snapshot,
        "immutableVersionId": settings_version,
        "contentHash": content_hash,
        "settingsHash": str(snapshot.get("settingsHash") or content_hash),
        "activationStatus": activation_status,
        "activationTimestamp": activated_at,
        "activatedAt": activated_at,
        "createdSource": str(source),
        "sourceMetadata": {
            **_record(snapshot.get("sourceMetadata")),
            "source": str(_record(snapshot.get("sourceMetadata")).get("source") or source),
            "createdBy": str(snapshot.get("createdBy") or source),
        },
        "reasonForActivationOrRollback": reason,
        "regimeProfileMatrixVersion": str(snapshot.get("regimeProfileMatrixVersion") or snapshot.get("profileVersion") or ""),
    }


def _validate_common_metadata(common: dict[str, str | None]) -> None:
    if common.get("algorithm_id") != REGIME_ALGORITHM_ID:
        raise ValueError("Regime repository rejects non-regime algorithm_id")
    for key in REGIME_OWNERSHIP_KEY_COLUMNS:
        if not common.get(key):
            raise ValueError(f"Regime repository requires ownership key: {key}")


def _require_full_ownership_identity(identity: dict[str, Any] | None) -> None:
    if not isinstance(identity, dict):
        raise ValueError("Regime repository requires explicit ownership identity")
    required = {
        "algorithm_id": ("algorithmId", "algorithm_id"),
        "algorithm_instance_id": ("algorithmInstanceId", "algorithm_instance_id"),
        "account_id": ("accountId", "account_id"),
        "runtime_mode": ("runtimeMode", "runtime_mode"),
        "symbol": ("symbol",),
    }
    missing = [canonical for canonical, aliases in required.items() if not any(identity.get(alias) for alias in aliases)]
    if missing:
        raise ValueError(f"Regime repository requires full ownership key: {', '.join(missing)}")


def _require_matching_ownership_identity(identity: dict[str, Any], payload: dict[str, Any]) -> None:
    aliases = {
        "algorithmInstanceId": ("algorithmInstanceId", "algorithm_instance_id"),
        "accountId": ("accountId", "account_id"),
        "runtimeMode": ("runtimeMode", "runtime_mode"),
        "symbol": ("symbol",),
    }
    mismatches: list[str] = []
    for expected_key, candidate_keys in aliases.items():
        expected = str(identity.get(expected_key) or "")
        supplied = ""
        for key in candidate_keys:
            value = payload.get(key)
            if value is not None and str(value) != "":
                supplied = str(value)
                break
        if not supplied:
            continue
        if expected_key == "symbol":
            mismatch = supplied.upper() != expected.upper()
        else:
            mismatch = supplied != expected
        if mismatch:
            mismatches.append(expected_key)
    if mismatches:
        raise ValueError(f"Regime repository rejects cross-identity inventory observation: {', '.join(mismatches)}")


def _supplied_algorithm_ids(*records: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for record in records:
        for key in ("algorithmId", "algorithm_id"):
            value = record.get(key)
            if value is not None:
                values.append(str(value))
    return tuple(values)


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


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
        "broker_order_id": _string_or_none(record.get("brokerOrderId") or record.get("broker_order_id") or record.get("brokerOrderID") or common.get("broker_order_id")),
        "position_id": _string_or_none(record.get("positionId") or record.get("position_id") or common.get("position_id")),
        "trade_id": _string_or_none(record.get("tradeId") or record.get("trade_id") or common.get("trade_id")),
    }


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _base_inventory_snapshot(identity: dict[str, Any], common: dict[str, str | None]) -> dict[str, Any]:
    return {
        "algorithmId": REGIME_ALGORITHM_ID,
        "algorithmInstanceId": common["algorithm_instance_id"],
        "accountId": common["account_id"],
        "runtimeMode": common["runtime_mode"],
        "symbol": common["symbol"],
        "quantity": 0,
        "averageEntryPrice": 0.0,
        "realizedPnl": 0.0,
        "unrealizedPnl": 0.0,
        "reservedCash": 0.0,
        "reservedRisk": 0.0,
        "openOrderQuantity": 0,
        "positionId": None,
        "tradeId": None,
        "lastBrokerReconciliationTime": None,
        "stateVersion": 0,
        "inventoryStatus": "flat",
        "lastDecisionId": None,
        "lastOrderIntentId": None,
        "lastOrderId": None,
        "lastBrokerOrderId": None,
        "lastFillId": None,
        "lastInventoryEventId": None,
    }


def _inventory_event_from_fill(identity: dict[str, Any], fill: dict[str, Any], *, settings_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    quantity = abs(_int(fill.get("filledQuantity") or fill.get("filled_quantity") or fill.get("quantity")))
    price = _float(fill.get("averageFillPrice") or fill.get("average_fill_price") or fill.get("fillPrice") or fill.get("price"))
    side = _normal_inventory_side(fill.get("side") or fill.get("orderSide") or "Buy")
    fill_id = str(fill.get("fillId") or fill.get("fill_id") or f"{fill.get('orderIntentId') or fill.get('order_intent_id')}:{fill.get('filledAt') or fill.get('timestamp')}:{quantity}:{price}")
    event_id = f"regime-inventory-fill-{_stable_snapshot_key(fill_id)}"
    return {
        "algorithmId": REGIME_ALGORITHM_ID,
        "eventType": "broker_fill",
        "inventoryEventId": event_id,
        "fillId": fill_id,
        "decisionId": fill.get("decisionId") or fill.get("decision_id"),
        "orderIntentId": fill.get("orderIntentId") or fill.get("order_intent_id"),
        "orderId": fill.get("orderId") or fill.get("order_id"),
        "brokerOrderId": fill.get("brokerOrderId") or fill.get("broker_order_id"),
        "positionId": fill.get("positionId") or fill.get("position_id"),
        "tradeId": fill.get("tradeId") or fill.get("trade_id"),
        "symbol": str(fill.get("symbol") or identity.get("symbol") or "SPY").upper(),
        "runtimeMode": fill.get("runtimeMode") or fill.get("runtime_mode") or identity.get("runtimeMode"),
        "side": side,
        "signedQuantity": quantity if side == "Buy" else -quantity,
        "quantity": quantity,
        "price": price,
        "timestamp": fill.get("filledAt") or fill.get("timestamp") or _utc_now(),
        "settingsVersion": (settings_snapshot or {}).get("settingsVersion") or fill.get("settingsVersion") or fill.get("settings_version"),
        "profileVersion": (settings_snapshot or {}).get("profileVersion") or fill.get("profileVersion") or fill.get("profile_version"),
        "submittedQuantity": _int(fill.get("submittedQuantity") or fill.get("submitted_quantity")),
        "brokerStatus": fill.get("status") or fill.get("fillStatus") or fill.get("processingStatus"),
    }


def _inventory_event_from_order_status(observation: dict[str, Any]) -> dict[str, Any]:
    ack = _record(observation.get("brokerAck"))
    status = str(observation.get("status") or observation.get("processingStatus") or ack.get("status") or "observed").lower()
    order_id = observation.get("orderId") or observation.get("order_id") or observation.get("brokerOrderId") or observation.get("broker_order_id") or ack.get("brokerOrderId")
    order_intent_id = observation.get("orderIntentId") or observation.get("order_intent_id")
    event_key = f"{order_id}:{order_intent_id}:{status}:{observation.get('timestamp') or ack.get('submittedAt') or ''}"
    return {
        "algorithmId": REGIME_ALGORITHM_ID,
        "eventType": "broker_order_status",
        "inventoryEventId": f"regime-inventory-order-{_stable_snapshot_key(event_key)}",
        "orderStatus": status,
        "decisionId": observation.get("decisionId") or observation.get("decision_id"),
        "orderIntentId": order_intent_id,
        "orderId": order_id,
        "brokerOrderId": observation.get("brokerOrderId") or observation.get("broker_order_id") or ack.get("brokerOrderId"),
        "positionId": observation.get("positionId") or observation.get("position_id"),
        "tradeId": observation.get("tradeId") or observation.get("trade_id"),
        "quantity": _int(observation.get("quantity") or observation.get("submittedQuantity") or ack.get("quantity")),
        "remainingQuantity": _int(observation.get("remainingQuantity") or observation.get("remaining_quantity") or ack.get("remainingQuantity")),
        "timestamp": observation.get("timestamp") or ack.get("submittedAt") or _utc_now(),
        "rawStatus": observation.get("status") or ack.get("status"),
    }


def _inventory_event_from_broker_correction(identity: dict[str, Any], correction: dict[str, Any]) -> dict[str, Any]:
    delta = _int(correction.get("deltaQuantity") or correction.get("delta_quantity") or correction.get("quantity"))
    side = "Buy" if delta >= 0 else "Sell"
    correction_id = str(correction.get("correctionId") or correction.get("correction_id") or f"{correction.get('brokerOrderId')}:{delta}:{correction.get('timestamp')}")
    return {
        **_inventory_event_from_fill(
            identity,
            {
                **correction,
                "fillId": correction_id,
                "filledQuantity": abs(delta),
                "side": side,
                "averageFillPrice": correction.get("price") or correction.get("averageFillPrice") or 0,
            },
        ),
        "eventType": "broker_correction",
        "inventoryEventId": f"regime-inventory-correction-{_stable_snapshot_key(correction_id)}",
        "correctionId": correction_id,
    }


def _apply_fill_to_inventory_snapshot(snapshot: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    previous_quantity = _int(snapshot.get("quantity"))
    previous_average = _float(snapshot.get("averageEntryPrice"))
    fill_signed_quantity = _int(event.get("signedQuantity"))
    fill_quantity = abs(fill_signed_quantity)
    fill_price = _float(event.get("price"))
    if fill_quantity <= 0:
        return snapshot
    new_quantity = previous_quantity + fill_signed_quantity
    realized_delta = 0.0
    if previous_quantity == 0 or (previous_quantity > 0 and fill_signed_quantity > 0) or (previous_quantity < 0 and fill_signed_quantity < 0):
        total_quantity = abs(previous_quantity) + fill_quantity
        average = ((previous_average * abs(previous_quantity)) + (fill_price * fill_quantity)) / total_quantity if total_quantity else 0.0
    else:
        closing_quantity = min(abs(previous_quantity), fill_quantity)
        realized_delta = (fill_price - previous_average) * closing_quantity * (1 if previous_quantity > 0 else -1)
        if new_quantity == 0:
            average = 0.0
        elif (previous_quantity > 0 and new_quantity > 0) or (previous_quantity < 0 and new_quantity < 0):
            average = previous_average
        else:
            average = fill_price
    state_version = _int(snapshot.get("stateVersion")) + 1
    open_order_quantity = max(0, _int(snapshot.get("openOrderQuantity")) - fill_quantity)
    position_id = event.get("positionId") or snapshot.get("positionId") or f"regime-position-{event.get('symbol', 'SPY')}-{event.get('orderIntentId') or event.get('fillId')}"
    trade_id = event.get("tradeId") or snapshot.get("tradeId") or f"regime-trade-{event.get('symbol', 'SPY')}-{event.get('orderIntentId') or event.get('fillId')}"
    return {
        **snapshot,
        "quantity": new_quantity,
        "averageEntryPrice": round(average, 8),
        "realizedPnl": round(_float(snapshot.get("realizedPnl")) + realized_delta, 8),
        "unrealizedPnl": 0.0 if new_quantity == 0 else round((fill_price - average) * abs(new_quantity) * (1 if new_quantity > 0 else -1), 8),
        "openOrderQuantity": open_order_quantity,
        "positionId": None if new_quantity == 0 else position_id,
        "tradeId": trade_id,
        "inventoryStatus": "flat" if new_quantity == 0 else "open",
        "stateVersion": state_version,
        "lastDecisionId": event.get("decisionId") or snapshot.get("lastDecisionId"),
        "lastOrderIntentId": event.get("orderIntentId") or snapshot.get("lastOrderIntentId"),
        "lastOrderId": event.get("orderId") or snapshot.get("lastOrderId"),
        "lastBrokerOrderId": event.get("brokerOrderId") or snapshot.get("lastBrokerOrderId"),
        "lastFillId": event.get("fillId"),
        "lastInventoryEventId": event.get("inventoryEventId"),
        "lastUpdatedAt": event.get("timestamp") or _utc_now(),
    }


def _apply_order_status_to_inventory_snapshot(snapshot: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    status = str(event.get("orderStatus") or "").lower()
    terminal = {"cancelled", "canceled", "rejected", "expired"}
    open_status = {"acknowledged", "accepted", "new", "partially_filled", "replaced", "submitted"}
    next_open_quantity = _int(snapshot.get("openOrderQuantity"))
    if status in terminal:
        next_open_quantity = 0
    elif status in open_status:
        quantity = _int(event.get("remainingQuantity")) or _int(event.get("quantity"))
        next_open_quantity = max(next_open_quantity, quantity)
    state_version = _int(snapshot.get("stateVersion")) + 1
    return {
        **snapshot,
        "openOrderQuantity": next_open_quantity,
        "stateVersion": state_version,
        "lastDecisionId": event.get("decisionId") or snapshot.get("lastDecisionId"),
        "lastOrderIntentId": event.get("orderIntentId") or snapshot.get("lastOrderIntentId"),
        "lastOrderId": event.get("orderId") or snapshot.get("lastOrderId"),
        "lastBrokerOrderId": event.get("brokerOrderId") or snapshot.get("lastBrokerOrderId"),
        "lastInventoryEventId": event.get("inventoryEventId"),
        "lastUpdatedAt": event.get("timestamp") or _utc_now(),
    }


def _inventory_snapshots_equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    keys = (
        "quantity",
        "averageEntryPrice",
        "realizedPnl",
        "unrealizedPnl",
        "reservedCash",
        "reservedRisk",
        "openOrderQuantity",
        "positionId",
        "tradeId",
        "stateVersion",
        "lastDecisionId",
        "lastOrderIntentId",
        "lastOrderId",
        "lastBrokerOrderId",
        "lastFillId",
        "lastInventoryEventId",
    )
    return {key: left.get(key) for key in keys} == {key: right.get(key) for key in keys}


def _broker_inventory_discrepancies(snapshot: dict[str, Any], broker_positions: list[dict[str, Any]]) -> list[str]:
    own_quantity = _int(snapshot.get("quantity"))
    own_position_id = str(snapshot.get("positionId") or "")
    discrepancies: list[str] = []
    regime_positions = [position for position in broker_positions if str(position.get("algorithmId") or position.get("algorithm_id") or "") == REGIME_ALGORITHM_ID]
    unattributed_positions = [
        position
        for position in broker_positions
        if str(position.get("algorithmId") or position.get("algorithm_id") or "") == ""
        and _int(position.get("quantity") or position.get("filledQuantity")) != 0
    ]
    if unattributed_positions:
        discrepancies.append("regime.inventory.unattributed_broker_position_requires_manual_review")
    if not regime_positions and own_quantity != 0:
        discrepancies.append("regime.inventory.broker_missing_open_position")
    for position in regime_positions:
        broker_position_id = str(position.get("positionId") or position.get("position_id") or "")
        broker_quantity = _int(position.get("quantity") or position.get("filledQuantity"))
        if own_position_id and broker_position_id and broker_position_id != own_position_id:
            discrepancies.append(f"regime.inventory.position_id_mismatch:{broker_position_id}")
        if broker_quantity != own_quantity:
            discrepancies.append(f"regime.inventory.quantity_mismatch:{broker_position_id or snapshot.get('symbol')}")
    return discrepancies


def _normal_inventory_side(value: Any) -> str:
    text = str(getattr(value, "value", value)).upper()
    return "Sell" if text in {"SELL", "SHORT"} else "Buy"


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _backend_result_as_regime(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("algorithmId") != REGIME_ALGORITHM_ID or not isinstance(snapshot.get("decision"), dict):
        return {}
    decision = _record(snapshot.get("decision"))
    classification = _record(decision.get("raw_classification"))
    confirmed = _record(decision.get("confirmed_state"))
    broker_submission = _record(snapshot.get("brokerSubmission"))
    if broker_submission and broker_submission.get("submitted") is False and not (
        broker_submission.get("brokerOrderId") or broker_submission.get("broker_order_id") or broker_submission.get("brokerOrder")
    ):
        broker_submission = {}
    return {
        "algorithmId": REGIME_ALGORITHM_ID,
        "algorithmInstanceId": snapshot.get("algorithmInstanceId") or "regime-default",
        "accountId": snapshot.get("accountId") or "default",
        "runtimeMode": snapshot.get("runtimeMode") or "shadow",
        "algorithmVersion": decision.get("algorithm_version"),
        "settingsVersion": decision.get("settings_version"),
        "strategyVersion": decision.get("strategy_catalog_version"),
        "profileVersion": decision.get("profile_version"),
        "timestamp": classification.get("timestamp"),
        "dataTimestamp": classification.get("timestamp"),
        "symbol": decision.get("symbol"),
        "decisionId": decision.get("decision_id"),
        "rawClassification": classification,
        "confirmedState": confirmed,
        "strategyOutputs": _list(decision.get("strategy_outputs")),
        "familyAggregation": [
            {"family": family, "score": score}
            for family, score in _record(decision.get("family_scores")).items()
        ],
        "effectiveSettings": _record(decision.get("effective_settings")),
        "settingsSnapshot": _record(snapshot.get("settingsSnapshot")),
        "localRiskResult": _record(snapshot.get("localRiskResult")),
        "orderIntent": _record(snapshot.get("orderIntent")),
        "hypotheticalFills": _list(snapshot.get("hypotheticalFills")),
        "globalGateOutcome": _record(snapshot.get("globalRiskApproval")),
        "brokerReconciliationResult": broker_submission,
    }


def _sequence_version(payload: Any) -> int:
    record = _record(payload)
    try:
        return max(1, int(record.get("sequenceVersion") or record.get("sequence_version") or 1))
    except (TypeError, ValueError):
        return 1


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(str(row["payload_json"]))
    if isinstance(payload, dict):
        payload["sequenceVersion"] = int(row["sequence_version"])
        return payload
    return {"payload": payload, "sequenceVersion": int(row["sequence_version"])}


def _next_sequence_version_for_id(
    conn: sqlite3.Connection,
    table: str,
    common: dict[str, str | None],
    id_column: str,
    id_value: str,
) -> int:
    row = conn.execute(
        f"""
        SELECT MAX(sequence_version) AS latest_sequence
        FROM {table}
        WHERE algorithm_id = 'regime'
          AND algorithm_instance_id = ?
          AND account_id = ?
          AND runtime_mode = ?
          AND symbol = ?
          AND {id_column} = ?
        """,
        (
            common["algorithm_instance_id"],
            common["account_id"],
            common["runtime_mode"],
            common["symbol"],
            id_value,
        ),
    ).fetchone()
    return int(row["latest_sequence"] or 0) + 1


def _regime_ledger_table_for_observation(observation: dict[str, Any]) -> str | None:
    kind = str(observation.get("type") or observation.get("kind") or observation.get("observationType") or "").lower()
    if "fill" in kind:
        return "regime_fills"
    if "position" in kind:
        return "regime_positions"
    if "trade" in kind:
        return "regime_trades"
    if "order" in kind:
        return "regime_orders"
    return None


def _broker_observation_duplicate_exists(
    conn: sqlite3.Connection,
    table: str,
    common: dict[str, str | None],
    payload: dict[str, Any],
    processing_status: str,
) -> bool:
    attribution = _attribution_metadata(common, payload)
    if table == "regime_orders":
        order_id = _string_or_none(payload.get("orderId") or payload.get("order_id") or common.get("order_id"))
        if not order_id:
            return False
        row = conn.execute(
            """
            SELECT record_id
            FROM regime_orders
            WHERE algorithm_id = 'regime'
              AND algorithm_instance_id = ?
              AND account_id = ?
              AND runtime_mode = ?
              AND symbol = ?
              AND order_id = ?
              AND processing_status = ?
            LIMIT 1
            """,
            (
                common["algorithm_instance_id"],
                common["account_id"],
                common["runtime_mode"],
                common["symbol"],
                order_id,
                processing_status,
            ),
        ).fetchone()
        return row is not None
    if table == "regime_fills":
        broker_order_id = attribution["broker_order_id"]
        trade_id = attribution["trade_id"]
        if not broker_order_id or not trade_id:
            return False
        row = conn.execute(
            """
            SELECT record_id
            FROM regime_fills
            WHERE algorithm_id = 'regime'
              AND algorithm_instance_id = ?
              AND account_id = ?
              AND runtime_mode = ?
              AND symbol = ?
              AND broker_order_id = ?
              AND trade_id = ?
              AND processing_status = ?
            LIMIT 1
            """,
            (
                common["algorithm_instance_id"],
                common["account_id"],
                common["runtime_mode"],
                common["symbol"],
                broker_order_id,
                trade_id,
                processing_status,
            ),
        ).fetchone()
        return row is not None
    return False


def _stable_regime_ledger_payload(table: str, observation: dict[str, Any], common: dict[str, str | None]) -> dict[str, Any]:
    payload = dict(observation)
    if table == "regime_positions":
        stable_id = _string_or_none(payload.get("positionId") or payload.get("position_id")) or _stable_id("regime-position", common, payload)
        payload["positionId"] = stable_id
    elif table in {"regime_trades", "regime_backtest_trades"}:
        stable_id = _string_or_none(payload.get("tradeId") or payload.get("trade_id")) or _stable_id("regime-trade", common, payload)
        payload["tradeId"] = stable_id
    elif table == "regime_orders":
        stable_id = _string_or_none(payload.get("orderId") or payload.get("order_id") or payload.get("brokerOrderId") or payload.get("broker_order_id")) or _stable_id("regime-order", common, payload)
        payload["orderId"] = stable_id
    else:
        stable_id = _stable_id(table.replace("_", "-"), common, payload)
    if table == "regime_fills":
        payload["fillId"] = _string_or_none(payload.get("fillId") or payload.get("fill_id")) or stable_id
    payload["stableId"] = stable_id
    return payload


def _stable_regime_trade_id(trade: dict[str, Any], run_id: str, index: int) -> str:
    return _string_or_none(trade.get("tradeId") or trade.get("trade_id")) or f"regime-trade-{hashlib.sha256(f'{run_id}:{index}:{trade}'.encode('utf-8')).hexdigest()[:16]}"


def _backtest_job_common(job: dict[str, Any]) -> dict[str, str | None]:
    timestamp = str(job.get("updatedAt") or job.get("heartbeatAt") or job.get("queuedAt") or _utc_now())
    job_id = str(job.get("jobId") or job.get("job_id") or job.get("decisionId") or f"regime-backtest-job:{_stable_snapshot_key(str(job))}")
    settings_version = str(job.get("settingsVersion") or _record(job.get("manifest")).get("settingsVersion") or "regime_base_settings_v2")
    return {
        "algorithm_id": REGIME_ALGORITHM_ID,
        "algorithm_instance_id": str(job.get("algorithmInstanceId") or job.get("algorithm_instance_id") or "regime-default"),
        "account_id": str(job.get("accountId") or job.get("account_id") or "default"),
        "runtime_mode": "backtest",
        "algorithm_version": str(job.get("algorithmVersion") or job.get("codeVersion") or "regime_algorithm_v3_backend_authoritative"),
        "settings_version": settings_version,
        "strategy_version": str(job.get("strategyVersion") or "regime_strategy_catalog_v3_backend"),
        "profile_version": str(job.get("profileVersion") or "regime_profile_matrix_v3_backend"),
        "model_version": None,
        "timestamp": timestamp,
        "event_timestamp": timestamp,
        "symbol": str(job.get("symbol") or "SPY").upper(),
        "data_timestamp": str(job.get("dataTimestamp") or timestamp),
        "decision_id": job_id,
        "order_id": None,
        "order_intent_id": None,
        "broker_order_id": None,
        "position_id": None,
        "trade_id": None,
        "sequence_version": str(job.get("sequenceVersion") or 1),
    }


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def _stable_id(prefix: str, common: dict[str, str | None], payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "instance": common.get("algorithm_instance_id"),
                "account": common.get("account_id"),
                "mode": common.get("runtime_mode"),
                "symbol": common.get("symbol"),
                "decision": common.get("decision_id"),
                "payload": payload,
            },
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _stable_snapshot_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _validate_regime_ml_backend_evidence_source(evidence: dict[str, Any]) -> tuple[bool, str]:
    source = str(evidence.get("backend_evidence_source") or evidence.get("backendEvidenceSource") or evidence.get("source") or "").strip()
    if source not in REGIME_ML_TRUSTED_BACKEND_EVIDENCE_SOURCES:
        return False, "untrusted_backend_evidence_source"
    if str(evidence.get("requestSource") or evidence.get("request_source") or "").lower() in {"frontend", "api", "client", "browser"}:
        return False, "frontend_supplied_evidence_rejected"
    required = (
        ("replay_evidence_id", "replayEvidenceId"),
        ("walk_forward_evidence_id", "walkForwardEvidenceId"),
        ("holdout_evidence_id", "holdoutEvidenceId"),
        ("paper_stability_evidence_id", "paperStabilityEvidenceId"),
        ("promotion_audit_id", "promotionAuditId"),
        ("rollback_artifact_id", "rollbackArtifactId"),
    )
    for snake, camel in required:
        if not str(evidence.get(snake) or evidence.get(camel) or "").strip():
            return False, f"missing_{snake}"
    return True, "trusted_backend_evidence"


__all__ = [
    "REGIME_PERSISTENCE_MIGRATION_VERSION",
    "REGIME_OWNED_TABLES",
    "REGIME_PERSISTENCE_TABLES",
    "REGIME_SHARED_ATTRIBUTED_TABLES",
    "REGIME_SHARED_ATTRIBUTION_COLUMNS",
    "REGIME_OWNERSHIP_KEY_COLUMNS",
    "REGIME_MUTABLE_STATE_TABLES",
    "REGIME_PROCESSING_STATUS_TABLES",
    "REGIME_VERSION_COLUMNS",
    "RegimeSqliteRepository",
    "migrate_regime_sqlite_database",
    "sanitize_persistence_payload",
]
