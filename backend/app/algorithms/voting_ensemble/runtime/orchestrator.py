"""Runtime orchestration boundary for Voting Ensemble commands."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.voting_ensemble.models import VotingEnsembleEvaluateRequest
from backend.app.algorithms.voting_ensemble.runtime.commands import (
    VotingEnsembleRuntimeCommand,
    backtest_command,
    manual_evaluation_command,
    recovery_reconciliation_command,
    replay_command,
    settings_refresh_command,
)
from backend.app.algorithms.voting_ensemble.runtime.events import FinalizedOneMinuteBarEvent
from backend.app.algorithms.voting_ensemble.runtime.queue import VotingEnsembleBackpressureError, VotingEnsemblePriorityQueue
from backend.app.algorithms.voting_ensemble.runtime.status_store import (
    VOTING_ENSEMBLE_STATUS_NAMESPACE,
    VotingEnsembleJobNotFound,
    VotingEnsembleJobNotReady,
    VotingEnsembleStatusStore,
    default_status_store_path,
)
from backend.app.algorithms.voting_ensemble.runtime.worker import (
    InProcessVotingEnsembleWorkerAdapter,
    VotingEnsembleAutomaticPayloadBuilder,
    VotingEnsembleEvaluator,
    VotingEnsembleWorker,
    VotingEnsembleWorkerThread,
)
from backend.app.algorithms.voting_ensemble.paper_execution import VotingEnsemblePaperExecutionRuntime


VOTING_ENSEMBLE_RUNTIME_VERSION = "voting_ensemble_background_runtime_v1"


class VotingEnsembleRuntimeOrchestrator:
    """Dedicated Voting Ensemble command ingress and worker coordinator."""

    def __init__(
        self,
        *,
        service: VotingEnsembleEvaluator | None = None,
        status_store: VotingEnsembleStatusStore | None = None,
        queue: VotingEnsemblePriorityQueue | None = None,
        auto_start: bool = False,
        high_watermark: int = 128,
        low_watermark: int = 32,
        status_store_path: str | None = None,
        paper_execution_runtime: VotingEnsemblePaperExecutionRuntime | None = None,
        automatic_payload_builder: VotingEnsembleAutomaticPayloadBuilder | None = None,
    ) -> None:
        persistence_path = status_store_path if status_store_path is not None else (default_status_store_path() if auto_start else None)
        self.status_store = status_store or VotingEnsembleStatusStore(persistence_path=persistence_path)
        self.queue = queue or VotingEnsemblePriorityQueue(high_watermark=high_watermark, low_watermark=low_watermark)
        self.worker = VotingEnsembleWorker(
            queue=self.queue,
            status_store=self.status_store,
            service=service,
            paper_execution_runtime=paper_execution_runtime,
            automatic_payload_builder=automatic_payload_builder,
        )
        self.paper_execution_runtime = self.worker.paper_execution_runtime
        self.in_process_adapter = InProcessVotingEnsembleWorkerAdapter(self.worker)
        self._thread: VotingEnsembleWorkerThread | None = None
        self.workerMode = "separable_worker_process_contract"
        self.autoManageWorker = auto_start
        if auto_start:
            self.recover_incomplete_jobs()
            self.start()

    def set_automatic_payload_builder(self, builder: VotingEnsembleAutomaticPayloadBuilder | None) -> None:
        self.worker.automatic_payload_builder = builder

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = VotingEnsembleWorkerThread(self.worker)
            self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._thread.stop()
            self._thread = None

    def ensure_worker_running(self) -> dict[str, Any]:
        was_alive = bool(self._thread and self._thread.is_alive())
        if self.autoManageWorker and not was_alive:
            self.start()
        return {
            "workerAliveBefore": was_alive,
            "workerAliveAfter": bool(self._thread and self._thread.is_alive()),
            "reasonCodes": [
                "voting_ensemble.runtime.worker_running"
                if was_alive
                else "voting_ensemble.runtime.worker_restarted"
                if self.autoManageWorker
                else "voting_ensemble.runtime.worker_manual_mode_not_started"
            ],
        }

    def enqueue_command(self, command: VotingEnsembleRuntimeCommand) -> dict[str, Any]:
        self.ensure_worker_running()
        record, accepted = self.status_store.persist_queued(command)
        if not accepted:
            return {
                **self.status_store.get_job(record["jobId"]),
                "accepted": False,
                "deduplicated": True,
                "reasonCodes": ["voting_ensemble.runtime.command.deduplicated"],
            }
        try:
            self.queue.enqueue(command)
        except VotingEnsembleBackpressureError as exc:
            blocked = self.status_store.block(command, str(exc))
            return {
                **_public(blocked),
                "accepted": False,
                "deduplicated": False,
                "reasonCodes": ["voting_ensemble.runtime.command.backpressure_blocked"],
            }
        return {
            **self.status_store.get_job(command.jobId),
            "accepted": True,
            "deduplicated": False,
            "reasonCodes": ["voting_ensemble.runtime.command.enqueued"],
        }

    def enqueue_manual_evaluation(
        self,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
        deadline_seconds: int = 30,
        settings_hash: str | None = None,
    ) -> dict[str, Any]:
        request = VotingEnsembleEvaluateRequest.model_validate(payload)
        command = manual_evaluation_command(
            request.model_dump(mode="json"),
            correlation_id=correlation_id,
            deadline_seconds=deadline_seconds,
            settings_hash=settings_hash,
        )
        return self.enqueue_command(command)

    def enqueue_finalized_bar_event(self, event: FinalizedOneMinuteBarEvent) -> dict[str, Any]:
        return self.enqueue_command(event.to_command())

    def enqueue_backtest(self, payload: dict[str, Any], *, correlation_id: str | None = None, deadline_seconds: int = 3600) -> dict[str, Any]:
        return self.enqueue_command(backtest_command(payload, correlation_id=correlation_id, deadline_seconds=deadline_seconds))

    def enqueue_replay(self, payload: dict[str, Any], *, correlation_id: str | None = None, deadline_seconds: int = 1800) -> dict[str, Any]:
        return self.enqueue_command(replay_command(payload, correlation_id=correlation_id, deadline_seconds=deadline_seconds))

    def enqueue_settings_refresh(self, payload: dict[str, Any], *, correlation_id: str | None = None, deadline_seconds: int = 120) -> dict[str, Any]:
        return self.enqueue_command(settings_refresh_command(payload, correlation_id=correlation_id, deadline_seconds=deadline_seconds))

    def enqueue_recovery_reconciliation(self, payload: dict[str, Any], *, correlation_id: str | None = None, deadline_seconds: int = 300) -> dict[str, Any]:
        return self.enqueue_command(recovery_reconciliation_command(payload, correlation_id=correlation_id, deadline_seconds=deadline_seconds))

    def recover_incomplete_jobs(self) -> dict[str, Any]:
        recovered_job_ids = set(self.status_store.recover_incomplete())
        requeued: list[str] = []
        for command in self.status_store.recoverable_commands():
            if command.jobId not in recovered_job_ids:
                continue
            try:
                self.queue.enqueue(command)
            except VotingEnsembleBackpressureError as exc:
                self.status_store.block(command, str(exc))
            else:
                requeued.append(command.jobId)
        return {
            "runtimeVersion": VOTING_ENSEMBLE_RUNTIME_VERSION,
            "statusNamespace": VOTING_ENSEMBLE_STATUS_NAMESPACE,
            "recoveredJobIds": sorted(recovered_job_ids),
            "requeuedJobIds": requeued,
            "reasonCodes": ["voting_ensemble.runtime.recovery.completed"],
        }

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self.status_store.get_job(job_id)

    def get_result(self, job_id: str) -> dict[str, Any]:
        return self.status_store.get_result(job_id)

    def drain_in_process(self, *, max_commands: int = 100) -> list[dict[str, Any]]:
        return self.in_process_adapter.drain(max_commands=max_commands)

    def summary(self) -> dict[str, Any]:
        self.ensure_worker_running()
        worker_thread = self._thread.snapshot() if self._thread is not None else {"alive": False}
        return {
            "runtimeVersion": VOTING_ENSEMBLE_RUNTIME_VERSION,
            "statusNamespace": VOTING_ENSEMBLE_STATUS_NAMESPACE,
            "workerMode": self.workerMode,
            "workerAlive": bool(worker_thread.get("alive")),
            "workerThread": worker_thread,
            "heavyProcessingInRequestPath": False,
            "singleLogicalWriter": self.status_store.writerNamespace,
            "queue": self.queue.snapshot(),
            "statusStore": self.status_store.summary(),
            "paperExecution": self.paper_execution_runtime.summary(),
            "reasonCodes": ["voting_ensemble.runtime.ready"],
        }


def _public(record: dict[str, Any]) -> dict[str, Any]:
    public = dict(record)
    public.pop("command", None)
    if public.get("status") != "completed":
        public.pop("result", None)
    return public


VOTING_ENSEMBLE_RUNTIME = VotingEnsembleRuntimeOrchestrator(auto_start=False, status_store_path=str(default_status_store_path()))
