"""Worker adapters for the Voting Ensemble runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Event, Thread
from typing import TYPE_CHECKING, Any, Protocol

from backend.app.algorithms.voting_ensemble.runtime.commands import VotingEnsembleRuntimeCommand
from backend.app.algorithms.voting_ensemble.runtime.queue import VotingEnsemblePriorityQueue
from backend.app.algorithms.voting_ensemble.runtime.status_store import VotingEnsembleStatusStore
from backend.app.algorithms.voting_ensemble.pipeline import VotingEnsemblePipeline


if TYPE_CHECKING:
    from backend.app.algorithms.voting_ensemble.backtesting_adapter import VotingEnsembleBacktestingAdapter


VOTING_ENSEMBLE_WORKER_VERSION = "voting_ensemble_runtime_worker_v1"


class VotingEnsembleEvaluator(Protocol):
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


class VotingEnsembleWorker:
    def __init__(
        self,
        *,
        queue: VotingEnsemblePriorityQueue,
        status_store: VotingEnsembleStatusStore,
        service: VotingEnsembleEvaluator | None = None,
        backtesting_adapter: "VotingEnsembleBacktestingAdapter | None" = None,
    ) -> None:
        self.queue = queue
        self.status_store = status_store
        self.service = service or VotingEnsemblePipeline()
        self.backtesting_adapter = backtesting_adapter or _default_backtesting_adapter()

    def process_once(self, *, timeout: float | None = 0.0) -> dict[str, Any] | None:
        command = self.queue.pop(timeout=timeout)
        if command is None:
            return None
        if _is_stale(command):
            return self.status_store.expire(command)
        self.status_store.mark_running(command)
        try:
            result = self._execute(command)
        except Exception as exc:
            return self.status_store.fail(command, str(exc))
        return self.status_store.complete(command, result)

    def _execute(self, command: VotingEnsembleRuntimeCommand) -> dict[str, Any]:
        if command.commandKind in {"manual_evaluation", "finalized_bar_evaluation"}:
            result = self.service.evaluate(command.payload)
            return {
                "algorithmId": "voting_ensemble",
                "commandKind": command.commandKind,
                "workerVersion": VOTING_ENSEMBLE_WORKER_VERSION,
                "orderSubmissionMode": "paper_only",
                "decision": result,
                "reasonCodes": ["voting_ensemble.runtime.evaluation.completed"],
            }
        if command.commandKind == "backtest":
            result = self.backtesting_adapter.run_backtest(
                list(command.payload.get("candles") or command.payload.get("spy_1m_candles") or []),
                timeframe=str(command.payload.get("timeframe") or "1Min"),
                risk_config_override=command.payload.get("riskConfigOverride"),
            )
            return {
                "algorithmId": "voting_ensemble",
                "commandKind": command.commandKind,
                "workerVersion": VOTING_ENSEMBLE_WORKER_VERSION,
                "backtest": result,
                "reasonCodes": ["voting_ensemble.runtime.backtest.completed"],
            }
        if command.commandKind == "replay":
            result = self.service.evaluate(command.payload) if command.payload.get("data_timestamp") or command.payload.get("candles") else None
            return {
                "algorithmId": "voting_ensemble",
                "commandKind": command.commandKind,
                "workerVersion": VOTING_ENSEMBLE_WORKER_VERSION,
                "replayAccepted": True,
                "decision": result,
                "payload": command.payload,
                "reasonCodes": ["voting_ensemble.runtime.replay.command_recorded", "voting_ensemble.runtime.replay.uses_unified_pipeline"],
            }
        if command.commandKind == "settings_refresh":
            return {
                "algorithmId": "voting_ensemble",
                "commandKind": command.commandKind,
                "workerVersion": VOTING_ENSEMBLE_WORKER_VERSION,
                "settingsHash": command.settingsHash,
                "reasonCodes": ["voting_ensemble.runtime.settings_refresh.completed"],
            }
        if command.commandKind == "recovery_reconciliation":
            recovered = self.status_store.recover_incomplete()
            return {
                "algorithmId": "voting_ensemble",
                "commandKind": command.commandKind,
                "workerVersion": VOTING_ENSEMBLE_WORKER_VERSION,
                "recoveredJobIds": recovered,
                "reasonCodes": ["voting_ensemble.runtime.recovery_reconciliation.completed"],
            }
        raise ValueError(f"Unsupported Voting Ensemble command kind: {command.commandKind}")


class InProcessVotingEnsembleWorkerAdapter:
    """Test adapter that runs the production worker loop in-process."""

    def __init__(self, worker: VotingEnsembleWorker) -> None:
        self.worker = worker

    def drain(self, *, max_commands: int = 100) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for _ in range(max_commands):
            result = self.worker.process_once(timeout=0.0)
            if result is None:
                break
            results.append(result)
        return results


class VotingEnsembleWorkerThread:
    """Separable process-friendly adapter; production can replace this with a process runner."""

    def __init__(self, worker: VotingEnsembleWorker) -> None:
        self.worker = worker
        self._stop = Event()
        self._thread = Thread(target=self._run, name="voting-ensemble-runtime-worker", daemon=True)

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.worker.process_once(timeout=0.25)


def _is_stale(command: VotingEnsembleRuntimeCommand) -> bool:
    deadline = command.deadlineAt
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline.astimezone(UTC) <= datetime.now(UTC)


def _default_backtesting_adapter() -> "VotingEnsembleBacktestingAdapter":
    from backend.app.algorithms.voting_ensemble.backtesting_adapter import VotingEnsembleBacktestingAdapter

    return VotingEnsembleBacktestingAdapter()
