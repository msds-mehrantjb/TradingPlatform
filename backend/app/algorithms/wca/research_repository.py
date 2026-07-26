"""Durable WCA research job repository."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.research_jobs import (
    TERMINAL_RESEARCH_JOB_STATUSES,
    WCA_RESEARCH_WORKER_VERSION,
    WcaResearchJob,
    WcaResearchJobReceipt,
    WcaResearchJobSnapshot,
    WcaResearchJobStatus,
)


WCA_RESEARCH_REPOSITORY_VERSION = "wca_research_repository_v1"


class WcaResearchRepository:
    def __init__(self, repository: WcaSqliteRepository | None = None, database_url: str | None = None) -> None:
        self.repository = repository or WcaSqliteRepository(database_url)

    @property
    def path(self):
        return self.repository.path

    def enqueue_job(self, job: WcaResearchJob) -> WcaResearchJobReceipt:
        with self.repository.connect() as conn:
            row = conn.execute("SELECT status FROM wca_background_jobs WHERE job_id = ?", (job.job_id,)).fetchone()
            if row is not None:
                return WcaResearchJobReceipt(job_id=job.job_id, job_type=job.job_type, status=row["status"], queued=False, reason_codes=("wca.research.job.duplicate",))
            conn.execute(
                """
                INSERT INTO wca_background_jobs (
                    job_id, algorithm_id, account_id, symbol, timestamp,
                    configuration_version, engine_version, market_snapshot_id,
                    decision_id, run_id, job_type, status, version,
                    payload_json, lease_owner, lease_expires_at, attempt_count,
                    max_attempts, progress_percent, cancel_requested, logs_json,
                    result_reference_json, error_json, expires_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, ?, 0, 0, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    WCA_ALGORITHM_ID,
                    job.account_id,
                    job.symbol,
                    _dt(job.created_at),
                    job.configuration_version,
                    WCA_RESEARCH_REPOSITORY_VERSION,
                    job.job_id,
                    job.job_id,
                    job.run_id or job.job_id,
                    _value(job.job_type),
                    WcaResearchJobStatus.QUEUED.value,
                    1,
                    job.model_dump_json(),
                    job.max_attempts,
                    _json(["wca.research.job.queued", *job.reason_codes]),
                    _json({}),
                    _json({}),
                    _dt(job.expires_at) if job.expires_at is not None else None,
                    _dt(_utc_now()),
                ),
            )
        return WcaResearchJobReceipt(job_id=job.job_id, job_type=job.job_type, status=WcaResearchJobStatus.QUEUED, queued=True, reason_codes=("wca.research.job.queued", *job.reason_codes))

    def claim_next_job(self, *, owner_id: str, lease_seconds: int = 300) -> WcaResearchJob | None:
        now = _utc_now()
        expires = now + timedelta(seconds=lease_seconds)
        with self.repository.connect() as conn:
            self._expire_overdue_jobs(conn, now)
            row = conn.execute(
                """
                SELECT job_id, payload_json
                FROM wca_background_jobs
                WHERE algorithm_id = ? AND status = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (WCA_ALGORITHM_ID, WcaResearchJobStatus.QUEUED.value),
            ).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                """
                UPDATE wca_background_jobs
                SET status = ?, lease_owner = ?, lease_expires_at = ?,
                    attempt_count = attempt_count + 1, logs_json = ?,
                    updated_at = ?, version = version + 1
                WHERE job_id = ? AND status = ?
                """,
                (
                    WcaResearchJobStatus.CLAIMED.value,
                    owner_id,
                    _dt(expires),
                    _json(["wca.research.job.claimed"]),
                    _dt(now),
                    row["job_id"],
                    WcaResearchJobStatus.QUEUED.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return WcaResearchJob.model_validate_json(row["payload_json"])

    def mark_running(self, job_id: str, *, owner_id: str) -> bool:
        with self.repository.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE wca_background_jobs
                SET status = ?, progress_percent = MAX(progress_percent, 1),
                    logs_json = ?, updated_at = ?, version = version + 1
                WHERE job_id = ? AND status = ? AND lease_owner = ?
                """,
                (WcaResearchJobStatus.RUNNING.value, _json(["wca.research.job.running"]), _dt(_utc_now()), job_id, WcaResearchJobStatus.CLAIMED.value, owner_id),
            )
        return cursor.rowcount == 1

    def update_progress(self, job_id: str, *, progress_percent: float, log: str | None = None) -> None:
        row = self._row(job_id)
        logs = _logs(row)
        if log:
            logs.append(log)
        with self.repository.connect() as conn:
            conn.execute(
                """
                UPDATE wca_background_jobs
                SET progress_percent = ?, logs_json = ?, updated_at = ?, version = version + 1
                WHERE job_id = ?
                """,
                (max(0.0, min(100.0, progress_percent)), _json(logs), _dt(_utc_now()), job_id),
            )

    def request_cancellation(self, job_id: str) -> bool:
        with self.repository.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE wca_background_jobs
                SET cancel_requested = 1, logs_json = ?, updated_at = ?, version = version + 1
                WHERE job_id = ? AND status NOT IN (?, ?, ?, ?, ?)
                """,
                (
                    _json(["wca.research.job.cancel_requested"]),
                    _dt(_utc_now()),
                    job_id,
                    WcaResearchJobStatus.SUCCEEDED.value,
                    WcaResearchJobStatus.FAILED.value,
                    WcaResearchJobStatus.CANCELLED.value,
                    WcaResearchJobStatus.EXPIRED.value,
                    WcaResearchJobStatus.QUARANTINED.value,
                ),
            )
        return cursor.rowcount == 1

    def cancellation_requested(self, job_id: str) -> bool:
        row = self._row(job_id)
        return bool(row and int(row["cancel_requested"]))

    def complete_job(self, job_id: str, *, result_reference: dict[str, Any], log: str = "wca.research.job.succeeded") -> None:
        self._terminal(job_id, WcaResearchJobStatus.SUCCEEDED, result_reference=result_reference, logs=(log,), progress_percent=100.0)

    def fail_job(self, job_id: str, *, error: dict[str, Any]) -> None:
        row = self._row(job_id)
        if row is not None and int(row["attempt_count"]) >= int(row["max_attempts"]):
            self._terminal(job_id, WcaResearchJobStatus.QUARANTINED, error=error, logs=("wca.research.job.quarantined_retry_limit",))
        else:
            self._terminal(job_id, WcaResearchJobStatus.FAILED, error=error, logs=("wca.research.job.failed",))

    def cancel_job(self, job_id: str) -> None:
        self._terminal(job_id, WcaResearchJobStatus.CANCELLED, logs=("wca.research.job.cancelled",))

    def save_candidate_result(self, *, job_id: str, candidate_type: str, candidate_version: str, payload: dict[str, Any], validation_status: str = "validated") -> str:
        candidate_id = f"wca-candidate-{candidate_type}-{candidate_version}"
        now = _utc_now()
        with self.repository.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wca_research_candidates (
                    candidate_id, algorithm_id, account_id, symbol, timestamp,
                    configuration_version, engine_version, market_snapshot_id,
                    decision_id, run_id, job_id, candidate_type, candidate_version,
                    validation_status, promotion_status, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    WCA_ALGORITHM_ID,
                    "paper",
                    "SPY",
                    _dt(now),
                    str(payload.get("configuration_version") or "wca_research_candidate"),
                    WCA_RESEARCH_WORKER_VERSION,
                    job_id,
                    job_id,
                    job_id,
                    job_id,
                    candidate_type,
                    candidate_version,
                    validation_status,
                    "pending_promotion",
                    _json(payload),
                ),
            )
        return candidate_id

    def read_job(self, job_id: str) -> WcaResearchJobSnapshot | None:
        row = self._row(job_id)
        if row is None:
            return None
        return WcaResearchJobSnapshot(
            job_id=row["job_id"],
            job_type=row["job_type"],
            status=row["status"],
            progress_percent=float(row["progress_percent"]),
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
            cancel_requested=bool(int(row["cancel_requested"])),
            result_reference=json.loads(row["result_reference_json"] or "{}"),
            logs=tuple(json.loads(row["logs_json"] or "[]")),
            error=json.loads(row["error_json"] or "{}"),
            reason_codes=(f"wca.research.job.{str(row['status']).lower()}",),
        )

    def _terminal(
        self,
        job_id: str,
        status: WcaResearchJobStatus,
        *,
        result_reference: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        logs: tuple[str, ...] = (),
        progress_percent: float | None = None,
    ) -> None:
        with self.repository.connect() as conn:
            conn.execute(
                """
                UPDATE wca_background_jobs
                SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                    progress_percent = COALESCE(?, progress_percent),
                    result_reference_json = ?, error_json = ?, logs_json = ?,
                    updated_at = ?, version = version + 1
                WHERE job_id = ?
                """,
                (
                    status.value,
                    progress_percent,
                    _json(result_reference or {}),
                    _json(error or {}),
                    _json(list(logs)),
                    _dt(_utc_now()),
                    job_id,
                ),
            )

    def _expire_overdue_jobs(self, conn: sqlite3.Connection, now: datetime) -> None:
        conn.execute(
            """
            UPDATE wca_background_jobs
            SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                logs_json = ?, updated_at = ?, version = version + 1
            WHERE algorithm_id = ? AND status NOT IN (?, ?, ?, ?, ?)
              AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (
                WcaResearchJobStatus.EXPIRED.value,
                _json(["wca.research.job.expired"]),
                _dt(now),
                WCA_ALGORITHM_ID,
                *tuple(sorted(TERMINAL_RESEARCH_JOB_STATUSES)),
                _dt(now),
            ),
        )
        conn.execute(
            """
            UPDATE wca_background_jobs
            SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                logs_json = ?, updated_at = ?, version = version + 1
            WHERE algorithm_id = ? AND status IN (?, ?)
              AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
            """,
            (
                WcaResearchJobStatus.QUEUED.value,
                _json(["wca.research.job.lease_expired_requeued"]),
                _dt(now),
                WCA_ALGORITHM_ID,
                WcaResearchJobStatus.CLAIMED.value,
                WcaResearchJobStatus.RUNNING.value,
                _dt(now),
            ),
        )

    def _row(self, job_id: str):
        with self.repository.connect() as conn:
            return conn.execute("SELECT * FROM wca_background_jobs WHERE job_id = ?", (job_id,)).fetchone()


def _logs(row) -> list[str]:
    if row is None:
        return []
    return list(json.loads(row["logs_json"] or "[]"))


def _dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


__all__ = [
    "WCA_RESEARCH_REPOSITORY_VERSION",
    "WcaResearchRepository",
]
