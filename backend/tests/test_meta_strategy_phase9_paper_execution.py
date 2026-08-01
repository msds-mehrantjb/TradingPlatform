import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from backend.app.algorithms.meta_strategy.alpaca_paper_broker import MetaStrategyAlpacaPaperBroker
from backend.app.algorithms.meta_strategy.execution import (
    MetaStrategyPaperOrderReconciliationWorker,
    MetaStrategyPaperOrderSubmissionWorker,
    MetaStrategyStaleOrderCancellationWorker,
)
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import build_meta_strategy_settings
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway
from backend.app.gates import AppliedGlobalGateDecision, GlobalGateResponse, apply_global_gate_response


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


class MetaStrategyPhase9PaperExecutionTest(unittest.TestCase):
    def test_crash_before_broker_submission_recovers_without_losing_outbox(self) -> None:
        env = RuntimeEnv()
        env.create_outbox()
        claimed = env.jobs.claim_next_execution_outbox(worker_id="crashed-before-submit", lease_seconds=10, now=NOW)

        worker = env.submission_worker()
        worker.run_once(now=NOW + timedelta(seconds=11))

        outbox = env.jobs.outbox_for_order_intent("intent-1")
        self.assertEqual(claimed["status"], "SUBMITTING")
        self.assertEqual(env.broker.submit_count, 1)
        self.assertEqual(outbox["status"], "ACKNOWLEDGED")
        self.assertEqual(env.inventory.current_inventory_snapshot().open_positions, ())

    def test_crash_after_submission_before_ack_persistence_is_recovered_by_reconciliation(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(crash_after_submit=True))
        env.create_outbox()
        first = env.submission_worker().run_once(now=NOW)

        env.broker.crash_after_submit = False
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=30))

        outbox = env.jobs.outbox_for_order_intent("intent-1")
        self.assertEqual(first["status"], "RETRY")
        self.assertEqual(env.broker.submit_count, 1)
        self.assertEqual(outbox["status"], "ACKNOWLEDGED")
        self.assertEqual(outbox["brokerOrderId"], "broker-intent-1")

    def test_submission_timeout_followed_by_later_broker_discovery_does_not_create_fill(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(timeout_after_submit=True))
        env.create_outbox()

        timeout_result = env.submission_worker().run_once(now=NOW)
        timed_out = env.jobs.outbox_for_order_intent("intent-1")
        self.assertEqual(timeout_result["status"], "RECONCILIATION_REQUIRED")
        self.assertEqual(timed_out["status"], "RECONCILIATION_REQUIRED")
        self.assertEqual(env.inventory.current_inventory_snapshot().reserved_risk_dollars, 100.0)
        env.broker.timeout_after_submit = False
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=20))

        outbox = env.jobs.outbox_for_order_intent("intent-1")
        snapshot = env.inventory.current_inventory_snapshot()
        self.assertEqual(outbox["status"], "ACKNOWLEDGED")
        self.assertEqual(snapshot.open_positions, ())
        self.assertEqual(env.jobs.broker_event_count(), 1)

    def test_duplicate_submission_retry_submits_one_logical_order(self) -> None:
        env = RuntimeEnv()
        env.create_outbox()
        env.submission_worker().run_once(now=NOW)
        duplicate = env.submission_worker().run_once(now=NOW + timedelta(seconds=1))

        self.assertIsNone(duplicate)
        self.assertEqual(env.broker.submit_count, 1)
        self.assertEqual(env.jobs.outbox_for_order_intent("intent-1")["clientOrderId"], env.broker.submitted_client_ids[0])

    def test_partial_fill_followed_by_cancellation_updates_fill_quantity_and_releases_risk(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(quantity=10, reserved_risk=100.0)
        env.submission_worker().run_once(now=NOW)
        env.broker.events.append(env.broker.fill_event(quantity=4, status="PARTIALLY_FILLED", event_id="fill-partial"))
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=10))
        env.broker.events.append(env.broker.status_event(status="CANCELED", event_id="cancel-after-partial"))
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=20))

        snapshot = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        outbox = env.jobs.outbox_for_order_intent("intent-1")
        self.assertEqual(snapshot.open_positions[0].quantity, 4.0)
        self.assertEqual(snapshot.reserved_risk_dollars, 0.0)
        self.assertEqual(outbox["status"], "CANCELLED")

    def test_duplicate_fill_event_is_idempotent(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(quantity=10, reserved_risk=100.0)
        env.submission_worker().run_once(now=NOW)
        fill = env.broker.fill_event(quantity=3, status="PARTIALLY_FILLED", event_id="duplicate-fill")
        env.broker.events.extend([fill, fill])

        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=10))
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=11))

        snapshot = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        self.assertEqual(snapshot.open_positions[0].quantity, 3.0)
        self.assertEqual(env.jobs.broker_event_count(), 2)

    def test_rejected_order_releases_reserved_risk_without_position(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(ack_status="REJECTED"))
        env.create_outbox(quantity=10, reserved_risk=100.0)

        env.submission_worker().run_once(now=NOW)

        snapshot = env.inventory.current_inventory_snapshot()
        outbox = env.jobs.outbox_for_order_intent("intent-1")
        self.assertEqual(outbox["status"], "REJECTED")
        self.assertEqual(snapshot.reserved_risk_dollars, 0.0)
        self.assertEqual(snapshot.open_positions, ())

    def test_stale_order_cancellation_worker_persists_cancel_evidence(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(fill_on_submit=False))
        env.create_outbox()
        env.submission_worker().run_once(now=NOW)

        result = env.stale_worker().run_once(now=NOW + timedelta(minutes=10))

        outbox = env.jobs.outbox_for_order_intent("intent-1")
        self.assertEqual(result["cancelled"], 1)
        self.assertEqual(outbox["status"], "CANCELLED")
        self.assertEqual(env.broker.cancel_count, 1)
        self.assertIn("meta_strategy.execution.stale_order_cancelled", outbox["payload"]["reasonCodes"])

    def test_restart_recovery_does_not_duplicate_submitted_order(self) -> None:
        database_url = f"sqlite:///{temp_db_path()}"
        broker = FakePaperBroker()
        jobs = MetaStrategyJobRepository(database_url)
        inventory = MetaStrategySqliteRepository(database_url)
        RuntimeEnv(jobs=jobs, inventory=inventory, broker=broker).create_outbox()
        MetaStrategyPaperOrderSubmissionWorker(repository=jobs, inventory_repository=inventory, paper_gateway=PaperOrderGateway(broker, jobs.gateway_store()), global_risk_source=AllowRisk()).run_once(now=NOW)

        restarted_jobs = MetaStrategyJobRepository(database_url)
        restarted_inventory = MetaStrategySqliteRepository(database_url)
        recovered = MetaStrategyPaperOrderReconciliationWorker(repository=restarted_jobs, inventory_repository=restarted_inventory, paper_gateway=PaperOrderGateway(broker, restarted_jobs.gateway_store())).run_once(now=NOW + timedelta(seconds=30))

        self.assertEqual(broker.submit_count, 1)
        self.assertEqual(recovered["status"], "OK")
        self.assertEqual(restarted_jobs.outbox_for_order_intent("intent-1")["status"], "ACKNOWLEDGED")

    def test_broker_order_belonging_to_another_algorithm_is_quarantined(self) -> None:
        env = RuntimeEnv()
        env.broker.events.append(
            {
                "brokerEventId": "foreign-event",
                "algorithmId": "weighted_voting",
                "clientOrderId": "foreign-client",
                "brokerOrderId": "foreign-broker",
                "orderIntentId": "foreign-intent",
                "status": "FILLED",
                "symbol": "SPY",
                "side": "BUY",
                "filledQuantity": 5,
                "averageFillPrice": 100.0,
                "timestamp": NOW.isoformat(),
            }
        )

        result = env.reconciliation_worker().run_once(now=NOW)

        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(env.inventory.current_inventory_snapshot().open_positions, ())

    def test_global_risk_rejection_and_resize_are_applied_before_submission(self) -> None:
        rejected = RuntimeEnv(global_risk=RejectRisk())
        rejected.create_outbox(quantity=10, reserved_risk=100.0)
        reject_result = rejected.submission_worker().run_once(now=NOW)

        resized = RuntimeEnv(global_risk=ResizeRisk(quantity=4, risk=40.0))
        resized.create_outbox(quantity=10, reserved_risk=100.0)
        resize_result = resized.submission_worker().run_once(now=NOW)

        self.assertEqual(reject_result["status"], "REJECTED")
        self.assertEqual(rejected.broker.submit_count, 0)
        self.assertEqual(rejected.inventory.current_inventory_snapshot().reserved_risk_dollars, 0.0)
        self.assertEqual(resize_result["status"], "ACKNOWLEDGED")
        self.assertEqual(resized.broker.last_quantity, 4)
        self.assertEqual(resized.inventory.current_inventory_snapshot().reserved_risk_dollars, 40.0)

    def test_submission_uses_meta_strategy_partitioned_client_order_id(self) -> None:
        env = RuntimeEnv()
        env.create_outbox()

        env.submission_worker().run_once(now=NOW)

        self.assertEqual(env.broker.submit_count, 1)
        self.assertTrue(env.broker.submitted_client_ids[0].startswith("meta-strategy-meta-strategy-paper-defa-"))

    def test_order_policy_is_carried_to_paper_gateway_and_broker_intent(self) -> None:
        env = RuntimeEnv()
        env.create_outbox(
            extra_order_payload={
                "orderType": "STOP_LIMIT",
                "timeInForce": "GTC",
                "stopLimitPrice": 94.5,
                "cancelAndReplaceEnabled": True,
                "maximumOrderAgeSeconds": 120,
                "maximumReplacementCount": 2,
                "protectiveExitEscalationPolicy": "CANCEL_AND_MARKETABLE_LIMIT",
            }
        )

        env.submission_worker().run_once(now=NOW)

        self.assertEqual(env.broker.last_intent.orderType, "STOP_LIMIT")
        self.assertEqual(env.broker.last_intent.timeInForce, "GTC")
        self.assertEqual(env.broker.last_intent.stopLimitPrice, 94.5)
        self.assertTrue(env.broker.last_intent.cancelAndReplaceEnabled)
        self.assertEqual(env.broker.last_intent.maxReplacementCount, 2)

    def test_broker_ack_and_fill_enqueue_position_management(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(fill_on_submit=True))
        env.create_outbox()

        env.submission_worker().run_once(now=NOW)

        status = env.jobs.queue_status(queue_name="position_management", now=NOW)
        self.assertGreaterEqual(status["queues"]["position_management"]["pending"], 1)

    def test_stale_order_replaces_only_with_configured_budget(self) -> None:
        env = RuntimeEnv(broker=FakePaperBroker(fill_on_submit=False))
        env.create_outbox(
            extra_order_payload={
                "cancelAndReplaceEnabled": True,
                "maximumReplacementCount": 1,
            }
        )
        env.submission_worker().run_once(now=NOW)

        result = env.stale_worker().run_once(now=NOW + timedelta(minutes=10))

        outbox = env.jobs.outbox_for_order_intent("intent-1")
        self.assertEqual(result["cancelled"], 0)
        self.assertEqual(env.broker.replace_count, 1)
        self.assertEqual(outbox["status"], "REPLACED")
        self.assertEqual(env.inventory.current_inventory_snapshot().reserved_risk_dollars, 100.0)

    def test_alpaca_paper_adapter_posts_configured_order_body(self) -> None:
        client = RecordingHttpClient(
            post_payload={
                "id": "alpaca-order-1",
                "client_order_id": "meta-strategy-meta-strategy-paper-def-test",
                "status": "accepted",
                "submitted_at": NOW.isoformat(),
            }
        )
        broker = MetaStrategyAlpacaPaperBroker(
            SimpleNamespace(
                alpaca_key_id="paper-key",
                alpaca_secret_key="paper-secret",
                alpaca_trading_base_url="https://paper-api.alpaca.markets/v2",
                has_alpaca_credentials=True,
            ),
            http_client=client,
        )
        intent = SimpleNamespace(
            symbol="SPY",
            submittedQuantity=10,
            side=Signal.BUY,
            orderType="STOP_LIMIT",
            timeInForce="GTC",
            clientOrderId="meta-strategy-meta-strategy-paper-def-test",
            limitPrice=100.0,
            stopPrice=95.0,
            stopLimitPrice=94.5,
            targetPrice=110.0,
        )

        ack = broker.submit_bracket_order(intent)

        self.assertEqual(ack.status, "ACCEPTED")
        self.assertEqual(client.last_post_json["client_order_id"], intent.clientOrderId)
        self.assertEqual(client.last_post_json["type"], "stop_limit")
        self.assertEqual(client.last_post_json["time_in_force"], "gtc")
        self.assertEqual(client.last_post_json["stop_loss"]["stop_price"], "95.0")
        self.assertEqual(client.last_post_json["stop_loss"]["limit_price"], "94.5")
        self.assertEqual(client.last_post_json["take_profit"]["limit_price"], "110.0")


class RuntimeEnv:
    def __init__(
        self,
        *,
        jobs: MetaStrategyJobRepository | None = None,
        inventory: MetaStrategySqliteRepository | None = None,
        broker: "FakePaperBroker | None" = None,
        global_risk=None,
    ) -> None:
        database_url = f"sqlite:///{temp_db_path()}"
        self.jobs = jobs or MetaStrategyJobRepository(database_url)
        self.inventory = inventory or MetaStrategySqliteRepository(database_url)
        self.broker = broker or FakePaperBroker()
        self.global_risk = global_risk or AllowRisk()
        self.gateway = PaperOrderGateway(self.broker, self.jobs.gateway_store())

    def create_outbox(self, *, quantity: int = 10, reserved_risk: float = 100.0, extra_order_payload: dict | None = None) -> None:
        settings = build_meta_strategy_settings(settings_version="phase9-settings", created_at=NOW)
        job = self.jobs.enqueue_finalised_bar_decision(mode="PAPER", symbol="SPY", timeframe="1m", bar_end=NOW, settings_version=settings.settings_version, now=NOW)
        claimed = self.jobs.claim_next_job(queue_name="finalised_bar_decisions", worker_id="decision-worker", now=NOW)
        event_id = self.jobs.read_payload(job.payload_reference)["payload"]["eventId"]
        event = self.jobs.event_by_id(event_id)
        self.jobs.persist_decision_atomic(
            job=claimed,
            event=event,
            decision_id="decision-1",
            payload={
                "algorithmId": "meta_strategy",
                "decisionId": "decision-1",
                "eventId": event_id,
                "jobId": claimed.job_id,
                "symbol": "SPY",
                "barEnd": NOW.isoformat(),
                "settingsVersion": settings.settings_version,
                "modelVersion": "phase9-model",
                "decisionStatus": "ORDER_PROPOSED",
            },
            order_intent={
                "algorithmId": "meta_strategy",
                "capitalPartitionId": "meta_strategy.paper.default",
                "mode": "PAPER",
                "settingsVersion": settings.settings_version,
                "decisionId": "decision-1",
                "jobId": claimed.job_id,
                "eventId": event_id,
                "orderIntentId": "intent-1",
                "symbol": "SPY",
                "side": "BUY",
                "quantity": quantity,
                "limitPrice": 100.0,
                "stopPrice": 95.0,
                "targetPrice": 110.0,
                "reservedRiskDollars": reserved_risk,
                "createdAt": NOW.isoformat(),
                "timestamp": NOW.isoformat(),
                **(extra_order_payload or {}),
            },
            now=NOW,
        )
        self.jobs.complete_job(claimed.job_id, worker_id="decision-worker", now=NOW)

    def submission_worker(self) -> MetaStrategyPaperOrderSubmissionWorker:
        return MetaStrategyPaperOrderSubmissionWorker(
            repository=self.jobs,
            inventory_repository=self.inventory,
            paper_gateway=self.gateway,
            global_risk_source=self.global_risk,
        )

    def reconciliation_worker(self) -> MetaStrategyPaperOrderReconciliationWorker:
        return MetaStrategyPaperOrderReconciliationWorker(
            repository=self.jobs,
            inventory_repository=self.inventory,
            paper_gateway=self.gateway,
        )

    def stale_worker(self) -> MetaStrategyStaleOrderCancellationWorker:
        return MetaStrategyStaleOrderCancellationWorker(
            repository=self.jobs,
            inventory_repository=self.inventory,
            paper_gateway=self.gateway,
        )


class FakePaperBroker:
    broker_kind = "alpaca_paper"
    configured = True
    paper_endpoint = True

    def __init__(
        self,
        *,
        ack_status: str = "ACCEPTED",
        fill_on_submit: bool = False,
        crash_after_submit: bool = False,
        timeout_after_submit: bool = False,
    ) -> None:
        self.ack_status = ack_status
        self.fill_on_submit = fill_on_submit
        self.crash_after_submit = crash_after_submit
        self.timeout_after_submit = timeout_after_submit
        self.submit_count = 0
        self.cancel_count = 0
        self.replace_count = 0
        self.orders: dict[str, dict] = {}
        self.events: list[dict] = []
        self.positions: list[dict] = []
        self.submitted_client_ids: list[str] = []
        self.last_quantity = 0
        self.last_intent = None

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent):
        self.submit_count += 1
        self.last_intent = intent
        self.last_quantity = intent.submittedQuantity
        self.submitted_client_ids.append(intent.clientOrderId)
        self.orders[intent.clientOrderId] = {
            "brokerEventId": f"ack-{intent.orderIntentId}",
            "algorithmId": intent.algorithmId,
            "clientOrderId": intent.clientOrderId,
            "brokerOrderId": f"broker-{intent.orderIntentId}",
            "orderIntentId": intent.orderIntentId,
            "status": self.ack_status,
            "symbol": intent.symbol,
            "side": intent.side.value if hasattr(intent.side, "value") else str(intent.side),
            "submittedQuantity": intent.submittedQuantity,
            "timestamp": NOW.isoformat(),
        }
        if self.crash_after_submit:
            raise RuntimeError("process crashed after broker accepted request")
        if self.timeout_after_submit:
            raise TimeoutError("broker submission timed out")
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"broker-{intent.orderIntentId}" if self.ack_status != "REJECTED" else None,
            status=self.ack_status,
            acceptedAt=NOW if self.ack_status != "REJECTED" else None,
            rejectedReason="rejected-by-test" if self.ack_status == "REJECTED" else None,
        )

    def refresh_order(self, client_order_id: str):
        if not self.fill_on_submit:
            return None
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="meta_strategy",
            orderIntentId="intent-1",
            symbol="SPY",
            side=Signal.BUY,
            filledQuantity=10,
            averageFillPrice=100.0,
            status="FILLED",
            filledAt=NOW,
        )

    def cancel_order(self, client_order_id: str) -> bool:
        self.cancel_count += 1
        order = self.orders.get(client_order_id)
        if order:
            order["status"] = "CANCELED"
            self.events.append({**order, "brokerEventId": f"cancel-{order['orderIntentId']}", "status": "CANCELED"})
        return bool(order)

    def replace_order(self, broker_order_id: str, *, quantity: int | None = None, limit_price: float | None = None, stop_price: float | None = None, client_order_id: str | None = None):
        self.replace_count += 1
        order = next((item for item in self.orders.values() if item["brokerOrderId"] == broker_order_id), None)
        if order is None:
            return None
        order["status"] = "REPLACED"
        order["clientOrderId"] = client_order_id or order["clientOrderId"]
        return {**order, "brokerEventId": f"replace-{order['orderIntentId']}", "status": "REPLACED"}

    def refresh_positions(self):
        return list(self.positions)

    def list_order_events(self):
        events = list(self.orders.values()) + list(self.events)
        self.events.clear()
        return events

    def fill_event(self, *, quantity: int, status: str, event_id: str) -> dict:
        order = next(iter(self.orders.values()))
        return {
            **order,
            "brokerEventId": event_id,
            "status": status,
            "filledQuantity": quantity,
            "averageFillPrice": 100.0,
            "timestamp": NOW.isoformat(),
        }

    def status_event(self, *, status: str, event_id: str) -> dict:
        order = next(iter(self.orders.values()))
        return {**order, "brokerEventId": event_id, "status": status, "timestamp": NOW.isoformat()}


class AllowRisk:
    def approve_order(self, proposal):
        return GlobalGateResponse(
            action="ALLOW",
            maximumAllowedQuantity=proposal.quantity,
            maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
            evaluatedAt=NOW,
            configurationHash="allow-risk",
        )


class RejectRisk:
    def approve_order(self, proposal):
        return GlobalGateResponse(
            action="REJECT_NEW_ENTRY",
            maximumAllowedQuantity=0,
            maximumAdditionalRiskDollars=0.0,
            rejectionReasons=("phase9.global_risk_rejected",),
            evaluatedAt=NOW,
            configurationHash="reject-risk",
        )


class ResizeRisk:
    def __init__(self, *, quantity: int, risk: float) -> None:
        self.quantity = quantity
        self.risk = risk

    def approve_order(self, proposal):
        return GlobalGateResponse(
            action="REDUCE_QUANTITY",
            maximumAllowedQuantity=self.quantity,
            maximumAdditionalRiskDollars=self.risk,
            evaluatedAt=NOW,
            configurationHash="resize-risk",
        )


class RecordingHttpClient:
    def __init__(self, *, post_payload: dict) -> None:
        self.post_payload = post_payload
        self.last_post_json: dict | None = None

    def post(self, url: str, *, headers: dict, json: dict):
        self.last_post_json = dict(json)
        return RecordingHttpResponse(self.post_payload)

    def get(self, url: str, *, headers: dict, params: dict | None = None):
        return RecordingHttpResponse({})

    def patch(self, url: str, *, headers: dict, json: dict):
        return RecordingHttpResponse({"id": "replacement", "client_order_id": json.get("client_order_id"), "status": "replaced"})


class RecordingHttpResponse:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.text = str(payload)

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return dict(self._payload)


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"meta-strategy-phase9-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
