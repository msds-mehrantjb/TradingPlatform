from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx

from backend.app.algorithms.regime.execution_gateway import validate_regime_paper_broker_safety
from backend.app.algorithms.regime.account_snapshot import REGIME_ACCOUNT_SNAPSHOT_SOURCE_AUTHORITY
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_factory import RegimeAlpacaPaperBroker, build_regime_paper_runtime
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.config import ApplicationConfig, Settings
from backend.app.execution import PaperGatewayBrokerAck


ROOT = Path(__file__).resolve().parents[3]
TEST_TMP_ROOT = ROOT / "backend" / ".pytest_regime_runtime_factory"


def test_phase23_factory_composes_explicit_regime_paper_runtime() -> None:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    repository = RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")
    service = RegimeApplicationService(repository)
    config = RegimeRuntimeSupervisorConfig(
        default_algorithm_instance_id="regime-paper-default",
        default_account_id="paper-account-123",
        default_runtime_mode="paper",
        symbol="SPY",
        heartbeat_interval_seconds=7,
        maintenance_interval_seconds=11,
        execution_interval_seconds=13,
        reconciliation_interval_seconds=17,
        publisher_interval_seconds=19,
    )
    broker = _VerifiedBroker()

    supervisor = build_regime_paper_runtime(service=service, config=config, broker=broker)
    account = supervisor.account_snapshot_provider(
        {
            "algorithmId": "regime",
            "algorithmInstanceId": "regime-paper-default",
            "accountId": "paper-account-123",
            "runtimeMode": "paper",
            "symbol": "SPY",
        }
    )
    broker_safety = validate_regime_paper_broker_safety(supervisor.paper_gateway, mode="paper")

    assert supervisor.service is service
    assert supervisor.config.default_runtime_mode == "paper"
    assert supervisor.config.default_algorithm_instance_id == "regime-paper-default"
    assert supervisor.config.default_account_id == "paper-account-123"
    assert supervisor.config.symbol == "SPY"
    assert supervisor.config.execution_interval_seconds == 13
    assert supervisor.config.reconciliation_interval_seconds == 17
    assert supervisor.config.publisher_interval_seconds == 19
    assert supervisor.config.execution_poll_interval_seconds == 13
    assert supervisor.config.reconciliation_poll_interval_seconds == 17
    assert supervisor.config.publisher_poll_interval_seconds == 19
    assert supervisor.paper_gateway is not None
    assert supervisor.paper_gateway.broker is broker
    assert account["sourceAuthority"] == REGIME_ACCOUNT_SNAPSHOT_SOURCE_AUTHORITY
    assert account["buyingPowerCurrent"] is True
    assert account["globalRiskCapacityQuantity"] >= 0
    assert broker_safety["verified"] is True
    assert supervisor.runtime_factory_diagnostics["dependencyBlockers"] == []


def test_phase23_factory_fails_closed_without_configured_paper_dependencies(monkeypatch) -> None:
    monkeypatch.delenv("REGIME_ALPACA_PAPER_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("REGIME_PAPER_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_ACCOUNT_ID", raising=False)
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    repository = RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")
    service = RegimeApplicationService(repository)

    supervisor = build_regime_paper_runtime(service=service, settings=_settings_without_alpaca())

    assert supervisor.config.default_runtime_mode == "paper"
    assert supervisor.config.default_algorithm_instance_id == "regime-paper-default"
    assert supervisor.config.default_account_id == "regime-paper-account-unconfigured"
    assert supervisor.config.symbol == "SPY"
    assert supervisor.paper_gateway is not None
    assert "regime.runtime_factory.paper_account_id_unconfigured" in supervisor.metrics.entry_block_reason_codes
    assert "regime.runtime_factory.alpaca_paper_credentials_or_account_unavailable" in supervisor.metrics.entry_block_reason_codes
    assert supervisor.metrics.broker_paper_mode_verified is False
    assert supervisor.metrics.broker_connectivity_ok is False
    assert supervisor.runtime_factory_diagnostics["runtimeMode"] == "paper"


def test_phase23_production_startup_uses_runtime_factory_not_implicit_supervisor() -> None:
    main_source = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    factory_source = (ROOT / "backend" / "app" / "algorithms" / "regime" / "runtime_factory.py").read_text(encoding="utf-8")

    assert "from .algorithms.regime.runtime_factory import get_regime_runtime_supervisor" in main_source
    assert "from .algorithms.regime.runtime_supervisor import get_regime_runtime_supervisor" not in main_source
    assert "RegimeRuntimeSupervisor(" in factory_source
    assert "RegimeApplicationService()" in factory_source
    assert "PaperOrderGateway(" in factory_source
    assert "RegimeAlpacaPaperBroker(" in factory_source


def test_phase23_alpaca_paper_broker_verifies_endpoint_account_and_trading_permission() -> None:
    client = _FakeAlpacaHttpClient(
        account={
            "id": "paper-account-123",
            "account_type": "paper",
            "status": "ACTIVE",
            "trading_blocked": False,
            "account_blocked": False,
            "trade_suspended_by_user": False,
            "equity": "100000",
            "buying_power": "90000",
        }
    )
    broker = RegimeAlpacaPaperBroker(account_id="paper-account-123", settings=_settings_with_alpaca(), http_client=client)

    verification = broker.startup_verification()
    configuration_json = json.dumps(broker.paper_trading_configuration(), sort_keys=True)

    assert verification["verified"] is True
    assert verification["accountEndpointResponsive"] is True
    assert verification["accountMatchesConfiguredIdentity"] is True
    assert verification["accountAllowedToTrade"] is True
    assert verification["marketDataCredentialsConfigured"] is True
    assert "paper-secret" not in configuration_json
    assert "paper-key" not in configuration_json


def test_phase23_alpaca_paper_broker_rejects_live_url_and_trading_blocked_account() -> None:
    live_broker = RegimeAlpacaPaperBroker(account_id="paper-account-123", settings=_settings_with_alpaca(trading_url="https://api.alpaca.markets/v2"), http_client=_FakeAlpacaHttpClient())
    blocked_broker = RegimeAlpacaPaperBroker(
        account_id="paper-account-123",
        settings=_settings_with_alpaca(),
        http_client=_FakeAlpacaHttpClient(account={"id": "paper-account-123", "account_type": "paper", "status": "ACTIVE", "trading_blocked": True}),
    )

    assert "regime.alpaca_paper.paper_endpoint_required" in live_broker.startup_verification()["reasonCodes"]
    blocked = blocked_broker.startup_verification()
    assert blocked["verified"] is False
    assert "regime.alpaca_paper.account_not_allowed_to_trade" in blocked["reasonCodes"]


def test_phase23_alpaca_paper_broker_recovers_uncertain_submission_by_client_order_id() -> None:
    client = _FakeAlpacaHttpClient(
        timeout_on_post=True,
        account={"id": "paper-account-123", "account_type": "paper", "status": "ACTIVE", "trading_blocked": False},
        order={"id": "alpaca-order-1", "client_order_id": "paper-deterministic-1", "status": "new", "symbol": "SPY", "side": "buy", "qty": "3", "filled_qty": "0"},
    )
    broker = RegimeAlpacaPaperBroker(account_id="paper-account-123", settings=_settings_with_alpaca(), http_client=client)

    ack = broker.submit_bracket_order(_intent(client_order_id="paper-deterministic-1"))

    assert ack.status == "ACCEPTED"
    assert ack.clientOrderId == "paper-deterministic-1"
    assert ack.brokerOrderId == "alpaca-order-1"
    assert client.last_post_json["client_order_id"] == "paper-deterministic-1"


def test_phase23_alpaca_paper_broker_supports_order_status_cancel_open_orders_fills_and_positions() -> None:
    client = _FakeAlpacaHttpClient(
        account={"id": "paper-account-123", "account_type": "paper", "status": "ACTIVE", "trading_blocked": False},
        order={"id": "alpaca-order-1", "client_order_id": "paper-deterministic-1", "status": "filled", "symbol": "SPY", "side": "buy", "qty": "3", "filled_qty": "3", "filled_avg_price": "501.25"},
        open_orders=[{"id": "alpaca-order-2", "client_order_id": "paper-open-1", "status": "new", "symbol": "SPY", "side": "buy", "qty": "1", "filled_qty": "0"}],
        fills=[{"id": "fill-1", "order_id": "alpaca-order-1", "client_order_id": "paper-deterministic-1", "symbol": "SPY", "side": "buy", "qty": "3", "price": "501.25"}],
        positions=[{"symbol": "SPY", "qty": "3", "avg_entry_price": "501.25", "market_value": "1503.75"}],
    )
    broker = RegimeAlpacaPaperBroker(account_id="paper-account-123", settings=_settings_with_alpaca(), http_client=client)

    assert broker.order_status("paper-deterministic-1")["status"] == "FILLED"
    assert broker.refresh_order("paper-deterministic-1").filledQuantity == 3
    assert broker.cancel_order("paper-deterministic-1") is True
    assert broker.refresh_open_orders()[0]["clientOrderId"] == "paper-open-1"
    assert broker.refresh_fills()[0]["fillId"] == "fill-1"
    assert broker.refresh_positions()[0]["quantity"] == 3


def test_phase23_runtime_status_exposes_exact_paper_broker_blockers() -> None:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    repository = RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")
    service = RegimeApplicationService(repository)
    supervisor = build_regime_paper_runtime(
        service=service,
        settings=_settings_with_alpaca(trading_url="https://api.alpaca.markets/v2"),
    )

    supervisor._verify_paper_broker_mode()
    status = supervisor.status()

    assert "regime.alpaca_paper.paper_endpoint_required" in status["entry_block_reason_codes"]
    assert "regime.execution.paper_broker.live_trading_enabled" in status["entry_block_reason_codes"]
    assert status["component_health"]["paper_broker"]["status"] == "unhealthy"


class _VerifiedBroker:
    broker_kind = "regime_alpaca_paper"
    base_url = "https://paper-api.alpaca.markets/v2"
    paper_only = True
    live_trading_enabled = False
    account_type = "paper"
    credentials_verified = True

    def verify_paper_account(self) -> bool:
        return True

    def paper_trading_configuration(self) -> dict:
        return {
            "brokerKind": self.broker_kind,
            "baseUrl": self.base_url,
            "paperOnly": True,
            "liveTradingEnabled": False,
            "accountType": "paper",
            "configured": True,
            "credentialsVerified": True,
        }

    def refresh_account_snapshot(self) -> dict:
        return {
            "sourceAuthority": "broker",
            "accountId": "paper-account-123",
            "equity": 100_000.0,
            "buyingPower": 100_000.0,
            "availableBuyingPower": 100_000.0,
            "accountSnapshotFresh": True,
            "buyingPowerCurrent": True,
            "positionsReconciled": True,
            "openOrdersReconciled": True,
        }

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        return PaperGatewayBrokerAck(clientOrderId=intent.clientOrderId, brokerOrderId="paper-test", status="ACCEPTED")

    def refresh_order(self, client_order_id: str):
        return None

    def cancel_order(self, client_order_id: str) -> bool:
        return True

    def refresh_positions(self) -> list[dict]:
        return []


def _settings_without_alpaca() -> Settings:
    return Settings(
        alpaca_key_id="",
        alpaca_secret_key="",
        alpaca_data_base_url="https://data.alpaca.markets/v2",
        alpaca_trading_base_url="https://paper-api.alpaca.markets/v2",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3",
        database_url="sqlite:///./data/trading.db",
        allowed_origins=[],
        application_config=ApplicationConfig(),
    )


def _settings_with_alpaca(*, trading_url: str = "https://paper-api.alpaca.markets/v2") -> Settings:
    return Settings(
        alpaca_key_id="paper-key",
        alpaca_secret_key="paper-secret",
        alpaca_data_base_url="https://data.alpaca.markets/v2",
        alpaca_trading_base_url=trading_url,
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3",
        database_url="sqlite:///./data/trading.db",
        allowed_origins=[],
        application_config=ApplicationConfig(),
    )


def _intent(*, client_order_id: str = "paper-deterministic-1"):
    return SimpleNamespace(
        clientOrderId=client_order_id,
        symbol="SPY",
        submittedQuantity=3,
        side="BUY",
        orderType="LIMIT",
        timeInForce="DAY",
        limitPrice=501.0,
        stopPrice=499.0,
        stopLimitPrice=None,
        targetPrice=504.0,
    )


class _FakeAlpacaHttpResponse:
    def __init__(self, payload, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://paper-api.alpaca.markets/v2/test")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("fake alpaca error", request=request, response=response)


class _FakeAlpacaHttpClient:
    def __init__(
        self,
        *,
        account: dict | None = None,
        order: dict | None = None,
        open_orders: list[dict] | None = None,
        fills: list[dict] | None = None,
        positions: list[dict] | None = None,
        timeout_on_post: bool = False,
    ) -> None:
        self.account = account or {}
        self.order = order or {}
        self.open_orders = open_orders or []
        self.fills = fills or []
        self.positions = positions or []
        self.timeout_on_post = timeout_on_post
        self.last_post_json: dict | None = None

    def get(self, url: str, **kwargs):
        if url.endswith("/account"):
            return _FakeAlpacaHttpResponse(self.account)
        if "orders:by_client_order_id" in url:
            return _FakeAlpacaHttpResponse(self.order if self.order else {"message": "not found"}, status_code=200 if self.order else 404)
        if url.endswith("/orders"):
            return _FakeAlpacaHttpResponse(self.open_orders)
        if url.endswith("/account/activities/FILL"):
            return _FakeAlpacaHttpResponse(self.fills)
        if url.endswith("/positions"):
            return _FakeAlpacaHttpResponse(self.positions)
        return _FakeAlpacaHttpResponse({})

    def post(self, url: str, **kwargs):
        self.last_post_json = dict(kwargs.get("json") or {})
        if self.timeout_on_post:
            raise httpx.TimeoutException("timeout")
        return _FakeAlpacaHttpResponse({"id": "alpaca-new", "client_order_id": self.last_post_json.get("client_order_id"), "status": "new"})

    def delete(self, url: str, **kwargs):
        return _FakeAlpacaHttpResponse({}, status_code=204)
