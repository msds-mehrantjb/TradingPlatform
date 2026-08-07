from __future__ import annotations

import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any

from fastapi.testclient import TestClient

import backend.app.algorithms.voting_ensemble.api as voting_ensemble_api
from backend.app.algorithms.voting_ensemble.runtime.commands import manual_evaluation_command
from backend.app.algorithms.voting_ensemble.runtime.events import FinalizedOneMinuteBarEvent
from backend.app.algorithms.voting_ensemble.runtime.orchestrator import VotingEnsembleRuntimeOrchestrator
from backend.app.algorithms.voting_ensemble.runtime.status_store import MAX_PERSISTED_TERMINAL_JOBS, VotingEnsembleStatusStore
from backend.app.algorithms.voting_ensemble.service import VotingEnsembleService
from backend.app.main import app


START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


class VotingEnsembleEvaluationJobsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_api_evaluate_returns_accepted_without_running_strategy_inline(self) -> None:
        release = Event()
        runtime = VotingEnsembleRuntimeOrchestrator(service=BlockingService(release), auto_start=False)
        original_runtime = voting_ensemble_api.VOTING_ENSEMBLE_RUNTIME
        voting_ensemble_api.VOTING_ENSEMBLE_RUNTIME = runtime
        started = time.monotonic()
        try:
            response = self.client.post("/api/voting-ensemble/evaluate", json=evaluate_payload(candles(8)))
        finally:
            voting_ensemble_api.VOTING_ENSEMBLE_RUNTIME = original_runtime
            release.set()

        elapsed = time.monotonic() - started
        self.assertEqual(response.status_code, 202, response.text)
        self.assertLess(elapsed, 0.5)
        job = response.json()
        self.assertEqual(job["algorithmId"], "voting_ensemble")
        self.assertEqual(job["commandKind"], "manual_evaluation")
        self.assertEqual(job["status"], "queued")
        self.assertIn("correlationId", job)
        self.assertIn("idempotencyKey", job)
        self.assertIn("/api/voting-ensemble/jobs/", job["statusUrl"])

    def test_evaluate_endpoint_enqueues_job_and_exposes_result(self) -> None:
        response = self.client.post("/api/voting-ensemble/evaluate", json=evaluate_payload(candles(45)))

        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        self.assertEqual(job["algorithmId"], "voting_ensemble")
        self.assertEqual(job["jobType"], "evaluate")

        completed = wait_for_api_job(self.client, job["statusUrl"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["algorithmId"], "voting_ensemble")

        result_response = self.client.get(job["resultUrl"])
        self.assertEqual(result_response.status_code, 200, result_response.text)
        result = result_response.json()
        self.assertEqual(result["jobId"], job["jobId"])
        self.assertEqual(result["correlationId"], job["correlationId"])
        self.assertEqual(result["idempotencyKey"], job["idempotencyKey"])
        self.assertEqual(result["result"]["decision"]["algorithm_id"], "voting_ensemble")
        self.assertIn(result["result"]["decision"]["final_signal"], {"Buy", "Sell", "Hold"})
        self.assertEqual(result["result"]["orderSubmissionMode"], "paper_only")

    def test_sync_compatibility_endpoint_now_enqueues_job(self) -> None:
        response = self.client.post("/api/voting-ensemble/evaluate/sync", json=evaluate_payload(candles(45)))

        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["algorithmId"], "voting_ensemble")
        self.assertEqual(body["commandKind"], "manual_evaluation")
        self.assertIn("jobId", body)

    def test_duplicate_finalized_bar_events_use_one_decision(self) -> None:
        service = CountingService()
        runtime = VotingEnsembleRuntimeOrchestrator(service=service, automatic_payload_builder=PassthroughAutomaticPayloadBuilder(), auto_start=False)
        event = FinalizedOneMinuteBarEvent(
            symbol="SPY",
            barEndTimestamp=START + timedelta(minutes=44),
            finalized=True,
            settingsHash="settings-a",
            evaluationPayload=evaluate_payload(candles(45)),
            correlationId="correlation-duplicate-test",
        )

        first = runtime.enqueue_finalized_bar_event(event)
        second = runtime.enqueue_finalized_bar_event(event)
        drained = runtime.drain_in_process()

        self.assertEqual(first["jobId"], second["jobId"])
        self.assertFalse(second["accepted"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(service.calls, 1)
        self.assertEqual(len(drained), 1)
        self.assertEqual(runtime.get_job(first["jobId"])["status"], "completed")

    def test_partial_one_minute_bar_event_is_rejected(self) -> None:
        runtime = VotingEnsembleRuntimeOrchestrator(service=CountingService(), auto_start=False)
        event = FinalizedOneMinuteBarEvent(
            symbol="SPY",
            barEndTimestamp=START,
            finalized=False,
            settingsHash="settings-a",
            evaluationPayload=evaluate_payload(candles(2)),
        )

        with self.assertRaises(ValueError):
            runtime.enqueue_finalized_bar_event(event)
        self.assertEqual(runtime.summary()["statusStore"]["jobs"]["queued"], 0)

    def test_stale_command_expires_without_evaluation(self) -> None:
        service = CountingService()
        runtime = VotingEnsembleRuntimeOrchestrator(service=service, auto_start=False)
        command = manual_evaluation_command(evaluate_payload(candles(4)))
        stale = command.model_copy(update={"deadlineAt": datetime.now(UTC) - timedelta(seconds=1)})

        job = runtime.enqueue_command(stale)
        runtime.drain_in_process()

        self.assertEqual(runtime.get_job(job["jobId"])["status"], "expired")
        self.assertEqual(service.calls, 0)

    def test_backpressure_blocks_excess_high_priority_work(self) -> None:
        runtime = VotingEnsembleRuntimeOrchestrator(service=CountingService(), auto_start=False, high_watermark=1)

        accepted = runtime.enqueue_manual_evaluation(evaluate_payload(candles(2)))
        blocked = runtime.enqueue_manual_evaluation(evaluate_payload(candles(3)))

        self.assertEqual(accepted["status"], "queued")
        self.assertEqual(blocked["status"], "blocked")
        self.assertFalse(blocked["accepted"])
        self.assertIn("queue is full", blocked["error"])

    def test_low_priority_backtest_does_not_block_high_priority_paper_evaluation(self) -> None:
        service = CountingService()
        runtime = VotingEnsembleRuntimeOrchestrator(service=service, auto_start=False)
        backtest = runtime.enqueue_backtest({"symbol": "SPY", "candles": candles(5), "settingsHash": "backtest-a"})
        evaluation = runtime.enqueue_manual_evaluation(evaluate_payload(candles(6)))

        runtime.drain_in_process(max_commands=1)

        self.assertEqual(runtime.get_job(evaluation["jobId"])["status"], "completed")
        self.assertEqual(runtime.get_job(backtest["jobId"])["status"], "queued")
        self.assertEqual(service.calls, 1)

    def test_worker_restart_requeues_running_job_without_duplicate_orders(self) -> None:
        status_store = VotingEnsembleStatusStore()
        crashed_runtime = VotingEnsembleRuntimeOrchestrator(service=CountingService(), status_store=status_store, auto_start=False)
        job = crashed_runtime.enqueue_manual_evaluation(evaluate_payload(candles(7)))
        command = crashed_runtime.queue.pop(timeout=0.0)
        self.assertIsNotNone(command)
        assert command is not None
        status_store.mark_running(command)

        restarted_runtime = VotingEnsembleRuntimeOrchestrator(service=CountingService(), status_store=status_store, auto_start=False)
        recovered = restarted_runtime.recover_incomplete_jobs()
        restarted_runtime.drain_in_process()

        self.assertEqual(recovered["requeuedJobIds"], [job["jobId"]])
        completed = restarted_runtime.get_job(job["jobId"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["attempts"], 2)
        self.assertEqual(completed["result"]["orderSubmissionMode"], "paper_only")

    def test_status_store_persists_queued_running_and_terminal_statuses(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime/status_store.json")
        store_path.parent.mkdir(parents=True, exist_ok=True)
        if store_path.exists():
            store_path.unlink()
        status_store = VotingEnsembleStatusStore(persistence_path=store_path)
        runtime = VotingEnsembleRuntimeOrchestrator(service=CountingService(), status_store=status_store, auto_start=False)
        job = runtime.enqueue_manual_evaluation(evaluate_payload(candles(9)))

        reloaded_store = VotingEnsembleStatusStore(persistence_path=store_path)
        restarted_runtime = VotingEnsembleRuntimeOrchestrator(service=CountingService(), status_store=reloaded_store, auto_start=False)
        recovered = restarted_runtime.recover_incomplete_jobs()
        restarted_runtime.drain_in_process()

        self.assertEqual(recovered["requeuedJobIds"], [job["jobId"]])
        completed = restarted_runtime.get_job(job["jobId"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["result"]["correlationId"], job["correlationId"])
        self.assertEqual(completed["result"]["idempotencyKey"], job["idempotencyKey"])

        final_store = VotingEnsembleStatusStore(persistence_path=store_path)
        self.assertEqual(final_store.get_job(job["jobId"])["status"], "completed")
        store_path.unlink()

    def test_status_store_prunes_old_terminal_commands_from_persistence(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime/status_store_pruned.json")
        store_path.parent.mkdir(parents=True, exist_ok=True)
        if store_path.exists():
            store_path.unlink()
        status_store = VotingEnsembleStatusStore(persistence_path=store_path)
        runtime = VotingEnsembleRuntimeOrchestrator(service=CountingService(), status_store=status_store, auto_start=False)
        large_payload = evaluate_payload(candles(500))

        for index in range(MAX_PERSISTED_TERMINAL_JOBS + 8):
            payload = dict(large_payload)
            payload["data_timestamp"] = (START + timedelta(minutes=45 + index)).isoformat().replace("+00:00", "Z")
            runtime.enqueue_manual_evaluation(payload)
            runtime.drain_in_process(max_commands=1)

        persisted = store_path.read_text(encoding="utf-8")
        final_store = VotingEnsembleStatusStore(persistence_path=store_path)

        self.assertLess(store_path.stat().st_size, 1_000_000)
        self.assertNotIn('"command"', persisted)
        self.assertLessEqual(final_store.summary()["jobs"]["completed"], MAX_PERSISTED_TERMINAL_JOBS)
        store_path.unlink()

    def test_status_reports_dedicated_runtime_contract(self) -> None:
        response = self.client.get("/api/voting-ensemble/status")

        self.assertEqual(response.status_code, 200, response.text)
        runtime = response.json()["runtime"]
        self.assertEqual(runtime["runtimeVersion"], "voting_ensemble_background_runtime_v1")
        self.assertEqual(runtime["statusNamespace"], "voting_ensemble.runtime.status")
        self.assertEqual(runtime["workerMode"], "separable_worker_process_contract")
        self.assertTrue(runtime["workerAlive"])
        self.assertTrue(runtime["workerThread"]["alive"])
        self.assertFalse(runtime["heavyProcessingInRequestPath"])
        self.assertEqual(runtime["singleLogicalWriter"], "voting_ensemble.runtime.status")

    def test_runtime_summary_restarts_dead_worker_thread(self) -> None:
        runtime = VotingEnsembleRuntimeOrchestrator(
            service=CountingService(),
            status_store=VotingEnsembleStatusStore(),
            auto_start=True,
        )

        first = runtime.summary()
        runtime.stop()
        restarted = runtime.summary()

        self.assertTrue(first["workerAlive"])
        self.assertTrue(restarted["workerAlive"])
        runtime.stop()

    def test_fail_closed_evaluation_returns_strategy_blocker_votes(self) -> None:
        result = VotingEnsembleService().evaluate(evaluate_payload(candles(45)))

        self.assertEqual(result["final_signal"], "Hold")
        self.assertIn("missing_spy_nbbo", result["reason_codes"])
        self.assertGreater(len(result["votes"]), 0)
        first_vote = result["votes"][0]
        self.assertEqual(first_vote["signal"], "Hold")
        self.assertFalse(first_vote["eligible"])
        self.assertFalse(first_vote["dataReady"])
        self.assertIn("missing_spy_nbbo", first_vote["reason"])
        self.assertEqual(result["counts"]["Hold"], len(result["votes"]))
        self.assertEqual(result["eligible_counts"], {"Buy": 0, "Sell": 0, "Hold": 0})

    def test_automatic_paper_disabled_does_not_block_strategy_evaluation(self) -> None:
        payload = evaluate_payload(candles(45))
        payload["nbbo"] = nbbo(payload["data_timestamp"])
        payload["market_context"] = {
            "sessionState": {"phase": "regular", "marketClosed": False, "marketStatus": "open"},
            "operationalHealthSnapshot": operational_permission_contract(payload["data_timestamp"], enabled=False),
        }

        result = VotingEnsembleService().evaluate(payload)

        self.assertEqual(result["final_signal"], "Hold")
        self.assertNotIn("voting_ensemble.evaluate.blocked_by_local_safety", result["reason_codes"])
        self.assertIn("voting_ensemble.local_gate.trading_disabled", result["reason_codes"])
        self.assertIn("voting_ensemble.local_gate.global_upstream_advisory_block", result["reason_codes"])
        self.assertNotIn("voting_ensemble.local_gate.global_hard_gate_block", result["reason_codes"])
        self.assertGreater(len(result["votes"]), 0)
        self.assertFalse(any("local safety gates blocked" in vote["reason"] for vote in result["votes"]))
        self.assertEqual(sum(result["counts"].values()), len(result["votes"]))
        self.assertEqual(result["local_gate_decision"]["eligible"], False)

    def test_dashboard_permission_contract_clears_upstream_and_trading_switch_blockers(self) -> None:
        payload = evaluate_payload(candles(45))
        payload["nbbo"] = nbbo(payload["data_timestamp"])
        payload["market_context"] = {
            "sessionState": {"phase": "regular", "marketClosed": False, "marketStatus": "open"},
            "operationalHealthSnapshot": operational_permission_contract(payload["data_timestamp"], enabled=True),
        }

        result = VotingEnsembleService().evaluate(payload)
        reason_codes = set(result["reason_codes"])

        self.assertNotIn("voting_ensemble.local_gate.trading_disabled", reason_codes)
        self.assertNotIn("voting_ensemble.local_gate.global_upstream_not_provided", reason_codes)
        self.assertIn("voting_ensemble.local_gate.global_upstream_allows", reason_codes)


class BlockingService:
    def __init__(self, release: Event) -> None:
        self.release = release

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.release.wait(timeout=5)
        return decision(payload, service_version="blocking-test")


class PassthroughAutomaticPayloadBuilder:
    def build(self, command: Any) -> dict[str, Any]:
        return dict(command.payload)


class CountingService:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return decision(payload, service_version="counting-test")


def wait_for_api_job(client: TestClient, status_url: str) -> dict[str, Any]:
    for _ in range(100):
        response = client.get(status_url)
        if response.status_code != 200:
            raise AssertionError(response.text)
        job = response.json()
        if job["status"] in {"completed", "blocked", "expired", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"Voting Ensemble API job at {status_url} did not finish")


def decision(payload: dict[str, Any], *, service_version: str) -> dict[str, Any]:
    timestamp = payload["data_timestamp"]
    return {
        "algorithm_id": "voting_ensemble",
        "service_version": service_version,
        "symbol": "SPY",
        "evaluated_at": timestamp,
        "data_timestamp": timestamp,
        "final_signal": "Hold",
        "votes": [],
        "context_signals": [],
        "context_confirmation": {
            "outcome": "not_applicable",
            "detail": "test",
            "evidence": [],
            "confirmations": 0,
            "conflicts": 0,
        },
        "counts": {"Buy": 0, "Sell": 0, "Hold": 0},
        "eligible_counts": {"Buy": 0, "Sell": 0, "Hold": 0},
        "family_scores": {},
        "base_score": 0.0,
        "context_adjusted_score": 0.0,
        "context_agreements": 0,
        "context_conflicts": 0,
        "context_adjustment_reason": "test",
        "family_support": {},
        "safety_gate_failed": False,
        "removed_voters": ["Ensemble Strategy Voting"],
        "reason_codes": ["test.hold"],
    }


def evaluate_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "symbol": "SPY",
        "data_timestamp": rows[-1]["timestamp"],
        "candles": rows,
        "spy_5m_candles": candles(12, minutes=5),
        "spy_15m_candles": candles(6, minutes=15),
        "qqq_candles": candles(len(rows), symbol="QQQ"),
        "iwm_candles": candles(len(rows), symbol="IWM"),
        "breadth_components": {"XLK": candles(len(rows), symbol="XLK")},
    }


def candles(count: int, *, minutes: int = 1, symbol: str = "SPY") -> list[dict[str, Any]]:
    rows = []
    price = 100.0
    for index in range(count):
        timestamp = START + timedelta(minutes=index * minutes)
        close = price + 0.08
        rows.append(
            {
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "open": round(price, 4),
                "high": round(close + 0.12, 4),
                "low": round(price - 0.05, 4),
                "close": round(close, 4),
                "volume": 1000 + index * 25,
            }
        )
        price = close
    return rows


def nbbo(timestamp: str) -> dict[str, Any]:
    return {
        "bid": 100.01,
        "ask": 100.03,
        "bidSize": 500,
        "askSize": 500,
        "quoteTimestamp": timestamp,
        "lastTradeTimestamp": timestamp,
        "marketDataReceiptTimestamp": timestamp,
    }


def operational_permission_contract(timestamp: str, *, enabled: bool) -> dict[str, Any]:
    return {
        "status": "ready",
        "tradingEnabled": enabled,
        "automaticPaperTradingEnabled": enabled,
        "paperTradingMode": True,
        "liveTradingEnabled": False,
        "marketOpen": True,
        "entryWindowOpen": True,
        "validSession": True,
        "feedDegraded": False,
        "clockDisagreement": False,
        "executionFailureCooldownActive": False,
        "globalGateDecision": {
            "status": "PASS" if enabled else "FAIL",
            "eligible": enabled,
            "dataReady": True,
            "gateResults": [],
            "reasonCodes": ["test.dashboard_global_gate_allowed" if enabled else "test.dashboard_global_gate_blocked"],
            "explanation": "test dashboard global gate",
            "checkedAt": timestamp,
            "sessionDate": timestamp[:10],
            "configurationHash": f"test-dashboard-global-gate-{enabled}",
        },
    }


if __name__ == "__main__":
    unittest.main()
