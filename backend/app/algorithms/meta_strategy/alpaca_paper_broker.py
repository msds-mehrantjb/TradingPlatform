"""Meta-Strategy-owned Alpaca paper broker adapter."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill


META_STRATEGY_ALPACA_PAPER_BROKER_VERSION = "meta_strategy_alpaca_paper_broker_v1"
_PAPER_HOST_MARKER = "paper-api.alpaca.markets"


class MetaStrategyAlpacaPaperBrokerConfigurationError(ValueError):
    pass


class MetaStrategyAlpacaPaperBroker:
    """Submits only to Alpaca paper trading endpoints."""

    broker_kind = "alpaca_paper"

    def __init__(
        self,
        settings: Any | None = None,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.settings = settings or _paper_settings_from_env()
        self.base_url = str(self.settings.alpaca_trading_base_url).rstrip("/")
        if _PAPER_HOST_MARKER not in self.base_url:
            raise MetaStrategyAlpacaPaperBrokerConfigurationError("meta_strategy.alpaca_paper.paper_endpoint_required")
        if not self.settings.has_alpaca_credentials:
            raise MetaStrategyAlpacaPaperBrokerConfigurationError("meta_strategy.alpaca_paper.credentials_required")
        self._owned_client = None if http_client is not None else httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 3.0)), trust_env=False)
        self.client = http_client or self._owned_client

    @property
    def configured(self) -> bool:
        return True

    @property
    def paper_endpoint(self) -> bool:
        return _PAPER_HOST_MARKER in self.base_url

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()

    def verify_paper_account(self) -> bool:
        try:
            response = self.client.get(f"{self.base_url}/account", headers=self._headers())
            response.raise_for_status()
        except (httpx.HTTPError, AttributeError):
            return False
        payload = response.json()
        return bool(payload.get("id") or payload.get("account_number"))

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        order_type = _alpaca_order_type(getattr(intent, "orderType", None), limit_price=intent.limitPrice)
        body: dict[str, Any] = {
            "symbol": intent.symbol,
            "qty": str(int(intent.submittedQuantity)),
            "side": "buy" if intent.side == Signal.BUY else "sell",
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
                stop_limit_price = getattr(intent, "stopLimitPrice", None)
                if stop_limit_price:
                    body["stop_loss"]["limit_price"] = str(stop_limit_price)
            if intent.targetPrice:
                body["take_profit"] = {"limit_price": str(intent.targetPrice)}
        try:
            response = self.client.post(f"{self.base_url}/orders", headers=self._headers(), json=body)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise TimeoutError("meta_strategy.alpaca_paper.submission_timeout") from exc
        except httpx.HTTPStatusError as exc:
            status = "REJECTED"
            try:
                reason = str(exc.response.json().get("message") or exc.response.text)
            except Exception:
                reason = str(exc)
            return PaperGatewayBrokerAck(
                clientOrderId=intent.clientOrderId,
                brokerOrderId=None,
                status=status,
                acceptedAt=None,
                rejectedReason=reason[:300],
            )
        payload = response.json()
        return PaperGatewayBrokerAck(
            clientOrderId=str(payload.get("client_order_id") or intent.clientOrderId),
            brokerOrderId=str(payload.get("id") or ""),
            status=_ack_status(payload),
            acceptedAt=_parse_time(payload.get("submitted_at") or payload.get("created_at")) or datetime.now(UTC),
            rejectedReason=str(payload.get("reject_reason") or "") or None,
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        try:
            response = self.client.get(
                f"{self.base_url}/orders:by_client_order_id",
                headers=self._headers(),
                params={"client_order_id": client_order_id},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        payload = response.json()
        filled = float(payload.get("filled_qty") or 0.0)
        if filled <= 0:
            return None
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="meta_strategy",
            orderIntentId=str(payload.get("client_order_id") or client_order_id),
            symbol=str(payload.get("symbol") or "UNKNOWN").upper(),
            side=Signal.SELL if str(payload.get("side") or "").lower() == "sell" else Signal.BUY,
            filledQuantity=int(filled),
            averageFillPrice=float(payload.get("filled_avg_price") or 0.01),
            status=_fill_status(payload),
            filledAt=_parse_time(payload.get("filled_at")) or datetime.now(UTC),
        )

    def cancel_order(self, client_order_id: str) -> bool:
        try:
            response = self.client.delete(
                f"{self.base_url}/orders:by_client_order_id",
                headers=self._headers(),
                params={"client_order_id": client_order_id},
            )
            return response.status_code in {200, 204}
        except httpx.HTTPError:
            return False

    def replace_order(self, broker_order_id: str, *, quantity: int | None = None, limit_price: float | None = None, stop_price: float | None = None, client_order_id: str | None = None) -> dict[str, Any] | None:
        if not broker_order_id:
            return None
        body: dict[str, Any] = {}
        if quantity is not None:
            body["qty"] = str(int(quantity))
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if stop_price is not None:
            body["stop_price"] = str(stop_price)
        if client_order_id:
            body["client_order_id"] = client_order_id
        try:
            response = self.client.patch(f"{self.base_url}/orders/{broker_order_id}", headers=self._headers(), json=body)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        payload = response.json()
        return _event_from_order(payload) if isinstance(payload, Mapping) else None

    def refresh_positions(self) -> list[dict[str, Any]]:
        try:
            response = self.client.get(f"{self.base_url}/positions", headers=self._headers())
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        payload = response.json()
        return [dict(item) for item in payload] if isinstance(payload, list) else []

    def list_order_events(self) -> list[dict[str, Any]]:
        try:
            response = self.client.get(f"{self.base_url}/orders", headers=self._headers(), params={"status": "all", "limit": 50, "nested": "false"})
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        payload = response.json()
        if not isinstance(payload, list):
            return []
        return [_event_from_order(order) for order in payload if isinstance(order, Mapping)]

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
        }


class MetaStrategyUnavailablePaperBroker:
    configured = False
    broker_kind = "unavailable"
    paper_endpoint = False

    def verify_paper_account(self) -> bool:
        return False

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=None,
            status="REJECTED",
            acceptedAt=None,
            rejectedReason="meta_strategy.alpaca_paper.unconfigured",
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        return None

    def cancel_order(self, client_order_id: str) -> bool:
        return False

    def refresh_positions(self) -> list[dict[str, Any]]:
        return []

    def list_order_events(self) -> list[dict[str, Any]]:
        return []

    def replace_order(self, broker_order_id: str, **_: Any) -> dict[str, Any] | None:
        return None


def _event_from_order(order: Mapping[str, Any]) -> dict[str, Any]:
    client_order_id = str(order.get("client_order_id") or "")
    status = _broker_status(str(order.get("status") or ""))
    return {
        "brokerEventId": str(order.get("id") or client_order_id),
        "algorithmId": "meta_strategy",
        "clientOrderId": client_order_id,
        "brokerOrderId": str(order.get("id") or ""),
        "orderIntentId": client_order_id,
        "status": status,
        "symbol": str(order.get("symbol") or "UNKNOWN").upper(),
        "side": str(order.get("side") or "buy").upper(),
        "filledQuantity": int(float(order.get("filled_qty") or 0.0)),
        "averageFillPrice": float(order.get("filled_avg_price") or 0.0),
        "timestamp": str(order.get("updated_at") or order.get("submitted_at") or datetime.now(UTC).isoformat()),
    }


def _ack_status(payload: Mapping[str, Any]) -> str:
    status = _broker_status(str(payload.get("status") or "accepted"))
    return "ACCEPTED" if status in {"ACCEPTED", "OPEN"} else status


def _fill_status(payload: Mapping[str, Any]) -> str:
    return _broker_status(str(payload.get("status") or "filled"))


def _broker_status(value: str) -> str:
    normalized = value.lower()
    if normalized in {"accepted", "new", "pending_new"}:
        return "ACCEPTED"
    if normalized in {"partially_filled"}:
        return "PARTIALLY_FILLED"
    if normalized == "filled":
        return "FILLED"
    if normalized in {"canceled", "cancelled"}:
        return "CANCELED"
    if normalized == "rejected":
        return "REJECTED"
    if normalized == "expired":
        return "CANCELED"
    if normalized == "replaced":
        return "REPLACED"
    return "OPEN"


def _alpaca_order_type(value: Any, *, limit_price: float | None) -> str:
    normalized = str(value or "").upper()
    if normalized == "MARKETABLE_LIMIT":
        return "limit"
    if normalized == "STOP_LIMIT":
        return "stop_limit"
    if normalized == "STOP":
        return "stop"
    if normalized == "MARKET":
        return "market"
    return "limit" if limit_price else "market"


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class _PaperSettings:
    def __init__(self, *, alpaca_key_id: str, alpaca_secret_key: str, alpaca_trading_base_url: str) -> None:
        self.alpaca_key_id = alpaca_key_id
        self.alpaca_secret_key = alpaca_secret_key
        self.alpaca_trading_base_url = alpaca_trading_base_url

    @property
    def has_alpaca_credentials(self) -> bool:
        return bool(self.alpaca_key_id and self.alpaca_secret_key)


def _paper_settings_from_env() -> _PaperSettings:
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    return _PaperSettings(
        alpaca_key_id=os.getenv("APCA_API_KEY_ID", ""),
        alpaca_secret_key=os.getenv("APCA_API_SECRET_KEY", ""),
        alpaca_trading_base_url=os.getenv("ALPACA_TRADING_BASE_URL", "https://paper-api.alpaca.markets/v2"),
    )


__all__ = [
    "META_STRATEGY_ALPACA_PAPER_BROKER_VERSION",
    "MetaStrategyAlpacaPaperBroker",
    "MetaStrategyAlpacaPaperBrokerConfigurationError",
    "MetaStrategyUnavailablePaperBroker",
]
