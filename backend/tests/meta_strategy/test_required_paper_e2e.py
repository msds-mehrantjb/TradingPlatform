from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.meta_strategy.decision_worker import (
    MetaStrategyDecisionWorkerContext,
    MetaStrategyFinalisedBarDecisionEvent,
    MetaStrategyFinalisedBarDecisionWorker,
)
from backend.app.algorithms.meta_strategy.execution_pipeline import (
    MetaStrategyExecutionPipelineConfig,
    run_meta_strategy_execution_pipeline,
)
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.order_intent import build_meta_strategy_order_intent
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import build_meta_strategy_settings
from backend.app.algorithms.meta_strategy.workers import MetaStrategyPositionManagementWorker
from backend.app.execution import PaperGatewayBrokerAck, PaperOrderGateway
from backend.app.gates import GlobalGateResponse
from backend.tests.test_meta_strategy_step7_market_snapshot import request_with


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


class MetaStrategyRequiredPaperE2ETest(unittest.TestCase):
    def test_finalized_bar_to_closed_paper_trade_and_pnl_with_integration_safe_broker(self) -> None:
        env = PaperE2EEnv()
        env.enqueue_finalized_bar()

        decision_job = env.decision_worker().run_once(now=NOW)
        outbox = env.jobs.outbox_for_order_intent("meta_strategy.order_intent.decision-1")
        self.assertIsNotNone(decision_job)
        self.assertEqual(env.jobs.queue_status(queue_name="finalised_bar_decisions", now=NOW)["queues"]["finalised_bar_decisions"]["succeeded"], 1)
        self.assertEqual(outbox["status"], "PENDING")

        env.submission_worker().run_once(now=NOW + timedelta(seconds=1))
        acknowledged = env.jobs.outbox_for_order_intent("meta_strategy.order_intent.decision-1")
        self.assertEqual(acknowledged["status"], "ACKNOWLEDGED")

        env.broker.enqueue_fill(order_intent_id="meta_strategy.order_intent.decision-1", quantity=10, price=100.0, side="BUY", event_id="entry-fill")
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=2))
        open_snapshot = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        self.assertEqual(open_snapshot.open_positions[0].quantity, 10.0)

        env.enqueue_end_of_day_position_management()
        position_result = env.position_worker().run_once(now=NOW + timedelta(minutes=1))
        exit_intent_id = "meta_strategy.exit.meta_strategy.position.meta_strategy.paper.default.SPY.SESSION_END"
        self.assertEqual(position_result["createdExitIntentCount"], 1)
        self.assertEqual(env.jobs.outbox_for_order_intent(exit_intent_id)["status"], "PENDING")

        env.submission_worker().run_once(now=NOW + timedelta(minutes=1, seconds=1))
        env.broker.enqueue_fill(order_intent_id=exit_intent_id, quantity=10, price=101.0, side="SELL", event_id="exit-fill")
        env.reconciliation_worker().run_once(now=NOW + timedelta(minutes=1, seconds=2))

        closed = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 101.0})
        self.assertEqual(closed.open_positions, ())
        self.assertGreater(closed.realised_pnl, 0.0)
        self.assertGreaterEqual(env.jobs.broker_event_count(), 2)


class PaperE2EEnv:
    def __init__(self) -> None:
        database_url = f"sqlite:///{temp_db_path()}"
        self.jobs = MetaStrategyJobRepository(database_url)
        self.inventory = MetaStrategySqliteRepository(database_url)
        self.settings = build_meta_strategy_settings(settings_version="required-e2e-settings", created_at=NOW)
        self.broker = E2EPaperBroker()
        self.gateway = PaperOrderGateway(self.broker, self.jobs.gateway_store())

    def enqueue_finalized_bar(self) -> None:
        self.jobs.enqueue_finalised_bar_decision(
            mode="PAPER",
            symbol="SPY",
            timeframe="1m",
            bar_end=NOW,
            settings_version=self.settings.settings_version,
            now=NOW,
        )

    def decision_worker(self) -> MetaStrategyFinalisedBarDecisionWorker:
        return MetaStrategyFinalisedBarDecisionWorker(
            repository=self.jobs,
            state_provider=StaticStateProvider(self.settings),
            pipeline_runner=forced_buy_runner,
        )

    def submission_worker(self):
        from backend.app.algorithms.meta_strategy.execution import MetaStrategyPaperOrderSubmissionWorker

        return MetaStrategyPaperOrderSubmissionWorker(
            repository=self.jobs,
            inventory_repository=self.inventory,
            paper_gateway=self.gateway,
            global_risk_source=AllowRisk(),
        )

    def reconciliation_worker(self):
        from backend.app.algorithms.meta_strategy.execution import MetaStrategyPaperOrderReconciliationWorker

        return MetaStrategyPaperOrderReconciliationWorker(
            repository=self.jobs,
            inventory_repository=self.inventory,
            paper_gateway=self.gateway,
        )

    def position_worker(self) -> MetaStrategyPositionManagementWorker:
        return MetaStrategyPositionManagementWorker(repository=self.jobs, inventory_repository=self.inventory)

    def enqueue_end_of_day_position_management(self) -> None:
        self.jobs.enqueue_job(
            job_type="position_management",
            idempotency_key="required-e2e-eod-exit",
            payload={
                "capitalPartitionId": "meta_strategy.paper.default",
                "settingsVersion": self.settings.settings_version,
                "decisionId": "required-e2e-position-management",
                "eventId": "required-e2e-position-event",
                "correlationId": "required-e2e-position",
                "symbol": "SPY",
                "candle": {"symbol": "SPY", "timestamp": NOW.isoformat(), "open": 101.0, "high": 101.2, "low": 100.8, "close": 101.0},
                "markPrices": {"SPY": 101.0},
                "mode": "PAPER",
                "endOfDayExitAt": NOW.isoformat(),
                "noOvernight": True,
            },
            now=NOW,
        )


class StaticStateProvider:
    def __init__(self, settings) -> None:
        self.settings = settings

    def load_context(self, event: MetaStrategyFinalisedBarDecisionEvent) -> MetaStrategyDecisionWorkerContext:
        return MetaStrategyDecisionWorkerContext(
            event=event,
            settings=self.settings,
            market_snapshot_request=request_with(decision_timestamp=event.bar_end, one_minute_end=event.bar_end - timedelta(minutes=1)),
            inventory_snapshot={"reservedRiskDollars": 0.0, "openPositions": (), "dailyTradeCount": 0},
            account_snapshot={"accountEquity": 100_000.0, "buyingPower": 100_000.0},
            global_risk_snapshot={"availableRiskDollars": 1_000.0, "maxQuantity": 10_000},
            event_state={"featureSchemaVersion": "meta_strategy_feature_schema_v1"},
            operational_health={"tradingAllowed": True},
            active_model_artifact=None,
        )


def forced_buy_runner(request, settings, global_risk_snapshot):
    result = run_meta_strategy_execution_pipeline(
        request,
        config=MetaStrategyExecutionPipelineConfig(submit_to_broker=False),
        config_settings=settings,
    )
    intent = build_meta_strategy_order_intent(
        snapshot=result.snapshot,
        side="BUY",
        quantity=10,
        stop_price=99.0,
        limit_price=100.05,
    ).intent
    assert intent is not None
    intent_payload = intent.model_dump(mode="json")
    intent_payload.update(
        {
            "orderIntentId": intent.order_intent_id,
            "limitPrice": intent.limit_price,
            "stopPrice": intent.stop_price,
            "targetPrice": 104.0,
            "reservedRiskDollars": 10.0,
        }
    )
    return replace(
        result,
        order_intent=intent_payload,
        final_valid=True,
        reason_codes=tuple(dict.fromkeys((*result.reason_codes, "meta_strategy.required_e2e.forced_paper_order"))),
    )


class E2EPaperBroker:
    broker_kind = "alpaca_paper"
    configured = True
    paper_endpoint = True

    def __init__(self) -> None:
        self.orders: dict[str, dict] = {}
        self.events: list[dict] = []

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        client_order_id = intent.clientOrderId
        self.orders[str(intent.orderIntentId)] = {
            "brokerEventId": f"ack-{intent.orderIntentId}",
            "algorithmId": "meta_strategy",
            "clientOrderId": client_order_id,
            "brokerOrderId": f"broker-{intent.orderIntentId}",
            "orderIntentId": str(intent.orderIntentId),
            "status": "ACCEPTED",
            "symbol": intent.symbol,
            "side": intent.side.value if hasattr(intent.side, "value") else str(intent.side),
            "submittedQuantity": int(intent.submittedQuantity),
            "timestamp": NOW.isoformat(),
        }
        return PaperGatewayBrokerAck(clientOrderId=client_order_id, brokerOrderId=f"broker-{intent.orderIntentId}", status="ACCEPTED", acceptedAt=NOW)

    def enqueue_fill(self, *, order_intent_id: str, quantity: int, price: float, side: str, event_id: str) -> None:
        order = self.orders[order_intent_id]
        self.events.append(
            {
                **order,
                "brokerEventId": event_id,
                "status": "FILLED",
                "side": side,
                "filledQuantity": quantity,
                "averageFillPrice": price,
                "timestamp": NOW.isoformat(),
            }
        )

    def refresh_order(self, client_order_id: str):
        return None

    def cancel_order(self, client_order_id: str) -> bool:
        return False

    def refresh_positions(self):
        return []

    def list_order_events(self):
        events = list(self.orders.values()) + list(self.events)
        self.events.clear()
        return events


class AllowRisk:
    def approve_order(self, proposal):
        return GlobalGateResponse(
            action="ALLOW",
            maximumAllowedQuantity=proposal.quantity,
            maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
            evaluatedAt=NOW,
            configurationHash="required-e2e-allow-risk",
        )


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"meta-strategy-required-e2e-{uuid4().hex}.sqlite"
