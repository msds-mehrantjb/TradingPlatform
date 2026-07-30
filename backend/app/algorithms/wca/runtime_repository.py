"""Database-backed WCA runtime queues, leases, checkpoints and health."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID
from backend.app.algorithms.wca.contracts import WcaLatencySnapshot
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.runtime_commands import WcaRuntimeCommand, WcaRuntimeCommandStatus, WcaRuntimeCommandType
from backend.app.algorithms.wca.runtime_events import WCA_RUNTIME_EVENT_SCHEMA_VERSION, WcaFinalizedBarEvent
from backend.app.algorithms.wca.runtime_health import WCA_RUNTIME_HEALTH_SCHEMA_VERSION, WcaRuntimeHealthSnapshot


WCA_RUNTIME_REPOSITORY_VERSION = "wca_runtime_repository_v1"


@dataclass(frozen=True)
class WcaRuntimeQueueResult:
    accepted: bool
    status: str
    reason_codes: tuple[str, ...]


class WcaRuntimeRepository:
    def __init__(self, repository: WcaSqliteRepository | None = None, database_url: str | None = None) -> None:
        self.repository = repository or WcaSqliteRepository(database_url)

    @property
    def path(self):
        return self.repository.path

    def publish_finalized_bar_event(
        self,
        event: WcaFinalizedBarEvent,
        *,
        account_id: str = "paper",
        max_queue_depth: int = 200,
        max_event_age_seconds: int = 300,
        now: datetime | None = None,
    ) -> WcaRuntimeQueueResult:
        current = now or _utc_now()
        if (current - event.publication_timestamp.astimezone(timezone.utc)).total_seconds() > max_event_age_seconds:
            return WcaRuntimeQueueResult(False, "rejected", ("wca.runtime.event.stale",))
        with self.repository.connect() as conn:
            if conn.execute("SELECT 1 FROM wca_runtime_event_queue WHERE event_id = ?", (event.event_id,)).fetchone():
                return WcaRuntimeQueueResult(False, "duplicate", ("wca.runtime.event.duplicate",))
            depth = _count_queue(conn, "wca_runtime_event_queue")
            if depth >= max_queue_depth:
                return WcaRuntimeQueueResult(False, "backpressure", ("wca.runtime.backpressure.event_queue_full",))
            latest = conn.execute(
                """
                SELECT MAX(finalized_candle_timestamp)
                FROM wca_runtime_event_queue
                WHERE symbol = ? AND status IN ('queued', 'processing', 'decision_queued', 'completed')
                """,
                (event.symbol,),
            ).fetchone()[0]
            if latest and event.finalized_candle_timestamp.astimezone(timezone.utc).isoformat() <= str(latest):
                return WcaRuntimeQueueResult(False, "rejected", ("wca.runtime.event.out_of_order",))
            queue_reason_codes = ["wca.runtime.event.accepted", *event.reason_codes]
            if latest:
                latest_timestamp = _parse_dt(str(latest))
                if event.finalized_candle_timestamp.astimezone(timezone.utc) - latest_timestamp > timedelta(minutes=1):
                    queue_reason_codes.append("wca.runtime.event.missing_minute_gap")
                    event = event.model_copy(
                        update={
                            "reason_codes": tuple(dict.fromkeys((*event.reason_codes, "wca.runtime.event.missing_minute_gap"))),
                            "missing_input_reason_codes": tuple(dict.fromkeys((*event.missing_input_reason_codes, "wca.runtime.event.missing_minute_gap"))),
                        }
                    )
            conn.execute(
                """
                INSERT INTO wca_runtime_event_queue (
                    event_id, algorithm_id, account_id, symbol, timestamp,
                    configuration_version, engine_version, market_snapshot_id,
                    decision_id, run_id, algorithm_subscription_id,
                    finalized_candle_timestamp, data_manifest_hash,
                    publication_timestamp, source, replay_or_recovery, status,
                    reason_codes_json, payload_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    WCA_ALGORITHM_ID,
                    account_id,
                    event.symbol,
                    _dt(current),
                    event.snapshot.configuration_version if event.snapshot is not None else "wca_runtime_event",
                    WCA_RUNTIME_EVENT_SCHEMA_VERSION,
                    event.data_manifest_hash,
                    f"wca-decision-{event.event_id}",
                    f"wca-runtime-{event.event_id}",
                    event.algorithm_subscription_id,
                    _dt(event.finalized_candle_timestamp),
                    event.data_manifest_hash,
                    _dt(event.publication_timestamp),
                    event.source,
                    1 if event.replay_or_recovery else 0,
                    "queued",
                    _json(tuple(dict.fromkeys(queue_reason_codes))),
                    event.model_dump_json(),
                    _dt(current),
                ),
            )
        return WcaRuntimeQueueResult(True, "queued", tuple(dict.fromkeys(queue_reason_codes)))

    def claim_next_event(self, *, owner_id: str, lease_seconds: int = 30) -> WcaFinalizedBarEvent | None:
        now = _utc_now()
        expires = now + timedelta(seconds=lease_seconds)
        with self.repository.connect() as conn:
            row = conn.execute(
                """
                SELECT event_id, payload_json
                FROM wca_runtime_event_queue
                WHERE status = 'queued'
                ORDER BY finalized_candle_timestamp ASC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                """
                UPDATE wca_runtime_event_queue
                SET status = 'processing', lease_owner = ?, lease_expires_at = ?,
                    attempt_count = attempt_count + 1, updated_at = ?
                WHERE event_id = ? AND status = 'queued'
                """,
                (owner_id, _dt(expires), _dt(now), row["event_id"]),
            )
            if cursor.rowcount != 1:
                return None
        return WcaFinalizedBarEvent.model_validate_json(row["payload_json"])

    def mark_event_decision_queued(self, event_id: str, *, command_id: str) -> None:
        now = _utc_now()
        with self.repository.connect() as conn:
            conn.execute(
                """
                UPDATE wca_runtime_event_queue
                SET status = 'decision_queued', lease_owner = NULL, lease_expires_at = NULL,
                    reason_codes_json = ?, updated_at = ?
                WHERE event_id = ?
                """,
                (_json(("wca.runtime.event.decision_command_queued", command_id)), _dt(now), event_id),
            )

    def complete_event_and_checkpoint(self, event: WcaFinalizedBarEvent, *, decision_id: str, run_id: str) -> None:
        now = _utc_now()
        checkpoint_payload = {
            "event_id": event.event_id,
            "decision_id": decision_id,
            "run_id": run_id,
            "symbol": event.symbol,
            "finalized_candle_timestamp": _dt(event.finalized_candle_timestamp),
            "completed_at": _dt(now),
            "reason_codes": ["wca.runtime.checkpoint.after_decision_commit"],
        }
        with self.repository.connect() as conn:
            conn.execute(
                """
                UPDATE wca_runtime_event_queue
                SET status = 'completed', decision_id = ?, run_id = ?,
                    lease_owner = NULL, lease_expires_at = NULL, reason_codes_json = ?,
                    updated_at = ?
                WHERE event_id = ?
                """,
                (decision_id, run_id, _json(("wca.runtime.event.completed",)), _dt(now), event.event_id),
            )
            row = conn.execute(
                "SELECT version FROM wca_runtime_checkpoints WHERE checkpoint_key = ?",
                (event.checkpoint_key,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO wca_runtime_checkpoints (
                        checkpoint_key, algorithm_id, account_id, symbol, timestamp,
                        configuration_version, engine_version, market_snapshot_id,
                        decision_id, run_id, version, payload_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (event.checkpoint_key, WCA_ALGORITHM_ID, "paper", event.symbol, _dt(now), event.snapshot.configuration_version if event.snapshot else "wca_runtime_checkpoint", WCA_RUNTIME_REPOSITORY_VERSION, event.event_id, decision_id, run_id, 1, _json(checkpoint_payload), _dt(now)),
                )
            else:
                conn.execute(
                    """
                    UPDATE wca_runtime_checkpoints
                    SET version = version + 1, timestamp = ?, market_snapshot_id = ?,
                        decision_id = ?, run_id = ?, payload_json = ?, updated_at = ?
                    WHERE checkpoint_key = ?
                    """,
                    (_dt(now), event.event_id, decision_id, run_id, _json(checkpoint_payload), _dt(now), event.checkpoint_key),
                )

    def enqueue_command(self, command: WcaRuntimeCommand, *, max_queue_depth: int = 500) -> WcaRuntimeQueueResult:
        with self.repository.connect() as conn:
            if conn.execute("SELECT 1 FROM wca_runtime_command_queue WHERE command_id = ?", (command.command_id,)).fetchone():
                return WcaRuntimeQueueResult(False, "duplicate", ("wca.runtime.command.duplicate",))
            if _count_queue(conn, "wca_runtime_command_queue") >= max_queue_depth:
                return WcaRuntimeQueueResult(False, "backpressure", ("wca.runtime.backpressure.command_queue_full",))
            conn.execute(
                """
                INSERT INTO wca_runtime_command_queue (
                    command_id, algorithm_id, account_id, symbol, timestamp,
                    configuration_version, engine_version, market_snapshot_id,
                    decision_id, run_id, event_id, command_type, priority, status,
                    reason_codes_json, payload_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command.command_id,
                    WCA_ALGORITHM_ID,
                    command.account_id,
                    command.symbol,
                    _dt(command.created_at),
                    str(command.payload.get("configuration_version") or "wca_runtime_command"),
                    command.schema_version,
                    command.event_id or command.command_id,
                    command.decision_id or command.command_id,
                    command.run_id or command.command_id,
                    command.event_id,
                    _value(command.command_type),
                    command.priority,
                    _value(command.status),
                    _json(command.reason_codes),
                    command.model_dump_json(),
                    _dt(_utc_now()),
                ),
            )
        return WcaRuntimeQueueResult(True, "queued", ("wca.runtime.command.queued",))

    def claim_next_command(self, command_type: WcaRuntimeCommandType, *, owner_id: str, lease_seconds: int = 30) -> WcaRuntimeCommand | None:
        now = _utc_now()
        expires = now + timedelta(seconds=lease_seconds)
        with self.repository.connect() as conn:
            row = conn.execute(
                """
                SELECT command_id, payload_json
                FROM wca_runtime_command_queue
                WHERE command_type = ? AND status = 'queued'
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """,
                (_value(command_type),),
            ).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                """
                UPDATE wca_runtime_command_queue
                SET status = 'running', lease_owner = ?, lease_expires_at = ?,
                    attempt_count = attempt_count + 1, updated_at = ?
                WHERE command_id = ? AND status = 'queued'
                """,
                (owner_id, _dt(expires), _dt(now), row["command_id"]),
            )
            if cursor.rowcount != 1:
                return None
        return WcaRuntimeCommand.model_validate_json(row["payload_json"])

    def complete_command(self, command_id: str, *, reason_codes: tuple[str, ...] = ("wca.runtime.command.completed",)) -> None:
        self._terminal_command(command_id, WcaRuntimeCommandStatus.COMPLETED, reason_codes)

    def block_command(self, command_id: str, *, reason_codes: tuple[str, ...]) -> None:
        self._terminal_command(command_id, WcaRuntimeCommandStatus.BLOCKED, reason_codes)

    def fail_command(self, command_id: str, *, reason_codes: tuple[str, ...]) -> None:
        self._terminal_command(command_id, WcaRuntimeCommandStatus.FAILED, reason_codes)

    def acquire_symbol_lease(self, *, symbol: str, owner_id: str, ttl_seconds: int = 30, account_id: str = "paper") -> bool:
        now = _utc_now()
        expires = now + timedelta(seconds=ttl_seconds)
        lease_key = f"wca.runtime.symbol.{symbol}"
        payload = {"owner_id": owner_id, "symbol": symbol, "lease_expires_at": _dt(expires)}
        with self.repository.connect() as conn:
            row = conn.execute(
                "SELECT owner_id, lease_expires_at, version FROM wca_runtime_symbol_leases WHERE lease_key = ?",
                (lease_key,),
            ).fetchone()
            if row is not None and row["owner_id"] != owner_id and _parse_dt(row["lease_expires_at"]) > now:
                return False
            if row is None:
                conn.execute(
                    """
                    INSERT INTO wca_runtime_symbol_leases (
                        lease_key, algorithm_id, account_id, symbol, owner_id,
                        lease_expires_at, version, payload_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (lease_key, WCA_ALGORITHM_ID, account_id, symbol, owner_id, _dt(expires), 1, _json(payload), _dt(now)),
                )
            else:
                conn.execute(
                    """
                    UPDATE wca_runtime_symbol_leases
                    SET owner_id = ?, lease_expires_at = ?, version = version + 1,
                        payload_json = ?, updated_at = ?
                    WHERE lease_key = ?
                    """,
                    (owner_id, _dt(expires), _json(payload), _dt(now), lease_key),
                )
        return True

    def release_symbol_lease(self, *, symbol: str, owner_id: str) -> None:
        lease_key = f"wca.runtime.symbol.{symbol}"
        with self.repository.connect() as conn:
            conn.execute(
                "DELETE FROM wca_runtime_symbol_leases WHERE lease_key = ? AND owner_id = ?",
                (lease_key, owner_id),
            )

    def recover_expired_work(self, *, now: datetime | None = None) -> dict[str, int]:
        current = now or _utc_now()
        with self.repository.connect() as conn:
            events = conn.execute(
                """
                UPDATE wca_runtime_event_queue
                SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                    reason_codes_json = ?, updated_at = ?
                WHERE status = 'processing' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
                """,
                (_json(("wca.runtime.recovery.event_requeued",)), _dt(current), _dt(current)),
            ).rowcount
            commands = conn.execute(
                """
                UPDATE wca_runtime_command_queue
                SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                    reason_codes_json = ?, updated_at = ?
                WHERE status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
                """,
                (_json(("wca.runtime.recovery.command_requeued",)), _dt(current), _dt(current)),
            ).rowcount
        return {"events_requeued": int(events), "commands_requeued": int(commands)}

    def queue_depths(self) -> dict[str, int]:
        with self.repository.connect() as conn:
            return {
                "events": _count_queue(conn, "wca_runtime_event_queue"),
                "commands": _count_queue(conn, "wca_runtime_command_queue"),
            }

    def queue_ages(self, *, now: datetime | None = None) -> dict[str, float]:
        current = now or _utc_now()
        with self.repository.connect() as conn:
            event_age = _oldest_age_seconds(conn, "wca_runtime_event_queue", current)
            command_age = _oldest_age_seconds(conn, "wca_runtime_command_queue", current)
        return {"events": event_age, "commands": command_age, "maximum": max(event_age, command_age)}

    def database_available(self) -> bool:
        try:
            with self.repository.connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def record_latency_snapshot(self, latency: WcaLatencySnapshot | None, *, account_id: str = "paper", symbol: str = "SPY", timestamp: datetime | None = None) -> None:
        if latency is None:
            return
        current = timestamp or _utc_now()
        metrics = latency.metrics.model_dump(mode="python")
        for field_name, value in metrics.items():
            if not field_name.endswith("_seconds"):
                continue
            self.record_latency_observation(
                component=field_name.removesuffix("_seconds"),
                value_seconds=value,
                account_id=account_id,
                symbol=symbol,
                timestamp=current,
                reason_codes=latency.metrics.reason_codes,
                payload={"timestamps": latency.timestamps.model_dump(mode="json")},
            )

    def record_latency_observation(
        self,
        *,
        component: str,
        value_seconds: float | None,
        account_id: str = "paper",
        symbol: str = "SPY",
        timestamp: datetime | None = None,
        failed: bool = False,
        reason_codes: tuple[str, ...] = (),
        payload: dict[str, Any] | None = None,
    ) -> None:
        current = timestamp or _utc_now()
        latency_id = f"wca-latency-{account_id}-{symbol}-{component}-{uuid4().hex}"
        with self.repository.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_runtime_latency_observations (
                    latency_id, algorithm_id, account_id, symbol, timestamp,
                    component, value_seconds, failed, reason_codes_json, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (latency_id, WCA_ALGORITHM_ID, account_id, symbol, _dt(current), component, value_seconds, 1 if failed else 0, _json(reason_codes), _json(payload or {})),
            )
            rows = conn.execute(
                """
                SELECT value_seconds, failed
                FROM wca_runtime_latency_observations
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND component = ?
                """,
                (WCA_ALGORITHM_ID, account_id, symbol, component),
            ).fetchall()
            values = sorted(float(row["value_seconds"]) for row in rows if row["value_seconds"] is not None)
            failures = sum(1 for row in rows if int(row["failed"]))
            summary = {
                "component": component,
                "sample_count": len(values),
                "failure_count": failures,
                "p50_seconds": _percentile(values, 0.50),
                "p95_seconds": _percentile(values, 0.95),
                "p99_seconds": _percentile(values, 0.99),
                "max_seconds": max(values) if values else None,
            }
            conn.execute(
                """
                INSERT INTO wca_runtime_latency_summaries (
                    summary_id, algorithm_id, account_id, symbol, component,
                    sample_count, failure_count, p50_seconds, p95_seconds,
                    p99_seconds, max_seconds, payload_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(algorithm_id, account_id, symbol, component) DO UPDATE SET
                    sample_count = excluded.sample_count,
                    failure_count = excluded.failure_count,
                    p50_seconds = excluded.p50_seconds,
                    p95_seconds = excluded.p95_seconds,
                    p99_seconds = excluded.p99_seconds,
                    max_seconds = excluded.max_seconds,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    f"wca-latency-summary-{account_id}-{symbol}-{component}",
                    WCA_ALGORITHM_ID,
                    account_id,
                    symbol,
                    component,
                    summary["sample_count"],
                    summary["failure_count"],
                    summary["p50_seconds"],
                    summary["p95_seconds"],
                    summary["p99_seconds"],
                    summary["max_seconds"],
                    _json(summary),
                    _dt(current),
                ),
            )

    def read_latency_summaries(self, *, account_id: str = "paper", symbol: str = "SPY") -> dict[str, dict[str, Any]]:
        with self.repository.connect() as conn:
            rows = conn.execute(
                """
                SELECT component, sample_count, failure_count, p50_seconds, p95_seconds, p99_seconds, max_seconds
                FROM wca_runtime_latency_summaries
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                ORDER BY component
                """,
                (WCA_ALGORITHM_ID, account_id, symbol),
            ).fetchall()
        return {
            row["component"]: {
                "sample_count": int(row["sample_count"]),
                "failure_count": int(row["failure_count"]),
                "p50_seconds": row["p50_seconds"],
                "p95_seconds": row["p95_seconds"],
                "p99_seconds": row["p99_seconds"],
                "max_seconds": row["max_seconds"],
            }
            for row in rows
        }

    def last_processed_bar(self, *, symbol: str = "SPY") -> datetime | None:
        key = f"wca.runtime.finalized_bar.{symbol}"
        with self.repository.connect() as conn:
            row = conn.execute("SELECT payload_json FROM wca_runtime_checkpoints WHERE checkpoint_key = ?", (key,)).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        value = payload.get("finalized_candle_timestamp")
        return _parse_dt(value) if value else None

    def last_decision_id(self) -> str:
        with self.repository.connect() as conn:
            row = conn.execute("SELECT decision_id FROM wca_decisions ORDER BY created_at DESC LIMIT 1").fetchone()
        return str(row["decision_id"]) if row else ""

    def write_runtime_health(self, health: WcaRuntimeHealthSnapshot) -> None:
        with self.repository.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_runtime_health (
                    health_id, algorithm_id, account_id, symbol, timestamp,
                    configuration_version, engine_version, market_snapshot_id,
                    decision_id, run_id, status, block_new_entries, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    health.health_id,
                    WCA_ALGORITHM_ID,
                    health.account_id,
                    health.symbol,
                    _dt(health.heartbeat_at),
                    "wca_runtime_health",
                    WCA_RUNTIME_HEALTH_SCHEMA_VERSION,
                    health.health_id,
                    health.last_decision_id or health.health_id,
                    "wca-runtime",
                    health.status,
                    1 if health.block_new_entries else 0,
                    health.model_dump_json(),
                ),
            )

    def read_latest_runtime_health(self) -> WcaRuntimeHealthSnapshot | None:
        with self.repository.connect() as conn:
            row = conn.execute("SELECT payload_json FROM wca_runtime_health ORDER BY created_at DESC LIMIT 1").fetchone()
        return WcaRuntimeHealthSnapshot.model_validate_json(row["payload_json"]) if row else None

    def _terminal_command(self, command_id: str, status: WcaRuntimeCommandStatus, reason_codes: tuple[str, ...]) -> None:
        with self.repository.connect() as conn:
            conn.execute(
                """
                UPDATE wca_runtime_command_queue
                SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                    reason_codes_json = ?, updated_at = ?
                WHERE command_id = ?
                """,
                (status.value, _json(reason_codes), _dt(_utc_now()), command_id),
            )


def _count_queue(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE status IN ('queued', 'running', 'processing', 'decision_queued')").fetchone()[0])


def _oldest_age_seconds(conn: sqlite3.Connection, table: str, current: datetime) -> float:
    row = conn.execute(f"SELECT MIN(created_at) FROM {table} WHERE status IN ('queued', 'running', 'processing', 'decision_queued')").fetchone()
    if row is None or row[0] is None:
        return 0.0
    return max(0.0, (current - _parse_dt(str(row[0]))).total_seconds())


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, int(round((len(values) - 1) * percentile))))
    return values[index]


def _dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


__all__ = [
    "WCA_RUNTIME_REPOSITORY_VERSION",
    "WcaRuntimeQueueResult",
    "WcaRuntimeRepository",
]
