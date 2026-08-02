"""Weighted Voting-owned Alpaca paper broker and account adapters."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.runtime_context import WeightedVotingReadOnlyAccountObservation
from backend.app.config import Settings, get_settings
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill


WEIGHTED_VOTING_ALPACA_PAPER_BROKER_VERSION = "weighted_voting_alpaca_paper_broker_v1"
_PAPER_HOST_MARKER = "paper-api.alpaca.markets"


class WeightedVotingAlpacaPaperBrokerConfigurationError(ValueError):
    pass


class WeightedVotingAlpacaPaperBroker:
    """Paper-only Alpaca adapter for Weighted Voting execution ownership."""

    broker_kind = "alpaca_paper"
    live_trading_enabled = False

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.settings = settings or get_settings()
        self.base_url = str(self.settings.alpaca_trading_base_url).rstrip("/")
        if not self.paper_endpoint:
            raise WeightedVotingAlpacaPaperBrokerConfigurationError("weighted_voting.alpaca_paper.paper_endpoint_required")
        self._owned_client = None if http_client is not None else httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 3.0)), trust_env=False)
        self.client = http_client or self._owned_client

    @property
    def paper_endpoint(self) -> bool:
        normalized = self.base_url.lower()
        return _PAPER_HOST_MARKER in normalized and "api.alpaca.markets" in normalized

    @property
    def configured(self) -> bool:
        return bool(self.paper_endpoint and self.settings.has_alpaca_credentials)

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()

    def verify_paper_account(self) -> bool:
        return self.paper_account_payload() is not None

    def paper_account_payload(self) -> dict[str, Any] | None:
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
        if not (payload.get("id") or payload.get("account_number")):
            return None
        return dict(payload)

    def account_observation(self, *, as_of: datetime) -> WeightedVotingReadOnlyAccountObservation:
        payload = self.paper_account_payload()
        if payload is None:
            return WeightedVotingReadOnlyAccountObservation(
                account_equity=None,
                broker_buying_power=None,
                observed_at=as_of,
                source_id="weighted_voting.alpaca_paper.account_unavailable",
                available=False,
                reason_codes=("weighted_voting.alpaca_paper.account_unavailable_or_unverified",),
            )
        equity = _optional_float(payload.get("equity") or payload.get("portfolio_value") or payload.get("cash"))
        buying_power = _optional_float(payload.get("buying_power") or payload.get("regt_buying_power") or payload.get("cash"))
        return WeightedVotingReadOnlyAccountObservation(
            account_equity=equity,
            broker_buying_power=buying_power,
            observed_at=as_of,
            source_id="weighted_voting.alpaca_paper.account",
            available=equity is not None and buying_power is not None,
            reason_codes=("weighted_voting.alpaca_paper.account_verified",),
        )

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        if not self.verify_paper_account():
            return PaperGatewayBrokerAck(
                clientOrderId=intent.clientOrderId,
                brokerOrderId=None,
                status="REJECTED",
                acceptedAt=None,
                rejectedReason="weighted_voting.alpaca_paper.account_unverified",
            )
        order_type = _alpaca_order_type(getattr(intent, "orderType", None), limit_price=getattr(intent, "limitPrice", None), stop_price=getattr(intent, "stopPrice", None))
        if order_type == "market":
            return PaperGatewayBrokerAck(
                clientOrderId=intent.clientOrderId,
                brokerOrderId=None,
                status="REJECTED",
                acceptedAt=None,
                rejectedReason="weighted_voting.alpaca_paper.market_orders_disabled",
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
        except httpx.TimeoutException as exc:
            raise TimeoutError("weighted_voting.alpaca_paper.submission_timeout") from exc
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

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        if not self.configured:
            return None
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
        filled = int(float(payload.get("filled_qty") or 0.0))
        average_price = _optional_float(payload.get("filled_avg_price"))
        if filled <= 0 or average_price is None:
            return None
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId=WEIGHTED_VOTING_ALGORITHM_ID,
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

    def refresh_orders(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        try:
            response = self.client.get(f"{self.base_url}/orders", headers=self._headers(), params={"status": "all", "limit": 100, "nested": "false"})
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        payload = response.json()
        if not isinstance(payload, list):
            return []
        return [_order_observation(row) for row in payload if isinstance(row, Mapping) and _is_weighted_voting_client_order(row.get("client_order_id"))]

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
        return [dict(item) for item in payload if isinstance(item, Mapping)]

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": self.settings.alpaca_secret_key,
        }


class WeightedVotingUnavailablePaperBroker:
    broker_kind = "unavailable"
    configured = False
    paper_endpoint = False
    live_trading_enabled = False
    base_url = "unavailable://weighted_voting"

    def verify_paper_account(self) -> bool:
        return False

    def account_observation(self, *, as_of: datetime) -> WeightedVotingReadOnlyAccountObservation:
        return WeightedVotingReadOnlyAccountObservation(
            account_equity=None,
            broker_buying_power=None,
            observed_at=as_of,
            source_id="weighted_voting.alpaca_paper.unavailable",
            available=False,
            reason_codes=("weighted_voting.alpaca_paper.unavailable",),
        )

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=None,
            status="REJECTED",
            acceptedAt=None,
            rejectedReason="weighted_voting.alpaca_paper.unavailable",
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        return None

    def cancel_order(self, client_order_id: str) -> bool:
        return False

    def refresh_orders(self) -> list[dict[str, Any]]:
        return []

    def refresh_positions(self) -> list[dict[str, Any]]:
        return []


def build_weighted_voting_paper_gateway_dependencies(settings: Settings | None = None) -> tuple[Any, Any]:
    try:
        broker = WeightedVotingAlpacaPaperBroker(settings=settings)
    except WeightedVotingAlpacaPaperBrokerConfigurationError:
        broker = WeightedVotingUnavailablePaperBroker()
    return broker, broker


def _order_observation(order: Mapping[str, Any]) -> dict[str, Any]:
    client_order_id = str(order.get("client_order_id") or "")
    return {
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "clientOrderId": client_order_id,
        "brokerOrderId": str(order.get("id") or ""),
        "orderIntentId": client_order_id,
        "symbol": str(order.get("symbol") or "SPY").upper(),
        "side": str(order.get("side") or "buy").upper(),
        "status": _broker_status(str(order.get("status") or "")),
        "quantity": int(float(order.get("qty") or 0.0)),
        "filledQuantity": int(float(order.get("filled_qty") or 0.0)),
        "averageFillPrice": _optional_float(order.get("filled_avg_price")),
        "observedAt": str(order.get("updated_at") or order.get("submitted_at") or datetime.now(UTC).isoformat()),
    }


def _is_weighted_voting_client_order(value: Any) -> bool:
    return str(value or "").startswith("wv-")


def _ack_status(payload: Mapping[str, Any]) -> str:
    status = _broker_status(str(payload.get("status") or "accepted"))
    return "ACCEPTED" if status in {"ACCEPTED", "OPEN"} else status


def _broker_status(value: str) -> str:
    normalized = value.lower()
    if normalized in {"accepted", "new", "pending_new"}:
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
    return "OPEN"


def _alpaca_order_type(value: Any, *, limit_price: float | None, stop_price: float | None) -> str:
    normalized = str(value or "").upper()
    if normalized == "STOP_LIMIT":
        return "stop_limit"
    if normalized == "STOP":
        return "stop"
    if normalized in {"LIMIT", "MARKETABLE_LIMIT"}:
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


__all__ = [
    "WEIGHTED_VOTING_ALPACA_PAPER_BROKER_VERSION",
    "WeightedVotingAlpacaPaperBroker",
    "WeightedVotingAlpacaPaperBrokerConfigurationError",
    "WeightedVotingUnavailablePaperBroker",
    "build_weighted_voting_paper_gateway_dependencies",
]
