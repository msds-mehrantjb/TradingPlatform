"""WCA-specific Alpaca paper broker transport."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

import httpx

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaSide
from backend.app.algorithms.wca.paper_account import (
    WCA_ALPACA_PAPER_API_KEY_ID,
    WCA_ALPACA_PAPER_API_SECRET_KEY,
    WCA_ALPACA_PAPER_BASE_URL,
    WCA_REQUIRED_ALPACA_PAPER_BASE_URL,
    validate_wca_automatic_paper_account,
)
from backend.app.algorithms.wca.paper_broker import (
    WcaPaperBrokerAck,
    WcaPaperBrokerFill,
    WcaPaperBrokerOrderRequest,
    WcaPaperBrokerTimeout,
    redact_secret_payload,
)
from backend.app.algorithms.wca.session_validation import WcaBrokerClock
from backend.app.domain.models import Signal
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState


WCA_ALPACA_PAPER_BROKER_VERSION = "wca_alpaca_paper_broker_v1"
WCA_ALPACA_ORDER_STREAM_UNAVAILABLE = "wca.alpaca_paper.stream_unavailable_poll_required"
_MAX_RETRIES = 2
_BACKOFF_BASE_SECONDS = 0.05


class WcaAlpacaPaperBrokerConfigurationError(ValueError):
    pass


class WcaAlpacaPaperHttpClient(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        ...


@dataclass(frozen=True)
class WcaAlpacaPaperCredentials:
    key_id: str
    secret_key: str
    base_url: str
    account_id: str


class WcaAlpacaPaperBroker:
    def __init__(
        self,
        *,
        account_id: str,
        key_id: str,
        secret_key: str,
        base_url: str = WCA_REQUIRED_ALPACA_PAPER_BASE_URL,
        http_client: WcaAlpacaPaperHttpClient | None = None,
        timeout_seconds: float = 4.0,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self.account_id = account_id.strip()
        self.key_id = key_id.strip()
        self.secret_key = secret_key.strip()
        self.base_url = base_url.strip().rstrip("/")
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.max_retries = max(0, int(max_retries))
        if self.base_url != WCA_REQUIRED_ALPACA_PAPER_BASE_URL:
            raise WcaAlpacaPaperBrokerConfigurationError("wca.alpaca_paper.paper_endpoint_required")
        if not self.account_id or not self.key_id or not self.secret_key:
            raise WcaAlpacaPaperBrokerConfigurationError("wca.alpaca_paper.credentials_incomplete")
        self._owned_client = None if http_client is not None else httpx.Client(timeout=httpx.Timeout(self.timeout_seconds, connect=min(self.timeout_seconds, 3.0)), trust_env=False)
        self.http_client = http_client or self._owned_client

    @classmethod
    def from_env(cls, *, account_id: str, environ: Mapping[str, str] | None = None, http_client: WcaAlpacaPaperHttpClient | None = None) -> "WcaAlpacaPaperBroker":
        validation = validate_wca_automatic_paper_account(account_id=account_id, environ=environ)
        if not validation.verified:
            raise WcaAlpacaPaperBrokerConfigurationError(";".join(validation.reason_codes))
        source = environ or __import__("os").environ
        return cls(
            account_id=account_id,
            key_id=str(source[WCA_ALPACA_PAPER_API_KEY_ID]),
            secret_key=str(source[WCA_ALPACA_PAPER_API_SECRET_KEY]),
            base_url=str(source[WCA_ALPACA_PAPER_BASE_URL]),
            http_client=http_client,
        )

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()

    def verify_account_and_endpoint_identity(self) -> tuple[bool, tuple[str, ...]]:
        if self.base_url != WCA_REQUIRED_ALPACA_PAPER_BASE_URL:
            return False, ("wca.alpaca_paper.paper_endpoint_required",)
        try:
            account = self._request("GET", "/v2/account")
        except httpx.TimeoutException:
            return False, ("wca.alpaca_paper.account_verification_timeout",)
        except httpx.HTTPStatusError:
            return False, ("wca.alpaca_paper.account_verification_http_error",)
        except httpx.TransportError:
            return False, ("wca.alpaca_paper.account_verification_transport_error",)
        broker_account_id = _account_identifier(account)
        if broker_account_id != self.account_id:
            return False, ("wca.alpaca_paper.account_id_mismatch",)
        return True, (WCA_ALPACA_PAPER_BROKER_VERSION, "wca.alpaca_paper.account_verified")

    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        account = self.refresh_account()
        positions = self.read_positions()
        open_orders = self.read_open_orders()
        pending = [order for order in open_orders if order.status != "PARTIALLY_FILLED"]
        partial = [order for order in open_orders if order.status == "PARTIALLY_FILLED"]
        return BrokerAccountSnapshot(
            accountId=_account_identifier(account),
            equity=_float(account.get("equity")),
            buyingPower=_float(account.get("buying_power") or account.get("buyingPower")),
            realizedPnlToday=_float(account.get("realized_intraday_pl") or account.get("realizedPnlToday")),
            positions=positions,
            pendingOrders=pending,
            partiallyFilledOrders=partial,
            observedAt=_utc_now(),
            sessionDate=_utc_now().date(),
            sourceAuthority="broker",
            positionsReconciled=True,
            openOrdersReconciled=True,
        )

    def refresh_account(self) -> dict[str, Any]:
        account = self._request("GET", "/v2/account")
        if _account_identifier(account) != self.account_id:
            raise WcaAlpacaPaperBrokerConfigurationError("wca.alpaca_paper.account_id_mismatch")
        return redact_secret_payload(account)

    def read_clock(self) -> WcaBrokerClock:
        clock = self._request("GET", "/v2/clock")
        return WcaBrokerClock(
            timestamp=_parse_dt(clock.get("timestamp")),
            is_open=bool(clock.get("is_open") or clock.get("isOpen")),
            next_open=_optional_datetime(clock.get("next_open") or clock.get("nextOpen")),
            next_close=_optional_datetime(clock.get("next_close") or clock.get("nextClose")),
            raw=redact_secret_payload(clock),
        )

    def read_positions(self) -> list[BrokerPositionState]:
        rows = self._request("GET", "/v2/positions")
        return [_position(row) for row in rows if str(row.get("symbol", "")).upper() == "SPY"]

    def read_open_orders(self) -> list[BrokerOrderState]:
        rows = self._request("GET", "/v2/orders", params={"status": "open", "nested": "false"})
        return [_order(row) for row in rows if _is_wca_order(row)]

    def read_order_by_broker_id(self, broker_order_id: str) -> dict[str, Any]:
        return redact_secret_payload(self._request("GET", f"/v2/orders/{broker_order_id}"))

    def find_order_by_client_order_id(self, client_order_id: str) -> dict[str, Any] | None:
        try:
            return redact_secret_payload(self._request("GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_order_id}))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    def submit_order(self, request: WcaPaperBrokerOrderRequest) -> WcaPaperBrokerAck:
        self._validate_request_identity(request)
        payload = _order_payload(request)
        try:
            response = self._request("POST", "/v2/orders", json=payload, retry=False)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            found = self.find_order_by_client_order_id(request.client_order_id)
            if found is not None:
                return _ack_from_order(found, request)
            raise WcaPaperBrokerTimeout("wca.alpaca_paper.submission_uncertain_reconciliation_required") from exc
        return _ack_from_order(response, request)

    def replace_order(self, broker_order_id: str, request: WcaPaperBrokerOrderRequest) -> WcaPaperBrokerAck:
        self._validate_request_identity(request)
        response = self._request("PATCH", f"/v2/orders/{broker_order_id}", json=_order_payload(request, replacement=True))
        return _ack_from_order(response, request)

    def cancel_order(self, broker_order_id: str) -> dict[str, Any]:
        return redact_secret_payload(self._request("DELETE", f"/v2/orders/{broker_order_id}", expected_statuses={200, 204}))

    def cancel_all_wca_entry_orders(self) -> tuple[dict[str, Any], ...]:
        cancelled = []
        for order in self._request("GET", "/v2/orders", params={"status": "open", "nested": "false"}):
            if _is_wca_order(order) and _order_side(order) in {"BUY", "SELL"} and not _is_protective_order(order):
                cancelled.append(self.cancel_order(str(order.get("id"))))
        return tuple(cancelled)

    def cancel_all_wca_protective_orders(self, *, symbol: str | None = None) -> tuple[dict[str, Any], ...]:
        cancelled = []
        for order in self._request("GET", "/v2/orders", params={"status": "open", "nested": "false"}):
            if symbol is not None and str(order.get("symbol") or "").upper() != symbol.upper():
                continue
            if _is_wca_order(order) and _is_protective_order(order):
                cancelled.append(self.cancel_order(str(order.get("id"))))
        return tuple(cancelled)

    def read_fills_and_activities(self, *, after: datetime | None = None) -> tuple[WcaPaperBrokerFill, ...]:
        params = {"activity_types": "FILL"}
        if after is not None:
            params["after"] = after.astimezone(timezone.utc).isoformat()
        rows = self._request("GET", "/v2/account/activities/FILL", params=params)
        return tuple(_fill_from_activity(row) for row in rows if str(row.get("client_order_id", "")).startswith("wca-"))

    def subscribe_trade_updates(self) -> tuple[str, ...]:
        return (WCA_ALPACA_ORDER_STREAM_UNAVAILABLE,)

    def poll_order_updates(self, client_order_id: str) -> dict[str, Any] | None:
        return self.find_order_by_client_order_id(client_order_id)

    def close_or_reduce_wca_position(self, *, symbol: str, quantity: int, side: WcaSide | str, client_order_id: str) -> WcaPaperBrokerAck:
        order = WcaPaperBrokerOrderRequest(
            account_id=self.account_id,
            symbol=symbol,
            side=WcaSide.SELL if _side_value(side) == WcaSide.BUY.value else WcaSide.BUY,
            quantity=quantity,
            order_type="LIMIT",
            limit_price=max(0.01, _mark_price(self.read_positions(), symbol)),
            client_order_id=client_order_id,
            idempotency_key=client_order_id,
            decision_id=client_order_id,
            order_intent_id=client_order_id,
            configuration_version="wca_alpaca_reduce_position",
        )
        return self.submit_order(order)

    def refresh_order(self, client_order_id: str) -> WcaPaperBrokerFill | None:
        order = self.find_order_by_client_order_id(client_order_id)
        if order is None:
            return None
        filled = int(float(order.get("filled_qty") or 0))
        quantity = int(float(order.get("qty") or 0))
        if filled <= 0:
            return None
        return WcaPaperBrokerFill(
            fill_id=str(order.get("id") or client_order_id),
            client_order_id=client_order_id,
            broker_order_id=str(order.get("id") or ""),
            filled_quantity=filled,
            remaining_quantity=max(0, quantity - filled),
            average_fill_price=_optional_float(order.get("filled_avg_price") or order.get("limit_price")),
            filled_at=_order_fill_timestamp(order),
            response_payload=redact_secret_payload(order),
        )

    def _validate_request_identity(self, request: WcaPaperBrokerOrderRequest) -> None:
        if request.algorithm_id != WCA_ALGORITHM_ID or request.account_id != self.account_id:
            raise WcaAlpacaPaperBrokerConfigurationError("wca.alpaca_paper.order_identity_mismatch")
        if not request.client_order_id.startswith("wca-"):
            raise WcaAlpacaPaperBrokerConfigurationError("wca.alpaca_paper.client_order_id_prefix_required")

    def _request(self, method: str, path: str, *, retry: bool = True, expected_statuses: set[int] | None = None, **kwargs: Any) -> Any:
        expected = expected_statuses or {200, 201, 202}
        attempts = self.max_retries + 1 if retry and method.upper() in {"GET", "DELETE", "PATCH"} else 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self.http_client.request(method.upper(), f"{self.base_url}{path}", headers=self._headers(), timeout=self.timeout_seconds, **kwargs)
                if response.status_code not in expected:
                    response.raise_for_status()
                if response.status_code == 204 or not getattr(response, "content", b""):
                    return {}
                return response.json()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt >= attempts - 1:
                    raise
                time.sleep(_BACKOFF_BASE_SECONDS * (2**attempt) + random.uniform(0, _BACKOFF_BASE_SECONDS))
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("wca.alpaca_paper.request_failed")

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type": "application/json",
        }


def _order_payload(request: WcaPaperBrokerOrderRequest, *, replacement: bool = False) -> dict[str, Any]:
    payload = {
        "symbol": request.symbol.upper(),
        "qty": str(request.quantity),
        "side": _side_value(request.side).lower(),
        "type": request.order_type.lower(),
        "time_in_force": request.time_in_force.lower(),
        "limit_price": str(request.limit_price),
        "client_order_id": request.client_order_id,
        "extended_hours": False,
    }
    if request.order_type == "STOP_LIMIT" and request.stop_price is not None:
        payload["stop_price"] = str(request.stop_price)
    if replacement:
        payload.pop("client_order_id", None)
        payload.pop("symbol", None)
        payload.pop("side", None)
    return payload


def _ack_from_order(order: dict[str, Any], request: WcaPaperBrokerOrderRequest) -> WcaPaperBrokerAck:
    status = str(order.get("status") or "").lower()
    if status in {"rejected", "canceled", "expired"}:
        ack_status = "REJECTED"
    else:
        ack_status = "ACKNOWLEDGED"
    return WcaPaperBrokerAck(
        status=ack_status,
        client_order_id=str(order.get("client_order_id") or request.client_order_id),
        broker_order_id=str(order.get("id") or ""),
        accepted_quantity=request.quantity if ack_status == "ACKNOWLEDGED" else 0,
        message=str(order.get("status") or ""),
        response_payload=redact_secret_payload(order),
        fill=_fill_from_order(order, request),
    )


def _fill_from_order(order: dict[str, Any], request: WcaPaperBrokerOrderRequest) -> WcaPaperBrokerFill | None:
    filled = int(float(order.get("filled_qty") or 0))
    if filled <= 0:
        return None
    return WcaPaperBrokerFill(
        fill_id=str(order.get("id") or request.client_order_id),
        client_order_id=str(order.get("client_order_id") or request.client_order_id),
        broker_order_id=str(order.get("id") or ""),
        filled_quantity=filled,
        remaining_quantity=max(0, request.quantity - filled),
        average_fill_price=_optional_float(order.get("filled_avg_price") or order.get("limit_price")),
        filled_at=_order_fill_timestamp(order),
        response_payload=redact_secret_payload(order),
    )


def _order_fill_timestamp(order: dict[str, Any]) -> datetime:
    return _parse_dt(order.get("filled_at") or order.get("updated_at") or order.get("submitted_at") or order.get("created_at"))


def _fill_from_activity(row: dict[str, Any]) -> WcaPaperBrokerFill:
    quantity = int(float(row.get("qty") or 0))
    return WcaPaperBrokerFill(
        fill_id=str(row.get("id") or row.get("order_id") or row.get("client_order_id")),
        client_order_id=str(row.get("client_order_id") or ""),
        broker_order_id=str(row.get("order_id") or ""),
        filled_quantity=quantity,
        remaining_quantity=0,
        average_fill_price=_optional_float(row.get("price")),
        filled_at=_parse_dt(row.get("transaction_time") or row.get("date")),
        response_payload=redact_secret_payload(row),
    )


def _position(row: dict[str, Any]) -> BrokerPositionState:
    qty = int(abs(float(row.get("qty") or 0)))
    side = Signal.BUY if float(row.get("qty") or 0) >= 0 else Signal.SELL
    average_entry_price = _required_positive_float(row.get("avg_entry_price"), "wca.alpaca_paper.position_entry_price_missing")
    mark_price = _required_positive_float(row.get("current_price") or row.get("market_value") or row.get("avg_entry_price"), "wca.alpaca_paper.position_mark_price_missing")
    return BrokerPositionState(
        algorithmId="wca",
        capitalPartitionId="wca.alpaca_paper",
        decisionId=_identity_hint(row, 1),
        orderIntentId=_identity_hint(row, 2),
        positionOwner="wca",
        symbol=str(row.get("symbol") or "SPY").upper(),
        side=side,
        quantity=qty,
        averageEntryPrice=average_entry_price,
        markPrice=mark_price,
    )


def _order(row: dict[str, Any]) -> BrokerOrderState:
    submitted = _parse_dt(row.get("submitted_at") or row.get("created_at"))
    return BrokerOrderState(
        algorithmId="wca",
        capitalPartitionId="wca.alpaca_paper",
        decisionId=_identity_hint(row, 1),
        orderIntentId=_identity_hint(row, 2),
        positionOwner="wca",
        symbol=str(row.get("symbol") or "SPY").upper(),
        side=Signal.BUY if _order_side(row) == "BUY" else Signal.SELL,
        clientOrderId=str(row.get("client_order_id") or ""),
        orderType=str(row.get("type") or "limit").upper(),
        status=_order_status(row),
        quantity=int(float(row.get("qty") or 0)),
        filledQuantity=int(float(row.get("filled_qty") or 0)),
        entryPrice=_required_positive_float(row.get("limit_price") or row.get("filled_avg_price"), "wca.alpaca_paper.order_entry_price_missing"),
        submittedAt=submitted,
    )


def _is_wca_order(row: dict[str, Any]) -> bool:
    return str(row.get("client_order_id") or "").startswith("wca-")


def _is_protective_order(row: dict[str, Any]) -> bool:
    order_type = str(row.get("type") or "").lower()
    client_order_id = str(row.get("client_order_id") or "").lower()
    return order_type in {"stop", "stop_limit", "trailing_stop"} or "-exit-" in client_order_id or "-protection-" in client_order_id


def _order_status(row: dict[str, Any]) -> str:
    filled = float(row.get("filled_qty") or 0)
    qty = float(row.get("qty") or 0)
    status = str(row.get("status") or "").lower()
    if filled > 0 and filled < qty:
        return "PARTIALLY_FILLED"
    if status in {"accepted", "new", "pending_new"}:
        return "ACCEPTED"
    return "PENDING"


def _identity_hint(row: dict[str, Any], index: int) -> str | None:
    if index == 1 and row.get("decision_id"):
        return str(row.get("decision_id"))
    if index == 2 and row.get("order_intent_id"):
        return str(row.get("order_intent_id"))
    parts = str(row.get("client_order_id") or "").split("-")
    return parts[index] if len(parts) > index else None


def _account_identifier(account: dict[str, Any]) -> str:
    return str(account.get("account_number") or account.get("id") or account.get("accountId") or "").strip()


def _order_side(row: dict[str, Any]) -> str:
    return str(row.get("side") or "").upper()


def _side_value(side: WcaSide | str) -> str:
    return side.value if isinstance(side, WcaSide) else str(side)


def _mark_price(positions: list[BrokerPositionState], symbol: str) -> float:
    for position in positions:
        if position.symbol.upper() == symbol.upper():
            return float(position.markPrice)
    return 0.01


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: Any) -> float | None:
    parsed = _float(value)
    return parsed if parsed > 0 else None


def _required_positive_float(value: Any, reason_code: str) -> float:
    parsed = _float(value)
    if parsed <= 0:
        raise WcaAlpacaPaperBrokerConfigurationError(reason_code)
    return parsed


def _optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return _parse_dt(value)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value or _utc_now().isoformat()).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "WCA_ALPACA_ORDER_STREAM_UNAVAILABLE",
    "WCA_ALPACA_PAPER_BROKER_VERSION",
    "WcaAlpacaPaperBroker",
    "WcaAlpacaPaperBrokerConfigurationError",
    "WcaAlpacaPaperCredentials",
]
