import asyncio
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.dynamic_settings import DynamicSettingsResolver, resolve_effective_settings
from backend.app.algorithms.weighted_voting.market_condition import classify_market_condition
from backend.app.algorithms.weighted_voting.market_snapshot import build_weighted_voting_market_snapshot
from backend.app.algorithms.weighted_voting.inventory import WeightedVotingInventoryEventType, WeightedVotingInventoryRepository
from backend.app.algorithms.weighted_voting.local_paper_broker import WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE, WeightedVotingLocalPaperBroker
from backend.app.algorithms.weighted_voting.rollout import WeightedVotingRolloutFlags, WeightedVotingRolloutValidation
from backend.app.algorithms.weighted_voting.runtime_supervisor import (
    WeightedVotingBarEventWorker,
    WeightedVotingEventBus,
    WeightedVotingFinalizedBarProducer,
    WeightedVotingFinalizedBarProducerConfig,
    WeightedVotingFinalisedBarEvent,
    WeightedVotingRiskWorker,
    WeightedVotingMarketCalendar,
    WeightedVotingRuntimeConfig,
    WeightedVotingRuntimeSupervisor,
    runtime_supervisor_status,
    weighted_voting_bar_event_idempotency_key,
    weighted_voting_decision_idempotency_key,
    weighted_voting_order_intent_idempotency_key,
)
from backend.app.algorithms.weighted_voting.runtime_context import WeightedVotingStaticAccountPort, WeightedVotingStaticGlobalRiskPort
from backend.app.algorithms.weighted_voting.persistence import WEIGHTED_VOTING_SETTINGS_KEY, persist_effective_settings
from backend.app.algorithms.weighted_voting.scheduler import CANDIDATE_WEIGHT_PREFIX, PUBLISHED_WEIGHT_PREFIX
from backend.app.algorithms.weighted_voting.service import WeightedVotingService
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway
from backend.app.gates import GlobalGateResponse, GlobalOrderProposal, apply_global_gate_response


SESSION_OPEN = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)
MAIN_PATH = Path(__file__).parents[1] / "app" / "main.py"


class WeightedVotingRuntimeSupervisorTest(unittest.TestCase):
    def test_supervisor_contract_declares_workers_and_backend_startup(self) -> None:
        status = runtime_supervisor_status()
        main_source = MAIN_PATH.read_text(encoding="utf-8")

        self.assertTrue(status["startsWithBackend"])
        self.assertFalse(status["dashboardRequired"])
        self.assertIn("WeightedVotingDecisionWorker", status["workers"])
        self.assertIn("await get_weighted_voting_runtime_supervisor().start()", main_source)
        self.assertIn("await get_weighted_voting_runtime_supervisor().shutdown()", main_source)

    def test_default_supervisor_constructs_local_paper_dependencies_fail_closed_without_alpaca_credentials(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ALPACA_TRADING_BASE_URL": "https://paper-api.alpaca.markets/v2",
                "APCA_API_KEY_ID": "",
                "APCA_API_SECRET_KEY": "",
            },
            clear=False,
        ):
            supervisor = WeightedVotingRuntimeSupervisor(
                store=MemoryStore(),
                config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
                event_bus=WeightedVotingEventBus(maxsize=8),
            )

        self.assertIsNotNone(supervisor.paper_gateway)
        self.assertEqual(supervisor.paper_gateway.broker.broker_kind, "weighted_voting_local_paper")
        self.assertEqual(supervisor.paper_gateway.execution_mode, "LOCAL_PAPER")
        self.assertEqual(
            supervisor.paper_gateway.account_snapshot_provider(evaluated_at=SESSION_OPEN).accountId,
            supervisor.paper_gateway.broker.gateway_account_snapshot(evaluated_at=SESSION_OPEN).accountId,
        )
        self.assertEqual(
            supervisor.paper_gateway.portfolio_snapshot_provider(evaluated_at=SESSION_OPEN).algorithmTradesToday,
            supervisor.paper_gateway.broker.gateway_portfolio_snapshot(evaluated_at=SESSION_OPEN).algorithmTradesToday,
        )
        self.assertFalse(supervisor.paper_gateway.broker.live_trading_enabled)
        self.assertTrue(supervisor.paper_gateway.broker.verify_paper_account())
        account = supervisor.account_port.account_observation(as_of=SESSION_OPEN)
        self.assertTrue(account.available)
        self.assertEqual(account.account_equity, 100000.0)
        self.assertEqual(account.broker_buying_power, 100000.0)
        self.assertIn("weighted_voting.local_paper.account_from_dedicated_inventory", account.reason_codes)
        snapshot = supervisor.inventory_repository.current_snapshot(now=SESSION_OPEN)
        self.assertEqual(snapshot.initial_capital, 100000.0)
        self.assertIn("weighted_voting.inventory.snapshot.current", supervisor.store.snapshots)

    def test_runtime_health_exposes_local_paper_inventory_without_alpaca_dependency(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ALPACA_TRADING_BASE_URL": "https://api.alpaca.markets/v2",
                "APCA_API_KEY_ID": "paper-key",
                "APCA_API_SECRET_KEY": "paper-secret",
            },
            clear=False,
        ):
            supervisor = WeightedVotingRuntimeSupervisor(
                store=MemoryStore(),
                config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
                event_bus=WeightedVotingEventBus(maxsize=8),
            )

        health = supervisor.health()
        inventory = health["inventory"]

        self.assertEqual(health["executionMode"], "LOCAL_PAPER")
        self.assertEqual(health["brokerKind"], "weighted_voting_local_paper")
        self.assertFalse(health["alpacaDependency"])
        self.assertEqual(health["operationalStatus"]["inventory"], inventory)
        self.assertTrue(inventory["available"])
        self.assertTrue(inventory["authoritative"])
        self.assertEqual(inventory["cash"], 100000.0)
        self.assertEqual(inventory["reservedCash"], 0.0)
        self.assertEqual(inventory["availableBuyingPower"], 100000.0)
        self.assertEqual(inventory["equity"], 100000.0)
        self.assertEqual(inventory["realizedPnl"], 0.0)
        self.assertEqual(inventory["unrealizedPnl"], 0.0)
        self.assertEqual(inventory["grossExposure"], 0.0)
        self.assertEqual(inventory["openPositions"], [])
        self.assertEqual(inventory["pendingOrders"], [])
        serialized = json.dumps(health)
        self.assertNotIn("paper-key", serialized)
        self.assertNotIn("paper-secret", serialized)

    def test_default_supervisor_ignores_live_alpaca_endpoint_for_local_paper(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ALPACA_TRADING_BASE_URL": "https://api.alpaca.markets/v2",
                "APCA_API_KEY_ID": "paper-key",
                "APCA_API_SECRET_KEY": "paper-secret",
            },
            clear=False,
        ):
            supervisor = WeightedVotingRuntimeSupervisor(
                store=MemoryStore(),
                config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
                event_bus=WeightedVotingEventBus(maxsize=8),
            )

        self.assertEqual(supervisor.paper_gateway.broker.broker_kind, "weighted_voting_local_paper")
        self.assertEqual(supervisor.paper_gateway.execution_mode, "LOCAL_PAPER")
        self.assertEqual(
            supervisor.paper_gateway.account_snapshot_provider(evaluated_at=SESSION_OPEN).accountId,
            supervisor.paper_gateway.broker.gateway_account_snapshot(evaluated_at=SESSION_OPEN).accountId,
        )
        self.assertEqual(
            supervisor.paper_gateway.portfolio_snapshot_provider(evaluated_at=SESSION_OPEN).algorithmTradesToday,
            supervisor.paper_gateway.broker.gateway_portfolio_snapshot(evaluated_at=SESSION_OPEN).algorithmTradesToday,
        )
        self.assertTrue(supervisor.paper_gateway.broker.verify_paper_endpoint())
        self.assertFalse(supervisor.paper_gateway.broker.live_trading_enabled)
        readiness = supervisor.runtime_control()["readiness"]
        self.assertNotIn("weighted_voting.runtime.control.paper_endpoint_unverified", readiness["blocking_reason_codes"])

    def test_default_supervisor_does_not_construct_alpaca_paper_dependencies(self) -> None:
        with patch(
            "backend.app.algorithms.weighted_voting.alpaca_paper_broker.build_weighted_voting_paper_gateway_dependencies",
            side_effect=AssertionError("Alpaca paper dependencies must not be built for LOCAL_PAPER"),
        ):
            supervisor = WeightedVotingRuntimeSupervisor(
                store=MemoryStore(),
                config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
                event_bus=WeightedVotingEventBus(maxsize=8),
            )

        self.assertEqual(supervisor.config.paper_execution_mode, "LOCAL_PAPER")
        self.assertIsInstance(supervisor.paper_gateway.broker, WeightedVotingLocalPaperBroker)
        self.assertEqual(supervisor.paper_gateway.execution_mode, "LOCAL_PAPER")

    def test_explicit_broker_paper_mode_constructs_alpaca_adapter_branch(self) -> None:
        broker = ExplicitBrokerPaperBroker()
        broker_account_port = WeightedVotingStaticAccountPort(
            account_equity=125000.0,
            broker_buying_power=100000.0,
            source_id="weighted_voting.test.explicit_broker_paper",
        )
        with patch(
            "backend.app.algorithms.weighted_voting.alpaca_paper_broker.build_weighted_voting_paper_gateway_dependencies",
            return_value=(broker, broker_account_port),
        ) as build_dependencies:
            supervisor = WeightedVotingRuntimeSupervisor(
                store=MemoryStore(),
                config=WeightedVotingRuntimeConfig(
                    paper_execution_mode="BROKER_PAPER",
                    heartbeat_interval_seconds=999.0,
                    maintenance_interval_seconds=999.0,
                ),
                event_bus=WeightedVotingEventBus(maxsize=8),
            )

        build_dependencies.assert_called_once_with()
        self.assertIs(supervisor.paper_gateway.broker, broker)
        self.assertEqual(supervisor.paper_gateway.execution_mode, "BROKER_PAPER")
        self.assertIs(supervisor.account_port, broker_account_port)

    def test_weighted_voting_config_selects_broker_paper_when_runtime_config_omitted(self) -> None:
        broker = ExplicitBrokerPaperBroker()
        with patch(
            "backend.app.algorithms.weighted_voting.alpaca_paper_broker.build_weighted_voting_paper_gateway_dependencies",
            return_value=(broker, broker),
        ) as build_dependencies:
            supervisor = WeightedVotingRuntimeSupervisor(
                store=MemoryStore(),
                weighted_config=WeightedVotingConfig(paper_execution_mode="BROKER_PAPER"),
                event_bus=WeightedVotingEventBus(maxsize=8),
            )

        build_dependencies.assert_called_once_with()
        self.assertEqual(supervisor.config.paper_execution_mode, "BROKER_PAPER")
        self.assertIs(supervisor.paper_gateway.broker, broker)
        self.assertEqual(supervisor.paper_gateway.execution_mode, "BROKER_PAPER")

    def test_finalised_bar_event_automatically_persists_one_decision(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)

        record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload())))

        self.assertEqual(record["status"], "decision_persisted")
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.decisions.")]), 1)
        self.assertTrue(any(key.startswith("weighted_voting.runtime.checkpoints.SPY") for key in store.snapshots))
        self.assertEqual(supervisor.health()["persistedDecisions"], 1)

    def test_finalised_bar_event_marks_weighted_voting_inventory_to_local_close(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        initial = inventory.current_snapshot(now=SESSION_OPEN)
        inventory.append_event(
            event_id="runtime-mark-to-market-position",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(position_id="runtime-mark-position", quantity=10, average_entry_price=100.0),
            occurred_at=SESSION_OPEN + timedelta(seconds=1),
            expected_snapshot_version=initial.snapshot_version,
        )
        supervisor = supervisor_for(store, inventory_repository=inventory)
        payload = evaluate_payload()
        expected_close = payload["candles"][-1]["close"]

        record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(payload)))
        marked = inventory.current_snapshot(now=SESSION_OPEN)
        context_records = [snapshot for key, snapshot in store.snapshots.items() if key.startswith("weighted_voting.runtime.contexts.")]

        self.assertEqual(record["status"], "decision_persisted")
        self.assertEqual(marked.last_price, expected_close)
        self.assertEqual(marked.open_positions[0].mark_price, expected_close)
        self.assertAlmostEqual(marked.unrealised_pnl, (expected_close - 100.0) * 10)
        self.assertAlmostEqual(marked.market_value, expected_close * 10)
        self.assertAlmostEqual(marked.equity, 25_000.0 + marked.unrealised_pnl)
        self.assertTrue(any("mark-to-market-SPY" in key for key in store.snapshots))
        self.assertEqual(context_records[-1]["inventory_snapshot_version"], marked.snapshot_version)

    def test_finalised_bar_triggers_weighted_voting_local_protective_stop(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        initial = inventory.current_snapshot(now=SESSION_OPEN)
        inventory.append_event(
            event_id="runtime-local-stop-entry-fill",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(position_id="runtime-local-stop-position", quantity=3, average_entry_price=100.0),
            occurred_at=SESSION_OPEN + timedelta(seconds=1),
            expected_snapshot_version=initial.snapshot_version,
        )
        store.write_snapshot(
            f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.runtime-local-stop",
            {
                "algorithmId": "weighted_voting",
                "executionMode": "LOCAL_PAPER",
                "clientOrderId": "runtime-local-stop",
                "parentClientOrderId": "runtime-local-stop-client",
                "parentPositionId": "runtime-local-stop-position",
                "decisionId": "runtime-local-stop-decision",
                "orderIntentId": "runtime-local-stop-intent",
                "symbol": "SPY",
                "side": "SELL",
                "quantity": 3,
                "filledQuantity": 0,
                "remainingQuantity": 3,
                "protectiveKind": "stop_loss",
                "orderType": "STOP",
                "stopPrice": 99.0,
                "status": "OPEN",
                "createdAt": SESSION_OPEN.isoformat(),
                "updatedAt": SESSION_OPEN.isoformat(),
                "reasonCodes": ("weighted_voting.test.local_protective_stop_active",),
            },
        )
        foreign_key = f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.foreign-runtime-stop"
        store.write_snapshot(foreign_key, {"algorithmId": "voting_ensemble", "clientOrderId": "foreign-runtime-stop", "symbol": "SPY", "status": "OPEN"})
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=WeightedVotingService(store=store),
            store=store,
            inventory_repository=inventory,
            paper_gateway=weighted_voting_local_gateway(broker, store),
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
        )
        payload = evaluate_payload(offset_minutes=3)
        payload["bid"] = 98.9
        payload["ask"] = 99.0

        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(payload)))
        snapshot = inventory.current_snapshot(now=SESSION_OPEN)
        stop_order = store.read_snapshot(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.runtime-local-stop")

        self.assertEqual(snapshot.open_positions, ())
        self.assertEqual(stop_order["status"], "FILLED")
        self.assertEqual(store.read_snapshot(foreign_key)["status"], "OPEN")

    def test_runtime_builds_full_context_from_completed_bar_event(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        payload = evaluate_payload()
        payload["five_minute_candles"] = [
            {
                "timestamp": payload["candles"][index]["timestamp"],
                "open": payload["candles"][index - 4]["open"],
                "high": max(row["high"] for row in payload["candles"][index - 4 : index + 1]),
                "low": min(row["low"] for row in payload["candles"][index - 4 : index + 1]),
                "close": payload["candles"][index]["close"],
                "volume": sum(row["volume"] for row in payload["candles"][index - 4 : index + 1]),
                "finalized": True,
            }
            for index in range(4, len(payload["candles"]), 5)
        ]
        snapshot = build_weighted_voting_market_snapshot(payload)
        service = WeightedVotingService(store=store)
        weight_state = service.active_weight_state()
        condition = classify_market_condition(snapshot)
        effective = DynamicSettingsResolver().resolve(condition, timestamp=snapshot.data_timestamp)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=service,
            store=store,
            inventory_repository=inventory,
            account_port=WeightedVotingStaticAccountPort(account_equity=100000.0, broker_buying_power=75000.0, source_id="weighted_voting.test.account_port"),
            global_risk_port=WeightedVotingStaticGlobalRiskPort(global_available_risk=1000.0, global_max_shares=100, gate_response=None, source_id="weighted_voting.test.global_risk_port"),
            config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
        )

        context = supervisor.build_runtime_context_from_finalised_bar(
            snapshot=snapshot,
            active_weight_state=weight_state,
            effective_settings=effective,
            market_condition=condition,
            observed_at=snapshot.data_timestamp,
        )

        self.assertEqual(context.mode, "production")
        self.assertEqual(len(context.finalised_one_minute_market_snapshot.one_minute_candles), len(payload["candles"]))
        self.assertGreaterEqual(len(context.five_minute_candles), 1)
        self.assertEqual(context.inventory_snapshot.algorithm_id, "weighted_voting")
        self.assertEqual(context.read_only_account_equity, 100000.0)
        self.assertEqual(context.read_only_broker_buying_power, 75000.0)
        self.assertEqual(context.global_risk_state.global_available_risk, 1000.0)
        self.assertEqual(context.global_risk_state.global_max_shares, 100)
        self.assertIn("exchange_calendar", context.exchange_session_state.reason_codes[0])
        self.assertEqual(context.algorithm_daily_pnl, 0.0)
        self.assertEqual(context.effective_settings.settings_version, effective.settings_version)
        self.assertTrue(any(key.startswith("weighted_voting.runtime.contexts.") for key in store.snapshots))

    def test_finalised_bar_event_uses_stable_settings_not_one_minute_payload_settings(self) -> None:
        store = MemoryStore()
        stable_settings = resolve_effective_settings(
            dynamic_values={"slippage_allowance_per_share": 0.02, "maximum_shares": 7},
            baseline_config=WeightedVotingConfig(),
            source_evidence=("weighted_voting.test.stable_settings_version",),
        )
        persist_effective_settings(store, stable_settings)
        supervisor = supervisor_for(store)
        first_payload = evaluate_payload()
        first_payload["settingsVersion"] = "one-minute-settings-should-be-ignored"
        first_payload["effective_settings"] = {"settings_version": "bar-derived-settings", "maximum_shares": 999999}
        first_payload["slippage_per_share"] = 12.34
        first_payload["fee_per_share"] = 56.78
        second_payload = evaluate_payload(offset_minutes=1)
        second_payload["settingsVersion"] = "different-one-minute-settings-should-still-be-ignored"
        second_payload["effective_settings"] = {"settings_version": "second-bar-derived-settings", "maximum_shares": 1}
        second_payload["slippage_per_share"] = 87.65
        second_payload["fee_per_share"] = 43.21

        first = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(first_payload)))
        second = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(second_payload)))

        self.assertEqual(first["status"], "decision_persisted")
        self.assertEqual(second["status"], "decision_persisted")
        self.assertEqual(store.read_snapshot(WEIGHTED_VOTING_SETTINGS_KEY)["settings_version"], stable_settings.settings_version)
        context_records = [
            snapshot
            for key, snapshot in store.snapshots.items()
            if key.startswith("weighted_voting.runtime.contexts.")
        ]
        self.assertEqual(len(context_records), 2)
        self.assertEqual({record["settings_version"] for record in context_records}, {stable_settings.settings_version})
        self.assertEqual({record["estimated_slippage"] for record in context_records}, {stable_settings.slippage_allowance_per_share})
        self.assertEqual({record["estimated_fees"] for record in context_records}, {WeightedVotingConfig().fee_per_share})
        proposal_records = [
            snapshot
            for key, snapshot in store.snapshots.items()
            if key.startswith("weighted_voting.order_proposals.")
        ]
        self.assertEqual(len(proposal_records), 2)
        self.assertEqual({record["settings_version"] for record in proposal_records}, {stable_settings.settings_version})

    def test_missing_effective_settings_are_bootstrapped_once_and_reused_across_bar_events(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)

        first = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload())))
        bootstrapped_version = store.read_snapshot(WEIGHTED_VOTING_SETTINGS_KEY)["settings_version"]
        second = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=1))))

        self.assertEqual(first["status"], "decision_persisted")
        self.assertEqual(second["status"], "decision_persisted")
        self.assertEqual(store.read_snapshot(WEIGHTED_VOTING_SETTINGS_KEY)["settings_version"], bootstrapped_version)
        context_versions = {
            snapshot["settings_version"]
            for key, snapshot in store.snapshots.items()
            if key.startswith("weighted_voting.runtime.contexts.")
        }
        self.assertEqual(context_versions, {bootstrapped_version})

    def test_duplicate_bar_events_produce_only_one_decision(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        event = event_from_payload(evaluate_payload())

        first = asyncio.run(supervisor.process_finalised_bar_event(event))
        second = asyncio.run(supervisor.process_finalised_bar_event(event))

        self.assertEqual(first["status"], "decision_persisted")
        self.assertEqual(second["status"], "duplicate_noop")
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.decisions.")]), 1)
        self.assertEqual(supervisor.health()["duplicateEvents"], 1)

    def test_restart_recovery_resumes_from_last_checkpoint(self) -> None:
        store = MemoryStore()
        first_supervisor = supervisor_for(store)
        event = event_from_payload(evaluate_payload())
        asyncio.run(first_supervisor.process_finalised_bar_event(event))

        recovered = supervisor_for(store)
        recovered.recover_from_checkpoints()

        self.assertEqual(recovered.health()["lastEventTimestampBySymbol"]["SPY"], event.finalised_candle_timestamp.isoformat())
        self.assertTrue(recovered.health()["lastCheckpointBySymbol"]["SPY"])

    def test_local_paper_restart_rebuilds_inventory_orders_fills_and_resumes_protective_monitoring_without_alpaca_positions(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        broker = WeightedVotingLocalPaperBroker(store, inventory)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=WeightedVotingService(store=store),
            store=store,
            inventory_repository=inventory,
            paper_gateway=weighted_voting_local_gateway(broker, store),
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        enable_automatic_entries(supervisor)
        proposal = global_proposal_for_snapshot(evaluate_payload(offset_minutes=6)).model_copy(
            update={
                "orderIntentId": "runtime-local-restart-intent",
                "settingsSnapshot": {
                    "settings_version": "runtime-local-restart",
                    "localPaperQuote": {"bid": 100.0, "ask": 100.0, "timestamp": SESSION_OPEN.isoformat()},
                    "localPaperAvailableQuantity": 2,
                },
            }
        )
        application = apply_global_gate_response(
            proposal,
            GlobalGateResponse(
                action="ALLOW",
                maximumAllowedQuantity=3,
                maximumAdditionalRiskDollars=50.0,
                evaluatedAt=SESSION_OPEN,
                configurationHash="runtime-local-restart-global",
            ),
        )
        item = supervisor._enqueue_execution_from_result(
            {
                "decision": {"decision_id": proposal.decisionId},
                "gateResult": {"permission_granted": True, "mode": "automatic", "reason_codes": ("weighted_voting.test.local_restart",)},
                "globalOrderProposal": proposal.model_dump(mode="json"),
                "globalGateApplication": application.model_dump(mode="json"),
            },
            idempotency_key="weighted_voting.test.local_restart",
            evaluated_at=SESSION_OPEN,
            inventory_snapshot_version=inventory.current_snapshot(now=SESSION_OPEN).snapshot_version,
        )
        self.assertIsNotNone(item)
        supervisor.process_execution_queue_item(item)
        before_restart = inventory.current_snapshot(now=SESSION_OPEN)
        self.assertEqual(len(before_restart.open_positions), 1)
        self.assertEqual(before_restart.open_positions[0].quantity, 2)
        self.assertEqual(before_restart.partially_filled_orders[0].remaining_quantity, 1)
        self.assertTrue(any(key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.fills.") for key in store.snapshots))
        self.assertTrue(any(key.startswith(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.protective_orders.") for key in store.snapshots))
        del store.snapshots["weighted_voting.inventory.snapshot.current"]

        recovered_inventory = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0)
        no_position_query_broker = NoPositionQueryLocalPaperBroker(store, recovered_inventory)
        recovered = WeightedVotingRuntimeSupervisor(
            service=WeightedVotingService(store=store),
            store=store,
            inventory_repository=recovered_inventory,
            paper_gateway=weighted_voting_local_gateway(no_position_query_broker, store),
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
        )
        recovered.recover_from_checkpoints()
        recovery = store.read_snapshot(f"{WEIGHTED_VOTING_LOCAL_PAPER_NAMESPACE}.recovery.latest")
        recovered.reconcile_broker_inventory(startup=True, trigger="restart_test")
        rebuilt = recovered_inventory.current_snapshot(now=SESSION_OPEN)

        self.assertEqual(recovery["inventorySnapshotVersion"], before_restart.snapshot_version)
        self.assertGreaterEqual(rebuilt.snapshot_version, before_restart.snapshot_version)
        self.assertEqual(rebuilt.cash, before_restart.cash)
        self.assertEqual(rebuilt.reserved_cash, before_restart.reserved_cash)
        self.assertEqual(rebuilt.open_positions[0].average_entry_price, before_restart.open_positions[0].average_entry_price)
        self.assertEqual(rebuilt.partially_filled_orders[0].filled_quantity, 2)
        self.assertEqual(recovery["pendingOrderCount"], 1)
        self.assertEqual(recovery["partialFillCount"], 1)
        self.assertEqual(recovery["localFillCount"], 1)
        self.assertTrue(recovery["fillIds"][0])
        self.assertTrue(recovery["protectiveOrderMonitoringResumed"])
        self.assertEqual(no_position_query_broker.refresh_positions_calls, 0)
        self.assertIn("weighted_voting.local_paper.restart_recovery.no_alpaca_positions_queried", recovery["reasonCodes"])

        stop_payload = evaluate_payload(offset_minutes=7)
        stop_payload["bid"] = float(proposal.stopPrice) - 0.05
        stop_payload["ask"] = float(proposal.stopPrice)
        asyncio.run(recovered.process_finalised_bar_event(event_from_payload(stop_payload)))

        flattened = recovered_inventory.current_snapshot(now=SESSION_OPEN)
        self.assertEqual(flattened.open_positions, ())
        self.assertEqual(no_position_query_broker.refresh_positions_calls, 0)

    def test_out_of_order_events_are_rejected_without_replay_recovery(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=10))))

        out_of_order = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=0))))

        self.assertEqual(out_of_order["status"], "rejected_out_of_order")
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.decisions.")]), 1)
        self.assertEqual(supervisor.health()["outOfOrderEvents"], 1)

    def test_stale_queued_event_cannot_create_order_or_decision(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store, max_queue_lag_seconds=1)
        stale_event = event_from_payload(evaluate_payload(), published_at=SESSION_OPEN)

        record = asyncio.run(supervisor.process_finalised_bar_event(stale_event))

        self.assertEqual(record["status"], "stale_no_order")
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.decisions.")]), 0)
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.order_proposals.")]), 0)
        self.assertTrue(supervisor.health()["automaticOrderCreationPaused"])

    def test_incomplete_one_minute_bar_event_cannot_create_decision(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        payload = evaluate_payload()
        payload["candles"][-1]["finalized"] = False
        event = WeightedVotingFinalisedBarEvent(
            algorithm_id="weighted_voting",
            symbol="SPY",
            finalised_candle_timestamp=datetime.fromisoformat(payload["data_timestamp"]),
            data_manifest_hash="incomplete-candle-manifest",
            market_payload=payload,
            published_at=datetime.now(timezone.utc),
        )

        record = asyncio.run(supervisor.process_finalised_bar_event(event))

        self.assertEqual(record["status"], "runtime_exception_safe_degradation")
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.decisions.")]), 0)
        self.assertTrue(supervisor.health()["automaticOrderCreationPaused"])
        self.assertTrue(supervisor.health()["recoveryRequired"])
        self.assertIn("completed bars", record["error"])

    def test_bounded_queue_applies_backpressure(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store, queue_maxsize=1)

        first = asyncio.run(supervisor.publish_finalised_bar(event_from_payload(evaluate_payload())))
        second = asyncio.run(supervisor.publish_finalised_bar(event_from_payload(evaluate_payload(offset_minutes=1))))

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(supervisor.health()["rejectedEvents"], 1)

    def test_finalized_bar_producer_publishes_to_supervisor_without_api_or_ui(self) -> None:
        store = MemoryStore()
        supervisor = WeightedVotingRuntimeSupervisor(
            service=WeightedVotingService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=300, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
        )
        producer = WeightedVotingFinalizedBarProducer(
            market_data_client=FakeWeightedVotingMarketDataClient(candle_rows_for_ingestion(96)),
            candle_store=MemoryCandleStore(),
            publish_event=supervisor.publish_finalised_bar,
            config=WeightedVotingFinalizedBarProducerConfig(max_staleness_seconds=300, finalization_delay_seconds=2),
        )

        result = asyncio.run(producer.process_symbol("SPY", now=SESSION_OPEN + timedelta(minutes=97, seconds=3)))
        event = supervisor.event_bus.queue.get_nowait()

        self.assertTrue(result.accepted)
        self.assertEqual(event.algorithm_id, "weighted_voting")
        self.assertEqual(event.symbol, "SPY")
        self.assertTrue(event.finalized)
        self.assertEqual(event.bar_start, SESSION_OPEN + timedelta(minutes=95))
        self.assertEqual(event.bar_end, SESSION_OPEN + timedelta(minutes=96))
        self.assertEqual(event.source_sequence, 96)
        self.assertIn("data_manifest_hash", event.as_dict())
        self.assertTrue(any(key.startswith("weighted_voting.runtime.finalized_bar_events.accepted.") for key in store.snapshots))
        self.assertEqual(supervisor.health()["metrics"]["finalizedBarEventsPublished"], 1)

    def test_bar_event_worker_polls_producer_and_reaches_supervisor_queue(self) -> None:
        store = MemoryStore()
        supervisor = WeightedVotingRuntimeSupervisor(
            service=WeightedVotingService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, market_data_poll_seconds=0.01, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
        )
        event = event_from_payload(evaluate_payload(offset_minutes=30), published_at=SESSION_OPEN + timedelta(minutes=126))
        supervisor.finalized_bar_producer = OneShotProducer(supervisor, event)
        worker = WeightedVotingBarEventWorker(supervisor, "WeightedVotingBarEventWorker")

        asyncio.run(worker.run())

        queued = supervisor.event_bus.queue.get_nowait()
        self.assertEqual(queued.event_id, event.event_id)
        self.assertEqual(supervisor.metrics.last_finalized_bar_producer_result["status"], "PUBLISHED")

    def test_finalized_bar_producer_rejects_partial_stale_and_gap_candles(self) -> None:
        cases = (
            (
                "partial",
                candle_rows_for_ingestion(96, finalized=False),
                SESSION_OPEN + timedelta(minutes=97, seconds=3),
                "REJECTED_INVALID_CANDLES",
                "weighted_voting.market_data.invalid_candle_rejected",
            ),
            (
                "stale",
                candle_rows_for_ingestion(96),
                SESSION_OPEN + timedelta(minutes=110),
                "REJECTED_STALE",
                "weighted_voting.market_data.stale_finalized_candle_rejected",
            ),
            (
                "gap",
                candle_rows_for_ingestion(96, omit_index=50),
                SESSION_OPEN + timedelta(minutes=97, seconds=3),
                "SEQUENCE_GAP",
                "weighted_voting.market_data.sequence_gap_detected",
            ),
        )
        for _, rows, now, expected_status, expected_reason in cases:
            with self.subTest(status=expected_status):
                supervisor = supervisor_for(MemoryStore())
                producer = WeightedVotingFinalizedBarProducer(
                    market_data_client=FakeWeightedVotingMarketDataClient(rows),
                    candle_store=MemoryCandleStore(),
                    publish_event=supervisor.publish_finalised_bar,
                    config=WeightedVotingFinalizedBarProducerConfig(max_staleness_seconds=300 if expected_status != "REJECTED_STALE" else 75, finalization_delay_seconds=2),
                )

                result = asyncio.run(producer.process_symbol("SPY", now=now))

                self.assertFalse(result.accepted)
                self.assertEqual(result.status, expected_status)
                self.assertIn(expected_reason, result.reason_codes)
                self.assertTrue(supervisor.event_bus.queue.empty())

    def test_finalized_bar_ingestion_rejects_conflicting_revision_after_acceptance(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        first = event_from_payload(evaluate_payload(offset_minutes=40), published_at=SESSION_OPEN + timedelta(minutes=136))
        second_payload = evaluate_payload(offset_minutes=40)
        second_payload["candles"][-1]["close"] += 0.25
        second_payload["data_manifest_hash"] = "conflicting-revision"
        second = event_from_payload(second_payload, published_at=SESSION_OPEN + timedelta(minutes=136, seconds=1))

        accepted = asyncio.run(supervisor.publish_finalised_bar(first))
        rejected = asyncio.run(supervisor.publish_finalised_bar(second))

        self.assertTrue(accepted)
        self.assertFalse(rejected)
        self.assertEqual(supervisor.event_bus.depth(), 1)
        self.assertTrue(any(key.startswith("weighted_voting.runtime.finalized_bar_events.rejected_conflict.") for key in store.snapshots))

    def test_finalized_bar_payload_uses_only_finalized_point_in_time_history(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        producer = WeightedVotingFinalizedBarProducer(
            market_data_client=FakeWeightedVotingMarketDataClient(candle_rows_for_ingestion(96)),
            candle_store=MemoryCandleStore(),
            publish_event=supervisor.publish_finalised_bar,
            config=WeightedVotingFinalizedBarProducerConfig(max_staleness_seconds=300, finalization_delay_seconds=2),
        )

        asyncio.run(producer.process_symbol("SPY", now=SESSION_OPEN + timedelta(minutes=97, seconds=3)))
        event = supervisor.event_bus.queue.get_nowait()
        one_minute_timestamps = [datetime.fromisoformat(row["timestamp"]) for row in event.market_payload["candles"]]
        five_minute_timestamps = [datetime.fromisoformat(row["timestamp"]) for row in event.market_payload["five_minute_candles"]]
        fifteen_minute_timestamps = [datetime.fromisoformat(row["timestamp"]) for row in event.market_payload["fifteen_minute_candles"]]

        self.assertEqual(max(one_minute_timestamps), event.bar_start)
        self.assertTrue(all(timestamp <= event.bar_start for timestamp in five_minute_timestamps))
        self.assertTrue(all(timestamp <= event.bar_start for timestamp in fifteen_minute_timestamps))
        self.assertTrue(all(row["finalized"] for row in event.market_payload["candles"]))
        self.assertTrue(event.market_payload["five_minute_candles"])
        self.assertTrue(event.market_payload["fifteen_minute_candles"])

    def test_idempotency_keys_separate_market_event_decision_and_order_intent_identity(self) -> None:
        key = weighted_voting_bar_event_idempotency_key(
            symbol="SPY",
            finalised_candle_timestamp=SESSION_OPEN,
            data_manifest_hash="manifest",
            settings_version="settings",
            weight_version="weights",
        )
        changed_runtime_versions = weighted_voting_bar_event_idempotency_key(
            symbol="SPY",
            finalised_candle_timestamp=SESSION_OPEN,
            data_manifest_hash="manifest2",
            settings_version="settings-v2",
            weight_version="weights-v2",
        )
        decision_key = weighted_voting_decision_idempotency_key(
            market_event_id=key,
            settings_version="settings",
            weight_version="weights",
            inventory_version=7,
            decision_kernel_version="kernel-v1",
        )
        order_intent_key = weighted_voting_order_intent_idempotency_key(
            decision_id="decision-1",
            intent_revision=1,
        )

        self.assertTrue(key.startswith("weighted_voting.market_event."))
        self.assertEqual(key, weighted_voting_bar_event_idempotency_key(symbol="SPY", finalised_candle_timestamp=SESSION_OPEN, data_manifest_hash="manifest", settings_version="settings", weight_version="weights"))
        self.assertEqual(key, changed_runtime_versions)
        self.assertTrue(decision_key.startswith("weighted_voting.decision_idempotency."))
        self.assertNotEqual(
            decision_key,
            weighted_voting_decision_idempotency_key(
                market_event_id=key,
                settings_version="settings-v2",
                weight_version="weights",
                inventory_version=7,
                decision_kernel_version="kernel-v1",
            ),
        )
        self.assertTrue(order_intent_key.startswith("weighted_voting.order_intent_idempotency."))
        self.assertEqual(order_intent_key, weighted_voting_order_intent_idempotency_key(decision_id="decision-1", intent_revision=1))
        self.assertNotEqual(order_intent_key, weighted_voting_order_intent_idempotency_key(decision_id="decision-1", intent_revision=2))

    def test_accepted_finalised_bar_decision_can_reach_paper_gateway_through_execution_queue(self) -> None:
        store = MemoryStore()
        broker = FakePaperBroker()
        gateway = weighted_voting_local_gateway(broker, store)
        inventory = seeded_inventory(store)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=gateway,
            inventory_repository=inventory,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        enable_automatic_entries(supervisor)

        record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload())))
        item = supervisor.execution_queue.get_nowait()
        outbox_ready = store.read_snapshot(f"weighted_voting.runtime.execution_outbox.{item.command.order_intent_id}")
        execution_record = supervisor.process_execution_queue_item(item)
        outbox_done = store.read_snapshot(f"weighted_voting.runtime.execution_outbox.{item.command.order_intent_id}")

        self.assertEqual(record["status"], "decision_persisted")
        self.assertEqual(outbox_ready["status"], "READY_TO_SUBMIT")
        self.assertEqual(outbox_ready["executionQueueItem"]["command"]["clientOrderId"], item.command.client_order_id)
        self.assertEqual(execution_record["status"], "submitted")
        self.assertEqual(outbox_done["status"], "FILLED")
        self.assertEqual(outbox_done["submissionAttemptCount"], 1)
        self.assertEqual([entry["status"] for entry in outbox_done["attemptRecords"]], ["SUBMITTING", "FILLED"])
        self.assertEqual(broker.submit_count, 1)
        self.assertEqual(supervisor.health()["submittedOrders"], 1)

    def test_risk_worker_persists_global_boundary_before_execution_outbox(self) -> None:
        store = MemoryStore()
        broker = FakePaperBroker()
        gateway = weighted_voting_local_gateway(broker, store)
        inventory = seeded_inventory(store)
        service = DecisionOnlyExecutionService(store=store, central_risk_service=ApproveExternalRiskService())
        supervisor = WeightedVotingRuntimeSupervisor(
            service=service,
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=gateway,
            inventory_repository=inventory,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        enable_automatic_entries(supervisor)

        async def scenario() -> None:
            worker = WeightedVotingRiskWorker(supervisor, "WeightedVotingRiskWorker")
            task = asyncio.create_task(worker.run())
            supervisor.tasks["WeightedVotingRiskWorker"] = task
            try:
                await supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=12)))
                await asyncio.wait_for(supervisor.risk_queue.join(), timeout=1.0)
            finally:
                supervisor.stop_event.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())

        item = supervisor.execution_queue.get_nowait()
        risk_records = [value for key, value in store.snapshots.items() if key.startswith("weighted_voting.runtime.risk.decisions.")]
        outbox = store.read_snapshot(f"weighted_voting.runtime.execution_outbox.{item.command.order_intent_id}")
        intent = store.read_snapshot(f"weighted_voting.runtime.order_intents.{item.command.order_intent_id}")
        self.assertEqual(risk_records[0]["status"], "approved_for_execution")
        self.assertEqual(risk_records[0]["finalAllowedQuantity"], item.command.quantity)
        self.assertEqual(outbox["status"], "READY_TO_SUBMIT")
        self.assertEqual(intent["status"], "EXECUTION_OUTBOX_READY_TO_SUBMIT")
        self.assertEqual(service.central_risk_service.calls, 1)

    def test_execution_outbox_recovers_pending_intent_after_restart(self) -> None:
        store = MemoryStore()
        broker = NoFillPaperBroker()
        gateway = weighted_voting_local_gateway(broker, store)
        inventory = seeded_inventory(store)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=gateway,
            inventory_repository=inventory,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        enable_automatic_entries(supervisor)
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=14))))
        item = supervisor.execution_queue.get_nowait()
        self.assertEqual(store.read_snapshot(f"weighted_voting.runtime.execution_outbox.{item.command.order_intent_id}")["status"], "READY_TO_SUBMIT")

        recovered = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=gateway,
            inventory_repository=inventory,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        recovery_record = recovered.recover_pending_execution_outbox()
        recovered_item = recovered.execution_queue.get_nowait()

        self.assertEqual(recovery_record["recoveredToQueue"], 1)
        self.assertEqual(recovered_item.command.client_order_id, item.command.client_order_id)
        self.assertEqual(broker.submit_count, 0)

    def test_retry_recovery_queries_broker_before_resubmitting_order(self) -> None:
        store = MemoryStore()
        broker = NoFillPaperBroker()
        gateway = weighted_voting_local_gateway(broker, store)
        inventory = seeded_inventory(store)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=gateway,
            inventory_repository=inventory,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        enable_automatic_entries(supervisor)
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=15))))
        item = supervisor.execution_queue.get_nowait()
        retry_outbox = dict(store.read_snapshot(f"weighted_voting.runtime.execution_outbox.{item.command.order_intent_id}"))
        retry_outbox["status"] = "SUBMITTING"
        retry_outbox["submissionAttemptCount"] = 1
        store.write_snapshot(f"weighted_voting.runtime.execution_outbox.{item.command.order_intent_id}", retry_outbox)
        lookup_broker = ExistingOrderPaperBroker(order_intent_id=item.command.order_intent_id)
        recovered = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=weighted_voting_local_gateway(lookup_broker, store),
            inventory_repository=inventory,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        recovery_record = recovered.recover_pending_execution_outbox()
        outbox = store.read_snapshot(f"weighted_voting.runtime.execution_outbox.{item.command.order_intent_id}")

        self.assertEqual(recovery_record["reconciledFromBroker"], 1)
        self.assertEqual(outbox["status"], "FILLED")
        self.assertEqual(lookup_broker.lookup_count, 1)
        self.assertEqual(lookup_broker.submit_count, 0)
        self.assertTrue(recovered.execution_queue.empty())

    def test_live_gateway_is_rejected_before_paper_submission(self) -> None:
        store = MemoryStore()
        broker = FakePaperBroker()
        inventory = seeded_inventory(store)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=weighted_voting_local_gateway(broker, store),
            inventory_repository=inventory,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        enable_automatic_entries(supervisor)
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=16))))
        item = supervisor.execution_queue.get_nowait()
        live_broker = LivePaperBroker()
        supervisor.paper_gateway = PaperOrderGateway(live_broker, store)

        execution_record = supervisor.process_execution_queue_item(item)
        outbox = store.read_snapshot(f"weighted_voting.runtime.execution_outbox.{item.command.order_intent_id}")

        self.assertEqual(execution_record["status"], "paper_endpoint_unverified")
        self.assertEqual(outbox["status"], "REJECTED")
        self.assertIn("weighted_voting.runtime.execution_outbox.live_gateway_rejected", outbox["reasonCodes"])
        self.assertEqual(live_broker.submit_count, 0)

    def test_submission_timeout_moves_outbox_to_reconciliation_required(self) -> None:
        store = MemoryStore()
        broker = TimeoutPaperBroker()
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=weighted_voting_local_gateway(broker, store),
            inventory_repository=seeded_inventory(store),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        enable_automatic_entries(supervisor)
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=17))))
        item = supervisor.execution_queue.get_nowait()

        execution_record = supervisor.process_execution_queue_item(item)
        outbox = store.read_snapshot(f"weighted_voting.runtime.execution_outbox.{item.command.order_intent_id}")

        self.assertEqual(execution_record["status"], "submission_failed_safe_degradation")
        self.assertEqual(outbox["status"], "RECONCILIATION_REQUIRED")
        self.assertEqual(outbox["submissionAttemptCount"], 1)
        self.assertEqual([entry["status"] for entry in outbox["attemptRecords"]], ["SUBMITTING", "RECONCILIATION_REQUIRED"])
        self.assertIn("weighted_voting.runtime.execution_outbox.submission_timeout_reconciliation_required", outbox["reasonCodes"])
        self.assertEqual(supervisor.health()["operationalStatus"]["lastReconciliation"]["trigger"], "submission_error")
        self.assertEqual(broker.submit_count, 1)

    def test_risk_worker_rejects_unavailable_or_increasing_global_risk_response(self) -> None:
        cases = (
            (None, "weighted_voting.global_risk.missing_response"),
            (IncreasingExternalRiskService(), "weighted_voting.global_risk.quantity_increase_rejected"),
            (MutableInventoryExternalRiskService(), "weighted_voting.global_risk.mutable_inventory_payload_rejected"),
        )
        for central_service, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                store = MemoryStore()
                service = DecisionOnlyExecutionService(store=store, central_risk_service=central_service)
                broker = FakePaperBroker()
                supervisor = WeightedVotingRuntimeSupervisor(
                    service=service,
                    store=store,
                    config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
                    event_bus=WeightedVotingEventBus(maxsize=8),
                    paper_gateway=weighted_voting_local_gateway(broker, store),
                    inventory_repository=seeded_inventory(store),
                    rollout_flags=validated_rollout_flags(),
                    rollout_validation=validated_rollout_validation(),
                )
                enable_automatic_entries(supervisor)

                asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=13))))

                risk_records = [value for key, value in store.snapshots.items() if key.startswith("weighted_voting.runtime.risk.decisions.")]
                self.assertEqual(supervisor.execution_queue.qsize(), 0)
                self.assertEqual(risk_records[0]["status"], "rejected_by_global_risk")
                self.assertIn(expected_reason, risk_records[0]["reasonCodes"])

    def test_repeated_market_event_after_settings_change_is_noop_and_does_not_resubmit(self) -> None:
        store = MemoryStore()
        broker = FakePaperBroker()
        gateway = weighted_voting_local_gateway(broker, store)
        inventory = seeded_inventory(store)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=gateway,
            inventory_repository=inventory,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        enable_automatic_entries(supervisor)
        event = event_from_payload(evaluate_payload())

        first_record = asyncio.run(supervisor.process_finalised_bar_event(event))
        first_item = supervisor.execution_queue.get_nowait()
        first_execution = supervisor.process_execution_queue_item(first_item)
        changed_settings = resolve_effective_settings(
            dynamic_values={"maximum_shares": 5},
            baseline_config=WeightedVotingConfig(),
            timestamp=SESSION_OPEN + timedelta(minutes=1),
            source_evidence=("weighted_voting.test.changed_after_market_event",),
        )
        persist_effective_settings(store, changed_settings)
        duplicate_record = asyncio.run(supervisor.process_finalised_bar_event(event))

        self.assertEqual(first_record["status"], "decision_persisted")
        self.assertEqual(first_execution["status"], "submitted")
        self.assertEqual(duplicate_record["status"], "duplicate_noop")
        self.assertEqual(broker.submit_count, 1)
        self.assertTrue(supervisor.execution_queue.empty())
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.runtime.decision_idempotency.")]), 1)
        self.assertTrue(any(key.startswith("weighted_voting.runtime.events.duplicate.") for key in store.snapshots))

    def test_unreconciled_inventory_blocks_new_entries_before_execution_queue(self) -> None:
        store = MemoryStore()
        gateway = PaperOrderGateway(UnreconciledPaperBroker(), store)
        inventory = seeded_inventory(store)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=gateway,
            inventory_repository=inventory,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        supervisor.reconcile_broker_inventory(startup=True)
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload())))

        self.assertTrue(supervisor.health()["entryCreationPausedForReconciliation"])
        self.assertTrue(supervisor.execution_queue.empty())
        self.assertTrue(any("reconciliation_blocks_new_entries" in str(value) for value in store.snapshots.values()))

    def test_health_exposes_and_persists_complete_runtime_observability(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload())))
        supervisor.metrics.last_global_risk_response = {"action": "APPROVE", "apiKey": "SHOULD_NOT_LEAK"}
        supervisor.metrics.last_order_submission = {"status": "ACKNOWLEDGED", "authorizationToken": "NOPE"}
        supervisor.metrics.last_fill = {"clientOrderId": "wv-fill", "filledQuantity": 1, "account_key": "HIDDEN"}

        health = supervisor.health()

        operational = health["operationalStatus"]
        metrics = health["metrics"]
        observability = store.snapshots["weighted_voting.observability.runtime.latest"]
        serialized = json.dumps({"health": health, "observability": observability}, sort_keys=True)
        self.assertNotIn("SHOULD_NOT_LEAK", serialized)
        self.assertNotIn("NOPE", serialized)
        self.assertNotIn("HIDDEN", serialized)
        self.assertIn("[REDACTED]", serialized)
        self.assertEqual(observability["algorithmId"], "weighted_voting")
        self.assertEqual(observability["observabilityVersion"], "weighted_voting_runtime_observability_v1")
        self.assertTrue(any(key.startswith("weighted_voting.observability.runtime.") for key in store.snapshots))
        self.assertIn("supervisorState", operational)
        self.assertIn("paperToggleState", operational)
        self.assertIn("readinessState", operational)
        self.assertIn("paperBrokerConnectivity", operational)
        self.assertIn("accountModeVerification", operational)
        self.assertIn("workerState", operational)
        self.assertIn("lastFinalisedBarReceived", operational)
        self.assertIn("lastBarProcessed", operational)
        self.assertIn("processingLagSeconds", operational)
        self.assertIn("queueLagSeconds", operational)
        self.assertIn("lastDecision", operational)
        self.assertIn("lastLocalGateResult", operational)
        self.assertIn("lastGlobalRiskResponse", operational)
        self.assertIn("lastIntent", operational)
        self.assertIn("lastSubmission", operational)
        self.assertIn("lastAcknowledgement", operational)
        self.assertIn("lastFill", operational)
        self.assertIn("openPositions", operational)
        self.assertIn("currentPosition", operational)
        self.assertIn("pendingOrders", operational)
        self.assertIn("protectiveOrderHealth", operational)
        self.assertIn("inventoryVersion", operational)
        self.assertIn("lastReconciliation", operational)
        self.assertIn("dailyTradeCount", operational)
        self.assertIn("dailyPnL", operational)
        self.assertIn("dailyLoss", operational)
        self.assertIn("remainingDailyRisk", operational)
        self.assertIn("settingsVersion", operational)
        self.assertIn("weightVersion", operational)
        self.assertIn("circuitBreakerOpen", operational)
        self.assertIn("automaticSubmissionRolloutState", operational)
        self.assertIn("decisionLatencyMs", metrics)
        self.assertIn("riskServiceLatencyMs", metrics)
        self.assertIn("brokerLatencyMs", metrics)
        self.assertIn("decisionRiskBrokerLatencyMs", metrics)
        self.assertIn("eventBacklog", metrics)
        self.assertIn("gateRejectionCounts", metrics)
        self.assertIn("strategyOpportunityCounts", metrics)
        self.assertIn("proposedVsAllowedQuantity", metrics)
        self.assertIn("reconciliationDiscrepancies", metrics)

    def test_health_records_explicit_no_trade_reason_codes(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)

        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload())))
        last_decision = supervisor.health()["operationalStatus"]["lastDecision"]

        self.assertIn("noTrade", last_decision)
        if last_decision["noTrade"]:
            self.assertTrue(last_decision["noTradeReasonCodes"])
            self.assertTrue(all(str(code).startswith("weighted_voting.") for code in last_decision["noTradeReasonCodes"]))

    def test_pause_new_entries_keeps_position_protection_active_and_audits(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)

        audit = supervisor.pause_new_entries(actor="ops-user", reason="weighted_voting.test.pause_entries")
        health = supervisor.health()

        self.assertEqual(audit["actor"], "ops-user")
        self.assertEqual(audit["action"], "pause_new_entries")
        self.assertFalse(health["paused"])
        self.assertTrue(health["automaticOrderCreationPaused"])
        self.assertTrue(health["riskReducingExitsAllowed"])
        self.assertEqual(health["operationalStatus"]["pauseReason"], "weighted_voting.test.pause_entries")
        self.assertTrue(any(key.startswith("weighted_voting.runtime.admin_audit.") for key in store.snapshots))

    def test_runtime_control_persists_on_state_and_does_not_queue_pre_toggle_decisions(self) -> None:
        store = MemoryStore()
        broker = FakePaperBroker()
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=weighted_voting_local_gateway(broker, store),
            inventory_repository=seeded_inventory(store),
            account_port=WeightedVotingStaticAccountPort(account_equity=100000.0, broker_buying_power=75000.0, source_id="weighted_voting.test.paper_account"),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        supervisor.metrics.supervisor_started = True

        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=7))))
        control = supervisor.update_runtime_control(
            paper_trading_enabled=True,
            automatic_entries_enabled=True,
            updated_by="ops-user",
            reason="weighted_voting.test.control_on",
        )
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=8))))

        persisted = store.read_snapshot("weighted_voting.runtime.control")
        item = supervisor.execution_queue.get_nowait()

        self.assertEqual(control["algorithm_id"], "weighted_voting")
        self.assertTrue(persisted["paper_trading_enabled"])
        self.assertTrue(persisted["automatic_entries_enabled"])
        self.assertEqual(persisted["mode"], "PAPER")
        self.assertEqual(persisted["updated_by"], "ops-user")
        self.assertEqual(item.command.decision_id, "runtime-auto-decision")
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.execution_gateway.queue.")]), 1)
        self.assertTrue(any(key.startswith("weighted_voting.runtime.control_audit.") for key in store.snapshots))

    def test_auto_paper_readiness_is_authoritative_typed_and_fail_closed(self) -> None:
        store = MemoryStore()
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=weighted_voting_local_gateway(FakePaperBroker(), store),
            inventory_repository=seeded_inventory(store),
            account_port=WeightedVotingStaticAccountPort(account_equity=100000.0, broker_buying_power=75000.0, source_id="weighted_voting.test.account_port"),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        cold = supervisor.runtime_control()["readiness"]
        enable_automatic_entries(supervisor)
        hot = supervisor.runtime_control()["readiness"]

        self.assertFalse(cold["ready"])
        self.assertFalse(cold["entry_submission_allowed"])
        self.assertEqual(cold["runtime_status"], "OFF")
        self.assertIn("runtime_supervisor_healthy", cold["dependency_health"])
        self.assertIn("weighted_voting.runtime.control.paper_trading_disabled", cold["blocking_reason_codes"])
        self.assertTrue(hot["ready"])
        self.assertTrue(hot["entry_submission_allowed"])
        self.assertEqual(hot["runtime_status"], "PAPER_READY")
        self.assertEqual(
            {
                "weighted_voting_enabled",
                "paper_trading_enabled",
                "automatic_entries_enabled",
                "broker_endpoint_is_paper",
                "paper_account_verified",
                "paper_account_snapshot_available",
                "paper_gateway_connected",
                "automatic_submission_rollout_passed",
                "runtime_supervisor_healthy",
                "finalized_bar_pipeline_healthy",
                "market_data_fresh",
                "exchange_session_open",
                "inside_entry_decision_window",
                "settings_loaded_and_valid",
                "active_weights_loaded_and_frozen",
                "algorithm_capital_allocation_positive",
                "inventory_loaded",
                "inventory_reconciled",
                "broker_orders_reconciled",
                "no_unprotected_position",
                "no_pending_recovery",
                "no_algorithm_halt",
                "no_global_halt",
                "daily_loss_limit_not_reached",
                "daily_trade_limit_not_reached",
                "remaining_algorithm_risk_positive",
            }.issubset(hot["dependency_health"]),
            True,
        )

    def test_local_daily_loss_blocks_new_entries_but_keeps_risk_reducing_exits_available(self) -> None:
        store, _broker, supervisor = activation_supervisor(inventory=loss_limit_inventory)
        prepare_runtime_dependencies(supervisor)
        supervisor.update_runtime_control(
            paper_trading_enabled=True,
            automatic_entries_enabled=True,
            updated_by="weighted_voting.test",
            reason="weighted_voting.test.local_daily_loss_requested_auto",
        )

        health = supervisor.health()
        readiness = supervisor.runtime_control()["readiness"]

        self.assertTrue(health["automaticOrderCreationPaused"])
        self.assertFalse(health["circuitBreakerOpen"])
        self.assertTrue(health["riskReducingExitsAllowed"])
        self.assertEqual(health["operationalStatus"]["pauseReason"], "weighted_voting.runtime.control.daily_loss_limit_reached")
        self.assertFalse(readiness["entry_submission_allowed"])
        self.assertTrue(readiness["risk_reducing_exits_allowed"])
        self.assertIn("weighted_voting.runtime.control.daily_loss_limit_reached", readiness["blocking_reason_codes"])
        self.assertTrue(any(key.startswith("weighted_voting.inventory.") for key in store.snapshots))
        self.assertFalse(any(key.startswith("voting_ensemble.") for key in store.snapshots))
        self.assertFalse(any(key.startswith("weighted_confidence_aggregation.") for key in store.snapshots))
        self.assertFalse(any(key.startswith("regime_based.") for key in store.snapshots))
        self.assertFalse(any(key.startswith("meta_model.") for key in store.snapshots))

    def test_entry_daily_loss_gate_does_not_block_risk_reducing_sell_close(self) -> None:
        store, _broker, supervisor = activation_supervisor(inventory=open_loss_limit_inventory)
        prepare_runtime_dependencies(supervisor)
        supervisor.update_runtime_control(
            paper_trading_enabled=True,
            automatic_entries_enabled=True,
            updated_by="weighted_voting.test",
            reason="weighted_voting.test.local_daily_loss_requested_auto",
        )
        health = supervisor.health()
        inventory = supervisor.inventory_repository.current_snapshot(now=SESSION_OPEN)
        proposal = GlobalOrderProposal(
            algorithmId="weighted_voting",
            capitalPartitionId="weighted_voting.paper.default",
            decisionId="runtime-risk-reducing-exit-decision",
            orderIntentId="runtime-risk-reducing-exit-intent",
            intent="risk_reducing",
            symbol="SPY",
            side="SELL",
            quantity=100,
            triggerPrice=90.0,
            limitPrice=90.0,
            stopPrice=None,
            targetPrice=None,
            plannedRiskDollars=0.0,
            settingsSnapshot={"settings_version": "runtime-test", "intentRevision": 1},
            entryFormula={"kind": "risk_reducing_exit"},
            stopFormula={},
            targetFormula={},
            strategyStateHash="runtime-risk-reducing-state",
            proposedAt=SESSION_OPEN + timedelta(minutes=95),
            sessionDate=SESSION_OPEN.date(),
            configurationHash="runtime-risk-reducing-config",
        )
        application = apply_global_gate_response(
            proposal,
            GlobalGateResponse(
                action="EXIT_ONLY",
                maximumAllowedQuantity=100,
                maximumAdditionalRiskDollars=0.0,
                rejectionReasons=("weighted_voting.test.entries_blocked_exit_only",),
                evaluatedAt=SESSION_OPEN + timedelta(minutes=95),
                configurationHash="runtime-risk-reducing-global",
            ),
        )

        item = supervisor._enqueue_execution_from_result(
            {
                "decision": {"decision_id": proposal.decisionId},
                "gateResult": {
                    "permission_granted": False,
                    "mode": "automatic",
                    "reason_codes": ("weighted_voting.gate.daily_loss_limit_exceeded",),
                    "explanation": "Synthetic entry gate failure.",
                },
                "globalOrderProposal": proposal.model_dump(mode="json"),
                "globalGateApplication": application.model_dump(mode="json"),
            },
            idempotency_key="weighted_voting.test.risk_reducing_exit_daily_loss",
            evaluated_at=SESSION_OPEN + timedelta(minutes=95),
            inventory_snapshot_version=inventory.snapshot_version,
        )

        self.assertTrue(health["automaticOrderCreationPaused"])
        self.assertTrue(health["riskReducingExitsAllowed"])
        self.assertIsNotNone(item)
        self.assertEqual(item.command.side, "SELL")
        self.assertEqual(item.proposal.intent, "risk_reducing")
        self.assertFalse(item.local_gate_passed)
        self.assertEqual(supervisor.execution_queue.qsize(), 1)
        self.assertFalse(any(key.startswith("weighted_voting.runtime.execution.blocked.runtime-risk-reducing-exit-intent") for key in store.snapshots))

    def test_local_risk_blocks_fail_closed_for_entries_but_allow_risk_reducing_exits(self) -> None:
        cases = (
            (
                "insufficient_local_buying_power",
                lambda: activation_supervisor(seed=seed_full_capital_reservation),
                "weighted_voting.runtime.control.remaining_algorithm_risk_exhausted",
            ),
            (
                "daily_loss_reached",
                lambda: activation_supervisor(inventory=loss_limit_inventory),
                "weighted_voting.runtime.control.daily_loss_limit_reached",
            ),
            (
                "max_trade_count_reached",
                lambda: activation_supervisor(inventory=trade_limit_inventory),
                "weighted_voting.runtime.control.daily_trade_limit_reached",
            ),
        )
        for name, build, expected_reason in cases:
            with self.subTest(name=name):
                store, broker, supervisor = build()
                prepare_runtime_dependencies(supervisor)
                control = supervisor.update_runtime_control(
                    paper_trading_enabled=True,
                    automatic_entries_enabled=True,
                    updated_by="weighted_voting.test",
                    reason=f"weighted_voting.test.{name}",
                )

                record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=70))))
                self.assertEqual(supervisor.execution_queue.qsize(), 0)
                exit_item = enqueue_runtime_risk_reducing_exit(supervisor, suffix=name)

                self.assertFalse(control["automatic_entries_enabled"])
                self.assertFalse(control["readiness"]["entrySubmissionAllowed"])
                self.assertTrue(supervisor.health()["riskReducingExitsAllowed"])
                self.assertIn(expected_reason, control["readiness"]["blockingReasonCodes"])
                self.assertEqual(record["status"], "decision_persisted")
                self.assertTrue(any(key.startswith("weighted_voting.runtime.executions.blocked.runtime-auto-intent") for key in store.snapshots))
                self.assertEqual(supervisor.execution_queue.qsize(), 1)
                self.assertIsNotNone(exit_item)
                self.assertEqual(exit_item.proposal.intent, "risk_reducing")
                self.assertEqual(exit_item.command.side, "SELL")
                self.assertEqual(getattr(broker, "submit_count", 0), 0)

    def test_required_activation_matrix_blocks_entry_orders(self) -> None:
        def with_requested_auto_entries(supervisor, _store, _broker):
            prepare_runtime_dependencies(supervisor)
            supervisor.update_runtime_control(
                paper_trading_enabled=True,
                automatic_entries_enabled=True,
                updated_by="activation-test",
                reason="weighted_voting.test.activation.request_auto",
            )

        def with_paper_on_auto_off(supervisor, _store, _broker):
            prepare_runtime_dependencies(supervisor)
            supervisor.update_runtime_control(
                paper_trading_enabled=True,
                automatic_entries_enabled=False,
                updated_by="activation-test",
                reason="weighted_voting.test.activation.auto_off",
            )

        def with_unreconciled_inventory(supervisor, _store, _broker):
            enable_automatic_entries(supervisor)
            supervisor.metrics.inventory_reconciled = False

        cases = (
            ("paper_toggle_off", lambda: activation_supervisor(), lambda s, st, b: None, evaluate_payload),
            ("automatic_entries_off", lambda: activation_supervisor(), with_paper_on_auto_off, evaluate_payload),
            ("shadow_mode_active", lambda: activation_supervisor(rollout_flags=replace(validated_rollout_flags(), shadow_mode=True)), with_requested_auto_entries, evaluate_payload),
            ("rollout_validation_fails", lambda: activation_supervisor(rollout_validation=WeightedVotingRolloutValidation()), with_requested_auto_entries, evaluate_payload),
            ("auto_submit_flag_false", lambda: activation_supervisor(rollout_flags=replace(validated_rollout_flags(), auto_submit_enabled=False)), with_requested_auto_entries, evaluate_payload),
            ("broker_endpoint_live", lambda: activation_supervisor(broker=LivePaperBroker()), with_requested_auto_entries, evaluate_payload),
            ("paper_account_unverified", lambda: activation_supervisor(broker=UnverifiedPaperBroker()), with_requested_auto_entries, evaluate_payload),
            ("market_closed", lambda: activation_supervisor(broker=ClosedClockPaperBroker()), with_requested_auto_entries, lambda: retime_payload(evaluate_payload(offset_minutes=51), SESSION_OPEN + timedelta(minutes=30))),
            ("exchange_holiday", lambda: activation_supervisor(), with_requested_auto_entries, lambda: retime_payload(evaluate_payload(offset_minutes=52), datetime(2026, 7, 3, 14, 30, tzinfo=timezone.utc))),
            ("entry_cutoff_passed", lambda: activation_supervisor(), with_requested_auto_entries, lambda: retime_payload(evaluate_payload(offset_minutes=53), datetime(2026, 7, 14, 19, 55, tzinfo=timezone.utc))),
            ("data_stale", lambda: activation_supervisor(), with_requested_auto_entries, stale_payload),
            ("five_minute_unavailable", lambda: activation_supervisor(service=WeightedVotingService), with_requested_auto_entries, short_payload_without_five_minute_confirmation),
            ("inventory_unreconciled", lambda: activation_supervisor(), with_unreconciled_inventory, evaluate_payload),
            ("capital_allocation_zero", lambda: activation_supervisor(inventory=zero_capital_inventory), with_requested_auto_entries, evaluate_payload),
            ("daily_loss_limit_reached", lambda: activation_supervisor(inventory=loss_limit_inventory), with_requested_auto_entries, evaluate_payload),
            ("daily_trade_limit_reached", lambda: activation_supervisor(inventory=trade_limit_inventory), with_requested_auto_entries, evaluate_payload),
            ("global_risk_rejects", lambda: activation_supervisor(service=DecisionOnlyExecutionService, central_risk_service=RejectExternalRiskService()), with_requested_auto_entries, evaluate_payload),
            ("current_position_exists", lambda: activation_supervisor(seed=seed_unprotected_position), with_requested_auto_entries, evaluate_payload),
            ("pending_entry_reserves_capital", lambda: activation_supervisor(seed=seed_full_capital_reservation), with_requested_auto_entries, evaluate_payload),
        )
        for name, build, setup, payload_factory in cases:
            with self.subTest(name=name):
                store, broker, supervisor = build()
                setup(supervisor, store, broker)
                try:
                    asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(payload_factory(), published_at=datetime.now(timezone.utc))))
                except Exception:
                    pass
                self.assertEqual(supervisor.execution_queue.qsize(), 0)
                self.assertEqual(getattr(broker, "submit_count", 0), 0)

        partial_store, partial_broker, partial_supervisor = activation_supervisor()
        enable_automatic_entries(partial_supervisor)
        with self.assertRaises(ValueError):
            WeightedVotingFinalisedBarEvent(
                algorithm_id="weighted_voting",
                symbol="SPY",
                finalised_candle_timestamp=SESSION_OPEN + timedelta(minutes=54 + 94),
                data_manifest_hash="partial-test",
                market_payload=evaluate_payload(offset_minutes=54),
                published_at=datetime.now(timezone.utc),
                finalized=False,
            )
        self.assertEqual(partial_supervisor.execution_queue.qsize(), 0)
        self.assertEqual(partial_broker.submit_count, 0)

        duplicate_store, duplicate_broker, duplicate_supervisor = activation_supervisor()
        enable_automatic_entries(duplicate_supervisor)
        duplicate_event = event_from_payload(evaluate_payload(offset_minutes=55))
        first = asyncio.run(duplicate_supervisor.process_finalised_bar_event(duplicate_event))
        second = asyncio.run(duplicate_supervisor.process_finalised_bar_event(duplicate_event))
        older = asyncio.run(duplicate_supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=54))))
        self.assertEqual(first["status"], "decision_persisted")
        self.assertEqual(second["status"], "duplicate_noop")
        self.assertEqual(older["status"], "rejected_out_of_order")
        self.assertEqual(duplicate_supervisor.execution_queue.qsize(), 1)
        duplicate_supervisor.execution_queue.get_nowait()
        self.assertEqual(duplicate_supervisor.execution_queue.qsize(), 0)
        self.assertEqual(duplicate_broker.submit_count, 0)

    def test_required_activation_happy_path_submits_exactly_one_paper_order(self) -> None:
        store, broker, supervisor = activation_supervisor()
        enable_automatic_entries(supervisor)

        record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=60))))
        item = supervisor.execution_queue.get_nowait()
        submitted = supervisor.process_execution_queue_item(item)

        self.assertEqual(record["status"], "decision_persisted")
        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(broker.submit_count, 1)
        self.assertTrue(supervisor.execution_queue.empty())
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.execution_gateway.automatic_result.")]), 1)

    def test_required_activation_persistence_unavailable_never_submits_order(self) -> None:
        store = FailingWriteStore()
        broker = FakePaperBroker()
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=weighted_voting_local_gateway(broker, store),
            inventory_repository=WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=61))))

        self.assertEqual(record["status"], "runtime_exception_safe_degradation")
        self.assertEqual(broker.submit_count, 0)
        self.assertTrue(supervisor.execution_queue.empty())

    def test_authoritative_calendar_rejects_holiday_even_when_payload_claims_open(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        payload = retime_payload(evaluate_payload(), datetime(2026, 7, 3, 14, 30, tzinfo=timezone.utc))
        payload["session_phase"] = "morning"
        payload["session_allowed"] = True

        record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(payload)))

        self.assertEqual(record["status"], "closed_session_skipped")
        self.assertIn("weighted_voting.runtime.session.exchange_calendar_closed", record["reason_codes"])
        self.assertEqual(supervisor.execution_queue.qsize(), 0)

    def test_entry_delay_after_open_blocks_automatic_entries(self) -> None:
        store = MemoryStore()
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=weighted_voting_local_gateway(FakePaperBroker(), store),
            inventory_repository=seeded_inventory(store),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        enable_automatic_entries(supervisor)
        payload = retime_payload(evaluate_payload(offset_minutes=18), SESSION_OPEN)

        record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(payload)))
        readiness = supervisor.runtime_control()["readiness"]

        self.assertEqual(record["status"], "decision_persisted")
        self.assertFalse(readiness["entrySubmissionAllowed"])
        self.assertIn("weighted_voting.runtime.outside_entry_decision_window", readiness["blockingReasonCodes"])
        self.assertEqual(supervisor.execution_queue.qsize(), 0)

    def test_calendar_handles_early_close_cutoff_and_flatten_time(self) -> None:
        calendar = WeightedVotingMarketCalendar()
        config = WeightedVotingConfig()
        before_cutoff = datetime(2026, 11, 27, 17, 40, tzinfo=timezone.utc)
        after_cutoff = datetime(2026, 11, 27, 17, 50, tzinfo=timezone.utc)
        flatten_due = datetime(2026, 11, 27, 17, 59, tzinfo=timezone.utc)

        self.assertTrue(calendar.session_clock(after_cutoff).early_close)
        self.assertTrue(calendar.inside_entry_decision_window(before_cutoff, config))
        self.assertFalse(calendar.inside_entry_decision_window(after_cutoff, config))
        self.assertTrue(calendar.should_cancel_entries(after_cutoff, config))
        self.assertTrue(calendar.should_flatten(flatten_due, config))

    def test_calendar_required_sessions_weekend_holiday_and_dst_transition(self) -> None:
        calendar = WeightedVotingMarketCalendar()
        config = WeightedVotingConfig()
        normal = datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc)
        weekend = datetime(2026, 7, 18, 15, 0, tzinfo=timezone.utc)
        holiday = datetime(2026, 7, 3, 15, 0, tzinfo=timezone.utc)
        before_open_delay = datetime(2026, 7, 14, 13, 34, tzinfo=timezone.utc)
        dst_after_open_delay = datetime(2026, 3, 9, 14, 40, tzinfo=timezone.utc)

        self.assertTrue(calendar.session_clock(normal).regular_session)
        self.assertTrue(calendar.inside_entry_decision_window(normal, config))
        self.assertFalse(calendar.session_clock(weekend).regular_session)
        self.assertFalse(calendar.inside_entry_decision_window(weekend, config))
        self.assertFalse(calendar.session_clock(holiday).regular_session)
        self.assertFalse(calendar.inside_entry_decision_window(holiday, config))
        self.assertFalse(calendar.inside_entry_decision_window(before_open_delay, config))
        self.assertTrue(calendar.session_clock(dst_after_open_delay).regular_session)
        self.assertTrue(calendar.inside_entry_decision_window(dst_after_open_delay, config))

    def test_broker_market_clock_veto_blocks_entries_on_calendar_open_session(self) -> None:
        store = MemoryStore()
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=weighted_voting_local_gateway(ClosedClockPaperBroker(), store),
            inventory_repository=seeded_inventory(store),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        payload = retime_payload(evaluate_payload(offset_minutes=19), SESSION_OPEN + timedelta(minutes=30))

        record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(payload)))

        self.assertEqual(record["status"], "closed_session_skipped")
        self.assertIn("weighted_voting.runtime.session.broker_market_clock_closed_veto", record["reason_codes"])
        self.assertEqual(supervisor.execution_queue.qsize(), 0)

    def test_runtime_control_off_blocks_entries_cancels_working_orders_and_preserves_exits(self) -> None:
        store = MemoryStore()
        broker = NoFillPaperBroker()
        gateway = weighted_voting_local_gateway(broker, store)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=gateway,
            inventory_repository=seeded_inventory(store),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        enable_automatic_entries(supervisor)
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=9))))
        item = supervisor.execution_queue.get_nowait()
        submitted = supervisor.process_execution_queue_item(item)

        control = supervisor.update_runtime_control(
            paper_trading_enabled=False,
            automatic_entries_enabled=False,
            updated_by="ops-user",
            reason="weighted_voting.test.control_off",
        )

        self.assertEqual(submitted["status"], "submitted")
        self.assertFalse(control["paper_trading_enabled"])
        self.assertFalse(control["automatic_entries_enabled"])
        self.assertTrue(control["transition"]["riskReducingExitsEnabled"])
        self.assertTrue(control["transition"]["protectiveOrdersEnabled"])
        self.assertTrue(control["transition"]["reconciliationContinues"])
        self.assertFalse(any(key.startswith("paper_order_gateway.") for key in store.snapshots))
        self.assertTrue(supervisor.health()["automaticOrderCreationPaused"])
        self.assertTrue(supervisor.health()["riskReducingExitsAllowed"])

    def test_runtime_control_off_cancels_unsubmitted_entry_queue_items(self) -> None:
        store = MemoryStore()
        broker = FakePaperBroker()
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=weighted_voting_local_gateway(broker, store),
            inventory_repository=seeded_inventory(store),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        enable_automatic_entries(supervisor)
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=10))))
        item = supervisor.execution_queue.get_nowait()

        control = supervisor.update_runtime_control(
            paper_trading_enabled=False,
            automatic_entries_enabled=False,
            updated_by="ops-user",
            reason="weighted_voting.test.control_off_unsubmitted",
        )

        queue_payload = store.read_snapshot(f"weighted_voting.execution_gateway.queue.{item.command.client_order_id}")
        self.assertIn(item.command.client_order_id, control["transition"]["cancelledUnsubmittedEntryClientOrderIds"])
        self.assertEqual(queue_payload["status"], "CANCELLED")
        self.assertEqual(broker.submit_count, 0)
        self.assertTrue(control["transition"]["riskReducingExitsEnabled"])

    def test_end_of_day_cancels_only_weighted_voting_entries_and_flattens_owned_inventory(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        snapshot = inventory.current_snapshot(now=SESSION_OPEN)
        inventory.append_event(
            event_id="runtime-eod-owned-fill",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(position_id="runtime-eod-owned-position", quantity=3, average_entry_price=100.0),
            occurred_at=SESSION_OPEN + timedelta(seconds=1),
            expected_snapshot_version=snapshot.snapshot_version,
        )
        weighted_key = "weighted_voting.execution_gateway.queue.weighted-entry-client"
        foreign_key = "weighted_voting.execution_gateway.queue.foreign-entry-client"
        store.write_snapshot(
            weighted_key,
            {
                "algorithmId": "weighted_voting",
                "status": "PENDING",
                "command": {
                    "algorithmId": "weighted_voting",
                    "clientOrderId": "weighted-entry-client",
                    "orderIntentId": "weighted-entry-intent",
                    "decisionId": "weighted-entry-decision",
                },
                "proposal": {"algorithmId": "weighted_voting", "intent": "new_entry"},
                "reasonCodes": ("weighted_voting.test.pending_entry",),
            },
        )
        store.write_snapshot(
            foreign_key,
            {
                "algorithmId": "voting_ensemble",
                "status": "PENDING",
                "command": {
                    "algorithmId": "voting_ensemble",
                    "clientOrderId": "foreign-entry-client",
                    "orderIntentId": "foreign-entry-intent",
                    "decisionId": "foreign-entry-decision",
                },
                "proposal": {"algorithmId": "voting_ensemble", "intent": "new_entry"},
                "reasonCodes": ("voting_ensemble.test.pending_entry",),
            },
        )
        supervisor = WeightedVotingRuntimeSupervisor(
            service=WeightedVotingService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            inventory_repository=inventory,
        )
        supervisor.metrics.last_bar_processed = {"close": 100.25, "ohlcv": {"close": 100.25}}

        record = supervisor.manage_positions_once(trigger="eod_test", managed_at=datetime(2026, 7, 14, 19, 59, tzinfo=timezone.utc))
        final_snapshot = inventory.current_snapshot(now=SESSION_OPEN)

        self.assertTrue(record["endOfDayExitDue"])
        self.assertEqual(record["closedTradeCount"], 1)
        self.assertEqual(final_snapshot.open_positions, ())
        self.assertEqual(store.read_snapshot(weighted_key)["status"], "CANCELLED")
        self.assertEqual(store.read_snapshot(foreign_key)["status"], "PENDING")
        self.assertEqual(record["entryCancellationTransition"]["cancelledUnsubmittedEntryClientOrderIds"], ["weighted-entry-client"])

    def test_runtime_control_repeated_requests_are_idempotent_and_stale_versions_conflict(self) -> None:
        store, _broker, supervisor = activation_supervisor()
        prepare_runtime_dependencies(supervisor)
        initial = supervisor.runtime_control()["record_version"]

        first = supervisor.update_runtime_control(
            paper_trading_enabled=True,
            automatic_entries_enabled=True,
            updated_by="ops-user",
            reason="weighted_voting.test.toggle_version_first",
            expected_version=initial,
        )
        repeated = supervisor.update_runtime_control(
            paper_trading_enabled=True,
            automatic_entries_enabled=True,
            updated_by="ops-user",
            reason="weighted_voting.test.toggle_version_repeated",
            expected_version=first["record_version"],
        )
        stale = supervisor.update_runtime_control(
            paper_trading_enabled=False,
            automatic_entries_enabled=False,
            updated_by="ops-user",
            reason="weighted_voting.test.toggle_version_stale",
            expected_version=initial,
        )
        persisted = store.read_snapshot("weighted_voting.runtime.control")

        self.assertTrue(first["paper_trading_enabled"])
        self.assertTrue(repeated["paper_trading_enabled"])
        self.assertEqual(repeated["paper_trading_enabled"], first["paper_trading_enabled"])
        self.assertEqual(stale["status"], "version_conflict")
        self.assertFalse(stale["mutationApplied"])
        self.assertTrue(persisted["paper_trading_enabled"])
        self.assertEqual(persisted["record_version"], repeated["record_version"])
        self.assertTrue(any(value.get("transition", {}).get("status") == "version_conflict" for value in store.snapshots.values() if isinstance(value, dict)))

    def test_automatic_entry_pause_blocks_execution_queue_but_keeps_shadow_decision(self) -> None:
        store = MemoryStore()
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
        )
        supervisor.pause_new_entries(actor="dashboard", reason="weighted_voting.test.global_paper_off")

        record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=20))))

        self.assertEqual(record["status"], "decision_persisted")
        self.assertEqual(supervisor.execution_queue.qsize(), 0)
        self.assertEqual(supervisor.health()["rejectedExecutionEvents"], 1)
        blocked = [value for key, value in store.snapshots.items() if key.startswith("weighted_voting.runtime.executions.blocked.")]
        self.assertEqual(blocked[0]["status"], "automatic_order_creation_paused")
        self.assertIn("weighted_voting.runtime.automatic_entries_paused", blocked[0]["reason_codes"])

    def test_all_admin_state_changes_capture_actor_prior_and_new_state(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)

        supervisor.pause(actor="ops-user", reason="weighted_voting.test.pause_runtime")
        supervisor.resume(actor="ops-user", reason="weighted_voting.test.resume_runtime")
        supervisor.resume_new_entries(actor="ops-user", reason="weighted_voting.test.resume_entries", validation_passed=True)
        supervisor.set_strategy_runtime_state("S3", "disabled", actor="ops-user", reason="weighted_voting.test.disable_strategy")
        supervisor.emergency_flatten(actor="ops-user", reason="weighted_voting.test.emergency_flatten")
        audits = [value for key, value in store.snapshots.items() if key.startswith("weighted_voting.runtime.admin_audit.")]

        self.assertGreaterEqual(len(audits), 5)
        self.assertTrue(all(item["actor"] == "ops-user" for item in audits))
        self.assertTrue(all("priorState" in item and "newState" in item and "recordedAt" in item for item in audits))
        self.assertEqual(store.read_snapshot("weighted_voting.runtime.strategy_controls.S3")["runtimeState"], "disabled")
        self.assertTrue(any(key.startswith("weighted_voting.runtime.emergency_flatten.") for key in store.snapshots))

    def test_force_reconciliation_control_makes_failure_visible_and_audited(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)

        audit = supervisor.force_reconciliation(actor="ops-user", reason="weighted_voting.test.force_reconciliation")
        health = supervisor.health()

        self.assertEqual(audit["action"], "force_reconciliation")
        self.assertTrue(health["entryCreationPausedForReconciliation"])
        self.assertTrue(health["automaticOrderCreationPaused"])
        self.assertEqual(health["operationalStatus"]["lastReconciliation"]["status"], "unavailable")
        self.assertTrue(any(key.startswith("weighted_voting.runtime.admin_audit.") for key in store.snapshots))

    def test_fault_injection_recovery_blocks_new_entries_for_crash_points_without_duplicates(self) -> None:
        cases = (
            ("decision_before_risk_response", seed_decision_without_risk, "decision_before_risk_response"),
            ("intent_before_global_risk_response", seed_intent_without_global_risk, "intent_before_global_risk_response"),
            ("risk_approval_before_broker_submission", seed_queued_order_without_submission, "risk_approval_before_broker_submission"),
            ("submission_before_local_acknowledgement", seed_submitted_lifecycle_without_ack, "submission_or_acknowledgement_incomplete"),
            ("acknowledgement_before_fill_persistence", seed_ack_lifecycle_without_fill, "submission_or_acknowledgement_incomplete"),
            ("restart_during_partial_fill", seed_partial_fill_lifecycle, "submission_or_acknowledgement_incomplete"),
            ("fill_before_inventory_update", seed_filled_result_without_reconciliation, "fill_before_inventory_update"),
            ("protective_orders_being_created", seed_unprotected_position, "protective_orders_being_created"),
        )
        for boundary, seed, expected_boundary in cases:
            with self.subTest(boundary=boundary):
                store = MemoryStore()
                item = seed(store)
                broker = FakePaperBroker()
                recovered = WeightedVotingRuntimeSupervisor(
                    service=AcceptedExecutionService(store=store),
                    store=store,
                    config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
                    event_bus=WeightedVotingEventBus(maxsize=8),
                    paper_gateway=weighted_voting_local_gateway(broker, store),
                    inventory_repository=WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0),
                    rollout_flags=validated_rollout_flags(),
                    rollout_validation=validated_rollout_validation(),
                )

                state = recovered.perform_recovery_safety_check(reason=f"weighted_voting.test.{boundary}")
                if item is not None:
                    execution_record = recovered.process_execution_queue_item(item)
                    self.assertEqual(execution_record["status"], "recovery_blocked")

                self.assertTrue(state["recoveryRequired"])
                self.assertTrue(recovered.health()["automaticOrderCreationPaused"])
                self.assertEqual(broker.submit_count, 0)
                self.assertTrue(any(item["boundary"] == expected_boundary for item in state["unresolvedBoundaries"]))

    def test_required_recovery_preserves_protected_positions_and_disconnect_does_not_duplicate_orders(self) -> None:
        protected_store = MemoryStore()
        seed_protected_position(protected_store)
        protected = WeightedVotingRuntimeSupervisor(
            service=WeightedVotingService(store=protected_store),
            store=protected_store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=weighted_voting_local_gateway(FakePaperBroker(), protected_store),
            inventory_repository=WeightedVotingInventoryRepository(protected_store, symbol="SPY", allocated_capital=25_000.0),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        protected.inventory_repository.recover_current_snapshot()

        protected_state = protected.perform_recovery_safety_check(reason="weighted_voting.test.protected_position_restart")

        self.assertFalse(any(item.get("boundary") == "protective_orders_being_created" for item in protected_state["unresolvedBoundaries"]))
        self.assertEqual(len(protected.inventory_repository.current_snapshot(now=SESSION_OPEN).open_positions), 1)

        disconnected_store = MemoryStore()
        item = seed_queued_order_without_submission(disconnected_store)
        disconnected = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=disconnected_store),
            store=disconnected_store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=None,
            inventory_repository=WeightedVotingInventoryRepository(disconnected_store, symbol="SPY", allocated_capital=25_000.0),
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        execution_record = disconnected.process_execution_queue_item(item)

        self.assertEqual(execution_record["status"], "gateway_unavailable")
        self.assertTrue(disconnected.health()["circuitBreakerOpen"])
        self.assertEqual(len([key for key in disconnected_store.snapshots if key.startswith("weighted_voting.execution_gateway.automatic_result.")]), 0)

    def test_fault_injection_degradation_boundaries_fail_closed(self) -> None:
        stale_market = evaluate_payload()
        stale_market["data_freshness_seconds"] = 999.0
        stale_quote = evaluate_payload(offset_minutes=1)
        stale_quote["quote_timestamp"] = (SESSION_OPEN - timedelta(minutes=15)).isoformat()
        future_payload = evaluate_payload(offset_minutes=2)
        future_payload["data_timestamp"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        future_payload["candles"][-1]["timestamp"] = future_payload["data_timestamp"]

        for boundary, payload in (
            ("stale_market_data_feed", stale_market),
            ("stale_quote_feed", stale_quote),
            ("clock_skew_future_bar", future_payload),
        ):
            with self.subTest(boundary=boundary):
                store = MemoryStore()
                supervisor = supervisor_for(store)
                record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(payload)))

                self.assertEqual(record["status"], "safe_degradation_no_order")
                self.assertTrue(supervisor.health()["automaticOrderCreationPaused"])
                self.assertTrue(any(boundary in code for code in record["reason_codes"]))

        store = MemoryStore()
        supervisor = WeightedVotingRuntimeSupervisor(service=GlobalRiskOutageService(store=store), store=store, config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0), event_bus=WeightedVotingEventBus(maxsize=8))
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=3))))
        self.assertTrue(supervisor.health()["automaticOrderCreationPaused"])
        self.assertTrue(supervisor.health()["recoveryRequired"])

        store = MemoryStore()
        item = seed_queued_order_without_submission(store)
        supervisor = supervisor_for(store)
        broker_record = supervisor.process_execution_queue_item(item)
        self.assertEqual(broker_record["status"], "gateway_unavailable")
        self.assertTrue(supervisor.health()["automaticOrderCreationPaused"])

        outage_store = FailingWriteStore()
        outage_supervisor = supervisor_for(outage_store)
        outage_record = asyncio.run(outage_supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=4))))
        self.assertEqual(outage_record["status"], "runtime_exception_safe_degradation")
        self.assertTrue(outage_supervisor.health()["recoveryRequired"])

    def test_corrupt_authoritative_snapshots_are_quarantined_and_last_approved_records_restored(self) -> None:
        store = MemoryStore()
        service = WeightedVotingService(store=store)
        approved_settings = service.get_config()["configuration"]
        approved_weights = service.weights_active()["weightState"]
        store.write_snapshot("weighted_voting.settings.last_approved", approved_settings)
        store.write_snapshot("weighted_voting.weights.last_approved", approved_weights)
        store.write_snapshot("weighted_voting.settings.effective", {"algorithm_id": "weighted_voting", "settings_version": ""})
        store.write_snapshot("weighted_voting.weights.active", {"algorithm_id": "weighted_voting", "strategy_weights": {"S2": 2.0}})
        store.write_snapshot("weighted_voting.inventory.snapshot.current", {"algorithm_id": "weighted_voting", "snapshot_version": "bad"})
        supervisor = supervisor_for(store)

        state = supervisor.perform_recovery_safety_check(reason="weighted_voting.test.corruption")

        self.assertTrue(state["recoveryRequired"])
        self.assertGreaterEqual(len(state["quarantinedSnapshots"]), 3)
        self.assertEqual(store.read_snapshot("weighted_voting.settings.effective"), approved_settings)
        self.assertEqual(store.read_snapshot("weighted_voting.weights.active"), approved_weights)
        self.assertTrue(any(key.startswith("weighted_voting.runtime.quarantine.") for key in store.snapshots))

    def test_inventory_conflict_event_backlog_and_duplicate_out_of_order_events_fail_closed(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store, queue_maxsize=1)
        asyncio.run(supervisor.publish_finalised_bar(event_from_payload(evaluate_payload(offset_minutes=5))))
        state = supervisor.perform_recovery_safety_check(reason="weighted_voting.test.event_backlog")
        self.assertTrue(any(item["boundary"] == "event_backlog" for item in state["unresolvedBoundaries"]))
        self.assertTrue(supervisor.health()["automaticOrderCreationPaused"])

        duplicate_store = MemoryStore()
        duplicate_supervisor = supervisor_for(duplicate_store)
        event = event_from_payload(evaluate_payload(offset_minutes=6))
        first = asyncio.run(duplicate_supervisor.process_finalised_bar_event(event))
        second = asyncio.run(duplicate_supervisor.process_finalised_bar_event(event))
        older = asyncio.run(duplicate_supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=5))))
        self.assertEqual(first["status"], "decision_persisted")
        self.assertEqual(second["status"], "duplicate_noop")
        self.assertEqual(older["status"], "rejected_out_of_order")
        self.assertEqual(len([key for key in duplicate_store.snapshots if key.startswith("weighted_voting.decisions.")]), 1)

    def test_circuit_breaker_requires_healthy_state_check_before_auto_submission_resumes(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        supervisor.metrics.inventory_reconciled = True
        supervisor.metrics.worker_failures["WeightedVotingDecisionWorker"] = 3
        supervisor.metrics.circuit_breaker_open = True
        supervisor.metrics.automatic_order_creation_paused = True

        rejected = supervisor.resume_new_entries(actor="ops-user", reason="weighted_voting.test.circuit_breaker", validation_passed=True)
        self.assertTrue(rejected["newState"]["automaticOrderCreationPaused"])
        self.assertTrue(supervisor.health()["circuitBreakerOpen"])

        supervisor.metrics.circuit_breaker_open = False
        supervisor.metrics.worker_failures.clear()
        supervisor.metrics.last_error = None
        supervisor.metrics.inventory_reconciled = True
        supervisor.metrics.entry_creation_paused_for_reconciliation = False
        accepted = supervisor.resume_new_entries(actor="ops-user", reason="weighted_voting.test.healthy_resume", validation_passed=True)
        self.assertFalse(accepted["newState"]["automaticOrderCreationPaused"])
        self.assertFalse(supervisor.health()["automaticOrderCreationPaused"])

    def test_circuit_breaker_trips_for_stale_market_data_and_requires_audited_healthy_resume(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        payload = evaluate_payload(offset_minutes=30)
        payload["data_freshness_seconds"] = 999.0

        record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(payload)))
        health = supervisor.health()

        self.assertEqual(record["status"], "safe_degradation_no_order")
        self.assertTrue(health["circuitBreakerOpen"])
        self.assertTrue(health["automaticOrderCreationPaused"])
        self.assertTrue(health["riskReducingExitsAllowed"])
        self.assertEqual(store.read_snapshot("weighted_voting.runtime.circuit_breaker.latest")["reasonCodes"], ["weighted_voting.runtime.circuit_breaker.market_data_stale"])
        rejected = supervisor.resume_new_entries(actor="ops-user", reason="weighted_voting.test.stale_resume", validation_passed=True)
        self.assertTrue(rejected["newState"]["automaticOrderCreationPaused"])

        supervisor.metrics.queue_lag_seconds = 0.0
        supervisor.metrics.last_finalised_bar_received = {**(supervisor.metrics.last_finalised_bar_received or {}), "dataFreshnessSeconds": 0.0}
        supervisor.metrics.last_error = None
        supervisor.metrics.inventory_reconciled = True
        accepted = supervisor.resume_new_entries(actor="ops-user", reason="weighted_voting.test.stale_resume_after_recovery", validation_passed=True)

        self.assertFalse(accepted["newState"]["automaticOrderCreationPaused"])
        self.assertFalse(supervisor.health()["circuitBreakerOpen"])
        self.assertEqual(store.read_snapshot("weighted_voting.runtime.circuit_breaker.latest")["status"], "CLOSED")
        self.assertTrue(any(value.get("action") == "circuit_breaker_closed" for value in store.snapshots.values() if isinstance(value, dict)))

    def test_circuit_breaker_trips_when_finalized_bar_gaps_exceed_tolerance(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        first = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=40))))
        gap = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=42))))

        self.assertEqual(first["status"], "decision_persisted")
        self.assertEqual(gap["status"], "rejected_source_sequence_gap")
        self.assertTrue(supervisor.health()["circuitBreakerOpen"])
        self.assertEqual(store.read_snapshot("weighted_voting.runtime.circuit_breaker.latest")["reasonCodes"], ["weighted_voting.runtime.circuit_breaker.finalized_bar_gap_tolerance_exceeded"])

    def test_broker_unavailable_breaker_blocks_entries_and_marks_exits_unavailable(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        supervisor.update_runtime_control(
            paper_trading_enabled=True,
            automatic_entries_enabled=False,
            updated_by="ops-user",
            reason="weighted_voting.test.paper_without_gateway",
        )

        health = supervisor.health()

        self.assertTrue(health["circuitBreakerOpen"])
        self.assertTrue(health["automaticOrderCreationPaused"])
        self.assertFalse(health["riskReducingExitsAllowed"])
        self.assertEqual(store.read_snapshot("weighted_voting.runtime.circuit_breaker.latest")["reasonCodes"], ["weighted_voting.runtime.circuit_breaker.broker_disconnected"])

    def test_reconciliation_unknown_broker_position_trips_breaker_but_keeps_exits_allowed(self) -> None:
        store = MemoryStore()
        broker = UnreconciledPaperBroker()
        supervisor = WeightedVotingRuntimeSupervisor(
            service=WeightedVotingService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=PaperOrderGateway(broker, store, execution_mode="BROKER_PAPER"),
            inventory_repository=seeded_inventory(store),
        )

        supervisor.reconcile_broker_inventory(startup=True)
        health = supervisor.health()

        self.assertTrue(health["circuitBreakerOpen"])
        self.assertTrue(health["riskReducingExitsAllowed"])
        self.assertEqual(store.read_snapshot("weighted_voting.runtime.circuit_breaker.latest")["reasonCodes"], ["weighted_voting.runtime.circuit_breaker.unknown_broker_position"])

    def test_daily_update_skips_intraday_and_keeps_active_weights_frozen(self) -> None:
        store = MemoryStore()
        service = WeightedVotingService(store=store)
        previous = service.active_weight_state().weight_version
        supervisor = supervisor_for(store)

        record = supervisor.run_daily_update_if_due(
            trigger="unit_test",
            now=SESSION_OPEN + timedelta(minutes=60),
        )

        self.assertEqual(record["status"], "skipped_intraday_weights_frozen")
        self.assertEqual(store.read_snapshot("weighted_voting.weights.active")["weight_version"], previous)
        self.assertFalse(any(key.startswith(CANDIDATE_WEIGHT_PREFIX) for key in store.snapshots))
        self.assertTrue(record["weightsFrozenDuringSession"])
        self.assertFalse(record["intradayWeightMutationAllowed"])

    def test_daily_update_worker_path_runs_after_session_from_persisted_finalized_bars(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=WeightedVotingService(store=store),
            store=store,
            inventory_repository=inventory,
            config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
        )
        payload = full_session_payload()
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(payload)))

        record = supervisor.run_daily_update_if_due(
            trigger="unit_test",
            now=datetime(2026, 7, 14, 21, 10, tzinfo=timezone.utc),
        )

        self.assertEqual(record["status"], "published")
        daily = record["details"]["dailyUpdate"]
        self.assertEqual(daily["active_weight_version"], daily["previous_weight_version"])
        self.assertEqual(daily["published_for_session_date"], "2026-07-15")
        self.assertTrue(any(key.startswith(CANDIDATE_WEIGHT_PREFIX) for key in store.snapshots))
        self.assertIn(f"{PUBLISHED_WEIGHT_PREFIX}2026-07-15", store.snapshots)
        self.assertEqual(record["details"]["lastReconciliation"]["trigger"], "daily_update")
        self.assertFalse(record["intradayWeightMutationAllowed"])

    def test_daily_update_uses_early_close_calendar_for_next_activation_session(self) -> None:
        store = MemoryStore()
        inventory = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0)
        session_date = datetime(2026, 11, 27, 14, 30, tzinfo=timezone.utc).date()
        inventory.initialize_session(
            session_date=session_date,
            allocated_capital=25_000.0,
            cash_available=25_000.0,
            occurred_at=datetime(2026, 11, 27, 14, 30, tzinfo=timezone.utc),
            expected_snapshot_version=0,
            event_id="runtime-early-close-session-start",
        )
        supervisor = WeightedVotingRuntimeSupervisor(
            service=WeightedVotingService(store=store),
            store=store,
            inventory_repository=inventory,
            config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
        )
        payload = full_session_payload(start=datetime(2026, 11, 27, 14, 30, tzinfo=timezone.utc), count=210)
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(payload)))

        record = supervisor.run_daily_update_if_due(
            trigger="unit_test",
            now=datetime(2026, 11, 27, 18, 10, tzinfo=timezone.utc),
        )

        self.assertEqual(record["status"], "published")
        self.assertEqual(record["details"]["dailyUpdate"]["published_for_session_date"], "2026-11-30")
        self.assertIn(f"{PUBLISHED_WEIGHT_PREFIX}2026-11-30", store.snapshots)


def supervisor_for(
    store: "MemoryStore",
    *,
    queue_maxsize: int = 8,
    max_queue_lag_seconds: int = 75,
    inventory_repository: WeightedVotingInventoryRepository | None = None,
) -> WeightedVotingRuntimeSupervisor:
    return WeightedVotingRuntimeSupervisor(
        service=WeightedVotingService(store=store),
        store=store,
        inventory_repository=inventory_repository,
        config=WeightedVotingRuntimeConfig(queue_maxsize=queue_maxsize, max_queue_lag_seconds=max_queue_lag_seconds, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
        event_bus=WeightedVotingEventBus(maxsize=queue_maxsize),
        paper_gateway=None,
    )


def activation_supervisor(
    *,
    broker=None,
    service=None,
    central_risk_service=None,
    inventory=None,
    rollout_flags: WeightedVotingRolloutFlags | None = None,
    rollout_validation: WeightedVotingRolloutValidation | None = None,
    seed=None,
):
    store = MemoryStore()
    active_broker = broker or FakePaperBroker()
    if seed is not None:
        seed(store)
        inventory_repository = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0)
        inventory_repository.recover_current_snapshot()
    else:
        inventory_builder = inventory or seeded_inventory
        inventory_repository = inventory_builder(store)
    service_class = service or AcceptedExecutionService
    service_instance = service_class(store=store, central_risk_service=central_risk_service)
    supervisor = WeightedVotingRuntimeSupervisor(
        service=service_instance,
        store=store,
        config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
        event_bus=WeightedVotingEventBus(maxsize=8),
        paper_gateway=weighted_voting_local_gateway(active_broker, store),
        inventory_repository=inventory_repository,
        account_port=WeightedVotingStaticAccountPort(account_equity=100000.0, broker_buying_power=75000.0, source_id="weighted_voting.test.paper_account"),
        rollout_flags=rollout_flags or validated_rollout_flags(),
        rollout_validation=rollout_validation or validated_rollout_validation(),
    )
    return store, active_broker, supervisor


def weighted_voting_local_gateway(broker, store, **kwargs) -> PaperOrderGateway:
    return PaperOrderGateway(broker, store, execution_mode="LOCAL_PAPER", **kwargs)


def prepare_runtime_dependencies(supervisor: WeightedVotingRuntimeSupervisor) -> None:
    supervisor.account_port = WeightedVotingStaticAccountPort(
        account_equity=100000.0,
        broker_buying_power=75000.0,
        source_id="weighted_voting.test.paper_account",
    )
    supervisor.metrics.supervisor_started = True
    supervisor.metrics.inventory_reconciled = True
    supervisor.metrics.processing_lag_seconds = 0.0
    supervisor.metrics.last_global_risk_response = {
        "action": "ALLOW",
        "maximumAllowedQuantity": 3,
        "maximumAdditionalRiskDollars": 50.0,
    }
    supervisor.metrics.last_finalised_bar_received = {
        "symbol": "SPY",
        "source": "weighted_voting.test",
        "finalisedCandleTimestamp": (SESSION_OPEN + timedelta(minutes=94)).isoformat(),
        "dataFreshnessSeconds": 0.0,
        "sessionPhase": "morning",
    }
    supervisor.metrics.last_bar_processed = dict(supervisor.metrics.last_finalised_bar_received)


def event_from_payload(payload: dict, *, published_at: datetime | None = None) -> WeightedVotingFinalisedBarEvent:
    snapshot = build_weighted_voting_market_snapshot(payload)
    return WeightedVotingFinalisedBarEvent(
        algorithm_id="weighted_voting",
        symbol=snapshot.symbol,
        finalised_candle_timestamp=snapshot.data_timestamp,
        data_manifest_hash=snapshot.data_manifest_hash,
        market_payload=payload,
        published_at=published_at or datetime.now(timezone.utc),
    )


def evaluate_payload(*, offset_minutes: int = 0) -> dict:
    rows = []
    start = SESSION_OPEN + timedelta(minutes=offset_minutes)
    for index in range(95):
        base = 100.0 + index * 0.03
        rows.append(
            {
                "timestamp": (start + timedelta(minutes=index)).isoformat(),
                "open": base,
                "high": base + 0.45,
                "low": base - 0.18,
                "close": base + 0.08,
                "volume": 200000 if index != 5 else 5000,
            }
        )
    return {
        "symbol": "SPY",
        "data_timestamp": rows[-1]["timestamp"],
        "candles": rows,
        "bid": rows[-1]["close"] - 0.01,
        "ask": rows[-1]["close"] + 0.01,
        "session_phase": "morning",
        "data_freshness_seconds": 0.0,
    }


def full_session_payload(*, start: datetime = SESSION_OPEN, count: int = 390) -> dict:
    rows = []
    for index in range(count):
        base = 100.0 + index * 0.03
        rows.append(
            {
                "timestamp": (start + timedelta(minutes=index)).isoformat(),
                "open": base,
                "high": base + 0.45,
                "low": base - 0.18,
                "close": base + 0.08,
                "volume": 200000 if index != 5 else 5000,
            }
        )
    return {
        "symbol": "SPY",
        "data_timestamp": rows[-1]["timestamp"],
        "candles": rows,
        "bid": rows[-1]["close"] - 0.01,
        "ask": rows[-1]["close"] + 0.01,
        "session_phase": "afternoon",
        "data_freshness_seconds": 0.0,
    }


def retime_payload(payload: dict, final_timestamp: datetime) -> dict:
    rows = [dict(row) for row in payload["candles"]]
    start = final_timestamp - timedelta(minutes=len(rows) - 1)
    for index, row in enumerate(rows):
        row["timestamp"] = (start + timedelta(minutes=index)).isoformat()
    updated = {**payload, "candles": rows, "data_timestamp": final_timestamp.isoformat()}
    return updated


def stale_payload() -> dict:
    payload = evaluate_payload(offset_minutes=56)
    payload["data_freshness_seconds"] = 999.0
    return payload


def short_payload_without_five_minute_confirmation() -> dict:
    payload = evaluate_payload(offset_minutes=57)
    rows = payload["candles"][:4]
    return {
        **payload,
        "candles": rows,
        "data_timestamp": rows[-1]["timestamp"],
    }


def global_proposal_for_snapshot(payload: dict) -> GlobalOrderProposal:
    snapshot = build_weighted_voting_market_snapshot(payload)
    return global_proposal_for_market_snapshot(snapshot)


def global_proposal_for_context(context) -> GlobalOrderProposal:
    return global_proposal_for_market_snapshot(context.finalised_one_minute_market_snapshot)


def global_proposal_for_market_snapshot(snapshot) -> GlobalOrderProposal:
    close = snapshot.one_minute_candles[-1].close
    return GlobalOrderProposal(
        algorithmId="weighted_voting",
        capitalPartitionId="weighted_voting.paper.default",
        decisionId="runtime-auto-decision",
        orderIntentId="runtime-auto-intent",
        intent="new_entry",
        symbol=snapshot.symbol,
        side="BUY",
        quantity=3,
        triggerPrice=close,
        limitPrice=close,
        stopPrice=close - 1.0,
        targetPrice=close + 2.0,
        plannedRiskDollars=50.0,
        settingsSnapshot={"settings_version": "runtime-test"},
        entryFormula={"kind": "limit"},
        stopFormula={"kind": "structural"},
        targetFormula={"kind": "r_multiple"},
        strategyStateHash="runtime-strategy-state",
        proposedAt=snapshot.data_timestamp,
        sessionDate=snapshot.data_timestamp.date(),
        configurationHash="runtime-auto-config",
    )


def enqueue_runtime_risk_reducing_exit(supervisor: WeightedVotingRuntimeSupervisor, *, suffix: str):
    inventory = supervisor.inventory_repository.current_snapshot(now=SESSION_OPEN + timedelta(minutes=95))
    proposal = GlobalOrderProposal(
        algorithmId="weighted_voting",
        capitalPartitionId="weighted_voting.paper.default",
        decisionId=f"runtime-risk-exit-{suffix}-decision",
        orderIntentId=f"runtime-risk-exit-{suffix}-intent",
        intent="risk_reducing",
        symbol="SPY",
        side="SELL",
        quantity=1,
        triggerPrice=100.0,
        limitPrice=100.0,
        stopPrice=None,
        targetPrice=None,
        plannedRiskDollars=0.0,
        settingsSnapshot={"settings_version": "runtime-test", "intentRevision": 1},
        entryFormula={"kind": "risk_reducing_exit"},
        stopFormula={},
        targetFormula={},
        strategyStateHash=f"runtime-risk-exit-{suffix}-state",
        proposedAt=SESSION_OPEN + timedelta(minutes=95),
        sessionDate=SESSION_OPEN.date(),
        configurationHash=f"runtime-risk-exit-{suffix}-config",
    )
    application = apply_global_gate_response(
        proposal,
        GlobalGateResponse(
            action="EXIT_ONLY",
            maximumAllowedQuantity=1,
            maximumAdditionalRiskDollars=0.0,
            rejectionReasons=(f"weighted_voting.test.{suffix}.entries_blocked_exit_only",),
            evaluatedAt=SESSION_OPEN + timedelta(minutes=95),
            configurationHash=f"runtime-risk-exit-{suffix}-global",
        ),
    )
    return supervisor._enqueue_execution_from_result(
        {
            "decision": {"decision_id": proposal.decisionId},
            "gateResult": {
                "permission_granted": False,
                "mode": "automatic",
                "reason_codes": (f"weighted_voting.test.{suffix}.entry_risk_blocked",),
                "explanation": "Synthetic entry gate failure with risk-reducing exit permitted.",
            },
            "globalOrderProposal": proposal.model_dump(mode="json"),
            "globalGateApplication": application.model_dump(mode="json"),
        },
        idempotency_key=f"weighted_voting.test.risk_reducing_exit.{suffix}",
        evaluated_at=SESSION_OPEN + timedelta(minutes=95),
        inventory_snapshot_version=inventory.snapshot_version,
    )


def validated_rollout_flags() -> WeightedVotingRolloutFlags:
    return WeightedVotingRolloutFlags(
        v2_enabled=True,
        shadow_mode=False,
        dynamic_reduction_enabled=True,
        dynamic_increase_enabled=False,
        auto_submit_enabled=True,
    )


def validated_rollout_validation() -> WeightedVotingRolloutValidation:
    return WeightedVotingRolloutValidation(
        backend_shadow_passed=True,
        shadow_comparison_passed=True,
        static_equal_weights_passed=True,
        performance_weights_validated=True,
        dynamic_reduction_validated=True,
        dynamic_entry_exit_validated=True,
        dynamic_increase_validated=True,
        manual_paper_submission_validated=True,
        tests_passed=True,
        paper_validations_passed=True,
        paper_broker_e2e_validated=True,
        reconciliation_validated=True,
        restart_recovery_validated=True,
        local_paper_broker_validated=True,
        local_inventory_reconciled=True,
        local_balance_accounting_validated=True,
        local_fill_simulation_validated=True,
        local_restart_recovery_validated=True,
        no_cross_algorithm_mutation_validated=True,
        no_alpaca_dependency_validated=True,
        risk_fail_closed_validated=True,
        protective_exits_validated=True,
        persisted_operator_approval=True,
        validation_record_id="weighted_voting.rollout.validation.runtime_test",
        source_authority="backend.weighted_voting.runtime_test_validation",
        approved_by="ops-user",
        recorded_at=SESSION_OPEN.isoformat(),
        live_trading_enabled=False,
    )


def enable_automatic_entries(supervisor: WeightedVotingRuntimeSupervisor) -> None:
    supervisor.account_port = WeightedVotingStaticAccountPort(
        account_equity=100000.0,
        broker_buying_power=75000.0,
        source_id="weighted_voting.test.paper_account",
    )
    supervisor.metrics.supervisor_started = True
    supervisor.metrics.inventory_reconciled = True
    supervisor.metrics.processing_lag_seconds = 0.0
    supervisor.metrics.last_global_risk_response = {
        "action": "ALLOW",
        "maximumAllowedQuantity": 3,
        "maximumAdditionalRiskDollars": 50.0,
    }
    supervisor.metrics.last_finalised_bar_received = {
        "symbol": "SPY",
        "source": "weighted_voting.test",
        "finalisedCandleTimestamp": (SESSION_OPEN + timedelta(minutes=94)).isoformat(),
        "dataFreshnessSeconds": 0.0,
        "sessionPhase": "morning",
    }
    supervisor.metrics.last_bar_processed = {
        "symbol": "SPY",
        "source": "weighted_voting.test",
        "finalisedCandleTimestamp": (SESSION_OPEN + timedelta(minutes=94)).isoformat(),
        "dataFreshnessSeconds": 0.0,
        "sessionPhase": "morning",
    }
    control = supervisor.update_runtime_control(
        paper_trading_enabled=True,
        automatic_entries_enabled=True,
        updated_by="weighted_voting.test",
        reason="weighted_voting.test.enable_automatic_entries",
    )
    assert control["automatic_entries_enabled"] is True
    assert not supervisor.health()["automaticOrderCreationPaused"]


def seeded_inventory(store: "MemoryStore") -> WeightedVotingInventoryRepository:
    inventory = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0)
    inventory.initialize_session(
        session_date=SESSION_OPEN.date(),
        allocated_capital=25_000.0,
        cash_available=25_000.0,
        occurred_at=SESSION_OPEN,
        expected_snapshot_version=0,
        event_id="runtime-session-start",
    )
    return inventory


def zero_capital_inventory(store: "MemoryStore") -> WeightedVotingInventoryRepository:
    inventory = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=0.0)
    inventory.initialize_session(
        session_date=SESSION_OPEN.date(),
        allocated_capital=0.0,
        cash_available=0.0,
        occurred_at=SESSION_OPEN,
        expected_snapshot_version=0,
        event_id="runtime-zero-capital-session-start",
    )
    return inventory


def position_payload(*, position_id: str, quantity: int, average_entry_price: float) -> dict:
    return {
        "algorithm_id": "weighted_voting",
        "position_id": position_id,
        "symbol": "SPY",
        "side": "LONG",
        "quantity": quantity,
        "average_entry_price": average_entry_price,
        "opened_at": SESSION_OPEN.isoformat(),
        "decision_id": f"{position_id}-decision",
        "order_intent_id": f"{position_id}-intent",
        "client_order_id": f"{position_id}-client",
        "source": "weighted_voting.test.position_payload",
    }


def loss_limit_inventory(store: "MemoryStore") -> WeightedVotingInventoryRepository:
    inventory = seeded_inventory(store)
    snapshot = inventory.current_snapshot(now=SESSION_OPEN)
    snapshot = inventory.append_event(
        event_id="runtime-loss-limit-fill",
        event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
        payload=position_payload(position_id="runtime-loss-limit", quantity=100, average_entry_price=100.0),
        occurred_at=SESSION_OPEN + timedelta(seconds=1),
        expected_snapshot_version=snapshot.snapshot_version,
    )
    inventory.append_event(
        event_id="runtime-loss-limit-close",
        event_type=WeightedVotingInventoryEventType.POSITION_CLOSED,
        payload={"algorithm_id": "weighted_voting", "position_id": "runtime-loss-limit", "exit_price": 90.0},
        occurred_at=SESSION_OPEN + timedelta(seconds=2),
        expected_snapshot_version=snapshot.snapshot_version,
    )
    return inventory


def open_loss_limit_inventory(store: "MemoryStore") -> WeightedVotingInventoryRepository:
    inventory = seeded_inventory(store)
    snapshot = inventory.current_snapshot(now=SESSION_OPEN)
    snapshot = inventory.append_event(
        event_id="runtime-open-loss-limit-fill",
        event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
        payload=position_payload(position_id="runtime-open-loss-limit", quantity=100, average_entry_price=100.0),
        occurred_at=SESSION_OPEN + timedelta(seconds=1),
        expected_snapshot_version=snapshot.snapshot_version,
    )
    inventory.mark_to_market(
        symbol="SPY",
        price=90.0,
        occurred_at=SESSION_OPEN + timedelta(seconds=2),
        market_event_id="runtime-open-loss-limit-mark",
        expected_snapshot_version=snapshot.snapshot_version,
    )
    return inventory


def trade_limit_inventory(store: "MemoryStore") -> WeightedVotingInventoryRepository:
    inventory = seeded_inventory(store)
    snapshot = inventory.current_snapshot(now=SESSION_OPEN)
    for index in range(10):
        position_id = f"runtime-trade-limit-{index}"
        snapshot = inventory.append_event(
            event_id=f"{position_id}-fill",
            event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
            payload=position_payload(position_id=position_id, quantity=1, average_entry_price=100.0),
            occurred_at=SESSION_OPEN + timedelta(seconds=index * 2 + 1),
            expected_snapshot_version=snapshot.snapshot_version,
        )
        snapshot = inventory.append_event(
            event_id=f"{position_id}-close",
            event_type=WeightedVotingInventoryEventType.POSITION_CLOSED,
            payload={"algorithm_id": "weighted_voting", "position_id": position_id, "exit_price": 100.0},
            occurred_at=SESSION_OPEN + timedelta(seconds=index * 2 + 2),
            expected_snapshot_version=snapshot.snapshot_version,
        )
    return inventory


def seed_full_capital_reservation(store: "MemoryStore") -> None:
    inventory = seeded_inventory(store)
    snapshot = inventory.current_snapshot(now=SESSION_OPEN)
    inventory.append_event(
        event_id="runtime-full-capital-reserved",
        event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
        payload={
            "algorithm_id": "weighted_voting",
            "order_id": "runtime-full-capital-order",
            "symbol": "SPY",
            "side": "BUY",
            "quantity": 250,
            "reserved_buying_power": 25_000.0,
            "planned_risk_dollars": 250.0,
            "decision_id": "runtime-full-capital-decision",
            "order_intent_id": "runtime-full-capital-intent",
            "client_order_id": "runtime-full-capital-client",
            "created_at": SESSION_OPEN.isoformat(),
            "status": "PENDING",
        },
        occurred_at=SESSION_OPEN,
        expected_snapshot_version=snapshot.snapshot_version,
    )


def seed_decision_without_risk(store: "MemoryStore"):
    store.write_snapshot(
        "weighted_voting.decisions.crash-decision",
        {
            "algorithm_id": "weighted_voting",
            "decision_id": "crash-decision",
            "status": "persisted_before_risk_response",
            "reason_codes": ("weighted_voting.test.crash.decision_before_risk",),
        },
    )
    return None


def seed_intent_without_global_risk(store: "MemoryStore"):
    store.write_snapshot(
        "weighted_voting.runtime.order_intents.intent-before-risk",
        {
            "algorithmId": "weighted_voting",
            "orderIntentId": "intent-before-risk",
            "decisionId": "decision-before-risk",
            "status": "PENDING_GLOBAL_RISK",
            "recordedAt": SESSION_OPEN.isoformat(),
            "reasonCodes": ("weighted_voting.test.crash.intent_before_global_risk",),
        },
    )
    return None


def seed_queued_order_without_submission(store: "MemoryStore"):
    supervisor = WeightedVotingRuntimeSupervisor(
        service=AcceptedExecutionService(store=store),
        store=store,
        config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
        event_bus=WeightedVotingEventBus(maxsize=8),
        paper_gateway=weighted_voting_local_gateway(FakePaperBroker(), store),
        inventory_repository=seeded_inventory(store),
        rollout_flags=validated_rollout_flags(),
        rollout_validation=validated_rollout_validation(),
    )
    enable_automatic_entries(supervisor)
    asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=20))))
    return supervisor.execution_queue.get_nowait()


def seed_submitted_lifecycle_without_ack(store: "MemoryStore"):
    item = seed_queued_order_without_submission(store)
    store.write_snapshot(
        f"weighted_voting.execution_gateway.lifecycle.{item.command.client_order_id}.latest",
        {
            "algorithmId": "weighted_voting",
            "clientOrderId": item.command.client_order_id,
            "orderIntentId": item.command.order_intent_id,
            "decisionId": item.command.decision_id,
            "status": "SUBMITTED",
            "recordedAt": SESSION_OPEN.isoformat(),
            "reasonCodes": ("weighted_voting.test.crash.submitted_before_ack",),
        },
    )
    return item


def seed_ack_lifecycle_without_fill(store: "MemoryStore"):
    item = seed_queued_order_without_submission(store)
    store.write_snapshot(
        f"weighted_voting.execution_gateway.lifecycle.{item.command.client_order_id}.latest",
        {
            "algorithmId": "weighted_voting",
            "clientOrderId": item.command.client_order_id,
            "orderIntentId": item.command.order_intent_id,
            "decisionId": item.command.decision_id,
            "status": "ACKNOWLEDGED",
            "recordedAt": SESSION_OPEN.isoformat(),
            "reasonCodes": ("weighted_voting.test.crash.ack_before_fill",),
        },
    )
    return item


def seed_partial_fill_lifecycle(store: "MemoryStore"):
    item = seed_queued_order_without_submission(store)
    store.write_snapshot(
        f"weighted_voting.execution_gateway.lifecycle.{item.command.client_order_id}.latest",
        {
            "algorithmId": "weighted_voting",
            "clientOrderId": item.command.client_order_id,
            "orderIntentId": item.command.order_intent_id,
            "decisionId": item.command.decision_id,
            "status": "PARTIALLY_FILLED",
            "recordedAt": SESSION_OPEN.isoformat(),
            "reasonCodes": ("weighted_voting.test.crash.partial_fill",),
        },
    )
    return item


def seed_filled_result_without_reconciliation(store: "MemoryStore"):
    item = seed_queued_order_without_submission(store)
    store.write_snapshot(
        f"weighted_voting.execution_gateway.automatic_result.{item.command.client_order_id}",
        {
            "algorithmId": "weighted_voting",
            "orderIntentId": item.command.order_intent_id,
            "decisionId": item.command.decision_id,
            "clientOrderId": item.command.client_order_id,
            "mode": "automatic",
            "submitted": True,
            "duplicate": False,
            "status": "FILLED",
            "fill": {
                "clientOrderId": item.command.client_order_id,
                "algorithmId": "weighted_voting",
                "orderIntentId": item.command.order_intent_id,
                "symbol": "SPY",
                "side": "BUY",
                "filledQuantity": 2,
                "averageFillPrice": 101.0,
                "status": "FILLED",
                "filledAt": SESSION_OPEN.isoformat(),
            },
            "reasonCodes": ("weighted_voting.test.crash.fill_before_inventory",),
        },
    )
    return item


def seed_unprotected_position(store: "MemoryStore"):
    inventory = seeded_inventory(store)
    snapshot = inventory.current_snapshot(now=SESSION_OPEN)
    inventory.append_event(
        event_id="runtime-test-unprotected-fill",
        event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
        payload={
            "algorithm_id": "weighted_voting",
            "position_id": "weighted_voting.position.SPY.unprotected",
            "symbol": "SPY",
            "side": "LONG",
            "quantity": 3,
            "average_entry_price": 100.0,
            "opened_at": SESSION_OPEN.isoformat(),
            "decision_id": "unprotected-decision",
            "order_intent_id": "unprotected-intent",
            "client_order_id": "unprotected-client",
            "source": "weighted_voting.test.unprotected_fill",
        },
        occurred_at=SESSION_OPEN,
        expected_snapshot_version=snapshot.snapshot_version,
    )
    return None


def seed_protected_position(store: "MemoryStore"):
    inventory = seeded_inventory(store)
    snapshot = inventory.current_snapshot(now=SESSION_OPEN)
    inventory.append_event(
        event_id="runtime-test-protected-fill",
        event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
        payload={
            "algorithm_id": "weighted_voting",
            "position_id": "weighted_voting.position.SPY.protected",
            "symbol": "SPY",
            "side": "LONG",
            "quantity": 3,
            "average_entry_price": 100.0,
            "opened_at": SESSION_OPEN.isoformat(),
            "decision_id": "protected-decision",
            "order_intent_id": "protected-intent",
            "client_order_id": "protected-client",
            "source": "weighted_voting.test.protected_fill",
        },
        occurred_at=SESSION_OPEN,
        expected_snapshot_version=snapshot.snapshot_version,
    )
    store.write_snapshot(
        "weighted_voting.position_manager.protection.protected-client",
        {
            "algorithmId": "weighted_voting",
            "clientOrderId": "protected-client",
            "protectedQuantity": 3,
            "status": "ACTIVE",
            "reasonCodes": ("weighted_voting.test.protective_order_active",),
        },
    )
    return None


class AcceptedExecutionService(WeightedVotingService):
    def evaluate_context(self, context, **_kwargs) -> dict:
        proposal = global_proposal_for_context(context)
        response = GlobalGateResponse(
            action="ALLOW",
            maximumAllowedQuantity=proposal.quantity,
            maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
            evaluatedAt=proposal.proposedAt,
            configurationHash="runtime-global-risk",
        )
        application = apply_global_gate_response(proposal, response)
        return {
            "decision": {"decision_id": proposal.decisionId},
            "gateResult": {
                "permission_granted": True,
                "mode": "automatic",
                "reason_codes": (),
                "explanation": "Synthetic accepted runtime gate result.",
            },
            "globalOrderProposal": proposal.model_dump(mode="json"),
            "globalRiskResponse": response.model_dump(mode="json"),
            "globalGateApplication": application.model_dump(mode="json"),
            "signals": (
                {"strategyId": "S2", "shadowRecordsOnly": False, "side": "BUY"},
                {"strategyId": "S3", "shadowRecordsOnly": True, "side": "HOLD"},
            ),
        }


class DecisionOnlyExecutionService(WeightedVotingService):
    def evaluate_context(self, context, **_kwargs) -> dict:
        proposal = global_proposal_for_context(context)
        return {
            "decision": {"decision_id": proposal.decisionId},
            "gateResult": {
                "permission_granted": True,
                "mode": "automatic",
                "reason_codes": (),
                "explanation": "Synthetic accepted runtime gate result before global risk.",
            },
            "globalOrderProposal": proposal.model_dump(mode="json"),
            "signals": (
                {"strategyId": "S2", "shadowRecordsOnly": False, "side": "BUY"},
                {"strategyId": "S3", "shadowRecordsOnly": True, "side": "HOLD"},
            ),
        }


class ApproveExternalRiskService:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, request):
        self.calls += 1
        return {
            "action": "APPROVE",
            "maximumAllowedQuantity": request.proposed_quantity,
            "maximumAdditionalRiskDollars": request.planned_risk,
            "configurationHash": "weighted_voting.test.external_approve",
            "configurationVersion": "weighted_voting.test.external_approve_v1",
            "evaluatedAt": request.request_timestamp.isoformat(),
            "expiresAt": (request.request_timestamp + timedelta(seconds=30)).isoformat(),
            "reasonCodes": ("weighted_voting.test.external_global_risk_approved",),
        }


class IncreasingExternalRiskService:
    def evaluate(self, request):
        return {
            "action": "APPROVE",
            "maximumAllowedQuantity": request.proposed_quantity + 1,
            "maximumAdditionalRiskDollars": request.planned_risk,
            "configurationHash": "weighted_voting.test.external_increase",
            "configurationVersion": "weighted_voting.test.external_increase_v1",
            "evaluatedAt": request.request_timestamp.isoformat(),
            "expiresAt": (request.request_timestamp + timedelta(seconds=30)).isoformat(),
            "reasonCodes": ("weighted_voting.test.external_global_risk_invalid_increase",),
        }


class MutableInventoryExternalRiskService:
    def evaluate(self, request):
        return {
            "action": "APPROVE",
            "maximumAllowedQuantity": request.proposed_quantity,
            "maximumAdditionalRiskDollars": request.planned_risk,
            "configurationHash": "weighted_voting.test.external_mutable_inventory",
            "configurationVersion": "weighted_voting.test.external_mutable_inventory_v1",
            "evaluatedAt": request.request_timestamp.isoformat(),
            "expiresAt": (request.request_timestamp + timedelta(seconds=30)).isoformat(),
            "reasonCodes": ("weighted_voting.test.external_global_risk_mutable_inventory",),
            "mergedInventory": {"cash": 1_000_000.0},
            "positions": {"SPY": {"quantity": 999}},
        }


class RejectExternalRiskService:
    def evaluate(self, request):
        return {
            "action": "REJECT",
            "maximumAllowedQuantity": 0,
            "maximumAdditionalRiskDollars": 0.0,
            "configurationHash": "weighted_voting.test.external_reject",
            "configurationVersion": "weighted_voting.test.external_reject_v1",
            "evaluatedAt": request.request_timestamp.isoformat(),
            "expiresAt": (request.request_timestamp + timedelta(seconds=30)).isoformat(),
            "reasonCodes": ("weighted_voting.test.external_global_risk_rejected",),
        }


class GlobalRiskOutageService(WeightedVotingService):
    def evaluate_context(self, context, **_kwargs) -> dict:
        proposal = global_proposal_for_context(context)
        return {
            "decision": {"decision_id": proposal.decisionId},
            "gateResult": {
                "permission_granted": True,
                "mode": "automatic",
                "reason_codes": ("weighted_voting.test.global_risk_outage",),
                "explanation": "Synthetic missing global-risk response.",
            },
            "globalOrderProposal": proposal.model_dump(mode="json"),
        }


class FakePaperBroker:
    broker_kind = "weighted_voting_local_paper"
    paper_endpoint = True
    live_trading_enabled = False

    def __init__(self) -> None:
        self.submit_count = 0
        self.cancel_count = 0
        self.base_url = "local-paper://weighted_voting"

    def verify_paper_endpoint(self) -> bool:
        return self.base_url == "local-paper://weighted_voting"

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"broker-{intent.clientOrderId}",
            status="ACCEPTED",
            acceptedAt=SESSION_OPEN,
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill:
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="weighted_voting",
            orderIntentId="runtime-auto-intent",
            symbol="SPY",
            side=Signal.BUY,
            filledQuantity=3,
            averageFillPrice=102.0,
            status="FILLED",
            filledAt=SESSION_OPEN,
        )

    def cancel_order(self, client_order_id: str) -> bool:
        self.cancel_count += 1
        return True

    def refresh_positions(self) -> list[dict]:
        return []


class ExplicitBrokerPaperBroker:
    broker_kind = "alpaca_paper"
    paper_endpoint = True
    live_trading_enabled = False

    def __init__(self) -> None:
        self.base_url = "https://paper-api.alpaca.markets/v2"

    def verify_paper_endpoint(self) -> bool:
        return True

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"broker-paper-{intent.clientOrderId}",
            status="ACCEPTED",
            acceptedAt=SESSION_OPEN,
        )


class NoPositionQueryLocalPaperBroker(WeightedVotingLocalPaperBroker):
    def __init__(self, store, inventory_repository) -> None:
        super().__init__(store, inventory_repository)
        self.refresh_positions_calls = 0

    def refresh_positions(self) -> list[dict]:
        self.refresh_positions_calls += 1
        raise AssertionError("LOCAL_PAPER restart recovery must not query broker positions")


class UnverifiedPaperBroker(FakePaperBroker):
    def verify_paper_account(self) -> bool:
        return False


class NoFillPaperBroker(FakePaperBroker):
    def refresh_order(self, client_order_id: str):
        return None


class ExistingOrderPaperBroker(FakePaperBroker):
    def __init__(self, *, order_intent_id: str) -> None:
        super().__init__()
        self.order_intent_id = order_intent_id
        self.lookup_count = 0

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill:
        self.lookup_count += 1
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="weighted_voting",
            orderIntentId=self.order_intent_id,
            symbol="SPY",
            side=Signal.BUY,
            filledQuantity=3,
            averageFillPrice=102.0,
            status="FILLED",
            filledAt=SESSION_OPEN,
        )


class LivePaperBroker(FakePaperBroker):
    def __init__(self) -> None:
        super().__init__()
        self.base_url = "https://api.alpaca.markets/v2"


class TimeoutPaperBroker(NoFillPaperBroker):
    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        raise TimeoutError("simulated broker acknowledgement timeout")


class ClosedClockPaperBroker(FakePaperBroker):
    def refresh_market_clock(self) -> dict:
        return {
            "isOpen": False,
            "status": "closed",
            "timestamp": (SESSION_OPEN + timedelta(minutes=30)).isoformat(),
            "source": "weighted_voting.test.closed_broker_clock",
        }


class UnreconciledPaperBroker(FakePaperBroker):
    def refresh_positions(self) -> list[dict]:
        return [
            {
                "positionId": "unknown-weighted-position",
                "clientOrderId": "unknown-weighted-client",
                "algorithmId": "weighted_voting",
                "symbol": "SPY",
                "quantity": 5,
                "averageEntryPrice": 100.0,
            }
        ]


class FakeWeightedVotingMarketDataClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls = 0

    async def get_bars(self, **_kwargs) -> list[dict]:
        self.calls += 1
        return [dict(row) for row in self.rows]


class MemoryCandleStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str], dict] = {}

    def upsert_many(self, candles: list[dict]) -> None:
        for candle in candles:
            key = (
                str(candle.get("symbol")).upper(),
                str(candle.get("timeframe")),
                str(candle.get("feed")),
                str(candle.get("timestamp")),
            )
            self.rows[key] = dict(candle)

    def latest_until(self, *, symbol: str, timeframe: str, feed: str, limit: int, end: str) -> list[dict]:
        rows = [
            row
            for (row_symbol, row_timeframe, row_feed, timestamp), row in self.rows.items()
            if row_symbol == symbol.upper() and row_timeframe == timeframe and row_feed == feed and timestamp <= end
        ]
        return sorted(rows, key=lambda row: row["timestamp"])[-limit:]


class OneShotProducer:
    def __init__(self, supervisor: WeightedVotingRuntimeSupervisor, event: WeightedVotingFinalisedBarEvent) -> None:
        self.supervisor = supervisor
        self.event = event
        self.polled = False

    async def poll_once(self):
        self.polled = True
        accepted = await self.supervisor.publish_finalised_bar(self.event)
        self.supervisor.stop_event.set()
        return (
            {
                "algorithmId": "weighted_voting",
                "status": "PUBLISHED" if accepted else "REJECTED",
                "accepted": accepted,
                "eventId": self.event.event_id,
            },
        )


def candle_rows_for_ingestion(count: int, *, finalized: bool = True, omit_index: int | None = None) -> list[dict]:
    rows = []
    for index in range(count):
        if omit_index is not None and index == omit_index:
            continue
        base = 100.0 + index * 0.02
        rows.append(
            {
                "provider": "alpaca",
                "feed": "iex",
                "symbol": "SPY",
                "timeframe": "1Min",
                "timestamp": (SESSION_OPEN + timedelta(minutes=index)).isoformat(),
                "open": base,
                "high": base + 0.10,
                "low": base - 0.08,
                "close": base + 0.03,
                "volume": 100000 + index,
                "finalized": finalized,
            }
        )
    return rows


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


class FailingWriteStore(MemoryStore):
    def write_snapshot(self, key: str, snapshot: dict) -> None:
        raise RuntimeError(f"simulated persistence outage for {key}")


if __name__ == "__main__":
    unittest.main()
