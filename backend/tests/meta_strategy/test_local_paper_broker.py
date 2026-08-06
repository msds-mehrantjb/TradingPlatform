from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from backend.app.algorithms.meta_strategy.local_paper_broker import MetaStrategyLocalPaperBroker
from backend.app.algorithms.meta_strategy.local_ledger_paper_broker import MetaStrategyLocalLedgerPaperBroker
from backend.app.algorithms.meta_strategy.repository import MetaStrategyRepositoryPersistenceAdapter, MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.runtime import (
    MetaStrategyRuntimeDependencies,
    MetaStrategyRuntimeMode,
    validate_meta_strategy_runtime_startup,
)
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore, build_meta_strategy_settings
from backend.app.algorithms.meta_strategy.worker_main import _require_paper_gateway
from backend.app.domain.models import Signal
from backend.app.execution import PaperOrderGateway, PaperOrderIntentRecord
from backend.app.gates import GlobalOrderProposal


NOW = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)


def test_local_paper_broker_rejects_live_account_flag() -> None:
    client = FakeHttpClient({("GET", "/account"): {"accountId": "paper-local", "liveTradingEnabled": True}})
    broker = MetaStrategyLocalPaperBroker(base_url="http://local-paper.test", http_client=client)

    assert broker.verify_paper_account() is False


def test_local_paper_broker_loads_authoritative_account_and_risk_snapshot() -> None:
    client = FakeHttpClient(
        {
            ("GET", "/account"): {
                "accountId": "paper-local",
                "accountEquity": 0,
                "buyingPower": 25000,
                "cashAvailable": 1000,
                "capturedAt": NOW.isoformat(),
                "accountType": "paper",
            },
            ("GET", "/risk/snapshot"): {
                "availableRiskDollars": 0,
                "maxQuantity": 0,
                "capturedAt": NOW.isoformat(),
                "reasonCodes": ["local.risk.loaded"],
            },
        }
    )
    broker = MetaStrategyLocalPaperBroker(base_url="http://local-paper.test", http_client=client)

    account = broker.read_account_snapshot(at=NOW)
    risk = broker.read_global_risk_snapshot(at=NOW, capital_partition_id="meta_strategy.paper.default")

    assert account is not None
    assert account["authoritativeReadOnly"] is True
    assert account["accountEquity"] == 0
    assert account["buyingPower"] == 25000
    assert account["paperAccountVerified"] is True
    assert risk is not None
    assert risk["authoritativeReadOnly"] is True
    assert risk["availableRiskDollars"] == 0
    assert risk["maxQuantity"] == 0


def test_local_paper_broker_maps_risk_approval_without_real_network() -> None:
    client = FakeHttpClient(
        {
            ("POST", "/risk/approve"): {
                "action": "APPROVED",
                "maximumAllowedQuantity": 7,
                "maximumAdditionalRiskDollars": 125.5,
                "evaluatedAt": NOW.isoformat(),
                "configurationHash": "local-risk-v1",
            }
        }
    )
    broker = MetaStrategyLocalPaperBroker(base_url="http://local-paper.test", http_client=client)

    response = broker.approve_order(_proposal(quantity=10, planned_risk=200.0))

    assert response.action == "ALLOW"
    assert response.maximumAllowedQuantity == 7
    assert response.maximumAdditionalRiskDollars == 125.5
    assert client.posts[0][0] == "http://local-paper.test/risk/approve"
    assert client.posts[0][1]["algorithmId"] == "meta_strategy"
    assert client.posts[0][1]["capitalPartitionId"] == "meta_strategy.paper.default"


def test_local_paper_broker_submits_paper_only_meta_strategy_order_envelope() -> None:
    client = FakeHttpClient(
        {
            ("POST", "/orders"): {
                "clientOrderId": "client-1",
                "brokerOrderId": "broker-1",
                "status": "accepted",
                "acceptedAt": NOW.isoformat(),
            }
        }
    )
    broker = MetaStrategyLocalPaperBroker(base_url="http://local-paper.test", http_client=client)

    ack = broker.submit_bracket_order(_intent())

    assert ack.status == "ACCEPTED"
    url, body = client.posts[0]
    assert url == "http://local-paper.test/orders"
    assert body["algorithmId"] == "meta_strategy"
    assert body["capitalPartitionId"] == "meta_strategy.paper.default"
    assert body["paperOnly"] is True
    assert body["liveTradingEnabled"] is False


def test_local_paper_broker_reads_order_and_position_arrays_for_reconciliation() -> None:
    client = FakeHttpClient(
        {
            ("GET", "/orders"): [
                {
                    "clientOrderId": "client-1",
                    "brokerOrderId": "broker-1",
                    "orderIntentId": "intent-1",
                    "symbol": "SPY",
                    "side": "buy",
                    "status": "filled",
                    "filledQuantity": 3,
                    "averageFillPrice": 500.0,
                    "timestamp": NOW.isoformat(),
                }
            ],
            ("GET", "/positions"): [{"symbol": "SPY", "qty": 3}],
        }
    )
    broker = MetaStrategyLocalPaperBroker(base_url="http://local-paper.test", http_client=client)

    events = broker.list_order_events()
    positions = broker.refresh_positions()

    assert events[0]["algorithmId"] == "meta_strategy"
    assert events[0]["status"] == "FILLED"
    assert positions == [{"symbol": "SPY", "qty": 3}]


def test_runtime_and_worker_accept_configured_local_paper_gateway() -> None:
    database_url = f"sqlite:///{_temp_db_path('local-paper-runtime')}"
    inventory = MetaStrategySqliteRepository(database_url)
    settings_store = MetaStrategySettingsStore(_temp_db_path("local-paper-settings"))
    baseline = settings_store.create_baseline(build_meta_strategy_settings(settings_version="local-paper-settings"), actor="test")
    settings_store.activate_settings(baseline.settings_version, actor="test")
    broker = ConfiguredLocalPaperBroker()
    gateway = PaperOrderGateway(broker, FakeGatewayStore())

    report = validate_meta_strategy_runtime_startup(
        MetaStrategyRuntimeDependencies(
            mode=MetaStrategyRuntimeMode.PAPER,
            persistence_adapter=MetaStrategyRepositoryPersistenceAdapter(inventory),
            broker_adapter=broker,
            inventory_repository=inventory,
            job_repository=object(),
            settings_store=settings_store,
            account_data_source=object(),
            global_risk_source=object(),
            operational_health_source=object(),
        )
    )

    assert report.ready is True
    assert _require_paper_gateway(gateway) is gateway


def test_local_ledger_paper_broker_verifies_writable_paper_ledger_and_never_live() -> None:
    store = FakeGatewayStore()
    broker = MetaStrategyLocalLedgerPaperBroker(store)

    assert broker.verify_paper_account() is True
    account = store.snapshots["meta_strategy.local_ledger.paper_account"]
    assert account["paperOnly"] is True
    assert account["liveTradingEnabled"] is False


def test_local_ledger_paper_broker_records_ack_fill_event_and_position_for_management() -> None:
    store = FakeGatewayStore()
    broker = MetaStrategyLocalLedgerPaperBroker(store, immediate_fills=True)

    ack = broker.submit_bracket_order(_intent())
    fill = broker.refresh_order("client-1")
    events = broker.list_order_events()
    positions = broker.refresh_positions()

    assert ack.status == "ACCEPTED"
    assert ack.brokerOrderId is not None
    assert fill is not None
    assert fill.algorithmId == "meta_strategy"
    assert fill.orderIntentId == "intent-1"
    assert fill.filledQuantity == 3
    assert any(event["status"] == "FILLED" for event in events)
    assert positions == [
        {
            "algorithmId": "meta_strategy",
            "capitalPartitionId": "meta_strategy.paper.default",
            "clientOrderId": "client-1",
            "brokerOrderId": ack.brokerOrderId,
            "symbol": "SPY",
            "quantity": 3,
            "side": "BUY",
            "averagePrice": 500.0,
            "paperOnly": True,
            "updatedAt": positions[0]["updatedAt"],
        }
    ]


def test_worker_accepts_configured_local_ledger_paper_gateway() -> None:
    gateway = PaperOrderGateway(MetaStrategyLocalLedgerPaperBroker(FakeGatewayStore()), FakeGatewayStore())

    assert _require_paper_gateway(gateway) is gateway


def _proposal(*, quantity: int = 1, planned_risk: float = 1.0) -> GlobalOrderProposal:
    return GlobalOrderProposal(
        algorithmId="meta_strategy",
        capitalPartitionId="meta_strategy.paper.default",
        decisionId="decision-1",
        orderIntentId="intent-1",
        intent="new_entry",
        symbol="SPY",
        side=Signal.BUY,
        quantity=quantity,
        limitPrice=500.0,
        stopPrice=499.0,
        targetPrice=502.0,
        plannedRiskDollars=planned_risk,
        strategyStateHash="state",
        proposedAt=NOW,
        sessionDate=date(2026, 8, 5),
        configurationHash="proposal",
    )


def _intent() -> PaperOrderIntentRecord:
    return PaperOrderIntentRecord(
        algorithmId="meta_strategy",
        capitalPartitionId="meta_strategy.paper.default",
        decisionId="decision-1",
        orderIntentId="intent-1",
        clientOrderId="client-1",
        mode="automatic",
        symbol="SPY",
        side=Signal.BUY,
        proposedQuantity=3,
        globallyAllowedQuantity=3,
        submittedQuantity=3,
        orderType="MARKETABLE_LIMIT",
        timeInForce="DAY",
        limitPrice=500.0,
        stopPrice=499.0,
        targetPrice=502.0,
        plannedRiskDollars=3.0,
        globalAction="ALLOW",
        localGatePassed=True,
        globalGatePassed=True,
        paperAccountVerified=True,
        createdAt=NOW,
        decisionTimestamp=NOW,
    )


def _temp_db_path(prefix: str) -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"{prefix}-{uuid4().hex}.sqlite"


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("fake http error", request=httpx.Request("GET", "http://local-paper.test"), response=httpx.Response(self.status_code))


class FakeHttpClient:
    def __init__(self, responses: dict[tuple[str, str], object]) -> None:
        self.responses = responses
        self.posts: list[tuple[str, dict]] = []

    def get(self, url: str, *, headers=None, params=None) -> FakeResponse:
        path = "/" + url.split("/", 3)[-1].split("?", 1)[0] if "://" in url else url
        return FakeResponse(self.responses[("GET", path)])

    def post(self, url: str, *, headers=None, json=None) -> FakeResponse:
        path = "/" + url.split("/", 3)[-1].split("?", 1)[0] if "://" in url else url
        self.posts.append((url, dict(json or {})))
        return FakeResponse(self.responses[("POST", path)])

    def patch(self, url: str, *, headers=None, json=None) -> FakeResponse:
        path = "/" + url.split("/", 3)[-1].split("?", 1)[0] if "://" in url else url
        return FakeResponse(self.responses[("PATCH", path)])

    def delete(self, url: str, *, headers=None) -> FakeResponse:
        path = "/" + url.split("/", 3)[-1].split("?", 1)[0] if "://" in url else url
        return FakeResponse(self.responses.get(("DELETE", path), {}), status_code=204)


class ConfiguredLocalPaperBroker:
    broker_kind = "local_paper"
    configured = True
    paper_endpoint = True

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent):
        raise AssertionError("test does not submit")

    def refresh_order(self, client_order_id: str):
        return None

    def cancel_order(self, client_order_id: str) -> bool:
        return False

    def refresh_positions(self):
        return []


class FakeGatewayStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot
