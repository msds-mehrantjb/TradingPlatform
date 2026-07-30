"""Durable WCA paper-broker outbox adapter and order state machine."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, ProposedOrder, WcaContractModel, WcaOrderStatus, WcaSide
from backend.app.algorithms.wca.latency import utc_now, with_order_latency
from backend.app.algorithms.wca.order_validation import WCA_FINAL_PRE_OUTBOX_VALIDATION_PASSED
from backend.app.algorithms.wca.repository import WcaExecutionOutboxRecord, WcaRepository


WCA_PAPER_BROKER_ADAPTER_VERSION = "wca_paper_broker_outbox_v1"
WCA_REAL_MONEY_ENDPOINTS_AVAILABLE = False
WCA_ALPACA_CLIENT_ORDER_ID_LIMIT = 48


class WcaPaperBrokerTimeout(TimeoutError):
    """Raised when the paper broker request outcome is unknown."""


class WcaPaperBrokerOrderRequest(WcaContractModel):
    algorithm_id: str = WCA_ALGORITHM_ID
    account_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: WcaSide | str
    quantity: int = Field(gt=0)
    order_type: Literal["LIMIT", "STOP_LIMIT"] = "LIMIT"
    limit_price: float = Field(gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    client_order_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    order_intent_id: str = Field(min_length=1)
    configuration_version: str = Field(min_length=1)
    configuration_hash: str = ""
    time_in_force: Literal["DAY"] = "DAY"
    paper_only: bool = True

    @model_validator(mode="after")
    def validate_paper_order(self) -> "WcaPaperBrokerOrderRequest":
        if self.algorithm_id != WCA_ALGORITHM_ID:
            raise ValueError("WCA paper broker request must preserve algorithm_id=wca")
        if not self.paper_only:
            raise ValueError("WCA broker adapter is paper-only")
        if self.order_type == "STOP_LIMIT" and self.stop_price is None:
            raise ValueError("WCA stop-limit orders require a stop price")
        return self


class WcaPaperBrokerFill(WcaContractModel):
    fill_id: str = Field(min_length=1)
    client_order_id: str = Field(min_length=1)
    broker_order_id: str = Field(min_length=1)
    filled_quantity: int = Field(ge=0)
    remaining_quantity: int = Field(ge=0)
    average_fill_price: float | None = Field(default=None, gt=0)
    filled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    response_payload: dict[str, Any] = Field(default_factory=dict)


class WcaPaperBrokerAck(WcaContractModel):
    status: Literal["ACKNOWLEDGED", "REJECTED", "DUPLICATE"]
    client_order_id: str = Field(min_length=1)
    broker_order_id: str | None = Field(default=None, min_length=1)
    accepted_quantity: int = Field(default=0, ge=0)
    message: str = ""
    response_payload: dict[str, Any] = Field(default_factory=dict)
    fill: WcaPaperBrokerFill | None = None


class WcaPaperBrokerSubmissionResult(WcaContractModel):
    outbox_id: str | None = None
    client_order_id: str | None = None
    broker_order_id: str | None = None
    submitted: bool = False
    state: WcaOrderStatus | str
    reason_codes: tuple[str, ...]
    outbox_record: Any = None


class WcaPaperBrokerTransport(Protocol):
    def submit_order(self, request: WcaPaperBrokerOrderRequest) -> WcaPaperBrokerAck:
        ...

    def refresh_order(self, client_order_id: str) -> WcaPaperBrokerFill | None:
        ...


class WcaDeterministicPaperBroker:
    """Paper-only simulator used when no external paper transport is injected."""

    def __init__(self, *, ack_status: Literal["ACKNOWLEDGED", "REJECTED", "DUPLICATE"] = "ACKNOWLEDGED", fill: WcaPaperBrokerFill | None = None, timeout: bool = False) -> None:
        self.ack_status = ack_status
        self.fill = fill
        self.timeout = timeout
        self.submit_count = 0

    def submit_order(self, request: WcaPaperBrokerOrderRequest) -> WcaPaperBrokerAck:
        self.submit_count += 1
        if self.timeout:
            raise WcaPaperBrokerTimeout("paper broker submission timed out")
        broker_order_id = f"paper-{request.client_order_id}"
        fill = self.fill.model_copy(update={"client_order_id": request.client_order_id, "broker_order_id": broker_order_id}) if self.fill is not None else None
        return WcaPaperBrokerAck(
            status=self.ack_status,
            client_order_id=request.client_order_id,
            broker_order_id=broker_order_id,
            accepted_quantity=request.quantity if self.ack_status != "REJECTED" else 0,
            response_payload={"status": self.ack_status, "broker_order_id": broker_order_id},
            fill=fill,
        )

    def refresh_order(self, client_order_id: str) -> WcaPaperBrokerFill | None:
        return self.fill.model_copy(update={"client_order_id": client_order_id}) if self.fill is not None else None


class WcaPaperBrokerOutboxAdapter:
    def process_next_outbox(self, repository: WcaRepository, broker: WcaPaperBrokerTransport, *, owner_id: str) -> WcaPaperBrokerSubmissionResult:
        record = repository.claim_next_execution_outbox(owner_id=owner_id)
        if record is None:
            return WcaPaperBrokerSubmissionResult(state="IDLE", submitted=False, reason_codes=("wca.paper_broker.outbox_idle",))
        if WCA_FINAL_PRE_OUTBOX_VALIDATION_PASSED not in record.proposed_order.reason_codes:
            repository.update_execution_outbox_state(
                outbox_id=record.outbox_id,
                status=WcaOrderStatus.CANCELLED,
                error_payload={"reason_codes": (WCA_PAPER_BROKER_ADAPTER_VERSION, "wca.paper_broker.final_validation_missing")},
            )
            return WcaPaperBrokerSubmissionResult(
                outbox_id=record.outbox_id,
                client_order_id=record.client_order_id,
                submitted=False,
                state=WcaOrderStatus.CANCELLED,
                outbox_record=record,
                reason_codes=(WCA_PAPER_BROKER_ADAPTER_VERSION, "wca.paper_broker.final_validation_missing"),
            )
        request = WcaPaperBrokerOrderRequest.model_validate(record.request_payload)
        broker_request_timestamp = utc_now()
        try:
            ack = broker.submit_order(request)
        except WcaPaperBrokerTimeout as exc:
            latency = with_order_latency(record.decision.latency, outbox_reservation=broker_request_timestamp, broker_request=broker_request_timestamp)
            repository.update_execution_outbox_state(
                outbox_id=record.outbox_id,
                status=WcaOrderStatus.SUBMISSION_UNKNOWN,
                error_payload=redact_secret_payload({"error": str(exc), "request": request.model_dump(mode="json"), "latency": latency.model_dump(mode="json")}),
            )
            return WcaPaperBrokerSubmissionResult(
                outbox_id=record.outbox_id,
                client_order_id=request.client_order_id,
                submitted=True,
                state=WcaOrderStatus.SUBMISSION_UNKNOWN,
                outbox_record=record,
                reason_codes=(WCA_PAPER_BROKER_ADAPTER_VERSION, "wca.paper_broker.submission_unknown_timeout"),
            )
        return self.apply_acknowledgement(repository, record, ack, request=request, broker_request_timestamp=broker_request_timestamp)

    def apply_acknowledgement(
        self,
        repository: WcaRepository,
        record: WcaExecutionOutboxRecord,
        ack: WcaPaperBrokerAck,
        *,
        request: WcaPaperBrokerOrderRequest | None = None,
        broker_request_timestamp: datetime | None = None,
    ) -> WcaPaperBrokerSubmissionResult:
        acknowledgement_timestamp = utc_now()
        request_payload = redact_secret_payload((request or WcaPaperBrokerOrderRequest.model_validate(record.request_payload)).model_dump(mode="json"))
        response_payload = redact_secret_payload(ack.model_dump(mode="json"))
        fill_quality = _fill_quality(record.proposed_order, ack.fill)
        latency = with_order_latency(
            record.decision.latency,
            outbox_reservation=broker_request_timestamp,
            broker_request=broker_request_timestamp,
            broker_acknowledgement=acknowledgement_timestamp,
            first_fill=ack.fill.filled_at if ack.fill is not None and ack.fill.filled_quantity > 0 else None,
            final_fill=ack.fill.filled_at if ack.fill is not None and ack.fill.remaining_quantity == 0 else None,
            slippage_per_share=_slippage(record.proposed_order, ack.fill),
            fill_quality=fill_quality,
        )
        if ack.status == "REJECTED":
            state = WcaOrderStatus.REJECTED
            reasons = (WCA_PAPER_BROKER_ADAPTER_VERSION, "wca.paper_broker.rejected")
        elif ack.status == "DUPLICATE":
            state = WcaOrderStatus.RECONCILIATION_REQUIRED
            reasons = (WCA_PAPER_BROKER_ADAPTER_VERSION, "wca.paper_broker.duplicate_reconciliation_required")
        else:
            state = _state_from_fill(ack.fill, record.proposed_order.quantity)
            reasons = (WCA_PAPER_BROKER_ADAPTER_VERSION, "wca.paper_broker.acknowledged")
        broker_order_id = ack.broker_order_id or f"paper-unknown-{record.client_order_id}"
        repository.record_broker_order(
            record.decision.model_copy(update={"proposed_order": record.proposed_order}),
            broker_order_id=broker_order_id,
            account_id=record.account_id,
            idempotency_key=record.idempotency_key,
            status=_value(state),
            payload={"request": request_payload, "response": response_payload, "client_order_id": record.client_order_id, "latency": latency.model_dump(mode="json")},
        )
        if ack.status == "REJECTED":
            repository.record_order_terminal_inventory_event(
                record.decision.model_copy(update={"proposed_order": record.proposed_order}),
                account_id=record.account_id,
                client_order_id=record.client_order_id,
                broker_order_id=broker_order_id,
                event_type="ORDER_REJECTED",
                event_timestamp=acknowledgement_timestamp,
                payload={"request": request_payload, "response": response_payload, "reason_codes": reasons},
            )
        if ack.fill is not None and ack.fill.filled_quantity > 0:
            repository.apply_fill_and_update_position(
                record.decision.model_copy(update={"proposed_order": record.proposed_order}),
                fill_id=ack.fill.fill_id,
                account_id=record.account_id,
                quantity=ack.fill.filled_quantity,
                broker_order_id=broker_order_id,
                payload=_fill_position_payload(record, ack.fill, client_order_id=record.client_order_id, delayed=False, latency=latency),
            )
        repository.update_execution_outbox_state(
            outbox_id=record.outbox_id,
            status=state,
            response_payload={"request": request_payload, "response": response_payload, "latency": latency.model_dump(mode="json")},
        )
        return WcaPaperBrokerSubmissionResult(
            outbox_id=record.outbox_id,
            client_order_id=record.client_order_id,
            broker_order_id=broker_order_id,
            submitted=True,
            state=state,
            outbox_record=record,
            reason_codes=reasons,
        )

    def apply_delayed_fill(self, repository: WcaRepository, record: WcaExecutionOutboxRecord, fill: WcaPaperBrokerFill) -> WcaPaperBrokerSubmissionResult:
        state = _state_from_fill(fill, record.proposed_order.quantity)
        latency = with_order_latency(
            record.decision.latency,
            first_fill=fill.filled_at if fill.filled_quantity > 0 else None,
            final_fill=fill.filled_at if fill.remaining_quantity == 0 else None,
            slippage_per_share=_slippage(record.proposed_order, fill),
            fill_quality=_fill_quality(record.proposed_order, fill),
        )
        repository.apply_fill_and_update_position(
            record.decision.model_copy(update={"proposed_order": record.proposed_order}),
            fill_id=fill.fill_id,
            account_id=record.account_id,
            quantity=fill.filled_quantity,
            broker_order_id=fill.broker_order_id,
            payload=_fill_position_payload(record, fill, client_order_id=record.client_order_id, delayed=True, latency=latency),
        )
        repository.update_execution_outbox_state(outbox_id=record.outbox_id, status=state, response_payload={"delayed_fill": redact_secret_payload(fill.model_dump(mode="json")), "latency": latency.model_dump(mode="json")})
        return WcaPaperBrokerSubmissionResult(
            outbox_id=record.outbox_id,
            client_order_id=record.client_order_id,
            broker_order_id=fill.broker_order_id,
            submitted=False,
            state=state,
            outbox_record=record,
            reason_codes=(WCA_PAPER_BROKER_ADAPTER_VERSION, "wca.paper_broker.delayed_fill_applied"),
        )


def build_wca_paper_broker_request(proposed: ProposedOrder) -> WcaPaperBrokerOrderRequest:
    if proposed.idempotency_key is None:
        raise ValueError("WCA paper broker request requires an idempotency key")
    limit_price = proposed.limit_price or proposed.trigger_price
    if limit_price is None:
        raise ValueError("WCA paper broker request requires a limit price")
    order_type: Literal["LIMIT", "STOP_LIMIT"] = "STOP_LIMIT" if proposed.stop_price is not None and proposed.trigger_price is not None and proposed.trigger_price != limit_price else "LIMIT"
    return WcaPaperBrokerOrderRequest(
        account_id=proposed.account_id,
        symbol=proposed.symbol,
        side=proposed.side,
        quantity=proposed.quantity,
        order_type=order_type,
        limit_price=limit_price,
        stop_price=proposed.stop_price if order_type == "STOP_LIMIT" else None,
        target_price=proposed.target_price,
        client_order_id=stable_wca_client_order_id(proposed),
        idempotency_key=proposed.idempotency_key,
        decision_id=proposed.decision_id,
        order_intent_id=proposed.order_intent_id,
        configuration_version=proposed.configuration_version or "legacy_api_compatibility_boundary",
        configuration_hash=proposed.configuration_hash,
        paper_only=True,
    )


def _fill_position_payload(record: WcaExecutionOutboxRecord, fill: WcaPaperBrokerFill, *, client_order_id: str, delayed: bool, latency: object) -> dict[str, object]:
    proposed = record.proposed_order
    reason_codes = tuple(str(code) for code in proposed.reason_codes)
    position_effect = "exit" if any("risk_reducing_exit" in code or ".exit" in code for code in reason_codes) else "entry"
    return {
        "fill": redact_secret_payload(fill.model_dump(mode="json")),
        "client_order_id": client_order_id,
        "delayed": delayed,
        "account_id": record.account_id,
        "symbol": proposed.symbol,
        "side": _value(proposed.side),
        "entry_price": fill.average_fill_price or proposed.limit_price or proposed.trigger_price,
        "stop_price": proposed.stop_price,
        "target_price": proposed.target_price,
        "opened_at": fill.filled_at.astimezone(timezone.utc).isoformat(),
        "remaining_quantity": fill.remaining_quantity,
        "position_effect": position_effect,
        "order_intent_id": proposed.order_intent_id,
        "decision_id": proposed.decision_id,
        "configuration_version": proposed.configuration_version,
        "configuration_hash": proposed.configuration_hash,
        "latency": latency.model_dump(mode="json") if hasattr(latency, "model_dump") else latency,
    }


def stable_wca_client_order_id(proposed: ProposedOrder) -> str:
    digest = hashlib.sha256(f"{proposed.account_id}:{proposed.symbol}:{proposed.decision_id}:{proposed.order_intent_id}:{proposed.idempotency_key}".encode("utf-8")).hexdigest()[:12]
    raw = f"wca-{proposed.account_id}-{proposed.decision_id}-{proposed.order_intent_id}-{digest}"
    return re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")[:WCA_ALPACA_CLIENT_ORDER_ID_LIMIT]


def cancel_wca_paper_order(repository: WcaRepository, *, outbox_id: str, cancellation_idempotency_key: str, original_idempotency_key: str) -> bool:
    if cancellation_idempotency_key == original_idempotency_key:
        raise ValueError("WCA cancellation requires a new cancellation idempotency key")
    record = next((row for row in repository.list_execution_outbox_records() if row.outbox_id == outbox_id), None)
    updated = repository.update_execution_outbox_state(outbox_id=outbox_id, status=WcaOrderStatus.CANCELLED, response_payload={"cancellation_idempotency_key": cancellation_idempotency_key})
    if updated and record is not None:
        repository.record_order_terminal_inventory_event(
            record.decision.model_copy(update={"proposed_order": record.proposed_order}),
            account_id=record.account_id,
            client_order_id=record.client_order_id,
            broker_order_id=None,
            event_type="ORDER_CANCELLED",
            event_timestamp=utc_now(),
            payload={"cancellation_idempotency_key": cancellation_idempotency_key},
        )
    return updated


def replace_wca_paper_order_requires_new_intent(*, replacement_idempotency_key: str, original_idempotency_key: str) -> None:
    if replacement_idempotency_key == original_idempotency_key:
        raise ValueError("WCA replacement must use new idempotency semantics and a new order intent")


def redact_secret_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        redacted = {}
        for key, value in payload.items():
            lowered = str(key).lower()
            if lowered in {"client_order_id", "idempotency_key"}:
                redacted[key] = value
            elif any(marker in lowered for marker in ("secret", "password", "credential", "api_key", "access_token", "refresh_token")):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = redact_secret_payload(value)
        return redacted
    if isinstance(payload, list):
        return [redact_secret_payload(value) for value in payload]
    return payload


def _state_from_fill(fill: WcaPaperBrokerFill | None, requested_quantity: int) -> WcaOrderStatus:
    if fill is None or fill.filled_quantity <= 0:
        return WcaOrderStatus.BROKER_ACKNOWLEDGED
    if fill.filled_quantity < requested_quantity or fill.remaining_quantity > 0:
        return WcaOrderStatus.PARTIALLY_FILLED
    return WcaOrderStatus.FILLED


def _slippage(proposed: ProposedOrder, fill: WcaPaperBrokerFill | None) -> float | None:
    if fill is None or fill.average_fill_price is None:
        return None
    expected = proposed.limit_price or proposed.trigger_price
    if expected is None:
        return None
    return max(0.0, abs(fill.average_fill_price - expected))


def _fill_quality(proposed: ProposedOrder, fill: WcaPaperBrokerFill | None) -> str | None:
    if fill is None or fill.filled_quantity <= 0:
        return None
    if fill.remaining_quantity > 0 or fill.filled_quantity < proposed.quantity:
        return "partial_fill"
    slippage = _slippage(proposed, fill) or 0.0
    if slippage <= 1e-9:
        return "at_limit"
    return "price_improved_or_slipped"


def _value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


__all__ = [
    "WCA_PAPER_BROKER_ADAPTER_VERSION",
    "WCA_ALPACA_CLIENT_ORDER_ID_LIMIT",
    "WCA_REAL_MONEY_ENDPOINTS_AVAILABLE",
    "WcaDeterministicPaperBroker",
    "WcaPaperBrokerAck",
    "WcaPaperBrokerFill",
    "WcaPaperBrokerOrderRequest",
    "WcaPaperBrokerOutboxAdapter",
    "WcaPaperBrokerSubmissionResult",
    "WcaPaperBrokerTimeout",
    "WcaPaperBrokerTransport",
    "build_wca_paper_broker_request",
    "cancel_wca_paper_order",
    "redact_secret_payload",
    "replace_wca_paper_order_requires_new_intent",
    "stable_wca_client_order_id",
]
