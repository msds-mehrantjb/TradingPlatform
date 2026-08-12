"""Durable WCA paper-broker outbox adapter and order state machine."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Mapping, Protocol

from pydantic import Field, model_validator

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, ProposedOrder, WcaContractModel, WcaOrderStatus, WcaSide
from backend.app.algorithms.wca.latency import utc_now, with_order_latency
from backend.app.algorithms.wca.order_validation import WCA_FINAL_PRE_OUTBOX_VALIDATION_PASSED
from backend.app.algorithms.wca.position_management import WcaManagedPosition
from backend.app.algorithms.wca.repository import WcaExecutionOutboxRecord, WcaInventoryLedgerEvent, WcaRepository


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
    """Paper-only simulator for tests, explicit simulation, and replay paths."""

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
    def process_next_outbox(
        self,
        repository: WcaRepository,
        broker: WcaPaperBrokerTransport,
        *,
        owner_id: str,
        pre_submit_check: Callable[[WcaExecutionOutboxRecord, WcaPaperBrokerOrderRequest], tuple[bool, tuple[str, ...]]] | None = None,
    ) -> WcaPaperBrokerSubmissionResult:
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
        if pre_submit_check is not None:
            permitted, reason_codes = pre_submit_check(record, request)
            if not permitted:
                reasons = tuple(dict.fromkeys((WCA_PAPER_BROKER_ADAPTER_VERSION, *reason_codes)))
                repository.update_execution_outbox_state(
                    outbox_id=record.outbox_id,
                    status=WcaOrderStatus.CANCELLED,
                    error_payload={"reason_codes": reasons, "request": redact_secret_payload(request.model_dump(mode="json"))},
                )
                return WcaPaperBrokerSubmissionResult(
                    outbox_id=record.outbox_id,
                    client_order_id=request.client_order_id,
                    submitted=False,
                    state=WcaOrderStatus.CANCELLED,
                    outbox_record=record,
                    reason_codes=reasons,
                )
        existing_ack = _lookup_existing_order_ack(broker, request)
        if existing_ack is not None:
            result = self.apply_acknowledgement(repository, record, existing_ack, request=request, broker_request_timestamp=broker_request_timestamp, broker=broker)
            return result.model_copy(
                update={
                    "submitted": False,
                    "reason_codes": tuple(dict.fromkeys((*result.reason_codes, "wca.paper_broker.duplicate_client_order_reconciled"))),
                }
            )
        try:
            ack = broker.submit_order(request)
        except WcaPaperBrokerTimeout as exc:
            existing_ack = _lookup_existing_order_ack(broker, request)
            if existing_ack is not None:
                result = self.apply_acknowledgement(repository, record, existing_ack, request=request, broker_request_timestamp=broker_request_timestamp, broker=broker)
                return result.model_copy(
                    update={
                        "submitted": False,
                        "reason_codes": tuple(dict.fromkeys((*result.reason_codes, "wca.paper_broker.timeout_existing_order_reconciled"))),
                    }
                )
            latency = with_order_latency(record.decision.latency, outbox_reservation=broker_request_timestamp, broker_request=broker_request_timestamp)
            repository.update_execution_outbox_state(
                outbox_id=record.outbox_id,
                status=WcaOrderStatus.SUBMISSION_UNKNOWN,
                error_payload=redact_secret_payload(
                    {
                        "error": str(exc),
                        "request": request.model_dump(mode="json"),
                        "latency": latency.model_dump(mode="json"),
                        "client_order_id": request.client_order_id,
                        "reason_codes": (WCA_PAPER_BROKER_ADAPTER_VERSION, "wca.paper_broker.submission_unknown_lookup_required"),
                    }
                ),
            )
            return WcaPaperBrokerSubmissionResult(
                outbox_id=record.outbox_id,
                client_order_id=request.client_order_id,
                submitted=True,
                state=WcaOrderStatus.SUBMISSION_UNKNOWN,
                outbox_record=record,
                reason_codes=(WCA_PAPER_BROKER_ADAPTER_VERSION, "wca.paper_broker.submission_unknown_timeout"),
            )
        return self.apply_acknowledgement(repository, record, ack, request=request, broker_request_timestamp=broker_request_timestamp, broker=broker)

    def apply_acknowledgement(
        self,
        repository: WcaRepository,
        record: WcaExecutionOutboxRecord,
        ack: WcaPaperBrokerAck,
        *,
        request: WcaPaperBrokerOrderRequest | None = None,
        broker_request_timestamp: datetime | None = None,
        broker: WcaPaperBrokerTransport | None = None,
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
            protection = place_or_replace_wca_protective_orders(repository, broker=broker, record=record, fill=ack.fill)
            if protection["status"] == "failed":
                reasons = tuple(dict.fromkeys((*reasons, "wca.paper_broker.protective_order_failed")))
            elif protection["status"] == "protected":
                reasons = tuple(dict.fromkeys((*reasons, "wca.paper_broker.protective_orders_submitted")))
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

    def apply_delayed_fill(self, repository: WcaRepository, record: WcaExecutionOutboxRecord, fill: WcaPaperBrokerFill, *, broker: WcaPaperBrokerTransport | None = None) -> WcaPaperBrokerSubmissionResult:
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
        protection = place_or_replace_wca_protective_orders(repository, broker=broker, record=record, fill=fill)
        repository.update_execution_outbox_state(outbox_id=record.outbox_id, status=state, response_payload={"delayed_fill": redact_secret_payload(fill.model_dump(mode="json")), "latency": latency.model_dump(mode="json")})
        return WcaPaperBrokerSubmissionResult(
            outbox_id=record.outbox_id,
            client_order_id=record.client_order_id,
            broker_order_id=fill.broker_order_id,
            submitted=False,
            state=state,
            outbox_record=record,
            reason_codes=tuple(
                dict.fromkeys(
                    (
                        WCA_PAPER_BROKER_ADAPTER_VERSION,
                        "wca.paper_broker.delayed_fill_applied",
                        *(("wca.paper_broker.protective_order_failed",) if protection["status"] == "failed" else ()),
                        *(("wca.paper_broker.protective_orders_submitted",) if protection["status"] == "protected" else ()),
                    )
                )
            ),
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


def place_or_replace_wca_protective_orders(
    repository: WcaRepository,
    *,
    broker: WcaPaperBrokerTransport | None,
    record: WcaExecutionOutboxRecord,
    fill: WcaPaperBrokerFill,
) -> dict[str, Any]:
    if fill.filled_quantity <= 0 or _record_is_risk_reducing_exit(record):
        return {"status": "skipped", "reason_codes": ("wca.protection.not_entry_fill",)}
    entry_order = record.proposed_order
    if entry_order.stop_price is None or entry_order.target_price is None:
        _record_protection_failure(repository, record=record, reason="wca.protection.entry_missing_stop_or_target", fill=fill)
        return {"status": "failed", "reason_codes": ("wca.protection.entry_missing_stop_or_target",)}
    if broker is None or not hasattr(broker, "submit_order"):
        _record_protection_failure(repository, record=record, reason="wca.protection.broker_unavailable", fill=fill)
        return {"status": "failed", "reason_codes": ("wca.protection.broker_unavailable",)}
    quantity = _current_wca_open_quantity(repository, record, fill)
    if quantity <= 0:
        return {"status": "skipped", "reason_codes": ("wca.protection.position_flat",)}
    requests = _protective_order_requests(record, quantity)
    desired_client_ids = {request.client_order_id for _, request in requests}
    cancelled = _cancel_stale_protective_siblings(broker, record=record, desired_client_ids=desired_client_ids)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for protection_kind, request in requests:
        try:
            ack = _lookup_existing_order_ack(broker, request) or broker.submit_order(request)
        except WcaPaperBrokerTimeout as exc:
            _record_protection_failure(repository, record=record, reason="wca.protection.submission_unknown", fill=fill, payload={"error": str(exc), "client_order_id": request.client_order_id})
            rejected.append({"kind": protection_kind, "client_order_id": request.client_order_id, "reason": "submission_unknown"})
            continue
        protection_decision = _protective_decision(record, request, protection_kind=protection_kind)
        broker_order_id = ack.broker_order_id or f"wca-protection-unknown-{request.client_order_id}"
        repository.record_broker_order(
            protection_decision,
            broker_order_id=broker_order_id,
            account_id=record.account_id,
            idempotency_key=request.idempotency_key,
            status=WcaOrderStatus.REJECTED.value if ack.status == "REJECTED" else WcaOrderStatus.ACKNOWLEDGED.value,
            payload={
                "request": request.model_dump(mode="json"),
                "response": ack.model_dump(mode="json"),
                "client_order_id": request.client_order_id,
                "protection_kind": protection_kind,
                "protection_group_id": _protective_group_id(record),
                "entry_client_order_id": record.client_order_id,
                "sibling_client_order_ids": sorted(desired_client_ids),
                "ownership": _protective_ownership_payload(record),
            },
        )
        if ack.status == "REJECTED":
            _record_protection_failure(repository, record=record, reason="wca.protection.broker_rejected", fill=fill, payload={"response": ack.model_dump(mode="json"), "client_order_id": request.client_order_id})
            rejected.append({"kind": protection_kind, "client_order_id": request.client_order_id, "reason": "broker_rejected"})
            continue
        repository.record_protective_order_created(
            protection_decision,
            account_id=record.account_id,
            client_order_id=request.client_order_id,
            broker_order_id=broker_order_id,
            source_fill_id=fill.fill_id,
            protected_quantity=quantity,
            event_timestamp=fill.filled_at,
            payload={
                "actual_broker_protective_order": True,
                "protection_kind": protection_kind,
                "protection_group_id": _protective_group_id(record),
                "entry_client_order_id": record.client_order_id,
                "sibling_client_order_ids": sorted(desired_client_ids),
                "cancelled_stale_siblings": cancelled,
                "ownership": _protective_ownership_payload(record),
            },
        )
        accepted.append({"kind": protection_kind, "client_order_id": request.client_order_id, "broker_order_id": broker_order_id})
    if rejected:
        return {"status": "failed", "accepted": accepted, "rejected": rejected, "reason_codes": ("wca.protection.failed",)}
    return {"status": "protected", "accepted": accepted, "cancelled": cancelled, "reason_codes": ("wca.protection.actual_broker_orders_submitted",)}


def stable_wca_client_order_id(proposed: ProposedOrder) -> str:
    seed = f"{proposed.account_id}:{proposed.symbol}:{proposed.decision_id}:{proposed.order_intent_id}:{proposed.idempotency_key}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    account = _client_id_part(proposed.account_id, 17)
    symbol = _client_id_part(proposed.symbol, 5)
    decision = _client_id_part(proposed.decision_id, 4)
    intent = _client_id_part(proposed.order_intent_id, 4)
    raw = f"wca-{account}-{symbol}-{decision}-{intent}-{digest}"
    return raw[:WCA_ALPACA_CLIENT_ORDER_ID_LIMIT]


def _client_id_part(value: str | None, limit: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "")).strip("-")
    return (cleaned or "na")[:limit]


def _protective_order_requests(record: WcaExecutionOutboxRecord, quantity: int) -> tuple[tuple[str, WcaPaperBrokerOrderRequest], tuple[str, WcaPaperBrokerOrderRequest]]:
    entry = record.proposed_order
    exit_side = WcaSide.SELL if _value(entry.side) == WcaSide.BUY.value else WcaSide.BUY
    group_digest = _protective_group_digest(record)
    stop_client_id = _protective_client_order_id(record, "stop", quantity, group_digest)
    target_client_id = _protective_client_order_id(record, "target", quantity, group_digest)
    stop_price = float(entry.stop_price or entry.limit_price or entry.trigger_price or 0)
    target_price = float(entry.target_price or entry.limit_price or entry.trigger_price or 0)
    stop_request = WcaPaperBrokerOrderRequest(
        account_id=record.account_id,
        symbol=record.symbol,
        side=exit_side,
        quantity=quantity,
        order_type="STOP_LIMIT",
        limit_price=max(0.01, stop_price),
        stop_price=max(0.01, stop_price),
        target_price=target_price if target_price > 0 else None,
        client_order_id=stop_client_id,
        idempotency_key=f"wca-protection:{record.idempotency_key}:stop:{quantity}:{group_digest}",
        decision_id=f"{record.decision_id}:protection:stop",
        order_intent_id=f"{entry.order_intent_id}:protection:stop",
        configuration_version=entry.configuration_version or record.decision.configuration_version,
        configuration_hash=entry.configuration_hash,
    )
    target_request = WcaPaperBrokerOrderRequest(
        account_id=record.account_id,
        symbol=record.symbol,
        side=exit_side,
        quantity=quantity,
        order_type="LIMIT",
        limit_price=max(0.01, target_price),
        target_price=target_price if target_price > 0 else None,
        client_order_id=target_client_id,
        idempotency_key=f"wca-protection:{record.idempotency_key}:target:{quantity}:{group_digest}",
        decision_id=f"{record.decision_id}:protection:target",
        order_intent_id=f"{entry.order_intent_id}:protection:target",
        configuration_version=entry.configuration_version or record.decision.configuration_version,
        configuration_hash=entry.configuration_hash,
    )
    return (("stop", stop_request), ("target", target_request))


def _protective_ownership_payload(record: WcaExecutionOutboxRecord) -> dict[str, str]:
    entry = record.proposed_order
    return {
        "algorithm_id": WCA_ALGORITHM_ID,
        "protected_algorithm_id": WCA_ALGORITHM_ID,
        "position_owner": WCA_ALGORITHM_ID,
        "exit_owner": WCA_ALGORITHM_ID,
        "account_id": record.account_id,
        "local_account_id": record.account_id,
        "symbol": record.symbol,
        "position_id": f"wca-position-{record.account_id}-{record.symbol}-{entry.order_intent_id}",
        "local_position_id": f"wca-local-position-{record.account_id}-{record.symbol}",
        "entry_order_intent_id": entry.order_intent_id,
        "entry_decision_id": entry.decision_id,
    }


def _protective_decision(record: WcaExecutionOutboxRecord, request: WcaPaperBrokerOrderRequest, *, protection_kind: str):
    proposed = ProposedOrder(
        decision_id=request.decision_id,
        order_intent_id=request.order_intent_id,
        idempotency_key=request.idempotency_key,
        account_id=request.account_id,
        symbol=request.symbol,
        side=request.side if isinstance(request.side, WcaSide) else WcaSide(str(request.side)),
        quantity=request.quantity,
        trigger_price=request.stop_price if request.order_type == "STOP_LIMIT" else request.limit_price,
        limit_price=request.limit_price,
        stop_price=request.stop_price,
        target_price=request.target_price,
        configuration_version=request.configuration_version,
        configuration_hash=request.configuration_hash,
        status=WcaOrderStatus.BROKER_ACKNOWLEDGED,
        reason_codes=(
            WCA_PAPER_BROKER_ADAPTER_VERSION,
            "wca.protection.actual_broker_order",
            f"wca.protection.{protection_kind}",
            "wca.protection.risk_reducing_exit",
        ),
    )
    return record.decision.model_copy(
        update={
            "decision_id": request.decision_id,
            "proposed_order": proposed,
            "reason_codes": tuple(dict.fromkeys((*record.decision.reason_codes, "wca.protection.actual_broker_order", f"wca.protection.{protection_kind}"))),
        }
    )


def _current_wca_open_quantity(repository: WcaRepository, record: WcaExecutionOutboxRecord, fill: WcaPaperBrokerFill) -> int:
    reader = getattr(repository, "open_wca_position_quantity", None)
    if reader is None:
        return int(fill.filled_quantity)
    try:
        return abs(int(reader(account_id=record.account_id, symbol=record.symbol)))
    except Exception:
        return int(fill.filled_quantity)


def _protective_group_id(record: WcaExecutionOutboxRecord) -> str:
    return f"wca-protection:{record.account_id}:{record.symbol}:{record.order_intent_id}"


def _protective_group_digest(record: WcaExecutionOutboxRecord) -> str:
    return hashlib.sha256(_protective_group_id(record).encode("utf-8")).hexdigest()[:10]


def _protective_client_order_id(record: WcaExecutionOutboxRecord, kind: str, quantity: int, group_digest: str) -> str:
    account = _client_id_part(record.account_id, 12)
    symbol = _client_id_part(record.symbol, 5)
    raw = f"wca-protection-{kind[:1]}-{account}-{symbol}-{quantity}-{group_digest}"
    return raw[:WCA_ALPACA_CLIENT_ORDER_ID_LIMIT]


def _cancel_stale_protective_siblings(broker: WcaPaperBrokerTransport, *, record: WcaExecutionOutboxRecord, desired_client_ids: set[str]) -> list[str]:
    reader = getattr(broker, "read_open_orders", None)
    if reader is None:
        return []
    cancelled: list[str] = []
    group_digest = _protective_group_digest(record)
    for order in reader() or ():
        client_order_id = str(getattr(order, "clientOrderId", "") or "")
        if group_digest not in client_order_id or client_order_id in desired_client_ids:
            continue
        found = None
        finder = getattr(broker, "find_order_by_client_order_id", None)
        if finder is not None:
            try:
                found = finder(client_order_id)
            except Exception:
                found = None
        broker_order_id = str((found or {}).get("id") or (found or {}).get("broker_order_id") or "")
        cancel = getattr(broker, "cancel_order", None)
        if cancel is not None and broker_order_id:
            cancel(broker_order_id)
            cancelled.append(client_order_id)
    return cancelled


def _record_protection_failure(
    repository: WcaRepository,
    *,
    record: WcaExecutionOutboxRecord,
    reason: str,
    fill: WcaPaperBrokerFill,
    payload: dict[str, Any] | None = None,
) -> None:
    recorder = getattr(repository, "record_inventory_event", None)
    if recorder is None:
        return
    timestamp = fill.filled_at.astimezone(timezone.utc)
    open_quantity = _current_wca_open_quantity(repository, record, fill)
    position = WcaManagedPosition(
        account_id=record.account_id,
        symbol=record.symbol,
        side=record.proposed_order.side,
        open_quantity=max(0, open_quantity),
        average_entry_price=float(fill.average_fill_price or record.proposed_order.limit_price or record.proposed_order.trigger_price or 0),
        mark_price=float(fill.average_fill_price or record.proposed_order.limit_price or record.proposed_order.trigger_price or 0.01),
        stop_price=record.proposed_order.stop_price,
        target_price=record.proposed_order.target_price,
        emergency_exit_due=True,
        circuit_breaker_open=True,
        reason_codes=(reason, "wca.protection.actual_broker_order_required", "wca.position.circuit_breaker.unprotected_position"),
    )
    snapshot_writer = getattr(repository, "write_position_management_snapshot", None)
    critical_writer = getattr(repository, "record_position_management_critical_event", None)
    if snapshot_writer is not None:
        try:
            snapshot_writer(position, evaluated_at=timestamp)
        except Exception:
            pass
    if critical_writer is not None:
        try:
            critical_writer(position, evaluated_at=timestamp)
        except Exception:
            pass
    event = WcaInventoryLedgerEvent(
        inventory_event_id=f"wca-protection-critical-{record.order_intent_id}-{fill.fill_id}-{hashlib.sha256(reason.encode('utf-8')).hexdigest()[:8]}",
        event_type="RECONCILIATION_CORRECTION",
        broker_account_id=record.account_id,
        symbol=record.symbol,
        event_timestamp=timestamp,
        trade_date=timestamp.date().isoformat(),
        order_intent_id=record.order_intent_id,
        client_order_id=record.client_order_id,
        broker_order_id=fill.broker_order_id,
        side=_value(record.proposed_order.side),
        quantity=record.proposed_order.quantity,
        filled_quantity=fill.filled_quantity,
        remaining_quantity=fill.remaining_quantity,
        average_entry_price=float(fill.average_fill_price or record.proposed_order.limit_price or record.proposed_order.trigger_price or 0),
        source_authority="wca_protective_order_coordinator",
        configuration_version=record.proposed_order.configuration_version,
        decision_id=record.decision_id,
        run_id=record.run_id,
        payload={
            "critical": True,
            "circuit_breaker_state": "open",
            "protective_exit_required": True,
            "reason_codes": (reason, "wca.protection.actual_broker_order_required"),
            **(payload or {}),
        },
    )
    try:
        recorder(event)
    except Exception:
        return


def _record_is_risk_reducing_exit(record: WcaExecutionOutboxRecord) -> bool:
    reasons = tuple(str(code) for code in record.proposed_order.reason_codes)
    return any("risk_reducing_exit" in code or ".exit" in code for code in reasons)


def _lookup_existing_order_ack(broker: WcaPaperBrokerTransport, request: WcaPaperBrokerOrderRequest) -> WcaPaperBrokerAck | None:
    finder = getattr(broker, "find_order_by_client_order_id", None)
    if finder is None:
        return None
    try:
        found = finder(request.client_order_id)
    except KeyError:
        return None
    if found is None:
        return None
    if isinstance(found, WcaPaperBrokerAck):
        return found
    if isinstance(found, Mapping):
        return _ack_from_existing_order_payload(found, request)
    return None


def _ack_from_existing_order_payload(order: Mapping[str, Any], request: WcaPaperBrokerOrderRequest) -> WcaPaperBrokerAck:
    status = str(order.get("status") or "").lower()
    ack_status: Literal["ACKNOWLEDGED", "REJECTED", "DUPLICATE"] = "REJECTED" if status in {"rejected", "canceled", "cancelled", "expired"} else "DUPLICATE"
    filled_quantity = _int_from_payload(order.get("filled_qty") or order.get("filledQuantity"))
    order_quantity = _int_from_payload(order.get("qty") or order.get("quantity") or request.quantity)
    fill = None
    if filled_quantity > 0:
        fill = WcaPaperBrokerFill(
            fill_id=str(order.get("id") or order.get("broker_order_id") or request.client_order_id),
            client_order_id=str(order.get("client_order_id") or request.client_order_id),
            broker_order_id=str(order.get("id") or order.get("broker_order_id") or ""),
            filled_quantity=filled_quantity,
            remaining_quantity=max(0, order_quantity - filled_quantity),
            average_fill_price=_float_from_payload(order.get("filled_avg_price") or order.get("average_fill_price") or order.get("limit_price") or request.limit_price),
            response_payload=redact_secret_payload(dict(order)),
        )
    return WcaPaperBrokerAck(
        status=ack_status,
        client_order_id=str(order.get("client_order_id") or request.client_order_id),
        broker_order_id=str(order.get("id") or order.get("broker_order_id") or ""),
        accepted_quantity=0 if ack_status == "REJECTED" else min(request.quantity, order_quantity),
        message=status,
        response_payload=redact_secret_payload(dict(order)),
        fill=fill,
    )


def _int_from_payload(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _float_from_payload(value: Any) -> float | None:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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
    if fill.remaining_quantity == 0:
        return WcaOrderStatus.FILLED
    return WcaOrderStatus.PARTIALLY_FILLED


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
    "place_or_replace_wca_protective_orders",
    "redact_secret_payload",
    "replace_wca_paper_order_requires_new_intent",
    "stable_wca_client_order_id",
]
