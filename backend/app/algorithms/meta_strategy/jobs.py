"""Durable Meta-Strategy job and event queues."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.ownership import META_STRATEGY_DEFAULT_CAPITAL_PARTITION
from backend.app.algorithms.meta_strategy.repository import apply_meta_strategy_persistence_migrations, persist_meta_strategy_projection_record
from backend.app.algorithms.meta_strategy.versions import (
    META_STRATEGY_ALGORITHM_VERSION,
    META_STRATEGY_FEATURE_SCHEMA_VERSION,
    META_STRATEGY_MODEL_VERSION,
    META_STRATEGY_STRATEGY_CATALOG_VERSION,
)
from backend.app.database import _sqlite_path


META_STRATEGY_JOB_MIGRATION_VERSION = "meta_strategy_jobs_007"
META_STRATEGY_WORKER_DECISION_SCHEMA_VERSION = "meta_strategy_worker_decision_v1"
META_STRATEGY_EXECUTION_OUTBOX_SCHEMA_VERSION = "meta_strategy_execution_outbox_v1"
META_STRATEGY_FINALIZED_CANDLE_OUTCOME_SCHEMA_VERSION = "meta_strategy_finalized_candle_outcome_v1"
META_STRATEGY_FINALIZED_CANDLE_TERMINAL_OUTCOMES: frozenset[str] = frozenset(
    {
        "NO_DECISION",
        "HOLD",
        "BLOCKED",
        "ORDER_PROPOSED",
        "ORDER_SUBMITTED",
        "ORDER_REJECTED",
        "RECONCILIATION_REQUIRED",
    }
)
META_STRATEGY_JOB_QUEUES: frozenset[str] = frozenset(
    {
        "finalised_bar_decisions",
        "order_submission",
        "order_reconciliation",
        "stale_order_handling",
        "inventory_reconciliation",
        "position_management",
        "training",
        "backtesting",
        "replay",
        "model_evaluation",
        "promotion",
        "reporting",
    }
)
META_STRATEGY_JOB_TYPE_TO_QUEUE: dict[str, str] = {
    "finalised_bar_decision": "finalised_bar_decisions",
    "order_submission": "order_submission",
    "order_reconciliation": "order_reconciliation",
    "stale_order_handling": "stale_order_handling",
    "inventory_reconciliation": "inventory_reconciliation",
    "position_management": "position_management",
    "training": "training",
    "backtesting": "backtesting",
    "replay": "replay",
    "deterministic_replay": "replay",
    "walk_forward_evaluation": "backtesting",
    "holdout_evaluation": "backtesting",
    "cost_slippage_analysis": "model_evaluation",
    "model_evaluation": "model_evaluation",
    "model_inference_validation": "model_evaluation",
    "paper_stability_evaluation": "model_evaluation",
    "settings_promotion": "promotion",
    "model_promotion": "promotion",
    "reporting": "reporting",
    "report_generation": "reporting",
}
META_STRATEGY_DEFAULT_QUEUE_CONCURRENCY_LIMITS: dict[str, int] = {
    "finalised_bar_decisions": 1,
    "order_submission": 1,
    "order_reconciliation": 1,
    "stale_order_handling": 1,
    "inventory_reconciliation": 1,
    "position_management": 1,
    "training": 1,
    "backtesting": 1,
    "replay": 1,
    "model_evaluation": 1,
    "promotion": 1,
    "reporting": 2,
}
TERMINAL_META_STRATEGY_JOB_STATUSES: frozenset[str] = frozenset({"SUCCEEDED", "FAILED", "DEAD_LETTER", "CANCELLED"})


class MetaStrategyJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRY = "RETRY"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class MetaStrategyJobRecord:
    job_id: str
    algorithm_id: str
    job_type: str
    queue_name: str
    idempotency_key: str
    payload_reference: str
    status: MetaStrategyJobStatus
    priority: int
    attempt_count: int
    max_attempts: int
    next_attempt_at: str
    lease_owner: str | None
    lease_expires_at: str | None
    created_at: str
    started_at: str | None
    updated_at: str
    completed_at: str | None
    result_reference: str | None
    error_category: str | None
    error_details: str | None
    cancellable: bool
    cancel_requested: bool
    duplicate: bool = False


@dataclass(frozen=True)
class MetaStrategyEventRecord:
    event_id: str
    algorithm_id: str
    event_type: str
    queue_name: str
    idempotency_key: str
    payload_reference: str
    job_id: str | None
    status: str
    created_at: str
    duplicate: bool = False


@dataclass(frozen=True)
class MetaStrategyPaperTradingControlRecord:
    algorithm_id: str
    capital_partition_id: str
    new_paper_entries_enabled: bool
    updated_at: str
    updated_by: str
    reason: str
    version: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithm_id,
            "capitalPartitionId": self.capital_partition_id,
            "newPaperEntriesEnabled": self.new_paper_entries_enabled,
            "automaticPaperTradingEnabled": self.new_paper_entries_enabled,
            "paperEntriesAllowed": self.new_paper_entries_enabled,
            "paperOnly": True,
            "liveExecutionEnabled": False,
            "updatedAt": self.updated_at,
            "updatedBy": self.updated_by,
            "reason": self.reason,
            "version": self.version,
            "reasonCodes": (
                "meta_strategy.paper_control.new_entries_enabled"
                if self.new_paper_entries_enabled
                else "meta_strategy.paper_control.new_entries_disabled"
            ),
        }


class MetaStrategyPaperGatewayStore:
    def __init__(self, repository: "MetaStrategyJobRepository") -> None:
        self.repository = repository

    @property
    def snapshots(self) -> dict[str, dict[str, Any]]:
        return self.repository.gateway_snapshots()

    def read_snapshot(self, key: str) -> dict[str, Any]:
        return self.repository.read_gateway_snapshot(key)

    def write_snapshot(self, key: str, snapshot: dict[str, Any]) -> None:
        self.repository.write_gateway_snapshot(key, snapshot)


class MetaStrategyJobRepository:
    def __init__(self, database_url: str | None = None) -> None:
        self.path = _sqlite_path(database_url or os.getenv("DATABASE_URL", "sqlite:///./data/trading.db"))
        migrate_meta_strategy_job_database(self.path)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            conn.execute("PRAGMA journal_mode=DELETE")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def enqueue_job(
        self,
        *,
        job_type: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        queue_name: str | None = None,
        priority: int = 100,
        max_attempts: int = 3,
        cancellable: bool = False,
        now: datetime | None = None,
    ) -> MetaStrategyJobRecord:
        _validate_job_type(job_type)
        queue = queue_name or META_STRATEGY_JOB_TYPE_TO_QUEUE[job_type]
        _validate_queue(queue)
        timestamp = _dt(now or _utc_now())
        job_id = f"meta_strategy.job.{_stable_hash((job_type, queue, idempotency_key))}"
        payload_reference = f"meta_strategy.job_payload.{job_id}"
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM meta_strategy_jobs WHERE algorithm_id = ? AND idempotency_key = ?",
                (ALGORITHM_ID, idempotency_key),
            ).fetchone()
            if existing is not None:
                return _job_from_row(existing, duplicate=True)
            payload_json = _json({"algorithmId": ALGORITHM_ID, "jobType": job_type, "queueName": queue, "payload": dict(payload)})
            conn.execute(
                """
                INSERT INTO meta_strategy_job_payloads (
                    payload_reference, algorithm_id, payload_json, created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (payload_reference, ALGORITHM_ID, payload_json, timestamp),
            )
            conn.execute(
                """
                INSERT INTO meta_strategy_jobs (
                    job_id, algorithm_id, job_type, queue_name, idempotency_key, payload_reference,
                    status, priority, attempt_count, max_attempts, next_attempt_at, lease_owner,
                    lease_expires_at, created_at, started_at, updated_at, completed_at,
                    result_reference, error_category, error_details, cancellable, cancel_requested
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL, NULL, ?, NULL, ?, NULL, NULL, NULL, NULL, ?, 0)
                """,
                (
                    job_id,
                    ALGORITHM_ID,
                    job_type,
                    queue,
                    idempotency_key,
                    payload_reference,
                    MetaStrategyJobStatus.PENDING.value,
                    int(priority),
                    int(max_attempts),
                    timestamp,
                    timestamp,
                    timestamp,
                    int(bool(cancellable)),
                ),
            )
            row = conn.execute("SELECT * FROM meta_strategy_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _job_from_row(row)

    def record_event(
        self,
        *,
        event_type: str,
        queue_name: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        job_id: str | None = None,
        now: datetime | None = None,
    ) -> MetaStrategyEventRecord:
        _validate_queue(queue_name)
        timestamp = _dt(now or _utc_now())
        event_id = f"meta_strategy.event.{_stable_hash((event_type, queue_name, idempotency_key))}"
        payload_reference = f"meta_strategy.event_payload.{event_id}"
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM meta_strategy_job_events WHERE algorithm_id = ? AND idempotency_key = ?",
                (ALGORITHM_ID, idempotency_key),
            ).fetchone()
            if existing is not None:
                return _event_from_row(existing, duplicate=True)
            conn.execute(
                """
                INSERT INTO meta_strategy_job_payloads (
                    payload_reference, algorithm_id, payload_json, created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (payload_reference, ALGORITHM_ID, _json({"eventType": event_type, "payload": dict(payload)}), timestamp),
            )
            conn.execute(
                """
                INSERT INTO meta_strategy_job_events (
                    event_id, algorithm_id, event_type, queue_name, idempotency_key,
                    payload_reference, job_id, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, ALGORITHM_ID, event_type, queue_name, idempotency_key, payload_reference, job_id, "RECORDED", timestamp),
            )
            row = conn.execute("SELECT * FROM meta_strategy_job_events WHERE event_id = ?", (event_id,)).fetchone()
        return _event_from_row(row)

    def enqueue_finalised_bar_decision(
        self,
        *,
        mode: str,
        symbol: str,
        timeframe: str,
        bar_end: datetime,
        settings_version: str,
        capital_partition_id: str = META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
        payload: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> MetaStrategyJobRecord:
        event_payload = {
            **dict(payload or {}),
            "algorithm_id": ALGORITHM_ID,
            "algorithmId": ALGORITHM_ID,
            "capital_partition_id": capital_partition_id,
            "capitalPartitionId": capital_partition_id,
            "mode": mode,
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "barEnd": _dt(bar_end),
            "bar_end": _dt(bar_end),
            "settingsVersion": settings_version,
            "settings_version": settings_version,
            "strategy_catalog_version": META_STRATEGY_STRATEGY_CATALOG_VERSION,
            "strategyCatalogVersion": META_STRATEGY_STRATEGY_CATALOG_VERSION,
            "feature_schema_version": META_STRATEGY_FEATURE_SCHEMA_VERSION,
            "featureSchemaVersion": META_STRATEGY_FEATURE_SCHEMA_VERSION,
            "model_version": META_STRATEGY_MODEL_VERSION,
            "modelVersion": META_STRATEGY_MODEL_VERSION,
        }
        key = finalised_bar_idempotency_key(
            mode=mode,
            symbol=symbol,
            timeframe=timeframe,
            bar_end=bar_end,
            settings_version=settings_version,
            capital_partition_id=capital_partition_id,
        )
        event = self.record_event(
            event_type="finalised_one_minute_bar",
            queue_name="finalised_bar_decisions",
            idempotency_key=key,
            payload=event_payload,
            now=now,
        )
        job = self.enqueue_job(
            job_type="finalised_bar_decision",
            idempotency_key=key,
            payload={"eventId": event.event_id},
            now=now,
        )
        with self.connect() as conn:
            conn.execute(
                "UPDATE meta_strategy_job_events SET job_id = ? WHERE algorithm_id = ? AND event_id = ? AND job_id IS NULL",
                (job.job_id, ALGORITHM_ID, event.event_id),
            )
        return job

    def read_payload(self, payload_reference: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM meta_strategy_job_payloads WHERE payload_reference = ? AND algorithm_id = ?",
                (payload_reference, ALGORITHM_ID),
            ).fetchone()
        if row is None:
            raise KeyError(payload_reference)
        return json.loads(str(row["payload_json"]))

    def event_by_id(self, event_id: str) -> MetaStrategyEventRecord:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM meta_strategy_job_events WHERE event_id = ? AND algorithm_id = ?",
                (event_id, ALGORITHM_ID),
            ).fetchone()
        if row is None:
            raise KeyError(event_id)
        return _event_from_row(row)

    def persist_decision_atomic(
        self,
        *,
        job: MetaStrategyJobRecord,
        event: MetaStrategyEventRecord,
        decision_id: str,
        payload: Mapping[str, Any],
        order_intent: Mapping[str, Any] | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        timestamp = _dt(now or _utc_now())
        normalized_payload = _decision_artifact_payload(
            payload,
            job=job,
            event=event,
            decision_id=decision_id,
            processing_timestamp=timestamp,
        )
        decision_payload = _json(_redact_sensitive(normalized_payload))
        outbox_id: str | None = None
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT decision_id FROM meta_strategy_worker_decisions WHERE algorithm_id = ? AND event_id = ?",
                (ALGORITHM_ID, event.event_id),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO meta_strategy_worker_decisions (
                        decision_id, algorithm_id, event_id, job_id, idempotency_key,
                        symbol, bar_end, settings_version, schema_version, model_version,
                        event_timestamp, processing_timestamp, causal_ids_json, status, payload_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision_id,
                        ALGORITHM_ID,
                        event.event_id,
                        job.job_id,
                        job.idempotency_key,
                        str(normalized_payload.get("symbol") or "UNKNOWN").upper(),
                        str(normalized_payload.get("barEnd") or ""),
                        str(normalized_payload.get("settingsVersion") or ""),
                        str(normalized_payload["schemaVersion"]),
                        str(normalized_payload["modelVersion"]),
                        str(normalized_payload["eventTimestamp"]),
                        str(normalized_payload["processingTimestamp"]),
                        _json(normalized_payload["causalIds"]),
                        str(normalized_payload.get("decisionStatus") or "PERSISTED"),
                        decision_payload,
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                decision_id = str(existing["decision_id"])
            projection_records = _persist_runtime_decision_projection_records(
                conn,
                normalized_payload,
                decision_id=decision_id,
                timestamp=timestamp,
            )
            if order_intent is not None:
                normalized_order = _outbox_artifact_payload(
                    order_intent,
                    decision_payload=normalized_payload,
                    job=job,
                    event=event,
                    decision_id=decision_id,
                    processing_timestamp=timestamp,
                )
                client_order_id = _deterministic_meta_strategy_client_order_id(normalized_order)
                normalized_order["clientOrderId"] = str(normalized_order.get("clientOrderId") or client_order_id)
                normalized_order["client_order_id"] = normalized_order["clientOrderId"]
                normalized_order = _persist_atomic_inventory_order_intent(conn, normalized_order, decision_payload=normalized_payload, timestamp=timestamp)
                outbox_id = f"meta_strategy.execution_outbox.{normalized_order.get('orderIntentId') or decision_id}"
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO meta_strategy_execution_outbox (
                        outbox_id, algorithm_id, event_id, job_id, decision_id,
                        order_intent_id, idempotency_key, schema_version, settings_version,
                        model_version, event_timestamp, processing_timestamp,
                        causal_ids_json, status, payload_json,
                        created_at, updated_at, client_order_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outbox_id,
                        ALGORITHM_ID,
                        event.event_id,
                        job.job_id,
                        decision_id,
                        str(normalized_order.get("orderIntentId") or normalized_order.get("order_intent_id") or ""),
                        f"{job.idempotency_key}:order_intent",
                        str(normalized_order["schemaVersion"]),
                        str(normalized_order["settingsVersion"]),
                        str(normalized_order["modelVersion"]),
                        str(normalized_order["eventTimestamp"]),
                        str(normalized_order["processingTimestamp"]),
                        _json(normalized_order["causalIds"]),
                        "PENDING",
                        _json(_redact_sensitive(normalized_order)),
                        timestamp,
                        timestamp,
                        normalized_order["clientOrderId"],
                    ),
                )
                if cursor.rowcount == 1:
                    _record_outbox_transition(
                        conn,
                        outbox_id=outbox_id,
                        payload=normalized_order,
                        previous_status=None,
                        next_status="PENDING",
                        reason_codes=("meta_strategy.outbox.pending_after_atomic_decision_commit",),
                        transitioned_at=timestamp,
                    )
            _upsert_finalized_candle_outcome(
                conn,
                _finalized_candle_outcome_payload(
                    event_id=event.event_id,
                    outcome=_decision_terminal_outcome(normalized_payload, order_intent=order_intent),
                    payload=_decision_outcome_audit_payload(
                        normalized_payload,
                        order_intent=normalized_order if order_intent is not None else None,
                    ),
                    job_id=job.job_id,
                    decision_id=decision_id,
                    order_intent_id=str((normalized_order if order_intent is not None else {}).get("orderIntentId") or ""),
                    client_order_id=str((normalized_order if order_intent is not None else {}).get("clientOrderId") or ""),
                    symbol=str(normalized_payload.get("symbol") or ""),
                    bar_end=str(normalized_payload.get("barEnd") or ""),
                    reason_codes=tuple(str(code) for code in normalized_payload.get("reasonCodes") or normalized_payload.get("reason_codes") or ()),
                ),
                timestamp=timestamp,
            )
        return {"decisionId": decision_id, "outboxId": outbox_id, "projectionRecords": projection_records}

    def enqueue_position_exit_outbox(
        self,
        *,
        job: MetaStrategyJobRecord,
        order_intent: Mapping[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        timestamp = _dt(now or _utc_now())
        normalized = _position_exit_outbox_payload(order_intent, job=job, processing_timestamp=timestamp)
        outbox_id = f"meta_strategy.execution_outbox.{normalized['orderIntentId']}"
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO meta_strategy_execution_outbox (
                    outbox_id, algorithm_id, event_id, job_id, decision_id,
                    order_intent_id, idempotency_key, schema_version, settings_version,
                    model_version, event_timestamp, processing_timestamp,
                    causal_ids_json, status, payload_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outbox_id,
                    ALGORITHM_ID,
                    normalized["eventId"],
                    job.job_id,
                    normalized["decisionId"],
                    normalized["orderIntentId"],
                    normalized["idempotencyKey"],
                    normalized["schemaVersion"],
                    normalized["settingsVersion"],
                    normalized["modelVersion"],
                    normalized["eventTimestamp"],
                    normalized["processingTimestamp"],
                    _json(normalized["causalIds"]),
                    "PENDING",
                    _json(normalized),
                    timestamp,
                    timestamp,
                ),
            )
            if cursor.rowcount == 1:
                _record_outbox_transition(
                    conn,
                    outbox_id=outbox_id,
                    payload=normalized,
                    previous_status=None,
                    next_status="PENDING",
                    reason_codes=("meta_strategy.outbox.pending_position_exit",),
                    transitioned_at=timestamp,
                )
            row = conn.execute("SELECT * FROM meta_strategy_execution_outbox WHERE outbox_id = ?", (outbox_id,)).fetchone()
        outbox = _outbox_from_row(row)
        return {**outbox, "duplicate": str(outbox["createdAt"]) != timestamp}

    def claim_next_job(self, *, queue_name: str, worker_id: str, lease_seconds: int = 300, now: datetime | None = None) -> MetaStrategyJobRecord | None:
        _validate_queue(queue_name)
        current = now or _utc_now()
        current_text = _dt(current)
        expires = _dt(current + timedelta(seconds=lease_seconds))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._recover_expired_leases(conn, now=current)
            if self._running_count(conn, queue_name=queue_name, now=current) >= self._queue_limit(conn, queue_name):
                return None
            row = conn.execute(
                """
                SELECT *
                FROM meta_strategy_jobs
                WHERE algorithm_id = ? AND queue_name = ? AND status IN (?, ?)
                  AND next_attempt_at <= ?
                ORDER BY priority ASC, created_at ASC, job_id ASC
                LIMIT 1
                """,
                (
                    ALGORITHM_ID,
                    queue_name,
                    MetaStrategyJobStatus.PENDING.value,
                    MetaStrategyJobStatus.RETRY.value,
                    current_text,
                ),
            ).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                """
                UPDATE meta_strategy_jobs
                SET status = ?, lease_owner = ?, lease_expires_at = ?,
                    attempt_count = attempt_count + 1,
                    started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE job_id = ? AND algorithm_id = ? AND status IN (?, ?)
                """,
                (
                    MetaStrategyJobStatus.RUNNING.value,
                    worker_id,
                    expires,
                    current_text,
                    current_text,
                    row["job_id"],
                    ALGORITHM_ID,
                    MetaStrategyJobStatus.PENDING.value,
                    MetaStrategyJobStatus.RETRY.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
            claimed = conn.execute("SELECT * FROM meta_strategy_jobs WHERE job_id = ?", (row["job_id"],)).fetchone()
            self._heartbeat(conn, worker_id=worker_id, queue_name=queue_name, now=current)
        return _job_from_row(claimed)

    def complete_job(self, job_id: str, *, worker_id: str, result: Mapping[str, Any] | None = None, now: datetime | None = None) -> bool:
        current = _dt(now or _utc_now())
        result_reference = f"meta_strategy.job_result.{job_id}"
        with self.connect() as conn:
            if result is not None:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO meta_strategy_job_payloads (
                        payload_reference, algorithm_id, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (result_reference, ALGORITHM_ID, _json({"result": dict(result)}), current),
                )
            cursor = conn.execute(
                """
                UPDATE meta_strategy_jobs
                SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                    completed_at = ?, updated_at = ?, result_reference = ?
                WHERE job_id = ? AND algorithm_id = ? AND lease_owner = ? AND status = ?
                """,
                (
                    MetaStrategyJobStatus.SUCCEEDED.value,
                    current,
                    current,
                    result_reference if result is not None else None,
                    job_id,
                    ALGORITHM_ID,
                    worker_id,
                    MetaStrategyJobStatus.RUNNING.value,
                ),
            )
        return cursor.rowcount == 1

    def fail_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        error_category: str,
        error_details: str,
        now: datetime | None = None,
    ) -> bool:
        current = now or _utc_now()
        current_text = _dt(current)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM meta_strategy_jobs WHERE job_id = ? AND algorithm_id = ? AND lease_owner = ? AND status = ?",
                (job_id, ALGORITHM_ID, worker_id, MetaStrategyJobStatus.RUNNING.value),
            ).fetchone()
            if row is None:
                return False
            attempts = int(row["attempt_count"])
            max_attempts = int(row["max_attempts"])
            if attempts >= max_attempts:
                status = MetaStrategyJobStatus.DEAD_LETTER.value
                next_attempt_at = current_text
                completed_at = current_text
            else:
                status = MetaStrategyJobStatus.RETRY.value
                next_attempt_at = _dt(current + _retry_delay(attempts=attempts, job_id=job_id))
                completed_at = None
            cursor = conn.execute(
                """
                UPDATE meta_strategy_jobs
                SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                    next_attempt_at = ?, updated_at = ?, completed_at = ?,
                    error_category = ?, error_details = ?
                WHERE job_id = ? AND algorithm_id = ? AND lease_owner = ?
                """,
                (
                    status,
                    next_attempt_at,
                    current_text,
                    completed_at,
                    error_category,
                    _sanitize_error(error_details),
                    job_id,
                    ALGORITHM_ID,
                    worker_id,
                ),
            )
        return cursor.rowcount == 1

    def dead_letter_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        error_category: str,
        error_details: str,
        now: datetime | None = None,
    ) -> bool:
        current = _dt(now or _utc_now())
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE meta_strategy_jobs
                SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                    completed_at = ?, updated_at = ?, error_category = ?, error_details = ?
                WHERE job_id = ? AND algorithm_id = ? AND lease_owner = ? AND status = ?
                """,
                (
                    MetaStrategyJobStatus.DEAD_LETTER.value,
                    current,
                    current,
                    error_category,
                    _sanitize_error(error_details),
                    job_id,
                    ALGORITHM_ID,
                    worker_id,
                    MetaStrategyJobStatus.RUNNING.value,
                ),
            )
        return cursor.rowcount == 1

    def cancel_job(self, job_id: str, *, now: datetime | None = None) -> bool:
        current = _dt(now or _utc_now())
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE meta_strategy_jobs
                SET status = ?, cancel_requested = 1, lease_owner = NULL,
                    lease_expires_at = NULL, completed_at = ?, updated_at = ?
                WHERE job_id = ? AND algorithm_id = ? AND cancellable = 1
                  AND status NOT IN (?, ?, ?, ?)
                """,
                (
                    MetaStrategyJobStatus.CANCELLED.value,
                    current,
                    current,
                    job_id,
                    ALGORITHM_ID,
                    MetaStrategyJobStatus.SUCCEEDED.value,
                    MetaStrategyJobStatus.FAILED.value,
                    MetaStrategyJobStatus.DEAD_LETTER.value,
                    MetaStrategyJobStatus.CANCELLED.value,
                ),
            )
        return cursor.rowcount == 1

    def record_worker_heartbeat(self, *, worker_id: str, queue_name: str, now: datetime | None = None) -> None:
        _validate_queue(queue_name)
        with self.connect() as conn:
            self._heartbeat(conn, worker_id=worker_id, queue_name=queue_name, now=now or _utc_now())

    def record_operational_event(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        status: str = "RECORDED",
        correlation_id: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _dt(now or _utc_now())
        body = dict(payload or {})
        event_id = f"meta_strategy.operational.{event_type}.{_stable_hash({'payload': body, 'time': current, 'correlation': correlation_id})}"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO meta_strategy_operational_events(
                    event_id, algorithm_id, event_type, status, correlation_id, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, ALGORITHM_ID, event_type, status, correlation_id, _json(_redact_sensitive(body)), current),
            )
        return {"eventId": event_id, "algorithmId": ALGORITHM_ID, "eventType": event_type, "status": status, "createdAt": current}

    def operational_events(self, *, event_type: str | None = None, limit: int = 100) -> tuple[dict[str, Any], ...]:
        bounded = max(1, min(int(limit), 1000))
        with self.connect() as conn:
            if event_type:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM meta_strategy_operational_events
                    WHERE algorithm_id = ? AND event_type = ?
                    ORDER BY created_at DESC, event_id DESC
                    LIMIT ?
                    """,
                    (ALGORITHM_ID, event_type, bounded),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM meta_strategy_operational_events
                    WHERE algorithm_id = ?
                    ORDER BY created_at DESC, event_id DESC
                    LIMIT ?
                    """,
                    (ALGORITHM_ID, bounded),
                ).fetchall()
        return tuple(
            {
                "eventId": str(row["event_id"]),
                "algorithmId": str(row["algorithm_id"]),
                "eventType": str(row["event_type"]),
                "status": str(row["status"]),
                "correlationId": str(row["correlation_id"]),
                "payload": json.loads(str(row["payload_json"])),
                "createdAt": str(row["created_at"]),
            }
            for row in rows
        )

    def read_job(self, job_id: str) -> MetaStrategyJobRecord:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM meta_strategy_jobs WHERE job_id = ? AND algorithm_id = ?", (job_id, ALGORITHM_ID)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _job_from_row(row)

    def cancel_pending_jobs(self, *, queue_name: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        current = _dt(now or _utc_now())
        params: tuple[Any, ...]
        where_queue = ""
        if queue_name is not None:
            _validate_queue(queue_name)
            where_queue = "AND queue_name = ?"
            params = (MetaStrategyJobStatus.CANCELLED.value, current, ALGORITHM_ID, queue_name, MetaStrategyJobStatus.PENDING.value, MetaStrategyJobStatus.RETRY.value)
        else:
            params = (MetaStrategyJobStatus.CANCELLED.value, current, ALGORITHM_ID, MetaStrategyJobStatus.PENDING.value, MetaStrategyJobStatus.RETRY.value)
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE meta_strategy_jobs
                SET status = ?, cancel_requested = 1, updated_at = ?, completed_at = ?
                WHERE algorithm_id = ? {where_queue} AND status IN (?, ?)
                """,
                (*params[:2], current, *params[2:]) if queue_name is not None else (*params[:2], current, *params[2:]),
            )
            cancelled = int(cursor.rowcount)
            conn.execute(
                """
                INSERT INTO meta_strategy_operational_events(
                    event_id, algorithm_id, event_type, status, correlation_id, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"meta_strategy.operational.cancel_pending_jobs.{_stable_hash({'queue': queue_name, 'time': current, 'cancelled': cancelled})}",
                    ALGORITHM_ID,
                    "cancel_pending_jobs",
                    "RECORDED",
                    "",
                    _json({"queueName": queue_name, "cancelledJobs": cancelled}),
                    current,
                ),
            )
        return {"algorithmId": ALGORITHM_ID, "cancelledJobs": cancelled, "queueName": queue_name}

    def resolve_dead_letter_jobs(
        self,
        *,
        queue_name: str | None = None,
        reason: str = "meta_strategy.dead_letter.operator_recovered",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _dt(now or _utc_now())
        where_queue = ""
        params: tuple[Any, ...] = (ALGORITHM_ID, MetaStrategyJobStatus.DEAD_LETTER.value)
        if queue_name is not None:
            _validate_queue(queue_name)
            where_queue = "AND queue_name = ?"
            params = (ALGORITHM_ID, MetaStrategyJobStatus.DEAD_LETTER.value, queue_name)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT job_id, queue_name, job_type, created_at, updated_at, attempt_count, max_attempts,
                       error_category, error_details
                FROM meta_strategy_jobs
                WHERE algorithm_id = ? AND status = ? {where_queue}
                ORDER BY created_at ASC, job_id ASC
                """,
                params,
            ).fetchall()
            job_ids = tuple(str(row["job_id"]) for row in rows)
            resolved = 0
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                cursor = conn.execute(
                    f"""
                    UPDATE meta_strategy_jobs
                    SET status = ?, cancel_requested = 1, lease_owner = NULL, lease_expires_at = NULL,
                        updated_at = ?, completed_at = COALESCE(completed_at, ?),
                        error_details = COALESCE(error_details, '') || ?
                    WHERE algorithm_id = ? AND status = ? AND job_id IN ({placeholders})
                    """,
                    (
                        MetaStrategyJobStatus.CANCELLED.value,
                        current,
                        current,
                        f"\nRecovered at {current}: {reason}",
                        ALGORITHM_ID,
                        MetaStrategyJobStatus.DEAD_LETTER.value,
                        *job_ids,
                    ),
                )
                resolved = int(cursor.rowcount)
            evidence_payload = {
                "queueName": queue_name,
                "resolvedDeadLetters": resolved,
                "reason": reason,
                "jobs": tuple(dict(row) for row in rows),
            }
            conn.execute(
                """
                INSERT INTO meta_strategy_operational_events(
                    event_id, algorithm_id, event_type, status, correlation_id, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"meta_strategy.operational.resolve_dead_letters.{_stable_hash({'queue': queue_name, 'time': current, 'resolved': resolved})}",
                    ALGORITHM_ID,
                    "resolve_dead_letters",
                    "RECORDED",
                    "",
                    _json(evidence_payload),
                    current,
                ),
            )
        return {
            "algorithmId": ALGORITHM_ID,
            "resolvedDeadLetters": resolved,
            "queueName": queue_name,
            "jobIds": job_ids,
        }

    def decision_for_event(self, event_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM meta_strategy_worker_decisions WHERE algorithm_id = ? AND event_id = ?",
                (ALGORITHM_ID, event_id),
            ).fetchone()
        if row is None:
            return None
        return _decision_from_row(row)

    def latest_worker_decision(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM meta_strategy_worker_decisions
                WHERE algorithm_id = ?
                ORDER BY created_at DESC, decision_id DESC
                LIMIT 1
                """,
                (ALGORITHM_ID,),
            ).fetchone()
        if row is None:
            return None
        return _decision_from_row(row)

    def outbox_for_decision(self, decision_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM meta_strategy_execution_outbox WHERE algorithm_id = ? AND decision_id = ?",
                (ALGORITHM_ID, decision_id),
            ).fetchone()
        if row is None:
            return None
        return _outbox_from_row(row)

    def outbox_for_order_intent(self, order_intent_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM meta_strategy_execution_outbox WHERE algorithm_id = ? AND order_intent_id = ?",
                (ALGORITHM_ID, order_intent_id),
            ).fetchone()
        if row is None:
            raise KeyError(order_intent_id)
        return _outbox_from_row(row)

    def claim_next_execution_outbox(self, *, worker_id: str, lease_seconds: int = 300, now: datetime | None = None) -> dict[str, Any] | None:
        current = now or _utc_now()
        current_text = _dt(current)
        expires = _dt(current + timedelta(seconds=lease_seconds))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._recover_expired_outbox_leases(conn, now=current)
            row = conn.execute(
                """
                SELECT *
                FROM meta_strategy_execution_outbox
                WHERE algorithm_id = ? AND status IN (?, ?)
                ORDER BY created_at ASC, outbox_id ASC
                LIMIT 1
                """,
                (ALGORITHM_ID, "PENDING", "RETRY"),
            ).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                """
                UPDATE meta_strategy_execution_outbox
                SET status = ?, lease_owner = ?, lease_expires_at = ?,
                    attempt_count = attempt_count + 1, updated_at = ?
                WHERE outbox_id = ? AND algorithm_id = ? AND status IN (?, ?)
                """,
                ("SUBMITTING", worker_id, expires, current_text, row["outbox_id"], ALGORITHM_ID, "PENDING", "RETRY"),
            )
            if cursor.rowcount != 1:
                return None
            _record_outbox_transition(
                conn,
                outbox_id=str(row["outbox_id"]),
                payload=json.loads(str(row["payload_json"])),
                previous_status=str(row["status"]),
                next_status="SUBMITTING",
                reason_codes=("meta_strategy.outbox.claimed_for_submission",),
                transitioned_at=current_text,
            )
            claimed = conn.execute("SELECT * FROM meta_strategy_execution_outbox WHERE outbox_id = ?", (row["outbox_id"],)).fetchone()
        return _outbox_from_row(claimed)

    def update_execution_outbox(
        self,
        outbox_id: str,
        *,
        status: str,
        payload: Mapping[str, Any] | None = None,
        client_order_id: str | None = None,
        broker_order_id: str | None = None,
        worker_id: str | None = None,
        retryable: bool = False,
        error_category: str | None = None,
        error_details: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _dt(now or _utc_now())
        normalized_status = status.upper()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM meta_strategy_execution_outbox WHERE algorithm_id = ? AND outbox_id = ?",
                (ALGORITHM_ID, outbox_id),
            ).fetchone()
            if row is None:
                raise KeyError(outbox_id)
            previous = json.loads(str(row["payload_json"]))
            merged = {**previous, **dict(payload or {})}
            attempts = int(row["attempt_count"] or 0)
            max_attempts = int(row["max_attempts"] or 3)
            final_status = normalized_status
            if retryable and attempts < max_attempts:
                final_status = "RETRY"
            if retryable and attempts >= max_attempts:
                final_status = "DEAD_LETTER"
            cursor = conn.execute(
                """
                UPDATE meta_strategy_execution_outbox
                SET status = ?, payload_json = ?, client_order_id = COALESCE(?, client_order_id),
                    broker_order_id = COALESCE(?, broker_order_id),
                    submitted_at = CASE WHEN ? IN ('SUBMITTED', 'ACKNOWLEDGED', 'OPEN', 'PARTIALLY_FILLED', 'FILLED') THEN COALESCE(submitted_at, ?) ELSE submitted_at END,
                    acknowledged_at = CASE WHEN ? IN ('ACKNOWLEDGED', 'OPEN', 'PARTIALLY_FILLED', 'FILLED') THEN COALESCE(acknowledged_at, ?) ELSE acknowledged_at END,
                    completed_at = CASE WHEN ? IN ('FILLED', 'CANCELLED', 'EXPIRED', 'REJECTED', 'DEAD_LETTER') THEN COALESCE(completed_at, ?) ELSE completed_at END,
                    lease_owner = NULL, lease_expires_at = NULL,
                    error_category = ?, error_details = ?, updated_at = ?
                WHERE outbox_id = ? AND algorithm_id = ?
                """,
                (
                    final_status,
                    _json(_redact_sensitive(merged)),
                    client_order_id,
                    broker_order_id,
                    final_status,
                    current,
                    final_status,
                    current,
                    final_status,
                    current,
                    error_category,
                    _sanitize_error(error_details or "") if error_details else None,
                    current,
                    outbox_id,
                    ALGORITHM_ID,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(outbox_id)
            if str(row["status"]) != final_status:
                _record_outbox_transition(
                    conn,
                    outbox_id=outbox_id,
                    payload=merged,
                    previous_status=str(row["status"]),
                    next_status=final_status,
                    reason_codes=tuple(str(code) for code in merged.get("reasonCodes") or merged.get("reason_codes") or ()),
                    transitioned_at=current,
                )
            _record_outcome_for_outbox_status(conn, row=row, payload=merged, status=final_status, transitioned_at=current)
            updated = conn.execute("SELECT * FROM meta_strategy_execution_outbox WHERE outbox_id = ?", (outbox_id,)).fetchone()
        return _outbox_from_row(updated)

    def record_finalized_candle_outcome(
        self,
        *,
        event_id: str,
        outcome: str,
        payload: Mapping[str, Any],
        job_id: str = "",
        decision_id: str = "",
        order_intent_id: str = "",
        client_order_id: str = "",
        symbol: str = "",
        bar_end: str = "",
        reason_codes: Sequence[str] = (),
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _dt(now or _utc_now())
        normalized = _finalized_candle_outcome_payload(
            event_id=event_id,
            outcome=outcome,
            payload=payload,
            job_id=job_id,
            decision_id=decision_id,
            order_intent_id=order_intent_id,
            client_order_id=client_order_id,
            symbol=symbol,
            bar_end=bar_end,
            reason_codes=reason_codes,
        )
        with self.connect() as conn:
            _upsert_finalized_candle_outcome(conn, normalized, timestamp=current)
        return normalized

    def finalized_candle_outcome(self, event_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM meta_strategy_finalized_candle_outcomes WHERE algorithm_id = ? AND event_id = ?",
                (ALGORITHM_ID, event_id),
            ).fetchone()
        return _finalized_candle_outcome_from_row(row) if row is not None else None

    def finalized_candle_outcomes(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        bounded = max(1, min(int(limit), 1000))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM meta_strategy_finalized_candle_outcomes
                WHERE algorithm_id = ?
                ORDER BY updated_at DESC, event_id DESC
                LIMIT ?
                """,
                (ALGORITHM_ID, bounded),
            ).fetchall()
        return tuple(_finalized_candle_outcome_from_row(row) for row in rows)

    def execution_outbox_transitions(self, outbox_id: str) -> tuple[dict[str, Any], ...]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM meta_strategy_execution_outbox_transitions
                WHERE algorithm_id = ? AND outbox_id = ?
                ORDER BY transitioned_at ASC, transition_id ASC
                """,
                (ALGORITHM_ID, outbox_id),
            ).fetchall()
        return tuple(
            {
                "transitionId": str(row["transition_id"]),
                "outboxId": str(row["outbox_id"]),
                "algorithmId": str(row["algorithm_id"]),
                "capitalPartitionId": str(row["capital_partition_id"]),
                "decisionId": str(row["decision_id"]),
                "orderIntentId": str(row["order_intent_id"]),
                "previousStatus": row["previous_status"],
                "nextStatus": str(row["next_status"]),
                "reasonCodes": tuple(json.loads(str(row["reason_codes_json"]))),
                "payload": json.loads(str(row["payload_json"])),
                "transitionedAt": str(row["transitioned_at"]),
            }
            for row in rows
        )

    def submitted_execution_outbox_records(self) -> tuple[dict[str, Any], ...]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM meta_strategy_execution_outbox
                WHERE algorithm_id = ? AND status IN ('SUBMITTING', 'SUBMITTED', 'ACKNOWLEDGED', 'OPEN', 'PARTIALLY_FILLED', 'CANCEL_PENDING', 'REPLACED', 'RECONCILIATION_REQUIRED')
                ORDER BY created_at ASC, outbox_id ASC
                """,
                (ALGORITHM_ID,),
            ).fetchall()
        return tuple(_outbox_from_row(row) for row in rows)

    def stale_execution_outbox_records(self, *, now: datetime, stale_seconds: int = 300) -> tuple[dict[str, Any], ...]:
        cutoff = _dt(now - timedelta(seconds=stale_seconds))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM meta_strategy_execution_outbox
                WHERE algorithm_id = ? AND status IN ('ACKNOWLEDGED', 'OPEN', 'PARTIALLY_FILLED')
                  AND COALESCE(submitted_at, created_at) <= ?
                ORDER BY created_at ASC, outbox_id ASC
                """,
                (ALGORITHM_ID, cutoff),
            ).fetchall()
        return tuple(_outbox_from_row(row) for row in rows)

    def write_gateway_snapshot(self, key: str, snapshot: Mapping[str, Any], *, now: datetime | None = None) -> None:
        current = _dt(now or _utc_now())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO meta_strategy_paper_gateway_snapshots(snapshot_key, algorithm_id, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(snapshot_key) DO UPDATE SET payload_json = excluded.payload_json, updated_at = excluded.updated_at
                """,
                (key, ALGORITHM_ID, _json(_redact_sensitive(dict(snapshot))), current),
            )

    def read_gateway_snapshot(self, key: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM meta_strategy_paper_gateway_snapshots WHERE snapshot_key = ? AND algorithm_id = ?",
                (key, ALGORITHM_ID),
            ).fetchone()
        if row is None:
            raise KeyError(key)
        return json.loads(str(row["payload_json"]))

    def gateway_snapshots(self) -> dict[str, dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT snapshot_key, payload_json FROM meta_strategy_paper_gateway_snapshots WHERE algorithm_id = ?",
                (ALGORITHM_ID,),
            ).fetchall()
        return {str(row["snapshot_key"]): json.loads(str(row["payload_json"])) for row in rows}

    def gateway_store(self) -> MetaStrategyPaperGatewayStore:
        return MetaStrategyPaperGatewayStore(self)

    def read_paper_trading_control(
        self,
        *,
        algorithm_id: str = ALGORITHM_ID,
        capital_partition_id: str = META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
    ) -> MetaStrategyPaperTradingControlRecord | None:
        _validate_meta_strategy_control_owner(algorithm_id)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM meta_strategy_paper_trading_control
                WHERE algorithm_id = ? AND capital_partition_id = ?
                """,
                (ALGORITHM_ID, capital_partition_id),
            ).fetchone()
        return _paper_control_from_row(row) if row is not None else None

    def update_paper_trading_control(
        self,
        *,
        new_paper_entries_enabled: bool,
        updated_by: str,
        reason: str,
        expected_version: int | None = None,
        algorithm_id: str = ALGORITHM_ID,
        capital_partition_id: str = META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
        now: datetime | None = None,
    ) -> MetaStrategyPaperTradingControlRecord:
        _validate_meta_strategy_control_owner(algorithm_id)
        current = _dt(now or _utc_now())
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM meta_strategy_paper_trading_control
                WHERE algorithm_id = ? AND capital_partition_id = ?
                """,
                (ALGORITHM_ID, capital_partition_id),
            ).fetchone()
            current_version = int(row["version"]) if row is not None else 0
            if expected_version is not None and int(expected_version) != current_version:
                raise ValueError("meta_strategy.paper_control.version_conflict")
            next_version = current_version + 1
            conn.execute(
                """
                INSERT INTO meta_strategy_paper_trading_control(
                    algorithm_id, capital_partition_id, new_paper_entries_enabled,
                    updated_at, updated_by, reason, version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(algorithm_id, capital_partition_id) DO UPDATE SET
                    new_paper_entries_enabled = excluded.new_paper_entries_enabled,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by,
                    reason = excluded.reason,
                    version = excluded.version
                """,
                (
                    ALGORITHM_ID,
                    capital_partition_id,
                    int(bool(new_paper_entries_enabled)),
                    current,
                    updated_by,
                    reason,
                    next_version,
                ),
            )
            updated = conn.execute(
                """
                SELECT *
                FROM meta_strategy_paper_trading_control
                WHERE algorithm_id = ? AND capital_partition_id = ?
                """,
                (ALGORITHM_ID, capital_partition_id),
            ).fetchone()
            conn.execute(
                """
                INSERT OR IGNORE INTO meta_strategy_operational_events(
                    event_id, algorithm_id, event_type, status, correlation_id, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"meta_strategy.operational.paper_control.state_transition.{_stable_hash({'partition': capital_partition_id, 'version': next_version})}",
                    ALGORITHM_ID,
                    "paper_control.state_transition",
                    "RECORDED",
                    capital_partition_id,
                    _json(
                        _redact_sensitive(
                            {
                                "algorithmId": ALGORITHM_ID,
                                "capitalPartitionId": capital_partition_id,
                                "newPaperEntriesEnabled": bool(new_paper_entries_enabled),
                                "updatedAt": current,
                                "updatedBy": updated_by,
                                "reason": reason,
                                "version": next_version,
                            }
                        )
                    ),
                    current,
                ),
            )
        return _paper_control_from_row(updated)

    def record_broker_event(self, event: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        current = _dt(now or _utc_now())
        broker_event_id = str(event.get("brokerEventId") or event.get("broker_event_id") or "")
        if not broker_event_id:
            raise ValueError("Meta-Strategy broker event requires brokerEventId")
        payload = dict(event)
        algorithm_id = str(payload.get("algorithmId") or payload.get("algorithm_id") or "")
        if algorithm_id != ALGORITHM_ID:
            self.record_reconciliation_evidence(
                "QUARANTINED_FOREIGN_BROKER_EVENT",
                payload,
                client_order_id=str(payload.get("clientOrderId") or ""),
                broker_order_id=str(payload.get("brokerOrderId") or ""),
                order_intent_id=str(payload.get("orderIntentId") or ""),
                status="QUARANTINED",
                now=now,
            )
            return {"status": "QUARANTINED", "duplicate": False, "brokerEventId": broker_event_id}
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO meta_strategy_paper_broker_events(
                    broker_event_id, algorithm_id, client_order_id, broker_order_id,
                    order_intent_id, event_type, status, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    broker_event_id,
                    ALGORITHM_ID,
                    str(payload.get("clientOrderId") or ""),
                    str(payload.get("brokerOrderId") or ""),
                    str(payload.get("orderIntentId") or ""),
                    str(payload.get("eventType") or payload.get("type") or "order"),
                    str(payload.get("status") or "").upper(),
                    _json(_redact_sensitive(payload)),
                    current,
                ),
            )
        return {"status": "RECORDED", "duplicate": cursor.rowcount == 0, "brokerEventId": broker_event_id}

    def broker_event_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM meta_strategy_paper_broker_events WHERE algorithm_id = ?", (ALGORITHM_ID,)).fetchone()
        return int(row["count"])

    def record_reconciliation_evidence(
        self,
        evidence_type: str,
        payload: Mapping[str, Any],
        *,
        client_order_id: str = "",
        broker_order_id: str = "",
        order_intent_id: str = "",
        status: str = "RECORDED",
        now: datetime | None = None,
    ) -> None:
        current = _dt(now or _utc_now())
        record_id = f"meta_strategy.reconciliation.{_stable_hash({'type': evidence_type, 'payload': dict(payload), 'time': current})}"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO meta_strategy_reconciliation_evidence(
                    record_id, algorithm_id, evidence_type, client_order_id, broker_order_id,
                    order_intent_id, status, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (record_id, ALGORITHM_ID, evidence_type, client_order_id, broker_order_id, order_intent_id, status, _json(_redact_sensitive(dict(payload))), current),
            )

    def record_job_progress(
        self,
        job_id: str,
        *,
        worker_id: str,
        status: str,
        progress_percent: float,
        payload: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        current = _dt(now or _utc_now())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO meta_strategy_job_progress(
                    job_id, algorithm_id, worker_id, status, progress_percent, payload_json, recorded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, ALGORITHM_ID, worker_id, status.upper(), float(progress_percent), _json(_redact_sensitive(dict(payload or {}))), current),
            )

    def job_progress(self, job_id: str) -> tuple[dict[str, Any], ...]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM meta_strategy_job_progress WHERE algorithm_id = ? AND job_id = ? ORDER BY id ASC",
                (ALGORITHM_ID, job_id),
            ).fetchall()
        return tuple(
            {
                "jobId": str(row["job_id"]),
                "algorithmId": str(row["algorithm_id"]),
                "workerId": str(row["worker_id"]),
                "status": str(row["status"]),
                "progressPercent": float(row["progress_percent"]),
                "payload": json.loads(str(row["payload_json"])),
                "recordedAt": str(row["recorded_at"]),
            }
            for row in rows
        )

    def persist_workflow_artifact(
        self,
        *,
        job: MetaStrategyJobRecord,
        workflow_type: str,
        metadata: Mapping[str, Any],
        payload: Mapping[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _dt(now or _utc_now())
        normalized_metadata = dict(metadata)
        normalized_payload = dict(payload)
        artifact_id = f"meta_strategy.workflow_artifact.{_stable_hash({'job': job.job_id, 'type': workflow_type, 'metadata': normalized_metadata, 'payload': normalized_payload})}"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO meta_strategy_workflow_artifacts(
                    artifact_id, algorithm_id, job_id, workflow_type, settings_version,
                    model_version, data_version, feature_version, metadata_json,
                    payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    ALGORITHM_ID,
                    job.job_id,
                    workflow_type,
                    str(normalized_metadata.get("settingsVersion") or ""),
                    str(normalized_metadata.get("modelVersion") or "shadow-only"),
                    str(normalized_metadata.get("dataVersion") or ""),
                    str(normalized_metadata.get("featureVersion") or ""),
                    _json(normalized_metadata),
                    _json(normalized_payload),
                    current,
                ),
            )
        return {
            "artifactId": artifact_id,
            "algorithmId": ALGORITHM_ID,
            "jobId": job.job_id,
            "workflowType": workflow_type,
            "metadata": normalized_metadata,
            "payload": normalized_payload,
            "createdAt": current,
        }

    def workflow_artifacts(self, job_id: str) -> tuple[dict[str, Any], ...]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM meta_strategy_workflow_artifacts WHERE algorithm_id = ? AND job_id = ? ORDER BY created_at ASC, artifact_id ASC",
                (ALGORITHM_ID, job_id),
            ).fetchall()
        return tuple(_workflow_artifact_from_row(row) for row in rows)

    def latest_workflow_artifact(self, job_id: str) -> dict[str, Any]:
        artifacts = self.workflow_artifacts(job_id)
        if not artifacts:
            raise KeyError(job_id)
        return artifacts[-1]

    def promote_model_atomically(
        self,
        *,
        job: MetaStrategyJobRecord,
        model_artifact_id: str,
        evidence: Mapping[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _dt(now or _utc_now())
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT model_artifact_id FROM meta_strategy_model_active_pointer WHERE algorithm_id = ?",
                (ALGORITHM_ID,),
            ).fetchone()
            previous = str(row["model_artifact_id"]) if row is not None else "shadow-only"
            conn.execute(
                """
                INSERT INTO meta_strategy_model_promotion_history(
                    promotion_id, algorithm_id, job_id, previous_model_artifact_id,
                    promoted_model_artifact_id, evidence_json, promoted_at, reversible
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    f"meta_strategy.model_promotion.{_stable_hash((job.job_id, model_artifact_id, current))}",
                    ALGORITHM_ID,
                    job.job_id,
                    previous,
                    model_artifact_id,
                    _json(dict(evidence)),
                    current,
                ),
            )
            conn.execute(
                """
                INSERT INTO meta_strategy_model_active_pointer(
                    algorithm_id, model_artifact_id, promotion_job_id, promoted_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(algorithm_id) DO UPDATE SET
                    model_artifact_id = excluded.model_artifact_id,
                    promotion_job_id = excluded.promotion_job_id,
                    promoted_at = excluded.promoted_at
                """,
                (ALGORITHM_ID, model_artifact_id, job.job_id, current),
            )
        return {
            "algorithmId": ALGORITHM_ID,
            "modelArtifactId": model_artifact_id,
            "previousModelArtifactId": previous,
            "promotionJobId": job.job_id,
            "promotedAt": current,
            "reversible": True,
        }

    def active_model_pointer(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM meta_strategy_model_active_pointer WHERE algorithm_id = ?",
                (ALGORITHM_ID,),
            ).fetchone()
        if row is None:
            return {"algorithmId": ALGORITHM_ID, "modelArtifactId": "shadow-only", "promotionJobId": None, "promotedAt": None}
        return {
            "algorithmId": str(row["algorithm_id"]),
            "modelArtifactId": str(row["model_artifact_id"]),
            "promotionJobId": str(row["promotion_job_id"]),
            "promotedAt": str(row["promoted_at"]),
        }

    def rollback_active_model(self, *, actor: str, reason: str, now: datetime | None = None) -> dict[str, Any]:
        current = _dt(now or _utc_now())
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT * FROM meta_strategy_model_active_pointer WHERE algorithm_id = ?",
                (ALGORITHM_ID,),
            ).fetchone()
            if active is None:
                target_model = "shadow-only"
                previous_model = None
            else:
                previous_model = str(active["model_artifact_id"])
                row = conn.execute(
                    """
                    SELECT previous_model_artifact_id
                    FROM meta_strategy_model_promotion_history
                    WHERE algorithm_id = ? AND promoted_model_artifact_id = ?
                    ORDER BY promoted_at DESC
                    LIMIT 1
                    """,
                    (ALGORITHM_ID, previous_model),
                ).fetchone()
                target_model = str(row["previous_model_artifact_id"] or "shadow-only") if row is not None else "shadow-only"
            conn.execute(
                """
                INSERT INTO meta_strategy_model_active_pointer(algorithm_id, model_artifact_id, promotion_job_id, promoted_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(algorithm_id) DO UPDATE SET
                    model_artifact_id=excluded.model_artifact_id,
                    promotion_job_id=excluded.promotion_job_id,
                    promoted_at=excluded.promoted_at
                """,
                (ALGORITHM_ID, target_model, "model_rollback", current),
            )
            conn.execute(
                """
                INSERT INTO meta_strategy_operational_events(
                    event_id, algorithm_id, event_type, status, correlation_id, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"meta_strategy.operational.model_rollback.{_stable_hash({'previous': previous_model, 'target': target_model, 'time': current})}",
                    ALGORITHM_ID,
                    "model_rollback",
                    "RECORDED",
                    "",
                    _json({"actor": actor, "reason": reason, "previousModelArtifactId": previous_model, "restoredModelArtifactId": target_model}),
                    current,
                ),
            )
        return {
            "algorithmId": ALGORITHM_ID,
            "previousModelArtifactId": previous_model,
            "restoredModelArtifactId": target_model,
            "rolledBackAt": current,
            "reasonCodes": ("meta_strategy.model.rollback_applied",),
        }

    def model_promotion_history(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        bounded = max(1, min(int(limit), 500))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM meta_strategy_model_promotion_history
                WHERE algorithm_id = ?
                ORDER BY promoted_at DESC, promotion_id DESC
                LIMIT ?
                """,
                (ALGORITHM_ID, bounded),
            ).fetchall()
        return tuple(
            {
                "promotionId": str(row["promotion_id"]),
                "algorithmId": str(row["algorithm_id"]),
                "jobId": str(row["job_id"]),
                "previousModelArtifactId": row["previous_model_artifact_id"],
                "promotedModelArtifactId": str(row["promoted_model_artifact_id"]),
                "evidence": json.loads(str(row["evidence_json"])),
                "promotedAt": str(row["promoted_at"]),
                "reversible": bool(row["reversible"]),
            }
            for row in rows
        )

    def blocked_decisions(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        bounded = max(1, min(int(limit), 500))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM meta_strategy_worker_decisions
                WHERE algorithm_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (ALGORITHM_ID, bounded),
            ).fetchall()
        blocked: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            status = str(row["status"])
            reason_codes = tuple(payload.get("reasonCodes") or payload.get("reason_codes") or ())
            final_valid = payload.get("finalValid", payload.get("final_valid"))
            if status.upper() in {"BLOCKED", "HOLD", "REJECTED"} or final_valid is False:
                blocked.append(
                    {
                        "decisionId": str(row["decision_id"]),
                        "algorithmId": str(row["algorithm_id"]),
                        "jobId": str(row["job_id"]),
                        "eventId": str(row["event_id"]),
                        "symbol": str(row["symbol"]),
                        "barEnd": str(row["bar_end"]),
                        "settingsVersion": str(row["settings_version"]),
                        "modelVersion": str(row["model_version"]),
                        "status": status,
                        "reasonCodes": reason_codes,
                        "createdAt": str(row["created_at"]),
                    }
                )
        return tuple(blocked)

    def decision_trace(self, decision_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            decision_row = conn.execute(
                "SELECT * FROM meta_strategy_worker_decisions WHERE algorithm_id = ? AND decision_id = ?",
                (ALGORITHM_ID, decision_id),
            ).fetchone()
            if decision_row is None:
                raise KeyError(decision_id)
            event_row = conn.execute(
                "SELECT * FROM meta_strategy_job_events WHERE algorithm_id = ? AND event_id = ?",
                (ALGORITHM_ID, decision_row["event_id"]),
            ).fetchone()
            job_row = conn.execute(
                "SELECT * FROM meta_strategy_jobs WHERE algorithm_id = ? AND job_id = ?",
                (ALGORITHM_ID, decision_row["job_id"]),
            ).fetchone()
            outbox_row = conn.execute(
                "SELECT * FROM meta_strategy_execution_outbox WHERE algorithm_id = ? AND decision_id = ?",
                (ALGORITHM_ID, decision_id),
            ).fetchone()
        if event_row is None or job_row is None:
            raise KeyError(decision_id)
        return {
            "algorithmId": ALGORITHM_ID,
            "decision": _decision_from_row(decision_row),
            "event": _event_record_payload(_event_from_row(event_row)),
            "job": _job_record_payload(_job_from_row(job_row)),
            "outbox": _outbox_from_row(outbox_row) if outbox_row is not None else None,
            "reasonCodes": ("meta_strategy.runtime.decision_trace_resolved",),
        }

    def validate_decision_projection(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM meta_strategy_worker_decisions WHERE algorithm_id = ?",
                (ALGORITHM_ID,),
            ).fetchone()
            orphan_outbox = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM meta_strategy_execution_outbox outbox
                LEFT JOIN meta_strategy_worker_decisions decisions
                  ON decisions.algorithm_id = outbox.algorithm_id
                 AND decisions.decision_id = outbox.decision_id
                WHERE outbox.algorithm_id = ? AND decisions.decision_id IS NULL
                """,
                (ALGORITHM_ID,),
            ).fetchone()
            malformed = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM meta_strategy_worker_decisions
                WHERE algorithm_id = ?
                  AND (schema_version = '' OR settings_version = '' OR event_timestamp = ''
                       OR processing_timestamp = '' OR causal_ids_json = '{}')
                """,
                (ALGORITHM_ID,),
            ).fetchone()
        orphan_count = int(orphan_outbox["count"])
        malformed_count = int(malformed["count"])
        valid = orphan_count == 0 and malformed_count == 0
        return {
            "algorithmId": ALGORITHM_ID,
            "valid": valid,
            "decisionCount": int(row["count"]),
            "orphanOutboxCount": orphan_count,
            "malformedDecisionCount": malformed_count,
            "reasonCodes": (
                "meta_strategy.runtime.decision_projection_valid"
                if valid
                else "meta_strategy.runtime.decision_projection_mismatch"
            ),
        }

    def queue_status(self, *, queue_name: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        current = now or _utc_now()
        current_text = _dt(current)
        queues = tuple([queue_name] if queue_name is not None else sorted(META_STRATEGY_JOB_QUEUES))
        for queue in queues:
            _validate_queue(queue)
        with self.connect() as conn:
            self._recover_expired_leases(conn, now=current)
            status_by_queue: dict[str, dict[str, Any]] = {}
            total_jobs = 0
            for queue in queues:
                rows = conn.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM meta_strategy_jobs
                    WHERE algorithm_id = ? AND queue_name = ?
                    GROUP BY status
                    """,
                    (ALGORITHM_ID, queue),
                ).fetchall()
                counts = {str(row["status"]).lower(): int(row["count"]) for row in rows}
                total_jobs += sum(counts.values())
                oldest = conn.execute(
                    """
                    SELECT MIN(created_at) AS oldest_created
                    FROM meta_strategy_jobs
                    WHERE algorithm_id = ? AND queue_name = ? AND status IN (?, ?)
                    """,
                    (ALGORITHM_ID, queue, MetaStrategyJobStatus.PENDING.value, MetaStrategyJobStatus.RETRY.value),
                ).fetchone()["oldest_created"]
                lag = int(max(0.0, (current - _parse_dt(oldest)).total_seconds())) if oldest else 0
                status_by_queue[queue] = {
                    "pending": counts.get("pending", 0),
                    "running": counts.get("running", 0),
                    "retry": counts.get("retry", 0),
                    "succeeded": counts.get("succeeded", 0),
                    "failed": counts.get("failed", 0),
                    "deadLetter": counts.get("dead_letter", 0),
                    "cancelled": counts.get("cancelled", 0),
                    "lagSeconds": lag,
                    "concurrencyLimit": self._queue_limit(conn, queue),
                }
            worker_rows = conn.execute(
                "SELECT * FROM meta_strategy_worker_heartbeats WHERE algorithm_id = ? ORDER BY worker_id ASC",
                (ALGORITHM_ID,),
            ).fetchall()
        return {
            "algorithmId": ALGORITHM_ID,
            "asOf": current_text,
            "totalJobs": total_jobs,
            "queues": status_by_queue,
            "workers": {
                str(row["worker_id"]): {
                    "queueName": str(row["queue_name"]),
                    "lastHeartbeatAt": str(row["last_heartbeat_at"]),
                    "status": str(row["status"]),
                }
                for row in worker_rows
            },
        }

    def operational_metrics(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = now or _utc_now()
        with self.connect() as conn:
            self._recover_expired_leases(conn, now=current)
            self._recover_expired_outbox_leases(conn, now=current)
            job_rows = conn.execute(
                """
                SELECT queue_name, status, COUNT(*) AS count
                FROM meta_strategy_jobs
                WHERE algorithm_id = ?
                GROUP BY queue_name, status
                """,
                (ALGORITHM_ID,),
            ).fetchall()
            oldest_jobs = conn.execute(
                """
                SELECT queue_name, MIN(created_at) AS oldest_created
                FROM meta_strategy_jobs
                WHERE algorithm_id = ? AND status IN (?, ?)
                GROUP BY queue_name
                """,
                (ALGORITHM_ID, MetaStrategyJobStatus.PENDING.value, MetaStrategyJobStatus.RETRY.value),
            ).fetchall()
            event_lag = conn.execute(
                """
                SELECT MIN(created_at) AS oldest_created
                FROM meta_strategy_jobs
                WHERE algorithm_id = ? AND queue_name = ? AND status IN (?, ?)
                """,
                (ALGORITHM_ID, "finalised_bar_decisions", MetaStrategyJobStatus.PENDING.value, MetaStrategyJobStatus.RETRY.value),
            ).fetchone()
            lease_recoveries = conn.execute(
                """
                SELECT event_type, COUNT(*) AS count
                FROM meta_strategy_operational_events
                WHERE algorithm_id = ? AND event_type IN (?, ?)
                GROUP BY event_type
                """,
                (ALGORITHM_ID, "lease_recovered", "outbox_lease_recovered"),
            ).fetchall()
            decisions = conn.execute(
                """
                SELECT payload_json
                FROM meta_strategy_worker_decisions
                WHERE algorithm_id = ?
                ORDER BY created_at DESC
                LIMIT 500
                """,
                (ALGORITHM_ID,),
            ).fetchall()
            outbox_counts = conn.execute(
                """
                SELECT status, COUNT(*) AS count, MIN(created_at) AS oldest_created
                FROM meta_strategy_execution_outbox
                WHERE algorithm_id = ?
                GROUP BY status
                """,
                (ALGORITHM_ID,),
            ).fetchall()
            broker_latency_rows = conn.execute(
                """
                SELECT created_at, submitted_at, acknowledged_at
                FROM meta_strategy_execution_outbox
                WHERE algorithm_id = ? AND submitted_at IS NOT NULL
                ORDER BY submitted_at DESC
                LIMIT 100
                """,
                (ALGORITHM_ID,),
            ).fetchall()
            reconciliation = conn.execute(
                """
                SELECT status, evidence_type, created_at
                FROM meta_strategy_reconciliation_evidence
                WHERE algorithm_id = ?
                ORDER BY created_at DESC
                LIMIT 500
                """,
                (ALGORITHM_ID,),
            ).fetchall()
            broker_events = conn.execute(
                "SELECT COUNT(*) AS count, MAX(created_at) AS latest FROM meta_strategy_paper_broker_events WHERE algorithm_id = ?",
                (ALGORITHM_ID,),
            ).fetchone()
            outcome_rows = conn.execute(
                """
                SELECT outcome, COUNT(*) AS count
                FROM meta_strategy_finalized_candle_outcomes
                WHERE algorithm_id = ?
                GROUP BY outcome
                """,
                (ALGORITHM_ID,),
            ).fetchall()
            outbox_payload_rows = conn.execute(
                """
                SELECT status, payload_json, created_at
                FROM meta_strategy_execution_outbox
                WHERE algorithm_id = ?
                ORDER BY created_at DESC
                LIMIT 500
                """,
                (ALGORITHM_ID,),
            ).fetchall()
            gateway_rows = conn.execute(
                """
                SELECT snapshot_key, payload_json, updated_at
                FROM meta_strategy_paper_gateway_snapshots
                WHERE algorithm_id = ?
                """,
                (ALGORITHM_ID,),
            ).fetchall()
            paper_control_rows = conn.execute(
                """
                SELECT payload_json, created_at
                FROM meta_strategy_operational_events
                WHERE algorithm_id = ? AND event_type = 'paper_control.state_transition'
                ORDER BY created_at ASC, event_id ASC
                LIMIT 250
                """,
                (ALGORITHM_ID,),
            ).fetchall()
        queue_counts: dict[str, dict[str, int]] = {}
        for row in job_rows:
            queue = str(row["queue_name"])
            queue_counts.setdefault(queue, {})
            queue_counts[queue][str(row["status"]).lower()] = int(row["count"])
        oldest_queued = {
            str(row["queue_name"]): {
                "createdAt": row["oldest_created"],
                "ageSeconds": _age_seconds(row["oldest_created"], current),
            }
            for row in oldest_jobs
            if row["oldest_created"]
        }
        decision_data_ages: list[float] = []
        decision_latencies: list[float] = []
        quote_ages: list[float] = []
        blocked_reasons: dict[str, int] = {}
        decision_counts = {"BUY": 0, "SELL": 0, "HOLD": 0, "BLOCKED": 0}
        no_trade_reasons: dict[str, int] = {}
        strategy_signal_counts: dict[str, dict[str, int]] = {}
        strategy_abstention_counts: dict[str, int] = {}
        family_conflicts: dict[str, int] = {}
        ml_inference_failures = 0
        ood_decisions = 0
        for row in decisions:
            payload = json.loads(str(row["payload_json"]))
            if payload.get("dataAgeSeconds") is not None:
                decision_data_ages.append(float(payload["dataAgeSeconds"]))
            quote_age = _decision_quote_age_seconds(payload, current)
            if quote_age is not None:
                quote_ages.append(quote_age)
            latency = payload.get("processingLatencyMs", payload.get("latencyMs"))
            if latency is not None:
                decision_latencies.append(float(latency))
            reason_codes = tuple(str(code) for code in tuple(payload.get("reasonCodes") or payload.get("reason_codes") or ()))
            decision_action = _decision_metric_action(payload, reason_codes)
            decision_counts[decision_action] = decision_counts.get(decision_action, 0) + 1
            if decision_action in {"HOLD", "BLOCKED"}:
                for code in reason_codes:
                    no_trade_reasons[code] = no_trade_reasons.get(code, 0) + 1
            if decision_action == "BLOCKED" or payload.get("finalValid") is False:
                for code in reason_codes:
                    blocked_reasons[str(code)] = blocked_reasons.get(str(code), 0) + 1
            _accumulate_strategy_metrics(payload, strategy_signal_counts, strategy_abstention_counts)
            _accumulate_family_conflicts(payload, family_conflicts)
            if _has_ml_failure(reason_codes, payload):
                ml_inference_failures += 1
            if any("ood" in code.lower() or "out_of_distribution" in code.lower() for code in reason_codes):
                ood_decisions += 1
        outbox = {
            str(row["status"]): {
                "count": int(row["count"]),
                "oldestAgeSeconds": _age_seconds(row["oldest_created"], current),
            }
            for row in outbox_counts
        }
        broker_latencies = [
            max(0, _age_seconds(row["created_at"], _parse_dt(row["submitted_at"])))
            for row in broker_latency_rows
            if row["created_at"] and row["submitted_at"]
        ]
        reconciliation_latest = reconciliation[0]["created_at"] if reconciliation else None
        inventory_mismatch_count = sum(1 for row in reconciliation if str(row["status"]).upper() == "QUARANTINED")
        lease_counts = {str(row["event_type"]): int(row["count"]) for row in lease_recoveries}
        outcome_counts = {str(row["outcome"]): int(row["count"]) for row in outcome_rows}
        outbox_payloads = tuple(json.loads(str(row["payload_json"])) for row in outbox_payload_rows)
        rejected_entry_reasons = _rejected_entry_reasons(outbox_payload_rows)
        duplicate_order_attempts = _duplicate_order_attempts(outbox_payloads)
        unknown_broker_outcomes = sum(1 for row in outbox_payload_rows if str(row["status"]).upper() == "RECONCILIATION_REQUIRED")
        broker_clock_ages = _broker_clock_ages(gateway_rows, current)
        paper_toggle_transitions = tuple(
            _paper_toggle_transition_from_metric_row(row)
            for row in paper_control_rows
        )
        return {
            "algorithmId": ALGORITHM_ID,
            "asOf": _dt(current),
            "finalisedBarEventLagSeconds": _age_seconds(event_lag["oldest_created"], current) if event_lag and event_lag["oldest_created"] else 0,
            "queueDepth": queue_counts,
            "oldestQueuedJob": oldest_queued,
            "leaseRecoveryCount": sum(lease_counts.values()),
            "leaseRecoveryByType": lease_counts,
            "jobRetryCount": sum(counts.get("retry", 0) for counts in queue_counts.values()),
            "jobDeadLetterCount": sum(counts.get("dead_letter", 0) for counts in queue_counts.values()),
            "snapshotDataAgeSeconds": _summary(decision_data_ages),
            "decisionLatencyMs": _summary(decision_latencies),
            "quoteAgeSeconds": _summary(quote_ages),
            "brokerClockAgeSeconds": _summary(broker_clock_ages),
            "decisionCountsByAction": decision_counts,
            "finalizedCandleOutcomeCounts": outcome_counts,
            "blockedDecisionReasonCounts": blocked_reasons,
            "rejectedEntriesByReason": rejected_entry_reasons,
            "noTradeReasonCounts": no_trade_reasons,
            "strategySignalCounts": strategy_signal_counts,
            "strategyAbstentionCounts": strategy_abstention_counts,
            "familyConflictCounts": family_conflicts,
            "mlInferenceFailureCount": ml_inference_failures,
            "oodRate": round(ood_decisions / len(decisions), 6) if decisions else 0.0,
            "orderOutbox": outbox,
            "orderOutboxOldestAgeSeconds": max((item["oldestAgeSeconds"] for item in outbox.values()), default=0),
            "brokerSubmissionLatencySeconds": _summary(broker_latencies),
            "reconciliationLagSeconds": _age_seconds(reconciliation_latest, current) if reconciliation_latest else 0,
            "openOrderAgeSeconds": max((item["oldestAgeSeconds"] for status, item in outbox.items() if status in {"SUBMITTED", "OPEN", "ACKNOWLEDGED", "RECONCILIATION_REQUIRED"}), default=0),
            "inventoryMismatchCount": inventory_mismatch_count,
            "inventoryDivergence": {"count": inventory_mismatch_count, "quarantinedReconciliationEvents": inventory_mismatch_count},
            "duplicateOrderAttempts": duplicate_order_attempts,
            "riskReservations": _risk_reservation_summary(outbox_payloads),
            "unknownBrokerOutcomes": unknown_broker_outcomes,
            "paperToggleStateTransitions": paper_toggle_transitions,
            "brokerEventCount": int(broker_events["count"]) if broker_events else 0,
            "latestBrokerEventAt": broker_events["latest"] if broker_events else None,
        }

    def _recover_expired_leases(self, conn: sqlite3.Connection, *, now: datetime) -> None:
        current = _dt(now)
        cursor = conn.execute(
            """
            UPDATE meta_strategy_jobs
            SET status = ?, lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
            WHERE algorithm_id = ? AND status = ? AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (MetaStrategyJobStatus.RETRY.value, current, ALGORITHM_ID, MetaStrategyJobStatus.RUNNING.value, current),
        )
        if cursor.rowcount:
            conn.execute(
                """
                INSERT INTO meta_strategy_operational_events(
                    event_id, algorithm_id, event_type, status, correlation_id, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"meta_strategy.operational.lease_recovered.{_stable_hash({'count': int(cursor.rowcount), 'time': current})}",
                    ALGORITHM_ID,
                    "lease_recovered",
                    "RECORDED",
                    "",
                    _json({"recoveredJobs": int(cursor.rowcount)}),
                    current,
                ),
            )

    def _recover_expired_outbox_leases(self, conn: sqlite3.Connection, *, now: datetime) -> None:
        current = _dt(now)
        expired = conn.execute(
            """
            SELECT *
            FROM meta_strategy_execution_outbox
            WHERE algorithm_id = ? AND status = ? AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (ALGORITHM_ID, "SUBMITTING", current),
        ).fetchall()
        cursor = conn.execute(
            """
            UPDATE meta_strategy_execution_outbox
            SET status = ?, lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
            WHERE algorithm_id = ? AND status = ? AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            ("RETRY", current, ALGORITHM_ID, "SUBMITTING", current),
        )
        if cursor.rowcount:
            for row in expired:
                _record_outbox_transition(
                    conn,
                    outbox_id=str(row["outbox_id"]),
                    payload=json.loads(str(row["payload_json"])),
                    previous_status="SUBMITTING",
                    next_status="RETRY",
                    reason_codes=("meta_strategy.outbox.expired_submission_lease_recovered",),
                    transitioned_at=current,
                )
            conn.execute(
                """
                INSERT INTO meta_strategy_operational_events(
                    event_id, algorithm_id, event_type, status, correlation_id, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"meta_strategy.operational.outbox_lease_recovered.{_stable_hash({'count': int(cursor.rowcount), 'time': current})}",
                    ALGORITHM_ID,
                    "outbox_lease_recovered",
                    "RECORDED",
                    "",
                    _json({"recoveredOutbox": int(cursor.rowcount)}),
                    current,
                ),
            )

    def _running_count(self, conn: sqlite3.Connection, *, queue_name: str, now: datetime) -> int:
        return int(
            conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM meta_strategy_jobs
                WHERE algorithm_id = ? AND queue_name = ? AND status = ?
                  AND lease_expires_at > ?
                """,
                (ALGORITHM_ID, queue_name, MetaStrategyJobStatus.RUNNING.value, _dt(now)),
            ).fetchone()["count"]
        )

    def _queue_limit(self, conn: sqlite3.Connection, queue_name: str) -> int:
        row = conn.execute(
            "SELECT concurrency_limit FROM meta_strategy_queue_limits WHERE algorithm_id = ? AND queue_name = ?",
            (ALGORITHM_ID, queue_name),
        ).fetchone()
        return int(row["concurrency_limit"]) if row is not None else META_STRATEGY_DEFAULT_QUEUE_CONCURRENCY_LIMITS[queue_name]

    def _heartbeat(self, conn: sqlite3.Connection, *, worker_id: str, queue_name: str, now: datetime) -> None:
        timestamp = _dt(now)
        conn.execute(
            """
            INSERT INTO meta_strategy_worker_heartbeats (
                worker_id, algorithm_id, queue_name, last_heartbeat_at, status
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(worker_id)
            DO UPDATE SET queue_name = excluded.queue_name,
                last_heartbeat_at = excluded.last_heartbeat_at,
                status = excluded.status
            """,
            (worker_id, ALGORITHM_ID, queue_name, timestamp, "alive"),
        )


class MetaStrategyWorker:
    def __init__(self, *, repository: MetaStrategyJobRepository, queue_name: str, worker_id: str, lease_seconds: int = 300) -> None:
        _validate_queue(queue_name)
        self.repository = repository
        self.queue_name = queue_name
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.shutdown_requested = False

    def request_shutdown(self) -> None:
        self.shutdown_requested = True

    def run_once(
        self,
        *,
        now: datetime | None = None,
        handler: Callable[[MetaStrategyJobRecord], Mapping[str, Any] | None] | None = None,
    ) -> MetaStrategyJobRecord | None:
        if self.shutdown_requested:
            self.repository.record_worker_heartbeat(worker_id=self.worker_id, queue_name=self.queue_name, now=now)
            return None
        current = now or _utc_now()
        job = self.repository.claim_next_job(queue_name=self.queue_name, worker_id=self.worker_id, lease_seconds=self.lease_seconds, now=current)
        if job is None:
            self.repository.record_worker_heartbeat(worker_id=self.worker_id, queue_name=self.queue_name, now=current)
            return None
        try:
            if handler is None:
                raise RuntimeError("meta_strategy.worker.handler_required")
            result = handler(job)
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            self.repository.fail_job(job.job_id, worker_id=self.worker_id, error_category=type(exc).__name__, error_details=str(exc), now=current)
            return job
        self.repository.complete_job(job.job_id, worker_id=self.worker_id, result=result or {}, now=current)
        return job


def migrate_meta_strategy_job_database(path: str | Path) -> None:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        apply_meta_strategy_job_migrations(conn)


def apply_meta_strategy_job_migrations(conn: sqlite3.Connection) -> None:
    apply_meta_strategy_persistence_migrations(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_job_payloads (
            payload_reference TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_jobs (
            job_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            job_type TEXT NOT NULL,
            queue_name TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            payload_reference TEXT NOT NULL,
            status TEXT NOT NULL,
            priority INTEGER NOT NULL,
            attempt_count INTEGER NOT NULL,
            max_attempts INTEGER NOT NULL,
            next_attempt_at TEXT NOT NULL,
            lease_owner TEXT,
            lease_expires_at TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            result_reference TEXT,
            error_category TEXT,
            error_details TEXT,
            cancellable INTEGER NOT NULL DEFAULT 0,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(payload_reference) REFERENCES meta_strategy_job_payloads(payload_reference)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_job_events (
            event_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            queue_name TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            payload_reference TEXT NOT NULL,
            job_id TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(payload_reference) REFERENCES meta_strategy_job_payloads(payload_reference)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_worker_heartbeats (
            worker_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            queue_name TEXT NOT NULL,
            last_heartbeat_at TEXT NOT NULL,
            status TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_queue_limits (
            algorithm_id TEXT NOT NULL,
            queue_name TEXT NOT NULL,
            concurrency_limit INTEGER NOT NULL,
            PRIMARY KEY (algorithm_id, queue_name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_worker_decisions (
            decision_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            symbol TEXT NOT NULL,
            bar_end TEXT NOT NULL,
            settings_version TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            model_version TEXT NOT NULL,
            event_timestamp TEXT NOT NULL,
            processing_timestamp TEXT NOT NULL,
            causal_ids_json TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_execution_outbox (
            outbox_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            order_intent_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            settings_version TEXT NOT NULL,
            model_version TEXT NOT NULL,
            event_timestamp TEXT NOT NULL,
            processing_timestamp TEXT NOT NULL,
            causal_ids_json TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            client_order_id TEXT,
            broker_order_id TEXT,
            submitted_at TEXT,
            acknowledged_at TEXT,
            completed_at TEXT,
            lease_owner TEXT,
            lease_expires_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            error_category TEXT,
            error_details TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_execution_outbox_transitions (
            transition_id TEXT PRIMARY KEY,
            outbox_id TEXT NOT NULL,
            algorithm_id TEXT NOT NULL,
            capital_partition_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            order_intent_id TEXT NOT NULL,
            previous_status TEXT,
            next_status TEXT NOT NULL,
            reason_codes_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            transitioned_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_finalized_candle_outcomes (
            event_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            order_intent_id TEXT NOT NULL,
            client_order_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            bar_end TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            outcome TEXT NOT NULL,
            reason_codes_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    _ensure_columns(
        conn,
        "meta_strategy_worker_decisions",
        {
            "schema_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_WORKER_DECISION_SCHEMA_VERSION}'",
            "model_version": "TEXT NOT NULL DEFAULT 'none'",
            "event_timestamp": "TEXT NOT NULL DEFAULT ''",
            "processing_timestamp": "TEXT NOT NULL DEFAULT ''",
            "causal_ids_json": "TEXT NOT NULL DEFAULT '{}'",
            "client_order_id": "TEXT",
            "broker_order_id": "TEXT",
            "submitted_at": "TEXT",
            "acknowledged_at": "TEXT",
            "completed_at": "TEXT",
            "lease_owner": "TEXT",
            "lease_expires_at": "TEXT",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "max_attempts": "INTEGER NOT NULL DEFAULT 3",
            "error_category": "TEXT",
            "error_details": "TEXT",
        },
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_paper_gateway_snapshots (
            snapshot_key TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_paper_trading_control (
            algorithm_id TEXT NOT NULL,
            capital_partition_id TEXT NOT NULL,
            new_paper_entries_enabled INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            reason TEXT NOT NULL,
            version INTEGER NOT NULL,
            PRIMARY KEY (algorithm_id, capital_partition_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_paper_broker_events (
            broker_event_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            client_order_id TEXT NOT NULL,
            broker_order_id TEXT,
            order_intent_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_reconciliation_evidence (
            record_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            client_order_id TEXT,
            broker_order_id TEXT,
            order_intent_id TEXT,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_operational_events (
            event_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            correlation_id TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_job_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            algorithm_id TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            status TEXT NOT NULL,
            progress_percent REAL NOT NULL,
            payload_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_workflow_artifacts (
            artifact_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            workflow_type TEXT NOT NULL,
            settings_version TEXT NOT NULL,
            model_version TEXT NOT NULL,
            data_version TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_model_active_pointer (
            algorithm_id TEXT PRIMARY KEY,
            model_artifact_id TEXT NOT NULL,
            promotion_job_id TEXT NOT NULL,
            promoted_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategy_model_promotion_history (
            promotion_id TEXT PRIMARY KEY,
            algorithm_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            previous_model_artifact_id TEXT NOT NULL,
            promoted_model_artifact_id TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            promoted_at TEXT NOT NULL,
            reversible INTEGER NOT NULL
        )
        """
    )
    _ensure_columns(
        conn,
        "meta_strategy_execution_outbox",
        {
            "schema_version": f"TEXT NOT NULL DEFAULT '{META_STRATEGY_EXECUTION_OUTBOX_SCHEMA_VERSION}'",
            "settings_version": "TEXT NOT NULL DEFAULT ''",
            "model_version": "TEXT NOT NULL DEFAULT 'none'",
            "event_timestamp": "TEXT NOT NULL DEFAULT ''",
            "processing_timestamp": "TEXT NOT NULL DEFAULT ''",
            "causal_ids_json": "TEXT NOT NULL DEFAULT '{}'",
            "client_order_id": "TEXT",
            "broker_order_id": "TEXT",
            "submitted_at": "TEXT",
            "acknowledged_at": "TEXT",
            "completed_at": "TEXT",
            "lease_owner": "TEXT",
            "lease_expires_at": "TEXT",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "max_attempts": "INTEGER NOT NULL DEFAULT 3",
            "error_category": "TEXT",
            "error_details": "TEXT",
        },
    )
    for queue, limit in META_STRATEGY_DEFAULT_QUEUE_CONCURRENCY_LIMITS.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO meta_strategy_queue_limits (
                algorithm_id, queue_name, concurrency_limit
            )
            VALUES (?, ?, ?)
            """,
            (ALGORITHM_ID, queue, int(limit)),
        )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_meta_strategy_jobs_idempotency ON meta_strategy_jobs(algorithm_id, idempotency_key)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_meta_strategy_events_idempotency ON meta_strategy_job_events(algorithm_id, idempotency_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_strategy_jobs_claim ON meta_strategy_jobs(algorithm_id, queue_name, status, next_attempt_at, priority, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_strategy_jobs_lease ON meta_strategy_jobs(algorithm_id, status, lease_expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_strategy_events_queue ON meta_strategy_job_events(algorithm_id, queue_name, created_at)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_meta_strategy_worker_decisions_event ON meta_strategy_worker_decisions(algorithm_id, event_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_meta_strategy_worker_decisions_idempotency ON meta_strategy_worker_decisions(algorithm_id, idempotency_key)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_meta_strategy_outbox_order_intent ON meta_strategy_execution_outbox(algorithm_id, order_intent_id)")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_meta_strategy_outbox_client_order_id
        ON meta_strategy_execution_outbox(algorithm_id, client_order_id)
        WHERE client_order_id IS NOT NULL AND client_order_id <> ''
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_strategy_outbox_status ON meta_strategy_execution_outbox(algorithm_id, status, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_strategy_outbox_client_order ON meta_strategy_execution_outbox(algorithm_id, client_order_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_strategy_outbox_transitions_owned ON meta_strategy_execution_outbox_transitions(algorithm_id, outbox_id, transitioned_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_strategy_candle_outcomes_owned ON meta_strategy_finalized_candle_outcomes(algorithm_id, outcome, updated_at)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_meta_strategy_gateway_snapshots_owned ON meta_strategy_paper_gateway_snapshots(algorithm_id, snapshot_key)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_meta_strategy_paper_control_partition ON meta_strategy_paper_trading_control(algorithm_id, capital_partition_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_meta_strategy_broker_events_owned ON meta_strategy_paper_broker_events(algorithm_id, broker_event_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_strategy_job_progress_job ON meta_strategy_job_progress(algorithm_id, job_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_strategy_workflow_artifacts_job ON meta_strategy_workflow_artifacts(algorithm_id, job_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_strategy_operational_events_type ON meta_strategy_operational_events(algorithm_id, event_type, created_at)")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (META_STRATEGY_JOB_MIGRATION_VERSION,))


def _validate_job_type(job_type: str) -> None:
    if job_type not in META_STRATEGY_JOB_TYPE_TO_QUEUE:
        raise ValueError(f"unsupported Meta-Strategy job type: {job_type}")


def _validate_queue(queue_name: str) -> None:
    if queue_name not in META_STRATEGY_JOB_QUEUES:
        raise ValueError(f"unsupported Meta-Strategy queue: {queue_name}")


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: Mapping[str, str]) -> None:
    existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for column, ddl in columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _validate_meta_strategy_control_owner(algorithm_id: str) -> None:
    if algorithm_id != ALGORITHM_ID:
        raise ValueError("meta_strategy.paper_control.foreign_algorithm_rejected")


def _paper_control_from_row(row: sqlite3.Row) -> MetaStrategyPaperTradingControlRecord:
    return MetaStrategyPaperTradingControlRecord(
        algorithm_id=str(row["algorithm_id"]),
        capital_partition_id=str(row["capital_partition_id"]),
        new_paper_entries_enabled=bool(row["new_paper_entries_enabled"]),
        updated_at=str(row["updated_at"]),
        updated_by=str(row["updated_by"]),
        reason=str(row["reason"]),
        version=int(row["version"]),
    )


def _job_from_row(row: sqlite3.Row, *, duplicate: bool = False) -> MetaStrategyJobRecord:
    if str(row["algorithm_id"]) != ALGORITHM_ID:
        raise ValueError(f"Meta-Strategy job repository refused foreign algorithm job {row['job_id']}")
    return MetaStrategyJobRecord(
        job_id=str(row["job_id"]),
        algorithm_id=str(row["algorithm_id"]),
        job_type=str(row["job_type"]),
        queue_name=str(row["queue_name"]),
        idempotency_key=str(row["idempotency_key"]),
        payload_reference=str(row["payload_reference"]),
        status=MetaStrategyJobStatus(str(row["status"])),
        priority=int(row["priority"]),
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        next_attempt_at=str(row["next_attempt_at"]),
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        created_at=str(row["created_at"]),
        started_at=row["started_at"],
        updated_at=str(row["updated_at"]),
        completed_at=row["completed_at"],
        result_reference=row["result_reference"],
        error_category=row["error_category"],
        error_details=row["error_details"],
        cancellable=bool(int(row["cancellable"])),
        cancel_requested=bool(int(row["cancel_requested"])),
        duplicate=duplicate,
    )


def _event_from_row(row: sqlite3.Row, *, duplicate: bool = False) -> MetaStrategyEventRecord:
    if str(row["algorithm_id"]) != ALGORITHM_ID:
        raise ValueError(f"Meta-Strategy event repository refused foreign algorithm event {row['event_id']}")
    return MetaStrategyEventRecord(
        event_id=str(row["event_id"]),
        algorithm_id=str(row["algorithm_id"]),
        event_type=str(row["event_type"]),
        queue_name=str(row["queue_name"]),
        idempotency_key=str(row["idempotency_key"]),
        payload_reference=str(row["payload_reference"]),
        job_id=row["job_id"],
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        duplicate=duplicate,
    )


def _event_record_payload(record: MetaStrategyEventRecord) -> dict[str, Any]:
    return {
        "eventId": record.event_id,
        "algorithmId": record.algorithm_id,
        "eventType": record.event_type,
        "queueName": record.queue_name,
        "idempotencyKey": record.idempotency_key,
        "payloadReference": record.payload_reference,
        "jobId": record.job_id,
        "status": record.status,
        "createdAt": record.created_at,
        "duplicate": record.duplicate,
    }


def _job_record_payload(record: MetaStrategyJobRecord) -> dict[str, Any]:
    return {
        "jobId": record.job_id,
        "algorithmId": record.algorithm_id,
        "jobType": record.job_type,
        "queueName": record.queue_name,
        "idempotencyKey": record.idempotency_key,
        "payloadReference": record.payload_reference,
        "status": record.status.value,
        "priority": record.priority,
        "attemptCount": record.attempt_count,
        "maxAttempts": record.max_attempts,
        "nextAttemptAt": record.next_attempt_at,
        "leaseOwner": record.lease_owner,
        "leaseExpiresAt": record.lease_expires_at,
        "createdAt": record.created_at,
        "startedAt": record.started_at,
        "updatedAt": record.updated_at,
        "completedAt": record.completed_at,
        "resultReference": record.result_reference,
        "errorCategory": record.error_category,
        "errorDetails": record.error_details,
        "cancellable": record.cancellable,
        "cancelRequested": record.cancel_requested,
        "duplicate": record.duplicate,
    }


def _decision_artifact_payload(
    payload: Mapping[str, Any],
    *,
    job: MetaStrategyJobRecord,
    event: MetaStrategyEventRecord,
    decision_id: str,
    processing_timestamp: str,
) -> dict[str, Any]:
    normalized = dict(payload)
    event_payload = _payload_inner_json_safe(payload)
    event_timestamp = str(normalized.get("eventTimestamp") or normalized.get("barEnd") or event_payload.get("barEnd") or event.created_at)
    model_version = str(normalized.get("modelVersion") or normalized.get("model_version") or "none")
    normalized["schemaVersion"] = str(normalized.get("schemaVersion") or META_STRATEGY_WORKER_DECISION_SCHEMA_VERSION)
    normalized["algorithmId"] = ALGORITHM_ID
    normalized["algorithm_id"] = ALGORITHM_ID
    normalized["capitalPartitionId"] = str(normalized.get("capitalPartitionId") or normalized.get("capital_partition_id") or event_payload.get("capitalPartitionId") or event_payload.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION)
    normalized["capital_partition_id"] = normalized["capitalPartitionId"]
    normalized["decisionId"] = str(normalized.get("decisionId") or decision_id)
    normalized["decision_id"] = normalized["decisionId"]
    normalized["eventId"] = event.event_id
    normalized["event_id"] = event.event_id
    normalized["jobId"] = job.job_id
    normalized["job_id"] = job.job_id
    normalized["settingsVersion"] = str(normalized.get("settingsVersion") or event_payload.get("settingsVersion") or "")
    normalized["settings_version"] = normalized["settingsVersion"]
    normalized["strategyCatalogVersion"] = str(normalized.get("strategyCatalogVersion") or normalized.get("strategy_catalog_version") or event_payload.get("strategyCatalogVersion") or event_payload.get("strategy_catalog_version") or META_STRATEGY_STRATEGY_CATALOG_VERSION)
    normalized["strategy_catalog_version"] = normalized["strategyCatalogVersion"]
    normalized["featureSchemaVersion"] = str(normalized.get("featureSchemaVersion") or normalized.get("feature_schema_version") or event_payload.get("featureSchemaVersion") or event_payload.get("feature_schema_version") or META_STRATEGY_FEATURE_SCHEMA_VERSION)
    normalized["feature_schema_version"] = normalized["featureSchemaVersion"]
    normalized["modelVersion"] = model_version
    normalized["model_version"] = model_version
    normalized["eventTimestamp"] = event_timestamp
    normalized["processingTimestamp"] = str(normalized.get("processingTimestamp") or processing_timestamp)
    normalized["causalIds"] = _causal_ids(
        normalized.get("causalIds"),
        event_id=event.event_id,
        job_id=job.job_id,
        decision_id=decision_id,
        idempotency_key=job.idempotency_key,
        order_intent_id=str(normalized.get("orderIntentId") or ""),
    )
    normalized["correlationId"] = str(normalized.get("correlationId") or normalized.get("correlation_id") or normalized["causalIds"]["correlationId"])
    normalized["correlation_id"] = normalized["correlationId"]
    return normalized


def _outbox_artifact_payload(
    payload: Mapping[str, Any],
    *,
    decision_payload: Mapping[str, Any],
    job: MetaStrategyJobRecord,
    event: MetaStrategyEventRecord,
    decision_id: str,
    processing_timestamp: str,
) -> dict[str, Any]:
    normalized = dict(payload)
    order_intent_id = str(normalized.get("orderIntentId") or normalized.get("order_intent_id") or "")
    normalized["schemaVersion"] = str(normalized.get("schemaVersion") or META_STRATEGY_EXECUTION_OUTBOX_SCHEMA_VERSION)
    normalized["algorithmId"] = ALGORITHM_ID
    normalized["algorithm_id"] = ALGORITHM_ID
    normalized["capitalPartitionId"] = str(normalized.get("capitalPartitionId") or normalized.get("capital_partition_id") or decision_payload.get("capitalPartitionId") or decision_payload.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION)
    normalized["capital_partition_id"] = normalized["capitalPartitionId"]
    normalized["decisionId"] = decision_id
    normalized["decision_id"] = decision_id
    normalized["eventId"] = event.event_id
    normalized["event_id"] = event.event_id
    normalized["jobId"] = job.job_id
    normalized["job_id"] = job.job_id
    normalized["settingsVersion"] = str(normalized.get("settingsVersion") or decision_payload.get("settingsVersion") or "")
    normalized["settings_version"] = normalized["settingsVersion"]
    normalized["strategyCatalogVersion"] = str(normalized.get("strategyCatalogVersion") or normalized.get("strategy_catalog_version") or decision_payload.get("strategyCatalogVersion") or decision_payload.get("strategy_catalog_version") or META_STRATEGY_STRATEGY_CATALOG_VERSION)
    normalized["strategy_catalog_version"] = normalized["strategyCatalogVersion"]
    normalized["featureSchemaVersion"] = str(normalized.get("featureSchemaVersion") or normalized.get("feature_schema_version") or decision_payload.get("featureSchemaVersion") or decision_payload.get("feature_schema_version") or META_STRATEGY_FEATURE_SCHEMA_VERSION)
    normalized["feature_schema_version"] = normalized["featureSchemaVersion"]
    normalized["modelVersion"] = str(normalized.get("modelVersion") or decision_payload.get("modelVersion") or "none")
    normalized["model_version"] = normalized["modelVersion"]
    normalized["eventTimestamp"] = str(normalized.get("eventTimestamp") or decision_payload.get("eventTimestamp") or event.created_at)
    normalized["processingTimestamp"] = str(normalized.get("processingTimestamp") or decision_payload.get("processingTimestamp") or processing_timestamp)
    normalized["causalIds"] = _causal_ids(
        normalized.get("causalIds") or decision_payload.get("causalIds"),
        event_id=event.event_id,
        job_id=job.job_id,
        decision_id=decision_id,
        idempotency_key=f"{job.idempotency_key}:order_intent",
        order_intent_id=order_intent_id,
    )
    normalized["correlationId"] = str(normalized.get("correlationId") or normalized.get("correlation_id") or normalized["causalIds"]["correlationId"])
    normalized["correlation_id"] = normalized["correlationId"]
    return normalized


def _position_exit_outbox_payload(
    payload: Mapping[str, Any],
    *,
    job: MetaStrategyJobRecord,
    processing_timestamp: str,
) -> dict[str, Any]:
    normalized = dict(payload)
    order_intent_id = str(normalized.get("orderIntentId") or normalized.get("order_intent_id") or "")
    if not order_intent_id:
        raise ValueError("meta_strategy.position_management.exit_order_intent_id_required")
    decision_id = str(normalized.get("decisionId") or normalized.get("decision_id") or f"meta_strategy.position_management.{order_intent_id}")
    event_id = str(normalized.get("eventId") or normalized.get("event_id") or job.job_id)
    idempotency_key = str(normalized.get("idempotencyKey") or normalized.get("idempotency_key") or f"{job.idempotency_key}:exit:{order_intent_id}")
    normalized["schemaVersion"] = str(normalized.get("schemaVersion") or META_STRATEGY_EXECUTION_OUTBOX_SCHEMA_VERSION)
    normalized["algorithmId"] = ALGORITHM_ID
    normalized["algorithm_id"] = ALGORITHM_ID
    normalized["capitalPartitionId"] = str(normalized.get("capitalPartitionId") or normalized.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION)
    normalized["capital_partition_id"] = normalized["capitalPartitionId"]
    normalized["decisionId"] = decision_id
    normalized["decision_id"] = decision_id
    normalized["eventId"] = event_id
    normalized["event_id"] = event_id
    normalized["jobId"] = job.job_id
    normalized["job_id"] = job.job_id
    normalized["settingsVersion"] = str(normalized.get("settingsVersion") or normalized.get("settings_version") or "")
    normalized["settings_version"] = normalized["settingsVersion"]
    normalized["strategyCatalogVersion"] = str(normalized.get("strategyCatalogVersion") or normalized.get("strategy_catalog_version") or META_STRATEGY_STRATEGY_CATALOG_VERSION)
    normalized["strategy_catalog_version"] = normalized["strategyCatalogVersion"]
    normalized["featureSchemaVersion"] = str(normalized.get("featureSchemaVersion") or normalized.get("feature_schema_version") or META_STRATEGY_FEATURE_SCHEMA_VERSION)
    normalized["feature_schema_version"] = normalized["featureSchemaVersion"]
    normalized["modelVersion"] = str(normalized.get("modelVersion") or normalized.get("model_version") or META_STRATEGY_MODEL_VERSION)
    normalized["model_version"] = normalized["modelVersion"]
    normalized["eventTimestamp"] = str(normalized.get("eventTimestamp") or normalized.get("event_timestamp") or processing_timestamp)
    normalized["processingTimestamp"] = processing_timestamp
    normalized["orderIntentId"] = order_intent_id
    normalized["order_intent_id"] = order_intent_id
    normalized["idempotencyKey"] = idempotency_key
    normalized["idempotency_key"] = idempotency_key
    normalized["causalIds"] = _causal_ids(
        normalized.get("causalIds"),
        event_id=event_id,
        job_id=job.job_id,
        decision_id=decision_id,
        idempotency_key=idempotency_key,
        order_intent_id=order_intent_id,
    )
    normalized["correlationId"] = str(normalized.get("correlationId") or normalized.get("correlation_id") or normalized["causalIds"]["correlationId"])
    normalized["correlation_id"] = normalized["correlationId"]
    return normalized


def _persist_runtime_decision_projection_records(
    conn: sqlite3.Connection,
    decision_payload: Mapping[str, Any],
    *,
    decision_id: str,
    timestamp: str,
) -> dict[str, Any]:
    records = _runtime_decision_projection_payloads(decision_payload, decision_id=decision_id, timestamp=timestamp)
    persisted: dict[str, list[str]] = {}
    for artifact_type, payloads in records.items():
        artifact_records: list[str] = []
        for payload in payloads:
            record_id = _runtime_projection_record_id(artifact_type, payload)
            record = persist_meta_strategy_projection_record(conn, artifact_type, payload, record_id=record_id)
            artifact_records.append(record.record_id)
        persisted[artifact_type] = artifact_records
    return {
        "status": "PERSISTED",
        "reasonCodes": ("meta_strategy.runtime_projection.persisted",),
        "recordCounts": {artifact_type: len(record_ids) for artifact_type, record_ids in persisted.items()},
        "recordIds": persisted,
    }


def _runtime_decision_projection_payloads(
    decision_payload: Mapping[str, Any],
    *,
    decision_id: str,
    timestamp: str,
) -> dict[str, tuple[dict[str, Any], ...]]:
    base = _runtime_projection_base(decision_payload, decision_id=decision_id, timestamp=timestamp)
    stages = decision_payload.get("stages") if isinstance(decision_payload.get("stages"), Mapping) else {}
    strategy_stage = dict(stages.get("strategyEvidence") or {}) if isinstance(stages, Mapping) else {}
    strategy_evidence = strategy_stage.get("evidence") if isinstance(strategy_stage.get("evidence"), Mapping) else {}
    strategy_outputs = tuple(
        _runtime_projection_payload(
            base,
            artifact_type="strategy_outputs",
            status=str(output.get("status") or strategy_stage.get("status") or "PERSISTED"),
            payload={
                "stage": "strategies",
                "stageResult": strategy_stage,
                "strategyOutput": dict(output),
            },
            artifact_id=str(output.get("strategyId") or output.get("strategy_id") or f"strategy-{index}"),
        )
        for index, output in enumerate(strategy_evidence.get("strategyOutputs") or strategy_evidence.get("strategy_outputs") or ())
        if isinstance(output, Mapping)
    )
    aggregation_stage = dict(stages.get("aggregateCandidate") or {}) if isinstance(stages, Mapping) else {}
    aggregation_evidence = aggregation_stage.get("evidence") if isinstance(aggregation_stage.get("evidence"), Mapping) else {}
    family_score_payloads = _family_score_projection_payloads(base, aggregation_stage, aggregation_evidence)
    return {
        "decisions": (
            _runtime_projection_payload(
                base,
                artifact_type="decisions",
                status=str(decision_payload.get("decisionStatus") or decision_payload.get("decision_status") or "PERSISTED"),
                payload={
                    "runtimeDecision": dict(decision_payload),
                    "stages": stages,
                },
            ),
        ),
        "market_snapshots": (
            _runtime_projection_payload(
                base,
                artifact_type="market_snapshots",
                status=str((stages.get("snapshot") or {}).get("status") or "PERSISTED"),
                payload={
                    "stage": "market_snapshot",
                    "stageResult": stages.get("snapshot") if isinstance(stages, Mapping) else {},
                    "authoritativeState": decision_payload.get("authoritativeState") or {},
                    "sourceVersions": decision_payload.get("sourceVersions") or {},
                    "sourceTimestamps": decision_payload.get("sourceTimestamps") or {},
                },
            ),
        ),
        "strategy_outputs": strategy_outputs
        or (
            _runtime_projection_payload(
                base,
                artifact_type="strategy_outputs",
                status=str(strategy_stage.get("status") or "BLOCKED"),
                payload={"stage": "strategies", "stageResult": strategy_stage},
            ),
        ),
        "family_scores": family_score_payloads,
    }


def _runtime_projection_base(decision_payload: Mapping[str, Any], *, decision_id: str, timestamp: str) -> dict[str, Any]:
    stages = decision_payload.get("stages") if isinstance(decision_payload.get("stages"), Mapping) else {}
    snapshot_stage = stages.get("snapshot") if isinstance(stages, Mapping) and isinstance(stages.get("snapshot"), Mapping) else {}
    snapshot_id = str(
        decision_payload.get("snapshotId")
        or decision_payload.get("snapshot_id")
        or snapshot_stage.get("snapshotId")
        or snapshot_stage.get("snapshot_id")
        or decision_id
    )
    return {
        "algorithmId": ALGORITHM_ID,
        "capitalPartitionId": str(decision_payload.get("capitalPartitionId") or decision_payload.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION),
        "algorithmVersion": str(decision_payload.get("algorithmVersion") or decision_payload.get("algorithm_version") or META_STRATEGY_ALGORITHM_VERSION),
        "settingsVersion": str(decision_payload.get("settingsVersion") or decision_payload.get("settings_version") or ""),
        "strategyCatalogVersion": str(decision_payload.get("strategyCatalogVersion") or decision_payload.get("strategy_catalog_version") or META_STRATEGY_STRATEGY_CATALOG_VERSION),
        "featureSchemaVersion": str(decision_payload.get("featureSchemaVersion") or decision_payload.get("feature_schema_version") or META_STRATEGY_FEATURE_SCHEMA_VERSION),
        "modelVersion": str(decision_payload.get("modelVersion") or decision_payload.get("model_version") or META_STRATEGY_MODEL_VERSION),
        "timestamp": str(decision_payload.get("barEnd") or decision_payload.get("bar_end") or timestamp),
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "symbol": str(decision_payload.get("symbol") or "UNKNOWN").upper(),
        "barEnd": str(decision_payload.get("barEnd") or decision_payload.get("bar_end") or ""),
        "decisionId": decision_id,
        "idempotencyKey": str(decision_payload.get("idempotencyKey") or decision_payload.get("idempotency_key") or decision_payload.get("correlationId") or decision_id),
        "jobId": str(decision_payload.get("jobId") or decision_payload.get("job_id") or ""),
        "eventId": str(decision_payload.get("eventId") or decision_payload.get("event_id") or ""),
        "snapshotId": snapshot_id,
        "mode": str(decision_payload.get("mode") or decision_payload.get("runtimeMode") or decision_payload.get("runtime_mode") or ""),
        "effectiveSettingsHash": decision_payload.get("effectiveSettingsHash"),
    }


def _runtime_projection_payload(
    base: Mapping[str, Any],
    *,
    artifact_type: str,
    status: str,
    payload: Mapping[str, Any],
    artifact_id: str = "",
) -> dict[str, Any]:
    return {
        **dict(base),
        "artifactType": artifact_type,
        "artifactId": artifact_id,
        "status": status,
        "payload": dict(payload),
    }


def _family_score_projection_payloads(
    base: Mapping[str, Any],
    aggregation_stage: Mapping[str, Any],
    aggregation_evidence: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    family_scores = aggregation_evidence.get("familyScores") or aggregation_evidence.get("family_scores") or {}
    payloads: list[dict[str, Any]] = []
    if isinstance(family_scores, Mapping):
        for family, score in sorted(family_scores.items()):
            payloads.append(
                _runtime_projection_payload(
                    base,
                    artifact_type="family_scores",
                    status=str(aggregation_stage.get("status") or "PERSISTED"),
                    payload={
                        "stage": "family_aggregation",
                        "family": str(family),
                        "score": score,
                        "stageResult": aggregation_stage,
                    },
                    artifact_id=str(family),
                )
            )
    elif isinstance(family_scores, list):
        for index, score in enumerate(family_scores):
            if isinstance(score, Mapping):
                family = str(score.get("family") or score.get("familyId") or score.get("family_id") or f"family-{index}")
                payloads.append(
                    _runtime_projection_payload(
                        base,
                        artifact_type="family_scores",
                        status=str(aggregation_stage.get("status") or "PERSISTED"),
                        payload={"stage": "family_aggregation", "family": family, "score": dict(score), "stageResult": aggregation_stage},
                        artifact_id=family,
                    )
                )
    if payloads:
        return tuple(payloads)
    return (
        _runtime_projection_payload(
            base,
            artifact_type="family_scores",
            status=str(aggregation_stage.get("status") or "BLOCKED"),
            payload={"stage": "family_aggregation", "stageResult": dict(aggregation_stage)},
            artifact_id="aggregate",
        ),
    )


def _runtime_projection_record_id(artifact_type: str, payload: Mapping[str, Any]) -> str:
    artifact_id = str(payload.get("artifactId") or payload.get("artifact_id") or "aggregate")
    digest = _stable_hash(
        {
            "artifactType": artifact_type,
            "decisionId": payload.get("decisionId"),
            "snapshotId": payload.get("snapshotId"),
            "artifactId": artifact_id,
            "payload": payload.get("payload"),
        }
    )
    return f"meta_strategy.{artifact_type}.{payload.get('decisionId')}.{artifact_id}.{digest}"


def _causal_ids(
    value: Any,
    *,
    event_id: str,
    job_id: str,
    decision_id: str,
    idempotency_key: str,
    order_intent_id: str,
) -> dict[str, str]:
    causal = dict(value) if isinstance(value, Mapping) else {}
    causal["eventId"] = str(causal.get("eventId") or event_id)
    causal["jobId"] = str(causal.get("jobId") or job_id)
    causal["decisionId"] = str(causal.get("decisionId") or decision_id)
    causal["idempotencyKey"] = str(causal.get("idempotencyKey") or idempotency_key)
    if order_intent_id:
        causal["orderIntentId"] = str(causal.get("orderIntentId") or order_intent_id)
    causal["correlationId"] = str(causal.get("correlationId") or causal.get("correlation_id") or order_intent_id or decision_id or job_id or event_id)
    return causal


def _persist_atomic_inventory_order_intent(
    conn: sqlite3.Connection,
    order_payload: Mapping[str, Any],
    *,
    decision_payload: Mapping[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    payload = _atomic_inventory_payload(order_payload, decision_payload=decision_payload, timestamp=timestamp)
    if str(payload.get("capitalPartitionId") or payload.get("capital_partition_id") or "") != META_STRATEGY_DEFAULT_CAPITAL_PARTITION:
        payload["atomicPersistence"] = {
            "decisionRecordPersisted": True,
            "orderIntentPersisted": False,
            "riskReservationPersisted": False,
            "outboxPersisted": True,
            "persistedAt": timestamp,
            "reasonCodes": ("meta_strategy.atomic_persistence.wrong_capital_partition_deferred_to_execution_guard",),
        }
        return payload
    intent_type = _intent_type(payload)
    reserved_risk = _float_value(payload, "reservedRiskDollars", "reserved_risk_dollars")
    if intent_type == "new_entry" and reserved_risk <= 0.0:
        raise ValueError("meta_strategy.atomic_persistence.new_entry_risk_reservation_required")
    metadata = _inventory_metadata(payload)
    order_record_id = f"meta_strategy_inventory_order_intents.{metadata['decision_id']}.{metadata['order_intent_id']}"
    conn.execute(
        """
        INSERT OR IGNORE INTO meta_strategy_inventory_order_intents (
            record_id, algorithm_id, capital_partition_id, settings_version, correlation_id,
            decision_id, job_id, event_id, order_intent_id, client_order_id, broker_order_id,
            broker_fill_id, symbol, side, quantity, price, status, realised_pnl, timestamp, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _inventory_insert_values(order_record_id, metadata, payload),
    )
    if intent_type != "new_entry" or reserved_risk <= 0.0:
        return payload
    reservation_payload = {
        **payload,
        "reservedRiskDelta": reserved_risk,
        "reservationStatus": "RESERVED",
        "atomicPersistence": {
            "decisionId": metadata["decision_id"],
            "orderIntentId": metadata["order_intent_id"],
            "persistedAt": timestamp,
            "reasonCodes": ("meta_strategy.atomic_persistence.risk_reserved_with_decision",),
        },
    }
    reservation_metadata = _inventory_metadata(reservation_payload)
    reservation_record_id = f"meta_strategy_inventory_reserved_risk.{reservation_metadata['decision_id']}.{reservation_metadata['order_intent_id']}.reserved"
    conn.execute(
        """
        INSERT OR IGNORE INTO meta_strategy_inventory_reserved_risk (
            record_id, algorithm_id, capital_partition_id, settings_version, correlation_id,
            decision_id, job_id, event_id, order_intent_id, client_order_id, broker_order_id,
            broker_fill_id, symbol, side, quantity, price, status, realised_pnl, timestamp, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _inventory_insert_values(reservation_record_id, reservation_metadata, reservation_payload),
    )
    return payload


def _atomic_inventory_payload(order_payload: Mapping[str, Any], *, decision_payload: Mapping[str, Any], timestamp: str) -> dict[str, Any]:
    payload = dict(order_payload)
    stages = dict(decision_payload.get("stages") or {})
    payload["algorithmId"] = ALGORITHM_ID
    payload["algorithm_id"] = ALGORITHM_ID
    payload["capitalPartitionId"] = str(payload.get("capitalPartitionId") or payload.get("capital_partition_id") or decision_payload.get("capitalPartitionId") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION)
    payload["capital_partition_id"] = payload["capitalPartitionId"]
    payload["decisionId"] = str(payload.get("decisionId") or decision_payload.get("decisionId") or "")
    payload["decision_id"] = payload["decisionId"]
    payload["eventId"] = str(payload.get("eventId") or decision_payload.get("eventId") or "")
    payload["event_id"] = payload["eventId"]
    payload["jobId"] = str(payload.get("jobId") or decision_payload.get("jobId") or "")
    payload["job_id"] = payload["jobId"]
    payload["settingsVersion"] = str(payload.get("settingsVersion") or decision_payload.get("settingsVersion") or "")
    payload["settings_version"] = payload["settingsVersion"]
    payload["effectiveSettingsHash"] = str(payload.get("effectiveSettingsHash") or decision_payload.get("effectiveSettingsHash") or "")
    payload["effective_settings_hash"] = payload["effectiveSettingsHash"]
    payload["modelVersion"] = str(payload.get("modelVersion") or decision_payload.get("modelVersion") or "none")
    payload["model_version"] = payload["modelVersion"]
    payload["strategyEvidence"] = stages.get("strategyEvidence")
    payload["hardSafetyResult"] = stages.get("safetyResult")
    payload["localGateResult"] = stages.get("localRisk")
    payload["sizingResult"] = stages.get("sizing")
    payload["globalRiskResult"] = dict((decision_payload.get("authoritativeState") or {}).get("globalRiskSnapshot") or {})
    payload["atomicPersistence"] = {
        "decisionRecordPersisted": True,
        "orderIntentPersisted": True,
        "riskReservationPersisted": _intent_type(payload) != "new_entry" or _float_value(payload, "reservedRiskDollars", "reserved_risk_dollars") > 0.0,
        "outboxPersisted": True,
        "persistedAt": timestamp,
        "reasonCodes": ("meta_strategy.atomic_persistence.decision_order_reservation_outbox_committed",),
    }
    payload["timestamp"] = str(payload.get("timestamp") or timestamp)
    return payload


def _inventory_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    algorithm_id = str(payload.get("algorithmId") or payload.get("algorithm_id") or "")
    capital_partition_id = str(payload.get("capitalPartitionId") or payload.get("capital_partition_id") or "")
    if algorithm_id != ALGORITHM_ID:
        raise ValueError("meta_strategy.atomic_persistence.foreign_algorithm_rejected")
    if capital_partition_id != META_STRATEGY_DEFAULT_CAPITAL_PARTITION:
        raise ValueError("meta_strategy.atomic_persistence.wrong_capital_partition")
    return {
        "capital_partition_id": capital_partition_id,
        "settings_version": str(payload.get("settingsVersion") or payload.get("settings_version") or ""),
        "correlation_id": str(payload.get("correlationId") or payload.get("correlation_id") or payload.get("orderIntentId") or payload.get("decisionId") or ""),
        "decision_id": str(payload.get("decisionId") or payload.get("decision_id") or ""),
        "job_id": str(payload.get("jobId") or payload.get("job_id") or ""),
        "event_id": str(payload.get("eventId") or payload.get("event_id") or ""),
        "order_intent_id": str(payload.get("orderIntentId") or payload.get("order_intent_id") or ""),
        "client_order_id": str(payload.get("clientOrderId") or payload.get("client_order_id") or ""),
        "broker_order_id": str(payload.get("brokerOrderId") or payload.get("broker_order_id") or ""),
        "broker_fill_id": str(payload.get("brokerFillId") or payload.get("broker_fill_id") or ""),
        "symbol": str(payload.get("symbol") or "UNKNOWN").upper(),
        "side": str(payload.get("side") or "").upper(),
        "quantity": _float_value(payload, "filledQuantity", "filled_quantity", "quantity", "orderQuantity"),
        "price": _float_value(payload, "fillPrice", "price", "averageFillPrice", "limitPrice"),
        "status": str(payload.get("orderStatus") or payload.get("order_status") or payload.get("status") or "PENDING").upper(),
        "realised_pnl": _float_value(payload, "realisedPnl", "realizedPnl"),
        "timestamp": str(payload.get("timestamp") or payload.get("createdAt") or ""),
    }


def _inventory_insert_values(record_id: str, metadata: Mapping[str, Any], payload: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        record_id,
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
        _json(payload),
    )


def _record_outbox_transition(
    conn: sqlite3.Connection,
    *,
    outbox_id: str,
    payload: Mapping[str, Any],
    previous_status: str | None,
    next_status: str,
    reason_codes: Sequence[str],
    transitioned_at: str,
) -> None:
    capital_partition_id = str(payload.get("capitalPartitionId") or payload.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION)
    decision_id = str(payload.get("decisionId") or payload.get("decision_id") or "")
    order_intent_id = str(payload.get("orderIntentId") or payload.get("order_intent_id") or "")
    stable = (outbox_id, previous_status or "", next_status, transitioned_at, tuple(reason_codes))
    transition_id = f"meta_strategy.outbox_transition.{_stable_hash(stable)}"
    conn.execute(
        """
        INSERT OR IGNORE INTO meta_strategy_execution_outbox_transitions (
            transition_id, outbox_id, algorithm_id, capital_partition_id, decision_id, order_intent_id,
            previous_status, next_status, reason_codes_json, payload_json, transitioned_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transition_id,
            outbox_id,
            ALGORITHM_ID,
            capital_partition_id,
            decision_id,
            order_intent_id,
            previous_status,
            next_status,
            _json(tuple(reason_codes)),
            _json(_redact_sensitive(dict(payload))),
            transitioned_at,
        ),
    )


def _record_outcome_for_outbox_status(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    payload: Mapping[str, Any],
    status: str,
    transitioned_at: str,
) -> None:
    outcome = _outbox_status_terminal_outcome(status)
    if outcome is None:
        return
    event_id = str(payload.get("eventId") or payload.get("event_id") or row["event_id"])
    if not event_id:
        return
    _upsert_finalized_candle_outcome(
        conn,
        _finalized_candle_outcome_payload(
            event_id=event_id,
            outcome=outcome,
            payload=_outbox_outcome_audit_payload(payload, status=status),
            job_id=str(payload.get("jobId") or payload.get("job_id") or row["job_id"]),
            decision_id=str(payload.get("decisionId") or payload.get("decision_id") or row["decision_id"]),
            order_intent_id=str(payload.get("orderIntentId") or payload.get("order_intent_id") or row["order_intent_id"]),
            client_order_id=str(payload.get("clientOrderId") or payload.get("client_order_id") or row["client_order_id"] or ""),
            symbol=str(payload.get("symbol") or ""),
            bar_end=str(payload.get("barEnd") or payload.get("bar_end") or payload.get("eventTimestamp") or row["event_timestamp"] or ""),
            reason_codes=tuple(str(code) for code in payload.get("reasonCodes") or payload.get("reason_codes") or ()),
        ),
        timestamp=transitioned_at,
    )


def _upsert_finalized_candle_outcome(conn: sqlite3.Connection, payload: Mapping[str, Any], *, timestamp: str) -> None:
    conn.execute(
        """
        INSERT INTO meta_strategy_finalized_candle_outcomes(
            event_id, algorithm_id, job_id, decision_id, order_intent_id, client_order_id,
            symbol, bar_end, schema_version, outcome, reason_codes_json, payload_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
            job_id = excluded.job_id,
            decision_id = excluded.decision_id,
            order_intent_id = excluded.order_intent_id,
            client_order_id = excluded.client_order_id,
            symbol = excluded.symbol,
            bar_end = excluded.bar_end,
            schema_version = excluded.schema_version,
            outcome = excluded.outcome,
            reason_codes_json = excluded.reason_codes_json,
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (
            str(payload["eventId"]),
            ALGORITHM_ID,
            str(payload.get("jobId") or ""),
            str(payload.get("decisionId") or ""),
            str(payload.get("orderIntentId") or ""),
            str(payload.get("clientOrderId") or ""),
            str(payload.get("symbol") or ""),
            str(payload.get("barEnd") or ""),
            str(payload.get("schemaVersion") or META_STRATEGY_FINALIZED_CANDLE_OUTCOME_SCHEMA_VERSION),
            str(payload["outcome"]),
            _json(tuple(payload.get("reasonCodes") or ())),
            _json(_redact_sensitive(payload.get("payload") if isinstance(payload.get("payload"), Mapping) else dict(payload))),
            timestamp,
            timestamp,
        ),
    )


def _finalized_candle_outcome_payload(
    *,
    event_id: str,
    outcome: str,
    payload: Mapping[str, Any],
    job_id: str,
    decision_id: str,
    order_intent_id: str,
    client_order_id: str,
    symbol: str,
    bar_end: str,
    reason_codes: Sequence[str],
) -> dict[str, Any]:
    normalized_outcome = str(outcome).upper()
    if normalized_outcome not in META_STRATEGY_FINALIZED_CANDLE_TERMINAL_OUTCOMES:
        raise ValueError(f"unsupported Meta-Strategy finalized-candle outcome: {outcome}")
    return {
        "eventId": str(event_id),
        "algorithmId": ALGORITHM_ID,
        "jobId": str(job_id),
        "decisionId": str(decision_id),
        "orderIntentId": str(order_intent_id),
        "clientOrderId": str(client_order_id),
        "symbol": str(symbol or "UNKNOWN").upper(),
        "barEnd": str(bar_end),
        "schemaVersion": META_STRATEGY_FINALIZED_CANDLE_OUTCOME_SCHEMA_VERSION,
        "outcome": normalized_outcome,
        "reasonCodes": tuple(dict.fromkeys(str(code) for code in reason_codes)),
        "payload": _redact_sensitive(dict(payload)),
    }


def _decision_terminal_outcome(payload: Mapping[str, Any], *, order_intent: Mapping[str, Any] | None) -> str:
    if order_intent is not None:
        return "ORDER_PROPOSED"
    status = str(payload.get("decisionStatus") or payload.get("status") or "").upper()
    reason_codes = tuple(str(code) for code in payload.get("reasonCodes") or payload.get("reason_codes") or ())
    if payload.get("finalValid") is False or status in {"BLOCKED", "REJECTED"} or any(_blocked_reason(code) for code in reason_codes):
        return "BLOCKED"
    return "HOLD"


def _outbox_status_terminal_outcome(status: str) -> str | None:
    normalized = str(status).upper()
    if normalized in {"SUBMITTED", "ACKNOWLEDGED", "OPEN", "PARTIALLY_FILLED", "FILLED"}:
        return "ORDER_SUBMITTED"
    if normalized in {"REJECTED", "DEAD_LETTER"}:
        return "ORDER_REJECTED"
    if normalized == "RECONCILIATION_REQUIRED":
        return "RECONCILIATION_REQUIRED"
    return None


def _decision_outcome_audit_payload(payload: Mapping[str, Any], *, order_intent: Mapping[str, Any] | None) -> dict[str, Any]:
    stages = dict(payload.get("stages") or {})
    return {
        "eventId": payload.get("eventId"),
        "jobId": payload.get("jobId"),
        "decisionId": payload.get("decisionId"),
        "orderIntentId": (order_intent or {}).get("orderIntentId"),
        "clientOrderId": (order_intent or {}).get("clientOrderId"),
        "symbol": payload.get("symbol"),
        "barEnd": payload.get("barEnd"),
        "dataAgeSeconds": payload.get("dataAgeSeconds"),
        "settingsVersion": payload.get("settingsVersion"),
        "modelVersion": payload.get("modelVersion"),
        "strategyResults": stages.get("strategyEvidence"),
        "regimeContext": stages.get("regime"),
        "safetyResult": stages.get("safetyResult"),
        "familyAggregation": stages.get("aggregateCandidate"),
        "candidateScore": _candidate_score(stages),
        "geometry": _stage_or_order(stages, order_intent, "geometry"),
        "costEstimate": _stage_or_order(stages, order_intent, "costEstimate"),
        "modelResult": stages.get("modelPrediction"),
        "localGates": stages.get("localRisk"),
        "sizingCaps": stages.get("sizing"),
        "globalRisk": dict((payload.get("authoritativeState") or {}).get("globalRiskSnapshot") or {}),
        "executionTimeGuard": None,
        "brokerResult": None,
        "inventoryResult": dict((payload.get("authoritativeState") or {}).get("inventorySnapshot") or {}),
        "latencyPerStage": payload.get("latencyMeasurements"),
        "reasonCodes": tuple(payload.get("reasonCodes") or ()),
    }


def _outbox_outcome_audit_payload(payload: Mapping[str, Any], *, status: str) -> dict[str, Any]:
    return {
        **_decision_outcome_audit_payload(payload, order_intent=payload),
        "orderIntentId": payload.get("orderIntentId") or payload.get("order_intent_id"),
        "clientOrderId": payload.get("clientOrderId") or payload.get("client_order_id"),
        "outboxStatus": str(status).upper(),
        "executionTimeGuard": payload.get("executionGuard"),
        "brokerResult": payload.get("gatewayResult") or payload.get("brokerResult"),
        "inventoryResult": payload.get("inventoryResult") or payload.get("inventory") or {},
        "globalRisk": payload.get("globalApplication") or payload.get("globalRiskResult") or {},
    }


def _candidate_score(stages: Mapping[str, Any]) -> Any:
    candidate = stages.get("aggregateCandidate") if isinstance(stages.get("aggregateCandidate"), Mapping) else {}
    for key in ("candidateScore", "winningScore", "edge", "score"):
        if candidate.get(key) is not None:
            return candidate.get(key)
    return None


def _stage_or_order(stages: Mapping[str, Any], order_intent: Mapping[str, Any] | None, key: str) -> Any:
    for stage_name in ("geometry", "orderProposal", "aggregateCandidate"):
        stage = stages.get(stage_name)
        if isinstance(stage, Mapping) and stage.get(key) is not None:
            return stage.get(key)
    return (order_intent or {}).get(key)


def _deterministic_meta_strategy_client_order_id(payload: Mapping[str, Any]) -> str:
    partition = str(payload.get("capitalPartitionId") or payload.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION)
    stable = {
        "algorithmId": ALGORITHM_ID,
        "capitalPartitionId": partition,
        "decisionId": payload.get("decisionId"),
        "orderIntentId": payload.get("orderIntentId") or payload.get("order_intent_id"),
        "symbol": str(payload.get("symbol") or "").upper(),
        "side": str(payload.get("side") or "").upper(),
    }
    partition_slug = "".join(ch if ch.isalnum() else "-" for ch in partition.lower()).strip("-")
    return "meta-strategy-" + partition_slug[:24] + "-" + hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:18]


def _intent_type(payload: Mapping[str, Any]) -> str:
    raw = str(payload.get("intent") or payload.get("intentType") or payload.get("orderIntentType") or "new_entry").strip().lower()
    if raw in {"protective_exit", "end_of_day_liquidation", "risk_reducing", "exit", "close"}:
        return raw
    return "new_entry"


def _float_value(payload: Mapping[str, Any], *keys: str) -> float:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return float(value)
    return 0.0


def _payload_inner_json_safe(payload: Mapping[str, Any]) -> dict[str, Any]:
    nested = payload.get("payload")
    return dict(nested) if isinstance(nested, Mapping) else {}


def _finalized_candle_outcome_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "eventId": str(row["event_id"]),
        "algorithmId": str(row["algorithm_id"]),
        "jobId": str(row["job_id"]),
        "decisionId": str(row["decision_id"]),
        "orderIntentId": str(row["order_intent_id"]),
        "clientOrderId": str(row["client_order_id"]),
        "symbol": str(row["symbol"]),
        "barEnd": str(row["bar_end"]),
        "schemaVersion": str(row["schema_version"]),
        "outcome": str(row["outcome"]),
        "reasonCodes": tuple(json.loads(str(row["reason_codes_json"]))),
        "payload": json.loads(str(row["payload_json"])),
        "createdAt": str(row["created_at"]),
        "updatedAt": str(row["updated_at"]),
    }


def _decision_from_row(row: sqlite3.Row) -> dict[str, Any]:
    if str(row["algorithm_id"]) != ALGORITHM_ID:
        raise ValueError(f"Meta-Strategy decision repository refused foreign algorithm decision {row['decision_id']}")
    payload = json.loads(str(row["payload_json"]))
    return {
        "decisionId": str(row["decision_id"]),
        "decision_id": str(row["decision_id"]),
        "algorithmId": str(row["algorithm_id"]),
        "algorithm_id": str(row["algorithm_id"]),
        "capitalPartitionId": str(payload.get("capitalPartitionId") or payload.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION),
        "capital_partition_id": str(payload.get("capital_partition_id") or payload.get("capitalPartitionId") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION),
        "eventId": str(row["event_id"]),
        "event_id": str(row["event_id"]),
        "jobId": str(row["job_id"]),
        "job_id": str(row["job_id"]),
        "idempotencyKey": str(row["idempotency_key"]),
        "symbol": str(row["symbol"]),
        "barEnd": str(row["bar_end"]),
        "settingsVersion": str(row["settings_version"]),
        "settings_version": str(row["settings_version"]),
        "strategyCatalogVersion": str(payload.get("strategyCatalogVersion") or payload.get("strategy_catalog_version") or META_STRATEGY_STRATEGY_CATALOG_VERSION),
        "strategy_catalog_version": str(payload.get("strategy_catalog_version") or payload.get("strategyCatalogVersion") or META_STRATEGY_STRATEGY_CATALOG_VERSION),
        "featureSchemaVersion": str(payload.get("featureSchemaVersion") or payload.get("feature_schema_version") or META_STRATEGY_FEATURE_SCHEMA_VERSION),
        "feature_schema_version": str(payload.get("feature_schema_version") or payload.get("featureSchemaVersion") or META_STRATEGY_FEATURE_SCHEMA_VERSION),
        "schemaVersion": str(row["schema_version"]),
        "modelVersion": str(row["model_version"]),
        "model_version": str(row["model_version"]),
        "correlationId": str(payload.get("correlationId") or payload.get("correlation_id") or row["decision_id"]),
        "correlation_id": str(payload.get("correlation_id") or payload.get("correlationId") or row["decision_id"]),
        "eventTimestamp": str(row["event_timestamp"]),
        "processingTimestamp": str(row["processing_timestamp"]),
        "causalIds": json.loads(str(row["causal_ids_json"])),
        "status": str(row["status"]),
        "payload": payload,
        "createdAt": str(row["created_at"]),
        "updatedAt": str(row["updated_at"]),
        "clientOrderId": row["client_order_id"],
        "brokerOrderId": row["broker_order_id"],
        "submittedAt": row["submitted_at"],
        "acknowledgedAt": row["acknowledged_at"],
        "completedAt": row["completed_at"],
        "leaseOwner": row["lease_owner"],
        "leaseExpiresAt": row["lease_expires_at"],
        "attemptCount": int(row["attempt_count"]),
        "maxAttempts": int(row["max_attempts"]),
        "errorCategory": row["error_category"],
        "errorDetails": row["error_details"],
    }


def _outbox_from_row(row: sqlite3.Row) -> dict[str, Any]:
    if str(row["algorithm_id"]) != ALGORITHM_ID:
        raise ValueError(f"Meta-Strategy outbox repository refused foreign algorithm outbox {row['outbox_id']}")
    payload = json.loads(str(row["payload_json"]))
    return {
        "outboxId": str(row["outbox_id"]),
        "algorithmId": str(row["algorithm_id"]),
        "algorithm_id": str(row["algorithm_id"]),
        "capitalPartitionId": str(payload.get("capitalPartitionId") or payload.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION),
        "capital_partition_id": str(payload.get("capital_partition_id") or payload.get("capitalPartitionId") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION),
        "eventId": str(row["event_id"]),
        "event_id": str(row["event_id"]),
        "jobId": str(row["job_id"]),
        "job_id": str(row["job_id"]),
        "decisionId": str(row["decision_id"]),
        "decision_id": str(row["decision_id"]),
        "orderIntentId": str(row["order_intent_id"]),
        "idempotencyKey": str(row["idempotency_key"]),
        "schemaVersion": str(row["schema_version"]),
        "settingsVersion": str(row["settings_version"]),
        "settings_version": str(row["settings_version"]),
        "strategyCatalogVersion": str(payload.get("strategyCatalogVersion") or payload.get("strategy_catalog_version") or META_STRATEGY_STRATEGY_CATALOG_VERSION),
        "strategy_catalog_version": str(payload.get("strategy_catalog_version") or payload.get("strategyCatalogVersion") or META_STRATEGY_STRATEGY_CATALOG_VERSION),
        "featureSchemaVersion": str(payload.get("featureSchemaVersion") or payload.get("feature_schema_version") or META_STRATEGY_FEATURE_SCHEMA_VERSION),
        "feature_schema_version": str(payload.get("feature_schema_version") or payload.get("featureSchemaVersion") or META_STRATEGY_FEATURE_SCHEMA_VERSION),
        "modelVersion": str(row["model_version"]),
        "model_version": str(row["model_version"]),
        "correlationId": str(payload.get("correlationId") or payload.get("correlation_id") or row["order_intent_id"] or row["decision_id"]),
        "correlation_id": str(payload.get("correlation_id") or payload.get("correlationId") or row["order_intent_id"] or row["decision_id"]),
        "eventTimestamp": str(row["event_timestamp"]),
        "processingTimestamp": str(row["processing_timestamp"]),
        "causalIds": json.loads(str(row["causal_ids_json"])),
        "status": str(row["status"]),
        "payload": payload,
        "createdAt": str(row["created_at"]),
        "updatedAt": str(row["updated_at"]),
        "clientOrderId": row["client_order_id"],
        "brokerOrderId": row["broker_order_id"],
        "submittedAt": row["submitted_at"],
        "acknowledgedAt": row["acknowledged_at"],
        "completedAt": row["completed_at"],
        "leaseOwner": row["lease_owner"],
        "leaseExpiresAt": row["lease_expires_at"],
        "attemptCount": int(row["attempt_count"]),
        "maxAttempts": int(row["max_attempts"]),
        "errorCategory": row["error_category"],
        "errorDetails": row["error_details"],
    }


def _workflow_artifact_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "artifactId": str(row["artifact_id"]),
        "algorithmId": str(row["algorithm_id"]),
        "jobId": str(row["job_id"]),
        "workflowType": str(row["workflow_type"]),
        "settingsVersion": str(row["settings_version"]),
        "modelVersion": str(row["model_version"]),
        "dataVersion": str(row["data_version"]),
        "featureVersion": str(row["feature_version"]),
        "metadata": json.loads(str(row["metadata_json"])),
        "payload": json.loads(str(row["payload_json"])),
        "createdAt": str(row["created_at"]),
    }


def finalised_bar_idempotency_key(
    *,
    mode: str | None = None,
    symbol: str,
    timeframe: str,
    bar_end: datetime,
    settings_version: str,
    capital_partition_id: str = META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
) -> str:
    return f"meta_strategy:{capital_partition_id}:{symbol.upper()}:{timeframe}:{_dt(bar_end)}:{settings_version}"


def _retry_delay(*, attempts: int, job_id: str) -> timedelta:
    jitter_ms = int(_stable_hash(job_id), 16) % 1000
    seconds = min(300.0, (2 ** max(0, attempts - 1)) * 5.0 + jitter_ms / 1000.0)
    return timedelta(seconds=seconds)


def _sanitize_error(value: str) -> str:
    sanitized = re.sub(r"(?i)(token|secret|password|api[_-]?key)=\S+", r"\1=[REDACTED]", value)
    return sanitized[:1000]


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()[:16]


def _json(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"))


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return _dt(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("_", "-")
            if any(fragment in normalized for fragment in ("secret", "api-key", "apikey", "authorization", "auth-header", "password", "token")):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_sensitive(item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_sensitive(item) for item in value]
    return value


def _dt(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _age_seconds(value: str | None, now: datetime) -> int:
    if not value:
        return 0
    return int(max(0.0, (now - _parse_dt(str(value))).total_seconds()))


def _optional_age_seconds(value: Any, now: datetime) -> float | None:
    if not value:
        return None
    try:
        return float(max(0.0, (now - _parse_dt(str(value))).total_seconds()))
    except Exception:
        return None


def _summary(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "max": None, "avg": None}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "avg": sum(values) / len(values),
    }


def _decision_quote_age_seconds(payload: Mapping[str, Any], now: datetime) -> float | None:
    if payload.get("quoteAgeSeconds") is not None:
        return float(payload["quoteAgeSeconds"])
    if payload.get("quoteTimestamp") is not None:
        return _optional_age_seconds(payload.get("quoteTimestamp"), now)
    stages = payload.get("stages") if isinstance(payload.get("stages"), Mapping) else {}
    snapshot = stages.get("snapshot") if isinstance(stages.get("snapshot"), Mapping) else {}
    quote = snapshot.get("quote") if isinstance(snapshot.get("quote"), Mapping) else {}
    return _optional_age_seconds(quote.get("timestamp"), now)


def _broker_clock_ages(gateway_rows: Sequence[sqlite3.Row], now: datetime) -> list[float]:
    values: list[float] = []
    for row in gateway_rows:
        key = str(row["snapshot_key"]).lower()
        payload = json.loads(str(row["payload_json"]))
        if "clock" not in key and "market" not in key:
            continue
        timestamp = payload.get("capturedAt") or payload.get("dataSourceTimestamp") or payload.get("updatedAt") or row["updated_at"]
        age = _optional_age_seconds(timestamp, now)
        if age is not None:
            values.append(age)
    return values


def _paper_toggle_transition_from_metric_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(str(row["payload_json"]))
    return {
        "newPaperEntriesEnabled": bool(payload.get("newPaperEntriesEnabled")),
        "updatedAt": str(payload.get("updatedAt") or row["created_at"]),
        "updatedBy": str(payload.get("updatedBy") or ""),
        "reason": str(payload.get("reason") or ""),
        "version": int(payload.get("version") or 0),
    }


def _rejected_entry_reasons(outbox_rows: Sequence[sqlite3.Row]) -> dict[str, int]:
    reasons: dict[str, int] = {}
    for row in outbox_rows:
        if str(row["status"]).upper() != "REJECTED":
            continue
        payload = json.loads(str(row["payload_json"]))
        if _intent_type(payload) != "new_entry":
            continue
        for code in tuple(payload.get("reasonCodes") or payload.get("reason_codes") or ()):
            reasons[str(code)] = reasons.get(str(code), 0) + 1
    return reasons


def _duplicate_order_attempts(outbox_payloads: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        1
        for payload in outbox_payloads
        for code in tuple(payload.get("reasonCodes") or payload.get("reason_codes") or ())
        if "duplicate" in str(code).lower()
    )


def _risk_reservation_summary(outbox_payloads: Sequence[Mapping[str, Any]]) -> dict[str, float | int]:
    values = [
        _float_value(payload, "reservedRiskDollars", "reserved_risk_dollars")
        for payload in outbox_payloads
        if _intent_type(payload) == "new_entry"
    ]
    return {"count": len(values), "totalReservedRiskDollars": round(sum(values), 10)}


def _decision_metric_action(payload: Mapping[str, Any], reason_codes: Sequence[str]) -> str:
    stages = payload.get("stages") if isinstance(payload.get("stages"), Mapping) else {}
    policy = stages.get("decisionPolicy") if isinstance(stages.get("decisionPolicy"), Mapping) else {}
    candidate = stages.get("aggregateCandidate") if isinstance(stages.get("aggregateCandidate"), Mapping) else {}
    action = str(
        payload.get("finalSignal")
        or payload.get("signal")
        or policy.get("finalSignal")
        or policy.get("signal")
        or candidate.get("direction")
        or candidate.get("signal")
        or ""
    ).upper()
    if action in {"BUY", "SELL"} and str(payload.get("decisionStatus") or "").upper() == "ORDER_PROPOSED":
        return action
    if payload.get("finalValid") is False or any(_blocked_reason(code) for code in reason_codes):
        return "BLOCKED"
    return "HOLD"


def _accumulate_strategy_metrics(payload: Mapping[str, Any], signal_counts: dict[str, dict[str, int]], abstentions: dict[str, int]) -> None:
    stages = payload.get("stages") if isinstance(payload.get("stages"), Mapping) else {}
    containers = (
        stages.get("strategyEvidence") if isinstance(stages.get("strategyEvidence"), Mapping) else {},
        stages.get("aggregateCandidate") if isinstance(stages.get("aggregateCandidate"), Mapping) else {},
    )
    directional: Mapping[str, Any] = {}
    for container in containers:
        candidate = container.get("directionalOutputs") if isinstance(container.get("directionalOutputs"), Mapping) else None
        if candidate:
            directional = candidate
            break
    for strategy_id, raw in directional.items():
        output = raw if isinstance(raw, Mapping) else {}
        signal = str(output.get("signal") or output.get("direction") or "HOLD").upper()
        if signal not in {"BUY", "SELL", "HOLD"}:
            signal = "HOLD"
        bucket = signal_counts.setdefault(str(strategy_id), {"BUY": 0, "SELL": 0, "HOLD": 0})
        bucket[signal] = bucket.get(signal, 0) + 1
        if signal == "HOLD" or output.get("eligible") is False:
            abstentions[str(strategy_id)] = abstentions.get(str(strategy_id), 0) + 1


def _accumulate_family_conflicts(payload: Mapping[str, Any], family_conflicts: dict[str, int]) -> None:
    stages = payload.get("stages") if isinstance(payload.get("stages"), Mapping) else {}
    containers = (
        stages.get("aggregateCandidate") if isinstance(stages.get("aggregateCandidate"), Mapping) else {},
        stages.get("decisionPolicy") if isinstance(stages.get("decisionPolicy"), Mapping) else {},
    )
    for container in containers:
        conflicts = container.get("conflictingFamilies") or container.get("familyConflicts") or ()
        if isinstance(conflicts, Mapping):
            for family, count in conflicts.items():
                family_conflicts[str(family)] = family_conflicts.get(str(family), 0) + int(count or 1)
        elif isinstance(conflicts, Sequence) and not isinstance(conflicts, (str, bytes)):
            for family in conflicts:
                family_conflicts[str(family)] = family_conflicts.get(str(family), 0) + 1


def _blocked_reason(code: str) -> bool:
    lowered = code.lower()
    return any(fragment in lowered for fragment in ("blocked", "reject", "missing_data", "local_gate", "safety"))


def _has_ml_failure(reason_codes: Sequence[str], payload: Mapping[str, Any]) -> bool:
    if any(
        "inference" in code.lower() and any(fragment in code.lower() for fragment in ("fail", "unavailable", "mismatch", "missing", "stale", "fallback"))
        for code in reason_codes
    ):
        return True
    stages = payload.get("stages") if isinstance(payload.get("stages"), Mapping) else {}
    model = stages.get("modelPrediction") if isinstance(stages.get("modelPrediction"), Mapping) else {}
    status = str(model.get("status") or model.get("modelStatus") or "").upper()
    return status in {"FAILED", "UNAVAILABLE", "INCOMPATIBLE", "STALE"}


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "META_STRATEGY_DEFAULT_QUEUE_CONCURRENCY_LIMITS",
    "META_STRATEGY_JOB_MIGRATION_VERSION",
    "META_STRATEGY_JOB_QUEUES",
    "META_STRATEGY_JOB_TYPE_TO_QUEUE",
    "MetaStrategyEventRecord",
    "MetaStrategyJobRecord",
    "MetaStrategyJobRepository",
    "MetaStrategyJobStatus",
    "MetaStrategyWorker",
    "apply_meta_strategy_job_migrations",
    "finalised_bar_idempotency_key",
    "migrate_meta_strategy_job_database",
]
