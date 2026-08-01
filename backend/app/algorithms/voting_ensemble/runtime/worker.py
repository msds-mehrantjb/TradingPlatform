"""Worker adapters for the Voting Ensemble runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Event, Thread
from time import sleep
from typing import TYPE_CHECKING, Any, Protocol

from backend.app.alpaca import AlpacaClient
from backend.app.config import get_settings
from backend.app.algorithms.voting_ensemble.finalized_bar_producer import VotingEnsembleAutomaticSnapshotError
from backend.app.algorithms.voting_ensemble.runtime.commands import VotingEnsembleRuntimeCommand
from backend.app.algorithms.voting_ensemble.runtime.queue import VotingEnsemblePriorityQueue
from backend.app.algorithms.voting_ensemble.runtime.status_store import VotingEnsembleStatusStore
from backend.app.algorithms.voting_ensemble.pipeline import VotingEnsemblePipeline
from backend.app.algorithms.voting_ensemble.paper_execution import VOTING_ENSEMBLE_PAPER_EXECUTION_RUNTIME, VotingEnsemblePaperExecutionRuntime
from backend.app.tick_data import parse_timestamp


if TYPE_CHECKING:
    from backend.app.algorithms.voting_ensemble.backtesting_adapter import VotingEnsembleBacktestingAdapter


VOTING_ENSEMBLE_WORKER_VERSION = "voting_ensemble_runtime_worker_v1"


class VotingEnsembleEvaluator(Protocol):
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


class VotingEnsembleAutomaticPayloadBuilder(Protocol):
    def build(self, command: VotingEnsembleRuntimeCommand) -> dict[str, Any]:
        ...


class VotingEnsembleWorker:
    def __init__(
        self,
        *,
        queue: VotingEnsemblePriorityQueue,
        status_store: VotingEnsembleStatusStore,
        service: VotingEnsembleEvaluator | None = None,
        backtesting_adapter: "VotingEnsembleBacktestingAdapter | None" = None,
        paper_execution_runtime: VotingEnsemblePaperExecutionRuntime | None = None,
        automatic_payload_builder: VotingEnsembleAutomaticPayloadBuilder | None = None,
    ) -> None:
        self.queue = queue
        self.status_store = status_store
        self.service = service or VotingEnsemblePipeline()
        self.backtesting_adapter = backtesting_adapter or _default_backtesting_adapter()
        self.paper_execution_runtime = paper_execution_runtime or VOTING_ENSEMBLE_PAPER_EXECUTION_RUNTIME
        self.automatic_payload_builder = automatic_payload_builder
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
            if command.commandKind == "finalized_bar_evaluation":
                if self.automatic_payload_builder is None:
                    return _automatic_fail_closed_result(
                        command,
                        "Voting Ensemble automatic snapshot builder is unavailable.",
                        ["voting_ensemble.runtime.automatic_snapshot_builder_missing"],
                    )
                try:
                    authoritative_payload = self.automatic_payload_builder.build(command)
                except VotingEnsembleAutomaticSnapshotError as exc:
                    return _automatic_fail_closed_result(command, str(exc), exc.reason_codes, snapshot=exc.snapshot)
                except Exception as exc:
                    return _automatic_fail_closed_result(
                        command,
                        f"Voting Ensemble automatic snapshot construction failed: {exc}",
                        ["voting_ensemble.runtime.automatic_snapshot_construction_failed"],
                    )
                payload = authoritative_payload
            else:
                authoritative_payload = {
                    **command.payload,
                    "runtimeMode": "manual_research",
                    "brokerSubmissionAllowed": False,
                }
                payload = self._payload_with_fresh_nbbo(authoritative_payload)
            try:
                result = self.service.evaluate(payload)
            except Exception as exc:
                if command.commandKind == "finalized_bar_evaluation":
                    return _automatic_fail_closed_result(
                        command,
                        f"Voting Ensemble automatic evaluation failed closed: {exc}",
                        ["voting_ensemble.runtime.automatic_evaluation_failed_closed"],
                    )
                raise
            if command.commandKind == "manual_evaluation":
                result = {
                    **result,
                    "runtimeMode": "manual_research",
                    "brokerSubmissionAllowed": False,
                    "reason_codes": [
                        *list(result.get("reason_codes") or []),
                        "voting_ensemble.runtime.manual_research_no_broker_submission",
                    ],
                }
            execution_enqueue_result = None
            if command.commandKind == "finalized_bar_evaluation":
                execution_enqueue_result = self.paper_execution_runtime.enqueue_from_decision(
                    result,
                    correlation_id=command.correlationId,
                    idempotency_key=command.idempotencyKey,
                    source_job_id=command.jobId,
                    source_command_id=command.commandId,
                    evaluated_at=datetime.now(UTC),
                    source_command_kind=command.commandKind,
                )
            return {
                "algorithmId": "voting_ensemble",
                "commandKind": command.commandKind,
                "workerVersion": VOTING_ENSEMBLE_WORKER_VERSION,
                "orderSubmissionMode": "paper_only",
                "runtimeMode": "automatic_finalized_bar" if command.commandKind == "finalized_bar_evaluation" else "manual_research",
                "brokerSubmissionAllowed": command.commandKind == "finalized_bar_evaluation",
                "automaticRuntimeSnapshotHash": _snapshot_hash(payload) if command.commandKind == "finalized_bar_evaluation" else None,
                "decision": result,
                "paperExecution": execution_enqueue_result,
                "reasonCodes": [
                    "voting_ensemble.runtime.evaluation.completed",
                    "voting_ensemble.runtime.finalized_bar_order_intent_handed_to_execution_worker"
                    if execution_enqueue_result and execution_enqueue_result.get("enqueued")
                    else "voting_ensemble.runtime.no_order_intent_enqueued",
                ],
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
                "brokerSubmissionAllowed": False,
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
                "brokerSubmissionAllowed": False,
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
                "brokerSubmissionAllowed": False,
                "settingsHash": command.settingsHash,
                "reasonCodes": ["voting_ensemble.runtime.settings_refresh.completed"],
            }
        if command.commandKind == "recovery_reconciliation":
            recovered = self.status_store.recover_incomplete()
            return {
                "algorithmId": "voting_ensemble",
                "commandKind": command.commandKind,
                "workerVersion": VOTING_ENSEMBLE_WORKER_VERSION,
                "brokerSubmissionAllowed": False,
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
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

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


def _automatic_fail_closed_result(
    command: VotingEnsembleRuntimeCommand,
    message: str,
    reason_codes: list[str] | tuple[str, ...],
    *,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evaluated_at = datetime.now(UTC)
    session_date = (command.barEndTimestamp or evaluated_at).astimezone(UTC).date()
    order_plan = {
        "orderPlanId": f"ve-fail-closed-{command.commandId}",
        "candidateId": f"ve-fail-closed-candidate-{command.commandId}",
        "symbol": command.symbol.upper(),
        "side": "HOLD",
        "orderType": "NO_ORDER",
        "quantity": 0,
        "entryPrice": 1.0,
        "stopPrice": None,
        "targetPrice": None,
        "limitPrice": None,
        "maximumHoldingMinutes": None,
        "strategyInvalidationPrice": None,
        "endOfDayExit": True,
        "timeInForce": "DAY",
        "eligible": False,
        "validationErrors": list(reason_codes),
        "explanation": message,
        "generatedAt": evaluated_at.isoformat().replace("+00:00", "Z"),
        "sessionDate": session_date.isoformat(),
        "configurationHash": f"{command.settingsHash}:fail_closed",
    }
    decision = {
        "algorithm_id": "voting_ensemble",
        "symbol": command.symbol.upper(),
        "evaluated_at": evaluated_at.isoformat().replace("+00:00", "Z"),
        "data_timestamp": (command.barEndTimestamp or evaluated_at).astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "final_signal": "Hold",
        "safety_gate_failed": True,
        "order_plan": order_plan,
        "runtimeMode": "automatic_finalized_bar",
        "brokerSubmissionAllowed": False,
        "automaticSnapshotReady": False,
        "automaticRuntimeSnapshot": snapshot,
        "reason_codes": [
            "voting_ensemble.runtime.automatic_fail_closed_hold_quantity_zero",
            *list(reason_codes),
        ],
    }
    return {
        "algorithmId": "voting_ensemble",
        "commandKind": command.commandKind,
        "workerVersion": VOTING_ENSEMBLE_WORKER_VERSION,
        "orderSubmissionMode": "paper_only",
        "runtimeMode": "automatic_finalized_bar",
        "brokerSubmissionAllowed": False,
        "decision": decision,
        "paperExecution": {
            "algorithmId": "voting_ensemble",
            "algorithm_id": "voting_ensemble",
            "enqueued": False,
            "deduplicated": False,
            "reasonCodes": ["voting_ensemble.runtime.automatic_fail_closed_no_order_intent"],
        },
        "reasonCodes": decision["reason_codes"],
    }


def _snapshot_hash(payload: dict[str, Any]) -> str | None:
    context = payload.get("market_context") if isinstance(payload.get("market_context"), dict) else {}
    snapshot = context.get("automaticRuntimeSnapshot") or context.get("pointInTimeSnapshot")
    return snapshot.get("snapshotHash") if isinstance(snapshot, dict) else None


def _default_backtesting_adapter() -> "VotingEnsembleBacktestingAdapter":
    from backend.app.algorithms.voting_ensemble.backtesting_adapter import VotingEnsembleBacktestingAdapter

    return VotingEnsembleBacktestingAdapter()
