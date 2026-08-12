"""WCA-owned local paper broker and account ledger."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from typing import Any, Mapping

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaOrderStatus, WcaSide
from backend.app.algorithms.wca.local_paper_account import (
    WCA_DEFAULT_LOCAL_PAPER_STARTING_BALANCE,
    WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
    WCA_LOCAL_PAPER_STARTING_BALANCE,
    WcaLocalPaperAccount,
    validate_wca_local_paper_account,
)
from backend.app.algorithms.wca.local_paper_risk import WcaLocalPaperRiskContext, WcaLocalPaperRiskManager
from backend.app.algorithms.wca.paper_broker import WcaPaperBrokerAck, WcaPaperBrokerFill, WcaPaperBrokerOrderRequest, place_or_replace_wca_protective_orders, redact_secret_payload
from backend.app.algorithms.wca.repository import WcaInventoryLedgerEvent, WcaSqliteRepository
from backend.app.algorithms.wca.session_validation import WcaBrokerClock
from backend.app.domain.models import Signal
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState


WCA_LOCAL_PAPER_BROKER_VERSION = "wca_local_paper_broker_v1"
_LOCAL_PARTITION_ID = "wca.local_paper"
_TERMINAL_ORDER_STATUSES = {
    WcaOrderStatus.FILLED.value,
    WcaOrderStatus.REJECTED.value,
    WcaOrderStatus.CANCELLED.value,
    WcaOrderStatus.RECONCILED.value,
}
WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED = "wca.local_paper.cross_algorithm_mutation_blocked"


@dataclass(frozen=True)
class WcaLocalPaperFillModel:
    slippage_bps: float = 0.0
    spread_cost_bps: float = 0.0
    commission_per_order: float = 0.0
    commission_per_share: float = 0.0
    minimum_commission: float = 0.0
    regulatory_fee_per_share: float = 0.0
    participation_limit: float = 1.0
    max_fill_quantity: int | None = None
    allow_partial_fills: bool = False
    allow_bar_execution: bool = False

    def __post_init__(self) -> None:
        if self.slippage_bps < 0 or self.spread_cost_bps < 0:
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.negative_execution_cost")
        if self.commission_per_order < 0 or self.commission_per_share < 0 or self.minimum_commission < 0 or self.regulatory_fee_per_share < 0:
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.negative_execution_cost")
        if self.participation_limit <= 0 or self.participation_limit > 1:
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.participation_limit_invalid")
        if self.max_fill_quantity is not None and self.max_fill_quantity <= 0:
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.max_fill_quantity_invalid")


@dataclass(frozen=True)
class _MarketContext:
    symbol: str | None
    timestamp: datetime
    price: float | None
    bid: float | None
    ask: float | None
    high: float | None
    low: float | None
    volume: int | None
    completed_bar: bool
    allow_bar_execution: bool

    @property
    def has_valid_quote(self) -> bool:
        return self.bid is not None and self.ask is not None and self.ask >= self.bid


class WcaLocalPaperBrokerConfigurationError(ValueError):
    pass


class WcaLocalPaperBroker:
    """Repository-backed WCA paper execution transport with no broker account dependency."""

    def __init__(
        self,
        *,
        repository: WcaSqliteRepository,
        account_id: str,
        symbol: str = "SPY",
        starting_balance: float = WCA_DEFAULT_LOCAL_PAPER_STARTING_BALANCE,
        fill_model: WcaLocalPaperFillModel | None = None,
        slippage_bps: float = 0.0,
        spread_cost_bps: float = 0.0,
        commission_per_order: float = 0.0,
        commission_per_share: float = 0.0,
        minimum_commission: float = 0.0,
        regulatory_fee_per_share: float = 0.0,
        participation_limit: float = 1.0,
        max_fill_quantity: int | None = None,
        allow_partial_fills: bool = False,
        allow_bar_execution: bool = False,
        buying_power_multiplier: float = 1.0,
        allow_short: bool = False,
    ) -> None:
        self.repository = repository
        self.account_id = str(account_id or "").strip()
        self.symbol = str(symbol or "SPY").upper()
        self.starting_balance = float(starting_balance)
        self.fill_model = fill_model or WcaLocalPaperFillModel(
            slippage_bps=float(slippage_bps),
            spread_cost_bps=float(spread_cost_bps),
            commission_per_order=float(commission_per_order),
            commission_per_share=float(commission_per_share),
            minimum_commission=float(minimum_commission),
            regulatory_fee_per_share=float(regulatory_fee_per_share),
            participation_limit=float(participation_limit),
            max_fill_quantity=max_fill_quantity,
            allow_partial_fills=bool(allow_partial_fills),
            allow_bar_execution=bool(allow_bar_execution),
        )
        self.buying_power_multiplier = float(buying_power_multiplier)
        self.allow_short = bool(allow_short)
        if not self.account_id:
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.account_id_required")
        if self.buying_power_multiplier <= 0:
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.buying_power_multiplier_invalid")
        if self.starting_balance <= 0:
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.starting_balance_invalid")
        self._ensure_bootstrap_state()

    @classmethod
    def from_env(
        cls,
        *,
        repository: WcaSqliteRepository,
        account_id: str,
        symbol: str = "SPY",
        environ: Mapping[str, str] | None = None,
    ) -> "WcaLocalPaperBroker":
        validation = validate_wca_local_paper_account(account_id=account_id, environ=environ)
        if not validation.verified:
            raise WcaLocalPaperBrokerConfigurationError(";".join(validation.reason_codes))
        source = environ or os.environ
        starting_balance = _float(source.get(WCA_LOCAL_PAPER_STARTING_BALANCE), validation.starting_balance)
        return cls(
            repository=repository,
            account_id=account_id,
            symbol=symbol,
            starting_balance=starting_balance,
            slippage_bps=_float(source.get("WCA_LOCAL_PAPER_SLIPPAGE_BPS"), 0.0),
            spread_cost_bps=_float(source.get("WCA_LOCAL_PAPER_SPREAD_COST_BPS"), 0.0),
            commission_per_order=_float(source.get("WCA_LOCAL_PAPER_COMMISSION_PER_ORDER"), 0.0),
            commission_per_share=_float(source.get("WCA_LOCAL_PAPER_COMMISSION_PER_SHARE"), 0.0),
            minimum_commission=_float(source.get("WCA_LOCAL_PAPER_MINIMUM_COMMISSION"), 0.0),
            regulatory_fee_per_share=_float(source.get("WCA_LOCAL_PAPER_REGULATORY_FEE_PER_SHARE"), 0.0),
            participation_limit=_float(source.get("WCA_LOCAL_PAPER_PARTICIPATION_LIMIT"), 1.0),
            max_fill_quantity=_optional_int(source.get("WCA_LOCAL_PAPER_MAX_FILL_QUANTITY")),
            allow_partial_fills=_bool(source.get("WCA_LOCAL_PAPER_ALLOW_PARTIAL_FILLS"), False),
            allow_bar_execution=_bool(source.get("WCA_LOCAL_PAPER_ALLOW_BAR_EXECUTION"), False),
            buying_power_multiplier=_float(source.get("WCA_LOCAL_PAPER_BUYING_POWER_MULTIPLIER"), 1.0),
            allow_short=_bool(source.get("WCA_LOCAL_PAPER_ALLOW_SHORT"), False),
        )

    def close(self) -> None:
        return None

    def verify_account_and_endpoint_identity(self) -> tuple[bool, tuple[str, ...]]:
        return True, (WCA_LOCAL_PAPER_BROKER_VERSION, "wca.local_paper.account_verified", "wca.local_paper.no_broker_execution")

    def read_clock(self) -> WcaBrokerClock:
        now = _utc_now()
        return WcaBrokerClock(
            timestamp=now,
            is_open=True,
            next_open=None,
            next_close=None,
            raw={"sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY, "paperOnly": True},
        )

    def reset_local_paper_account(
        self,
        *,
        starting_balance: float | None = None,
        reset_at: datetime | str | None = None,
        force: bool = False,
        reason: str = "wca.local_paper.reset_requested",
        command_id: str | None = None,
    ) -> dict[str, Any]:
        balance = float(starting_balance if starting_balance is not None else self.starting_balance)
        result = self.repository.reset_wca_local_paper_account(
            local_account_id=self.account_id,
            symbol=self.symbol,
            starting_balance=balance,
            reset_at=reset_at,
            force=force,
            reason=reason,
            command_id=command_id,
        )
        if result.get("reset"):
            self.starting_balance = balance
        return result

    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        self._ensure_bootstrap_state()
        now = _utc_now()
        return self._account(session_date=now.date()).to_broker_account_snapshot(symbol=self.symbol, observed_at=now)

    def refresh_account(self) -> dict[str, Any]:
        snapshot = self._account().get_account_snapshot()
        return {
            "id": self.account_id,
            "account_number": self.account_id,
            "equity": snapshot.equity,
            "buying_power": snapshot.buying_power,
            "cash": snapshot.cash,
            "realized_pnl": snapshot.realized_pnl,
            "unrealized_pnl": snapshot.unrealized_pnl,
            "reserved_risk": snapshot.reserved_risk,
            "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
        }

    def read_positions(self) -> list[BrokerPositionState]:
        return list(self._account().to_broker_account_snapshot(symbol=self.symbol).positions)

    def read_open_orders(self) -> list[BrokerOrderState]:
        orders: list[BrokerOrderState] = []
        for record in self.repository.list_execution_outbox_records(account_id=self.account_id):
            if record.symbol.upper() != self.symbol or _order_status_value(record.status) in _TERMINAL_ORDER_STATUSES:
                continue
            orders.append(_order_from_outbox(record))
        orders.extend(self._protective_orders())
        return orders

    def get_open_orders(self, symbol: str | None = None) -> list[BrokerOrderState]:
        selected_symbol = (symbol or self.symbol).upper()
        return [order for order in self.read_open_orders() if order.symbol.upper() == selected_symbol]

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        identifier = str(order_id or "").strip()
        if not identifier:
            return None
        with self.repository.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wca_broker_orders
                WHERE algorithm_id = ? AND account_id = ?
                  AND (broker_order_id = ? OR client_order_id = ?)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (WCA_ALGORITHM_ID, self.account_id, identifier, identifier),
            ).fetchone()
        return _order_payload_from_broker_row(row) if row is not None else None

    def find_order_by_client_order_id(self, client_order_id: str) -> dict[str, Any] | None:
        with self.repository.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wca_broker_orders
                WHERE algorithm_id = ? AND account_id = ? AND client_order_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (WCA_ALGORITHM_ID, self.account_id, client_order_id),
            ).fetchone()
        return _order_payload_from_broker_row(row) if row is not None else None

    def replace_order(self, broker_order_id: str, request: WcaPaperBrokerOrderRequest) -> WcaPaperBrokerAck:
        self._validate_request_identity(request, operation="replace")
        existing = self._lookup_order_any_scope(broker_order_id)
        if existing is None:
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.order_not_found")
        self._assert_order_mutation_scope(existing, operation="replace", symbol=request.symbol, require_position=_is_protective_payload(existing))
        if str(existing.get("status") or "").lower() in {"filled", "rejected", "canceled", "cancelled"}:
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.terminal_order_not_replaceable")
        selected_symbol = str(existing.get("symbol") or "").upper()
        broker_id = str(existing.get("id") or broker_order_id)
        response_payload = _response_payload(request, broker_id, status="accepted")
        now = _utc_now().isoformat()
        with self.repository.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE wca_broker_orders
                SET client_order_id = ?, order_intent_id = ?, idempotency_key = ?,
                    side = ?, quantity = ?, status = ?, request_payload_json = ?,
                    response_payload_json = ?
                WHERE algorithm_id = ? AND account_id = ? AND broker_order_id = ?
                  AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
                """,
                (
                    request.client_order_id,
                    request.order_intent_id,
                    request.idempotency_key,
                    _side_value(request.side),
                    int(request.quantity),
                    WcaOrderStatus.ACKNOWLEDGED.value,
                    json.dumps(request.model_dump(mode="json"), sort_keys=True),
                    json.dumps(response_payload, sort_keys=True),
                    WCA_ALGORITHM_ID,
                    self.account_id,
                    broker_id,
                ),
            )
            if cursor.rowcount != 1:
                raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.order_replace_conflict")
            conn.execute(
                """
                UPDATE wca_local_orders
                SET client_order_id = ?, side = ?, order_type = ?, quantity = ?,
                    remaining_quantity = ?, limit_price = ?, stop_price = ?,
                    target_price = ?, status = ?, updated_at = ?,
                    decision_id = ?, idempotency_key = ?, payload_json = ?
                WHERE algorithm_id = ? AND local_account_id = ? AND local_order_id = ?
                """,
                (
                    request.client_order_id,
                    _side_value(request.side),
                    request.order_type,
                    int(request.quantity),
                    int(request.quantity),
                    request.limit_price,
                    request.stop_price,
                    request.target_price,
                    WcaOrderStatus.ACKNOWLEDGED.value,
                    now,
                    request.decision_id,
                    request.idempotency_key,
                    json.dumps({"replacement": request.model_dump(mode="json"), "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY}, sort_keys=True),
                    WCA_ALGORITHM_ID,
                    self.account_id,
                    broker_id,
                ),
            )
        return WcaPaperBrokerAck(
            status="ACKNOWLEDGED",
            client_order_id=request.client_order_id,
            broker_order_id=broker_id,
            accepted_quantity=request.quantity,
            message="replaced_local_resting_order",
            response_payload=response_payload,
            fill=None,
        )

    def submit_order(self, request: WcaPaperBrokerOrderRequest) -> WcaPaperBrokerAck:
        self._validate_request_identity(request, operation="submit")
        broker_order_id = f"wca-local-{_digest(request.client_order_id)}"
        record = self._outbox_record_for_request(request)
        short_reasons = self._short_entry_rejection_reasons(request)
        if short_reasons:
            response_payload = _response_payload(request, broker_order_id, status="rejected")
            response_payload["localRisk"] = {"permitted": False, "reason_codes": short_reasons}
            return WcaPaperBrokerAck(
                status="REJECTED",
                client_order_id=request.client_order_id,
                broker_order_id=broker_order_id,
                accepted_quantity=0,
                message="local_paper_short_rejected",
                response_payload=redact_secret_payload(response_payload),
                fill=None,
            )
        risk_decision = WcaLocalPaperRiskManager().evaluate_order(
            WcaLocalPaperRiskContext(
                account_snapshot=self._risk_account_snapshot(),
                request=request,
                decision=record.decision if record is not None else None,
            )
        )
        if not risk_decision.permitted:
            response_payload = _response_payload(request, broker_order_id, status="rejected")
            response_payload["localRisk"] = risk_decision.__dict__
            return WcaPaperBrokerAck(
                status="REJECTED",
                client_order_id=request.client_order_id,
                broker_order_id=broker_order_id,
                accepted_quantity=0,
                message="local_paper_risk_rejected",
                response_payload=redact_secret_payload(response_payload),
                fill=None,
            )
        response_payload = _response_payload(request, broker_order_id, status="accepted")
        response_payload["localRisk"] = risk_decision.__dict__
        return WcaPaperBrokerAck(
            status="ACKNOWLEDGED",
            client_order_id=request.client_order_id,
            broker_order_id=broker_order_id,
            accepted_quantity=request.quantity,
            message="accepted_local_resting_order_pending_market_data",
            response_payload=redact_secret_payload(response_payload),
            fill=None,
        )

    def _outbox_record_for_request(self, request: WcaPaperBrokerOrderRequest):
        for record in self.repository.list_execution_outbox_records(account_id=self.account_id):
            if record.client_order_id == request.client_order_id or record.idempotency_key == request.idempotency_key:
                return record
        return None

    def refresh_order(self, client_order_id: str) -> WcaPaperBrokerFill | None:
        order = self.find_order_by_client_order_id(client_order_id)
        if order is None:
            return None
        filled = int(float(order.get("filled_qty") or order.get("filledQuantity") or 0))
        if filled <= 0:
            return None
        quantity = int(float(order.get("qty") or order.get("quantity") or filled))
        return WcaPaperBrokerFill(
            fill_id=str(order.get("fill_id") or order.get("id") or client_order_id),
            client_order_id=client_order_id,
            broker_order_id=str(order.get("id") or order.get("broker_order_id") or ""),
            filled_quantity=filled,
            remaining_quantity=max(0, quantity - filled),
            average_fill_price=_optional_float(order.get("filled_avg_price") or order.get("limit_price")),
            filled_at=_parse_dt(order.get("filled_at") or order.get("updated_at") or order.get("submitted_at")),
            response_payload=redact_secret_payload(dict(order)),
        )

    def poll_order_updates(self, client_order_id: str) -> dict[str, Any] | None:
        return self.find_order_by_client_order_id(client_order_id)

    def read_fills_and_activities(self, *, after: datetime | None = None) -> tuple[WcaPaperBrokerFill, ...]:
        cutoff = after.astimezone(timezone.utc) if after is not None else None
        with self.repository.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM wca_local_fills
                WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ?
                ORDER BY timestamp
                """,
                (WCA_ALGORITHM_ID, self.account_id, self.symbol),
            ).fetchall()
        fills: list[WcaPaperBrokerFill] = []
        for row in rows:
            filled_at = _parse_dt(row["timestamp"]) or _utc_now()
            if cutoff is not None and filled_at <= cutoff:
                continue
            payload = _json_payload(row["payload_json"])
            snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
            nested_fill = snapshot.get("fill") if isinstance(snapshot.get("fill"), dict) else {}
            client_order_id = str(
                payload.get("client_order_id")
                or snapshot.get("client_order_id")
                or nested_fill.get("client_order_id")
                or row["order_id"]
            )
            broker_order_id = str(snapshot.get("broker_order_id") or nested_fill.get("broker_order_id") or row["order_id"])
            fills.append(
                WcaPaperBrokerFill(
                    fill_id=row["fill_id"],
                    client_order_id=client_order_id,
                    broker_order_id=broker_order_id,
                    filled_quantity=int(row["quantity"]),
                    remaining_quantity=int(snapshot.get("remaining_quantity") or nested_fill.get("remaining_quantity") or 0),
                    average_fill_price=float(row["fill_price"]),
                    filled_at=filled_at,
                    response_payload=redact_secret_payload({**payload, "client_order_id": client_order_id, "broker_order_id": broker_order_id}),
                )
            )
        return tuple(fills)

    def process_market_update(self, market_update: Mapping[str, Any] | None = None, **kwargs: Any) -> tuple[WcaPaperBrokerFill, ...]:
        context = _market_context({**dict(market_update or {}), **kwargs}, fill_model=self.fill_model)
        if context.symbol and context.symbol != self.symbol:
            return ()
        self._expire_day_orders(context.timestamp)
        return self._process_orders(context, protective_only=False)

    def process_protective_orders(self, market_update: Mapping[str, Any] | _MarketContext | None = None, **kwargs: Any) -> tuple[WcaPaperBrokerFill, ...]:
        context = market_update if isinstance(market_update, _MarketContext) else _market_context({**dict(market_update or {}), **kwargs}, fill_model=self.fill_model)
        selected_symbol = context.symbol or self.symbol
        if selected_symbol != self.symbol:
            return ()
        return self._process_orders(context, protective_only=True)

    def _process_orders(self, context: _MarketContext, *, protective_only: bool) -> tuple[WcaPaperBrokerFill, ...]:
        selected_symbol = context.symbol or self.symbol
        fills: list[WcaPaperBrokerFill] = []
        for order in self._open_broker_order_payloads(symbol=selected_symbol, protective_only=protective_only):
            decision = _execution_decision(order, context, self.fill_model)
            if decision == "trigger_only":
                self._mark_stop_triggered(str(order["id"]), context.timestamp)
                continue
            if decision != "fill":
                continue
            fill_quantity = _fill_quantity_for_order(order, context, self.fill_model)
            if fill_quantity <= 0:
                continue
            fill = self.simulate_fill(
                str(order["id"]),
                fill_price=_fill_price_for_order(order, context, self.fill_model),
                quantity=fill_quantity,
                filled_at=context.timestamp,
            )
            if fill is not None:
                fills.append(fill)
        return tuple(fills)

    def simulate_fill(
        self,
        order_id: str | None = None,
        *,
        client_order_id: str | None = None,
        fill_price: float | None = None,
        quantity: int | None = None,
        filled_at: datetime | str | None = None,
    ) -> WcaPaperBrokerFill | None:
        identifier = order_id or client_order_id or ""
        order = self._lookup_order_any_scope(identifier)
        if order is None:
            return None
        selected_symbol = str(order.get("symbol") or "").upper()
        self._assert_order_mutation_scope(order, operation="fill", symbol=selected_symbol, require_position=_is_protective_payload(order))
        status = str(order.get("status") or "").lower()
        if status in {"filled", "rejected", "canceled", "cancelled"}:
            return None
        total_quantity = int(float(order.get("qty") or order.get("quantity") or 0))
        already_filled = int(float(order.get("filled_qty") or order.get("filledQuantity") or 0))
        remaining = max(0, total_quantity - already_filled)
        fill_quantity = min(remaining, int(quantity or remaining))
        if fill_quantity <= 0:
            return None
        price = max(0.01, float(fill_price or order.get("limit_price") or order.get("filled_avg_price") or 0.01))
        evaluated_at = _parse_dt(filled_at) or _utc_now()
        broker_id = str(order.get("id") or order.get("broker_order_id") or "")
        client_id = str(order.get("client_order_id") or client_order_id or "")
        is_protective = _is_protective_payload(order)
        new_filled = already_filled + fill_quantity
        new_remaining = max(0, total_quantity - new_filled)
        new_status = WcaOrderStatus.FILLED.value if new_remaining == 0 else WcaOrderStatus.PARTIALLY_FILLED.value
        fill_id = f"wca-local-fill-{_digest(f'{broker_id}:{client_id}:{new_filled}:{price}:{evaluated_at.isoformat()}')}"
        costs = _costs_for_fill(quantity=fill_quantity, fill_price=price, model=self.fill_model)
        response_payload = _simulated_response_payload(
            order,
            broker_order_id=broker_id,
            fill_id=fill_id,
            filled_quantity=new_filled,
            remaining_quantity=new_remaining,
            fill_price=price,
            filled_at=evaluated_at,
            status=new_status,
            costs=costs,
        )
        if is_protective:
            if not self._protective_order_matches_local_position(order, selected_symbol):
                self._record_protective_ownership_failure(order, symbol=selected_symbol, evaluated_at=evaluated_at, fill_id=fill_id)
                return None
            closed = self.repository.close_wca_attributed_position_quantity(
                account_id=self.account_id,
                symbol=selected_symbol,
                quantity=fill_quantity,
                exit_price=price,
                exit_reason="local_paper_protective_order",
                evaluated_at=evaluated_at,
                client_order_id=client_id,
                broker_order_id=broker_id,
                fill_id=fill_id,
                payload={
                    "position_effect": "exit",
                    "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
                    "order": redact_secret_payload(dict(order)),
                    "commissions": costs["commissions"],
                    "fees": costs["fees"],
                    "slippage": costs["slippage"],
                    "regulatory_fees": costs["regulatory_fees"],
                    "spread_cost": costs["spread_cost"],
                },
            )
            if not closed:
                return None
        else:
            record = self._outbox_record_for_order(order)
            if record is None:
                return None
            applied = self.repository.apply_fill_and_update_position(
                record.decision.model_copy(update={"proposed_order": record.proposed_order}),
                fill_id=fill_id,
                account_id=self.account_id,
                quantity=fill_quantity,
                broker_order_id=broker_id,
                payload={
                    "fill": response_payload["fill"],
                    "client_order_id": client_id,
                    "broker_order_id": broker_id,
                    "entry_price": price,
                    "opened_at": evaluated_at.isoformat(),
                    "remaining_quantity": new_remaining,
                    "position_effect": "entry",
                    "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
                    "commissions": costs["commissions"],
                    "fees": costs["fees"],
                    "slippage": costs["slippage"],
                    "regulatory_fees": costs["regulatory_fees"],
                    "spread_cost": costs["spread_cost"],
                },
            )
            if not applied:
                return None
        with self.repository.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE wca_broker_orders
                SET status = ?, response_payload_json = ?
                WHERE algorithm_id = ? AND account_id = ? AND broker_order_id = ?
                """,
                (new_status, json.dumps(response_payload, sort_keys=True), WCA_ALGORITHM_ID, self.account_id, broker_id),
            )
            if cursor.rowcount != 1:
                return None
            conn.execute(
                """
                UPDATE wca_local_orders
                SET remaining_quantity = ?, status = ?, updated_at = ?
                WHERE algorithm_id = ? AND local_account_id = ? AND local_order_id = ?
                """,
                (new_remaining, new_status, evaluated_at.isoformat(), WCA_ALGORITHM_ID, self.account_id, broker_id),
            )
            conn.execute(
                """
                UPDATE wca_execution_outbox
                SET status = ?, updated_at = ?
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                  AND (client_order_id = ? OR order_intent_id = ?)
                """,
                (new_status, evaluated_at.isoformat(), WCA_ALGORITHM_ID, self.account_id, selected_symbol, client_id, str(order.get("order_intent_id") or "")),
            )
        fill = WcaPaperBrokerFill(
            fill_id=fill_id,
            client_order_id=client_id,
            broker_order_id=broker_id,
            filled_quantity=fill_quantity,
            remaining_quantity=new_remaining,
            average_fill_price=price,
            filled_at=evaluated_at,
            response_payload=response_payload,
        )
        if is_protective:
            self._cancel_protective_siblings(symbol=selected_symbol, except_broker_order_id=broker_id, evaluated_at=evaluated_at)
        else:
            record = self._outbox_record_for_order(order)
            if record is not None:
                protection = place_or_replace_wca_protective_orders(self.repository, broker=self, record=record, fill=fill)
                if protection.get("status") == "failed":
                    self._handle_local_protection_failure(record=record, fill=fill, protection=protection)
        return fill

    def cancel_order(self, broker_order_id: str) -> dict[str, Any]:
        order = self._lookup_order_any_scope(broker_order_id)
        if order is None:
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.order_not_found")
        self._assert_order_mutation_scope(order, operation="cancel", symbol=str(order.get("symbol") or ""), require_position=_is_protective_payload(order))
        with self.repository.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE wca_broker_orders
                SET status = ?
                WHERE algorithm_id = ? AND account_id = ? AND broker_order_id = ?
                """,
                (WcaOrderStatus.CANCELLED.value, WCA_ALGORITHM_ID, self.account_id, broker_order_id),
            )
            if cursor.rowcount != 1:
                raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.order_cancel_conflict")
            conn.execute(
                """
                UPDATE wca_local_orders
                SET status = ?, updated_at = ?
                WHERE algorithm_id = ? AND local_account_id = ? AND local_order_id = ?
                """,
                (WcaOrderStatus.CANCELLED.value, _utc_now().isoformat(), WCA_ALGORITHM_ID, self.account_id, broker_order_id),
            )
        return {"id": broker_order_id, "status": "canceled", "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY}

    def cancel_all_wca_entry_orders(self) -> tuple[dict[str, Any], ...]:
        cancelled: list[dict[str, Any]] = []
        now = _utc_now().isoformat()
        with self.repository.connect() as conn:
            rows = conn.execute(
                """
                SELECT broker_order_id, client_order_id, symbol, payload_json
                FROM wca_broker_orders
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                  AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
                  AND client_order_id NOT LIKE 'wca-protection-%'
                ORDER BY created_at, broker_order_id
                """,
                (WCA_ALGORITHM_ID, self.account_id, self.symbol),
            ).fetchall()
            for row in rows:
                payload = _order_payload_from_broker_row(row)
                if payload is not None:
                    self._assert_order_mutation_scope(payload, operation="cancel_entry", symbol=self.symbol, require_position=False)
                cancelled.append({"id": row["broker_order_id"], "client_order_id": row["client_order_id"], "status": "canceled"})
            conn.execute(
                """
                UPDATE wca_broker_orders
                SET status = ?
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                  AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
                  AND client_order_id NOT LIKE 'wca-protection-%'
                """,
                (WcaOrderStatus.CANCELLED.value, WCA_ALGORITHM_ID, self.account_id, self.symbol),
            )
            conn.execute(
                """
                UPDATE wca_local_orders
                SET status = ?, updated_at = ?
                WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ?
                  AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
                  AND client_order_id NOT LIKE 'wca-protection-%'
                """,
                (WcaOrderStatus.CANCELLED.value, now, WCA_ALGORITHM_ID, self.account_id, self.symbol),
            )
        for record in self.repository.list_execution_outbox_records(account_id=self.account_id):
            if record.symbol.upper() != self.symbol or _order_status_value(record.status) in _TERMINAL_ORDER_STATUSES:
                continue
            if _record_is_protective(record):
                continue
            cancelled.append({"client_order_id": record.client_order_id, "status": "canceled"})
        return tuple(cancelled)

    def cancel_all_wca_protective_orders(self, *, symbol: str | None = None) -> tuple[dict[str, Any], ...]:
        selected_symbol = (symbol or self.symbol).upper()
        if selected_symbol != self.symbol:
            self._reject_cross_algorithm_mutation(operation="cancel_protective", symbol=selected_symbol, identifier=None)
        orders = tuple(self._protective_orders(symbol=selected_symbol))
        with self.repository.connect() as conn:
            conn.execute(
                """
                UPDATE wca_broker_orders
                SET status = ?
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                  AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
                  AND client_order_id LIKE 'wca-protection-%'
                """,
                (WcaOrderStatus.CANCELLED.value, WCA_ALGORITHM_ID, self.account_id, selected_symbol),
            )
            conn.execute(
                """
                UPDATE wca_local_orders
                SET status = ?, updated_at = ?
                WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ?
                  AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
                  AND client_order_id LIKE 'wca-protection-%'
                """,
                (WcaOrderStatus.CANCELLED.value, _utc_now().isoformat(), WCA_ALGORITHM_ID, self.account_id, selected_symbol),
            )
        return tuple({"client_order_id": order.clientOrderId, "status": "canceled"} for order in orders)

    def close_or_reduce_wca_position(self, *, symbol: str, quantity: int, side: WcaSide | str, client_order_id: str, price: float | None = None, evaluated_at: datetime | str | None = None) -> WcaPaperBrokerAck:
        selected_symbol = str(symbol or "").upper()
        if selected_symbol != self.symbol:
            self._reject_cross_algorithm_mutation(operation="reduce", symbol=selected_symbol, identifier=client_order_id)
        lots = self.repository.list_open_wca_lots(account_id=self.account_id, symbol=selected_symbol)
        self._assert_lot_mutation_scope(lots, operation="reduce", symbol=selected_symbol, identifier=client_order_id)
        owned_quantity = sum(int(lot.get("quantity") or 0) for lot in lots)
        if quantity <= 0 or quantity > owned_quantity:
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.wca_owned_quantity_required")
        if any(_side_value(lot.get("side", side)) != _side_value(side) for lot in lots):
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.position_side_mismatch")
        exit_side = WcaSide.SELL if _side_value(side) == WcaSide.BUY.value else WcaSide.BUY
        limit_price = max(0.01, float(price or _average_mark_from_lots(lots)))
        request = WcaPaperBrokerOrderRequest(
            account_id=self.account_id,
            symbol=selected_symbol,
            side=exit_side,
            quantity=quantity,
            order_type="LIMIT",
            limit_price=limit_price,
            client_order_id=client_order_id,
            idempotency_key=client_order_id,
            decision_id=client_order_id,
            order_intent_id=client_order_id,
            configuration_version="wca_local_paper_reduce_position",
        )
        broker_order_id = f"wca-local-{_digest(request.client_order_id)}"
        fill = WcaPaperBrokerFill(
            fill_id=f"wca-local-fill-{_digest(f'{request.client_order_id}:{quantity}:{limit_price}')}",
            client_order_id=request.client_order_id,
            broker_order_id=broker_order_id,
            filled_quantity=quantity,
            remaining_quantity=0,
            average_fill_price=limit_price,
            filled_at=_parse_dt(evaluated_at) or _utc_now(),
            response_payload=_response_payload(request, broker_order_id, status="filled"),
        )
        return WcaPaperBrokerAck(
            status="ACKNOWLEDGED",
            client_order_id=request.client_order_id,
            broker_order_id=broker_order_id,
            accepted_quantity=quantity,
            message="filled_by_wca_local_risk_reduction",
            response_payload=_response_payload(request, broker_order_id, status="filled"),
            fill=fill,
        )

    def flatten_wca_positions(
        self,
        *,
        symbol: str | None = None,
        quantity: int | None = None,
        client_order_id: str | None = None,
        price: float | None = None,
        evaluated_at: datetime | str | None = None,
    ) -> WcaPaperBrokerAck:
        selected_symbol = (symbol or self.symbol).upper()
        if selected_symbol != self.symbol:
            self._reject_cross_algorithm_mutation(operation="flatten", symbol=selected_symbol, identifier=client_order_id)
        lots = self.repository.list_open_wca_lots(account_id=self.account_id, symbol=selected_symbol)
        self._assert_lot_mutation_scope(lots, operation="flatten", symbol=selected_symbol, identifier=client_order_id)
        owned_quantity = sum(int(lot.get("quantity") or 0) for lot in lots)
        close_quantity = owned_quantity if quantity is None else int(quantity)
        if close_quantity <= 0 or close_quantity > owned_quantity:
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.wca_owned_quantity_required")
        side = _side_value(lots[0].get("side", WcaSide.BUY))
        exit_side = WcaSide.SELL if side == WcaSide.BUY.value else WcaSide.BUY
        fill_price = max(0.01, float(price or _average_mark_from_lots(lots)))
        evaluated = _parse_dt(evaluated_at) or _utc_now()
        client_id = (client_order_id or f"wca-flatten-{self.account_id}-{selected_symbol}-{_digest(evaluated.isoformat())}")[:48]
        broker_id = f"wca-local-{_digest(client_id)}"
        fill_id = f"wca-local-fill-{_digest(f'{broker_id}:{close_quantity}:{fill_price}:{evaluated.isoformat()}')}"
        response_payload = {
            "id": broker_id,
            "client_order_id": client_id,
            "symbol": selected_symbol,
            "side": _side_value(exit_side).lower(),
            "qty": str(close_quantity),
            "filled_qty": str(close_quantity),
            "filled_avg_price": str(fill_price),
            "limit_price": str(fill_price),
            "type": "limit",
            "time_in_force": "day",
            "status": "filled",
            "filled_at": evaluated.isoformat(),
            "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
            "local_inventory_already_closed": True,
        }
        closed = self.repository.close_wca_attributed_position_quantity(
            account_id=self.account_id,
            symbol=selected_symbol,
            quantity=close_quantity,
            exit_price=fill_price,
            exit_reason="local_paper_flatten",
            evaluated_at=evaluated,
            client_order_id=client_id,
            broker_order_id=broker_id,
            fill_id=fill_id,
            payload={"position_effect": "exit", "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY},
        )
        if not closed:
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.flatten_failed")
        self.cancel_all_wca_protective_orders(symbol=selected_symbol)
        fill = WcaPaperBrokerFill(
            fill_id=fill_id,
            client_order_id=client_id,
            broker_order_id=broker_id,
            filled_quantity=close_quantity,
            remaining_quantity=0,
            average_fill_price=fill_price,
            filled_at=evaluated,
            response_payload=response_payload,
        )
        return WcaPaperBrokerAck(
            status="ACKNOWLEDGED",
            client_order_id=client_id,
            broker_order_id=broker_id,
            accepted_quantity=close_quantity,
            message="flattened_wca_local_position",
            response_payload=response_payload,
            fill=fill,
        )

    def _short_entry_rejection_reasons(self, request: WcaPaperBrokerOrderRequest) -> tuple[str, ...]:
        if self.allow_short or _side_value(request.side) != WcaSide.SELL.value:
            return ()
        position = self._account().get_position(request.symbol)
        if position is not None and position.quantity > 0:
            return ()
        return ("wca.local_paper.short_entries_disabled",)


    def _account(self, *, session_date: Any | None = None) -> WcaLocalPaperAccount:
        self._ensure_bootstrap_state()
        return WcaLocalPaperAccount.restore(
            self.repository,
            account_id=self.account_id,
            symbol=self.symbol,
            starting_balance=self.starting_balance,
            session_date=session_date or _utc_now().date(),
        )

    def _risk_account_snapshot(self):
        snapshot = self._account().get_account_snapshot()
        buying_power = max(0.0, snapshot.buying_power * self.buying_power_multiplier)
        return replace(snapshot, buying_power=buying_power)

    def _ensure_bootstrap_state(self) -> None:
        with self.repository.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM wca_inventory_ledger
                WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?
                  AND event_type = 'DAILY_STATE_RESET'
                LIMIT 1
                """,
                (WCA_ALGORITHM_ID, self.account_id, self.symbol),
            ).fetchone()
        if row is not None:
            return
        now = _utc_now()
        session_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
        event_id = f"wca-local-paper-bootstrap-{self.account_id}-{self.symbol}-{now.date().isoformat()}"
        self.repository.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id=event_id,
                event_type="DAILY_STATE_RESET",
                broker_account_id=self.account_id,
                symbol=self.symbol,
                event_timestamp=session_start.isoformat(),
                trade_date=now.date().isoformat(),
                source_authority=WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
                configuration_version=WCA_LOCAL_PAPER_BROKER_VERSION,
                decision_id=event_id,
                run_id=event_id,
                payload={
                    "starting_balance": self.starting_balance,
                    "cash": self.starting_balance,
                    "equity": self.starting_balance,
                    "buying_power": self.starting_balance,
                    "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
                },
            )
        )

    def _positions(self, projection: Any) -> list[BrokerPositionState]:
        quantity = int(projection.open_quantity or 0)
        if quantity <= 0:
            return []
        lots = self.repository.list_open_wca_lots(account_id=self.account_id, symbol=self.symbol)
        first_lot = lots[0] if lots else {}
        first_payload = first_lot.get("payload") if isinstance(first_lot.get("payload"), dict) else {}
        side = _side_value(first_lot.get("side", WcaSide.BUY))
        mark_price = max(0.01, float(projection.average_entry_price or _average_mark_from_lots(lots) or 0.01))
        return [
            BrokerPositionState(
                algorithmId=WCA_ALGORITHM_ID,
                capitalPartitionId=_LOCAL_PARTITION_ID,
                decisionId=str(first_lot.get("decision_id") or getattr(projection, "decision_id", "") or "") or None,
                orderIntentId=str(first_payload.get("order_intent_id") or first_lot.get("order_intent_id") or "") or None,
                positionOwner=WCA_ALGORITHM_ID,
                symbol=self.symbol,
                side=Signal.BUY if side == WcaSide.BUY.value else Signal.SELL,
                quantity=quantity,
                averageEntryPrice=mark_price,
                markPrice=mark_price,
                stopPrice=_optional_float(first_lot.get("stop_price")),
                realizedPnlToday=0.0,
                openedAt=_parse_dt(first_lot.get("opened_at")),
            )
        ]

    def _protective_orders(self, *, symbol: str | None = None) -> list[BrokerOrderState]:
        selected_symbol = (symbol or self.symbol).upper()
        with self.repository.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM wca_broker_orders
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                  AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
                  AND client_order_id LIKE 'wca-protection-%'
                ORDER BY created_at
                """,
                (WCA_ALGORITHM_ID, self.account_id, selected_symbol),
            ).fetchall()
        orders = []
        for row in rows:
            payload = _order_payload_from_broker_row(row)
            if payload is not None:
                orders.append(_order_from_payload(payload))
        return orders

    def _outbox_record_for_order(self, order: Mapping[str, Any]) -> Any | None:
        client_id = str(order.get("client_order_id") or "")
        order_intent_id = str(order.get("order_intent_id") or "")
        for record in self.repository.list_execution_outbox_records(account_id=self.account_id):
            if record.symbol.upper() != self.symbol:
                continue
            if client_id and record.client_order_id == client_id:
                return record
            if order_intent_id and record.order_intent_id == order_intent_id:
                return record
        return None

    def _open_broker_order_payloads(self, *, symbol: str, protective_only: bool = False) -> list[dict[str, Any]]:
        selected_symbol = symbol.upper()
        protection_filter = "AND client_order_id LIKE 'wca-protection-%'" if protective_only else ""
        with self.repository.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM wca_broker_orders
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                  AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
                  {protection_filter}
                ORDER BY created_at
                """,
                (WCA_ALGORITHM_ID, self.account_id, selected_symbol),
            ).fetchall()
        return [payload for row in rows if (payload := _order_payload_from_broker_row(row)) is not None]

    def _mark_stop_triggered(self, broker_order_id: str, triggered_at: datetime) -> None:
        order = self._lookup_order_any_scope(broker_order_id)
        if order is None:
            return
        self._assert_order_mutation_scope(order, operation="stop_trigger", symbol=str(order.get("symbol") or ""), require_position=True)
        response_payload = {
            **order,
            "status": "accepted",
            "stop_triggered": True,
            "stop_triggered_at": triggered_at.isoformat(),
            "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
        }
        with self.repository.connect() as conn:
            conn.execute(
                """
                UPDATE wca_broker_orders
                SET response_payload_json = ?
                WHERE algorithm_id = ? AND account_id = ? AND broker_order_id = ?
                  AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
                """,
                (json.dumps(response_payload, sort_keys=True), WCA_ALGORITHM_ID, self.account_id, broker_order_id),
            )

    def _expire_day_orders(self, market_timestamp: datetime) -> None:
        market_day = market_timestamp.astimezone(timezone.utc).date()
        with self.repository.connect() as conn:
            rows = conn.execute(
                """
                SELECT broker_order_id, timestamp
                FROM wca_broker_orders
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                  AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
                """,
                (WCA_ALGORITHM_ID, self.account_id, self.symbol),
            ).fetchall()
            expired = [
                row["broker_order_id"]
                for row in rows
                if (_parse_dt(row["timestamp"]) or market_timestamp).astimezone(timezone.utc).date() < market_day
            ]
            if not expired:
                return
            now = market_timestamp.isoformat()
            for broker_order_id in expired:
                conn.execute(
                    "UPDATE wca_broker_orders SET status = ? WHERE broker_order_id = ? AND algorithm_id = ? AND account_id = ?",
                    (WcaOrderStatus.CANCELLED.value, broker_order_id, WCA_ALGORITHM_ID, self.account_id),
                )
                conn.execute(
                    "UPDATE wca_local_orders SET status = ?, updated_at = ? WHERE local_order_id = ? AND algorithm_id = ? AND local_account_id = ?",
                    (WcaOrderStatus.CANCELLED.value, now, broker_order_id, WCA_ALGORITHM_ID, self.account_id),
                )

    def _cancel_protective_siblings(self, *, symbol: str, except_broker_order_id: str, evaluated_at: datetime) -> None:
        with self.repository.connect() as conn:
            conn.execute(
                """
                UPDATE wca_broker_orders
                SET status = ?
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                  AND broker_order_id <> ?
                  AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
                  AND client_order_id LIKE 'wca-protection-%'
                """,
                (WcaOrderStatus.CANCELLED.value, WCA_ALGORITHM_ID, self.account_id, symbol, except_broker_order_id),
            )
            conn.execute(
                """
                UPDATE wca_local_orders
                SET status = ?, updated_at = ?
                WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ?
                  AND local_order_id <> ?
                  AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
                  AND client_order_id LIKE 'wca-protection-%'
                """,
                (WcaOrderStatus.CANCELLED.value, evaluated_at.isoformat(), WCA_ALGORITHM_ID, self.account_id, symbol, except_broker_order_id),
            )

    def _lookup_order_any_scope(self, identifier: str) -> dict[str, Any] | None:
        lookup = str(identifier or "").strip()
        if not lookup:
            return None
        with self.repository.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wca_broker_orders
                WHERE broker_order_id = ? OR client_order_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (lookup, lookup),
            ).fetchone()
        return _order_payload_from_broker_row(row) if row is not None else None

    def _assert_order_mutation_scope(self, order: Mapping[str, Any], *, operation: str, symbol: str | None = None, require_position: bool = False) -> None:
        selected_symbol = str(symbol or order.get("symbol") or "").upper()
        mismatches: list[str] = []
        if str(order.get("algorithm_id") or "") != WCA_ALGORITHM_ID:
            mismatches.append("order.algorithm_id")
        if str(order.get("account_id") or order.get("local_account_id") or "") != self.account_id:
            mismatches.append("order.account_id")
        order_symbol = str(order.get("symbol") or "").upper()
        if not order_symbol or order_symbol != selected_symbol or order_symbol != self.symbol:
            mismatches.append("order.symbol")
        if require_position and not self._protective_order_matches_local_position(order, selected_symbol):
            mismatches.append("position.ownership")
        if mismatches:
            self._reject_cross_algorithm_mutation(
                operation=operation,
                order=order,
                symbol=selected_symbol or order_symbol or self.symbol,
                identifier=str(order.get("broker_order_id") or order.get("id") or order.get("client_order_id") or ""),
                mismatches=tuple(mismatches),
            )

    def _assert_lot_mutation_scope(self, lots: tuple[dict[str, Any], ...], *, operation: str, symbol: str, identifier: str | None = None) -> None:
        selected_symbol = str(symbol or "").upper()
        mismatches: list[str] = []
        for lot in lots:
            if str(lot.get("account_id") or "") != self.account_id:
                mismatches.append("position.account_id")
            if str(lot.get("symbol") or "").upper() != selected_symbol or selected_symbol != self.symbol:
                mismatches.append("position.symbol")
        if mismatches:
            self._reject_cross_algorithm_mutation(
                operation=operation,
                symbol=selected_symbol or self.symbol,
                identifier=identifier,
                mismatches=tuple(sorted(set(mismatches))),
            )

    def _reject_cross_algorithm_mutation(
        self,
        *,
        operation: str,
        order: Mapping[str, Any] | None = None,
        symbol: str | None = None,
        identifier: str | None = None,
        mismatches: tuple[str, ...] = (),
    ) -> None:
        self._record_cross_algorithm_mutation_blocked(
            operation=operation,
            order=order,
            symbol=symbol,
            identifier=identifier,
            mismatches=mismatches,
        )
        raise WcaLocalPaperBrokerConfigurationError(WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED)

    def _record_cross_algorithm_mutation_blocked(
        self,
        *,
        operation: str,
        order: Mapping[str, Any] | None = None,
        symbol: str | None = None,
        identifier: str | None = None,
        mismatches: tuple[str, ...] = (),
    ) -> None:
        evaluated_at = _utc_now()
        selected_symbol = str(symbol or (order or {}).get("symbol") or self.symbol).upper()
        event_id = f"wca-cross-mutation-blocked-{self.account_id}-{selected_symbol}-{_digest(f'{operation}:{identifier}:{evaluated_at.isoformat()}')}"
        self.repository.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id=event_id,
                event_type="RECONCILIATION_CORRECTION",
                broker_account_id=self.account_id,
                symbol=selected_symbol or self.symbol,
                event_timestamp=evaluated_at,
                trade_date=evaluated_at.date().isoformat(),
                client_order_id=str((order or {}).get("client_order_id") or identifier or ""),
                broker_order_id=str((order or {}).get("broker_order_id") or (order or {}).get("id") or identifier or ""),
                fill_id=None,
                side=str((order or {}).get("side") or ""),
                quantity=0,
                filled_quantity=0,
                source_authority=WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
                configuration_version=WCA_LOCAL_PAPER_BROKER_VERSION,
                decision_id=str((order or {}).get("decision_id") or "wca-cross-algorithm-mutation-blocked"),
                run_id="wca-local-paper-ownership-guard",
                payload={
                    "critical": True,
                    "operation": operation,
                    "reason_codes": (WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED,),
                    "mismatches": mismatches,
                    "expected": {"algorithm_id": WCA_ALGORITHM_ID, "account_id": self.account_id, "symbol": self.symbol},
                    "order": redact_secret_payload(dict(order or {})),
                },
            )
        )

    def _protective_order_matches_local_position(self, order: Mapping[str, Any], symbol: str) -> bool:
        ownership = _ownership_payload(order)
        if str(order.get("algorithm_id") or "") != WCA_ALGORITHM_ID:
            return False
        if str(order.get("account_id") or order.get("local_account_id") or "") != self.account_id:
            return False
        if str(order.get("symbol") or "").upper() != symbol.upper():
            return False
        if not ownership:
            return False
        if str(ownership.get("protected_algorithm_id") or ownership.get("algorithm_id") or "") != WCA_ALGORITHM_ID:
            return False
        if str(ownership.get("position_owner") or "") != WCA_ALGORITHM_ID or str(ownership.get("exit_owner") or "") != WCA_ALGORITHM_ID:
            return False
        if str(ownership.get("local_account_id") or ownership.get("account_id") or "") != self.account_id:
            return False
        if str(ownership.get("symbol") or "").upper() != symbol.upper():
            return False
        position_id = str(ownership.get("position_id") or "")
        if not position_id:
            return False
        with self.repository.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM wca_owned_lots
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
                  AND position_id = ? AND status = 'open' AND quantity > 0
                LIMIT 1
                """,
                (WCA_ALGORITHM_ID, self.account_id, symbol.upper(), position_id),
            ).fetchone()
        return row is not None

    def _record_protective_ownership_failure(self, order: Mapping[str, Any], *, symbol: str, evaluated_at: datetime, fill_id: str) -> None:
        ownership = _ownership_payload(order)
        event_id = f"wca-protection-owner-reject-{self.account_id}-{symbol}-{_digest(fill_id)}"
        self.repository.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id=event_id,
                event_type="RECONCILIATION_CORRECTION",
                broker_account_id=self.account_id,
                symbol=symbol,
                event_timestamp=evaluated_at,
                trade_date=evaluated_at.date().isoformat(),
                client_order_id=str(order.get("client_order_id") or ""),
                broker_order_id=str(order.get("broker_order_id") or order.get("id") or ""),
                fill_id=None,
                side=str(order.get("side") or ""),
                quantity=0,
                filled_quantity=0,
                source_authority=WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
                configuration_version=WCA_LOCAL_PAPER_BROKER_VERSION,
                decision_id=str(order.get("decision_id") or "wca-protection-ownership-reject"),
                run_id="wca-local-paper-protection-ownership",
                payload={
                    "critical": True,
                    "protection_status": "REJECTED_OWNERSHIP_MISMATCH",
                    "reason_codes": (WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED, "wca.protection.ownership_mismatch"),
                    "ownership": ownership,
                    "order": redact_secret_payload(dict(order)),
                },
            )
        )

    def _handle_local_protection_failure(self, *, record: Any, fill: WcaPaperBrokerFill, protection: Mapping[str, Any]) -> None:
        evaluated_at = fill.filled_at.astimezone(timezone.utc) if fill.filled_at is not None else _utc_now()
        self._persist_unprotected_state(record=record, fill=fill, protection=protection, evaluated_at=evaluated_at, stage="detected")
        flatten_error = None
        flattened = False
        flatten_at = evaluated_at + timedelta(microseconds=1)
        try:
            open_quantity = abs(int(self.repository.open_wca_position_quantity(account_id=self.account_id, symbol=self.symbol)))
            if open_quantity > 0:
                self.flatten_wca_positions(
                    symbol=self.symbol,
                    client_order_id=f"wca-flatten-unprotected-{_digest(fill.fill_id)}",
                    price=max(0.01, float(fill.average_fill_price or getattr(record.proposed_order, "limit_price", None) or getattr(record.proposed_order, "trigger_price", None) or 0.01)),
                    evaluated_at=flatten_at,
                )
                flattened = True
        except Exception as exc:
            flatten_error = str(exc)
        self._persist_unprotected_state(
            record=record,
            fill=fill,
            protection={**dict(protection), "flattened": flattened, "flatten_error": flatten_error},
            evaluated_at=flatten_at,
            stage="flatten_attempted",
        )

    def _persist_unprotected_state(self, *, record: Any, fill: WcaPaperBrokerFill, protection: Mapping[str, Any], evaluated_at: datetime, stage: str) -> None:
        reason_codes = tuple(str(code) for code in protection.get("reason_codes", ("wca.protection.failed",)))
        cooldown_until = None
        with self.repository.connect() as conn:
            position_rows = conn.execute(
                """
                SELECT position_id, payload_json
                FROM wca_local_positions
                WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ?
                """,
                (WCA_ALGORITHM_ID, self.account_id, self.symbol),
            ).fetchall()
            for row in position_rows:
                payload = _json_payload(row["payload_json"])
                snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
                snapshot.update({"protection_status": "UNPROTECTED", "protection_failure_stage": stage, "reason_codes": reason_codes})
                payload["snapshot"] = snapshot
                conn.execute(
                    """
                    UPDATE wca_local_positions
                    SET payload_json = ?, updated_at = ?
                    WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ? AND position_id = ?
                    """,
                    (json.dumps(payload, sort_keys=True), evaluated_at.isoformat(), WCA_ALGORITHM_ID, self.account_id, self.symbol, row["position_id"]),
                )
            lot_rows = conn.execute(
                """
                SELECT lot_id, payload_json
                FROM wca_owned_lots
                WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND status = 'open'
                """,
                (WCA_ALGORITHM_ID, self.account_id, self.symbol),
            ).fetchall()
            for row in lot_rows:
                payload = _json_payload(row["payload_json"])
                payload.update({"protection_status": "UNPROTECTED", "protection_failure_stage": stage, "reason_codes": reason_codes})
                conn.execute(
                    """
                    UPDATE wca_owned_lots
                    SET payload_json = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND lot_id = ?
                    """,
                    (json.dumps(payload, sort_keys=True), WCA_ALGORITHM_ID, self.account_id, self.symbol, row["lot_id"]),
                )
            account_row = conn.execute(
                """
                SELECT payload_json
                FROM wca_local_paper_account
                WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ?
                """,
                (WCA_ALGORITHM_ID, self.account_id, self.symbol),
            ).fetchone()
            account_payload = _json_payload(account_row["payload_json"] if account_row is not None else "{}")
            snapshot = account_payload.get("account_snapshot") if isinstance(account_payload.get("account_snapshot"), dict) else {}
            snapshot.update({
                "circuit_breaker_state": "unprotected_position",
                "protection_status": "UNPROTECTED",
                "protection_failure_stage": stage,
                "new_entries_blocked": True,
                "cooldown_until": cooldown_until,
                "reason_codes": reason_codes,
            })
            account_payload["account_snapshot"] = snapshot
            conn.execute(
                """
                UPDATE wca_local_paper_account
                SET circuit_breaker_state = ?, cooldown_until = ?, payload_json = ?, updated_at = ?
                WHERE algorithm_id = ? AND local_account_id = ? AND symbol = ?
                """,
                ("unprotected_position", cooldown_until, json.dumps(account_payload, sort_keys=True), evaluated_at.isoformat(), WCA_ALGORITHM_ID, self.account_id, self.symbol),
            )
        self.repository.record_inventory_event(
            WcaInventoryLedgerEvent(
                inventory_event_id=f"wca-protection-failure-{stage}-{self.account_id}-{self.symbol}-{_digest(fill.fill_id + stage)}",
                event_type="RECONCILIATION_CORRECTION",
                broker_account_id=self.account_id,
                symbol=self.symbol,
                event_timestamp=evaluated_at,
                trade_date=evaluated_at.date().isoformat(),
                order_intent_id=getattr(record, "order_intent_id", None),
                client_order_id=getattr(record, "client_order_id", None),
                broker_order_id=fill.broker_order_id,
                fill_id=None,
                side=_side_value(getattr(record.proposed_order, "side", WcaSide.BUY)),
                quantity=0,
                filled_quantity=0,
                remaining_quantity=0,
                fill_price=float(fill.average_fill_price or 0.0),
                source_authority=WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
                configuration_version=getattr(record.proposed_order, "configuration_version", WCA_LOCAL_PAPER_BROKER_VERSION) or WCA_LOCAL_PAPER_BROKER_VERSION,
                decision_id=getattr(record, "decision_id", "wca-protection-failure"),
                run_id=getattr(record, "run_id", "wca-local-paper-protection-failure"),
                payload={
                    "critical": True,
                    "protection_status": "UNPROTECTED",
                    "circuit_breaker_state": "unprotected_position",
                    "new_entries_blocked": True,
                    "risk_reduction_attempted": True,
                    "stage": stage,
                    "source_fill_id": fill.fill_id,
                    "protection": dict(protection),
                    "reason_codes": reason_codes,
                },
            )
        )

    def _validate_request_identity(self, request: WcaPaperBrokerOrderRequest, *, operation: str = "submit") -> None:
        if request.algorithm_id != WCA_ALGORITHM_ID or request.account_id != self.account_id:
            self._reject_cross_algorithm_mutation(
                operation=operation,
                order=request.model_dump(mode="json"),
                symbol=request.symbol,
                identifier=request.client_order_id,
                mismatches=("request.algorithm_id", "request.account_id"),
            )
        if not request.client_order_id.startswith("wca-"):
            raise WcaLocalPaperBrokerConfigurationError("wca.local_paper.client_order_id_prefix_required")


def _order_from_outbox(record: Any) -> BrokerOrderState:
    return _order_from_payload(_order_payload_from_outbox(record))


def _order_from_payload(payload: Mapping[str, Any]) -> BrokerOrderState:
    submitted = _parse_dt(payload.get("submitted_at") or payload.get("created_at")) or _utc_now()
    return BrokerOrderState(
        algorithmId=WCA_ALGORITHM_ID,
        capitalPartitionId=_LOCAL_PARTITION_ID,
        decisionId=str(payload.get("decision_id") or "") or None,
        orderIntentId=str(payload.get("order_intent_id") or "") or None,
        positionOwner=WCA_ALGORITHM_ID,
        exitOwner=WCA_ALGORITHM_ID if _is_protective_payload(payload) else None,
        symbol=str(payload.get("symbol") or "SPY").upper(),
        side=Signal.BUY if _side_value(payload.get("side") or WcaSide.BUY) == WcaSide.BUY.value else Signal.SELL,
        clientOrderId=str(payload.get("client_order_id") or ""),
        orderType=str(payload.get("type") or payload.get("order_type") or "LIMIT").upper(),
        status=_status(payload),
        quantity=int(float(payload.get("qty") or payload.get("quantity") or 0)),
        filledQuantity=int(float(payload.get("filled_qty") or payload.get("filledQuantity") or 0)),
        entryPrice=max(0.01, float(payload.get("limit_price") or payload.get("entry_price") or 0.01)),
        stopPrice=_optional_float(payload.get("stop_price")),
        submittedAt=submitted,
    )


def _order_payload_from_outbox(record: Any) -> dict[str, Any]:
    proposed = getattr(record, "proposed_order", None)
    request_payload = dict(getattr(record, "request_payload", {}) or {})
    response_payload = dict(getattr(record, "response_payload", {}) or {})
    fill_payload = ((response_payload.get("response") or {}).get("fill") if isinstance(response_payload.get("response"), dict) else None) or {}
    status = _order_status_value(getattr(record, "status", "") or "")
    return {
        "id": f"wca-local-{_digest(str(getattr(record, 'client_order_id', '') or ''))}",
        "algorithm_id": WCA_ALGORITHM_ID,
        "account_id": str(getattr(record, "account_id", "") or ""),
        "client_order_id": str(getattr(record, "client_order_id", "") or ""),
        "order_intent_id": str(getattr(record, "order_intent_id", "") or getattr(proposed, "order_intent_id", "") or ""),
        "decision_id": str(getattr(record, "decision_id", "") or getattr(proposed, "decision_id", "") or ""),
        "symbol": str(getattr(record, "symbol", "") or getattr(proposed, "symbol", "SPY") or "SPY"),
        "side": _side_value(getattr(proposed, "side", request_payload.get("side", WcaSide.BUY))),
        "qty": int(getattr(proposed, "quantity", request_payload.get("quantity", 0)) or 0),
        "filled_qty": int((fill_payload or {}).get("filled_quantity") or (fill_payload or {}).get("filledQuantity") or (int(getattr(proposed, "quantity", 0) or 0) if status == WcaOrderStatus.FILLED.value else 0)),
        "limit_price": float(getattr(proposed, "limit_price", None) or request_payload.get("limit_price") or request_payload.get("limitPrice") or 0.01),
        "stop_price": getattr(proposed, "stop_price", None) or request_payload.get("stop_price"),
        "type": request_payload.get("order_type") or request_payload.get("type") or "LIMIT",
        "status": _payload_status(status),
        "submitted_at": getattr(record, "created_at", None) or _utc_now().isoformat(),
        "filled_at": (fill_payload or {}).get("filled_at") or (fill_payload or {}).get("filledAt"),
        "filled_avg_price": (fill_payload or {}).get("average_fill_price") or (fill_payload or {}).get("averageFillPrice"),
        "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
    }


def _order_payload_from_broker_row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    request = _json_payload(row["request_payload_json"] or "{}")
    response = _json_payload(row["response_payload_json"] or "{}")
    record_payload = _json_payload(row["payload_json"] or "{}")
    ownership = _ownership_payload(record_payload) or _ownership_payload(response) or _ownership_payload(request)
    fill = _fill_payload_from_response(response)
    return {
        "id": row["broker_order_id"],
        "broker_order_id": row["broker_order_id"],
        "algorithm_id": row["algorithm_id"],
        "account_id": row["account_id"],
        "client_order_id": row["client_order_id"],
        "order_intent_id": row["order_intent_id"],
        "decision_id": row["decision_id"],
        "symbol": row["symbol"],
        "side": row["side"],
        "qty": row["quantity"],
        "quantity": row["quantity"],
        "filled_qty": fill.get("filled_quantity") or fill.get("filledQuantity") or response.get("filled_qty") or response.get("filledQuantity") or 0,
        "limit_price": request.get("limit_price") or request.get("limitPrice") or response.get("limit_price") or 0.01,
        "stop_price": request.get("stop_price") or request.get("stopPrice") or response.get("stop_price") or response.get("stopPrice"),
        "target_price": request.get("target_price") or request.get("targetPrice") or response.get("target_price") or response.get("targetPrice"),
        "type": request.get("order_type") or request.get("type") or response.get("type") or "LIMIT",
        "time_in_force": request.get("time_in_force") or request.get("timeInForce") or response.get("time_in_force") or "DAY",
        "status": _payload_status(row["status"]),
        "submitted_at": row["timestamp"],
        "filled_at": fill.get("filled_at") or fill.get("filledAt") or response.get("filled_at"),
        "filled_avg_price": fill.get("average_fill_price") or fill.get("averageFillPrice") or response.get("filled_avg_price"),
        "stop_triggered": response.get("stop_triggered") or response.get("stopTriggered") or False,
        "stop_triggered_at": response.get("stop_triggered_at") or response.get("stopTriggeredAt"),
        "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
        "ownership": ownership,
        "position_id": ownership.get("position_id"),
        "local_position_id": ownership.get("local_position_id"),
        "position_owner": ownership.get("position_owner"),
        "exit_owner": ownership.get("exit_owner"),
        "protected_algorithm_id": ownership.get("protected_algorithm_id") or ownership.get("algorithm_id"),
        "local_account_id": ownership.get("local_account_id") or ownership.get("account_id"),
    }


def _order_status_value(status: Any) -> str:
    return status.value if hasattr(status, "value") else str(status or "")


def _payload_status(status: str) -> str:
    normalized = str(status or "").upper()
    if normalized in {WcaOrderStatus.FILLED.value, "FILLED"}:
        return "filled"
    if normalized in {WcaOrderStatus.REJECTED.value, "REJECTED"}:
        return "rejected"
    if normalized in {WcaOrderStatus.CANCELLED.value, "CANCELLED", "CANCELED"}:
        return "canceled"
    return "accepted"


def _status(payload: Mapping[str, Any]) -> str:
    filled = int(float(payload.get("filled_qty") or payload.get("filledQuantity") or 0))
    if filled > 0:
        qty = int(float(payload.get("qty") or payload.get("quantity") or 0))
        return "PARTIALLY_FILLED" if filled < qty else "FILLED"
    return "ACCEPTED"


def _response_payload(request: WcaPaperBrokerOrderRequest, broker_order_id: str, *, status: str) -> dict[str, Any]:
    return {
        "id": broker_order_id,
        "client_order_id": request.client_order_id,
        "symbol": request.symbol,
        "side": _side_value(request.side).lower(),
        "qty": str(request.quantity),
        "filled_qty": str(request.quantity if status == "filled" else 0),
        "filled_avg_price": str(request.limit_price) if status == "filled" else None,
        "limit_price": str(request.limit_price),
        "stop_price": str(request.stop_price) if request.stop_price is not None else None,
        "target_price": str(request.target_price) if request.target_price is not None else None,
        "type": request.order_type.lower(),
        "time_in_force": request.time_in_force.lower(),
        "status": status,
        "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
    }


def _simulated_response_payload(
    order: Mapping[str, Any],
    *,
    broker_order_id: str,
    fill_id: str,
    filled_quantity: int,
    remaining_quantity: int,
    fill_price: float,
    filled_at: datetime,
    status: str,
    costs: Mapping[str, float],
) -> dict[str, Any]:
    fill = {
        "fill_id": fill_id,
        "client_order_id": order.get("client_order_id"),
        "broker_order_id": broker_order_id,
        "filled_quantity": filled_quantity,
        "remaining_quantity": remaining_quantity,
        "average_fill_price": fill_price,
        "filled_at": filled_at.isoformat(),
        "commissions": float(costs.get("commissions") or 0.0),
        "fees": float(costs.get("fees") or 0.0),
        "slippage": float(costs.get("slippage") or 0.0),
        "spread_cost": float(costs.get("spread_cost") or 0.0),
        "regulatory_fees": float(costs.get("regulatory_fees") or 0.0),
    }
    return {
        "id": broker_order_id,
        "client_order_id": order.get("client_order_id"),
        "symbol": order.get("symbol"),
        "side": str(order.get("side") or "").lower(),
        "qty": str(order.get("qty") or order.get("quantity") or 0),
        "filled_qty": str(filled_quantity),
        "filled_avg_price": str(fill_price),
        "limit_price": str(order.get("limit_price") or fill_price),
        "stop_price": str(order.get("stop_price")) if order.get("stop_price") is not None else None,
        "target_price": str(order.get("target_price")) if order.get("target_price") is not None else None,
        "type": str(order.get("type") or "LIMIT").lower(),
        "time_in_force": str(order.get("time_in_force") or "DAY").lower(),
        "status": status.lower(),
        "filled_at": filled_at.isoformat(),
        "fill": fill,
        "commissions": fill["commissions"],
        "fees": fill["fees"],
        "slippage": fill["slippage"],
        "spread_cost": fill["spread_cost"],
        "regulatory_fees": fill["regulatory_fees"],
        "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
        "ownership": _ownership_payload(order),
    }


def _json_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _ownership_payload(payload: Mapping[str, Any] | Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    nested = payload.get("ownership")
    if isinstance(nested, Mapping):
        return dict(nested)
    keys = {
        "algorithm_id",
        "protected_algorithm_id",
        "position_owner",
        "exit_owner",
        "account_id",
        "local_account_id",
        "symbol",
        "position_id",
        "local_position_id",
        "entry_order_intent_id",
        "entry_decision_id",
    }
    ownership = {key: payload.get(key) for key in keys if payload.get(key) is not None}
    return ownership

def _fill_payload_from_response(response: Mapping[str, Any]) -> dict[str, Any]:
    direct = response.get("fill")
    if isinstance(direct, dict):
        return direct
    nested = response.get("response")
    if isinstance(nested, dict) and isinstance(nested.get("fill"), dict):
        return nested["fill"]
    return {}


def _market_context(payload: Mapping[str, Any], *, fill_model: WcaLocalPaperFillModel) -> _MarketContext:
    timestamp = _parse_dt(payload.get("timestamp") or payload.get("t") or payload.get("time")) or _utc_now()
    symbol = str(payload.get("symbol") or payload.get("S") or "").upper() or None
    bid = _optional_float(payload.get("bid") or payload.get("bid_price") or payload.get("bp"))
    ask = _optional_float(payload.get("ask") or payload.get("ask_price") or payload.get("ap"))
    high = _optional_float(payload.get("high") or payload.get("h"))
    low = _optional_float(payload.get("low") or payload.get("l"))
    price = _optional_float(payload.get("price") or payload.get("last") or payload.get("mark_price") or payload.get("close") or payload.get("c"))
    if price is None and bid is not None and ask is not None and ask >= bid:
        price = round((bid + ask) / 2, 10)
    completed_bar = _bool(payload.get("completed_bar") or payload.get("bar_complete") or payload.get("is_final"), False)
    allow_bar_execution = _bool(payload.get("allow_bar_execution"), fill_model.allow_bar_execution)
    volume = _optional_int(payload.get("volume") or payload.get("v"))
    return _MarketContext(symbol=symbol, timestamp=timestamp, price=price, bid=bid, ask=ask, high=high, low=low, volume=volume, completed_bar=completed_bar, allow_bar_execution=allow_bar_execution)


def _execution_decision(order: Mapping[str, Any], context: _MarketContext, model: WcaLocalPaperFillModel) -> str:
    order_type = str(order.get("type") or order.get("order_type") or "LIMIT").upper()
    if order_type == "STOP_LIMIT":
        if _stop_is_triggered(order):
            return "fill" if _limit_executable(order, context, model) else "none"
        if context.has_valid_quote:
            if _stop_condition_met(order, context, quote_only=True):
                return "fill" if _limit_executable(order, context, model) else "trigger_only"
            return "none"
        if _bar_execution_allowed(context, model) and _stop_condition_met(order, context, quote_only=False):
            return "trigger_only"
        return "none"
    return "fill" if _limit_executable(order, context, model) else "none"


def _limit_executable(order: Mapping[str, Any], context: _MarketContext, model: WcaLocalPaperFillModel) -> bool:
    side = _side_value(order.get("side") or WcaSide.BUY).upper()
    limit_price = _optional_float(order.get("limit_price"))
    if limit_price is None:
        return False
    if context.has_valid_quote:
        return context.ask <= limit_price if side == WcaSide.BUY.value else context.bid >= limit_price
    if not _bar_execution_allowed(context, model):
        return False
    if side == WcaSide.BUY.value:
        return context.low is not None and context.low <= limit_price
    return context.high is not None and context.high >= limit_price


def _stop_condition_met(order: Mapping[str, Any], context: _MarketContext, *, quote_only: bool) -> bool:
    side = _side_value(order.get("side") or WcaSide.BUY).upper()
    stop_price = _optional_float(order.get("stop_price"))
    if stop_price is None:
        return False
    if context.has_valid_quote:
        return context.bid <= stop_price if side == WcaSide.SELL.value else context.ask >= stop_price
    if quote_only:
        return False
    if side == WcaSide.SELL.value:
        return context.low is not None and context.low <= stop_price
    return context.high is not None and context.high >= stop_price


def _bar_execution_allowed(context: _MarketContext, model: WcaLocalPaperFillModel) -> bool:
    return context.allow_bar_execution and model.allow_bar_execution and context.completed_bar


def _stop_is_triggered(order: Mapping[str, Any]) -> bool:
    return bool(order.get("stop_triggered") or order.get("stopTriggered") or order.get("stop_triggered_at") or order.get("stopTriggeredAt"))


def _fill_quantity_for_order(order: Mapping[str, Any], context: _MarketContext, model: WcaLocalPaperFillModel) -> int:
    total_quantity = int(float(order.get("qty") or order.get("quantity") or 0))
    already_filled = int(float(order.get("filled_qty") or order.get("filledQuantity") or 0))
    remaining = max(0, total_quantity - already_filled)
    if remaining <= 0:
        return 0
    capped = remaining
    if model.max_fill_quantity is not None:
        capped = min(capped, int(model.max_fill_quantity))
    if context.volume is not None:
        capped = min(capped, int(context.volume * model.participation_limit))
    if capped <= 0:
        return 0
    if capped < remaining and not model.allow_partial_fills:
        return 0
    return min(remaining, capped)


def _fill_price_for_order(order: Mapping[str, Any], context: _MarketContext, model: WcaLocalPaperFillModel) -> float:
    side = _side_value(order.get("side") or WcaSide.BUY).upper()
    limit_price = _optional_float(order.get("limit_price")) or 0.01
    if context.has_valid_quote:
        quote_price = context.ask if side == WcaSide.BUY.value else context.bid
        executable_price = quote_price if quote_price is not None else limit_price
        if side == WcaSide.BUY.value:
            return max(0.01, min(limit_price, executable_price))
        return max(0.01, max(limit_price, executable_price))
    return max(0.01, limit_price)


def _costs_for_fill(*, quantity: int, fill_price: float, model: WcaLocalPaperFillModel) -> dict[str, float]:
    notional = max(0.0, quantity * fill_price)
    spread_cost = round(notional * model.spread_cost_bps / 10_000, 10)
    slippage = round(notional * model.slippage_bps / 10_000, 10)
    commissions = round(max(model.minimum_commission, model.commission_per_order + (model.commission_per_share * quantity)), 10)
    regulatory_fees = round(model.regulatory_fee_per_share * quantity, 10)
    fees = regulatory_fees
    return {
        "commissions": commissions,
        "fees": fees,
        "slippage": round(slippage + spread_cost, 10),
        "spread_cost": spread_cost,
        "regulatory_fees": regulatory_fees,
    }


def _record_is_protective(record: Any) -> bool:
    return str(getattr(record, "client_order_id", "") or "").startswith("wca-protection-")


def _is_resting_protective_order(request: WcaPaperBrokerOrderRequest) -> bool:
    return request.client_order_id.startswith("wca-protection-") or request.order_type == "STOP_LIMIT"


def _is_protective_payload(payload: Mapping[str, Any]) -> bool:
    client_id = str(payload.get("client_order_id") or "")
    order_type = str(payload.get("type") or payload.get("order_type") or "").upper()
    return client_id.startswith("wca-protection-") or order_type in {"STOP", "STOP_LIMIT", "TRAILING_STOP"}


def _position_notional(positions: list[BrokerPositionState]) -> float:
    return sum(position.quantity * position.markPrice for position in positions)


def _average_mark_from_lots(lots: tuple[dict[str, Any], ...]) -> float:
    total_quantity = sum(int(lot.get("quantity") or 0) for lot in lots)
    if total_quantity <= 0:
        return 0.01
    total = sum(int(lot.get("quantity") or 0) * float(lot.get("entry_price") or 0.01) for lot in lots)
    return max(0.01, total / total_quantity)


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _bool(value: Any, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _float(value: Any, default: float) -> float:
    parsed = _optional_float(value)
    return default if parsed is None else parsed


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _side_value(side: WcaSide | str | Any) -> str:
    return side.value if isinstance(side, WcaSide) else str(side)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "WCA_LOCAL_PAPER_BROKER_VERSION",
    "WCA_LOCAL_PAPER_CROSS_ALGORITHM_MUTATION_BLOCKED",
    "WcaLocalPaperBroker",
    "WcaLocalPaperBrokerConfigurationError",
]
