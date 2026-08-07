from __future__ import annotations

import unittest
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.algorithms.voting_ensemble import api as voting_ensemble_api
from backend.app.algorithms.voting_ensemble.finalized_bar_producer import (
    VotingEnsembleAutomaticEvaluationPayloadBuilder,
    VotingEnsembleFinalizedBarEventStore,
    VotingEnsembleFinalizedBarMarketEvent,
    VotingEnsembleFinalizedBarProducer,
    VotingEnsembleFinalizedBarProducerConfig,
    finalized_market_event_from_candle,
)
from backend.app.algorithms.voting_ensemble.paper_execution import (
    VotingEnsemblePaperExecutionQueue,
    VotingEnsemblePaperExecutionRepository,
    VotingEnsemblePaperExecutionRuntime,
)
from backend.app.algorithms.voting_ensemble.runtime.events import FinalizedOneMinuteBarEvent
from backend.app.algorithms.voting_ensemble.runtime.commands import finalized_bar_evaluation_command
from backend.app.algorithms.voting_ensemble.runtime.orchestrator import VotingEnsembleRuntimeOrchestrator
from backend.app.algorithms.voting_ensemble.runtime.status_store import VotingEnsembleStatusStore
from backend.app.algorithms.voting_ensemble.runtime_supervisor import (
    VOTING_ENSEMBLE_RUNTIME_SUPERVISOR_VERSION,
    VotingEnsembleRuntimeControlRepository,
    VotingEnsembleRuntimeControlStore,
    VotingEnsembleRuntimeSupervisor,
    VotingEnsembleRuntimeSupervisorConfig,
)
from backend.app.config import ApplicationConfig, Settings
from backend.app.domain.models import OrderPlan, Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


class VotingEnsembleRuntimeSupervisorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        supervisor = getattr(self, "supervisor", None)
        if supervisor is not None:
            await supervisor.shutdown()

    async def test_supervisor_starts_workers_and_exposes_readiness(self) -> None:
        self.supervisor = supervisor_with_runtime()

        await self.supervisor.start()
        status = self.supervisor.status()

        self.assertEqual(status["supervisorVersion"], VOTING_ENSEMBLE_RUNTIME_SUPERVISOR_VERSION)
        self.assertTrue(status["supervisorStarted"])
        self.assertEqual(status["readiness"]["status"], "ready")
        self.assertTrue(status["workerHealth"]["evaluationWorker"]["alive"])
        self.assertTrue(status["workerHealth"]["executionWorker"]["alive"])
        self.assertFalse(status["readiness"]["liveTradingEnabled"])

    async def test_phase11_status_reports_flat_operational_contract(self) -> None:
        self.supervisor = supervisor_with_runtime(
            settings=paper_settings(),
            market_clock_provider=lambda: {"isOpen": True, "status": "open", "timestamp": NOW.isoformat().replace("+00:00", "Z")},
        )
        await self.supervisor.start()
        self.supervisor.update_control(requested_paper_trading_enabled=True, updated_by="test")
        self.supervisor.metrics.lastFinalizedBarEvent = finalized_bar_event().model_dump(mode="json")
        self.supervisor.metrics.lastReconciliation = {
            "algorithmId": "voting_ensemble",
            "status": "RECONCILED",
            "evaluatedAt": NOW.isoformat().replace("+00:00", "Z"),
            "reasonCodes": ["test.reconciled"],
        }
        self.supervisor.paper_execution_runtime.repository.write_snapshot(
            "outbox.phase11",
            {
                "algorithmId": "voting_ensemble",
                "algorithm_id": "voting_ensemble",
                "orderIntentId": "intent-phase11",
                "clientOrderId": "ve-entry-phase11",
                "settingsHash": "settings-phase11",
                "symbol": "SPY",
                "side": "Buy",
                "status": "PENDING",
                "createdAt": NOW.isoformat().replace("+00:00", "Z"),
            },
        )
        self.supervisor.paper_execution_runtime.repository.write_snapshot(
            "local_order.ve-entry-phase11",
            {
                "algorithmId": "voting_ensemble",
                "algorithm_id": "voting_ensemble",
                "clientOrderId": "ve-entry-phase11",
                "orderIntentId": "intent-phase11",
                "executionMode": "LOCAL_PAPER",
                "settingsHash": "settings-phase11",
                "symbol": "SPY",
                "side": "Buy",
                "status": "ACCEPTED",
                "quantity": 2,
                "filledQuantity": 0,
                "observedAt": NOW.isoformat().replace("+00:00", "Z"),
            },
        )
        self.supervisor.paper_execution_runtime.repository.write_snapshot(
            "local_position.spy",
            {
                "algorithmId": "voting_ensemble",
                "algorithm_id": "voting_ensemble",
                "capitalPartitionId": "voting_ensemble.paper.default",
                "accountId": "voting_ensemble.paper.default.account",
                "executionMode": "LOCAL_PAPER",
                "schemaVersion": "voting_ensemble_local_position_v1",
                "symbol": "SPY",
                "side": "Buy",
                "quantity": 2,
                "averagePrice": 100.0,
                "markPrice": 100.0,
                "notional": 200.0,
                "unrealizedPnl": 0.0,
                "observedAt": NOW.isoformat().replace("+00:00", "Z"),
                "updatedAt": NOW.isoformat().replace("+00:00", "Z"),
            },
        )

        status = self.supervisor.status()

        expected_keys = {
            "paperReady",
            "paperReadyBlockingReasonCodes",
            "supervisorRunning",
            "evaluationWorkerHealthy",
            "executionWorkerHealthy",
            "reconciliationHealthy",
            "requestedPaperTradingEnabled",
            "effectivePaperTradingEnabled",
            "liveTradingEnabled",
            "brokerPaperAccountVerified",
            "localPaperAccountVerified",
            "localInventoryVerified",
            "marketOpen",
            "marketDataReady",
            "inventoryReconciled",
            "newEntriesAllowed",
            "activeEntryBlocks",
            "lastFinalizedBar",
            "lastEvaluation",
            "lastDecision",
            "lastExecutionIntent",
            "lastLocalOrder",
            "lastBrokerOrder",
            "openVotingEnsembleOrders",
            "openVotingEnsemblePositions",
            "lastReconciliation",
            "lastError",
            "settingsHash",
            "executionMode",
            "sourceAuthority",
            "automaticTradingReady",
            "localPaperObservability",
            "localPaperAccount",
            "localPositions",
            "localOrders",
            "openOrders",
            "recentFills",
            "closedTrades",
        }
        self.assertLessEqual(expected_keys, set(status))
        self.assertEqual(status["executionMode"], "LOCAL_PAPER")
        self.assertEqual(status["sourceAuthority"], "voting_ensemble_local_paper_account")
        self.assertFalse(status["automaticTradingReady"])
        observability = status["localPaperObservability"]
        self.assertEqual(observability["executionMode"], "LOCAL_PAPER")
        self.assertEqual(observability["sourceAuthority"], "voting_ensemble_local_paper_account")
        self.assertEqual(observability["localPaperAccount"]["accountId"], "voting_ensemble.paper.default.account")
        self.assertEqual(observability["initialCash"], 100000.0)
        self.assertEqual(observability["cash"], 100000.0)
        self.assertEqual(observability["equity"], 100200.0)
        self.assertEqual(observability["buyingPower"], 100000.0)
        self.assertEqual(observability["realizedPnl"], 0.0)
        self.assertEqual(observability["realizedPnlToday"], 0.0)
        self.assertEqual(observability["unrealizedPnl"], 0.0)
        self.assertEqual(observability["dailyNetPnl"], 0.0)
        self.assertEqual(observability["positions"], observability["localPositions"])
        self.assertEqual(observability["localPositions"][0]["symbol"], "SPY")
        self.assertEqual(observability["openOrders"][0]["clientOrderId"], "ve-entry-phase11")
        self.assertEqual(observability["recentFills"], [])
        self.assertEqual(observability["closedTrades"], [])
        self.assertEqual(observability["grossExposure"], 200.0)
        self.assertEqual(observability["netExposure"], 200.0)
        self.assertEqual(observability["openRisk"], 0.0)
        self.assertEqual(observability["drawdown"], 0.0)
        self.assertTrue(observability["inventoryHealthy"])
        self.assertTrue(observability["persistenceHealthy"])
        self.assertFalse(observability["automaticTradingReady"])
        self.assertEqual(status["localPaperAccount"], observability["localPaperAccount"])
        self.assertEqual(status["localPositions"], observability["localPositions"])
        self.assertEqual(status["localOrders"], observability["localOrders"])
        self.assertEqual(status["openOrders"], observability["openOrders"])
        self.assertEqual(status["recentFills"], observability["recentFills"])
        self.assertEqual(status["closedTrades"], observability["closedTrades"])
        self.assertTrue(status["supervisorRunning"])
        self.assertFalse(status["paperReady"])
        self.assertNotIn("voting_ensemble.paper_ready.alpaca_paper_client_not_configured", status["paperReadyBlockingReasonCodes"])
        self.assertNotIn("voting_ensemble.paper_ready.reconciliation_not_healthy", status["paperReadyBlockingReasonCodes"])
        self.assertNotIn("voting_ensemble.paper_ready.execution_state_not_durable", status["paperReadyBlockingReasonCodes"])
        self.assertIn("voting_ensemble.paper_ready.backend_finalized_bar_producer_not_configured", status["paperReadyBlockingReasonCodes"])
        self.assertTrue(status["evaluationWorkerHealthy"])
        self.assertTrue(status["executionWorkerHealthy"])
        self.assertTrue(status["reconciliationHealthy"])
        self.assertTrue(status["requestedPaperTradingEnabled"])
        self.assertTrue(status["effectivePaperTradingEnabled"])
        self.assertFalse(status["liveTradingEnabled"])
        self.assertIsNone(status["brokerPaperAccountVerified"])
        self.assertTrue(status["localPaperAccountVerified"])
        self.assertTrue(status["localInventoryVerified"])
        self.assertTrue(status["localPaperAccountLoaded"])
        self.assertTrue(status["inventoryHealthy"])
        self.assertTrue(status["persistenceHealthy"])
        self.assertTrue(status["marketDataHealthy"])
        self.assertTrue(status["marketDataFresh"])
        self.assertTrue(status["marketClockHealthy"])
        self.assertTrue(status["killSwitchOff"])
        self.assertTrue(status["automaticExecutionEnabled"])
        self.assertTrue(status["marketOpen"])
        self.assertTrue(status["marketDataReady"])
        self.assertTrue(status["inventoryReconciled"])
        self.assertTrue(status["newEntriesAllowed"])
        self.assertEqual(status["activeEntryBlocks"], [])
        self.assertEqual(status["lastFinalizedBar"]["symbol"], "SPY")
        self.assertEqual(status["lastExecutionIntent"]["orderIntentId"], "intent-phase11")
        self.assertEqual(status["lastLocalOrder"]["clientOrderId"], "ve-entry-phase11")
        self.assertIsNone(status["lastBrokerOrder"])
        self.assertEqual(status["openVotingEnsembleOrders"][0]["clientOrderId"], "ve-entry-phase11")
        self.assertEqual(status["openVotingEnsemblePositions"][0]["symbol"], "SPY")
        self.assertEqual(status["settingsHash"], "settings-phase11")

    async def test_runtime_status_api_returns_phase11_status_contract(self) -> None:
        self.supervisor = supervisor_with_runtime(
            settings=paper_settings(),
            market_clock_provider=lambda: {"isOpen": True, "status": "open", "timestamp": NOW.isoformat().replace("+00:00", "Z")},
        )
        await self.supervisor.start()
        self.supervisor.metrics.lastReconciliation = {
            "algorithmId": "voting_ensemble",
            "status": "RECONCILED",
            "evaluatedAt": NOW.isoformat().replace("+00:00", "Z"),
        }
        original_getter = voting_ensemble_api.get_voting_ensemble_runtime_supervisor
        voting_ensemble_api.get_voting_ensemble_runtime_supervisor = lambda: self.supervisor
        test_app = FastAPI()
        test_app.include_router(voting_ensemble_api.router)
        try:
            response = TestClient(test_app).get("/api/voting-ensemble/runtime/status")
        finally:
            voting_ensemble_api.get_voting_ensemble_runtime_supervisor = original_getter

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["algorithmId"], "voting_ensemble")
        self.assertIn("supervisorRunning", payload)
        self.assertIn("paperReady", payload)
        self.assertIn("paperReadyBlockingReasonCodes", payload)
        self.assertIn("evaluationWorkerHealthy", payload)
        self.assertIn("executionWorkerHealthy", payload)
        self.assertIn("reconciliationHealthy", payload)
        self.assertIn("activeEntryBlocks", payload)
        self.assertIn("lastFinalizedBar", payload)
        self.assertIn("lastDecision", payload)
        self.assertFalse(payload["liveTradingEnabled"])

    async def test_worker_failure_blocks_new_finalized_bar_entries(self) -> None:
        self.supervisor = supervisor_with_runtime()
        await self.supervisor.start()

        self.supervisor.record_worker_failure("evaluation_worker", "forced failure")
        result = self.supervisor.enqueue_finalized_bar_event(finalized_bar_event())

        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "blocked")
        self.assertIn("voting_ensemble.runtime.evaluation_worker.failed", result["reasonCodes"])
        self.assertEqual(self.supervisor.status()["readiness"]["status"], "blocked")

    async def test_control_defaults_off_and_effective_state_is_backend_calculated(self) -> None:
        control_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"control-{uuid4().hex}.json"
        self.supervisor = supervisor_with_runtime(
            control_store=VotingEnsembleRuntimeControlStore(VotingEnsembleRuntimeControlRepository(control_path)),
            settings=paper_settings(),
            market_clock_provider=lambda: {"isOpen": True, "status": "open"},
        )
        await self.supervisor.start()

        default_control = self.supervisor.control_status()
        enabled = self.supervisor.update_control(requested_paper_trading_enabled=True, updated_by="test")

        self.assertFalse(default_control["requestedPaperTradingEnabled"])
        self.assertFalse(default_control["effectivePaperTradingEnabled"])
        self.assertTrue(enabled["requestedPaperTradingEnabled"])
        self.assertTrue(enabled["effectivePaperTradingEnabled"])
        self.assertTrue(enabled["newEntriesEnabled"])
        self.assertFalse(enabled["liveTradingEnabled"])
        control_path.unlink(missing_ok=True)

    async def test_control_update_can_clear_operator_local_entry_block(self) -> None:
        control_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"control-{uuid4().hex}.json"
        self.supervisor = supervisor_with_runtime(
            control_store=VotingEnsembleRuntimeControlStore(VotingEnsembleRuntimeControlRepository(control_path)),
            settings=paper_settings(),
            market_clock_provider=lambda: {"isOpen": True, "status": "open"},
        )
        await self.supervisor.start()
        self.supervisor.control_store.block_new_entries("voting_ensemble.runtime.finalized_bar_producer.failed")

        enabled = self.supervisor.update_control(
            requested_paper_trading_enabled=True,
            clear_local_entry_block=True,
            updated_by="test",
        )

        self.assertTrue(enabled["requestedPaperTradingEnabled"])
        self.assertTrue(enabled["effectivePaperTradingEnabled"])
        self.assertTrue(enabled["newEntriesEnabled"])
        self.assertFalse(enabled["localEntryBlockActive"])
        self.assertEqual(enabled["localEntryBlockReasonCodes"], [])
        self.assertIn("voting_ensemble.control.effective_paper_on", enabled["reasonCodes"])
        control_path.unlink(missing_ok=True)

    async def test_control_store_reloads_external_lightweight_control_write(self) -> None:
        control_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"control-{uuid4().hex}.json"
        self.supervisor = supervisor_with_runtime(
            control_store=VotingEnsembleRuntimeControlStore(VotingEnsembleRuntimeControlRepository(control_path)),
            settings=paper_settings(),
            market_clock_provider=lambda: {"isOpen": True, "status": "open"},
        )
        await self.supervisor.start()

        payload = self.supervisor.control_status(refresh_readiness=False)
        payload["requestedPaperTradingEnabled"] = True
        payload["effectivePaperTradingEnabled"] = False
        payload["newEntriesEnabled"] = False
        payload["updatedBy"] = "market_data_control_api"
        payload["reasonCodes"] = ["voting_ensemble.control.paper_requested_on"]
        control_path.write_text(json.dumps(payload), encoding="utf-8")

        permission = self.supervisor.entry_permission_snapshot()
        status = self.supervisor.status()

        self.assertTrue(permission["requestedPaperTradingEnabled"])
        self.assertTrue(permission["effectivePaperTradingEnabled"])
        self.assertTrue(permission["newEntriesAllowed"])
        self.assertIn("voting_ensemble.control.effective_paper_on", permission["reasonCodes"])
        self.assertTrue(status["requestedPaperTradingEnabled"])
        self.assertTrue(status["effectivePaperTradingEnabled"])
        self.assertTrue(status["newEntriesAllowed"])
        control_path.unlink(missing_ok=True)

    async def test_recovered_worker_failure_block_is_cleared_by_health_refresh(self) -> None:
        control_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"control-{uuid4().hex}.json"
        self.supervisor = supervisor_with_runtime(
            control_store=VotingEnsembleRuntimeControlStore(VotingEnsembleRuntimeControlRepository(control_path)),
            settings=paper_settings(),
            market_clock_provider=lambda: {"isOpen": True, "status": "open"},
            market_data_client=FakeMarketDataClient([]),
            candle_store=MemoryCandleStore(),
        )
        await self.supervisor.start()
        self.supervisor.update_control(requested_paper_trading_enabled=True, updated_by="test")
        self.supervisor.control_store.block_new_entries("voting_ensemble.runtime.finalized_bar_producer.failed")
        self.supervisor.metrics.workerStatus["finalized_bar_producer"] = "running"

        status = self.supervisor.control_status()

        self.assertTrue(status["requestedPaperTradingEnabled"])
        self.assertTrue(status["effectivePaperTradingEnabled"])
        self.assertTrue(status["newEntriesEnabled"])
        self.assertFalse(status["localEntryBlockActive"])
        self.assertEqual(status["localEntryBlockReasonCodes"], [])
        self.assertIn("voting_ensemble.control.effective_paper_on", status["reasonCodes"])
        control_path.unlink(missing_ok=True)

    async def test_recovered_worker_failure_clears_stale_last_error_block(self) -> None:
        control_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"control-{uuid4().hex}.json"
        self.supervisor = supervisor_with_runtime(
            control_store=VotingEnsembleRuntimeControlStore(VotingEnsembleRuntimeControlRepository(control_path)),
            settings=paper_settings(),
            market_clock_provider=lambda: {"isOpen": True, "status": "open"},
        )
        await self.supervisor.start()
        self.supervisor.update_control(requested_paper_trading_enabled=True, updated_by="test")
        self.supervisor.control_store.block_new_entries("voting_ensemble.runtime.execution_worker.failed")
        self.supervisor.metrics.workerStatus["execution_worker"] = "running"
        self.supervisor.metrics.lastError = "transient execution worker failure"
        self.supervisor.metrics.lastErrorAt = datetime.now(UTC).isoformat()

        status = self.supervisor.status()

        self.assertIsNone(status["lastError"])
        self.assertTrue(status["effectivePaperTradingEnabled"])
        self.assertTrue(status["newEntriesAllowed"])
        self.assertNotIn("voting_ensemble.runtime.worker_failure_recorded", status["paperReadyBlockingReasonCodes"])
        control_path.unlink(missing_ok=True)

    async def test_local_paper_readiness_refreshes_stale_market_data_mark_from_quote_provider(self) -> None:
        quote_time = datetime.now(UTC) + timedelta(seconds=1)
        self.supervisor = supervisor_with_runtime(
            settings=paper_settings(),
            market_clock_provider=lambda: {"isOpen": True, "status": "open"},
            market_data_client=FakeMarketDataClient(
                [],
                quote={
                    "provider": "alpaca",
                    "feed": "iex",
                    "symbol": "SPY",
                    "bid": 100.0,
                    "ask": 100.01,
                    "bidSize": 100,
                    "askSize": 100,
                    "quoteTimestamp": quote_time.isoformat().replace("+00:00", "Z"),
                    "marketDataReceiptTimestamp": quote_time.isoformat().replace("+00:00", "Z"),
                },
            ),
            candle_store=MemoryCandleStore(),
        )
        await self.supervisor.start()
        self.supervisor.paper_execution_runtime.repository.mark_local_positions_from_market_data(
            symbol="SPY",
            nbbo=None,
            observed_at=datetime.now(UTC),
        )

        enabled = self.supervisor.update_control(
            requested_paper_trading_enabled=True,
            clear_local_entry_block=True,
            updated_by="test",
        )
        status = self.supervisor.status()

        self.assertTrue(enabled["effectivePaperTradingEnabled"])
        self.assertTrue(enabled["newEntriesEnabled"])
        self.assertTrue(status["marketDataFresh"])

    async def test_live_alpaca_endpoint_does_not_block_local_paper_trading(self) -> None:
        self.supervisor = supervisor_with_runtime(
            settings=live_settings(),
            market_clock_provider=lambda: {"isOpen": True, "status": "open"},
        )
        await self.supervisor.start()

        enabled = self.supervisor.update_control(requested_paper_trading_enabled=True, updated_by="test")

        self.assertTrue(enabled["requestedPaperTradingEnabled"])
        self.assertTrue(enabled["effectivePaperTradingEnabled"])
        self.assertTrue(enabled["newEntriesEnabled"])
        self.assertNotIn("voting_ensemble.control.alpacaPaperUrlVerified", enabled["reasonCodes"])
        self.assertIn("voting_ensemble.control.effective_paper_on", enabled["reasonCodes"])

    async def test_local_paper_readiness_without_alpaca_credentials_blocks_on_local_clock_only(self) -> None:
        self.supervisor = supervisor_with_runtime(settings=no_alpaca_settings())
        await self.supervisor.start()

        enabled = self.supervisor.update_control(requested_paper_trading_enabled=True, updated_by="test")
        status = self.supervisor.status()

        self.assertFalse(enabled["effectivePaperTradingEnabled"])
        self.assertIn("voting_ensemble.control.marketClockHealthy", enabled["reasonCodes"])
        self.assertFalse(status["marketClockHealthy"])
        self.assertIn("voting_ensemble.paper_ready.market_clock_not_healthy", status["paperReadyBlockingReasonCodes"])
        self.assertNotIn("voting_ensemble.control.paper_credentials_missing", enabled["reasonCodes"])
        self.assertNotIn("voting_ensemble.paper_ready.alpaca_paper_client_not_configured", status["paperReadyBlockingReasonCodes"])

    async def test_local_paper_supervisor_uses_local_consistency_not_broker_reconciliation(self) -> None:
        self.supervisor = supervisor_with_runtime(settings=paper_settings(), market_clock_provider=lambda: {"isOpen": True})
        broker_calls: list[datetime] = []
        local_calls: list[datetime] = []

        def broker_reconcile(*, evaluated_at: datetime):
            broker_calls.append(evaluated_at)
            raise AssertionError("LOCAL_PAPER supervisor must not call broker reconciliation")

        def validate_local_consistency(*, evaluated_at: datetime):
            local_calls.append(evaluated_at)
            return {
                "algorithmId": "voting_ensemble",
                "capitalPartitionId": "voting_ensemble.paper.default",
                "accountId": "voting_ensemble.paper.default.account",
                "executionMode": "LOCAL_PAPER",
                "status": "VALIDATED",
                "brokerAccountsObserved": 0,
                "brokerPositionsObserved": 0,
                "brokerOrdersObserved": 0,
                "brokerFillsObserved": 0,
                "reasonCodes": ["voting_ensemble.local_paper_consistency.validated"],
            }

        self.supervisor.paper_execution_runtime.reconcile_broker_state = broker_reconcile  # type: ignore[method-assign]
        self.supervisor.paper_execution_runtime.validate_local_consistency = validate_local_consistency  # type: ignore[method-assign]

        await self.supervisor.start()

        self.assertEqual(broker_calls, [])
        self.assertGreaterEqual(len(local_calls), 1)
        self.assertEqual(self.supervisor.metrics.lastReconciliation["status"], "VALIDATED")
        self.assertEqual(self.supervisor.metrics.lastReconciliation["brokerPositionsObserved"], 0)

    async def test_paper_off_blocks_new_entries_but_not_exit_and_reconciliation_work(self) -> None:
        self.supervisor = supervisor_with_runtime(settings=paper_settings(), market_clock_provider=lambda: {"isOpen": True})
        await self.supervisor.start()
        self.supervisor.update_control(requested_paper_trading_enabled=True, updated_by="test")

        disabled = self.supervisor.update_control(requested_paper_trading_enabled=False, updated_by="test")
        permission = self.supervisor.entry_permission_snapshot()
        result = self.supervisor.enqueue_finalized_bar_event(finalized_bar_event())

        self.assertFalse(disabled["effectivePaperTradingEnabled"])
        self.assertFalse(disabled["newEntriesEnabled"])
        self.assertFalse(result["accepted"])
        self.assertTrue(permission["protectiveExitsEnabled"])
        self.assertTrue(permission["stopLossOrdersEnabled"])
        self.assertTrue(permission["profitTargetOrdersEnabled"])
        self.assertTrue(permission["positionReducingExitsEnabled"])
        self.assertTrue(permission["endOfDayLiquidationEnabled"])
        self.assertTrue(permission["fillProcessingEnabled"])
        self.assertTrue(permission["cancelReplaceProcessingEnabled"])
        self.assertFalse(permission["brokerReconciliationEnabled"])
        self.assertTrue(permission["localInventoryRecoveryEnabled"])
        self.assertEqual(permission["localInventoryAuthority"], "voting_ensemble.local_paper_account")

    async def test_broker_submission_rechecks_control_after_intent_exists(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        permission = {"newEntriesAllowed": True, "effectivePaperTradingEnabled": True, "reasonCodes": ["test.allowed"]}
        execution = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            entry_permission_provider=lambda: permission,
            auto_start=False,
        )

        enqueued = execution.enqueue_from_decision(
            buy_decision(),
            correlation_id="corr-control",
            idempotency_key="idem-control",
            source_job_id="job-control",
            source_command_id="cmd-control",
            evaluated_at=NOW,
        )
        permission.update({"newEntriesAllowed": False, "effectivePaperTradingEnabled": False, "reasonCodes": ["voting_ensemble.control.paper_requested_off"]})
        result = execution.process_once(evaluated_at=NOW + timedelta(seconds=1))

        self.assertTrue(enqueued["enqueued"])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["submitted"])
        self.assertFalse(any(key.startswith("voting_ensemble.paper_execution.local_order.") for key in repository.snapshots))
        self.assertIn("voting_ensemble.paper_execution.control_blocked_before_broker_submission", result["reasonCodes"])

    async def test_shutdown_stops_workers_without_losing_persisted_commands(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"supervisor-{uuid4().hex}.json"
        runtime = VotingEnsembleRuntimeOrchestrator(
            service=HoldService(),
            status_store=VotingEnsembleStatusStore(persistence_path=store_path),
            paper_execution_runtime=paper_runtime(),
            auto_start=False,
        )
        self.supervisor = VotingEnsembleRuntimeSupervisor(runtime=runtime, paper_execution_runtime=runtime.paper_execution_runtime)
        await self.supervisor.start()

        job = self.supervisor.enqueue_manual_evaluation(evaluate_payload())
        await self.supervisor.shutdown()

        reloaded = VotingEnsembleStatusStore(persistence_path=store_path)
        persisted = reloaded.get_job(job["jobId"])
        self.assertEqual(persisted["algorithmId"], "voting_ensemble")
        self.assertIn(persisted["status"], {"queued", "running", "completed"})
        status = self.supervisor.status()
        self.assertFalse(status["supervisorStarted"])
        self.assertFalse(status["workerHealth"]["evaluationWorker"].get("alive", False))
        store_path.unlink(missing_ok=True)

    async def test_authoritative_producer_enqueues_once_and_records_duplicate_and_stale_events(self) -> None:
        candle_store = MemoryCandleStore()
        published: list[tuple[VotingEnsembleFinalizedBarMarketEvent, str, int]] = []
        event_store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"events-{uuid4().hex}.json"
        producer = VotingEnsembleFinalizedBarProducer(
            market_data_client=FakeMarketDataClient([stored_candle(NOW - timedelta(minutes=1))]),
            candle_store=candle_store,
            event_store=VotingEnsembleFinalizedBarEventStore(event_store_path),
            config=VotingEnsembleFinalizedBarProducerConfig(decision_deadline_seconds=20, finalization_delay_seconds=2),
            publish_event=lambda event, settings_hash, deadline: published_event_job(published, event, settings_hash, deadline),
            settings_hash_provider=lambda: "settings-a",
        )

        first = await producer.process_symbol("SPY", now=NOW + timedelta(seconds=3))
        duplicate = await producer.process_symbol("SPY", now=NOW + timedelta(seconds=4))
        stale = await producer.process_symbol("SPY", now=NOW + timedelta(seconds=45))

        self.assertTrue(first.accepted)
        self.assertEqual(first.status, "enqueued")
        self.assertEqual(len(published), 1)
        self.assertFalse(duplicate.accepted)
        self.assertTrue(duplicate.duplicate)
        self.assertFalse(stale.accepted)
        self.assertTrue(stale.stale)
        receipts = producer.event_store.receipts()
        self.assertEqual([receipt["status"] for receipt in receipts], ["enqueued", "duplicate", "stale"])
        event_store_path.unlink(missing_ok=True)

    async def test_automatic_payload_builder_constructs_backend_authoritative_evaluation_payload(self) -> None:
        candle_store = MemoryCandleStore()
        bar_start = NOW - timedelta(minutes=1)
        symbols = ["SPY", "QQQ", "IWM", "XLK", "XLF", "XLY", "XLP", "XLV", "XLI", "XLE", "XLB", "XLU", "XLRE", "XLC"]
        for symbol in symbols:
            candle_store.upsert_many(
                [
                    stored_candle(NOW - timedelta(minutes=15 - index), symbol=symbol, close=100.0 + index * 0.01)
                    for index in range(15)
                ]
            )
        event = finalized_market_event_from_candle(
            stored_candle(bar_start),
            sequence=1,
            received_at=NOW + timedelta(seconds=3),
            finalized_at=NOW + timedelta(seconds=2),
            source_authority="backend.test.finalized_bar",
        )
        command = finalized_bar_evaluation_command(
            {"marketEvent": event.snapshot()},
            symbol="SPY",
            bar_end_timestamp=event.barEndTimestamp,
            settings_hash="settings-a",
            deadline_seconds=20,
        )
        builder = VotingEnsembleAutomaticEvaluationPayloadBuilder(
            candle_store=candle_store,
            control_snapshot_provider=lambda: {
                "requestedPaperTradingEnabled": True,
                "effectivePaperTradingEnabled": True,
                "newEntriesEnabled": True,
                "liveTradingEnabled": False,
                "reasonCodes": ["test.backend_control_allowed"],
            },
            paper_inventory_provider=lambda: {
                "orders": [],
                "fills": [],
                "positions": [{"symbol": "SPY", "notional": 12500.0}],
                "account": {
                    "accountId": "voting_ensemble.paper.default.account",
                    "capitalPartitionId": "voting_ensemble.paper.default",
                    "equity": 100000.0,
                    "buyingPower": 87500.0,
                    "realizedPnlToday": 120.0,
                    "unrealizedPnlToday": 50.0,
                    "dailyNetPnlAfterExitCosts": 170.0,
                    "intradayEquityHigh": 101000.0,
                    "drawdownPercent": 0.990099,
                    "openPositionNotional": 12500.0,
                    "totalOpenRiskPercent": 0.75,
                    "tradesToday": 4,
                    "sessionDate": NOW.date().isoformat(),
                    "observedAt": (NOW + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "sourceAuthority": "voting_ensemble_local_paper_account",
                },
            },
            market_status_provider=lambda: {"isOpen": True, "status": "open", "timestamp": NOW.isoformat().replace("+00:00", "Z")},
            account_snapshot_provider=lambda: {
                "accountId": "broker-account-must-not-be-authoritative",
                "equity": 999999.0,
                "buyingPower": 999999.0,
                "observedAt": (NOW + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                "sourceAuthority": "broker",
            },
            quote_provider=lambda **_: {
                "provider": "alpaca",
                "feed": "iex",
                "symbol": "SPY",
                "bid": 100.0,
                "ask": 100.01,
                "bidSize": 10,
                "askSize": 12,
                "quoteTimestamp": (NOW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                "marketDataReceiptTimestamp": (NOW + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
            },
            last_trade_provider=lambda **_: {
                "provider": "alpaca",
                "feed": "iex",
                "symbol": "SPY",
                "price": 100.005,
                "size": 100,
                "tradeTimestamp": (NOW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                "marketDataReceiptTimestamp": (NOW + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
            },
        )

        payload = builder.build(command)

        self.assertEqual(payload["symbol"], "SPY")
        self.assertEqual(payload["data_timestamp"], "2026-01-05T15:00:03Z")
        self.assertEqual(payload["candles"][-1]["timestamp"], "2026-01-05T14:59:00Z")
        self.assertTrue(payload["spy_5m_candles"])
        self.assertTrue(payload["spy_15m_candles"])
        self.assertTrue(payload["breadth_components"]["XLK"])
        self.assertEqual(payload["nbbo"]["lastTradePrice"], 100.005)
        operational = payload["market_context"]["operationalHealthSnapshot"]
        self.assertTrue(operational["tradingEnabled"])
        self.assertFalse(operational["liveTradingEnabled"])
        self.assertEqual(operational["authoritativeInventory"]["positions"], [{"symbol": "SPY", "notional": 12500.0}])
        self.assertEqual(payload["market_context"]["sourceAuthority"], "backend.test.finalized_bar")
        automatic_snapshot = payload["market_context"]["automaticRuntimeSnapshot"]
        self.assertTrue(automatic_snapshot["immutable"])
        account_risk = payload["market_context"]["accountRiskSnapshot"]
        self.assertEqual(account_risk["accountId"], "voting_ensemble.paper.default.account")
        self.assertEqual(account_risk["equity"], 100000.0)
        self.assertEqual(account_risk["buyingPower"], 87500.0)
        self.assertEqual(account_risk["realizedPnlToday"], 120.0)
        self.assertEqual(account_risk["unrealizedPnlToday"], 50.0)
        self.assertEqual(account_risk["dailyNetPnlAfterExitCosts"], 170.0)
        self.assertEqual(account_risk["intradayEquityHigh"], 101000.0)
        self.assertEqual(account_risk["drawdownPercent"], 0.990099)
        self.assertEqual(account_risk["openPositionNotional"], 12500.0)
        self.assertEqual(account_risk["totalOpenRiskPercent"], 0.75)
        self.assertEqual(account_risk["totalSpyNotionalPercent"], 12.5)
        self.assertEqual(account_risk["tradesToday"], 4)
        self.assertEqual(account_risk["sourceAuthority"], "voting_ensemble.local_paper_account")
        self.assertTrue(account_risk["localPaperAccount"])
        self.assertFalse(account_risk["externalBrokerAccount"])
        self.assertEqual(automatic_snapshot["backendAccountSnapshot"]["sourceAuthority"], "voting_ensemble.local_paper_account")
        self.assertEqual(automatic_snapshot["localPaperAccountSnapshot"], account_risk)
        self.assertTrue(automatic_snapshot["dataFreshnessAndSynchronization"]["snapshotSynchronized"])


class HoldService:
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        timestamp = payload.get("data_timestamp") or NOW.isoformat()
        return {
            "algorithm_id": "voting_ensemble",
            "service_version": "supervisor-test",
            "symbol": "SPY",
            "evaluated_at": timestamp,
            "data_timestamp": timestamp,
            "final_signal": "Hold",
            "votes": [],
            "context_signals": [],
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
            "removed_voters": [],
            "reason_codes": ["test.hold"],
        }


class FakePaperBroker:
    configured = True
    broker_kind = "alpaca_paper"
    paper_endpoint = True

    def __init__(self) -> None:
        self.submit_count = 0

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent: Any) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"broker-{intent.clientOrderId}",
            status="ACCEPTED",
            acceptedAt=NOW,
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        return None

    def cancel_order(self, client_order_id: str) -> bool:
        return True

    def refresh_positions(self) -> list[dict[str, Any]]:
        return []


class FakeMarketDataClient:
    def __init__(self, rows: list[dict[str, Any]], quote: dict[str, Any] | None = None) -> None:
        self.rows = rows
        self.quote = quote

    async def get_bars(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.rows)

    def get_latest_quote_sync(self, **kwargs: Any) -> dict[str, Any] | None:
        return dict(self.quote) if self.quote is not None else None


class MemoryCandleStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def upsert_many(self, candles: list[dict]) -> None:
        by_key = {
            (row["symbol"], row["timeframe"], row["feed"], row["timestamp"]): dict(row)
            for row in self.rows
        }
        for candle_row in candles:
            by_key[(candle_row["symbol"], candle_row["timeframe"], candle_row["feed"], candle_row["timestamp"])] = dict(candle_row)
        self.rows = sorted(by_key.values(), key=lambda row: str(row["timestamp"]))

    def latest_until(self, *, symbol: str, timeframe: str, feed: str, limit: int, end: str) -> list[dict]:
        rows = [
            dict(row)
            for row in self.rows
            if row["symbol"] == symbol and row["timeframe"] == timeframe and row["feed"] == feed and str(row["timestamp"]) <= end
        ]
        return rows[-limit:]


def published_event_job(
    published: list[tuple[VotingEnsembleFinalizedBarMarketEvent, str, int]],
    event: VotingEnsembleFinalizedBarMarketEvent,
    settings_hash: str,
    deadline: int,
) -> dict[str, Any]:
    published.append((event, settings_hash, deadline))
    return {
        "algorithmId": "voting_ensemble",
        "accepted": True,
        "deduplicated": False,
        "jobId": "ve-job-test",
        "commandId": "ve-command-test",
        "reasonCodes": ["voting_ensemble.runtime.command.enqueued"],
    }


def supervisor_with_runtime(
    *,
    control_store: VotingEnsembleRuntimeControlStore | None = None,
    settings: Settings | None = None,
    market_clock_provider=None,
    market_data_client=None,
    candle_store=None,
) -> VotingEnsembleRuntimeSupervisor:
    execution = paper_runtime()
    if control_store is None:
        control_store = VotingEnsembleRuntimeControlStore(
            VotingEnsembleRuntimeControlRepository(Path("backend/tests/.tmp_voting_ensemble_runtime") / f"control-{uuid4().hex}.json")
        )
    runtime = VotingEnsembleRuntimeOrchestrator(
        service=HoldService(),
        status_store=VotingEnsembleStatusStore(),
        paper_execution_runtime=execution,
        auto_start=False,
    )
    return VotingEnsembleRuntimeSupervisor(
        runtime=runtime,
        paper_execution_runtime=execution,
        control_store=control_store,
        settings=settings,
        market_clock_provider=market_clock_provider,
        market_data_client=market_data_client,
        candle_store=candle_store,
        config=VotingEnsembleRuntimeSupervisorConfig(health_poll_seconds=0.05, reconciliation_poll_seconds=0.05),
    )


def paper_runtime() -> VotingEnsemblePaperExecutionRuntime:
    repository = VotingEnsemblePaperExecutionRepository()
    queue = VotingEnsemblePaperExecutionQueue()
    return VotingEnsemblePaperExecutionRuntime(
        repository=repository,
        queue=queue,
        auto_start=False,
    )


def finalized_bar_event() -> FinalizedOneMinuteBarEvent:
    return FinalizedOneMinuteBarEvent(
        symbol="SPY",
        barEndTimestamp=NOW,
        finalized=True,
        settingsHash="settings-a",
        evaluationPayload=evaluate_payload(),
        correlationId="supervisor-test",
    )


def evaluate_payload() -> dict[str, Any]:
    return {
        "symbol": "SPY",
        "data_timestamp": NOW.isoformat().replace("+00:00", "Z"),
        "candles": [candle()],
        "spy_5m_candles": [candle(minutes=5)],
        "spy_15m_candles": [candle(minutes=15)],
        "qqq_candles": [candle()],
        "iwm_candles": [candle()],
        "breadth_components": {"XLK": [candle()]},
    }


def candle(*, minutes: int = 1) -> dict[str, Any]:
    return {
        "timestamp": (NOW + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
        "open": 100.0,
        "high": 100.2,
        "low": 99.9,
        "close": 100.1,
        "volume": 1000,
    }


def stored_candle(timestamp: datetime, *, symbol: str = "SPY", close: float = 100.1) -> dict[str, Any]:
    return {
        "provider": "alpaca",
        "feed": "iex",
        "symbol": symbol,
        "timeframe": "1Min",
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "open": 100.0,
        "high": max(100.2, close + 0.1),
        "low": 99.9,
        "close": close,
        "volume": 1000,
        "trade_count": 10,
        "vwap": 100.05,
    }


def paper_settings() -> Settings:
    return Settings(
        alpaca_key_id="paper-key",
        alpaca_secret_key="paper-secret",
        alpaca_data_base_url="https://data.alpaca.markets/v2",
        alpaca_trading_base_url="https://paper-api.alpaca.markets/v2",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3",
        database_url="sqlite:///:memory:",
        allowed_origins=[],
        application_config=ApplicationConfig(),
    )


def no_alpaca_settings() -> Settings:
    return Settings(
        alpaca_key_id="",
        alpaca_secret_key="",
        alpaca_data_base_url="https://data.alpaca.markets/v2",
        alpaca_trading_base_url="https://paper-api.alpaca.markets/v2",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3",
        database_url="sqlite:///:memory:",
        allowed_origins=[],
        application_config=ApplicationConfig(),
    )


def live_settings() -> Settings:
    return Settings(
        alpaca_key_id="paper-key",
        alpaca_secret_key="paper-secret",
        alpaca_data_base_url="https://data.alpaca.markets/v2",
        alpaca_trading_base_url="https://api.alpaca.markets/v2",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3",
        database_url="sqlite:///:memory:",
        allowed_origins=[],
        application_config=ApplicationConfig(),
    )


def buy_decision() -> dict[str, Any]:
    return {
        "algorithm_id": "voting_ensemble",
        "final_signal": "Buy",
        "safety_gate_failed": False,
        "order_plan": order_plan().model_dump(mode="json"),
        "reason_codes": ["test.buy"],
    }


def order_plan() -> OrderPlan:
    return OrderPlan(
        orderPlanId="ve-order-plan-control",
        candidateId="ve-candidate-control",
        symbol="SPY",
        side=Signal.BUY,
        orderType="LIMIT",
        quantity=3,
        entryPrice=100.0,
        stopPrice=99.0,
        targetPrice=101.5,
        limitPrice=100.0,
        maximumHoldingMinutes=30,
        timeInForce="DAY",
        eligible=True,
        explanation="test order",
        generatedAt=NOW,
        sessionDate=NOW.date(),
        configurationHash="order-config",
    )


if __name__ == "__main__":
    unittest.main()
