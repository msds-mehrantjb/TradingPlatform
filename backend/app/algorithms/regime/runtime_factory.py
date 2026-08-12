"""Explicit production composition root for the Regime paper runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

import httpx

from backend.app.algorithms.regime.account_snapshot import build_regime_authoritative_account_snapshot_provider, normalize_regime_account_snapshot
from backend.app.algorithms.regime.contracts import (
    REGIME_UNCONFIGURED_PAPER_ACCOUNT_ID,
    RegimeRuntimeMode,
)
from backend.app.algorithms.regime.execution_gateway import RegimePaperGatewayStore
from backend.app.algorithms.regime.local_paper_broker import (
    REGIME_LOCAL_PAPER_BROKER_VERSION,
    RegimeLocalPaperBroker,
)
from backend.app.algorithms.regime.global_risk_adapter import REGIME_SHARED_GLOBAL_RISK_MANAGER
from backend.app.algorithms.regime.local_paper_account import REGIME_DEFAULT_LOCAL_PAPER_INITIAL_BALANCE
from backend.app.algorithms.regime.runtime_publisher import RegimeFinalizedOneMinutePublisher, RegimeFinalizedOneMinutePublisherConfig
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.config import Settings, get_settings
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway


REGIME_RUNTIME_FACTORY_VERSION = "regime_runtime_factory_v1"
REGIME_ALPACA_PAPER_BROKER_VERSION = "regime_alpaca_paper_broker_v1"
_PAPER_HOST_MARKER = "paper-api.alpaca.markets"


class RegimeAlpacaPaperSubmissionUncertain(TimeoutError):
    safe_to_retry = True
    submission_uncertain = True


@dataclass(frozen=True)
class RegimeRuntimeFactoryDiagnostics:
    algorithm_id: str
    runtime_mode: str
    algorithm_instance_id: str
    account_id: str
    symbol: str
    dependency_blockers: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithm_id,
            "runtimeMode": self.runtime_mode,
            "algorithmInstanceId": self.algorithm_instance_id,
            "accountId": self.account_id,
            "symbol": self.symbol,
            "dependencyBlockers": list(self.dependency_blockers),
        }

class RegimeAlpacaPaperBroker:
    """Regime-owned adapter that can only target Alpaca Paper endpoints."""

    broker_kind = "regime_alpaca_paper"
    account_type = "paper"

    def __init__(
        self,
        *,
        account_id: str,
        settings: Settings | None = None,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 4.0,
    ) -> None:
        self.settings = settings or get_settings()
        self.account_id = str(account_id or "").strip()
        self.base_url = str(self.settings.alpaca_trading_base_url).rstrip("/")
        self.paper_only = self.paper_endpoint
        self.live_trading_enabled = not self.paper_endpoint
        self.credentials_verified = False
        self.account_endpoint_responsive = False
        self.account_matches_configured_identity = False
        self.account_allowed_to_trade = False
        self.market_data_credentials_configured = self.settings.has_alpaca_credentials and bool(str(self.settings.alpaca_data_base_url or "").strip())
        self.last_verification_reason_codes: tuple[str, ...] = ()
        self._last_account_payload: dict[str, Any] | None = None
        self._owned_client = None if http_client is not None else httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 3.0)), trust_env=False)
        self.client = http_client or self._owned_client

    @property
    def paper_endpoint(self) -> bool:
        normalized = self.base_url.lower()
        return _PAPER_HOST_MARKER in normalized and "api.alpaca.markets" in normalized

    @property
    def configured(self) -> bool:
        return bool(self.paper_endpoint and self.settings.has_alpaca_credentials and self.account_id and self.account_id != REGIME_UNCONFIGURED_PAPER_ACCOUNT_ID)

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()

    def verify_paper_account(self) -> bool:
        return bool(self.startup_verification().get("verified"))

    def startup_verification(self) -> dict[str, Any]:
        reasons = list(self._configuration_reason_codes())
        payload = None if reasons else self._read_account_payload()
        if payload is None:
            if self.configured:
                reasons.append("regime.alpaca_paper.account_endpoint_unavailable")
        else:
            self.account_endpoint_responsive = True
            account_identifier = _account_identifier(payload)
            self.account_matches_configured_identity = bool(account_identifier and account_identifier == self.account_id)
            self.account_allowed_to_trade = _account_allowed_to_trade(payload)
            if not self.account_matches_configured_identity:
                reasons.append("regime.alpaca_paper.account_id_mismatch")
            if not _account_payload_is_paper(payload):
                reasons.append("regime.alpaca_paper.account_type_not_paper")
            if not self.account_allowed_to_trade:
                reasons.append("regime.alpaca_paper.account_not_allowed_to_trade")
        self.credentials_verified = payload is not None and not reasons
        self.last_verification_reason_codes = tuple(dict.fromkeys(reasons))
        return {
            "brokerVersion": REGIME_ALPACA_PAPER_BROKER_VERSION,
            "verified": self.credentials_verified,
            "paperOnly": self.paper_endpoint,
            "liveTradingEnabled": not self.paper_endpoint,
            "accountType": "paper",
            "accountId": self.account_id,
            "configured": self.configured,
            "accountEndpointResponsive": self.account_endpoint_responsive,
            "accountMatchesConfiguredIdentity": self.account_matches_configured_identity,
            "accountAllowedToTrade": self.account_allowed_to_trade,
            "credentialsConfigured": self.settings.has_alpaca_credentials,
            "credentialsVerified": self.credentials_verified,
            "marketDataCredentialsConfigured": self.market_data_credentials_configured,
            "reasonCodes": self.last_verification_reason_codes or ("regime.alpaca_paper.account_verified",),
        }

    def paper_account_payload(self) -> dict[str, Any] | None:
        return self._read_account_payload()

    def _read_account_payload(self) -> dict[str, Any] | None:
        if not self.configured:
            return None
        try:
            response = self.client.get(f"{self.base_url}/account", headers=self._headers())
            response.raise_for_status()
        except (httpx.HTTPError, AttributeError):
            return None
        payload = response.json()
        if not isinstance(payload, Mapping):
            return None
        self._last_account_payload = dict(payload)
        return dict(payload)

    def refresh_account_snapshot(self) -> dict[str, Any]:
        payload = self.paper_account_payload()
        observed_at = _utc_now()
        if payload is None:
            return {
                "sourceAuthority": "broker",
                "accountId": self.account_id,
                "runtimeMode": "paper",
                "equity": 0.0,
                "cash": 0.0,
                "buyingPower": 0.0,
                "availableBuyingPower": 0.0,
                "globalRiskCapacityQuantity": 0,
                "dailyAccountPnl": 0.0,
                "accountSnapshotFresh": False,
                "buyingPowerCurrent": False,
                "positionsReconciled": False,
                "openOrdersReconciled": False,
                "accountTradingBlocked": True,
                "observedAt": observed_at,
                "paperOnly": True,
                "liveTradingEnabled": False,
                "reasonCodes": self._configuration_reason_codes() or ("regime.alpaca_paper.account_unverified",),
            }
        equity = _optional_float(payload.get("equity") or payload.get("portfolio_value") or payload.get("cash")) or 0.0
        cash = _optional_float(payload.get("cash") or payload.get("cash_withdrawable") or payload.get("settled_cash")) or 0.0
        buying_power = _optional_float(payload.get("buying_power") or payload.get("regt_buying_power") or payload.get("cash")) or 0.0
        return {
            "sourceAuthority": "broker",
            "accountId": self.account_id,
            "runtimeMode": "paper",
            "accountSnapshotId": f"regime-alpaca-paper-{self.account_id}-{observed_at}",
            "equity": equity,
            "cash": cash,
            "buyingPower": buying_power,
            "availableBuyingPower": buying_power,
            "globalRiskCapacityQuantity": 0,
            "dailyAccountPnl": _optional_float(payload.get("realized_pl") or payload.get("realized_pnl") or payload.get("daily_pl")) or 0.0,
            "accountSnapshotFresh": True,
            "buyingPowerCurrent": True,
            "positionsReconciled": True,
            "openOrdersReconciled": True,
            "accountTradingBlocked": not _account_allowed_to_trade(payload),
            "observedAt": observed_at,
            "paperOnly": True,
            "liveTradingEnabled": False,
            "reasonCodes": ("regime.alpaca_paper.account_verified",),
        }

    def paper_trading_configuration(self) -> dict[str, Any]:
        return {
            "brokerVersion": REGIME_ALPACA_PAPER_BROKER_VERSION,
            "brokerKind": self.broker_kind,
            "baseUrl": self.base_url,
            "paperOnly": self.paper_endpoint,
            "liveTradingEnabled": not self.paper_endpoint,
            "accountType": "paper",
            "accountId": self.account_id,
            "configured": self.configured,
            "accountEndpointResponsive": self.account_endpoint_responsive,
            "accountMatchesConfiguredIdentity": self.account_matches_configured_identity,
            "accountAllowedToTrade": self.account_allowed_to_trade,
            "credentialsConfigured": self.settings.has_alpaca_credentials,
            "credentialsVerified": self.credentials_verified,
            "marketDataCredentialsConfigured": self.market_data_credentials_configured,
            "reasonCodes": self.last_verification_reason_codes or self._configuration_reason_codes(),
        }

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        if not self.verify_paper_account():
            return PaperGatewayBrokerAck(
                clientOrderId=intent.clientOrderId,
                brokerOrderId=None,
                status="REJECTED",
                acceptedAt=None,
                rejectedReason="regime.alpaca_paper.account_unverified",
            )
        order_type = _alpaca_order_type(getattr(intent, "orderType", None), limit_price=getattr(intent, "limitPrice", None), stop_price=getattr(intent, "stopPrice", None))
        if order_type == "market":
            return PaperGatewayBrokerAck(
                clientOrderId=intent.clientOrderId,
                brokerOrderId=None,
                status="REJECTED",
                acceptedAt=None,
                rejectedReason="regime.alpaca_paper.market_orders_disabled",
            )
        body: dict[str, Any] = {
            "symbol": intent.symbol,
            "qty": str(int(intent.submittedQuantity)),
            "side": _alpaca_side(intent.side),
            "type": order_type,
            "time_in_force": str(getattr(intent, "timeInForce", "DAY")).lower(),
            "client_order_id": intent.clientOrderId,
        }
        if order_type in {"limit", "stop_limit"} and intent.limitPrice:
            body["limit_price"] = str(intent.limitPrice)
        if order_type in {"stop", "stop_limit"} and intent.stopPrice:
            body["stop_price"] = str(intent.stopPrice)
        if intent.stopPrice or intent.targetPrice:
            body["order_class"] = "bracket"
            if intent.stopPrice:
                body["stop_loss"] = {"stop_price": str(intent.stopPrice)}
                if getattr(intent, "stopLimitPrice", None):
                    body["stop_loss"]["limit_price"] = str(intent.stopLimitPrice)
            if intent.targetPrice:
                body["take_profit"] = {"limit_price": str(intent.targetPrice)}
        try:
            response = self.client.post(f"{self.base_url}/orders", headers=self._headers(), json=body)
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            found = self.find_order_by_client_order_id(intent.clientOrderId)
            if found is not None:
                return _ack_from_order(found, intent)
            raise RegimeAlpacaPaperSubmissionUncertain("regime.alpaca_paper.submission_uncertain_reconciliation_required") from exc
        except httpx.HTTPStatusError as exc:
            return PaperGatewayBrokerAck(
                clientOrderId=intent.clientOrderId,
                brokerOrderId=None,
                status="REJECTED",
                acceptedAt=None,
                rejectedReason=_http_rejection_reason(exc)[:300],
            )
        payload = response.json()
        return PaperGatewayBrokerAck(
            clientOrderId=str(payload.get("client_order_id") or intent.clientOrderId),
            brokerOrderId=str(payload.get("id") or ""),
            status=_ack_status(payload),
            acceptedAt=_parse_time(payload.get("submitted_at") or payload.get("created_at")) or datetime.now(UTC),
            rejectedReason=str(payload.get("reject_reason") or "") or None,
        )

    def find_order_by_client_order_id(self, client_order_id: str) -> dict[str, Any] | None:
        if not self.configured:
            return None
        try:
            response = self.client.get(
                f"{self.base_url}/orders:by_client_order_id",
                headers=self._headers(),
                params={"client_order_id": client_order_id},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if getattr(exc.response, "status_code", None) == 404:
                return None
            raise
        except httpx.HTTPError:
            return None
        payload = response.json()
        return dict(payload) if isinstance(payload, Mapping) else None

    def order_status(self, client_order_id: str) -> dict[str, Any] | None:
        order = self.find_order_by_client_order_id(client_order_id)
        return _order_observation(order) if order is not None else None

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        payload = self.find_order_by_client_order_id(client_order_id)
        if payload is None:
            return None
        filled = int(float(payload.get("filled_qty") or 0.0))
        average_price = _optional_float(payload.get("filled_avg_price"))
        if filled <= 0 or average_price is None:
            return None
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="regime",
            orderIntentId=client_order_id,
            symbol=str(payload.get("symbol") or "SPY").upper(),
            side=Signal.SELL if str(payload.get("side") or "").lower() == "sell" else Signal.BUY,
            filledQuantity=filled,
            averageFillPrice=average_price,
            status=_broker_status(str(payload.get("status") or "filled")),
            filledAt=_parse_time(payload.get("filled_at")) or datetime.now(UTC),
        )

    def cancel_order(self, client_order_id: str) -> bool:
        if not self.configured:
            return False
        try:
            response = self.client.delete(
                f"{self.base_url}/orders:by_client_order_id",
                headers=self._headers(),
                params={"client_order_id": client_order_id},
            )
            return response.status_code in {200, 204}
        except httpx.HTTPError:
            return False

    def refresh_positions(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        try:
            response = self.client.get(f"{self.base_url}/positions", headers=self._headers())
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        payload = response.json()
        if not isinstance(payload, list):
            return []
        return [_position_observation(row) for row in payload if isinstance(row, Mapping) and str(row.get("symbol") or "").upper() == "SPY"]

    def refresh_open_orders(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        try:
            response = self.client.get(f"{self.base_url}/orders", headers=self._headers(), params={"status": "open", "nested": "false"})
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        payload = response.json()
        if not isinstance(payload, list):
            return []
        return [_order_observation(row) for row in payload if isinstance(row, Mapping) and str(row.get("symbol") or "").upper() == "SPY"]

    def refresh_fills(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        try:
            response = self.client.get(f"{self.base_url}/account/activities/FILL", headers=self._headers())
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        payload = response.json()
        if not isinstance(payload, list):
            return []
        return [_fill_observation(row) for row in payload if isinstance(row, Mapping) and str(row.get("symbol") or "").upper() == "SPY"]

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
        }

    def _configuration_reason_codes(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if not self.paper_endpoint:
            reasons.append("regime.alpaca_paper.paper_endpoint_required")
        if not self.settings.has_alpaca_credentials:
            reasons.append("regime.alpaca_paper.credentials_required")
        if not self.market_data_credentials_configured:
            reasons.append("regime.alpaca_paper.market_data_credentials_required")
        if not self.account_id or self.account_id == REGIME_UNCONFIGURED_PAPER_ACCOUNT_ID:
            reasons.append("regime.alpaca_paper.account_id_required")
        return tuple(reasons)


def build_regime_paper_runtime(
    *,
    settings: Settings | None = None,
    service: RegimeApplicationService | None = None,
    config: RegimeRuntimeSupervisorConfig | None = None,
    broker: Any | None = None,
    paper_gateway: PaperOrderGateway | None = None,
    account_snapshot_provider: Callable[[dict[str, str]], dict[str, Any]] | None = None,
    market_data_client: Any | None = None,
    candle_store: Any | None = None,
    publisher_config: RegimeFinalizedOneMinutePublisherConfig | None = None,
    http_client: httpx.Client | None = None,
) -> RegimeRuntimeSupervisor:
    settings = settings or get_settings()
    config = config or RegimeRuntimeSupervisorConfig.paper_runtime_from_env()
    if config.default_runtime_mode not in {RegimeRuntimeMode.PAPER.value, RegimeRuntimeMode.LOCAL_PAPER.value}:
        raise ValueError("regime.runtime_factory.paper_runtime_mode_required")
    service = service or RegimeApplicationService()
    identity = _identity_from_config(config)
    if broker is None and config.default_runtime_mode == RegimeRuntimeMode.LOCAL_PAPER.value:
        broker = RegimeLocalPaperBroker(
            repository=service.repository,
            identity=identity,
            starting_balance=_env_float("REGIME_LOCAL_PAPER_INITIAL_BALANCE", REGIME_DEFAULT_LOCAL_PAPER_INITIAL_BALANCE),
        )
    broker = broker or RegimeAlpacaPaperBroker(account_id=identity["accountId"], settings=settings, http_client=http_client)
    paper_gateway = paper_gateway or PaperOrderGateway(
        broker,
        RegimePaperGatewayStore(service.repository, identity),
        max_decision_age_seconds=_env_int("REGIME_RUNTIME_MAX_DECISION_AGE_SECONDS", 300),
        execution_mode="LOCAL_PAPER" if config.default_runtime_mode == RegimeRuntimeMode.LOCAL_PAPER.value else "BROKER_PAPER",
        account_snapshot_provider=getattr(broker, "gateway_account_snapshot", None)
        if config.default_runtime_mode == RegimeRuntimeMode.LOCAL_PAPER.value
        else None,
        portfolio_snapshot_provider=getattr(broker, "gateway_portfolio_snapshot", None)
        if config.default_runtime_mode == RegimeRuntimeMode.LOCAL_PAPER.value
        else None,
    )
    shared_global_risk_manager = getattr(paper_gateway, "global_risk_manager", None) or REGIME_SHARED_GLOBAL_RISK_MANAGER
    if account_snapshot_provider is None and config.default_runtime_mode == RegimeRuntimeMode.LOCAL_PAPER.value:
        account_snapshot_provider = _local_paper_account_snapshot_provider(broker)
    account_snapshot_provider = account_snapshot_provider or _broker_account_snapshot_provider(
        broker,
        global_risk_manager=shared_global_risk_manager,
    )
    supervisor = RegimeRuntimeSupervisor(
        service=service,
        config=config,
        paper_gateway=paper_gateway,
        account_snapshot_provider=account_snapshot_provider,
    )
    supervisor.market_event_publisher = RegimeFinalizedOneMinutePublisher(
        identity=identity,
        repository=service.repository,
        market_data_client=market_data_client,
        candle_store=candle_store,
        publish_completed_bar=supervisor.publish_completed_bar,
        config=publisher_config or _publisher_config_from_env(),
    )
    blockers = _dependency_blockers(config, broker)
    if blockers:
        _apply_factory_blockers(supervisor, blockers)
    supervisor.runtime_factory_diagnostics = RegimeRuntimeFactoryDiagnostics(
        algorithm_id="regime",
        runtime_mode=config.default_runtime_mode,
        algorithm_instance_id=config.default_algorithm_instance_id,
        account_id=config.default_account_id,
        symbol=config.symbol,
        dependency_blockers=blockers,
    ).as_dict()
    return supervisor


_REGIME_RUNTIME_SUPERVISOR: RegimeRuntimeSupervisor | None = None


def get_regime_runtime_supervisor(
    *,
    settings: Settings | None = None,
    service: RegimeApplicationService | None = None,
    broker: Any | None = None,
    paper_gateway: PaperOrderGateway | None = None,
    account_snapshot_provider: Callable[[dict[str, str]], dict[str, Any]] | None = None,
    market_data_client: Any | None = None,
    candle_store: Any | None = None,
    publisher_config: RegimeFinalizedOneMinutePublisherConfig | None = None,
) -> RegimeRuntimeSupervisor:
    global _REGIME_RUNTIME_SUPERVISOR
    if _REGIME_RUNTIME_SUPERVISOR is None:
        _REGIME_RUNTIME_SUPERVISOR = build_regime_paper_runtime(
            settings=settings,
            service=service,
            broker=broker,
            paper_gateway=paper_gateway,
            account_snapshot_provider=account_snapshot_provider,
            market_data_client=market_data_client,
            candle_store=candle_store,
            publisher_config=publisher_config,
        )
    return _REGIME_RUNTIME_SUPERVISOR


def set_regime_runtime_supervisor_for_tests(supervisor: RegimeRuntimeSupervisor | None) -> None:
    global _REGIME_RUNTIME_SUPERVISOR
    _REGIME_RUNTIME_SUPERVISOR = supervisor


def _local_paper_account_snapshot_provider(broker: Any) -> Callable[[dict[str, str]], dict[str, Any]]:
    def provider(identity: dict[str, str]) -> dict[str, Any]:
        snapshot_provider = getattr(broker, "refresh_account_snapshot", None)
        raw = dict(snapshot_provider() if callable(snapshot_provider) else {})
        return normalize_regime_account_snapshot(raw, identity=identity, max_age_seconds=None)

    return provider

def _broker_account_snapshot_provider(
    broker: Any,
    *,
    global_risk_manager: Any | None = None,
) -> Callable[[dict[str, str]], dict[str, Any]]:
    snapshot_provider = getattr(broker, "refresh_account_snapshot", None)
    return build_regime_authoritative_account_snapshot_provider(
        account_provider=snapshot_provider if callable(snapshot_provider) else None,
        global_risk_manager=global_risk_manager,
    )


def _dependency_blockers(config: RegimeRuntimeSupervisorConfig, broker: Any) -> tuple[str, ...]:
    blockers: list[str] = []
    if config.default_runtime_mode not in {RegimeRuntimeMode.PAPER.value, RegimeRuntimeMode.LOCAL_PAPER.value}:
        blockers.append("regime.runtime_factory.paper_runtime_mode_required")
    if config.symbol.upper() != "SPY":
        blockers.append("regime.runtime_factory.spy_symbol_required")
    if config.default_runtime_mode == RegimeRuntimeMode.PAPER.value and config.default_account_id == REGIME_UNCONFIGURED_PAPER_ACCOUNT_ID:
        blockers.append("regime.runtime_factory.paper_account_id_unconfigured")
    startup_verification = getattr(broker, "startup_verification", None)
    configuration = dict(startup_verification() if callable(startup_verification) else {})
    if not configuration and callable(getattr(broker, "paper_trading_configuration", None)):
        configuration = dict(broker.paper_trading_configuration())
    local_paper = config.default_runtime_mode == RegimeRuntimeMode.LOCAL_PAPER.value
    if configuration.get("paperOnly") is not True:
        blockers.append("regime.runtime_factory.local_paper_endpoint_required" if local_paper else "regime.runtime_factory.alpaca_paper_endpoint_required")
    if configuration.get("liveTradingEnabled") is True:
        blockers.append("regime.runtime_factory.live_trading_endpoint_rejected")
    if configuration.get("configured") is not True:
        blockers.append("regime.runtime_factory.local_paper_account_unavailable" if local_paper else "regime.runtime_factory.alpaca_paper_credentials_or_account_unavailable")
    if configuration.get("verified") is False:
        blockers.append("regime.runtime_factory.local_paper_account_unverified" if local_paper else "regime.runtime_factory.alpaca_paper_account_unverified")
    blockers.extend(str(reason) for reason in configuration.get("reasonCodes") or () if reason)
    return tuple(dict.fromkeys(blockers))


def _apply_factory_blockers(supervisor: RegimeRuntimeSupervisor, blockers: tuple[str, ...]) -> None:
    supervisor.metrics.entry_creation_paused_for_reconciliation = True
    supervisor.metrics.broker_paper_mode_verified = False
    supervisor.metrics.broker_connectivity_ok = False
    for blocker in blockers:
        supervisor._block_new_entries(blocker)
    supervisor._mark_component(
        "paper_broker",
        "unhealthy",
        reason_codes=blockers,
        details={"factoryVersion": REGIME_RUNTIME_FACTORY_VERSION, "failClosed": True},
    )
    supervisor._mark_component(
        "broker_connectivity",
        "unhealthy",
        reason_codes=blockers,
        details={"factoryVersion": REGIME_RUNTIME_FACTORY_VERSION, "failClosed": True},
    )


def _publisher_config_from_env() -> RegimeFinalizedOneMinutePublisherConfig:
    return RegimeFinalizedOneMinutePublisherConfig(
        feed=str(os.getenv("REGIME_PUBLISHER_FEED") or "iex"),
        fetch_limit=_env_int("REGIME_PUBLISHER_FETCH_LIMIT", 240),
        warmup_bars=_env_int("REGIME_PUBLISHER_WARMUP_BARS", 120),
        finalization_delay_seconds=_env_int("REGIME_PUBLISHER_FINALIZATION_DELAY_SECONDS", 5),
        max_event_age_seconds=_env_int("REGIME_PUBLISHER_MAX_EVENT_AGE_SECONDS", 300),
        material_gap_minutes=_env_int("REGIME_PUBLISHER_MATERIAL_GAP_MINUTES", 2),
        publisher_poll_interval_seconds=_env_float_any(("REGIME_PUBLISHER_POLL_INTERVAL_SECONDS", "REGIME_RUNTIME_PUBLISHER_POLL_INTERVAL_SECONDS"), 1.0),
        closed_market_poll_interval_seconds=_env_float_any(
            ("REGIME_PUBLISHER_CLOSED_MARKET_POLL_INTERVAL_SECONDS", "REGIME_RUNTIME_CLOSED_MARKET_PUBLISHER_POLL_INTERVAL_SECONDS"),
            300.0,
        ),
    )


def _identity_from_config(config: RegimeRuntimeSupervisorConfig) -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": config.default_algorithm_instance_id,
        "accountId": config.default_account_id,
        "runtimeMode": config.default_runtime_mode,
        "symbol": config.symbol,
    }


def _account_identifier(payload: Mapping[str, Any]) -> str:
    return str(payload.get("id") or payload.get("account_number") or payload.get("accountId") or payload.get("account_id") or "").strip()


def _account_payload_is_paper(payload: Mapping[str, Any]) -> bool:
    account_type = str(payload.get("account_type") or payload.get("accountType") or "").strip().lower()
    return not account_type or account_type in {"paper", "paper_trading"}


def _account_allowed_to_trade(payload: Mapping[str, Any]) -> bool:
    status = str(payload.get("status") or "").strip().upper()
    if status and status not in {"ACTIVE", "ACCOUNT_STATUS_ACTIVE"}:
        return False
    for key in ("trading_blocked", "account_blocked", "trade_suspended_by_user"):
        if bool(payload.get(key)):
            return False
    return True


def _order_observation(order: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "clientOrderId": str(order.get("client_order_id") or ""),
        "brokerOrderId": str(order.get("id") or ""),
        "symbol": str(order.get("symbol") or "SPY").upper(),
        "side": str(order.get("side") or "buy").upper(),
        "status": _broker_status(str(order.get("status") or "open")),
        "quantity": int(float(order.get("qty") or 0.0)),
        "filledQuantity": int(float(order.get("filled_qty") or 0.0)),
        "averageFillPrice": _optional_float(order.get("filled_avg_price")),
        "timestamp": str(order.get("updated_at") or order.get("submitted_at") or _utc_now()),
    }


def _fill_observation(fill: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "clientOrderId": str(fill.get("client_order_id") or ""),
        "brokerOrderId": str(fill.get("order_id") or ""),
        "fillId": str(fill.get("id") or ""),
        "symbol": str(fill.get("symbol") or "SPY").upper(),
        "side": str(fill.get("side") or "buy").upper(),
        "filledQuantity": int(float(fill.get("qty") or 0.0)),
        "averageFillPrice": _optional_float(fill.get("price")),
        "status": "FILLED",
        "filledAt": str(fill.get("transaction_time") or _utc_now()),
        "timestamp": str(fill.get("transaction_time") or _utc_now()),
    }


def _position_observation(position: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "symbol": str(position.get("symbol") or "SPY").upper(),
        "side": "Buy" if float(position.get("qty") or 0.0) >= 0 else "Sell",
        "quantity": abs(int(float(position.get("qty") or 0.0))),
        "averageFillPrice": _optional_float(position.get("avg_entry_price")),
        "marketValue": _optional_float(position.get("market_value")),
        "timestamp": _utc_now(),
    }


def _ack_status(payload: Mapping[str, Any]) -> str:
    status = _broker_status(str(payload.get("status") or "accepted"))
    return "ACCEPTED" if status in {"ACCEPTED", "OPEN"} else status


def _ack_from_order(payload: Mapping[str, Any], intent) -> PaperGatewayBrokerAck:
    return PaperGatewayBrokerAck(
        clientOrderId=str(payload.get("client_order_id") or intent.clientOrderId),
        brokerOrderId=str(payload.get("id") or ""),
        status=_ack_status(payload),
        acceptedAt=_parse_time(payload.get("submitted_at") or payload.get("created_at")) or datetime.now(UTC),
        rejectedReason=str(payload.get("reject_reason") or "") or None,
    )


def _broker_status(value: str) -> str:
    normalized = value.lower()
    if normalized in {"accepted", "new", "pending_new", "open"}:
        return "ACCEPTED"
    if normalized == "partially_filled":
        return "PARTIALLY_FILLED"
    if normalized == "filled":
        return "FILLED"
    if normalized in {"canceled", "cancelled", "expired"}:
        return "CANCELED"
    if normalized == "rejected":
        return "REJECTED"
    if normalized == "replaced":
        return "REPLACED"
    return "ACCEPTED"


def _alpaca_order_type(value: Any, *, limit_price: float | None, stop_price: float | None) -> str:
    normalized = str(value or "").upper()
    if normalized == "STOP_LIMIT":
        return "stop_limit"
    if normalized == "STOP":
        return "stop"
    if normalized in {"LIMIT", "BRACKET_LIMIT", "MARKETABLE_LIMIT"}:
        return "limit"
    if normalized == "MARKET":
        return "market"
    if stop_price and limit_price:
        return "stop_limit"
    if limit_price:
        return "limit"
    return "market"


def _alpaca_side(value: Any) -> str:
    raw = value.value if hasattr(value, "value") else str(value)
    return "sell" if str(raw).upper() == "SELL" else "buy"


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _optional_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _http_rejection_reason(exc: httpx.HTTPStatusError) -> str:
    try:
        payload = exc.response.json()
        if isinstance(payload, Mapping):
            return str(payload.get("message") or payload.get("error") or exc.response.text)
    except Exception:
        pass
    return str(exc)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.01, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float_any(names: tuple[str, ...], default: float) -> float:
    for name in names:
        if os.getenv(name) is not None:
            return _env_float(name, default)
    return default


__all__ = [
    "REGIME_ALPACA_PAPER_BROKER_VERSION",
    "REGIME_LOCAL_PAPER_BROKER_VERSION",
    "REGIME_RUNTIME_FACTORY_VERSION",
    "RegimeAlpacaPaperBroker",
    "RegimeLocalPaperBroker",
    "RegimeAlpacaPaperSubmissionUncertain",
    "RegimeRuntimeFactoryDiagnostics",
    "build_regime_paper_runtime",
    "get_regime_runtime_supervisor",
    "set_regime_runtime_supervisor_for_tests",
]
