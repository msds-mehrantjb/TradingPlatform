from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlparse
from unittest.mock import patch

import httpx
import pytest

from backend.app.algorithms.wca.alpaca_paper_broker import (
    WCA_ALPACA_ORDER_STREAM_UNAVAILABLE,
    WcaAlpacaPaperBroker,
    WcaAlpacaPaperBrokerConfigurationError,
)
from backend.app.algorithms.wca.contracts import WcaOrderStatus, WcaOrderValidationContext, WcaRuntimeMode, WcaSide
from backend.app.algorithms.wca.paper_account import (
    WCA_ALPACA_PAPER_ACCOUNT_ID,
    WCA_ALPACA_PAPER_API_KEY_ID,
    WCA_ALPACA_PAPER_API_SECRET_KEY,
    WCA_ALPACA_PAPER_BASE_URL,
    WCA_AUTOMATIC_PAPER_ENABLED,
    WCA_REQUIRED_ALPACA_PAPER_BASE_URL,
)
from backend.app.algorithms.wca.paper_broker import (
    WCA_ALPACA_CLIENT_ORDER_ID_LIMIT,
    WcaPaperBrokerOutboxAdapter,
    WcaPaperBrokerTimeout,
    build_wca_paper_broker_request,
)
from backend.app.algorithms.wca.runtime_commands import WcaRuntimeCommandType, runtime_command
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.runtime_supervisor import WcaRuntimeSettings, WcaRuntimeSupervisor
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.tests.test_wca_step6_inventory_persistence import decision_with_order, temp_db_path


ACCOUNT_ID = "paper-phase6"


class FakeAlpacaResponse:
    def __init__(self, status_code: int = 200, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}" if payload is not None else b""

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        request = httpx.Request("GET", WCA_REQUIRED_ALPACA_PAPER_BASE_URL)
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError("alpaca request failed", request=request, response=response)


class FakeAlpacaClient:
    def __init__(self, routes=None, queue=None) -> None:
        self.routes = routes or {}
        self.queue = list(queue or [])
        self.calls = []

    def request(self, method: str, url: str, **kwargs):
        parsed = urlparse(url)
        call = {
            "method": method.upper(),
            "path": parsed.path,
            "url": url,
            "json": kwargs.get("json"),
            "params": kwargs.get("params"),
            "headers": kwargs.get("headers", {}),
            "timeout": kwargs.get("timeout"),
        }
        self.calls.append(call)
        if self.queue:
            result = self.queue.pop(0)
        else:
            result = self.routes[(method.upper(), parsed.path)]
        if callable(result):
            result = result(call)
        if isinstance(result, Exception):
            raise result
        return result


def test_alpaca_paper_endpoint_and_account_identity_are_enforced() -> None:
    with pytest.raises(WcaAlpacaPaperBrokerConfigurationError, match="paper_endpoint_required"):
        WcaAlpacaPaperBroker(
            account_id=ACCOUNT_ID,
            key_id="wca-key",
            secret_key="wca-secret",
            base_url="https://api.alpaca.markets",
            http_client=FakeAlpacaClient(),
        )

    broker = WcaAlpacaPaperBroker(
        account_id=ACCOUNT_ID,
        key_id="wca-key",
        secret_key="wca-secret",
        http_client=FakeAlpacaClient({("GET", "/v2/account"): FakeAlpacaResponse(payload={"account_number": "other-paper"})}),
    )

    verified, reason_codes = broker.verify_account_and_endpoint_identity()

    assert verified is False
    assert reason_codes == ("wca.alpaca_paper.account_id_mismatch",)


def test_order_submission_payload_mapping_redacts_response_and_preserves_wca_identity() -> None:
    request = phase6_request()
    client = FakeAlpacaClient(
        {
            ("POST", "/v2/orders"): FakeAlpacaResponse(
                payload={
                    "id": "alpaca-order-1",
                    "client_order_id": request.client_order_id,
                    "status": "accepted",
                    "filled_qty": "0",
                    "api_secret": "do-not-return",
                }
            )
        }
    )
    broker = broker_with(client)

    ack = broker.submit_order(request)

    post = client.calls[0]
    assert post["method"] == "POST"
    assert post["path"] == "/v2/orders"
    assert post["timeout"] == 4.0
    assert post["json"] == {
        "symbol": "SPY",
        "qty": str(request.quantity),
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": str(request.limit_price),
        "client_order_id": request.client_order_id,
        "extended_hours": False,
    }
    assert request.client_order_id.startswith(f"wca-{ACCOUNT_ID}-")
    assert len(request.client_order_id) <= WCA_ALPACA_CLIENT_ORDER_ID_LIMIT
    assert ack.status == "ACKNOWLEDGED"
    assert ack.broker_order_id == "alpaca-order-1"
    assert ack.response_payload["api_secret"] == "***REDACTED***"
    assert "do-not-return" not in str(ack.model_dump(mode="json"))


def test_uncertain_submit_timeout_reconciles_by_client_order_id_without_post_retry() -> None:
    request = phase6_request()
    client = FakeAlpacaClient(
        queue=[
            httpx.TimeoutException("submission timed out"),
            FakeAlpacaResponse(status_code=404, payload={"message": "not found"}),
        ]
    )
    broker = broker_with(client)

    with pytest.raises(WcaPaperBrokerTimeout, match="submission_uncertain_reconciliation_required"):
        broker.submit_order(request)

    assert [call["method"] for call in client.calls] == ["POST", "GET"]
    assert client.calls[1]["path"] == "/v2/orders:by_client_order_id"
    assert client.calls[1]["params"] == {"client_order_id": request.client_order_id}


def test_rejection_and_partial_fill_are_mapped_to_acknowledgement_contracts() -> None:
    rejected_request = phase6_request(suffix="rejected")
    rejected = broker_with(
        FakeAlpacaClient({("POST", "/v2/orders"): FakeAlpacaResponse(payload={"id": "alpaca-rejected", "client_order_id": rejected_request.client_order_id, "status": "rejected"})})
    ).submit_order(rejected_request)

    partial_request = phase6_request(suffix="partial")
    partial = broker_with(
        FakeAlpacaClient(
            {
                ("POST", "/v2/orders"): FakeAlpacaResponse(
                    payload={
                        "id": "alpaca-partial",
                        "client_order_id": partial_request.client_order_id,
                        "status": "partially_filled",
                        "qty": str(partial_request.quantity),
                        "filled_qty": "2",
                        "filled_avg_price": str(partial_request.limit_price),
                    }
                )
            }
        )
    ).submit_order(partial_request)

    assert rejected.status == "REJECTED"
    assert rejected.accepted_quantity == 0
    assert partial.status == "ACKNOWLEDGED"
    assert partial.fill is not None
    assert partial.fill.filled_quantity == 2
    assert partial.fill.remaining_quantity == partial_request.quantity - 2


def test_alpaca_adapter_supports_required_read_replace_cancel_fill_poll_and_reduce_operations() -> None:
    request = phase6_request()
    order_row = {
        "id": "alpaca-order-ops",
        "client_order_id": request.client_order_id,
        "symbol": "SPY",
        "side": "buy",
        "type": "limit",
        "status": "accepted",
        "qty": str(request.quantity),
        "filled_qty": "0",
        "limit_price": str(request.limit_price),
        "submitted_at": "2026-01-06T15:00:00Z",
    }
    position_row = {
        "symbol": "SPY",
        "qty": "5",
        "avg_entry_price": "100",
        "current_price": "101",
    }
    client = FakeAlpacaClient(
        {
            ("GET", "/v2/account"): FakeAlpacaResponse(payload={"account_number": ACCOUNT_ID, "equity": "100000", "buying_power": "50000", "realized_intraday_pl": "12.5"}),
            ("GET", "/v2/positions"): FakeAlpacaResponse(payload=[position_row]),
            ("GET", "/v2/orders"): FakeAlpacaResponse(payload=[order_row, order_row | {"client_order_id": "other-order"}]),
            ("GET", "/v2/orders/alpaca-order-ops"): FakeAlpacaResponse(payload=order_row),
            ("GET", "/v2/orders:by_client_order_id"): FakeAlpacaResponse(payload=order_row),
            ("PATCH", "/v2/orders/alpaca-order-ops"): FakeAlpacaResponse(payload=order_row | {"limit_price": "100.25"}),
            ("DELETE", "/v2/orders/alpaca-order-ops"): FakeAlpacaResponse(status_code=204),
            ("GET", "/v2/account/activities/FILL"): FakeAlpacaResponse(payload=[{"id": "fill-ops", "client_order_id": request.client_order_id, "order_id": "alpaca-order-ops", "qty": "5", "price": "100.1", "transaction_time": "2026-01-06T15:01:00Z"}]),
            ("POST", "/v2/orders"): FakeAlpacaResponse(payload={"id": "alpaca-reduce", "client_order_id": "wca-close-phase6", "status": "accepted", "filled_qty": "0"}),
        }
    )
    broker = broker_with(client)

    verified, _ = broker.verify_account_and_endpoint_identity()
    snapshot = broker.refresh_account_snapshot()
    broker_order = broker.read_order_by_broker_id("alpaca-order-ops")
    found = broker.find_order_by_client_order_id(request.client_order_id)
    replaced = broker.replace_order("alpaca-order-ops", request)
    cancelled = broker.cancel_all_wca_entry_orders()
    fills = broker.read_fills_and_activities(after=datetime(2026, 1, 6, tzinfo=timezone.utc))
    stream_status = broker.subscribe_trade_updates()
    polled = broker.poll_order_updates(request.client_order_id)
    reduce_ack = broker.close_or_reduce_wca_position(symbol="SPY", quantity=1, side=WcaSide.BUY, client_order_id="wca-close-phase6")

    assert verified is True
    assert snapshot.accountId == ACCOUNT_ID
    assert snapshot.positions[0].quantity == 5
    assert snapshot.pendingOrders[0].clientOrderId == request.client_order_id
    assert broker_order["id"] == "alpaca-order-ops"
    assert found is not None
    assert replaced.broker_order_id == "alpaca-order-ops"
    assert cancelled == ({},)
    assert fills[0].fill_id == "fill-ops"
    assert stream_status == (WCA_ALPACA_ORDER_STREAM_UNAVAILABLE,)
    assert polled is not None
    assert reduce_ack.broker_order_id == "alpaca-reduce"


def test_existing_outbox_persists_decision_intent_idempotency_client_and_broker_mapping() -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    decision = decision_with_order("phase6-map-decision", "phase6-map-intent", "phase6-map-key")
    assert decision.proposed_order is not None
    proposed = decision.proposed_order.model_copy(update={"account_id": ACCOUNT_ID})
    decision = decision.model_copy(update={"proposed_order": proposed})
    request = build_wca_paper_broker_request(proposed)
    repository.reserve_decision_order_and_outbox(
        decision,
        run_id="phase6-map-run",
        account_id=ACCOUNT_ID,
        idempotency_key=request.idempotency_key,
        client_order_id=request.client_order_id,
        request_payload=request.model_dump(mode="json"),
        final_validation_context=phase6_validation_context(decision, request),
    )
    client = FakeAlpacaClient(
        {
            ("POST", "/v2/orders"): FakeAlpacaResponse(
                payload={
                    "id": "alpaca-map-order",
                    "client_order_id": request.client_order_id,
                    "status": "accepted",
                    "filled_qty": "0",
                }
            )
        }
    )

    result = WcaPaperBrokerOutboxAdapter().process_next_outbox(repository, broker_with(client), owner_id="phase6")

    assert result.state == WcaOrderStatus.BROKER_ACKNOWLEDGED.value
    with sqlite3.connect(repository.path) as conn:
        outbox = conn.execute(
            """
            SELECT decision_id, order_intent_id, idempotency_key, client_order_id, status
            FROM wca_execution_outbox
            WHERE idempotency_key = ?
            """,
            (request.idempotency_key,),
        ).fetchone()
        broker_order = conn.execute(
            """
            SELECT broker_order_id, decision_id, order_intent_id, idempotency_key, client_order_id, status
            FROM wca_broker_orders
            WHERE broker_order_id = ?
            """,
            ("alpaca-map-order",),
        ).fetchone()

    assert outbox == (decision.decision_id, proposed.order_intent_id, request.idempotency_key, request.client_order_id, WcaOrderStatus.BROKER_ACKNOWLEDGED.value)
    assert broker_order == ("alpaca-map-order", decision.decision_id, proposed.order_intent_id, request.idempotency_key, request.client_order_id, WcaOrderStatus.BROKER_ACKNOWLEDGED.value)


def test_runtime_blocks_automatic_paper_when_alpaca_adapter_is_unavailable_without_deterministic_fallback() -> None:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    runtime_repository = WcaRuntimeRepository(repository)
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(account_id=ACCOUNT_ID),
        owner_id="phase6-runtime",
    )
    decision = decision_with_order("phase6-runtime-decision", "phase6-runtime-intent", "phase6-runtime-key")
    command = runtime_command(
        WcaRuntimeCommandType.EXECUTION_OUTBOX,
        account_id=ACCOUNT_ID,
        decision_id=decision.decision_id,
        run_id="phase6-runtime-run",
        payload={"decision": decision.model_dump(mode="json"), "configuration_version": decision.configuration_version},
    )
    runtime_repository.enqueue_command(command)

    with patch.dict("os.environ", valid_env(), clear=True), patch.object(
        repository,
        "reserve_decision_order_and_outbox",
        return_value=SimpleNamespace(outbox_id="phase6-outbox"),
    ), patch(
        "backend.app.algorithms.wca.runtime_supervisor.WcaAlpacaPaperBroker.from_env",
        side_effect=WcaAlpacaPaperBrokerConfigurationError("wca.alpaca_paper.account_id_mismatch"),
    ), patch.object(
        WcaPaperBrokerOutboxAdapter,
        "process_next_outbox",
        side_effect=AssertionError("deterministic broker fallback attempted"),
    ):
        result = next(worker for worker in supervisor.workers if worker.worker_name == "execution_outbox_worker").run_once()

    assert result["status"] == "blocked"
    assert result["submitted"] is False
    assert "wca.runtime.execution_outbox.alpaca_paper_broker_blocked" in result["reasonCodes"]
    assert "wca.alpaca_paper.account_id_mismatch" in result["reasonCodes"]


def phase6_request(*, suffix: str = "accepted"):
    decision = decision_with_order(f"phase6-{suffix}-decision", f"phase6-{suffix}-intent", f"phase6-{suffix}-key")
    assert decision.proposed_order is not None
    proposed = decision.proposed_order.model_copy(update={"account_id": ACCOUNT_ID})
    return build_wca_paper_broker_request(proposed)


def broker_with(client: FakeAlpacaClient) -> WcaAlpacaPaperBroker:
    return WcaAlpacaPaperBroker(
        account_id=ACCOUNT_ID,
        key_id="wca-key",
        secret_key="wca-secret",
        http_client=client,
    )


def phase6_validation_context(decision, request) -> WcaOrderValidationContext:
    return WcaOrderValidationContext(
        evaluation_timestamp=decision.decision_timestamp,
        account_id=ACCOUNT_ID,
        broker_endpoint="paper",
        runtime_mode=WcaRuntimeMode.AUTOMATIC_PAPER,
        requires_executable_paper_stage=True,
        automatic_paper_enabled=True,
        market_is_open=True,
        allowed_session_window=True,
        data_ready=decision.market_snapshot.data_ready,
        quote_freshness_seconds=15,
        candle_freshness_seconds=120,
        available_buying_power=100_000,
        account_equity=100_000,
        max_position_value=100_000,
        max_approved_quantity=1000,
        order_type=request.order_type,
        time_in_force=request.time_in_force,
        protective_exit_plan_present=True,
        idempotency_required=True,
    )


def valid_env() -> dict[str, str]:
    return {
        WCA_ALPACA_PAPER_API_KEY_ID: "wca-key",
        WCA_ALPACA_PAPER_API_SECRET_KEY: "wca-secret",
        WCA_ALPACA_PAPER_BASE_URL: WCA_REQUIRED_ALPACA_PAPER_BASE_URL,
        WCA_ALPACA_PAPER_ACCOUNT_ID: ACCOUNT_ID,
        WCA_AUTOMATIC_PAPER_ENABLED: "true",
    }
