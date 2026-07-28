"""Single-writer status store for Voting Ensemble runtime commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from backend.app.algorithms.voting_ensemble.intelligence_capture import VotingEnsembleCaptureWriter, capture_operational_event
from backend.app.algorithms.voting_ensemble.runtime.commands import (
    VOTING_ENSEMBLE_EVALUATION_RESULT_CONTRACT_VERSION,
    VotingEnsembleRuntimeCommand,
)


VOTING_ENSEMBLE_STATUS_NAMESPACE = "voting_ensemble.runtime.status"
VOTING_ENSEMBLE_STATUS_STORE_VERSION = "voting_ensemble_status_store_v1"
VotingEnsembleJobStatus = Literal["queued", "running", "completed", "blocked", "expired", "failed"]
TERMINAL_STATUSES = {"completed", "blocked", "expired", "failed"}
ACTIVE_STATUSES = {"queued", "running"}


class VotingEnsembleJobNotFound(KeyError):
    pass


class VotingEnsembleJobNotReady(ValueError):
    pass


class VotingEnsembleStatusStore:
    """In-memory status namespace with one logical writer lock.

    The store is intentionally injectable so tests and a separable production worker
    process can share a persistence adapter later without changing API contracts.
    """

    def __init__(self, *, persistence_path: str | Path | None = None, capture_writer: VotingEnsembleCaptureWriter | None = None) -> None:
        self._lock = Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._idempotency_index: dict[str, str] = {}
        self._evaluation_index: dict[tuple[str, str, str, str], str] = {}
        self.writerNamespace = VOTING_ENSEMBLE_STATUS_NAMESPACE
        self.persistencePath = Path(persistence_path).resolve() if persistence_path is not None else None
        self.captureWriter = capture_writer
        self._load()

    def persist_queued(self, command: VotingEnsembleRuntimeCommand) -> tuple[dict[str, Any], bool]:
        with self._lock:
            existing_job_id = self._idempotency_index.get(command.idempotencyKey)
            if existing_job_id:
                return dict(self._jobs[existing_job_id]), False
            evaluation_key = command.evaluation_key
            if evaluation_key is not None and evaluation_key in self._evaluation_index:
                return dict(self._jobs[self._evaluation_index[evaluation_key]]), False
            record = {
                "algorithmId": "voting_ensemble",
                "statusNamespace": VOTING_ENSEMBLE_STATUS_NAMESPACE,
                "statusStoreVersion": VOTING_ENSEMBLE_STATUS_STORE_VERSION,
                "evaluationResultContractVersion": command.evaluationResultContractVersion,
                "jobId": command.jobId,
                "jobType": "evaluate" if command.commandKind in {"manual_evaluation", "finalized_bar_evaluation"} else command.commandKind,
                "commandId": command.commandId,
                "commandKind": command.commandKind,
                "priority": command.priority,
                "status": "queued",
                "symbol": command.symbol,
                "barEndTimestamp": _iso(command.barEndTimestamp) if command.barEndTimestamp else None,
                "settingsHash": command.settingsHash,
                "correlationId": command.correlationId,
                "idempotencyKey": command.idempotencyKey,
                "source": command.source,
                "command": command.model_dump(mode="json"),
                "createdAt": _iso(command.createdAt),
                "deadlineAt": _iso(command.deadlineAt),
                "updatedAt": _now(),
                "startedAt": None,
                "completedAt": None,
                "expiresAt": _iso(command.deadlineAt),
                "attempts": 0,
                "result": None,
                "error": None,
                "reasonCodes": ["voting_ensemble.runtime.command.queued"],
            }
            self._jobs[command.jobId] = record
            self._idempotency_index[command.idempotencyKey] = command.jobId
            if evaluation_key is not None:
                self._evaluation_index[evaluation_key] = command.jobId
            self._persist_locked()
            self._capture_worker_job_status_locked(record)
            return dict(record), True

    def mark_running(self, command: VotingEnsembleRuntimeCommand) -> dict[str, Any]:
        return self._update(
            command.jobId,
            {
                "status": "running",
                "startedAt": _now(),
                "updatedAt": _now(),
                "attempts": int(self._jobs.get(command.jobId, {}).get("attempts") or 0) + 1,
                "reasonCodes": ["voting_ensemble.runtime.command.running"],
            },
        )

    def complete(self, command: VotingEnsembleRuntimeCommand, result: dict[str, Any]) -> dict[str, Any]:
        return self._terminal(command, "completed", result=result, error=None, reason_code="voting_ensemble.runtime.command.completed")

    def fail(self, command: VotingEnsembleRuntimeCommand, error: str) -> dict[str, Any]:
        return self._terminal(command, "failed", result=None, error=error, reason_code="voting_ensemble.runtime.command.failed")

    def block(self, command: VotingEnsembleRuntimeCommand, error: str) -> dict[str, Any]:
        return self._terminal(command, "blocked", result=None, error=error, reason_code="voting_ensemble.runtime.command.blocked")

    def expire(self, command: VotingEnsembleRuntimeCommand, error: str = "Voting Ensemble command expired before execution") -> dict[str, Any]:
        return self._terminal(command, "expired", result=None, error=error, reason_code="voting_ensemble.runtime.command.expired")

    def recover_incomplete(self) -> list[str]:
        recovered: list[str] = []
        with self._lock:
            for job_id, record in list(self._jobs.items()):
                if record["status"] in ACTIVE_STATUSES:
                    self._jobs[job_id] = {
                        **record,
                        "status": "queued",
                        "updatedAt": _now(),
                        "reasonCodes": ["voting_ensemble.runtime.command.recovered_after_worker_restart"],
                    }
                    recovered.append(job_id)
            if recovered:
                self._persist_locked()
        return recovered

    def recoverable_commands(self) -> tuple[VotingEnsembleRuntimeCommand, ...]:
        with self._lock:
            commands: list[VotingEnsembleRuntimeCommand] = []
            for record in self._jobs.values():
                if record["status"] in ACTIVE_STATUSES and isinstance(record.get("command"), dict):
                    commands.append(VotingEnsembleRuntimeCommand.model_validate(record["command"]))
            return tuple(commands)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise VotingEnsembleJobNotFound(job_id)
            return _public_record(record)

    def get_result(self, job_id: str) -> dict[str, Any]:
        record = self.get_job(job_id)
        if record["status"] != "completed":
            raise VotingEnsembleJobNotReady(job_id)
        return {
            "algorithmId": "voting_ensemble",
            "statusNamespace": VOTING_ENSEMBLE_STATUS_NAMESPACE,
            "jobId": job_id,
            "commandId": record["commandId"],
            "status": record["status"],
            "correlationId": record["correlationId"],
            "idempotencyKey": record["idempotencyKey"],
            "result": record["result"],
            "reasonCodes": ["voting_ensemble.runtime.result.ready"],
        }

    def list_jobs(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(_public_record(record) for record in self._jobs.values())

    def summary(self) -> dict[str, Any]:
        jobs = self.list_jobs()
        return {
            "statusNamespace": VOTING_ENSEMBLE_STATUS_NAMESPACE,
            "statusStoreVersion": VOTING_ENSEMBLE_STATUS_STORE_VERSION,
            "logicalWriter": self.writerNamespace,
            "persistencePath": str(self.persistencePath) if self.persistencePath is not None else None,
            "jobs": {status: sum(1 for job in jobs if job["status"] == status) for status in ("queued", "running", "completed", "blocked", "expired", "failed")},
            "reasonCodes": ["voting_ensemble.runtime.status_store.ready"],
        }

    def _terminal(
        self,
        command: VotingEnsembleRuntimeCommand,
        status: VotingEnsembleJobStatus,
        *,
        result: dict[str, Any] | None,
        error: str | None,
        reason_code: str,
    ) -> dict[str, Any]:
        payload = result
        if payload is not None:
            payload = {
                **payload,
                "correlationId": command.correlationId,
                "idempotencyKey": command.idempotencyKey,
                "commandId": command.commandId,
                "jobId": command.jobId,
            }
        return self._update(
            command.jobId,
            {
                "status": status,
                "completedAt": _now(),
                "updatedAt": _now(),
                "result": payload,
                "error": error,
                "reasonCodes": [reason_code],
            },
        )

    def _update(self, job_id: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise VotingEnsembleJobNotFound(job_id)
            self._jobs[job_id] = {**record, **values}
            self._persist_locked()
            self._capture_worker_job_status_locked(self._jobs[job_id])
            return dict(self._jobs[job_id])

    def _load(self) -> None:
        if self.persistencePath is None or not self.persistencePath.exists():
            return
        try:
            payload = json.loads(self.persistencePath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, dict):
            return
        with self._lock:
            self._jobs = {str(job_id): dict(record) for job_id, record in jobs.items() if isinstance(record, dict)}
            self._rebuild_indexes_locked()

    def _persist_locked(self) -> None:
        if self.persistencePath is None:
            return
        payload = {
            "statusNamespace": VOTING_ENSEMBLE_STATUS_NAMESPACE,
            "statusStoreVersion": VOTING_ENSEMBLE_STATUS_STORE_VERSION,
            "updatedAt": _now(),
            "jobs": self._jobs,
        }
        self.persistencePath.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.persistencePath.with_suffix(f"{self.persistencePath.suffix}.tmp")
        encoded = json.dumps(payload, sort_keys=True, indent=2)
        temporary.write_text(encoded, encoding="utf-8")
        try:
            temporary.replace(self.persistencePath)
        except PermissionError:
            self.persistencePath.write_text(encoded, encoding="utf-8")
            try:
                temporary.unlink()
            except OSError:
                pass

    def _rebuild_indexes_locked(self) -> None:
        self._idempotency_index = {}
        self._evaluation_index = {}
        for job_id, record in self._jobs.items():
            idempotency_key = record.get("idempotencyKey")
            if isinstance(idempotency_key, str) and idempotency_key:
                self._idempotency_index[idempotency_key] = job_id
            symbol = record.get("symbol")
            bar_end = record.get("barEndTimestamp")
            settings_hash = record.get("settingsHash")
            if isinstance(symbol, str) and isinstance(bar_end, str) and isinstance(settings_hash, str):
                self._evaluation_index[(symbol.upper(), bar_end, settings_hash, _evaluation_result_contract(record))] = job_id

    def _capture_worker_job_status_locked(self, record: dict[str, Any]) -> None:
        if self.captureWriter is None:
            return
        capture_operational_event(
            writer=self.captureWriter,
            event_type="worker_job_status",
            payload=_public_record(record),
            correlation_id=str(record.get("correlationId") or record.get("jobId")),
            job_id=str(record.get("jobId")) if record.get("jobId") else None,
            decision_id=str(record.get("commandId")) if record.get("commandId") else None,
            settings_hash=str(record.get("settingsHash")) if record.get("settingsHash") else None,
            snapshot_timestamp=_datetime_or_none(record.get("barEndTimestamp")),
        )


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    public = dict(record)
    public.pop("command", None)
    if public.get("status") != "completed":
        public.pop("result", None)
    return public


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _evaluation_result_contract(record: dict[str, Any]) -> str:
    command = record.get("command")
    if isinstance(command, dict):
        version = command.get("evaluationResultContractVersion") or command.get("resultContractVersion")
        if isinstance(version, str) and version:
            return version
    version = record.get("evaluationResultContractVersion") or record.get("resultContractVersion")
    if isinstance(version, str) and version:
        return version
    command_schema = record.get("command", {}).get("commandSchemaVersion") if isinstance(record.get("command"), dict) else None
    if command_schema == "voting_ensemble_runtime_command_v2":
        return VOTING_ENSEMBLE_EVALUATION_RESULT_CONTRACT_VERSION
    return "legacy_evaluation_result_contract"


def default_status_store_path() -> Path:
    return Path(__file__).resolve().parents[4] / "data" / "algorithms" / "voting_ensemble" / "runtime" / "status_store.json"
