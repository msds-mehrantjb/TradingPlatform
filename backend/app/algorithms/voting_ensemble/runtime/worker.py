"""Worker adapters for the Voting Ensemble runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Event, Thread
from time import sleep
from typing import TYPE_CHECKING, Any, Protocol

from backend.app.alpaca import AlpacaClient
from backend.app.config import get_settings
from backend.app.algorithms.voting_ensemble.runtime.commands import VotingEnsembleRuntimeCommand
from backend.app.algorithms.voting_ensemble.runtime.queue import VotingEnsemblePriorityQueue
from backend.app.algorithms.voting_ensemble.runtime.status_store import VotingEnsembleStatusStore
from backend.app.algorithms.voting_ensemble.pipeline import VotingEnsemblePipeline
from backend.app.tick_data import parse_timestamp


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
        self.quote_client = AlpacaClient(get_settings())

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
            payload = self._payload_with_fresh_nbbo(command.payload)
            result = self.service.evaluate(payload)
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
            payload = self._payload_with_fresh_nbbo(command.payload)
            result = self.service.evaluate(payload) if payload.get("data_timestamp") or payload.get("candles") else None
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

    def _payload_with_fresh_nbbo(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("symbol") or "SPY").upper()
        feed = str(payload.get("feed") or payload.get("marketDataFeed") or "iex").lower()
        try:
            quote = self.quote_client.get_latest_quote_sync(symbol=symbol, feed=feed)
        except Exception:
            return payload
        if not quote:
            return payload
        enriched = dict(payload)
        enriched["nbbo"] = quote
        timestamps = [
            parse_timestamp(enriched.get("data_timestamp")),
            parse_timestamp(quote.get("quoteTimestamp")),
            parse_timestamp(quote.get("lastTradeTimestamp")),
            parse_timestamp(quote.get("marketDataReceiptTimestamp")),
        ]
        latest = max((timestamp for timestamp in timestamps if timestamp is not None), default=None)
        if latest is not None:
            enriched["data_timestamp"] = latest.isoformat().replace("+00:00", "Z")
        context = dict(enriched.get("market_context") or {})
        context["nbbo"] = quote
        context["nbboSource"] = "worker_hydrated_alpaca_latest_quote"
        enriched["market_context"] = context
        return enriched


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
        self.startedAt: str | None = None
        self.lastError: str | None = None
        self.lastErrorAt: str | None = None

    def start(self) -> None:
        if not self._thread.is_alive():
            self.startedAt = datetime.now(UTC).isoformat()
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def snapshot(self) -> dict[str, Any]:
        return {
            "alive": self.is_alive(),
            "startedAt": self.startedAt,
            "lastError": self.lastError,
            "lastErrorAt": self.lastErrorAt,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.worker.process_once(timeout=0.25)
            except Exception as exc:  # pragma: no cover - defensive worker liveness guard
                self.lastError = str(exc) or type(exc).__name__
                self.lastErrorAt = datetime.now(UTC).isoformat()
                sleep(0.25)


def _is_stale(command: VotingEnsembleRuntimeCommand) -> bool:
    deadline = command.deadlineAt
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline.astimezone(UTC) <= datetime.now(UTC)


def _default_backtesting_adapter() -> "VotingEnsembleBacktestingAdapter":
    from backend.app.algorithms.voting_ensemble.backtesting_adapter import VotingEnsembleBacktestingAdapter

    return VotingEnsembleBacktestingAdapter()
