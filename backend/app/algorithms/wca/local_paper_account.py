"""Authoritative WCA-local paper account state."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaOrderStatus, WcaSide
from backend.app.algorithms.wca.repository import WcaInventoryLedgerEvent, WcaSqliteRepository
from backend.app.domain.models import Signal
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState

WCA_ALPACA_PAPER_API_KEY_ID = "WCA_ALPACA_PAPER_API_KEY_ID"
WCA_ALPACA_PAPER_API_SECRET_KEY = "WCA_ALPACA_PAPER_API_SECRET_KEY"
WCA_ALPACA_PAPER_BASE_URL = "WCA_ALPACA_PAPER_BASE_URL"
WCA_ALPACA_PAPER_ACCOUNT_ID = "WCA_ALPACA_PAPER_ACCOUNT_ID"
WCA_AUTOMATIC_PAPER_ENABLED = "WCA_AUTOMATIC_PAPER_ENABLED"
WCA_ALPACA_PAPER_ACCOUNT_SHARED = "WCA_ALPACA_PAPER_ACCOUNT_SHARED"
WCA_ACCOUNT_LEVEL_ALLOCATOR_ENABLED = "WCA_ACCOUNT_LEVEL_ALLOCATOR_ENABLED"
WCA_LOCAL_PAPER_ACCOUNT_ID = "WCA_LOCAL_PAPER_ACCOUNT_ID"
WCA_LOCAL_PAPER_STARTING_BALANCE = "WCA_LOCAL_PAPER_STARTING_BALANCE"
WCA_LOCAL_PAPER_SOURCE_AUTHORITY = "wca_local_paper_account"
WCA_DEFAULT_LOCAL_PAPER_STARTING_BALANCE = 100_000.0

_GENERIC_ALPACA_KEY_ID = "APCA_API_KEY_ID"
_GENERIC_ALPACA_SECRET_KEY = "APCA_API_SECRET_KEY"
WCA_LOCAL_PAPER_ACCOUNT_VERSION = "wca_local_paper_account_v1"
WCA_LOCAL_PAPER_CAPITAL_PARTITION_ID = "wca.local_paper"
_TERMINAL_ORDER_STATUSES = {
    WcaOrderStatus.FILLED.value,
    WcaOrderStatus.REJECTED.value,
    WcaOrderStatus.CANCELLED.value,
    WcaOrderStatus.RECONCILED.value,
}


@dataclass(frozen=True)
class WcaLocalPaperAccountValidation:
    verified: bool
    account_id: str | None
    base_url: str | None
    automatic_paper_enabled: bool
    starting_balance: float
    source_authority: str
    reason_codes: tuple[str, ...]

    def model_dump(self) -> dict[str, object]:
        return {
            "verified": self.verified,
            "account_id": self.account_id,
            "base_url": self.base_url,
            "automatic_paper_enabled": self.automatic_paper_enabled,
            "starting_balance": self.starting_balance,
            "source_authority": self.source_authority,
            "reason_codes": self.reason_codes,
        }


def validate_wca_local_paper_account(
    *,
    account_id: str,
    environ: Mapping[str, str] | None = None,
) -> WcaLocalPaperAccountValidation:
    source = environ or os.environ
    key_id = _clean(source.get(WCA_ALPACA_PAPER_API_KEY_ID))
    secret = _clean(source.get(WCA_ALPACA_PAPER_API_SECRET_KEY))
    base_url = _clean(source.get(WCA_ALPACA_PAPER_BASE_URL))
    configured_account = _clean(source.get(WCA_LOCAL_PAPER_ACCOUNT_ID)) or account_id
    legacy_configured_account = _clean(source.get(WCA_ALPACA_PAPER_ACCOUNT_ID))
    automatic_enabled = _env_bool(source.get(WCA_AUTOMATIC_PAPER_ENABLED))
    starting_balance = _env_float(source.get(WCA_LOCAL_PAPER_STARTING_BALANCE), WCA_DEFAULT_LOCAL_PAPER_STARTING_BALANCE)
    reasons: list[str] = ["wca.local_paper_account.validation"]

    if not automatic_enabled:
        reasons.append("wca.local_paper_account.automatic_paper_disabled")
    if not account_id:
        reasons.append("wca.local_paper_account.account_id_missing")
    if not configured_account:
        reasons.append("wca.local_paper_account.configured_account_id_missing")
    elif configured_account != account_id:
        reasons.append("wca.local_paper_account.account_id_mismatch")
    if starting_balance <= 0:
        reasons.append("wca.local_paper_account.starting_balance_invalid")
    if key_id or secret or base_url or legacy_configured_account:
        reasons.append("wca.local_paper_account.alpaca_paper_execution_disabled")
    if _reuses_generic_alpaca_credentials(source, key_id=key_id, secret=secret):
        reasons.append("wca.local_paper_account.shared_alpaca_credentials_rejected")

    verified = reasons == ["wca.local_paper_account.validation"]
    if verified:
        reasons.append("wca.local_paper_account.verified")
    return WcaLocalPaperAccountValidation(
        verified=verified,
        account_id=configured_account,
        base_url=None,
        automatic_paper_enabled=automatic_enabled,
        starting_balance=starting_balance,
        source_authority=WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
        reason_codes=tuple(reasons),
    )


WcaPaperAccountValidation = WcaLocalPaperAccountValidation
validate_wca_automatic_paper_account = validate_wca_local_paper_account

@dataclass(frozen=True)
class WcaLocalPaperLotSnapshot:
    lot_id: str
    algorithm_id: str
    account_id: str
    symbol: str
    side: str
    quantity: int
    entry_price: float
    remaining_quantity: int | None = None
    entry_timestamp: datetime | None = None
    opened_at: datetime | None = None
    decision_id: str | None = None
    order_intent_id: str | None = None
    stop_price: float | None = None
    target_price: float | None = None


@dataclass(frozen=True)
class WcaLocalPaperPositionSnapshot:
    algorithm_id: str
    account_id: str
    symbol: str
    side: str
    quantity: int
    average_entry_price: float
    mark_price: float
    unrealized_pnl: float
    lots: tuple[WcaLocalPaperLotSnapshot, ...] = ()
    stop_price: float | None = None
    target_price: float | None = None
    opened_at: datetime | None = None
    position_id: str = ""
    realized_pnl: float = 0.0
    last_updated_at: datetime | None = None


@dataclass(frozen=True)
class WcaLocalPaperOrderSnapshot:
    algorithm_id: str
    account_id: str
    symbol: str
    side: str
    quantity: int
    status: str
    client_order_id: str
    order_intent_id: str | None = None
    broker_order_id: str | None = None
    order_type: str = "LIMIT"
    limit_price: float | None = None
    stop_price: float | None = None
    submitted_at: datetime | None = None
    position_owner: str = WCA_ALGORITHM_ID
    exit_owner: str | None = None
    local_order_id: str | None = None
    remaining_quantity: int | None = None
    target_price: float | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    decision_id: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class WcaLocalPaperFillSnapshot:
    fill_id: str
    algorithm_id: str
    account_id: str
    order_id: str
    symbol: str
    side: str
    quantity: int
    fill_price: float
    timestamp: datetime
    commissions: float = 0.0
    fees: float = 0.0
    slippage: float = 0.0


@dataclass(frozen=True)
class WcaLocalPaperAccountSnapshot:
    algorithm_id: str
    account_id: str
    starting_balance: float
    cash: float
    equity: float
    buying_power: float
    realized_pnl: float
    unrealized_pnl: float
    daily_realized_pnl: float
    daily_unrealized_pnl: float
    daily_loss: float
    gross_exposure: float
    net_exposure: float
    reserved_risk: float
    trades_today: int
    session_date: date
    positions: tuple[WcaLocalPaperPositionSnapshot, ...]
    open_orders: tuple[WcaLocalPaperOrderSnapshot, ...]
    lots: tuple[WcaLocalPaperLotSnapshot, ...]
    fills: tuple[WcaLocalPaperFillSnapshot, ...]
    circuit_breaker_state: str
    cooldown_until: datetime | None
    last_mark_timestamp: datetime | None
    state_version: str


class WcaLocalPaperAccount:
    """Mutable WCA-local paper account core that returns immutable snapshots."""

    algorithm_id = WCA_ALGORITHM_ID

    def __init__(
        self,
        *,
        account_id: str,
        starting_balance: float,
        session_date: date | str | None = None,
        cash: float | None = None,
        realized_pnl: float = 0.0,
        daily_realized_pnl: float = 0.0,
        reserved_risk: float = 0.0,
        trades_today: int = 0,
        lots: Iterable[WcaLocalPaperLotSnapshot] = (),
        open_orders: Iterable[WcaLocalPaperOrderSnapshot] = (),
        fills: Iterable[WcaLocalPaperFillSnapshot] = (),
        circuit_breaker_state: str = "closed",
        cooldown_until: datetime | str | None = None,
        last_mark_timestamp: datetime | str | None = None,
    ) -> None:
        self.account_id = str(account_id or "").strip()
        self.starting_balance = float(starting_balance)
        if not self.account_id:
            raise ValueError("WCA local paper account requires account_id")
        if self.starting_balance <= 0:
            raise ValueError("WCA local paper account requires positive starting_balance")
        self.session_date = _coerce_date(session_date)
        self._cash = float(self.starting_balance if cash is None else cash)
        self._reserved_cash = 0.0
        self._realized_pnl = float(realized_pnl)
        self._daily_realized_pnl = float(daily_realized_pnl)
        self._reserved_risk = float(reserved_risk)
        self._trades_today = int(trades_today)
        self._lots = tuple(_copy_lot(lot, account_id=self.account_id) for lot in lots)
        self._open_orders = tuple(_copy_order(order, account_id=self.account_id) for order in open_orders)
        self._fills = tuple(_copy_fill(fill, account_id=self.account_id) for fill in fills)
        self._marks = {lot.symbol: lot.entry_price for lot in self._lots}
        self._circuit_breaker_state = str(circuit_breaker_state or "closed")
        self._cooldown_until = _parse_dt(cooldown_until)
        self._last_mark_timestamp = _parse_dt(last_mark_timestamp)
        self._validate_owned_state()

    @classmethod
    def restore(
        cls,
        repository: WcaSqliteRepository,
        *,
        account_id: str,
        symbol: str,
        starting_balance: float,
        session_date: date | str | None = None,
    ) -> "WcaLocalPaperAccount":
        selected_symbol = str(symbol or "").upper()
        selected_session = _coerce_date(session_date)
        starting_balance = _restored_starting_balance_from_ledger(
            repository,
            account_id=account_id,
            symbol=selected_symbol,
            fallback=starting_balance,
        )
        local_inventory = repository.read_wca_local_inventory_snapshot(local_account_id=account_id, symbol=selected_symbol)
        if local_inventory is not None:
            return _account_from_local_inventory(cls, local_inventory, account_id=account_id, symbol=selected_symbol, starting_balance=starting_balance, session_date=selected_session)
        projection = repository.read_inventory_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=account_id, symbol=selected_symbol)
        daily = repository.read_daily_state_projection(algorithm_id=WCA_ALGORITHM_ID, broker_account_id=account_id, symbol=selected_symbol, session_date=selected_session.isoformat())
        lots = tuple(_lot_from_repository(row) for row in repository.list_open_wca_lots(account_id=account_id, symbol=selected_symbol))
        if not lots and projection.open_quantity > 0:
            lots = (_lot_from_projection(account_id=account_id, symbol=selected_symbol, projection=projection),)
        open_orders = _open_orders_from_repository(repository, account_id=account_id, symbol=selected_symbol)
        equity = max(0.0, float(starting_balance) + float(projection.realized_pnl or 0.0) + float(projection.unrealized_pnl or 0.0))
        cash = max(0.0, equity - _gross_exposure_from_lots(lots))
        account = cls(
            account_id=account_id,
            starting_balance=starting_balance,
            session_date=selected_session,
            cash=cash,
            realized_pnl=float(projection.realized_pnl or 0.0),
            daily_realized_pnl=float(daily.realized_pnl_today or 0.0),
            reserved_risk=float(projection.reserved_risk or daily.current_reserved_risk or 0.0),
            trades_today=int(daily.trades_completed_today or 0),
            lots=lots,
            open_orders=open_orders,
            circuit_breaker_state=daily.circuit_breaker_state or "closed",
            cooldown_until=daily.cooldown_until,
            last_mark_timestamp=projection.last_event_timestamp,
        )
        if projection.average_entry_price > 0:
            account._marks[selected_symbol] = float(projection.average_entry_price)
        return account

    def get_account_snapshot(self) -> WcaLocalPaperAccountSnapshot:
        positions = self._positions()
        unrealized = round(sum(position.unrealized_pnl for position in positions), 10)
        gross = round(sum(abs(position.quantity * position.mark_price) for position in positions), 10)
        net = round(sum(_signed_quantity(position.side, position.quantity) * position.mark_price for position in positions), 10)
        equity = round(self._cash + gross, 10)
        buying_power = max(0.0, round(self._cash - self._reserved_cash - self._reserved_risk, 10))
        seed = {
            "algorithm_id": WCA_ALGORITHM_ID,
            "account_id": self.account_id,
            "cash": round(self._cash, 10),
            "equity": equity,
            "buying_power": buying_power,
            "realized_pnl": round(self._realized_pnl, 10),
            "unrealized_pnl": unrealized,
            "reserved_risk": round(self._reserved_risk, 10),
            "trades_today": self._trades_today,
            "session_date": self.session_date.isoformat(),
            "positions": [position.__dict__ for position in positions],
            "open_orders": [order.__dict__ for order in self._open_orders],
            "lots": [lot.__dict__ for lot in self._lots],
            "fills": [fill.__dict__ for fill in self._fills],
            "circuit_breaker_state": self._circuit_breaker_state,
            "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None,
            "last_mark_timestamp": self._last_mark_timestamp.isoformat() if self._last_mark_timestamp else None,
        }
        return WcaLocalPaperAccountSnapshot(
            algorithm_id=WCA_ALGORITHM_ID,
            account_id=self.account_id,
            starting_balance=self.starting_balance,
            cash=round(self._cash, 10),
            equity=equity,
            buying_power=buying_power,
            realized_pnl=round(self._realized_pnl, 10),
            unrealized_pnl=unrealized,
            daily_realized_pnl=round(self._daily_realized_pnl, 10),
            daily_unrealized_pnl=unrealized,
            daily_loss=max(0.0, round(-self._daily_realized_pnl, 10)),
            gross_exposure=gross,
            net_exposure=net,
            reserved_risk=round(self._reserved_risk, 10),
            trades_today=self._trades_today,
            session_date=self.session_date,
            positions=positions,
            open_orders=tuple(self._open_orders),
            lots=tuple(self._lots),
            fills=tuple(self._fills),
            circuit_breaker_state=self._circuit_breaker_state,
            cooldown_until=self._cooldown_until,
            last_mark_timestamp=self._last_mark_timestamp,
            state_version=_stable_hash(seed),
        )

    def get_position(self, symbol: str) -> WcaLocalPaperPositionSnapshot | None:
        selected = str(symbol or "").upper()
        return next((position for position in self.get_account_snapshot().positions if position.symbol == selected), None)

    def get_open_orders(self, symbol: str | None = None) -> tuple[WcaLocalPaperOrderSnapshot, ...]:
        if symbol is None:
            return tuple(self._open_orders)
        selected = str(symbol or "").upper()
        return tuple(order for order in self._open_orders if order.symbol == selected)

    def reserve_cash(self, amount: float, *, reason: str = "") -> WcaLocalPaperAccountSnapshot:
        parsed = _positive_amount(amount, "cash reservation")
        if parsed > self.get_account_snapshot().buying_power:
            raise ValueError("WCA local paper account cannot reserve more cash than buying power")
        self._reserved_cash = round(self._reserved_cash + parsed, 10)
        return self.get_account_snapshot()

    def release_cash(self, amount: float, *, reason: str = "") -> WcaLocalPaperAccountSnapshot:
        parsed = _positive_amount(amount, "cash release")
        if parsed > self._reserved_cash + 1e-9:
            raise ValueError("WCA local paper account cannot release more cash than reserved")
        self._reserved_cash = round(max(0.0, self._reserved_cash - parsed), 10)
        return self.get_account_snapshot()

    def reserve_risk(self, amount: float, *, reason: str = "") -> WcaLocalPaperAccountSnapshot:
        parsed = _positive_amount(amount, "risk reservation")
        if parsed > self.get_account_snapshot().buying_power:
            raise ValueError("WCA local paper account cannot reserve more risk than buying power")
        self._reserved_risk = round(self._reserved_risk + parsed, 10)
        return self.get_account_snapshot()

    def release_risk(self, amount: float, *, reason: str = "") -> WcaLocalPaperAccountSnapshot:
        parsed = _positive_amount(amount, "risk release")
        if parsed > self._reserved_risk + 1e-9:
            raise ValueError("WCA local paper account cannot release more risk than reserved")
        self._reserved_risk = round(max(0.0, self._reserved_risk - parsed), 10)
        return self.get_account_snapshot()

    def apply_fill(
        self,
        *,
        symbol: str,
        side: WcaSide | str,
        quantity: int,
        price: float,
        filled_at: datetime | str | None = None,
        decision_id: str | None = None,
        order_intent_id: str | None = None,
        lot_id: str | None = None,
        local_order_id: str | None = None,
        client_order_id: str | None = None,
        fill_id: str | None = None,
        commissions: float = 0.0,
        fees: float = 0.0,
        slippage: float = 0.0,
    ) -> WcaLocalPaperAccountSnapshot:
        selected_symbol = str(symbol or "").upper()
        selected_side = _side_value(side)
        parsed_quantity = int(quantity)
        parsed_price = _positive_amount(price, "fill price")
        if parsed_quantity <= 0:
            raise ValueError("WCA local paper fill quantity must be positive")
        timestamp = _parse_dt(filled_at) or _utc_now()
        order_id = str(local_order_id or order_intent_id or client_order_id or "")
        generated_fill_id = fill_id or f"wca-local-fill-{_stable_hash((self.account_id, selected_symbol, selected_side, timestamp.isoformat(), parsed_quantity, parsed_price, order_id))[:12]}"
        total_charges = round(max(0.0, float(commissions)) + max(0.0, float(fees)) + max(0.0, float(slippage)), 10)
        if selected_side == WcaSide.BUY.value:
            notional = parsed_quantity * parsed_price
            if notional + total_charges > self.get_account_snapshot().buying_power + self._reserved_cash + self._reserved_risk:
                raise ValueError("WCA local paper fill exceeds local buying power")
            created_lot_id = lot_id or f"wca-local-lot-{_stable_hash((self.account_id, selected_symbol, timestamp.isoformat(), parsed_quantity, parsed_price))[:12]}"
            self._cash = round(self._cash - notional - total_charges, 10)
            self._lots = (*self._lots, WcaLocalPaperLotSnapshot(
                lot_id=created_lot_id,
                algorithm_id=WCA_ALGORITHM_ID,
                account_id=self.account_id,
                symbol=selected_symbol,
                side=selected_side,
                quantity=parsed_quantity,
                entry_price=parsed_price,
                remaining_quantity=parsed_quantity,
                entry_timestamp=timestamp,
                opened_at=timestamp,
                decision_id=decision_id,
                order_intent_id=order_intent_id,
            ))
            self._fills = (*self._fills, WcaLocalPaperFillSnapshot(
                fill_id=generated_fill_id,
                algorithm_id=WCA_ALGORITHM_ID,
                account_id=self.account_id,
                order_id=order_id or created_lot_id,
                symbol=selected_symbol,
                side=selected_side,
                quantity=parsed_quantity,
                fill_price=parsed_price,
                commissions=max(0.0, float(commissions)),
                fees=max(0.0, float(fees)),
                slippage=max(0.0, float(slippage)),
                timestamp=timestamp,
            ))
            self._marks[selected_symbol] = parsed_price
            self._last_mark_timestamp = timestamp
            return self.get_account_snapshot()
        return self.close_position(
            symbol=selected_symbol,
            quantity=parsed_quantity,
            price=parsed_price,
            closed_at=timestamp,
            order_id=order_id,
            fill_id=generated_fill_id,
            commissions=commissions,
            fees=fees,
            slippage=slippage,
        )

    def mark_to_market(self, *, symbol: str, mark_price: float, marked_at: datetime | str | None = None) -> WcaLocalPaperAccountSnapshot:
        selected_symbol = str(symbol or "").upper()
        self._marks[selected_symbol] = _positive_amount(mark_price, "mark price")
        self._last_mark_timestamp = _parse_dt(marked_at) or _utc_now()
        return self.get_account_snapshot()

    def close_position(
        self,
        *,
        symbol: str,
        quantity: int | None = None,
        price: float,
        closed_at: datetime | str | None = None,
        order_id: str | None = None,
        fill_id: str | None = None,
        commissions: float = 0.0,
        fees: float = 0.0,
        slippage: float = 0.0,
    ) -> WcaLocalPaperAccountSnapshot:
        selected_symbol = str(symbol or "").upper()
        parsed_price = _positive_amount(price, "close price")
        open_lots = [lot for lot in self._lots if lot.symbol == selected_symbol]
        available = sum(lot.quantity for lot in open_lots)
        close_quantity = available if quantity is None else int(quantity)
        if close_quantity <= 0 or close_quantity > available:
            raise ValueError("WCA local paper account cannot close more quantity than WCA owns")
        timestamp = _parse_dt(closed_at) or _utc_now()
        remaining_to_close = close_quantity
        updated_lots: list[WcaLocalPaperLotSnapshot] = []
        realized = 0.0
        close_side = WcaSide.SELL.value if open_lots[0].side == WcaSide.BUY.value else WcaSide.BUY.value
        for lot in self._lots:
            if lot.symbol != selected_symbol or remaining_to_close <= 0:
                updated_lots.append(lot)
                continue
            closing = min(lot.quantity, remaining_to_close)
            realized = round(realized + _realized_pnl(lot.side, lot.entry_price, parsed_price, closing), 10)
            remaining = lot.quantity - closing
            if remaining > 0:
                updated_lots.append(replace(lot, quantity=remaining, remaining_quantity=remaining))
            remaining_to_close -= closing
        total_charges = round(max(0.0, float(commissions)) + max(0.0, float(fees)) + max(0.0, float(slippage)), 10)
        realized = round(realized - total_charges, 10)
        self._lots = tuple(updated_lots)
        self._cash = round(self._cash + close_quantity * parsed_price - total_charges, 10)
        self._realized_pnl = round(self._realized_pnl + realized, 10)
        self._daily_realized_pnl = round(self._daily_realized_pnl + realized, 10)
        self._fills = (*self._fills, WcaLocalPaperFillSnapshot(
            fill_id=fill_id or f"wca-local-fill-{_stable_hash((self.account_id, selected_symbol, close_side, timestamp.isoformat(), close_quantity, parsed_price, order_id or 'close'))[:12]}",
            algorithm_id=WCA_ALGORITHM_ID,
            account_id=self.account_id,
            order_id=str(order_id or f"wca-local-close-{self.account_id}-{selected_symbol}-{timestamp.isoformat()}"),
            symbol=selected_symbol,
            side=close_side,
            quantity=close_quantity,
            fill_price=parsed_price,
            commissions=max(0.0, float(commissions)),
            fees=max(0.0, float(fees)),
            slippage=max(0.0, float(slippage)),
            timestamp=timestamp,
        ))
        self._trades_today += 1 if self.get_position(selected_symbol) is None else 0
        self._last_mark_timestamp = timestamp
        return self.get_account_snapshot()

    def reset_daily_state(
        self,
        *,
        session_date: date | str | None = None,
        reset_circuit_breaker: bool = True,
        reset_cooldown: bool = True,
    ) -> WcaLocalPaperAccountSnapshot:
        self.session_date = _coerce_date(session_date)
        self._daily_realized_pnl = 0.0
        self._trades_today = 0
        if reset_circuit_breaker:
            self._circuit_breaker_state = "closed"
        if reset_cooldown:
            self._cooldown_until = None
        return self.get_account_snapshot()

    def can_open_position(self, *, symbol: str, estimated_notional: float, risk_amount: float = 0.0) -> tuple[bool, tuple[str, ...]]:
        reasons: list[str] = []
        if self._circuit_breaker_state.lower() not in {"", "closed"}:
            reasons.append("wca.local_paper.circuit_breaker_open")
        if self._cooldown_until is not None and _utc_now() < self._cooldown_until:
            reasons.append("wca.local_paper.cooldown_active")
        required = max(0.0, float(estimated_notional)) + max(0.0, float(risk_amount))
        if required > self.get_account_snapshot().buying_power + 1e-9:
            reasons.append("wca.local_paper.insufficient_buying_power")
        if self.get_position(symbol) is not None:
            reasons.append("wca.local_paper.position_already_open")
        return not reasons, tuple(reasons or ("wca.local_paper.can_open_position",))

    def persist(self, repository: WcaSqliteRepository, *, symbol: str = "SPY", event_id: str | None = None, timestamp: datetime | str | None = None) -> WcaLocalPaperAccountSnapshot:
        snapshot = self.get_account_snapshot()
        selected_symbol = str(symbol or "SPY").upper()
        position = self.get_position(selected_symbol)
        now = _parse_dt(timestamp) or _utc_now()
        inventory_event = WcaInventoryLedgerEvent(
            inventory_event_id=event_id or f"wca-local-paper-account-persist-{self.account_id}-{selected_symbol}-{snapshot.state_version[:16]}",
            event_type="RECONCILIATION_CORRECTION",
            broker_account_id=self.account_id,
            symbol=selected_symbol,
            event_timestamp=now.isoformat(),
            trade_date=snapshot.session_date.isoformat(),
            quantity=position.quantity if position else 0,
            average_entry_price=position.average_entry_price if position else 0.0,
            realized_pnl=snapshot.realized_pnl,
            unrealized_pnl=snapshot.unrealized_pnl,
            reserved_risk=snapshot.reserved_risk,
            source_authority=WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
            configuration_version=WCA_LOCAL_PAPER_ACCOUNT_VERSION,
            decision_id="wca-local-paper-account-persist",
            run_id="wca-local-paper-account-persist",
            payload={
                "open_quantity": position.quantity if position else 0,
                "average_entry_price": position.average_entry_price if position else 0.0,
                "cash": snapshot.cash,
                "equity": snapshot.equity,
                "buying_power": snapshot.buying_power,
                "daily_loss": snapshot.daily_loss,
                "reserved_risk": snapshot.reserved_risk,
                "trades_today": snapshot.trades_today,
                "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
                "stateVersion": snapshot.state_version,
            },
        )
        repository.write_broker_account_snapshot(
            self.to_broker_account_snapshot(symbol=selected_symbol),
            symbol=selected_symbol,
            cash=snapshot.cash,
            configuration_version=WCA_LOCAL_PAPER_ACCOUNT_VERSION,
            decision_id="wca-local-paper-account-persist",
            run_id="wca-local-paper-account-persist",
        )
        repository.write_wca_local_inventory_snapshot(snapshot, symbol=selected_symbol, timestamp=now, inventory_event=inventory_event)
        return snapshot

    def to_broker_account_snapshot(self, *, symbol: str | None = None, observed_at: datetime | None = None) -> BrokerAccountSnapshot:
        snapshot = self.get_account_snapshot()
        selected_symbol = str(symbol).upper() if symbol else None
        positions = [_broker_position(position) for position in snapshot.positions if selected_symbol is None or position.symbol == selected_symbol]
        orders = [_broker_order(order) for order in snapshot.open_orders if selected_symbol is None or order.symbol == selected_symbol]
        pending = [order for order in orders if order.status != "PARTIALLY_FILLED"]
        partial = [order for order in orders if order.status == "PARTIALLY_FILLED"]
        observed = observed_at or self._last_mark_timestamp or _utc_now()
        return BrokerAccountSnapshot(
            accountId=self.account_id,
            equity=snapshot.equity,
            buyingPower=snapshot.buying_power,
            realizedPnlToday=snapshot.daily_realized_pnl,
            intradayEquityHigh=max(snapshot.equity, self.starting_balance),
            positions=positions,
            pendingOrders=pending,
            partiallyFilledOrders=partial,
            observedAt=observed,
            sessionDate=snapshot.session_date,
            sourceAuthority=WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
            positionsReconciled=True,
            openOrdersReconciled=True,
        )

    def _positions(self) -> tuple[WcaLocalPaperPositionSnapshot, ...]:
        positions: list[WcaLocalPaperPositionSnapshot] = []
        for symbol in sorted({lot.symbol for lot in self._lots}):
            lots = tuple(lot for lot in self._lots if lot.symbol == symbol)
            quantity = sum(lot.quantity for lot in lots)
            if quantity <= 0:
                continue
            side = lots[0].side
            average_entry = round(sum(lot.quantity * lot.entry_price for lot in lots) / quantity, 10)
            mark = float(self._marks.get(symbol) or average_entry)
            unrealized = _realized_pnl(side, average_entry, mark, quantity)
            positions.append(
                WcaLocalPaperPositionSnapshot(
                    algorithm_id=WCA_ALGORITHM_ID,
                    account_id=self.account_id,
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    average_entry_price=average_entry,
                    mark_price=mark,
                    unrealized_pnl=unrealized,
                    lots=lots,
                    stop_price=next((lot.stop_price for lot in lots if lot.stop_price), None),
                    target_price=next((lot.target_price for lot in lots if lot.target_price), None),
                    opened_at=min((lot.opened_at or lot.entry_timestamp for lot in lots if lot.opened_at or lot.entry_timestamp), default=None),
                    position_id=f"wca-local-position-{self.account_id}-{symbol}",
                    realized_pnl=self._realized_pnl,
                    last_updated_at=self._last_mark_timestamp,
                )
            )
        return tuple(positions)

    def _validate_owned_state(self) -> None:
        for lot in self._lots:
            if lot.algorithm_id != WCA_ALGORITHM_ID or lot.account_id != self.account_id:
                raise ValueError("WCA local paper account cannot load non-WCA lots")
        for order in self._open_orders:
            if order.algorithm_id != WCA_ALGORITHM_ID or order.account_id != self.account_id:
                raise ValueError("WCA local paper account cannot load non-WCA orders")
        for fill in self._fills:
            if fill.algorithm_id != WCA_ALGORITHM_ID or fill.account_id != self.account_id:
                raise ValueError("WCA local paper account cannot load non-WCA fills")


def _open_orders_from_repository(repository: WcaSqliteRepository, *, account_id: str, symbol: str) -> tuple[WcaLocalPaperOrderSnapshot, ...]:
    orders: list[WcaLocalPaperOrderSnapshot] = []
    for record in repository.list_execution_outbox_records(account_id=account_id):
        if record.symbol.upper() != symbol or _order_status_value(record.status) in _TERMINAL_ORDER_STATUSES:
            continue
        proposed = record.proposed_order
        record_timestamp = getattr(record, "created_at", None) or getattr(record.decision, "decision_timestamp", None) or _utc_now()
        orders.append(
            WcaLocalPaperOrderSnapshot(
                algorithm_id=WCA_ALGORITHM_ID,
                account_id=record.account_id,
                symbol=record.symbol.upper(),
                side=_side_value(getattr(proposed, "side", WcaSide.BUY)),
                quantity=int(getattr(proposed, "quantity", 0) or 0),
                status=str(record.status),
                client_order_id=record.client_order_id,
                order_intent_id=record.order_intent_id,
                order_type=str((record.request_payload or {}).get("order_type") or "LIMIT").upper(),
                limit_price=_optional_float((record.request_payload or {}).get("limit_price")),
                stop_price=_optional_float((record.request_payload or {}).get("stop_price")),
                submitted_at=_parse_dt(record_timestamp),
                local_order_id=record.outbox_id,
                remaining_quantity=int(getattr(proposed, "quantity", 0) or 0),
                created_at=_parse_dt(record_timestamp),
                updated_at=_parse_dt(getattr(record, "updated_at", None) or record_timestamp),
                decision_id=record.decision_id,
                idempotency_key=record.idempotency_key,
            )
        )
    with repository.connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM wca_broker_orders
            WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
              AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED', 'RECONCILED')
              AND client_order_id LIKE 'wca-protection-%'
            ORDER BY created_at
            """,
            (WCA_ALGORITHM_ID, account_id, symbol),
        ).fetchall()
    for row in rows:
        request = json.loads(row["request_payload_json"] or "{}")
        orders.append(
            WcaLocalPaperOrderSnapshot(
                algorithm_id=WCA_ALGORITHM_ID,
                account_id=row["account_id"],
                symbol=row["symbol"].upper(),
                side=str(row["side"]),
                quantity=int(row["quantity"]),
                status=str(row["status"]),
                client_order_id=row["client_order_id"],
                order_intent_id=row["order_intent_id"],
                broker_order_id=row["broker_order_id"],
                order_type=str(request.get("order_type") or request.get("type") or "LIMIT").upper(),
                limit_price=_optional_float(request.get("limit_price") or request.get("limitPrice")),
                stop_price=_optional_float(request.get("stop_price") or request.get("stopPrice")),
                submitted_at=_parse_dt(row["timestamp"]),
                exit_owner=WCA_ALGORITHM_ID,
                local_order_id=row["broker_order_id"],
                remaining_quantity=int(row["quantity"]),
                target_price=_optional_float(request.get("target_price") or request.get("targetPrice")),
                created_at=_parse_dt(row["created_at"]),
                updated_at=_parse_dt(row["created_at"]),
                decision_id=row["decision_id"],
                idempotency_key=row["idempotency_key"],
            )
        )
    return tuple(orders)


def _lot_from_repository(row: Mapping[str, Any]) -> WcaLocalPaperLotSnapshot:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return WcaLocalPaperLotSnapshot(
        lot_id=str(row.get("lot_id") or ""),
        algorithm_id=WCA_ALGORITHM_ID,
        account_id=str(row.get("account_id") or ""),
        symbol=str(row.get("symbol") or "").upper(),
        side=_side_value(row.get("side") or WcaSide.BUY),
        quantity=int(row.get("quantity") or 0),
        entry_price=float(row.get("entry_price") or payload.get("entry_price") or 0.01),
        remaining_quantity=int(row.get("remaining_quantity") or row.get("quantity") or 0),
        entry_timestamp=_parse_dt(row.get("entry_timestamp") or row.get("opened_at")),
        opened_at=_parse_dt(row.get("opened_at") or row.get("entry_timestamp")),
        decision_id=str(row.get("decision_id") or payload.get("decision_id") or "") or None,
        order_intent_id=str(payload.get("order_intent_id") or row.get("order_intent_id") or "") or None,
        stop_price=_optional_float(row.get("stop_price") or payload.get("stop_price")),
        target_price=_optional_float(row.get("target_price") or payload.get("target_price")),
    )


def _lot_from_projection(*, account_id: str, symbol: str, projection: Any) -> WcaLocalPaperLotSnapshot:
    return WcaLocalPaperLotSnapshot(
        lot_id=f"wca-local-paper-projection-lot-{account_id}-{symbol}",
        algorithm_id=WCA_ALGORITHM_ID,
        account_id=account_id,
        symbol=symbol,
        side=WcaSide.BUY.value,
        quantity=int(projection.open_quantity or 0),
        entry_price=max(0.01, float(projection.average_entry_price or 0.01)),
        remaining_quantity=int(projection.open_quantity or 0),
        entry_timestamp=_parse_dt(projection.last_event_timestamp),
        opened_at=_parse_dt(projection.last_event_timestamp),
        decision_id=str(projection.decision_id or "") or None,
    )


def _restored_starting_balance_from_ledger(
    repository: WcaSqliteRepository,
    *,
    account_id: str,
    symbol: str,
    fallback: float,
) -> float:
    with repository.connect() as conn:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM wca_inventory_ledger
            WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?
              AND event_type = 'DAILY_STATE_RESET'
            ORDER BY event_timestamp DESC, inventory_event_id DESC
            """,
            (WCA_ALGORITHM_ID, account_id, symbol),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
            persisted = float(payload.get("starting_balance") or 0.0)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if persisted > 0:
            return persisted
    return float(fallback)


def _account_from_local_inventory(
    cls: type[WcaLocalPaperAccount],
    inventory: Mapping[str, Any],
    *,
    account_id: str,
    symbol: str,
    starting_balance: float,
    session_date: date,
) -> WcaLocalPaperAccount:
    payload = inventory.get("account_snapshot") if isinstance(inventory.get("account_snapshot"), Mapping) else {}
    restored_starting_balance = float(payload.get("starting_balance") or starting_balance)
    lots = tuple(_lot_from_local_inventory(row) for row in inventory.get("lots", ()))
    orders = tuple(_order_from_local_inventory(row) for row in inventory.get("orders", ()))
    fills = tuple(_fill_from_local_inventory(row) for row in inventory.get("fills", ()))
    cash_default = max(0.0, restored_starting_balance - _gross_exposure_from_lots(lots))
    account = cls(
        account_id=account_id,
        starting_balance=restored_starting_balance,
        session_date=_coerce_date(payload.get("session_date") or session_date),
        cash=float(payload.get("cash", cash_default) or 0.0),
        realized_pnl=float(payload.get("realized_pnl", 0.0) or 0.0),
        daily_realized_pnl=float(payload.get("daily_realized_pnl", 0.0) or 0.0),
        reserved_risk=float(payload.get("reserved_risk", 0.0) or 0.0),
        trades_today=int(payload.get("trades_today", 0) or 0),
        lots=lots,
        open_orders=orders,
        fills=fills,
        circuit_breaker_state=str(payload.get("circuit_breaker_state") or "closed"),
        cooldown_until=payload.get("cooldown_until"),
        last_mark_timestamp=payload.get("last_mark_timestamp"),
    )
    for row in inventory.get("positions", ()):
        row_payload = _payload_snapshot(row)
        mark_price = _optional_float(row_payload.get("mark_price")) or _optional_float(row.get("average_entry_price"))
        if mark_price:
            account._marks[str(row.get("symbol") or symbol).upper()] = mark_price
    return account


def _payload_snapshot(row: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        payload = json.loads(str(row.get("payload_json") or "{}"))
    except json.JSONDecodeError:
        return {}
    snapshot = payload.get("snapshot") if isinstance(payload, Mapping) else {}
    return snapshot if isinstance(snapshot, Mapping) else {}


def _lot_from_local_inventory(row: Mapping[str, Any]) -> WcaLocalPaperLotSnapshot:
    snapshot = _payload_snapshot(row)
    quantity = int(row.get("remaining_quantity") or row.get("quantity") or 0)
    return WcaLocalPaperLotSnapshot(
        lot_id=str(row.get("lot_id") or ""),
        algorithm_id=str(row.get("algorithm_id") or WCA_ALGORITHM_ID),
        account_id=str(row.get("local_account_id") or ""),
        symbol=str(row.get("symbol") or "").upper(),
        side=_side_value(row.get("side") or WcaSide.BUY),
        quantity=quantity,
        entry_price=float(row.get("entry_price") or 0.0),
        remaining_quantity=quantity,
        entry_timestamp=_parse_dt(row.get("entry_timestamp")),
        opened_at=_parse_dt(row.get("entry_timestamp")),
        decision_id=str(row.get("decision_id") or "") or None,
        order_intent_id=str(row.get("order_intent_id") or "") or None,
        stop_price=_optional_float(snapshot.get("stop_price")),
        target_price=_optional_float(snapshot.get("target_price")),
    )


def _order_from_local_inventory(row: Mapping[str, Any]) -> WcaLocalPaperOrderSnapshot:
    snapshot = _payload_snapshot(row)
    client_order_id = str(row.get("client_order_id") or "")
    return WcaLocalPaperOrderSnapshot(
        algorithm_id=str(row.get("algorithm_id") or WCA_ALGORITHM_ID),
        account_id=str(row.get("local_account_id") or ""),
        symbol=str(row.get("symbol") or "").upper(),
        side=_side_value(row.get("side") or WcaSide.BUY),
        quantity=int(row.get("quantity") or 0),
        status=str(row.get("status") or ""),
        client_order_id=client_order_id,
        order_intent_id=str(snapshot.get("order_intent_id") or row.get("decision_id") or "") or None,
        order_type=str(row.get("order_type") or "LIMIT").upper(),
        limit_price=_optional_float(row.get("limit_price")),
        stop_price=_optional_float(row.get("stop_price")),
        submitted_at=_parse_dt(row.get("created_at")),
        local_order_id=str(row.get("local_order_id") or "") or None,
        remaining_quantity=int(row.get("remaining_quantity") or row.get("quantity") or 0),
        target_price=_optional_float(row.get("target_price")),
        created_at=_parse_dt(row.get("created_at")),
        updated_at=_parse_dt(row.get("updated_at")),
        decision_id=str(row.get("decision_id") or "") or None,
        idempotency_key=str(row.get("idempotency_key") or "") or None,
        exit_owner=WCA_ALGORITHM_ID if client_order_id.startswith("wca-protection-") else None,
    )


def _fill_from_local_inventory(row: Mapping[str, Any]) -> WcaLocalPaperFillSnapshot:
    return WcaLocalPaperFillSnapshot(
        fill_id=str(row.get("fill_id") or ""),
        algorithm_id=str(row.get("algorithm_id") or WCA_ALGORITHM_ID),
        account_id=str(row.get("local_account_id") or ""),
        order_id=str(row.get("order_id") or ""),
        symbol=str(row.get("symbol") or "").upper(),
        side=_side_value(row.get("side") or WcaSide.BUY),
        quantity=int(row.get("quantity") or 0),
        fill_price=float(row.get("fill_price") or 0.0),
        commissions=float(row.get("commissions") or 0.0),
        fees=float(row.get("fees") or 0.0),
        slippage=float(row.get("slippage") or 0.0),
        timestamp=_parse_dt(row.get("timestamp")) or _utc_now(),
    )


def _broker_position(position: WcaLocalPaperPositionSnapshot) -> BrokerPositionState:
    first_lot = position.lots[0] if position.lots else None
    return BrokerPositionState(
        algorithmId=WCA_ALGORITHM_ID,
        capitalPartitionId=WCA_LOCAL_PAPER_CAPITAL_PARTITION_ID,
        decisionId=first_lot.decision_id if first_lot is not None else None,
        orderIntentId=first_lot.order_intent_id if first_lot is not None else None,
        positionOwner=WCA_ALGORITHM_ID,
        symbol=position.symbol,
        side=Signal.BUY if position.side == WcaSide.BUY.value else Signal.SELL,
        quantity=position.quantity,
        averageEntryPrice=position.average_entry_price,
        markPrice=position.mark_price,
        stopPrice=position.stop_price,
        realizedPnlToday=0.0,
        openedAt=position.opened_at,
    )


def _broker_order(order: WcaLocalPaperOrderSnapshot) -> BrokerOrderState:
    return BrokerOrderState(
        algorithmId=WCA_ALGORITHM_ID,
        capitalPartitionId=WCA_LOCAL_PAPER_CAPITAL_PARTITION_ID,
        orderIntentId=order.order_intent_id,
        positionOwner=WCA_ALGORITHM_ID,
        exitOwner=order.exit_owner,
        symbol=order.symbol,
        side=Signal.BUY if order.side == WcaSide.BUY.value else Signal.SELL,
        clientOrderId=order.client_order_id,
        orderType=order.order_type,
        status=_broker_order_status(order.status),
        quantity=order.quantity,
        filledQuantity=0,
        entryPrice=order.limit_price or 0.01,
        stopPrice=order.stop_price,
        submittedAt=order.submitted_at or _utc_now(),
    )

def _broker_order_status(status: str) -> str:
    normalized = str(status or "").upper()
    if normalized in {"PARTIALLY_FILLED", WcaOrderStatus.PARTIALLY_FILLED.value}:
        return "PARTIALLY_FILLED"
    if normalized in {"PENDING", WcaOrderStatus.OUTBOX_RESERVED.value, WcaOrderStatus.SUBMITTED.value}:
        return "PENDING"
    if normalized in {"NEW"}:
        return "NEW"
    return "ACCEPTED"

def _copy_lot(lot: WcaLocalPaperLotSnapshot, *, account_id: str) -> WcaLocalPaperLotSnapshot:
    if lot.algorithm_id != WCA_ALGORITHM_ID or lot.account_id != account_id:
        raise ValueError("WCA local paper account cannot load non-WCA lots")
    return replace(lot, symbol=lot.symbol.upper())


def _copy_order(order: WcaLocalPaperOrderSnapshot, *, account_id: str) -> WcaLocalPaperOrderSnapshot:
    if order.algorithm_id != WCA_ALGORITHM_ID or order.account_id != account_id or order.position_owner != WCA_ALGORITHM_ID:
        raise ValueError("WCA local paper account cannot load non-WCA orders")
    return replace(order, symbol=order.symbol.upper())


def _copy_fill(fill: WcaLocalPaperFillSnapshot, *, account_id: str) -> WcaLocalPaperFillSnapshot:
    if fill.algorithm_id != WCA_ALGORITHM_ID or fill.account_id != account_id:
        raise ValueError("WCA local paper account cannot load non-WCA fills")
    return replace(fill, symbol=fill.symbol.upper())


def _gross_exposure_from_lots(lots: tuple[WcaLocalPaperLotSnapshot, ...]) -> float:
    return round(sum(lot.quantity * lot.entry_price for lot in lots), 10)


def _signed_quantity(side: str, quantity: int) -> int:
    return quantity if side == WcaSide.BUY.value else -quantity


def _realized_pnl(side: str, entry_price: float, exit_price: float, quantity: int) -> float:
    if side == WcaSide.BUY.value:
        return round((exit_price - entry_price) * quantity, 10)
    return round((entry_price - exit_price) * quantity, 10)


def _positive_amount(value: float, label: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise ValueError(f"WCA local paper {label} must be positive")
    return parsed


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _order_status_value(status: Any) -> str:
    return status.value if hasattr(status, "value") else str(status or "")


def _side_value(side: WcaSide | str | Any) -> str:
    return side.value if isinstance(side, WcaSide) else str(side).upper()


def _coerce_date(value: date | str | None) -> date:
    if value is None:
        return _utc_now().date()
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _reuses_generic_alpaca_credentials(source: Mapping[str, str], *, key_id: str, secret: str) -> bool:
    generic_key = _clean(source.get(_GENERIC_ALPACA_KEY_ID))
    generic_secret = _clean(source.get(_GENERIC_ALPACA_SECRET_KEY))
    return bool((key_id and generic_key and key_id == generic_key) or (secret and generic_secret and secret == generic_secret))


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def _env_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_float(value: str | None, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        return float(str(value).strip())
    except ValueError:
        return default

def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


__all__ = [
    "WCA_ALPACA_PAPER_ACCOUNT_ID",
    "WCA_ALPACA_PAPER_API_KEY_ID",
    "WCA_ALPACA_PAPER_API_SECRET_KEY",
    "WCA_ALPACA_PAPER_BASE_URL",
    "WCA_AUTOMATIC_PAPER_ENABLED",
    "WCA_DEFAULT_LOCAL_PAPER_STARTING_BALANCE",
    "WCA_LOCAL_PAPER_ACCOUNT_ID",
    "WCA_LOCAL_PAPER_ACCOUNT_VERSION",
    "WCA_LOCAL_PAPER_CAPITAL_PARTITION_ID",
    "WCA_LOCAL_PAPER_SOURCE_AUTHORITY",
    "WCA_LOCAL_PAPER_STARTING_BALANCE",
    "WcaLocalPaperAccount",
    "WcaLocalPaperAccountValidation",
    "WcaLocalPaperAccountSnapshot",
    "WcaLocalPaperFillSnapshot",
    "WcaLocalPaperLotSnapshot",
    "WcaLocalPaperOrderSnapshot",
    "WcaLocalPaperPositionSnapshot",
    "WcaPaperAccountValidation",
    "validate_wca_automatic_paper_account",
    "validate_wca_local_paper_account",
]
