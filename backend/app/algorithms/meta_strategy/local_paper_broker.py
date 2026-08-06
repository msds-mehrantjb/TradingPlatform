"""Local paper broker and risk-source adapter for Meta-Strategy testing."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill
from backend.app.gates import GlobalGateResponse, GlobalOrderProposal


META_STRATEGY_LOCAL_PAPER_BROKER_VERSION = "meta_strategy_local_paper_broker_v1"


class MetaStrategyLocalPaperBroker:
    """Adapter for an explicitly configured local PAPER-only account/risk service."""

    broker_kind = "local_paper"
    paper_endpoint = True

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout_seconds: float = 5.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("META_STRATEGY_LOCAL_PAPER_BASE_URL") or "").rstrip("/")
        if not self.base_url:
            raise ValueError("meta_strategy.local_paper.base_url_required")
        self.token = token if token is not None else os.getenv("META_STRATEGY_LOCAL_PAPER_TOKEN")
        self.account_path = _path("META_STRATEGY_LOCAL_PAPER_ACCOUNT_PATH", "/account")
        self.clock_path = _path("META_STRATEGY_LOCAL_PAPER_CLOCK_PATH", "/clock")
        self.orders_path = _path("META_STRATEGY_LOCAL_PAPER_ORDERS_PATH", "/orders")
        self.positions_path = _path("META_STRATEGY_LOCAL_PAPER_POSITIONS_PATH", "/positions")
        self.risk_snapshot_path = _path("META_STRATEGY_LOCAL_RISK_SNAPSHOT_PATH", "/risk/snapshot")
        self.risk_approval_path = _path("META_STRATEGY_LOCAL_RISK_APPROVAL_PATH", "/risk/approve")
        self._owned_client = (
            None
            if http_client is not None
            else httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 2.0)), trust_env=False)
        )
        self.client = http_client or self._owned_client

    @property
    def configured(self) -> bool:
        return True

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()

    def verify_paper_account(self) -> bool:
        try:
            payload = self._get_json(self.account_path)
        except httpx.HTTPError:
            return False
        live_flag = payload.get("liveTradingEnabled")
        if live_flag is None:
            live_flag = payload.get("live_trading_enabled")
        if _truthy(live_flag):
            return False
        account_type = str(payload.get("accountType") or payload.get("account_type") or payload.get("type") or "paper").lower()
        if account_type == "live":
            return False
        return bool(payload.get("accountId") or payload.get("account_id") or payload.get("id") or payload.get("account_number"))

    def read_account_snapshot(self, *, at: datetime) -> Mapping[str, Any] | None:
        try:
            payload = self._get_json(self.account_path, params={"at": at.isoformat()})
        except httpx.HTTPError:
            return None
        captured = str(payload.get("capturedAt") or payload.get("captured_at") or payload.get("updatedAt") or datetime.now(UTC).isoformat())
        return {
            "source": "local_paper_account",
            "authoritativeReadOnly": True,
            "accountId": str(payload.get("accountId") or payload.get("account_id") or payload.get("id") or "local-paper"),
            "capturedAt": captured,
            "accountEquity": _number(payload, "accountEquity", "account_equity", "equity", "portfolioValue"),
            "buyingPower": _number(payload, "buyingPower", "buying_power", "availableBuyingPower"),
            "cashAvailable": _number(payload, "cashAvailable", "cash_available", "cash"),
            "paperAccountVerified": self.verify_paper_account(),
            "reasonCodes": ("meta_strategy.local_paper.account_snapshot_loaded",),
        }

    def read_global_risk_snapshot(self, *, at: datetime, capital_partition_id: str) -> Mapping[str, Any] | None:
        try:
            payload = self._get_json(self.risk_snapshot_path, params={"at": at.isoformat(), "capitalPartitionId": capital_partition_id})
        except httpx.HTTPError:
            return None
        captured = str(payload.get("capturedAt") or payload.get("captured_at") or payload.get("updatedAt") or datetime.now(UTC).isoformat())
        reject = _truthy(payload.get("reject") or payload.get("rejected") or payload.get("tradingHalt") or payload.get("trading_halt"))
        return {
            "source": "local_paper_risk",
            "authoritativeReadOnly": True,
            "capitalPartitionId": capital_partition_id,
            "capturedAt": captured,
            "availableRiskDollars": _number(payload, "availableRiskDollars", "available_risk_dollars", "remainingAlgorithmRisk", "remaining_algorithm_risk"),
            "maxQuantity": int(_number(payload, "maxQuantity", "max_quantity", "globalQuantityCap", default=0) or 0),
            "reject": reject,
            "tradingHalt": _truthy(_first_present(payload, "tradingHalt", "trading_halt")),
            "reasonCodes": tuple(payload.get("reasonCodes") or payload.get("reason_codes") or ("meta_strategy.local_paper.risk_snapshot_loaded",)),
        }

    def approve_order(self, proposal: GlobalOrderProposal) -> GlobalGateResponse:
        evaluated_at = datetime.now(UTC)
        try:
            payload = self._post_json(self.risk_approval_path, proposal.model_dump(mode="json"))
        except httpx.HTTPError:
            return _reject_response(
                proposal,
                evaluated_at=evaluated_at,
                reason="meta_strategy.local_paper.risk_approval_unavailable",
            )
        action = str(payload.get("action") or payload.get("status") or "REJECT_NEW_ENTRY").upper()
        if action in {"ALLOW", "APPROVED", "PASS"}:
            action = "ALLOW"
        elif action in {"REDUCE", "REDUCED", "REDUCE_QUANTITY"}:
            action = "REDUCE_QUANTITY"
        elif action not in {"REJECT_NEW_ENTRY", "EXIT_ONLY", "EMERGENCY_LIQUIDATE"}:
            action = "REJECT_NEW_ENTRY"
        max_quantity = int(_number(payload, "maximumAllowedQuantity", "maximum_allowed_quantity", "approvedQuantity", "approved_quantity", default=0) or 0)
        max_risk = _number(payload, "maximumAdditionalRiskDollars", "maximum_additional_risk_dollars", "approvedRiskDollars", "approved_risk_dollars", default=0.0) or 0.0
        reasons = tuple(payload.get("rejectionReasons") or payload.get("rejection_reasons") or payload.get("reasonCodes") or payload.get("reason_codes") or ())
        return GlobalGateResponse(
            action=action,  # type: ignore[arg-type]
            maximumAllowedQuantity=max(0, max_quantity),
            maximumAdditionalRiskDollars=max(0.0, float(max_risk)),
            rejectionReasons=reasons,
            emergencyAction=payload.get("emergencyAction") or payload.get("emergency_action"),
            evaluatedAt=_parse_time(payload.get("evaluatedAt") or payload.get("evaluated_at")) or evaluated_at,
            configurationHash=str(payload.get("configurationHash") or payload.get("configuration_hash") or "meta_strategy.local_paper_risk"),
        )

    def get_clock(self) -> dict[str, Any]:
        try:
            payload = self._get_json(self.clock_path)
        except httpx.HTTPError:
            return {
                "source": "local_paper_clock",
                "isOpen": False,
                "status": "unavailable",
                "authoritativeReadOnly": False,
                "fresh": False,
                "canAuthorizeNewEntries": False,
                "reasonCodes": ("meta_strategy.local_paper.clock_unavailable",),
            }
        timestamp = str(payload.get("dataSourceTimestamp") or payload.get("timestamp") or payload.get("capturedAt") or datetime.now(UTC).isoformat())
        return {
            "source": "local_paper_clock",
            "capturedAt": str(payload.get("capturedAt") or timestamp),
            "dataSourceTimestamp": timestamp,
            "isOpen": bool(payload.get("isOpen") if "isOpen" in payload else payload.get("is_open")),
            "status": str(payload.get("status") or ("open" if payload.get("isOpen") or payload.get("is_open") else "closed")),
            "nextOpen": payload.get("nextOpen") or payload.get("next_open"),
            "nextClose": payload.get("nextClose") or payload.get("next_close"),
            "regularSessionOpen": payload.get("regularSessionOpen") or payload.get("regular_session_open"),
            "regularSessionClose": payload.get("regularSessionClose") or payload.get("regular_session_close"),
            "holiday": bool(payload.get("holiday")),
            "earlyClose": bool(payload.get("earlyClose") or payload.get("early_close")),
            "authoritativeReadOnly": True,
            "fresh": payload.get("fresh", True) is True,
            "canAuthorizeNewEntries": payload.get("canAuthorizeNewEntries", True) is True,
            "reasonCodes": tuple(payload.get("reasonCodes") or payload.get("reason_codes") or ("meta_strategy.local_paper.clock_loaded",)),
        }

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        body = {
            "algorithmId": "meta_strategy",
            "capitalPartitionId": getattr(intent, "capitalPartitionId", "meta_strategy.paper.default"),
            "orderIntentId": intent.orderIntentId,
            "clientOrderId": intent.clientOrderId,
            "symbol": intent.symbol,
            "side": "buy" if intent.side == Signal.BUY else "sell",
            "quantity": int(intent.submittedQuantity),
            "orderType": getattr(intent, "orderType", "MARKETABLE_LIMIT"),
            "limitPrice": intent.limitPrice,
            "stopPrice": intent.stopPrice,
            "targetPrice": intent.targetPrice,
            "timeInForce": getattr(intent, "timeInForce", "DAY"),
            "paperOnly": True,
            "liveTradingEnabled": False,
        }
        try:
            payload = self._post_json(self.orders_path, body)
        except httpx.TimeoutException as exc:
            raise TimeoutError("meta_strategy.local_paper.submission_timeout") from exc
        except httpx.HTTPError as exc:
            return PaperGatewayBrokerAck(
                clientOrderId=intent.clientOrderId,
                brokerOrderId=None,
                status="REJECTED",
                acceptedAt=None,
                rejectedReason=str(exc)[:300],
            )
        return PaperGatewayBrokerAck(
            clientOrderId=str(payload.get("clientOrderId") or payload.get("client_order_id") or intent.clientOrderId),
            brokerOrderId=str(payload.get("brokerOrderId") or payload.get("broker_order_id") or payload.get("orderId") or ""),
            status=_broker_status(str(payload.get("status") or "ACCEPTED")),
            acceptedAt=_parse_time(payload.get("acceptedAt") or payload.get("accepted_at") or payload.get("submittedAt")) or datetime.now(UTC),
            rejectedReason=str(payload.get("rejectedReason") or payload.get("rejected_reason") or "") or None,
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        try:
            payload = self._get_json(f"{self.orders_path}/{client_order_id}")
        except httpx.HTTPError:
            return None
        filled = int(_number(payload, "filledQuantity", "filled_quantity", "filledQty", default=0) or 0)
        if filled <= 0:
            return None
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="meta_strategy",
            orderIntentId=str(payload.get("orderIntentId") or payload.get("order_intent_id") or client_order_id),
            symbol=str(payload.get("symbol") or "UNKNOWN").upper(),
            side=Signal.SELL if str(payload.get("side") or "").lower() == "sell" else Signal.BUY,
            filledQuantity=filled,
            averageFillPrice=float(_number(payload, "averageFillPrice", "average_fill_price", "filledAvgPrice", default=0.01) or 0.01),
            status=_broker_status(str(payload.get("status") or "FILLED")),
            filledAt=_parse_time(payload.get("filledAt") or payload.get("filled_at") or payload.get("updatedAt")) or datetime.now(UTC),
        )

    def cancel_order(self, client_order_id: str) -> bool:
        try:
            response = self.client.delete(f"{self.base_url}{self.orders_path}/{client_order_id}", headers=self._headers())
            return response.status_code in {200, 202, 204}
        except httpx.HTTPError:
            return False

    def refresh_positions(self) -> list[dict[str, Any]]:
        try:
            payload = self._get_json(self.positions_path)
        except httpx.HTTPError:
            return []
        rows = (payload.get("positions") or payload.get("items")) if isinstance(payload, Mapping) else payload
        return [dict(row) for row in rows] if isinstance(rows, list) else []

    def list_order_events(self) -> list[dict[str, Any]]:
        try:
            payload = self._get_json(self.orders_path, params={"status": "all", "limit": 100})
        except httpx.HTTPError:
            return []
        rows = (payload.get("orders") or payload.get("items")) if isinstance(payload, Mapping) else payload
        return [_event_from_order(row) for row in rows] if isinstance(rows, list) else []

    def replace_order(self, broker_order_id: str, **updates: Any) -> dict[str, Any] | None:
        if not broker_order_id:
            return None
        try:
            payload = self._patch_json(f"{self.orders_path}/{broker_order_id}", updates)
        except httpx.HTTPError:
            return None
        return _event_from_order(payload)

    def _get_json(self, path: str, *, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(f"{self.base_url}{path}", headers=self._headers(), params=params)
        response.raise_for_status()
        payload = response.json()
        return dict(payload) if isinstance(payload, Mapping) else {"items": payload}

    def _post_json(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        response = self.client.post(f"{self.base_url}{path}", headers=self._headers(), json=dict(payload))
        response.raise_for_status()
        data = response.json()
        return dict(data) if isinstance(data, Mapping) else {"items": data}

    def _patch_json(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        response = self.client.patch(f"{self.base_url}{path}", headers=self._headers(), json=dict(payload))
        response.raise_for_status()
        data = response.json()
        return dict(data) if isinstance(data, Mapping) else {"items": data}

    def _headers(self) -> dict[str, str]:
        headers = {"X-Meta-Strategy-Paper-Only": "true"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


def _reject_response(proposal: GlobalOrderProposal, *, evaluated_at: datetime, reason: str) -> GlobalGateResponse:
    return GlobalGateResponse(
        action="REJECT_NEW_ENTRY",
        maximumAllowedQuantity=0,
        maximumAdditionalRiskDollars=0.0,
        rejectionReasons=(reason,),
        evaluatedAt=evaluated_at,
        configurationHash="meta_strategy.local_paper_risk_unavailable",
    )


def _event_from_order(order: Mapping[str, Any]) -> dict[str, Any]:
    client_order_id = str(order.get("clientOrderId") or order.get("client_order_id") or "")
    return {
        "brokerEventId": str(order.get("brokerEventId") or order.get("broker_event_id") or order.get("brokerOrderId") or client_order_id),
        "algorithmId": "meta_strategy",
        "clientOrderId": client_order_id,
        "brokerOrderId": str(order.get("brokerOrderId") or order.get("broker_order_id") or ""),
        "orderIntentId": str(order.get("orderIntentId") or order.get("order_intent_id") or client_order_id),
        "status": _broker_status(str(order.get("status") or "OPEN")),
        "symbol": str(order.get("symbol") or "UNKNOWN").upper(),
        "side": str(order.get("side") or "buy").upper(),
        "filledQuantity": int(_number(order, "filledQuantity", "filled_quantity", default=0) or 0),
        "averageFillPrice": float(_number(order, "averageFillPrice", "average_fill_price", default=0.0) or 0.0),
        "timestamp": str(order.get("timestamp") or order.get("updatedAt") or datetime.now(UTC).isoformat()),
    }


def _path(env_name: str, default: str) -> str:
    value = os.getenv(env_name, default).strip() or default
    return value if value.startswith("/") else f"/{value}"


def _number(payload: Mapping[str, Any], *keys: str, default: float | None = None) -> float | None:
    for key in keys:
        if payload.get(key) is not None:
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                return default
    return default


def _first_present(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if payload.get(key) is not None:
            return payload[key]
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "live"}


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _broker_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"accepted", "new", "pending_new", "open"}:
        return "ACCEPTED"
    if normalized in {"partially_filled", "partial"}:
        return "PARTIALLY_FILLED"
    if normalized == "filled":
        return "FILLED"
    if normalized in {"canceled", "cancelled", "expired"}:
        return "CANCELED"
    if normalized == "rejected":
        return "REJECTED"
    return value.upper() or "OPEN"


__all__ = ["META_STRATEGY_LOCAL_PAPER_BROKER_VERSION", "MetaStrategyLocalPaperBroker"]
