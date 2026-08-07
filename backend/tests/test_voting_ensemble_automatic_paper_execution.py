from __future__ import annotations

import json
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from backend.app.algorithms.voting_ensemble.paper_execution import (
    AlpacaPaperBrokerClient,
    VOTING_ENSEMBLE_ALGORITHM_ID,
    VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
    VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
    VotingEnsembleAlpacaPaperBrokerConfigurationError,
    VotingEnsembleDurableExecutionStateStore,
    VotingEnsembleLocalPaperExecutionEngine,
    VotingEnsembleLocalPaperBroker,
    VotingEnsemblePaperExecutionNamespaceError,
    VotingEnsemblePaperExecutionPersistenceError,
    VotingEnsemblePaperExecutionRepository,
    VotingEnsemblePaperExecutionRuntime,
    VotingEnsemblePaperExecutionWorker,
    VotingEnsemblePaperExecutionQueue,
)
from backend.app.algorithms.voting_ensemble.runtime.events import FinalizedOneMinuteBarEvent
from backend.app.algorithms.voting_ensemble.runtime.orchestrator import VotingEnsembleRuntimeOrchestrator
from backend.app.domain.models import OrderPlan, Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway
from backend.app.execution.broker_reconciliation import BrokerFillUpdate, BrokerOrderAck
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)


class VotingEnsembleAutomaticPaperExecutionTest(unittest.TestCase):
    def test_finalized_bar_evaluation_hands_eligible_order_to_execution_worker(self) -> None:
        execution_runtime = RecordingExecutionRuntime()
        runtime = VotingEnsembleRuntimeOrchestrator(
            service=BuyDecisionService(),
            paper_execution_runtime=execution_runtime,
            automatic_payload_builder=PassthroughAutomaticPayloadBuilder(),
            auto_start=False,
        )
        event = FinalizedOneMinuteBarEvent(
            symbol="SPY",
            barEndTimestamp=NOW,
            finalized=True,
            settingsHash="settings-a",
            evaluationPayload={"symbol": "SPY", "data_timestamp": NOW.isoformat(), "candles": [{"timestamp": NOW.isoformat(), "timeframe": "1Min"}]},
            correlationId="corr-1",
        )

        job = runtime.enqueue_finalized_bar_event(event)
        runtime.drain_in_process()
        completed = runtime.get_job(job["jobId"])

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(len(execution_runtime.enqueued), 1)
        self.assertEqual(execution_runtime.enqueued[0]["algorithm_id"], VOTING_ENSEMBLE_ALGORITHM_ID)
        self.assertTrue(completed["result"]["paperExecution"]["enqueued"])
        self.assertIn("voting_ensemble.runtime.finalized_bar_order_intent_handed_to_execution_worker", completed["result"]["reasonCodes"])

    def test_manual_evaluation_never_hands_order_to_execution_worker(self) -> None:
        execution_runtime = RecordingExecutionRuntime()
        runtime = VotingEnsembleRuntimeOrchestrator(
            service=BuyDecisionService(),
            paper_execution_runtime=execution_runtime,
            auto_start=False,
        )

        job = runtime.enqueue_manual_evaluation({"symbol": "SPY", "data_timestamp": NOW.isoformat(), "candles": [candle()]})
        runtime.drain_in_process()

        self.assertEqual(runtime.get_job(job["jobId"])["status"], "completed")
        self.assertEqual(execution_runtime.enqueued, [])

    def test_finalized_bar_without_backend_snapshot_builder_fails_closed(self) -> None:
        execution_runtime = RecordingExecutionRuntime()
        runtime = VotingEnsembleRuntimeOrchestrator(
            service=ExplodingService(),
            paper_execution_runtime=execution_runtime,
            auto_start=False,
        )
        event = FinalizedOneMinuteBarEvent(
            symbol="SPY",
            barEndTimestamp=NOW,
            finalized=True,
            settingsHash="settings-a",
            evaluationPayload={
                "symbol": "SPY",
                "data_timestamp": NOW.isoformat(),
                "candles": [candle()],
                "accountRiskSnapshot": {"equity": 999999},
                "tradingEnabled": True,
                "marketOpen": True,
            },
            correlationId="corr-builder-missing",
        )

        job = runtime.enqueue_finalized_bar_event(event)
        runtime.drain_in_process()
        completed = runtime.get_job(job["jobId"])

        self.assertEqual(completed["status"], "completed")
        decision = completed["result"]["decision"]
        self.assertEqual(decision["final_signal"], "Hold")
        self.assertEqual(decision["order_plan"]["quantity"], 0)
        self.assertFalse(completed["result"]["paperExecution"]["enqueued"])
        self.assertEqual(execution_runtime.enqueued, [])
        self.assertIn("voting_ensemble.runtime.automatic_snapshot_builder_missing", decision["reason_codes"])

    def test_execution_worker_submits_paper_order_and_repository_stamps_owned_records(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        queue = VotingEnsemblePaperExecutionQueue()
        broker = FakePaperBroker(fill_status="FILLED", filled_quantity=3)
        gateway = PaperOrderGateway(broker, repository)
        execution_runtime = VotingEnsemblePaperExecutionRuntime(repository=repository, queue=queue, paper_gateway=gateway, execution_mode="BROKER_PAPER", auto_start=False)

        enqueued = execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-2",
            idempotency_key="idem-2",
            source_job_id="job-2",
            source_command_id="cmd-2",
            evaluated_at=NOW,
        )
        result = execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))

        self.assertTrue(enqueued["enqueued"])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["submitted"])
        self.assertEqual(broker.submit_count, 1)
        self.assertTrue(all(record["algorithm_id"] == VOTING_ENSEMBLE_ALGORITHM_ID for record in repository.snapshots.values()))
        self.assertTrue(any(key.startswith("voting_ensemble.paper_gateway.paper_order_gateway.intent.") for key in repository.snapshots))
        self.assertTrue(any(key.startswith("voting_ensemble.paper_gateway.paper_order_gateway.fill.") for key in repository.snapshots))

    def test_decision_and_execution_intent_are_persisted_before_submission(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        broker = FakePaperBroker()
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(broker, repository),
            execution_mode="BROKER_PAPER",
            auto_start=False,
        )

        enqueued = execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-persist",
            idempotency_key="idem-persist",
            source_job_id="job-persist",
            source_command_id="cmd-persist",
            evaluated_at=NOW,
        )
        inventory = execution_runtime.inventory_snapshot()

        self.assertTrue(enqueued["enqueued"])
        self.assertEqual(broker.submit_count, 0)
        self.assertEqual(inventory["decisions"][0]["decisionId"], "ve-order-plan-1")
        self.assertEqual(inventory["outbox"][0]["status"], "PENDING")
        self.assertEqual(inventory["outbox"][0]["sourceCommandIdempotencyKey"], "idem-persist")
        self.assertTrue(inventory["outbox"][0]["executionIdempotencyKey"].startswith("voting_ensemble:execution:"))

    def test_durable_outbox_survives_restart_without_losing_pending_intent(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-execution-{uuid4().hex}.json"
        first_repository = VotingEnsemblePaperExecutionRepository(store_path)
        first_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=first_repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), first_repository),
            execution_mode="BROKER_PAPER",
            auto_start=False,
        )
        first_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-restart",
            idempotency_key="idem-restart",
            source_job_id="job-restart",
            source_command_id="cmd-restart",
            evaluated_at=NOW,
        )

        restarted_repository = VotingEnsemblePaperExecutionRepository(store_path)
        broker = FakePaperBroker()
        restarted_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=restarted_repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(broker, restarted_repository),
            execution_mode="BROKER_PAPER",
            auto_start=False,
        )
        result = restarted_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))
        inventory = restarted_runtime.inventory_snapshot()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["submitted"])
        self.assertEqual(broker.submit_count, 1)
        self.assertIn(inventory["outbox"][0]["status"], {"ACCEPTED", "FILLED"})
        store_path.unlink(missing_ok=True)

    def test_uncertain_submitting_outbox_requires_reconciliation_after_restart(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-uncertain-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            execution_mode="BROKER_PAPER",
            auto_start=False,
        )
        enqueued = runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-uncertain",
            idempotency_key="idem-uncertain",
            source_job_id="job-uncertain",
            source_command_id="cmd-uncertain",
            evaluated_at=NOW,
        )
        intent = repository.pending_intents()[0]
        repository.mark_outbox_status(intent, "SUBMITTING", reason_codes=("test.crash_after_submitting_state",))

        restarted_repository = VotingEnsemblePaperExecutionRepository(store_path)
        broker = FakePaperBroker()
        restarted_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=restarted_repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(broker, restarted_repository),
            execution_mode="BROKER_PAPER",
            auto_start=False,
        )
        result = restarted_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))

        self.assertTrue(enqueued["enqueued"])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["submitted"])
        self.assertEqual(result["status"], "RECONCILIATION_REQUIRED")
        self.assertEqual(broker.submit_count, 0)
        self.assertEqual(restarted_runtime.inventory_snapshot()["outbox"][0]["status"], "RECONCILIATION_REQUIRED")
        store_path.unlink(missing_ok=True)

    def test_non_finalized_commands_cannot_create_automatic_execution_intent(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            execution_mode="BROKER_PAPER",
            auto_start=False,
        )

        result = execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-manual",
            idempotency_key="idem-manual",
            source_job_id="job-manual",
            source_command_id="cmd-manual",
            evaluated_at=NOW,
            source_command_kind="manual_evaluation",
        )

        self.assertFalse(result["enqueued"])
        self.assertEqual(execution_runtime.inventory_snapshot()["outbox"], [])
        self.assertIn("voting_ensemble.paper_execution.non_finalized_command_cannot_create_automatic_intent", result["reasonCodes"])

    def test_alpaca_paper_broker_client_rejects_live_endpoint(self) -> None:
        with self.assertRaises(VotingEnsembleAlpacaPaperBrokerConfigurationError):
            AlpacaPaperBrokerClient(settings=FakeAlpacaSettings("https://api.alpaca.markets/v2"), http_client=FakeAlpacaHttpClient())

    def test_alpaca_paper_broker_client_uses_real_paper_order_and_broker_state_endpoints(self) -> None:
        http_client = FakeAlpacaHttpClient()
        client = AlpacaPaperBrokerClient(settings=FakeAlpacaSettings("https://paper-api.alpaca.markets/v2"), http_client=http_client)
        plan = order_plan()

        account = client.refresh_account_snapshot()
        clock = client.refresh_market_clock()
        tradable = client.verify_symbol_tradable("SPY")
        buying_power = client.verify_buying_power(plan)
        ack = client.submit_order(plan, "ve-client-paper")
        status = client.refresh_order_status("ve-client-paper")
        fill = client.refresh_order("ve-client-paper")
        open_orders = client.refresh_open_orders()
        positions = client.refresh_positions()
        fills = client.retrieve_fills(after=NOW - timedelta(minutes=1))
        protective = client.submit_protective_order(
            symbol="SPY",
            side=Signal.BUY,
            quantity=1,
            stop_price=99.0,
            target_price=101.0,
            client_order_id="ve-protective-paper",
        )
        replaced = client.replace_order("ve-client-paper", limit_price=100.05)
        canceled = client.cancel_order("ve-client-paper")

        self.assertEqual(account.accountId, "paper-account")
        self.assertTrue(clock["isOpen"])
        self.assertTrue(tradable)
        self.assertTrue(buying_power)
        self.assertEqual(ack.status, "ACCEPTED")
        self.assertEqual(status, "PARTIALLY_FILLED")
        self.assertEqual(fill.filledQuantity, 1)
        self.assertEqual(open_orders[0].clientOrderId, "ve-client-paper")
        self.assertEqual(positions[0].algorithmId, VOTING_ENSEMBLE_ALGORITHM_ID)
        self.assertEqual(fills[0].filledQuantity, 1)
        self.assertEqual(protective.status, "ACCEPTED")
        self.assertEqual(replaced["client_order_id"], "ve-client-paper")
        self.assertTrue(canceled)
        self.assertTrue(any(call["method"] == "POST" and call["url"].endswith("/orders") for call in http_client.calls))

    def test_paper_inventory_snapshot_reports_backend_orders_fills_and_positions(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        seed_local_quote(repository)
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            auto_start=False,
        )

        execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-inventory",
            idempotency_key="idem-inventory",
            source_job_id="job-inventory",
            source_command_id="cmd-inventory",
            evaluated_at=NOW,
        )
        execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))
        inventory = execution_runtime.inventory_snapshot()

        self.assertEqual(inventory["algorithm_id"], VOTING_ENSEMBLE_ALGORITHM_ID)
        self.assertEqual(len(inventory["orders"]), 3)
        self.assertEqual({order.get("protectiveKind") for order in inventory["orders"] if order.get("protectiveKind")}, {"STOP_LOSS", "PROFIT_TARGET"})
        self.assertEqual(len(inventory["orderIntents"]), 1)
        self.assertEqual(len(inventory["fills"]), 1)
        self.assertEqual(inventory["positions"][0]["symbol"], "SPY")
        self.assertEqual(inventory["positions"][0]["quantity"], 3)
        self.assertEqual(inventory["orders"][0]["schemaVersion"], "voting_ensemble_local_order_v1")
        self.assertTrue(inventory["orderIntents"][0]["snapshotKey"].startswith("voting_ensemble.paper_gateway.paper_order_gateway.intent."))

    def test_default_automatic_paper_runtime_uses_local_account_not_alpaca(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        seed_local_quote(repository)
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            auto_start=False,
        )

        execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-local-default",
            idempotency_key="idem-local-default",
            source_job_id="job-local-default",
            source_command_id="event-local-default",
            evaluated_at=NOW,
        )
        result = execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))
        inventory = execution_runtime.inventory_snapshot()

        self.assertIsInstance(execution_runtime.paper_gateway.broker, VotingEnsembleLocalPaperBroker)
        self.assertIsNone(execution_runtime.broker_client)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["submitted"])
        self.assertEqual(execution_runtime.summary()["executionMode"], "LOCAL_PAPER")
        self.assertEqual(result["gatewayResult"]["executionMode"], "LOCAL_PAPER")
        risk_account = repository.read_snapshot(f"paper_order_gateway.global_risk_account.{result['gatewayResult']['orderIntentId']}")
        self.assertEqual(risk_account["accountId"], VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID)
        self.assertEqual(risk_account["equity"], 100000.0)
        self.assertEqual(risk_account["availableBuyingPower"], 100000.0)
        self.assertNotEqual(risk_account["equity"], 3000.0)
        self.assertEqual(inventory["account"]["accountId"], VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID)
        self.assertEqual(inventory["account"]["capitalPartitionId"], VOTING_ENSEMBLE_CAPITAL_PARTITION_ID)
        self.assertEqual(inventory["account"]["sourceAuthority"], "voting_ensemble_local_paper_account")
        self.assertEqual(inventory["positions"][0]["quantity"], 3)
        self.assertTrue(inventory["riskSnapshots"])
        for collection_name in ("orders", "fills", "positions", "accounts", "riskSnapshots"):
            for record in inventory[collection_name]:
                self.assertEqual(record["algorithmId"], VOTING_ENSEMBLE_ALGORITHM_ID)
                self.assertEqual(record["capitalPartitionId"], VOTING_ENSEMBLE_CAPITAL_PARTITION_ID)

    def test_local_paper_new_entry_uses_ve_cash_not_foreign_unused_capital(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "50"}):
            repository = VotingEnsemblePaperExecutionRepository()
            repository.snapshots["global_risk.read_only_account.algorithm_b"] = {
                "algorithmId": "algorithm_b",
                "capitalPartitionId": "algorithm_b.paper.default",
                "accountId": "algorithm-b-paper",
                "equity": 1_000_000.0,
                "buyingPower": 1_000_000.0,
                "readOnly": True,
                "sourceAuthority": "global_risk.read_only_aggregate",
            }
            seed_local_quote(repository)
            execution_runtime = VotingEnsemblePaperExecutionRuntime(
                repository=repository,
                queue=VotingEnsemblePaperExecutionQueue(),
                auto_start=False,
            )

            execution_runtime.enqueue_from_decision(
                BuyDecisionService().evaluate({}),
                correlation_id="corr-local-cash-isolation",
                idempotency_key="idem-local-cash-isolation",
                source_job_id="job-local-cash-isolation",
                source_command_id="event-local-cash-isolation",
                evaluated_at=NOW,
            )
            result = execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))
            inventory = execution_runtime.inventory_snapshot()

            self.assertIsNotNone(result)
            assert result is not None
            self.assertFalse(result["submitted"])
            self.assertEqual(result["gatewayResult"]["status"], "NOT_SUBMITTED")
            self.assertIn("paper_gateway.global_portfolio_risk_denied", result["gatewayResult"]["reasonCodes"])
            risk_account = repository.read_snapshot(f"paper_order_gateway.global_risk_account.{result['gatewayResult']['orderIntentId']}")
            self.assertEqual(risk_account["accountId"], VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID)
            self.assertEqual(risk_account["equity"], 50.0)
            self.assertEqual(risk_account["availableBuyingPower"], 50.0)
            self.assertEqual(inventory["positions"], [])
            self.assertEqual(inventory["fills"], [])

    def test_local_paper_account_fields_initial_cash_and_restart_persistence(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-account-fields-{uuid4().hex}.json"
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "250000"}):
            repository = VotingEnsemblePaperExecutionRepository(store_path)
            account = repository.local_account_snapshot(observed_at=NOW)

            self.assertEqual(account["initialCash"], 250000.0)
            self.assertEqual(account["cash"], 250000.0)
            self.assertEqual(account["buyingPower"], 250000.0)
            self.assertEqual(account["usableEntryBuyingPower"], 250000.0)
            self.assertFalse(account["allowLeverage"])
            self.assertFalse(account["allowMargin"])
            self.assertFalse(account["allowShorts"])
            self.assertEqual(account["maxLeverage"], 1.0)
            self.assertEqual(account["buyingPowerModel"], "LOCAL_CASH_NO_MARGIN_LONG_ONLY")
            self.assertEqual(account["equityModel"], "cash_plus_local_owned_position_market_value")
            for field in (
                "equity",
                "realizedPnl",
                "realizedPnlToday",
                "unrealizedPnl",
                "dailyNetPnl",
                "intradayEquityHigh",
                "drawdownDollars",
                "drawdownPercent",
                "openPositionNotional",
                "grossExposure",
                "netExposure",
                "totalOpenRiskDollars",
                "totalOpenRiskPercent",
                "tradesToday",
                "sessionDate",
                "lastMarkPrice",
                "lastMarkedAt",
                "cashBuyingPower",
                "marginBuyingPower",
                "algorithmId",
                "capitalPartitionId",
                "version",
            ):
                self.assertIn(field, account)

            runtime = VotingEnsemblePaperExecutionRuntime(repository=repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
            seed_local_quote(repository)
            runtime.enqueue_from_decision(
                BuyDecisionService().evaluate({}),
                correlation_id="corr-account-fields",
                idempotency_key="idem-account-fields",
                source_job_id="job-account-fields",
                source_command_id="event-account-fields",
                evaluated_at=NOW,
            )
            runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))
            inventory_after_fill = runtime.inventory_snapshot()
            after_fill = inventory_after_fill["account"]
            self.assertLess(after_fill["cash"], 250000.0)
            self.assertEqual(after_fill["initialCash"], 250000.0)
            self.assertEqual(after_fill["tradesToday"], 1)
            self.assertEqual(after_fill["sessionDate"], SESSION_DATE.isoformat())
            self.assertEqual(after_fill["lastMarkPrice"], inventory_after_fill["positions"][0]["markPrice"])
            self.assertEqual(after_fill["grossExposure"], after_fill["openPositionNotional"])

        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "999999"}):
            restarted = VotingEnsemblePaperExecutionRepository(store_path)
            restarted_account = restarted.local_account_snapshot(observed_at=NOW + timedelta(minutes=1))
            self.assertEqual(restarted_account["cash"], after_fill["cash"])
            self.assertEqual(restarted_account["initialCash"], 250000.0)

    def test_local_paper_fill_accounting_uses_cost_basis_not_sale_proceeds(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository()
            ledger = repository.inventory_ledger
            repository.local_account_snapshot(observed_at=NOW)

            ledger.apply_fill(client_order_id="buy-100", order_intent_id="intent-buy-100", symbol="SPY", side=Signal.BUY, requested_quantity=100, fill_price=500.0, filled_at=NOW)
            first = repository.inventory_snapshot()
            self.assertEqual(first["positions"][0]["signedQuantity"], 100)
            self.assertEqual(first["positions"][0]["averageEntryPrice"], 500.0)
            self.assertEqual(first["account"]["cash"], 50000.0)

            ledger.apply_fill(client_order_id="buy-50", order_intent_id="intent-buy-50", symbol="SPY", side=Signal.BUY, requested_quantity=50, fill_price=510.0, filled_at=NOW + timedelta(minutes=1))
            second = repository.inventory_snapshot()
            self.assertEqual(second["positions"][0]["signedQuantity"], 150)
            self.assertAlmostEqual(second["positions"][0]["averageEntryPrice"], 503.333333, places=6)
            self.assertEqual(second["account"]["cash"], 24500.0)

            ledger.apply_fill(client_order_id="sell-40", order_intent_id="intent-sell-40", symbol="SPY", side=Signal.SELL, requested_quantity=40, fill_price=520.0, filled_at=NOW + timedelta(minutes=2))
            reduced = repository.inventory_snapshot()
            self.assertEqual(reduced["positions"][0]["signedQuantity"], 110)
            self.assertAlmostEqual(reduced["positions"][0]["averageEntryPrice"], 503.333333, places=6)
            self.assertEqual(reduced["account"]["cash"], 45300.0)
            self.assertAlmostEqual(reduced["account"]["realizedPnl"], 666.67, places=2)
            self.assertAlmostEqual(reduced["account"]["dailyNetPnl"], 2500.0, places=2)

            ledger.apply_fill(client_order_id="sell-110", order_intent_id="intent-sell-110", symbol="SPY", side=Signal.SELL, requested_quantity=110, fill_price=515.0, filled_at=NOW + timedelta(minutes=3))
            closed = repository.inventory_snapshot()
            self.assertEqual(closed["positions"], [])
            self.assertAlmostEqual(closed["account"]["cash"], 101950.0, places=2)
            self.assertAlmostEqual(closed["account"]["realizedPnl"], 1950.0, places=2)
            self.assertTrue(closed["closedTrades"])
            full_close = [trade for trade in closed["closedTrades"] if trade["exitOrderId"] == "sell-110"][0]
            self.assertEqual(full_close["quantity"], 110)
            self.assertIn("buy-100", full_close["associatedFillIds"])
            self.assertIn("buy-50", full_close["associatedFillIds"])
            self.assertIn("sell-110", full_close["associatedFillIds"])

    def test_local_paper_fill_accounting_applies_configured_fees_to_cash(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0.01",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "1.00",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository()
            repository.local_account_snapshot(observed_at=NOW)
            repository.inventory_ledger.apply_fill(
                client_order_id="fee-buy",
                order_intent_id="intent-fee-buy",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=10,
                fill_price=100.0,
                filled_at=NOW,
            )
            inventory = repository.inventory_snapshot()
            self.assertEqual(inventory["fills"][0]["grossNotional"], 1000.0)
            self.assertEqual(inventory["fills"][0]["feeAmount"], 1.1)
            self.assertEqual(inventory["account"]["cash"], 98998.9)

    def test_local_paper_duplicate_fill_is_applied_once(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository()
            repository.local_account_snapshot(observed_at=NOW)
            fill_kwargs = {
                "client_order_id": "dup-buy",
                "order_intent_id": "intent-dup-buy",
                "symbol": "SPY",
                "side": Signal.BUY,
                "requested_quantity": 10,
                "fill_price": 100.0,
                "filled_at": NOW,
            }

            first_fill = repository.inventory_ledger.apply_fill(**fill_kwargs)
            after_first = repository.inventory_snapshot()
            duplicate_fill = repository.inventory_ledger.apply_fill(**fill_kwargs)
            after_duplicate = repository.inventory_snapshot()

            self.assertIsNotNone(first_fill)
            self.assertIsNotNone(duplicate_fill)
            self.assertEqual(after_duplicate["account"]["cash"], after_first["account"]["cash"])
            self.assertEqual(after_duplicate["account"]["tradesToday"], 1)
            self.assertEqual(after_duplicate["positions"][0]["signedQuantity"], 10)
            self.assertEqual(len(after_duplicate["fills"]), 1)
            self.assertEqual(len(after_duplicate["account"]["appliedFillIds"]), 1)

    def test_local_paper_duplicate_fill_after_restart_is_applied_once(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-fill-idempotency-{uuid4().hex}.json"
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository(store_path)
            repository.local_account_snapshot(observed_at=NOW)
            fill_kwargs = {
                "client_order_id": "restart-dup-buy",
                "order_intent_id": "intent-restart-dup-buy",
                "symbol": "SPY",
                "side": Signal.BUY,
                "requested_quantity": 10,
                "fill_price": 100.0,
                "filled_at": NOW,
            }

            repository.inventory_ledger.apply_fill(**fill_kwargs)
            after_first = repository.inventory_snapshot()
            restarted = VotingEnsemblePaperExecutionRepository(store_path)
            restarted.inventory_ledger.apply_fill(**fill_kwargs)
            after_replay = restarted.inventory_snapshot()

            self.assertEqual(after_replay["account"]["cash"], after_first["account"]["cash"])
            self.assertEqual(after_replay["account"]["tradesToday"], 1)
            self.assertEqual(after_replay["positions"][0]["signedQuantity"], 10)
            self.assertEqual(len(after_replay["fills"]), 1)
            self.assertEqual(after_replay["account"]["appliedFillIds"], after_first["account"]["appliedFillIds"])

    def test_local_paper_durable_persistence_manifest_tracks_owned_schema_records(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-persistence-manifest-{uuid4().hex}.json"
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository(store_path)
            repository.local_account_snapshot(observed_at=NOW)
            repository.inventory_ledger.apply_fill(
                client_order_id="persist-buy",
                order_intent_id="intent-persist-buy",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=5,
                fill_price=100.0,
                filled_at=NOW,
            )
            repository.inventory_ledger.apply_fill(
                client_order_id="persist-sell",
                order_intent_id="intent-persist-sell",
                symbol="SPY",
                side=Signal.SELL,
                requested_quantity=5,
                fill_price=103.0,
                filled_at=NOW + timedelta(minutes=1),
            )

            reloaded = VotingEnsemblePaperExecutionRepository(store_path)
            inventory = reloaded.inventory_snapshot()
            manifest = inventory["localInventoryManifest"]
            snapshots = reloaded.snapshots
            expected_prefixes = (
                "voting_ensemble.paper_execution.local_account.latest",
                "voting_ensemble.paper_execution.local_position.SPY",
                "voting_ensemble.paper_gateway.paper_order_gateway.fill.persist-buy",
                "voting_ensemble.paper_gateway.paper_order_gateway.fill.persist-sell",
                "voting_ensemble.paper_execution.applied_fill.",
                "voting_ensemble.paper_execution.local_closed_trade.",
                "voting_ensemble.paper_execution.local_realized_pnl.",
                "voting_ensemble.paper_execution.local_risk_snapshot.latest",
                "voting_ensemble.paper_execution.local_inventory_manifest.latest",
            )

            self.assertIsNotNone(manifest)
            assert manifest is not None
            self.assertEqual(manifest["schemaVersion"], "voting_ensemble_local_inventory_manifest_v1")
            self.assertEqual(manifest["version"], "voting_ensemble_local_inventory_manifest_v1")
            self.assertEqual(manifest["conceptualStorageKeys"]["voting_ensemble.local_account.latest"], "voting_ensemble.paper_execution.local_account.latest")
            self.assertEqual(manifest["conceptualStorageKeys"]["voting_ensemble.inventory.positions"], "voting_ensemble.paper_execution.local_position.")
            self.assertEqual(manifest["conceptualStorageKeys"]["voting_ensemble.local_orders"], "voting_ensemble.paper_execution.local_order.")
            self.assertEqual(manifest["conceptualStorageKeys"]["voting_ensemble.local_fills"], "voting_ensemble.paper_gateway.paper_order_gateway.fill.")
            self.assertEqual(manifest["conceptualStorageKeys"]["voting_ensemble.closed_trades"], "voting_ensemble.paper_execution.local_closed_trade.")
            self.assertEqual(manifest["conceptualStorageKeys"]["voting_ensemble.applied_fill_ids"], "voting_ensemble.paper_execution.applied_fill.")
            self.assertEqual(manifest["tradeCounters"]["tradesToday"], 2)
            self.assertEqual(manifest["sessionDate"], SESSION_DATE.isoformat())
            self.assertEqual(len(manifest["appliedFillIds"]), 2)
            for prefix in expected_prefixes:
                self.assertTrue(any(key.startswith(prefix) for key in snapshots), prefix)
            for key, payload in snapshots.items():
                if not (
                    key.startswith("voting_ensemble.paper_execution.local_")
                    or key.startswith("voting_ensemble.paper_execution.applied_fill.")
                    or key.startswith("voting_ensemble.paper_gateway.paper_order_gateway.fill.")
                ):
                    continue
                self.assertEqual(payload["algorithmId"], VOTING_ENSEMBLE_ALGORITHM_ID, key)
                self.assertEqual(payload["capitalPartitionId"], VOTING_ENSEMBLE_CAPITAL_PARTITION_ID, key)
                self.assertIn("schemaVersion", payload, key)
        store_path.unlink(missing_ok=True)

    def test_local_paper_restart_recovers_persisted_inventory_without_broker_positions(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-local-restart-{uuid4().hex}.json"
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository(store_path)
            engine = VotingEnsembleLocalPaperExecutionEngine(repository)
            seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=3)
            engine.submit_order(local_engine_intent(client_order_id="restart-entry", quantity=3, side=Signal.BUY, limit_price=100.0))
            engine.refresh_order("restart-entry")
            seed_local_quote(repository, bid=101.0, ask=101.05, ask_size=100, observed_at=NOW + timedelta(seconds=1))
            engine.submit_order(local_engine_intent(client_order_id="restart-open-entry", quantity=1, side=Signal.BUY, limit_price=100.0))
            before = repository.inventory_snapshot()

            restarted_repository = VotingEnsemblePaperExecutionRepository(store_path)
            restarted_runtime = VotingEnsemblePaperExecutionRuntime(repository=restarted_repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
            recovery = restarted_runtime.reconcile_broker_state(evaluated_at=NOW + timedelta(seconds=1))
            after = restarted_runtime.inventory_snapshot()
            recovered_position = after["positions"][0]
            open_order = [order for order in after["orders"] if order["clientOrderId"] == "restart-open-entry"][0]

            self.assertIsNotNone(recovery)
            assert recovery is not None
            self.assertEqual(recovery["status"], "VALIDATED")
            self.assertTrue(recovery["brokerReconciliationSkipped"])
            self.assertEqual(recovery["brokerPositionsObserved"], 0)
            self.assertEqual(recovery["recovery"]["status"], "RECOVERED")
            self.assertEqual(recovery["recovery"]["markRecompute"]["symbolsMarked"], ["SPY"])
            self.assertEqual(recovered_position["signedQuantity"], before["positions"][0]["signedQuantity"])
            self.assertEqual(recovered_position["averageEntryPrice"], before["positions"][0]["averageEntryPrice"])
            self.assertEqual(after["account"]["cash"], before["account"]["cash"])
            self.assertEqual(after["account"]["realizedPnl"], before["account"]["realizedPnl"])
            self.assertEqual(open_order["status"], "OPEN")
            self.assertFalse(after["reconciliationBlocks"])
        store_path.unlink(missing_ok=True)

    def test_local_paper_restart_corruption_blocks_entries_but_preserves_safe_exit(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-local-restart-corrupt-{uuid4().hex}.json"
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository(store_path)
            repository.local_account_snapshot(observed_at=NOW)
            repository.inventory_ledger.apply_fill(
                client_order_id="corrupt-restart-entry",
                order_intent_id="intent-corrupt-restart-entry",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=3,
                fill_price=100.0,
                filled_at=NOW,
            )
            payload = json.loads(store_path.read_text(encoding="utf-8"))
            account_key = "voting_ensemble.paper_execution.local_account.latest"
            payload["snapshots"][account_key]["cash"] = 12345.0
            payload["snapshots"][account_key]["cashBalance"] = 12345.0
            store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            restarted_repository = VotingEnsemblePaperExecutionRepository(store_path)
            restarted_runtime = VotingEnsemblePaperExecutionRuntime(repository=restarted_repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
            recovery = restarted_runtime.reconcile_broker_state(evaluated_at=NOW + timedelta(seconds=1))
            blocked_entry = restarted_runtime.enqueue_from_decision(
                BuyDecisionService().evaluate({}),
                correlation_id="corr-corrupt-restart-buy",
                idempotency_key="idem-corrupt-restart-buy",
                source_job_id="job-corrupt-restart-buy",
                source_command_id="event-corrupt-restart-buy",
                evaluated_at=NOW + timedelta(seconds=2),
            )
            safe_exit = restarted_runtime.enqueue_from_decision(
                {"algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID, "final_signal": "Sell", "safety_gate_failed": False, "order_plan": order_plan(side=Signal.SELL).model_dump(mode="json")},
                correlation_id="corr-corrupt-restart-sell",
                idempotency_key="idem-corrupt-restart-sell",
                source_job_id="job-corrupt-restart-sell",
                source_command_id="event-corrupt-restart-sell",
                evaluated_at=NOW + timedelta(seconds=2),
            )
            inventory = restarted_runtime.inventory_snapshot()

            self.assertIsNotNone(recovery)
            assert recovery is not None
            self.assertEqual(recovery["status"], "LOCAL_CONSISTENCY_REQUIRED")
            self.assertTrue(recovery["brokerReconciliationSkipped"])
            self.assertEqual(recovery["recovery"]["status"], "RECOVERY_FAILED")
            self.assertIn("voting_ensemble.local_paper_recovery.cash_fill_invariant_failed", recovery["recovery"]["reasonCodes"])
            self.assertFalse(blocked_entry["enqueued"])
            self.assertIn("voting_ensemble.local_paper_recovery.cash_fill_invariant_failed", blocked_entry["reasonCodes"])
            self.assertTrue(safe_exit["enqueued"])
            self.assertEqual(inventory["positions"][0]["signedQuantity"], 3)
            self.assertTrue(inventory["reconciliationBlocks"])
        store_path.unlink(missing_ok=True)

    def test_local_paper_restart_missing_account_does_not_fabricate_balance(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-local-restart-missing-account-{uuid4().hex}.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(
            json.dumps(
                {
                    "snapshots": {
                        "voting_ensemble.paper_execution.local_position.SPY": {
                            "schemaVersion": "voting_ensemble_local_position_v1",
                            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
                            "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
                            "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
                            "executionMode": "LOCAL_PAPER",
                            "symbol": "SPY",
                            "quantity": 3,
                            "signedQuantity": 3,
                            "side": "LONG",
                            "averagePrice": 100.0,
                            "averageEntryPrice": 100.0,
                            "markPrice": 100.0,
                            "notional": 300.0,
                            "marketValue": 300.0,
                            "unrealizedPnl": 0.0,
                            "realizedPnl": 0.0,
                            "positionOwner": VOTING_ENSEMBLE_ALGORITHM_ID,
                            "exitOwner": VOTING_ENSEMBLE_ALGORITHM_ID,
                            "updatedAt": NOW.isoformat().replace("+00:00", "Z"),
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        repository = VotingEnsemblePaperExecutionRepository(store_path)
        runtime = VotingEnsemblePaperExecutionRuntime(repository=repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
        recovery = runtime.reconcile_broker_state(evaluated_at=NOW)
        inventory = runtime.inventory_snapshot()

        self.assertEqual(recovery["status"], "LOCAL_CONSISTENCY_REQUIRED")
        self.assertTrue(recovery["brokerReconciliationSkipped"])
        self.assertIn("voting_ensemble.local_paper_recovery.account_missing_with_existing_inventory", recovery["recovery"]["reasonCodes"])
        self.assertIsNone(inventory["account"])
        self.assertNotIn("voting_ensemble.paper_execution.local_account.latest", repository.snapshots)
        store_path.unlink(missing_ok=True)

    def test_local_paper_recovery_migrates_legacy_fill_derived_position_once_when_account_matches(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-local-fill-migration-{uuid4().hex}.json"
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository(store_path)
            repository.local_account_snapshot(observed_at=NOW)
            repository.inventory_ledger.apply_fill(
                client_order_id="legacy-migrate-buy",
                order_intent_id="intent-legacy-migrate-buy",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=3,
                fill_price=100.0,
                filled_at=NOW,
            )
            payload = json.loads(store_path.read_text(encoding="utf-8"))
            payload["snapshots"].pop("voting_ensemble.paper_execution.local_position.SPY")
            store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            restarted = VotingEnsemblePaperExecutionRepository(store_path)
            runtime = VotingEnsemblePaperExecutionRuntime(repository=restarted, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
            first = runtime.reconcile_broker_state(evaluated_at=NOW + timedelta(seconds=1))
            second = runtime.reconcile_broker_state(evaluated_at=NOW + timedelta(seconds=2))
            inventory = runtime.inventory_snapshot()

            self.assertEqual(first["status"], "VALIDATED")
            self.assertEqual(second["status"], "VALIDATED")
            self.assertTrue(first["brokerReconciliationSkipped"])
            self.assertTrue(second["brokerReconciliationSkipped"])
            self.assertEqual(first["recovery"]["migration"]["status"], "MIGRATED")
            self.assertEqual(first["recovery"]["migration"]["migratedSymbols"], ["SPY"])
            self.assertEqual(second["recovery"]["migration"]["status"], "MIGRATED")
            self.assertEqual(inventory["positions"][0]["signedQuantity"], 3)
            self.assertEqual(inventory["positions"][0]["averageEntryPrice"], 100.0)
            self.assertEqual(inventory["account"]["cash"], 99700.0)
            self.assertEqual(inventory["account"]["realizedPnl"], 0.0)
            self.assertEqual(inventory["localFillMigration"]["normalRuntimeAuthority"], "canonical_local_inventory_positions_not_fill_replay")
            self.assertEqual(inventory["localFillMigration"]["fillReplayAllowedFor"], ["migration", "audit", "recovery_verification"])
        store_path.unlink(missing_ok=True)

    def test_local_paper_recovery_failed_migration_blocks_entries_when_cash_not_reconstructable(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-local-fill-migration-failed-{uuid4().hex}.json"
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository(store_path)
            repository.local_account_snapshot(observed_at=NOW)
            repository.inventory_ledger.apply_fill(
                client_order_id="legacy-bad-cash-buy",
                order_intent_id="intent-legacy-bad-cash-buy",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=3,
                fill_price=100.0,
                filled_at=NOW,
            )
            payload = json.loads(store_path.read_text(encoding="utf-8"))
            payload["snapshots"].pop("voting_ensemble.paper_execution.local_position.SPY")
            account = payload["snapshots"]["voting_ensemble.paper_execution.local_account.latest"]
            account["cash"] = 100000.0
            account["cashBalance"] = 100000.0
            store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            restarted = VotingEnsemblePaperExecutionRepository(store_path)
            runtime = VotingEnsemblePaperExecutionRuntime(repository=restarted, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
            recovery = runtime.reconcile_broker_state(evaluated_at=NOW + timedelta(seconds=1))
            blocked = runtime.enqueue_from_decision(
                BuyDecisionService().evaluate({}),
                correlation_id="corr-bad-migration-buy",
                idempotency_key="idem-bad-migration-buy",
                source_job_id="job-bad-migration-buy",
                source_command_id="event-bad-migration-buy",
                evaluated_at=NOW + timedelta(seconds=2),
            )
            inventory = runtime.inventory_snapshot()

            self.assertEqual(recovery["status"], "LOCAL_CONSISTENCY_REQUIRED")
            self.assertTrue(recovery["brokerReconciliationSkipped"])
            self.assertEqual(recovery["recovery"]["status"], "RECOVERY_FAILED")
            self.assertIn("voting_ensemble.local_paper_migration.account_cash_or_pnl_not_reconstructable", recovery["recovery"]["reasonCodes"])
            self.assertEqual(inventory["localFillMigration"]["status"], "FAILED")
            self.assertEqual(inventory["positions"], [])
            self.assertFalse(blocked["enqueued"])
            self.assertIn("voting_ensemble.local_paper_migration.account_cash_or_pnl_not_reconstructable", blocked["reasonCodes"])
        store_path.unlink(missing_ok=True)

    def test_local_paper_mark_to_market_uses_fresh_bid_for_long_accounting(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_MAX_MARK_QUOTE_AGE_SECONDS": "5",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository()
            repository.local_account_snapshot(observed_at=NOW)
            repository.inventory_ledger.apply_fill(
                client_order_id="mark-buy",
                order_intent_id="intent-mark-buy",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=10,
                fill_price=100.0,
                filled_at=NOW,
            )

            mark_result = repository.mark_local_positions_from_market_data(
                symbol="SPY",
                nbbo={
                    "bid": 104.0,
                    "ask": 104.05,
                    "quoteTimestamp": (NOW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "marketDataReceiptTimestamp": (NOW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                },
                observed_at=NOW + timedelta(seconds=2),
            )
            inventory = repository.inventory_snapshot()
            position = inventory["positions"][0]

            self.assertTrue(mark_result["fresh"])
            self.assertEqual(position["markPrice"], 104.0)
            self.assertEqual(position["marketValue"], 1040.0)
            self.assertEqual(position["unrealizedPnl"], 40.0)
            self.assertEqual(position["markPricePolicy"], "conservative_liquidation_nbbo_bid_for_long_ask_for_short")
            self.assertEqual(inventory["account"]["equity"], 100040.0)
            self.assertEqual(inventory["account"]["buyingPower"], 99000.0)
            self.assertEqual(inventory["account"]["usableEntryBuyingPower"], 99000.0)
            self.assertEqual(inventory["account"]["marginBuyingPower"], 0.0)
            self.assertFalse(inventory["account"]["allowLeverage"])
            self.assertFalse(inventory["account"]["allowMargin"])
            self.assertFalse(inventory["account"]["allowShorts"])
            self.assertEqual(inventory["account"]["maxLeverage"], 1.0)
            self.assertEqual(inventory["account"]["openPositionNotional"], 1040.0)
            self.assertEqual(inventory["account"]["grossExposure"], 1040.0)
            self.assertEqual(inventory["account"]["netExposure"], 1040.0)
            self.assertEqual(inventory["account"]["drawdownDollars"], 0.0)
            self.assertEqual(inventory["account"]["totalOpenRiskDollars"], 0.0)

    def test_stale_local_mark_blocks_new_entries_but_allows_risk_reducing_exit(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_MAX_MARK_QUOTE_AGE_SECONDS": "5",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository()
            execution_runtime = VotingEnsemblePaperExecutionRuntime(repository=repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
            repository.local_account_snapshot(observed_at=NOW)
            repository.inventory_ledger.apply_fill(
                client_order_id="stale-mark-buy",
                order_intent_id="intent-stale-mark-buy",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=3,
                fill_price=100.0,
                filled_at=NOW,
            )
            stale_mark = repository.mark_local_positions_from_market_data(
                symbol="SPY",
                nbbo={
                    "bid": 101.0,
                    "ask": 101.05,
                    "quoteTimestamp": NOW.isoformat().replace("+00:00", "Z"),
                    "marketDataReceiptTimestamp": NOW.isoformat().replace("+00:00", "Z"),
                },
                observed_at=NOW + timedelta(seconds=10),
            )

            blocked = execution_runtime.enqueue_from_decision(
                BuyDecisionService().evaluate({}),
                correlation_id="corr-stale-mark-buy",
                idempotency_key="idem-stale-mark-buy",
                source_job_id="job-stale-mark-buy",
                source_command_id="event-stale-mark-buy",
                evaluated_at=NOW + timedelta(seconds=10),
            )
            exit_result = execution_runtime.enqueue_from_decision(
                {"algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID, "final_signal": "Sell", "safety_gate_failed": False, "order_plan": order_plan(side=Signal.SELL).model_dump(mode="json")},
                correlation_id="corr-stale-mark-sell",
                idempotency_key="idem-stale-mark-sell",
                source_job_id="job-stale-mark-sell",
                source_command_id="event-stale-mark-sell",
                evaluated_at=NOW + timedelta(seconds=10),
            )

            self.assertFalse(stale_mark["fresh"])
            self.assertFalse(blocked["enqueued"])
            self.assertIn("voting_ensemble.paper_execution.local_mark_stale_blocks_new_entries", blocked["reasonCodes"])
            self.assertTrue(exit_result["enqueued"])

    def test_finalized_bar_worker_marks_local_inventory_from_payload_nbbo(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        paper_runtime = VotingEnsemblePaperExecutionRuntime(repository=repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
        repository.local_account_snapshot(observed_at=NOW)
        repository.inventory_ledger.apply_fill(
            client_order_id="worker-mark-buy",
            order_intent_id="intent-worker-mark-buy",
            symbol="SPY",
            side=Signal.BUY,
            requested_quantity=2,
            fill_price=100.0,
            filled_at=NOW,
        )
        runtime = VotingEnsembleRuntimeOrchestrator(
            service=HoldDecisionService(),
            paper_execution_runtime=paper_runtime,
            automatic_payload_builder=PassthroughAutomaticPayloadBuilder(),
            auto_start=False,
        )
        event = FinalizedOneMinuteBarEvent(
            symbol="SPY",
            barEndTimestamp=NOW + timedelta(minutes=1),
            finalized=True,
            evaluationPayload={
                "symbol": "SPY",
                "data_timestamp": (NOW + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                "candles": [],
                "nbbo": {
                    "bid": 103.0,
                    "ask": 103.05,
                    "quoteTimestamp": (NOW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                    "marketDataReceiptTimestamp": (NOW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                },
            },
        )

        runtime.enqueue_finalized_bar_event(event)
        result = runtime.drain_in_process(max_commands=1)[0]
        inventory = paper_runtime.inventory_snapshot()

        self.assertTrue(result["result"]["localPaperMarkToMarket"]["fresh"])
        self.assertEqual(inventory["positions"][0]["markPrice"], 103.0)
        self.assertEqual(inventory["positions"][0]["unrealizedPnl"], 6.0)

    def test_local_paper_mode_rejects_broker_trading_client(self) -> None:
        with self.assertRaises(VotingEnsemblePaperExecutionNamespaceError):
            VotingEnsemblePaperExecutionRuntime(
                repository=VotingEnsemblePaperExecutionRepository(),
                queue=VotingEnsemblePaperExecutionQueue(),
                broker_client=FakePaperBrokerClient(),
                auto_start=False,
            )

        repository = VotingEnsemblePaperExecutionRepository()
        with self.assertRaises(VotingEnsemblePaperExecutionNamespaceError):
            VotingEnsemblePaperExecutionRuntime(
                repository=repository,
                queue=VotingEnsemblePaperExecutionQueue(),
                paper_gateway=PaperOrderGateway(FakePaperBroker(), repository, execution_mode="LOCAL_PAPER"),
                auto_start=False,
            )

    def test_local_paper_execution_engine_accepts_order_and_applies_fill_atomically(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository()
            engine = VotingEnsembleLocalPaperExecutionEngine(repository)
            seed_local_quote(repository)
            intent = local_engine_intent(client_order_id="engine-buy", quantity=5, side=Signal.BUY, limit_price=100.0)

            ack = engine.submit_order(intent)
            fill = engine.refresh_order("engine-buy")
            inventory = repository.inventory_snapshot()

            self.assertTrue(engine.verify_account())
            self.assertEqual(ack.status, "OPEN")
            self.assertIsNotNone(fill)
            self.assertEqual(fill.filledQuantity, 5)
            self.assertEqual(inventory["positions"][0]["signedQuantity"], 5)
            self.assertEqual(inventory["account"]["cash"], 99500.0)
            self.assertEqual(inventory["orders"][0]["status"], "FILLED")
            self.assertEqual(inventory["orders"][0]["algorithmId"], VOTING_ENSEMBLE_ALGORITHM_ID)
            self.assertEqual(inventory["orders"][0]["capitalPartitionId"], VOTING_ENSEMBLE_CAPITAL_PARTITION_ID)
            self.assertEqual(inventory["orders"][0]["accountId"], VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID)
            self.assertEqual(inventory["fills"][0]["clientOrderId"], "engine-buy")
            self.assertEqual(inventory["localExecutions"][-1]["status"], "FILLED")
            self.assertEqual(inventory["localExecutions"][-1]["executionMode"], "LOCAL_PAPER")

    def test_local_paper_execution_engine_rejects_foreign_inventory_and_insufficient_cash(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "1000"}):
            repository = VotingEnsemblePaperExecutionRepository()
            engine = VotingEnsembleLocalPaperExecutionEngine(repository)

            foreign = local_engine_intent(client_order_id="engine-foreign", algorithm_id="weighted_voting", quantity=1)
            foreign_partition = local_engine_intent(client_order_id="engine-foreign-partition", capital_partition_id="weighted_voting.paper.default", quantity=1)
            too_large = local_engine_intent(client_order_id="engine-too-large", quantity=20, limit_price=100.0)

            foreign_ack = engine.submit_order(foreign)
            partition_ack = engine.submit_order(foreign_partition)
            cash_ack = engine.submit_order(too_large)
            inventory = repository.inventory_snapshot()

            self.assertEqual(foreign_ack.status, "REJECTED")
            self.assertEqual(foreign_ack.rejectedReason, "voting_ensemble.local_paper.foreign_algorithm_rejected")
            self.assertEqual(partition_ack.status, "REJECTED")
            self.assertEqual(partition_ack.rejectedReason, "voting_ensemble.local_paper.foreign_capital_partition_rejected")
            self.assertEqual(cash_ack.status, "REJECTED")
            self.assertEqual(cash_ack.rejectedReason, "voting_ensemble.local_paper.insufficient_buying_power")
            self.assertEqual(inventory["positions"], [])
            self.assertEqual(inventory["fills"], [])
            self.assertEqual(len(inventory["localExecutions"]), 3)

    def test_local_paper_execution_engine_rejects_invalid_local_risk_and_naked_exit(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        engine = VotingEnsembleLocalPaperExecutionEngine(repository)

        risk_ack = engine.submit_order(local_engine_intent(client_order_id="engine-risk", quantity=1, planned_risk=200000.0))
        sell_ack = engine.submit_order(local_engine_intent(client_order_id="engine-sell", quantity=1, side=Signal.SELL))

        self.assertEqual(risk_ack.status, "REJECTED")
        self.assertEqual(risk_ack.rejectedReason, "voting_ensemble.local_paper.local_risk_limit_exceeded")
        self.assertEqual(sell_ack.status, "REJECTED")
        self.assertEqual(sell_ack.rejectedReason, "voting_ensemble.local_paper.sell_cannot_mutate_foreign_or_absent_position")

    def test_local_paper_execution_engine_enforces_configured_local_entry_risk_caps(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        engine = VotingEnsembleLocalPaperExecutionEngine(repository)

        risk_ack = engine.submit_order(
            local_engine_intent(
                client_order_id="engine-risk-per-trade",
                quantity=1,
                planned_risk=20.0,
                settings_snapshot={"riskPerTradePercent": 0.01},
            )
        )
        repository.write_snapshot(
            "local_account.latest",
            {
                **repository.local_account_snapshot(observed_at=NOW),
                "tradesToday": 3,
            },
        )
        trade_count_ack = engine.submit_order(
            local_engine_intent(
                client_order_id="engine-trade-count",
                quantity=1,
                planned_risk=1.0,
                settings_snapshot={"maximumTradesPerDay": 3},
            )
        )
        repository.write_snapshot(
            "local_account.latest",
            {
                **repository.local_account_snapshot(observed_at=NOW),
                "tradesToday": 0,
                "dailyNetPnl": -2000.0,
                "dailyNetPnlAfterExitCosts": -2000.0,
            },
        )
        daily_loss_ack = engine.submit_order(
            local_engine_intent(
                client_order_id="engine-daily-loss",
                quantity=1,
                planned_risk=1.0,
                settings_snapshot={"maximumDailyLossPercent": 2.0},
            )
        )

        self.assertEqual(risk_ack.status, "REJECTED")
        self.assertEqual(risk_ack.rejectedReason, "voting_ensemble.local_paper.risk_dollars_per_trade_exceeded")
        self.assertEqual(trade_count_ack.status, "REJECTED")
        self.assertEqual(trade_count_ack.rejectedReason, "voting_ensemble.local_paper.maximum_trades_per_day_exceeded")
        self.assertEqual(daily_loss_ack.status, "REJECTED")
        self.assertEqual(daily_loss_ack.rejectedReason, "voting_ensemble.local_paper.maximum_daily_loss_exceeded")

    def test_local_paper_execution_engine_limit_requires_executable_quote(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        engine = VotingEnsembleLocalPaperExecutionEngine(repository)
        seed_local_quote(repository, bid=100.0, ask=100.25, ask_size=100)
        engine.submit_order(local_engine_intent(client_order_id="engine-buy-not-executable", quantity=5, side=Signal.BUY, limit_price=100.0))

        fill = engine.refresh_order("engine-buy-not-executable")
        inventory = repository.inventory_snapshot()

        self.assertIsNone(fill)
        self.assertEqual(inventory["orders"][0]["status"], "OPEN")
        self.assertIn("voting_ensemble.local_paper_execution_engine.buy_limit_not_executable", inventory["orders"][0]["reasonCodes"])
        self.assertEqual(inventory["positions"], [])

    def test_local_paper_execution_engine_partial_fill_uses_quote_size_and_participation(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_MAX_PARTICIPATION_PCT": "50"}):
            repository = VotingEnsemblePaperExecutionRepository()
            engine = VotingEnsembleLocalPaperExecutionEngine(repository)
            seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=4)
            engine.submit_order(local_engine_intent(client_order_id="engine-partial", quantity=10, side=Signal.BUY, limit_price=100.0))

            fill = engine.refresh_order("engine-partial")
            inventory = repository.inventory_snapshot()

            self.assertIsNotNone(fill)
            assert fill is not None
            self.assertEqual(fill.filledQuantity, 2)
            self.assertEqual(inventory["orders"][0]["status"], "PARTIALLY_FILLED")
            self.assertEqual(inventory["orders"][0]["filledQuantity"], 2)
            self.assertEqual(inventory["positions"][0]["signedQuantity"], 2)

    def test_local_paper_execution_engine_stop_limit_triggers_then_uses_limit_rules(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        engine = VotingEnsembleLocalPaperExecutionEngine(repository)
        seed_local_quote(repository, bid=100.0, ask=100.5, ask_size=100)
        engine.submit_order(
            local_engine_intent(
                client_order_id="engine-stop-limit",
                quantity=3,
                side=Signal.BUY,
                order_type="STOP_LIMIT",
                trigger_price=101.0,
                limit_price=101.5,
            )
        )

        first = engine.refresh_order("engine-stop-limit")
        seed_local_quote(repository, bid=101.0, ask=101.25, ask_size=100, observed_at=NOW + timedelta(seconds=1))
        second = engine.refresh_order("engine-stop-limit")
        inventory = repository.inventory_snapshot()

        self.assertIsNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(inventory["orders"][0]["status"], "FILLED")
        self.assertTrue(inventory["orders"][0]["stopTriggered"])
        self.assertLessEqual(inventory["fills"][0]["averageFillPrice"], 101.5)

    def test_local_paper_execution_engine_slippage_never_worse_than_limit(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_SLIPPAGE_BPS": "100"}):
            repository = VotingEnsemblePaperExecutionRepository()
            engine = VotingEnsembleLocalPaperExecutionEngine(repository)
            seed_local_quote(repository, bid=99.45, ask=99.5, ask_size=100)
            engine.submit_order(local_engine_intent(client_order_id="engine-slippage-cap", quantity=1, side=Signal.BUY, limit_price=100.0))

            fill = engine.refresh_order("engine-slippage-cap")

            self.assertIsNotNone(fill)
            assert fill is not None
            self.assertEqual(fill.averageFillPrice, 100.0)

    def test_local_paper_entry_fill_creates_same_inventory_stop_and_target_oco(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_MAX_PARTICIPATION_PCT": "100"}):
            repository = VotingEnsemblePaperExecutionRepository()
            engine = VotingEnsembleLocalPaperExecutionEngine(repository)
            seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=2)
            engine.submit_order(local_engine_intent(client_order_id="engine-oco-entry", quantity=5, side=Signal.BUY, limit_price=100.0))

            fill = engine.refresh_order("engine-oco-entry")
            inventory = repository.inventory_snapshot()
            protective = [order for order in inventory["orders"] if order.get("ocoGroupId") == "engine-oco-entry-oco"]

            self.assertIsNotNone(fill)
            assert fill is not None
            self.assertEqual(fill.filledQuantity, 2)
            self.assertEqual(inventory["positions"][0]["signedQuantity"], 2)
            self.assertEqual({order["protectiveKind"] for order in protective}, {"STOP_LOSS", "PROFIT_TARGET"})
            self.assertTrue(all(order["quantity"] == 2 for order in protective))
            self.assertTrue(all(order["positionOwner"] == VOTING_ENSEMBLE_ALGORITHM_ID for order in protective))
            self.assertTrue(all(order["exitOwner"] == VOTING_ENSEMBLE_ALGORITHM_ID for order in protective))

    def test_local_paper_partial_target_exit_resizes_competing_stop_to_remaining_owned_quantity(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_MAX_PARTICIPATION_PCT": "100"}):
            repository = VotingEnsemblePaperExecutionRepository()
            engine = VotingEnsembleLocalPaperExecutionEngine(repository)
            seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=5)
            engine.submit_order(local_engine_intent(client_order_id="engine-oco-partial", quantity=5, side=Signal.BUY, limit_price=100.0))
            engine.refresh_order("engine-oco-partial")

            seed_local_quote(repository, bid=101.5, ask=101.55, bid_size=2, observed_at=NOW + timedelta(seconds=1))
            target_fill = engine.refresh_order("engine-oco-partial-target")
            inventory = repository.inventory_snapshot()
            stop = [order for order in inventory["orders"] if order.get("clientOrderId") == "engine-oco-partial-stop"][0]

            self.assertIsNotNone(target_fill)
            assert target_fill is not None
            self.assertEqual(target_fill.filledQuantity, 2)
            self.assertEqual(inventory["positions"][0]["signedQuantity"], 3)
            self.assertEqual(stop["status"], "OPEN")
            self.assertEqual(stop["quantity"], 3)

    def test_local_paper_completed_target_exit_cancels_competing_stop(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        engine = VotingEnsembleLocalPaperExecutionEngine(repository)
        seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=3)
        engine.submit_order(local_engine_intent(client_order_id="engine-oco-complete", quantity=3, side=Signal.BUY, limit_price=100.0))
        engine.refresh_order("engine-oco-complete")

        seed_local_quote(repository, bid=101.5, ask=101.55, bid_size=3, observed_at=NOW + timedelta(seconds=1))
        target_fill = engine.refresh_order("engine-oco-complete-target")
        inventory = repository.inventory_snapshot()
        stop = [order for order in inventory["orders"] if order.get("clientOrderId") == "engine-oco-complete-stop"][0]
        target = [order for order in inventory["orders"] if order.get("clientOrderId") == "engine-oco-complete-target"][0]

        self.assertIsNotNone(target_fill)
        self.assertEqual(inventory["positions"], [])
        self.assertEqual(target["status"], "FILLED")
        self.assertEqual(stop["status"], "CANCELED")
        self.assertIn("voting_ensemble.local_paper_execution_engine.oco_sibling_canceled_after_exit_fill", stop["reasonCodes"])

    def test_local_paper_stop_exit_uses_remaining_voting_ensemble_quantity_not_foreign_spy(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()
        engine = VotingEnsembleLocalPaperExecutionEngine(repository)
        repository.snapshots["foreign.position.spy"] = {"algorithmId": "weighted_voting", "symbol": "SPY", "quantity": 50}
        seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=3)
        engine.submit_order(local_engine_intent(client_order_id="engine-oco-owned-only", quantity=3, side=Signal.BUY, limit_price=100.0))
        engine.refresh_order("engine-oco-owned-only")

        seed_local_quote(repository, bid=99.0, ask=99.05, bid_size=100, observed_at=NOW + timedelta(seconds=1))
        stop_fill = engine.refresh_order("engine-oco-owned-only-stop")
        inventory = repository.inventory_snapshot()

        self.assertIsNotNone(stop_fill)
        assert stop_fill is not None
        self.assertEqual(stop_fill.filledQuantity, 3)
        self.assertEqual(inventory["positions"], [])

    def test_local_paper_end_of_day_close_uses_local_inventory_and_persists_accounting(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-local-eod-{uuid4().hex}.json"
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository(store_path)
            runtime = VotingEnsemblePaperExecutionRuntime(repository=repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
            repository.local_account_snapshot(observed_at=NOW)
            repository.inventory_ledger.apply_fill(
                client_order_id="local-eod-entry",
                order_intent_id="intent-local-eod-entry",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=3,
                fill_price=100.0,
                filled_at=NOW,
            )
            seed_local_quote(repository, bid=105.0, ask=105.05, bid_size=3, observed_at=NOW + timedelta(seconds=1))
            runtime.update_local_market_clock(
                {"nextClose": (NOW + timedelta(minutes=4)).isoformat().replace("+00:00", "Z"), "sourceAuthority": "local_test_clock"},
                observed_at=NOW,
            )

            result = runtime.reconcile_broker_state(evaluated_at=NOW)
            inventory = runtime.inventory_snapshot()
            reloaded = VotingEnsemblePaperExecutionRepository(store_path).inventory_snapshot()
            blocked = runtime.enqueue_from_decision(
                BuyDecisionService().evaluate({}),
                correlation_id="corr-eod-block",
                idempotency_key="idem-eod-block",
                source_job_id="job-eod-block",
                source_command_id="event-eod-block",
                evaluated_at=NOW + timedelta(seconds=2),
            )

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["brokerPositionsObserved"], 0)
            self.assertEqual(result["eodExitsSubmitted"], 1)
            self.assertEqual(inventory["positions"], [])
            self.assertEqual(inventory["account"]["cash"], 100015.0)
            self.assertEqual(inventory["account"]["realizedPnl"], 15.0)
            self.assertEqual(inventory["account"]["realizedPnlToday"], 15.0)
            self.assertEqual(inventory["account"]["dailyNetPnl"], 15.0)
            eod_orders = [order for order in inventory["orders"] if order.get("exitReason") == "END_OF_DAY_LIQUIDATION"]
            self.assertEqual(len(eod_orders), 1)
            self.assertEqual(eod_orders[0]["quantity"], 3)
            self.assertEqual(eod_orders[0]["status"], "FILLED")
            self.assertEqual(inventory["closedTrades"][0]["quantity"], 3)
            self.assertEqual(reloaded["positions"], [])
            self.assertEqual(reloaded["account"]["cash"], 100015.0)
            self.assertFalse(blocked["enqueued"])
            self.assertIn("voting_ensemble.local_paper.end_of_day_blocks_new_entries", blocked["reasonCodes"])
        store_path.unlink(missing_ok=True)

    def test_local_paper_end_of_day_close_ignores_foreign_spy_inventory(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
                "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
            },
        ):
            repository = VotingEnsemblePaperExecutionRepository()
            runtime = VotingEnsemblePaperExecutionRuntime(repository=repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
            repository.snapshots["foreign.position.spy"] = {"algorithmId": "weighted_voting", "symbol": "SPY", "quantity": 50}
            repository.local_account_snapshot(observed_at=NOW)
            repository.inventory_ledger.apply_fill(
                client_order_id="local-eod-owned-entry",
                order_intent_id="intent-local-eod-owned-entry",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=3,
                fill_price=100.0,
                filled_at=NOW,
            )
            seed_local_quote(repository, bid=105.0, ask=105.05, bid_size=100, observed_at=NOW + timedelta(seconds=1))
            runtime.update_local_market_clock({"forceClose": True, "sourceAuthority": "local_test_clock"}, observed_at=NOW)

            runtime.reconcile_broker_state(evaluated_at=NOW)
            inventory = runtime.inventory_snapshot()
            eod_fill = [fill for fill in inventory["fills"] if fill["clientOrderId"].startswith("ve-eod-")][0]

            self.assertEqual(eod_fill["filledQuantity"], 3)
            self.assertEqual(inventory["positions"], [])
            self.assertEqual(inventory["closedTrades"][0]["quantity"], 3)
            self.assertFalse(any(record.get("algorithmId") == "weighted_voting" for record in inventory["positions"]))

    def test_local_paper_end_of_day_flatten_cancels_remaining_same_inventory_exits(self) -> None:
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0", "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0"}):
            repository = VotingEnsemblePaperExecutionRepository()
            engine = VotingEnsembleLocalPaperExecutionEngine(repository)
            runtime = VotingEnsemblePaperExecutionRuntime(repository=repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
            seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=3)
            engine.submit_order(local_engine_intent(client_order_id="local-eod-protected-entry", quantity=3, side=Signal.BUY, limit_price=100.0))
            engine.refresh_order("local-eod-protected-entry")
            seed_local_quote(repository, bid=101.0, ask=101.05, bid_size=3, observed_at=NOW + timedelta(seconds=1))
            runtime.update_local_market_clock({"forceClose": True, "sourceAuthority": "local_test_clock"}, observed_at=NOW)

            runtime.reconcile_broker_state(evaluated_at=NOW)
            inventory = runtime.inventory_snapshot()
            protective = [order for order in inventory["orders"] if order.get("protectiveKind")]

            self.assertEqual(inventory["positions"], [])
            self.assertTrue(protective)
            self.assertTrue(all(order["status"] == "CANCELED" for order in protective))
            self.assertTrue(
                all(
                    "voting_ensemble.local_paper_execution_engine.local_exit_siblings_canceled_after_flatten"
                    in order["reasonCodes"]
                    for order in protective
                )
            )

    def test_local_paper_exit_requires_position_owned_by_voting_ensemble(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-exit-owner-{uuid4().hex}.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(
            json.dumps(
                {
                    "snapshots": {
                        "voting_ensemble.paper_execution.local_position.SPY": {
                            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
                            "capitalPartitionId": VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
                            "accountId": VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
                            "symbol": "SPY",
                            "quantity": 10,
                            "signedQuantity": 10,
                            "averagePrice": 100.0,
                            "averageEntryPrice": 100.0,
                            "markPrice": 100.0,
                            "notional": 1000.0,
                            "marketValue": 1000.0,
                            "unrealizedPnl": 0.0,
                            "realizedPnl": 0.0,
                            "positionOwner": "weighted_voting",
                            "exitOwner": "weighted_voting",
                            "updatedAt": NOW.isoformat().replace("+00:00", "Z"),
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        engine = VotingEnsembleLocalPaperExecutionEngine(repository)

        sell_ack = engine.submit_order(local_engine_intent(client_order_id="engine-foreign-owner-sell", quantity=1, side=Signal.SELL))
        inventory = repository.inventory_snapshot()

        self.assertEqual(sell_ack.status, "REJECTED")
        self.assertEqual(sell_ack.rejectedReason, "voting_ensemble.local_paper.sell_cannot_mutate_foreign_or_absent_position")
        self.assertEqual(inventory["positions"], [])
        store_path.unlink(missing_ok=True)

    def test_local_paper_inventory_ignores_foreign_spy_and_sell_closes_only_owned_quantity(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-foreign-isolation-{uuid4().hex}.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(
            json.dumps(
                {
                    "snapshots": {
                        "foreign.position.spy": {
                            "algorithmId": "weighted_voting",
                            "capitalPartitionId": "weighted_voting.paper.default",
                            "symbol": "SPY",
                            "quantity": 50,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        seed_local_quote(repository)
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            auto_start=False,
        )

        execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-owned-buy",
            idempotency_key="idem-owned-buy",
            source_job_id="job-owned-buy",
            source_command_id="event-owned-buy",
            evaluated_at=NOW,
        )
        execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))
        owned_inventory = execution_runtime.inventory_snapshot()
        position = owned_inventory["positions"][0]
        for field in (
            "symbol",
            "signedQuantity",
            "side",
            "averageEntryPrice",
            "markPrice",
            "marketValue",
            "unrealizedPnl",
            "realizedPnl",
            "openedAt",
            "updatedAt",
            "stopPrice",
            "profitTargetPrice",
            "entryOrderId",
            "lastFillId",
            "algorithmId",
            "capitalPartitionId",
            "positionOwner",
            "exitOwner",
        ):
            self.assertIn(field, position)
        self.assertEqual(position["signedQuantity"], 3)
        self.assertEqual(position["side"], "LONG")
        self.assertEqual(position["positionOwner"], VOTING_ENSEMBLE_ALGORITHM_ID)
        self.assertEqual(position["exitOwner"], VOTING_ENSEMBLE_ALGORITHM_ID)

        sell_plan = order_plan(side=Signal.SELL).model_copy(update={"quantity": 10})
        execution_runtime.enqueue_from_decision(
            {"algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID, "final_signal": "Sell", "safety_gate_failed": False, "order_plan": sell_plan.model_dump(mode="json")},
            correlation_id="corr-owned-sell",
            idempotency_key="idem-owned-sell",
            source_job_id="job-owned-sell",
            source_command_id="event-owned-sell",
            evaluated_at=NOW + timedelta(seconds=2),
        )
        execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=3))
        inventory = execution_runtime.inventory_snapshot()

        self.assertEqual(inventory["positions"], [])
        self.assertEqual(len(inventory["closedTrades"]), 1)
        self.assertEqual(inventory["closedTrades"][0]["quantity"], 3)
        sell_order = [order for order in inventory["orders"] if order["side"] == Signal.SELL.value][0]
        sell_fill = [fill for fill in inventory["fills"] if fill["side"] == Signal.SELL.value][0]
        self.assertEqual(sell_order["quantity"], 3)
        self.assertEqual(sell_fill["filledQuantity"], 3)
        self.assertEqual(inventory["closedTrades"][0]["algorithmId"], VOTING_ENSEMBLE_ALGORITHM_ID)
        self.assertEqual(inventory["closedTrades"][0]["capitalPartitionId"], VOTING_ENSEMBLE_CAPITAL_PARTITION_ID)
        self.assertFalse(any(record.get("algorithmId") == "weighted_voting" for record in repository.snapshots.values()))
        store_path.unlink(missing_ok=True)

    def test_local_paper_global_risk_reads_foreign_exposure_without_mutating_ve_inventory(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-global-risk-readonly-{uuid4().hex}.json"
        with patch.dict("os.environ", {"VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0", "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0"}):
            repository = VotingEnsemblePaperExecutionRepository(store_path)
            repository.local_account_snapshot(observed_at=NOW)
            repository.inventory_ledger.apply_fill(
                client_order_id="ve-owned-100",
                order_intent_id="intent-ve-owned-100",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=100,
                fill_price=100.0,
                filled_at=NOW,
            )
            repository.snapshots["global_risk.read_only_position.algorithm_b.spy"] = {
                "algorithmId": "algorithm_b",
                "capitalPartitionId": "algorithm_b.paper.default",
                "symbol": "SPY",
                "side": "SHORT",
                "quantity": 30,
                "marketValue": 3000.0,
                "readOnly": True,
                "sourceAuthority": "global_risk.read_only_aggregate",
            }
            seed_local_quote(repository, bid=100.0, ask=100.05, bid_size=200, observed_at=NOW + timedelta(seconds=1))
            execution_runtime = VotingEnsemblePaperExecutionRuntime(
                repository=repository,
                queue=VotingEnsemblePaperExecutionQueue(),
                auto_start=False,
            )
            sell_plan = order_plan(side=Signal.SELL).model_copy(update={"quantity": 150})

            execution_runtime.enqueue_from_decision(
                {"algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID, "final_signal": "Sell", "safety_gate_failed": False, "order_plan": sell_plan.model_dump(mode="json")},
                correlation_id="corr-global-readonly-sell",
                idempotency_key="idem-global-readonly-sell",
                source_job_id="job-global-readonly-sell",
                source_command_id="event-global-readonly-sell",
                evaluated_at=NOW + timedelta(seconds=2),
            )
            result = execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=3))
            inventory = execution_runtime.inventory_snapshot()

            self.assertIsNotNone(result)
            assert result is not None
            risk_portfolio = repository.read_snapshot(f"paper_order_gateway.global_risk_portfolio.{result['gatewayResult']['orderIntentId']}")
            self.assertEqual(
                [(position["algorithmId"], position["symbol"], position["quantity"], position["side"]) for position in risk_portfolio["positions"]],
                [(VOTING_ENSEMBLE_ALGORITHM_ID, "SPY", 100, "long"), ("algorithm_b", "SPY", 30, "short")],
            )
            self.assertEqual(inventory["positions"], [])
            self.assertEqual(inventory["closedTrades"][0]["quantity"], 100)
            sell_order = [order for order in inventory["orders"] if order["side"] == Signal.SELL.value][0]
            sell_fill = [fill for fill in inventory["fills"] if fill["side"] == Signal.SELL.value][0]
            self.assertEqual(sell_order["quantity"], 100)
            self.assertEqual(sell_fill["filledQuantity"], 100)
            self.assertFalse(any(position.get("algorithmId") == "algorithm_b" for position in inventory["positions"]))
        store_path.unlink(missing_ok=True)

    def test_automatic_broker_client_execution_state_is_durable_and_reloadable(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-execution-state-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        broker_client = FakePaperBrokerClient(
            fill=BrokerFillUpdate(
                clientOrderId="placeholder",
                filledQuantity=2,
                averageFillPrice=100.04,
                status="PARTIALLY_FILLED",
                updatedAt=NOW + timedelta(seconds=2),
            )
        )
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            broker_client=broker_client,
            execution_mode="BROKER_PAPER",
            auto_start=False,
        )

        enqueued = execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({"settingsHash": "settings-state"}),
            correlation_id="corr-state",
            idempotency_key="idem-state",
            source_job_id="job-state",
            source_command_id="event-state",
            evaluated_at=NOW,
        )
        result = execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))
        reloaded_repository = VotingEnsemblePaperExecutionRepository(store_path)
        reloaded_store = VotingEnsembleDurableExecutionStateStore(reloaded_repository)
        execution_states = reloaded_repository.inventory_snapshot()["executionStates"]

        self.assertTrue(enqueued["enqueued"])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(broker_client.submit_count, 1)
        self.assertEqual(len(execution_states), 1)
        state_record = execution_states[0]
        self.assertEqual(state_record["algorithm_id"], VOTING_ENSEMBLE_ALGORITHM_ID)
        self.assertEqual(state_record["brokerOrderId"], f"broker-{result['clientOrderId']}")
        self.assertEqual(state_record["idempotencyKey"], enqueued["executionIdempotencyKey"])
        self.assertEqual(state_record["parentDecisionId"], "ve-order-plan-1")
        self.assertEqual(state_record["parentEventId"], "event-state")
        self.assertEqual(state_record["settingsHash"], "order-config")
        self.assertEqual(state_record["symbol"], "SPY")
        self.assertEqual(state_record["side"], "BUY")
        self.assertEqual(state_record["requestedQuantity"], 3)
        self.assertEqual(state_record["filledQuantity"], 2)
        self.assertEqual(state_record["averageFillPrice"], 100.04)
        self.assertEqual(state_record["entryOrderStatus"], "ACCEPTED")
        self.assertEqual(state_record["stopPrice"], 99.0)
        self.assertEqual(state_record["targetPrice"], 101.5)
        self.assertIn("voting_ensemble.execution_adapter.partial_fill_tracked", state_record["completeReasonCodes"])
        self.assertIsNotNone(reloaded_store.get(result["clientOrderId"]))
        store_path.unlink(missing_ok=True)

    def test_automatic_broker_client_requires_durable_store_and_persistence_failure_blocks_entries(self) -> None:
        memory_repository = VotingEnsemblePaperExecutionRepository()
        with self.assertRaises(VotingEnsemblePaperExecutionPersistenceError):
            VotingEnsemblePaperExecutionRuntime(
                repository=memory_repository,
                queue=VotingEnsemblePaperExecutionQueue(),
                paper_gateway=PaperOrderGateway(FakePaperBroker(), memory_repository),
                broker_client=FakePaperBrokerClient(),
                execution_mode="BROKER_PAPER",
                auto_start=False,
            )

        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-persistence-failure-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        repository.record_persistence_failure(RuntimeError("disk full"))
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            execution_mode="BROKER_PAPER",
            auto_start=False,
        )

        permission = execution_runtime._entry_permission()
        summary = execution_runtime.summary()

        self.assertFalse(permission["newEntriesAllowed"])
        self.assertFalse(summary["persistenceHealthy"])
        self.assertEqual(summary["highSeverityRuntimeWarnings"][0]["severity"], "HIGH")
        self.assertIn("voting_ensemble.paper_execution.persistence_failure_blocks_new_entries", permission["reasonCodes"])
        store_path.unlink(missing_ok=True)

    def test_broker_confirmed_fill_is_required_before_local_position_exists(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-no-fill-position-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        broker_client = FakePaperBrokerClient(fill=None)
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            broker_client=broker_client,
            execution_mode="BROKER_PAPER",
            auto_start=False,
        )

        execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-no-fill",
            idempotency_key="idem-no-fill",
            source_job_id="job-no-fill",
            source_command_id="event-no-fill",
            evaluated_at=NOW,
        )
        execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))
        inventory = execution_runtime.inventory_snapshot()

        self.assertEqual(inventory["positions"], [])
        self.assertEqual(inventory["fills"], [])
        self.assertEqual(broker_client.submit_count, 1)
        store_path.unlink(missing_ok=True)

    def test_partial_fills_submit_and_resize_actual_protective_orders(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-protection-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        broker_client = FakePaperBrokerClient(
            fill=BrokerFillUpdate(
                clientOrderId="placeholder",
                filledQuantity=1,
                averageFillPrice=100.02,
                status="PARTIALLY_FILLED",
                updatedAt=NOW + timedelta(seconds=2),
            )
        )
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            broker_client=broker_client,
            execution_mode="BROKER_PAPER",
            auto_start=False,
        )

        execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-protect",
            idempotency_key="idem-protect",
            source_job_id="job-protect",
            source_command_id="event-protect",
            evaluated_at=NOW,
        )
        first = execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))
        broker_client.fill = BrokerFillUpdate(
            clientOrderId="placeholder",
            filledQuantity=3,
            averageFillPrice=100.03,
            status="FILLED",
            updatedAt=NOW + timedelta(seconds=10),
        )
        execution_runtime.reconcile_broker_state(evaluated_at=NOW + timedelta(seconds=10))
        inventory = execution_runtime.inventory_snapshot()

        self.assertIsNotNone(first)
        self.assertEqual(broker_client.protective_orders[0]["quantity"], 1)
        self.assertEqual(broker_client.replacements[0]["quantity"], 3)
        self.assertEqual(inventory["protectiveOrders"][0]["quantity"], 3)
        self.assertEqual(inventory["positions"][0]["quantity"], 3)
        self.assertLessEqual(inventory["protectiveOrders"][0]["quantity"], inventory["fills"][0]["filledQuantity"])
        store_path.unlink(missing_ok=True)

    def test_paper_off_still_allows_position_reducing_sell_exit(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-off-exit-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        broker_client = FakePaperBrokerClient(
            fill=BrokerFillUpdate(
                clientOrderId="placeholder",
                filledQuantity=3,
                averageFillPrice=100.02,
                status="FILLED",
                updatedAt=NOW + timedelta(seconds=2),
            )
        )
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            broker_client=broker_client,
            entry_permission_provider=lambda: {"newEntriesAllowed": False, "effectivePaperTradingEnabled": False, "reasonCodes": ["paper.off"]},
            execution_mode="BROKER_PAPER",
            auto_start=False,
        )
        repository.upsert_broker_fill(
            BrokerFillUpdate(clientOrderId="ve-existing-long", filledQuantity=3, averageFillPrice=100.0, status="FILLED", updatedAt=NOW),
            order_plan=order_plan(),
            order_intent_id="existing",
            observed_at=NOW,
        )

        enqueued = execution_runtime.enqueue_from_decision(
            {"algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID, "final_signal": "Sell", "safety_gate_failed": False, "order_plan": order_plan(side=Signal.SELL).model_dump(mode="json")},
            correlation_id="corr-exit",
            idempotency_key="idem-exit",
            source_job_id="job-exit",
            source_command_id="event-exit",
            evaluated_at=NOW,
        )
        result = execution_runtime.process_once(evaluated_at=NOW + timedelta(seconds=1))

        self.assertTrue(enqueued["enqueued"])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["submitted"])
        self.assertEqual(broker_client.submit_count, 1)
        store_path.unlink(missing_ok=True)

    def test_short_entries_are_blocked_when_voting_ensemble_short_trading_is_disabled(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-short-block-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            broker_client=FakePaperBrokerClient(),
            short_trading_enabled=False,
            execution_mode="BROKER_PAPER",
            auto_start=False,
        )

        result = execution_runtime.enqueue_from_decision(
            {"algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID, "final_signal": "Sell", "safety_gate_failed": False, "order_plan": order_plan(side=Signal.SELL).model_dump(mode="json")},
            correlation_id="corr-short",
            idempotency_key="idem-short",
            source_job_id="job-short",
            source_command_id="event-short",
            evaluated_at=NOW,
        )

        self.assertFalse(result["enqueued"])
        self.assertIn("voting_ensemble.paper_execution.short_entries_disabled", result["reasonCodes"])
        store_path.unlink(missing_ok=True)

    def test_startup_reconciliation_reads_broker_state_and_blocks_unattributed_position(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-startup-reconcile-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        broker_client = FakePaperBrokerClient()
        broker_client.positions = [
            BrokerPositionState(
                algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
                symbol="SPY",
                side=Signal.BUY,
                quantity=5,
                averageEntryPrice=100.0,
                markPrice=100.2,
                realizedPnlToday=0.0,
                openedAt=NOW,
            )
        ]
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            broker_client=broker_client,
            execution_mode="BROKER_PAPER",
            auto_start=False,
        )

        execution_runtime.start()
        execution_runtime.stop()
        inventory = execution_runtime.inventory_snapshot()
        blocked = execution_runtime.enqueue_from_decision(
            BuyDecisionService().evaluate({}),
            correlation_id="corr-ambiguous",
            idempotency_key="idem-ambiguous",
            source_job_id="job-ambiguous",
            source_command_id="event-ambiguous",
            evaluated_at=NOW,
        )

        self.assertGreaterEqual(broker_client.account_read_count, 1)
        self.assertGreaterEqual(broker_client.open_order_read_count, 1)
        self.assertGreaterEqual(broker_client.position_read_count, 1)
        self.assertEqual(inventory["brokerPositions"], [])
        self.assertIn("voting_ensemble.paper_execution.unattributed_broker_position_not_claimed", inventory["reconciliationBlocks"][0]["reasonCodes"])
        self.assertFalse(blocked["enqueued"])
        self.assertIn("voting_ensemble.paper_execution.unattributed_broker_position_not_claimed", blocked["reasonCodes"])
        store_path.unlink(missing_ok=True)

    def test_reconciliation_recovers_position_only_when_attributed_to_voting_ensemble_fill(self) -> None:
        store_path = Path("backend/tests/.tmp_voting_ensemble_runtime") / f"paper-attributed-reconcile-{uuid4().hex}.json"
        repository = VotingEnsemblePaperExecutionRepository(store_path)
        repository.upsert_broker_fill(
            BrokerFillUpdate(clientOrderId="ve-attributed-entry", filledQuantity=5, averageFillPrice=100.0, status="FILLED", updatedAt=NOW),
            order_plan=order_plan(),
            order_intent_id="attributed",
            observed_at=NOW,
        )
        broker_client = FakePaperBrokerClient()
        broker_client.positions = [
            BrokerPositionState(
                algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
                symbol="SPY",
                side=Signal.BUY,
                quantity=5,
                averageEntryPrice=100.0,
                markPrice=100.2,
                realizedPnlToday=0.0,
                openedAt=NOW,
            )
        ]
        execution_runtime = VotingEnsemblePaperExecutionRuntime(
            repository=repository,
            queue=VotingEnsemblePaperExecutionQueue(),
            paper_gateway=PaperOrderGateway(FakePaperBroker(), repository),
            broker_client=broker_client,
            execution_mode="BROKER_PAPER",
            auto_start=False,
        )

        result = execution_runtime.reconcile_broker_state(evaluated_at=NOW + timedelta(seconds=1))
        inventory = execution_runtime.inventory_snapshot()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["status"], "RECONCILED")
        self.assertEqual(inventory["brokerPositions"][0]["quantity"], 5)
        self.assertFalse(inventory["reconciliationBlocks"])
        store_path.unlink(missing_ok=True)

    def test_repository_rejects_foreign_algorithm_records(self) -> None:
        repository = VotingEnsemblePaperExecutionRepository()

        with self.assertRaises(VotingEnsemblePaperExecutionNamespaceError):
            repository.write_snapshot("paper_order_gateway.intent.foreign", {"algorithmId": "weighted_voting", "orderIntentId": "foreign"})

    def test_finalized_bar_event_rejects_non_spy_or_non_one_minute(self) -> None:
        with self.assertRaisesRegex(ValueError, "SPY"):
            FinalizedOneMinuteBarEvent(
                symbol="QQQ",
                barEndTimestamp=NOW,
                finalized=True,
                evaluationPayload={"symbol": "QQQ", "timeframe": "1Min"},
            ).to_command()
        with self.assertRaisesRegex(ValueError, "one-minute"):
            FinalizedOneMinuteBarEvent(
                symbol="SPY",
                barEndTimestamp=NOW,
                finalized=True,
                evaluationPayload={"symbol": "SPY", "timeframe": "5Min"},
            ).to_command()


class RecordingExecutionRuntime:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []

    def enqueue_from_decision(self, decision: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.enqueued.append({"algorithm_id": decision["algorithm_id"], **kwargs})
        return {
            "algorithmId": VOTING_ENSEMBLE_ALGORITHM_ID,
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "orderIntentId": "ve-intent-recording",
            "enqueued": True,
            "deduplicated": False,
            "reasonCodes": ["voting_ensemble.paper_execution.intent_enqueued"],
        }


class PassthroughAutomaticPayloadBuilder:
    def build(self, command: Any) -> dict[str, Any]:
        return dict(command.payload)


class BuyDecisionService:
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "final_signal": "Buy",
            "safety_gate_failed": False,
            "order_plan": order_plan().model_dump(mode="json"),
            "reason_codes": ["test.buy"],
        }


class HoldDecisionService:
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "algorithm_id": VOTING_ENSEMBLE_ALGORITHM_ID,
            "final_signal": "Hold",
            "safety_gate_failed": False,
            "reason_codes": ["test.hold"],
        }


class ExplodingService:
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("automatic finalized-bar evaluation must fail closed before service evaluation")


class FakeAlpacaSettings:
    def __init__(self, base_url: str) -> None:
        self.alpaca_trading_base_url = base_url
        self.alpaca_key_id = "paper-key"
        self.alpaca_secret_key = "paper-secret"

    @property
    def has_alpaca_credentials(self) -> bool:
        return True


class FakeAlpacaHttpClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> "FakeAlpacaResponse":
        self.calls.append({"method": "GET", "url": url, "kwargs": kwargs})
        if url.endswith("/account"):
            return FakeAlpacaResponse({"id": "paper-account", "equity": "100000", "buying_power": "90000"})
        if url.endswith("/clock"):
            return FakeAlpacaResponse({"is_open": True, "timestamp": NOW.isoformat().replace("+00:00", "Z")})
        if url.endswith("/assets/SPY"):
            return FakeAlpacaResponse({"symbol": "SPY", "status": "active", "tradable": True})
        if url.endswith("/orders"):
            return FakeAlpacaResponse([alpaca_order_payload()])
        if url.endswith("/positions"):
            return FakeAlpacaResponse([{"symbol": "SPY", "qty": "1", "side": "long", "avg_entry_price": "100", "current_price": "100.1"}])
        if url.endswith("/account/activities/FILL"):
            return FakeAlpacaResponse([{"client_order_id": "ve-client-paper", "qty": "1", "price": "100.01", "transaction_time": NOW.isoformat().replace("+00:00", "Z")}])
        if url.endswith("/orders:by_client_order_id"):
            return FakeAlpacaResponse(alpaca_order_payload())
        return FakeAlpacaResponse({})

    def post(self, url: str, **kwargs: Any) -> "FakeAlpacaResponse":
        self.calls.append({"method": "POST", "url": url, "kwargs": kwargs})
        client_order_id = str((kwargs.get("json") or {}).get("client_order_id") or "ve-client-paper")
        return FakeAlpacaResponse({**alpaca_order_payload(client_order_id=client_order_id), "status": "accepted", "filled_qty": "0"})

    def patch(self, url: str, **kwargs: Any) -> "FakeAlpacaResponse":
        self.calls.append({"method": "PATCH", "url": url, "kwargs": kwargs})
        return FakeAlpacaResponse(alpaca_order_payload())

    def delete(self, url: str, **kwargs: Any) -> "FakeAlpacaResponse":
        self.calls.append({"method": "DELETE", "url": url, "kwargs": kwargs})
        return FakeAlpacaResponse({}, status_code=204)


class FakeAlpacaResponse:
    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        return None


def alpaca_order_payload(*, client_order_id: str = "ve-client-paper") -> dict[str, Any]:
    return {
        "id": "broker-order-1",
        "client_order_id": client_order_id,
        "symbol": "SPY",
        "side": "buy",
        "type": "limit",
        "status": "partially_filled",
        "qty": "3",
        "filled_qty": "1",
        "filled_avg_price": "100.01",
        "limit_price": "100.0",
        "submitted_at": NOW.isoformat().replace("+00:00", "Z"),
        "updated_at": NOW.isoformat().replace("+00:00", "Z"),
        "filled_at": NOW.isoformat().replace("+00:00", "Z"),
    }


class FakePaperBrokerClient:
    broker_kind = "alpaca_paper_client"
    paper_endpoint = True
    configured = True

    def __init__(self, *, fill: BrokerFillUpdate | None = None) -> None:
        self.fill = fill
        self.submit_count = 0
        self.orders: list[BrokerOrderState] = []
        self.positions: list[BrokerPositionState] = []
        self.protective_orders: list[dict[str, Any]] = []
        self.replacements: list[dict[str, Any]] = []
        self.exit_orders: list[dict[str, Any]] = []
        self.clock: dict[str, Any] = {"isOpen": True, "sourceAuthority": "test_broker_clock"}
        self.account_read_count = 0
        self.open_order_read_count = 0
        self.position_read_count = 0

    def refresh_market_clock(self) -> dict[str, Any]:
        return dict(self.clock)

    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        self.account_read_count += 1
        return BrokerAccountSnapshot(
            accountId="paper",
            equity=100_000,
            buyingPower=100_000,
            observedAt=NOW,
            sessionDate=SESSION_DATE,
            sourceAuthority="broker",
            positionsReconciled=True,
            openOrdersReconciled=True,
        )

    def refresh_positions(self) -> list[BrokerPositionState]:
        self.position_read_count += 1
        return self.positions

    def refresh_open_orders(self) -> list[BrokerOrderState]:
        self.open_order_read_count += 1
        return self.orders

    def verify_symbol_tradable(self, symbol: str) -> bool:
        return symbol.upper() == "SPY"

    def verify_buying_power(self, order_plan: OrderPlan) -> bool:
        return True

    def submit_order(self, order_plan: OrderPlan, client_order_id: str) -> BrokerOrderAck:
        self.submit_count += 1
        if self.fill and self.fill.filledQuantity > 0:
            signed_side = order_plan.side
            self.positions = [
                BrokerPositionState(
                    algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
                    symbol=order_plan.symbol,
                    side=signed_side,
                    quantity=self.fill.filledQuantity,
                    averageEntryPrice=self.fill.averageFillPrice or order_plan.entryPrice,
                    markPrice=self.fill.averageFillPrice or order_plan.entryPrice,
                    realizedPnlToday=0.0,
                    openedAt=NOW,
                )
            ]
        self.orders.append(
            BrokerOrderState(
                algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
                symbol=order_plan.symbol,
                side=order_plan.side,
                clientOrderId=client_order_id,
                orderType=order_plan.orderType,
                status="ACCEPTED",
                quantity=order_plan.quantity,
                filledQuantity=self.fill.filledQuantity if self.fill else 0,
                entryPrice=order_plan.entryPrice,
                stopPrice=order_plan.stopPrice,
                submittedAt=NOW,
            )
        )
        return BrokerOrderAck(
            clientOrderId=client_order_id,
            brokerOrderId=f"broker-{client_order_id}",
            status="ACCEPTED",
            acceptedAt=NOW + timedelta(seconds=1),
        )

    def refresh_order(self, client_order_id: str) -> BrokerFillUpdate | None:
        return self.fill.model_copy(update={"clientOrderId": client_order_id}) if self.fill else None

    def refresh_order_status(self, client_order_id: str) -> str | None:
        return "FILLED" if self.fill and self.fill.status == "FILLED" else "PARTIALLY_FILLED" if self.fill else "ACCEPTED"

    def submit_protective_order(self, *, symbol: str, side: Signal | str, quantity: int, stop_price: float | None, target_price: float | None, client_order_id: str) -> BrokerOrderAck:
        payload = {
            "symbol": symbol,
            "side": Signal(side).value,
            "quantity": quantity,
            "stopPrice": stop_price,
            "targetPrice": target_price,
            "clientOrderId": client_order_id,
        }
        self.protective_orders.append(payload)
        return BrokerOrderAck(clientOrderId=client_order_id, brokerOrderId=f"broker-{client_order_id}", status="ACCEPTED", acceptedAt=NOW)

    def replace_order(self, client_order_id: str, *, limit_price: float | None = None, quantity: int | None = None, stop_price: float | None = None) -> dict[str, Any] | None:
        payload = {"id": f"broker-{client_order_id}", "client_order_id": client_order_id, "status": "accepted", "quantity": quantity, "stop_price": stop_price, "limit_price": limit_price}
        self.replacements.append(payload)
        return payload

    def submit_position_exit_order(self, *, symbol: str, side: Signal | str, quantity: int, limit_price: float, client_order_id: str) -> BrokerOrderAck:
        self.exit_orders.append({"symbol": symbol, "side": Signal(side).value, "quantity": quantity, "limitPrice": limit_price, "clientOrderId": client_order_id})
        return BrokerOrderAck(clientOrderId=client_order_id, brokerOrderId=f"broker-{client_order_id}", status="ACCEPTED", acceptedAt=NOW)


class FakePaperBroker:
    configured = True
    broker_kind = "alpaca_paper"
    paper_endpoint = True

    def __init__(self, *, fill_status: str = "ACCEPTED", filled_quantity: int = 0) -> None:
        self.fill_status = fill_status
        self.filled_quantity = filled_quantity
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
        if self.filled_quantity <= 0:
            return None
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId=VOTING_ENSEMBLE_ALGORITHM_ID,
            orderIntentId=client_order_id,
            symbol="SPY",
            side=Signal.BUY,
            filledQuantity=self.filled_quantity,
            averageFillPrice=100.01,
            status=self.fill_status,
            filledAt=NOW,
        )

    def cancel_order(self, client_order_id: str) -> bool:
        return True

    def refresh_positions(self) -> list[dict[str, Any]]:
        return []


def order_plan(*, side: Signal = Signal.BUY) -> OrderPlan:
    stop_price = 99.0 if side == Signal.BUY else 101.0
    target_price = 101.5 if side == Signal.BUY else 98.5
    return OrderPlan(
        orderPlanId="ve-order-plan-1",
        candidateId="ve-candidate-1",
        symbol="SPY",
        side=side,
        orderType="LIMIT",
        quantity=3,
        entryPrice=100.0,
        stopPrice=stop_price,
        targetPrice=target_price,
        limitPrice=100.0,
        maximumHoldingMinutes=30,
        timeInForce="DAY",
        eligible=True,
        validationErrors=[],
        explanation="test order",
        generatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="order-config",
    )


def local_engine_intent(
    *,
    client_order_id: str,
    quantity: int,
    side: Signal = Signal.BUY,
    limit_price: float = 100.0,
    trigger_price: float | None = None,
    order_type: str = "LIMIT",
    planned_risk: float = 5.0,
    algorithm_id: str = VOTING_ENSEMBLE_ALGORITHM_ID,
    capital_partition_id: str = VOTING_ENSEMBLE_CAPITAL_PARTITION_ID,
    settings_snapshot: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        executionMode="LOCAL_PAPER",
        algorithmId=algorithm_id,
        capitalPartitionId=capital_partition_id,
        accountId=VOTING_ENSEMBLE_LOCAL_ACCOUNT_ID,
        decisionId=f"decision-{client_order_id}",
        orderIntentId=f"intent-{client_order_id}",
        clientOrderId=client_order_id,
        symbol="SPY",
        side=side,
        submittedQuantity=quantity,
        proposedQuantity=quantity,
        globallyAllowedQuantity=quantity,
        orderType=order_type,
        limitPrice=limit_price,
        triggerPrice=trigger_price if trigger_price is not None else limit_price,
        stopPrice=99.0 if side == Signal.BUY else 101.0,
        targetPrice=101.5 if side == Signal.BUY else 98.5,
        plannedRiskDollars=planned_risk,
        createdAt=NOW,
        decisionTimestamp=NOW,
        timeInForce="DAY",
        reasonCodes=(),
        settingsSnapshot=dict(settings_snapshot or {}),
    )


def seed_local_quote(
    repository: VotingEnsemblePaperExecutionRepository,
    *,
    bid: float = 100.0,
    ask: float = 100.0,
    bid_size: float = 100.0,
    ask_size: float = 100.0,
    observed_at: datetime = NOW,
) -> None:
    repository.mark_local_positions_from_market_data(
        symbol="SPY",
        nbbo={
            "bid": bid,
            "ask": ask,
            "bidSize": bid_size,
            "askSize": ask_size,
            "quoteTimestamp": observed_at.isoformat().replace("+00:00", "Z"),
            "marketDataReceiptTimestamp": observed_at.isoformat().replace("+00:00", "Z"),
        },
        observed_at=observed_at,
    )


def candle() -> dict[str, Any]:
    return {
        "timestamp": NOW.isoformat(),
        "open": 100.0,
        "high": 100.2,
        "low": 99.9,
        "close": 100.1,
        "volume": 1000,
    }


if __name__ == "__main__":
    unittest.main()
