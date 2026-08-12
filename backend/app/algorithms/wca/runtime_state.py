"""Authoritative WCA runtime-state loader."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from dataclasses import asdict
from pydantic import Field

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaContractModel, WcaRuntimeMode, WcaSide, coerce_wca_runtime_mode
from backend.app.algorithms.wca.exits import WcaBacktestOpenPosition
from backend.app.algorithms.wca.local_paper_account import WCA_DEFAULT_LOCAL_PAPER_STARTING_BALANCE, WCA_LOCAL_PAPER_SOURCE_AUTHORITY, WcaLocalPaperAccount, WcaLocalPaperAccountSnapshot, WcaLocalPaperLotSnapshot, WcaLocalPaperOrderSnapshot, WcaLocalPaperPositionSnapshot
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.domain.models import Signal
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState, aggregate_global_account_risk


WCA_AUTHORITATIVE_RUNTIME_STATE_VERSION = "wca_authoritative_runtime_state_v1"


class WcaAuthoritativeRuntimeState(WcaContractModel):
    algorithm_id: str = WCA_ALGORITHM_ID
    state_version: str
    state_hash: str = ""
    broker_account: dict[str, Any]
    local_account: dict[str, Any] = Field(default_factory=dict)
    inventory: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)
    orders: dict[str, Any] = Field(default_factory=dict)
    broker_account_id: str
    symbol: str
    wca_inventory: dict[str, Any]
    current_position_direction: str | None
    current_quantity: int | None = Field(default=None, ge=0)
    available_quantity: int | None = Field(default=None, ge=0)
    average_entry_price: float | None = Field(default=None, ge=0)
    open_lots: tuple[dict[str, Any], ...] = ()
    position_entry_timestamp: datetime | None = None
    original_decision_id: str | None = None
    entry_configuration_version: str | None = None
    position_stop_price: float | None = Field(default=None, gt=0)
    position_target_price: float | None = Field(default=None, gt=0)
    position_unprotected: bool = False
    position_inconsistent: bool = False
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    pending_entry_orders: tuple[dict[str, Any], ...] = ()
    pending_exit_orders: tuple[dict[str, Any], ...] = ()
    partially_filled_orders: tuple[dict[str, Any], ...] = ()
    protective_orders: tuple[dict[str, Any], ...] = ()
    reserved_risk: float | None = Field(default=None, ge=0)
    daily_trade_count: int | None = Field(default=None, ge=0)
    daily_loss: float | None = Field(default=None, ge=0)
    cooldown_state: dict[str, Any]
    circuit_breaker_state: str | None
    equity: float | None = Field(default=None, ge=0)
    buying_power: float | None = Field(default=None, ge=0)
    cash: float | None = Field(default=None, ge=0)
    current_broker_positions: tuple[dict[str, Any], ...] = ()
    pending_broker_orders: tuple[dict[str, Any], ...] = ()
    partial_fills: tuple[dict[str, Any], ...] = ()
    account_status: str | None = None
    pattern_day_trading_restrictions: str | None = None
    trading_restrictions: tuple[str, ...] = ()
    broker_observation_timestamp: datetime | None = None
    broker_source_authority: str | None = None
    global_risk: dict[str, Any]
    account_wide_entry_permission: bool
    account_wide_exit_permission: bool
    maximum_approved_quantity: int
    remaining_portfolio_risk: float | None = Field(default=None, ge=0)
    current_aggregate_exposure: float | None = Field(default=None, ge=0)
    concentration_restrictions: tuple[str, ...] = ()
    global_circuit_breaker_status: str
    global_risk_decision_id: str
    global_risk_expiration: datetime
    wca_configuration_version: str
    dynamic_profile_version: str
    weight_snapshot_version: str
    calibration_version: str
    inventory_state_version: str
    reconciliation_watermark: str | None = None
    state_timestamp: datetime
    maximum_permitted_state_age_seconds: int
    freshness_result: str
    reason_codes: tuple[str, ...] = ()

    @property
    def fresh(self) -> bool:
        return self.freshness_result == "PASS"

    def to_open_position(self) -> WcaBacktestOpenPosition | None:
        if not self.fresh or not self.current_quantity or self.current_quantity <= 0:
            return None
        if self.position_inconsistent or self.position_unprotected:
            return None
        if (
            self.average_entry_price is None
            or self.average_entry_price <= 0
            or self.position_stop_price is None
            or self.position_target_price is None
            or self.position_entry_timestamp is None
        ):
            return None
        side = WcaSide.BUY if self.current_position_direction == WcaSide.BUY.value else WcaSide.SELL
        return WcaBacktestOpenPosition(
            trade_id=f"wca-runtime-position-{self.broker_account_id}-{self.symbol}-{self.inventory_state_version[:12]}",
            decision_id=self.original_decision_id or self.global_risk_decision_id,
            symbol=self.symbol,
            side=side,
            quantity=self.current_quantity,
            entry_at=self.position_entry_timestamp,
            entry_price=self.average_entry_price,
            stop_price=self.position_stop_price,
            target_price=self.position_target_price,
        )


def load_wca_authoritative_runtime_state(
    repository: WcaSqliteRepository,
    *,
    broker_account_id: str,
    symbol: str,
    state_timestamp: datetime,
    maximum_permitted_state_age_seconds: int,
    runtime_mode: WcaRuntimeMode | str | None = None,
    market_data: dict[str, Any] | None = None,
) -> WcaAuthoritativeRuntimeState:
    timestamp = state_timestamp.astimezone(timezone.utc)
    local_runtime_mode = runtime_mode is not None and coerce_wca_runtime_mode(runtime_mode) == WcaRuntimeMode.LOCAL_AUTOMATIC_PAPER
    reason_codes: list[str] = [WCA_AUTHORITATIVE_RUNTIME_STATE_VERSION]
    with repository.connect() as conn:
        conn.execute("BEGIN")
        inventory = conn.execute(
            """
            SELECT *
            FROM wca_inventory_projection
            WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?
            """,
            (WCA_ALGORITHM_ID, broker_account_id, symbol),
        ).fetchone()
        daily = conn.execute(
            """
            SELECT *
            FROM wca_daily_state
            WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ? AND session_date = ?
            """,
            (WCA_ALGORITHM_ID, broker_account_id, symbol, timestamp.date().isoformat()),
        ).fetchone()
        broker = conn.execute(
            """
            SELECT *
            FROM wca_broker_account_snapshots
            WHERE algorithm_id = ? AND broker_account_id = ?
            ORDER BY observed_at DESC, created_at DESC
            LIMIT 1
            """,
            (WCA_ALGORITHM_ID, broker_account_id),
        ).fetchone()
        lot_rows = conn.execute(
            """
            SELECT lot_id, account_id, symbol, timestamp, configuration_version, engine_version,
                   market_snapshot_id, decision_id, run_id, position_id, side, quantity, payload_json
            FROM wca_owned_lots
            WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND status = 'open' AND quantity > 0
            ORDER BY created_at, lot_id
            """,
            (WCA_ALGORITHM_ID, broker_account_id, symbol),
        ).fetchall()
        pending_rows = conn.execute(
            """
            SELECT status, client_order_id, order_intent_id, payload_json
            FROM wca_execution_outbox
            WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
              AND status NOT IN ('FILLED', 'REJECTED', 'CANCELLED')
            ORDER BY created_at
            """,
            (WCA_ALGORITHM_ID, broker_account_id, symbol),
        ).fetchall()
        protective_rows = conn.execute(
            """
            SELECT event_timestamp, order_intent_id, client_order_id, broker_order_id, quantity, payload_json
            FROM wca_inventory_ledger
            WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?
              AND event_type IN ('PROTECTIVE_ORDER_CREATED', 'PROTECTIVE_ORDER_REPLACED')
            ORDER BY event_timestamp DESC, inventory_event_id DESC
            """,
            (WCA_ALGORITHM_ID, broker_account_id, symbol),
        ).fetchall()
        partial_fill_rows = conn.execute(
            """
            SELECT payload_json
            FROM wca_inventory_ledger
            WHERE algorithm_id = ? AND broker_account_id = ? AND symbol = ?
              AND event_type = 'PARTIAL_FILL_RECEIVED'
            ORDER BY event_timestamp DESC, inventory_event_id DESC
            """,
            (WCA_ALGORITHM_ID, broker_account_id, symbol),
        ).fetchall()
        config_row = conn.execute(
            "SELECT configuration_version, payload_json FROM wca_active_configuration WHERE algorithm_id = ?",
            (WCA_ALGORITHM_ID,),
        ).fetchone()
        weight_row = conn.execute(
            "SELECT weight_version FROM wca_weight_snapshots WHERE algorithm_id = ? ORDER BY timestamp DESC, created_at DESC LIMIT 1",
            (WCA_ALGORITHM_ID,),
        ).fetchone()
        calibration_rows = conn.execute(
            "SELECT calibration_version FROM wca_confidence_calibrations WHERE algorithm_id = ? AND symbol = ? ORDER BY timestamp DESC, created_at DESC",
            (WCA_ALGORITHM_ID, symbol),
        ).fetchall()
        profile_row = conn.execute(
            "SELECT profile_id, payload_json FROM wca_effective_setting_snapshots WHERE algorithm_id = ? AND symbol = ? ORDER BY timestamp DESC, created_at DESC LIMIT 1",
            (WCA_ALGORITHM_ID, symbol),
        ).fetchone()
        reconciliation_row = conn.execute(
            """
            SELECT reconciliation_id, timestamp, hard_operational_warning, discrepancy_count
            FROM wca_broker_reconciliations
            WHERE algorithm_id = ? AND account_id = ? AND symbol = ?
            ORDER BY timestamp DESC, created_at DESC
            LIMIT 1
            """,
            (WCA_ALGORITHM_ID, broker_account_id, symbol),
        ).fetchone()

    if local_runtime_mode:
        return _load_wca_local_authoritative_runtime_state(
            repository,
            broker_account_id=broker_account_id,
            symbol=symbol,
            timestamp=timestamp,
            maximum_permitted_state_age_seconds=maximum_permitted_state_age_seconds,
            market_data=market_data,
            reason_codes=reason_codes,
            config_row=config_row,
            weight_row=weight_row,
            calibration_rows=calibration_rows,
            profile_row=profile_row,
            reconciliation_row=reconciliation_row,
        )

    if inventory is None:
        reason_codes.append("wca.runtime_state.inventory_missing")
    if daily is None:
        reason_codes.append("wca.runtime_state.daily_state_missing")
    if broker is None:
        reason_codes.append("wca.runtime_state.broker_snapshot_missing")
    if config_row is None:
        reason_codes.append("wca.runtime_state.configuration_missing")
    if weight_row is None:
        reason_codes.append("wca.runtime_state.weights_missing")

    broker_payload = json.loads(broker["payload_json"] or "{}") if broker is not None else {}
    broker_snapshot = BrokerAccountSnapshot.model_validate(broker_payload["snapshot"]) if broker is not None else None
    broker_observed_at = broker_snapshot.observedAt.astimezone(timezone.utc) if broker_snapshot is not None else None
    if broker_observed_at is not None and (timestamp - broker_observed_at).total_seconds() > maximum_permitted_state_age_seconds:
        reason_codes.append("wca.runtime_state.broker_snapshot_stale")
    if inventory is not None and inventory["last_event_timestamp"]:
        inventory_timestamp = _parse_dt(inventory["last_event_timestamp"])
        if (timestamp - inventory_timestamp).total_seconds() > maximum_permitted_state_age_seconds:
            reason_codes.append("wca.runtime_state.inventory_stale")
    if inventory is not None and not inventory["reconciliation_watermark"] and reconciliation_row is None:
        reason_codes.append("wca.runtime_state.reconciliation_watermark_missing")
    if reconciliation_row is not None and (int(reconciliation_row["hard_operational_warning"]) or int(reconciliation_row["discrepancy_count"]) > 0):
        reason_codes.append("wca.runtime_state.reconciliation_blocks_entries")
    if broker_snapshot is not None and broker_snapshot.sourceAuthority not in {"broker", WCA_LOCAL_PAPER_SOURCE_AUTHORITY}:
        reason_codes.append("wca.runtime_state.account_source_not_authoritative")
    if broker_snapshot is not None and (not broker_snapshot.positionsReconciled or not broker_snapshot.openOrdersReconciled):
        reason_codes.append("wca.runtime_state.broker_not_reconciled")

    open_quantity = int(inventory["open_quantity"]) if inventory is not None else None
    open_lots = tuple(_open_lot(row) for row in lot_rows)
    position_direction = _position_direction(open_lots, open_quantity)
    available_quantity = open_quantity
    pending_entry_orders = tuple(_pending_order(row) for row in pending_rows if _is_entry_order(row, position_direction))
    pending_exit_orders = tuple(_pending_order(row) for row in pending_rows if not _is_entry_order(row, position_direction))
    protective_orders = tuple(_protective_order(row) for row in protective_rows)
    partial_fills = tuple(json.loads(row["payload_json"] or "{}") for row in partial_fill_rows)
    position_details = _position_details(
        open_lots=open_lots,
        protective_orders=protective_orders,
        open_quantity=open_quantity,
        position_direction=position_direction,
    )
    reason_codes.extend(position_details["reason_codes"])
    if broker_snapshot is not None:
        broker_isolation_reasons = _broker_snapshot_isolation_reason_codes(broker_snapshot, broker_account_id=broker_account_id, symbol=symbol)
        reason_codes.extend(broker_isolation_reasons)
    daily_loss = float(daily["daily_loss"]) if daily is not None else None
    daily_realized_pnl = float(daily["realized_pnl_today"]) if daily is not None else 0.0
    wca_broker_snapshot = _wca_only_broker_snapshot(broker_snapshot, local_realized_pnl_today=daily_realized_pnl) if broker_snapshot is not None else None
    broker_positions = tuple(position.model_dump(mode="json") for position in wca_broker_snapshot.positions) if wca_broker_snapshot is not None else ()
    pending_broker_orders = tuple(order.model_dump(mode="json") for order in wca_broker_snapshot.pendingOrders) if wca_broker_snapshot is not None else ()
    broker_partial_orders = tuple(order.model_dump(mode="json") for order in wca_broker_snapshot.partiallyFilledOrders) if wca_broker_snapshot is not None else ()
    global_snapshot = aggregate_global_account_risk(wca_broker_snapshot, candidateSymbol=symbol, candidateSide=position_direction) if wca_broker_snapshot is not None else None
    global_risk = global_snapshot.model_dump(mode="json") if global_snapshot is not None else {}
    broker_state = global_snapshot.brokerState if global_snapshot is not None else {}
    risk_state = global_snapshot.riskState if global_snapshot is not None else {}
    account_entry_permission = bool(
        broker_snapshot is not None
        and broker_state.get("brokerConnected")
        and broker_state.get("accountNotRestricted")
        and broker_state.get("buyingPowerCurrent")
        and broker_state.get("positionsReconciled")
        and broker_state.get("openOrdersReconciled")
        and not broker_payload.get("trading_restrictions")
        and not broker_payload.get("pattern_day_trading_restrictions")
    )
    account_exit_permission = broker_snapshot is not None and bool(broker_state.get("brokerConnected", False))
    if not account_entry_permission:
        reason_codes.append("wca.runtime_state.account_entry_not_permitted")
    if not account_exit_permission:
        reason_codes.append("wca.runtime_state.account_exit_authority_missing")

    remaining_portfolio_risk = None
    if broker_snapshot is not None:
        remaining_portfolio_risk = max(0.0, wca_broker_snapshot.equity - float(risk_state.get("totalOpenRiskDollars", 0.0))) if wca_broker_snapshot is not None else None
    inventory_payload = _row_payload(inventory)
    inventory_state_version = _stable_hash(
        {
            "inventory": inventory_payload,
            "daily": _row_payload(daily),
            "reconciliation": _row_payload(reconciliation_row),
        }
    )
    state_seed = {
        "broker_account_id": broker_account_id,
        "symbol": symbol,
        "inventory_state_version": inventory_state_version,
        "broker_snapshot_id": broker["broker_snapshot_id"] if broker is not None else "",
        "state_timestamp": timestamp.isoformat(),
        "reason_codes": reason_codes,
    }
    state_hash = _stable_hash(state_seed)
    freshness_result = "FAIL" if any(code.startswith("wca.runtime_state.") and code not in {"wca.runtime_state.account_entry_not_permitted", "wca.runtime_state.local_paper_authority"} for code in reason_codes[1:]) else "PASS"
    if freshness_result == "PASS":
        reason_codes.append("wca.runtime_state.fresh")

    state = WcaAuthoritativeRuntimeState(
        state_version=WCA_AUTHORITATIVE_RUNTIME_STATE_VERSION,
        state_hash=state_hash,
        broker_account=broker_payload,
        local_account={},
        inventory=_inventory_section(
            current_position=None,
            quantity=open_quantity,
            average_entry=float(inventory["average_entry_price"]) if inventory is not None else None,
            open_lots=open_lots,
            realized_pnl=float(inventory["realized_pnl"]) if inventory is not None else None,
            unrealized_pnl=float(inventory["unrealized_pnl"]) if inventory is not None else None,
            stop=position_details["stop_price"],
            target=position_details["target_price"],
        ),
        risk=_risk_section(
            reserved_risk=float(inventory["reserved_risk"]) if inventory is not None else None,
            daily_loss=daily_loss,
            trades_today=int(daily["trades_completed_today"]) if daily is not None else None,
            circuit_breaker=daily["circuit_breaker_state"] if daily is not None else None,
            cooldown_until=daily["cooldown_until"] if daily is not None else None,
            timestamp=timestamp,
        ),
        orders=_orders_section(
            pending_entries=pending_entry_orders,
            pending_exits=pending_exit_orders,
            protective=protective_orders,
            partial=broker_partial_orders or partial_fills,
        ),
        broker_account_id=broker_account_id,
        symbol=symbol,
        wca_inventory=inventory_payload,
        current_position_direction=position_direction,
        current_quantity=open_quantity,
        available_quantity=available_quantity,
        average_entry_price=float(inventory["average_entry_price"]) if inventory is not None else None,
        open_lots=open_lots,
        position_entry_timestamp=position_details["entry_timestamp"],
        original_decision_id=position_details["decision_id"],
        entry_configuration_version=position_details["configuration_version"],
        position_stop_price=position_details["stop_price"],
        position_target_price=position_details["target_price"],
        position_unprotected=bool(position_details["unprotected"]),
        position_inconsistent=bool(position_details["inconsistent"]),
        realized_pnl=float(inventory["realized_pnl"]) if inventory is not None else None,
        unrealized_pnl=float(inventory["unrealized_pnl"]) if inventory is not None else None,
        pending_entry_orders=pending_entry_orders,
        pending_exit_orders=pending_exit_orders,
        partially_filled_orders=broker_partial_orders,
        protective_orders=protective_orders,
        reserved_risk=float(inventory["reserved_risk"]) if inventory is not None else None,
        daily_trade_count=int(daily["trades_completed_today"]) if daily is not None else None,
        daily_loss=daily_loss,
        cooldown_state={"cooldown_until": daily["cooldown_until"], "active": bool(daily["cooldown_until"] and timestamp < _parse_dt(daily["cooldown_until"]))} if daily is not None else {},
        circuit_breaker_state=daily["circuit_breaker_state"] if daily is not None else None,
        equity=float(wca_broker_snapshot.equity) if wca_broker_snapshot is not None else None,
        buying_power=float(wca_broker_snapshot.buyingPower) if wca_broker_snapshot is not None else None,
        cash=float(broker_payload.get("cash")) if broker_payload.get("cash") is not None else None,
        current_broker_positions=broker_positions,
        pending_broker_orders=pending_broker_orders,
        partial_fills=partial_fills,
        account_status=broker_payload.get("account_status") if broker is not None else None,
        pattern_day_trading_restrictions=broker_payload.get("pattern_day_trading_restrictions") if broker is not None else None,
        trading_restrictions=tuple(broker_payload.get("trading_restrictions") or ()),
        broker_observation_timestamp=broker_observed_at,
        broker_source_authority=broker_snapshot.sourceAuthority if broker_snapshot is not None else None,
        global_risk=global_risk,
        account_wide_entry_permission=account_entry_permission and freshness_result == "PASS",
        account_wide_exit_permission=account_exit_permission,
        maximum_approved_quantity=2_147_483_647 if account_entry_permission and freshness_result == "PASS" else 0,
        remaining_portfolio_risk=remaining_portfolio_risk,
        current_aggregate_exposure=float(risk_state.get("positionNotionalDollars", 0.0)) if wca_broker_snapshot is not None else None,
        concentration_restrictions=tuple(code for code in global_snapshot.reasonCodes if "exposure" in code) if global_snapshot is not None else (),
        global_circuit_breaker_status="closed" if freshness_result == "PASS" and account_entry_permission else "entries_blocked",
        global_risk_decision_id=f"wca-runtime-risk-{state_hash[:16]}",
        global_risk_expiration=timestamp + timedelta(seconds=maximum_permitted_state_age_seconds),
        wca_configuration_version=config_row["configuration_version"] if config_row is not None else "",
        dynamic_profile_version=_dynamic_profile_version(profile_row),
        weight_snapshot_version=weight_row["weight_version"] if weight_row is not None else "",
        calibration_version=",".join(row["calibration_version"] for row in calibration_rows) if calibration_rows else "wca.calibration.none_recorded",
        inventory_state_version=inventory_state_version,
        reconciliation_watermark=inventory["reconciliation_watermark"] if inventory is not None else None,
        state_timestamp=timestamp,
        maximum_permitted_state_age_seconds=maximum_permitted_state_age_seconds,
        freshness_result=freshness_result,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
    )
    return state


def _load_wca_local_authoritative_runtime_state(
    repository: WcaSqliteRepository,
    *,
    broker_account_id: str,
    symbol: str,
    timestamp: datetime,
    maximum_permitted_state_age_seconds: int,
    market_data: dict[str, Any] | None,
    reason_codes: list[str],
    config_row: sqlite3.Row | None,
    weight_row: sqlite3.Row | None,
    calibration_rows: list[sqlite3.Row],
    profile_row: sqlite3.Row | None,
    reconciliation_row: sqlite3.Row | None,
) -> WcaAuthoritativeRuntimeState:
    selected_symbol = symbol.upper()
    reason_codes.append("wca.runtime_state.local_paper_authority")
    local_inventory_snapshot = repository.read_wca_local_inventory_snapshot(local_account_id=broker_account_id, symbol=selected_symbol)
    if local_inventory_snapshot is None:
        reason_codes.append("wca.runtime_state.local_account_missing")
    account = WcaLocalPaperAccount.restore(
        repository,
        account_id=broker_account_id,
        symbol=selected_symbol,
        starting_balance=_local_paper_starting_balance_from_config(config_row),
        session_date=timestamp.date(),
    )
    mark_price = _mark_price_from_market_data(market_data)
    if mark_price is not None:
        account.mark_to_market(symbol=selected_symbol, mark_price=mark_price, marked_at=timestamp)
    snapshot = account.get_account_snapshot()
    local_account = _local_account_section(snapshot)
    open_lots = tuple(_local_lot_payload(lot) for lot in snapshot.lots if lot.symbol == selected_symbol and int(lot.remaining_quantity or lot.quantity or 0) > 0)
    current_position = next((position for position in snapshot.positions if position.symbol == selected_symbol), None)
    current_position_payload = _local_position_payload(current_position) if current_position is not None else None
    open_quantity = int(current_position.quantity) if current_position is not None else sum(int(lot.get("quantity") or 0) for lot in open_lots)
    position_direction = current_position.side if current_position is not None else _position_direction(open_lots, open_quantity)
    average_entry = current_position.average_entry_price if current_position is not None else _weighted_average_entry(open_lots)
    available_quantity = open_quantity
    local_orders = tuple(_local_order_payload(order) for order in snapshot.open_orders if order.symbol == selected_symbol)
    protective_orders = tuple(order for order in local_orders if _is_local_protective_order(order))
    pending_entry_orders = tuple(order for order in local_orders if not _is_local_protective_order(order) and _is_local_entry_order(order, position_direction))
    pending_exit_orders = tuple(order for order in local_orders if order not in pending_entry_orders and not _is_local_protective_order(order))
    partially_filled_orders = tuple(order for order in local_orders if str(order.get("status") or "").upper() == "PARTIALLY_FILLED")
    partial_fills = tuple(_local_fill_payload(fill) for fill in snapshot.fills if fill.symbol == selected_symbol)
    stop = current_position.stop_price if current_position is not None else next((lot.get("stop_price") for lot in open_lots if lot.get("stop_price")), None)
    target = current_position.target_price if current_position is not None else next((lot.get("target_price") for lot in open_lots if lot.get("target_price")), None)
    inventory = _inventory_section(
        current_position=current_position_payload,
        quantity=open_quantity,
        average_entry=average_entry,
        open_lots=open_lots,
        realized_pnl=snapshot.realized_pnl,
        unrealized_pnl=snapshot.unrealized_pnl,
        stop=stop,
        target=target,
    )
    risk = _risk_section(
        reserved_risk=snapshot.reserved_risk,
        daily_loss=snapshot.daily_loss,
        trades_today=snapshot.trades_today,
        circuit_breaker=snapshot.circuit_breaker_state,
        cooldown_until=snapshot.cooldown_until,
        timestamp=timestamp,
    )
    orders = _orders_section(
        pending_entries=pending_entry_orders,
        pending_exits=pending_exit_orders,
        protective=protective_orders,
        partial=partially_filled_orders,
    )
    if config_row is None:
        reason_codes.append("wca.runtime_state.configuration_missing")
    if weight_row is None:
        reason_codes.append("wca.runtime_state.weights_missing")
    if snapshot.last_mark_timestamp is not None and (timestamp - snapshot.last_mark_timestamp.astimezone(timezone.utc)).total_seconds() > maximum_permitted_state_age_seconds:
        reason_codes.append("wca.runtime_state.local_mark_stale")
    cooldown_active = bool(snapshot.cooldown_until is not None and timestamp < snapshot.cooldown_until.astimezone(timezone.utc))
    if cooldown_active:
        reason_codes.append("wca.runtime_state.local_cooldown_active")
    if str(snapshot.circuit_breaker_state or "closed").lower() not in {"", "closed"}:
        reason_codes.append("wca.runtime_state.local_circuit_breaker_open")
    if reconciliation_row is not None and (int(reconciliation_row["hard_operational_warning"]) or int(reconciliation_row["discrepancy_count"]) > 0):
        reason_codes.append("wca.runtime_state.reconciliation_blocks_entries")
    local_broker_snapshot = _broker_snapshot_from_local_account(snapshot, symbol=selected_symbol, timestamp=timestamp)
    global_snapshot = aggregate_global_account_risk(local_broker_snapshot, candidateSymbol=selected_symbol, candidateSide=position_direction)
    global_risk = global_snapshot.model_dump(mode="json")
    broker_state = global_snapshot.brokerState
    risk_state = global_snapshot.riskState
    account_entry_permission = bool(
        local_inventory_snapshot is not None
        and broker_state.get("buyingPowerCurrent")
        and not cooldown_active
        and str(snapshot.circuit_breaker_state or "closed").lower() in {"", "closed"}
    )
    account_exit_permission = local_inventory_snapshot is not None
    if not account_entry_permission:
        reason_codes.append("wca.runtime_state.account_entry_not_permitted")
    if not account_exit_permission:
        reason_codes.append("wca.runtime_state.account_exit_authority_missing")
    inventory_state_version = _stable_hash(
        {
            "local_account": local_account,
            "inventory": inventory,
            "risk": risk,
            "orders": orders,
            "fills": partial_fills,
            "reconciliation": _row_payload(reconciliation_row),
        }
    )
    state_seed = {
        "broker_account_id": broker_account_id,
        "symbol": selected_symbol,
        "inventory_state_version": inventory_state_version,
        "local_account_state_version": snapshot.state_version,
        "state_timestamp": timestamp.isoformat(),
        "reason_codes": reason_codes,
    }
    state_hash = _stable_hash(state_seed)
    freshness_result = "FAIL" if any(code.startswith("wca.runtime_state.") and code not in {"wca.runtime_state.account_entry_not_permitted", "wca.runtime_state.local_paper_authority"} for code in reason_codes[1:]) else "PASS"
    if freshness_result == "PASS":
        reason_codes.append("wca.runtime_state.fresh")
    return WcaAuthoritativeRuntimeState(
        state_version=WCA_AUTHORITATIVE_RUNTIME_STATE_VERSION,
        state_hash=state_hash,
        broker_account={"sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY, "local_account": local_account},
        local_account=local_account,
        inventory=inventory,
        risk=risk,
        orders=orders,
        broker_account_id=broker_account_id,
        symbol=selected_symbol,
        wca_inventory={**inventory, "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY},
        current_position_direction=position_direction,
        current_quantity=open_quantity,
        available_quantity=available_quantity,
        average_entry_price=average_entry,
        open_lots=open_lots,
        position_entry_timestamp=_parse_optional_dt(current_position_payload.get("opened_at") if current_position_payload else None),
        original_decision_id=next((str(lot.get("decision_id")) for lot in open_lots if lot.get("decision_id")), None),
        entry_configuration_version=None,
        position_stop_price=stop,
        position_target_price=target,
        position_unprotected=bool(open_quantity and (stop is None or target is None)),
        position_inconsistent=False,
        realized_pnl=snapshot.realized_pnl,
        unrealized_pnl=snapshot.unrealized_pnl,
        pending_entry_orders=pending_entry_orders,
        pending_exit_orders=pending_exit_orders,
        partially_filled_orders=partially_filled_orders,
        protective_orders=protective_orders,
        reserved_risk=snapshot.reserved_risk,
        daily_trade_count=snapshot.trades_today,
        daily_loss=snapshot.daily_loss,
        cooldown_state=risk["cooldown"],
        circuit_breaker_state=snapshot.circuit_breaker_state,
        equity=snapshot.equity,
        buying_power=snapshot.buying_power,
        cash=snapshot.cash,
        current_broker_positions=tuple(position.model_dump(mode="json") for position in local_broker_snapshot.positions),
        pending_broker_orders=tuple(order.model_dump(mode="json") for order in local_broker_snapshot.pendingOrders),
        partial_fills=partial_fills,
        account_status="LOCAL_PAPER",
        pattern_day_trading_restrictions=None,
        trading_restrictions=(),
        broker_observation_timestamp=snapshot.last_mark_timestamp or timestamp,
        broker_source_authority=WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
        global_risk=global_risk,
        account_wide_entry_permission=account_entry_permission and freshness_result == "PASS",
        account_wide_exit_permission=account_exit_permission,
        maximum_approved_quantity=2_147_483_647 if account_entry_permission and freshness_result == "PASS" else 0,
        remaining_portfolio_risk=max(0.0, snapshot.equity - float(risk_state.get("totalOpenRiskDollars", 0.0))),
        current_aggregate_exposure=float(risk_state.get("positionNotionalDollars", 0.0)),
        concentration_restrictions=tuple(code for code in global_snapshot.reasonCodes if "exposure" in code),
        global_circuit_breaker_status="closed" if freshness_result == "PASS" and account_entry_permission else "entries_blocked",
        global_risk_decision_id=f"wca-runtime-risk-{state_hash[:16]}",
        global_risk_expiration=timestamp + timedelta(seconds=maximum_permitted_state_age_seconds),
        wca_configuration_version=config_row["configuration_version"] if config_row is not None else "",
        dynamic_profile_version=_dynamic_profile_version(profile_row),
        weight_snapshot_version=weight_row["weight_version"] if weight_row is not None else "",
        calibration_version=",".join(row["calibration_version"] for row in calibration_rows) if calibration_rows else "wca.calibration.none_recorded",
        inventory_state_version=inventory_state_version,
        reconciliation_watermark="wca-local-paper-authority",
        state_timestamp=timestamp,
        maximum_permitted_state_age_seconds=maximum_permitted_state_age_seconds,
        freshness_result=freshness_result,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
    )


def _local_paper_starting_balance_from_config(config_row: sqlite3.Row | None) -> float:
    if config_row is None:
        return WCA_DEFAULT_LOCAL_PAPER_STARTING_BALANCE
    try:
        payload = json.loads(config_row["payload_json"] or "{}")
    except (KeyError, json.JSONDecodeError, TypeError):
        return WCA_DEFAULT_LOCAL_PAPER_STARTING_BALANCE
    local_paper = payload.get("local_paper") if isinstance(payload.get("local_paper"), dict) else {}
    try:
        return float(local_paper.get("starting_balance") or WCA_DEFAULT_LOCAL_PAPER_STARTING_BALANCE)
    except (TypeError, ValueError):
        return WCA_DEFAULT_LOCAL_PAPER_STARTING_BALANCE


def _local_account_section(snapshot: WcaLocalPaperAccountSnapshot) -> dict[str, Any]:
    return {
        "algorithm_id": snapshot.algorithm_id,
        "account_id": snapshot.account_id,
        "starting_balance": snapshot.starting_balance,
        "cash": snapshot.cash,
        "equity": snapshot.equity,
        "buying_power": snapshot.buying_power,
        "sourceAuthority": WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
        "state_version": snapshot.state_version,
    }


def _inventory_section(
    *,
    current_position: dict[str, Any] | None,
    quantity: int | None,
    average_entry: float | None,
    open_lots: tuple[dict[str, Any], ...],
    realized_pnl: float | None,
    unrealized_pnl: float | None,
    stop: float | None,
    target: float | None,
) -> dict[str, Any]:
    return {
        "current_position": current_position,
        "quantity": int(quantity or 0),
        "average_entry": average_entry,
        "open_lots": open_lots,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "stop": stop,
        "target": target,
    }


def _risk_section(
    *,
    reserved_risk: float | None,
    daily_loss: float | None,
    trades_today: int | None,
    circuit_breaker: str | None,
    cooldown_until: datetime | str | None,
    timestamp: datetime,
) -> dict[str, Any]:
    cooldown_dt = _parse_optional_dt(cooldown_until)
    return {
        "reserved_risk": reserved_risk,
        "daily_loss": daily_loss,
        "trades_today": trades_today,
        "circuit_breaker": circuit_breaker,
        "cooldown": {
            "cooldown_until": cooldown_dt.isoformat() if cooldown_dt else None,
            "active": bool(cooldown_dt and timestamp < cooldown_dt),
        },
    }


def _orders_section(
    *,
    pending_entries: tuple[dict[str, Any], ...],
    pending_exits: tuple[dict[str, Any], ...],
    protective: tuple[dict[str, Any], ...],
    partial: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    return {
        "pending_entries": pending_entries,
        "pending_exits": pending_exits,
        "protective_orders": protective,
        "partial_fills": partial,
    }


def _local_position_payload(position: WcaLocalPaperPositionSnapshot | None) -> dict[str, Any] | None:
    return _dataclass_payload(position) if position is not None else None


def _local_lot_payload(lot: WcaLocalPaperLotSnapshot) -> dict[str, Any]:
    payload = _dataclass_payload(lot)
    payload["quantity"] = int(lot.remaining_quantity or lot.quantity or 0)
    payload["entry_price"] = float(lot.entry_price)
    return payload


def _local_order_payload(order: WcaLocalPaperOrderSnapshot) -> dict[str, Any]:
    return _dataclass_payload(order)


def _local_fill_payload(fill: Any) -> dict[str, Any]:
    return _dataclass_payload(fill)


def _dataclass_payload(value: Any) -> dict[str, Any]:
    payload = asdict(value)
    return json.loads(json.dumps(payload, default=str))


def _is_local_protective_order(order: dict[str, Any]) -> bool:
    client_order_id = str(order.get("client_order_id") or "")
    order_type = str(order.get("order_type") or "").upper()
    return bool(order.get("exit_owner") or client_order_id.startswith("wca-protection-") or order_type == "STOP_LIMIT")


def _is_local_entry_order(order: dict[str, Any], current_direction: str | None) -> bool:
    side = str(order.get("side") or "").upper()
    return current_direction is None or side == current_direction


def _weighted_average_entry(open_lots: tuple[dict[str, Any], ...]) -> float | None:
    quantity = sum(int(lot.get("quantity") or 0) for lot in open_lots)
    if quantity <= 0:
        return None
    return round(sum(int(lot.get("quantity") or 0) * float(lot.get("entry_price") or 0.0) for lot in open_lots) / quantity, 10)


def _broker_snapshot_from_local_account(snapshot: WcaLocalPaperAccountSnapshot, *, symbol: str, timestamp: datetime) -> BrokerAccountSnapshot:
    positions = [_broker_position_from_local(position) for position in snapshot.positions if position.symbol == symbol]
    orders = [_broker_order_from_local(order) for order in snapshot.open_orders if order.symbol == symbol]
    partial_orders = [order for order in orders if order.status == "PARTIALLY_FILLED"]
    pending_orders = [order for order in orders if order.status != "PARTIALLY_FILLED"]
    return BrokerAccountSnapshot(
        accountId=snapshot.account_id,
        equity=snapshot.equity,
        buyingPower=snapshot.buying_power,
        realizedPnlToday=snapshot.daily_realized_pnl,
        positions=positions,
        pendingOrders=pending_orders,
        partiallyFilledOrders=partial_orders,
        observedAt=snapshot.last_mark_timestamp or timestamp,
        sessionDate=snapshot.session_date,
        sourceAuthority=WCA_LOCAL_PAPER_SOURCE_AUTHORITY,
        positionsReconciled=True,
        openOrdersReconciled=True,
    )


def _broker_position_from_local(position: WcaLocalPaperPositionSnapshot) -> BrokerPositionState:
    return BrokerPositionState(
        algorithmId=WCA_ALGORITHM_ID,
        capitalPartitionId="wca.local_paper",
        positionOwner=WCA_ALGORITHM_ID,
        symbol=position.symbol,
        side=Signal.BUY if position.side == WcaSide.BUY.value else Signal.SELL,
        quantity=position.quantity,
        averageEntryPrice=max(0.01, position.average_entry_price),
        markPrice=max(0.01, position.mark_price),
        stopPrice=position.stop_price,
        realizedPnlToday=position.realized_pnl,
        openedAt=position.opened_at,
    )


def _broker_order_from_local(order: WcaLocalPaperOrderSnapshot) -> BrokerOrderState:
    return BrokerOrderState(
        algorithmId=WCA_ALGORITHM_ID,
        capitalPartitionId="wca.local_paper",
        decisionId=order.decision_id,
        orderIntentId=order.order_intent_id,
        positionOwner=WCA_ALGORITHM_ID,
        exitOwner=order.exit_owner,
        symbol=order.symbol,
        side=Signal.BUY if order.side == WcaSide.BUY.value else Signal.SELL,
        clientOrderId=order.client_order_id,
        orderType=order.order_type,
        status=_broker_order_status(order.status),
        quantity=order.quantity,
        filledQuantity=max(0, order.quantity - int(order.remaining_quantity if order.remaining_quantity is not None else order.quantity)),
        entryPrice=max(0.01, float(order.limit_price or order.stop_price or order.target_price or 0.01)),
        stopPrice=order.stop_price,
        submittedAt=order.submitted_at or order.created_at or order.updated_at or datetime.now(timezone.utc),
    )


def _broker_order_status(status: str) -> str:
    normalized = str(status or "").upper()
    if normalized == "PARTIALLY_FILLED":
        return "PARTIALLY_FILLED"
    if normalized in {"PENDING", "OUTBOX_RESERVED", "SUBMITTED"}:
        return "PENDING"
    if normalized == "NEW":
        return "NEW"
    return "ACCEPTED"


def _mark_price_from_market_data(market_data: dict[str, Any] | None) -> float | None:
    if not market_data:
        return None
    price = market_data.get("price") or market_data.get("mark_price") or market_data.get("close")
    if price is not None:
        return float(price)
    bid = market_data.get("bid")
    ask = market_data.get("ask")
    if bid is not None and ask is not None:
        return max(0.01, (float(bid) + float(ask)) / 2.0)
    return None


def _parse_optional_dt(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return _parse_dt(str(value))

def _position_direction(open_lots: tuple[dict[str, Any], ...], quantity: int | None) -> str | None:
    if quantity is None or quantity <= 0:
        return None
    if not open_lots:
        return None
    side = str(open_lots[-1]["side"]).upper()
    return WcaSide.SELL.value if side == WcaSide.SELL.value else WcaSide.BUY.value


def _open_lot(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(row["payload_json"] or "{}")
    fill = payload.get("fill") if isinstance(payload.get("fill"), dict) else {}
    return {
        "lot_id": row["lot_id"],
        "account_id": row["account_id"],
        "symbol": row["symbol"],
        "timestamp": row["timestamp"],
        "configuration_version": row["configuration_version"],
        "engine_version": row["engine_version"],
        "market_snapshot_id": row["market_snapshot_id"],
        "decision_id": row["decision_id"],
        "run_id": row["run_id"],
        "position_id": row["position_id"],
        "side": row["side"],
        "quantity": int(row["quantity"]),
        "entry_price": _positive_float(payload.get("entry_price") or fill.get("average_fill_price") or fill.get("averageFillPrice")),
        "stop_price": _positive_float(payload.get("stop_price")),
        "target_price": _positive_float(payload.get("target_price")),
        "opened_at": payload.get("opened_at") or row["timestamp"],
        "order_intent_id": str(payload.get("order_intent_id") or ""),
        "protective_order_ids": tuple(payload.get("protective_order_ids") or ()),
        "payload": payload,
    }


def _protective_order(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(row["payload_json"] or "{}")
    return {
        "event_timestamp": row["event_timestamp"],
        "order_intent_id": row["order_intent_id"],
        "client_order_id": row["client_order_id"],
        "broker_order_id": row["broker_order_id"],
        "quantity": int(row["quantity"] or 0),
        "protective_order_id": payload.get("protective_order_id"),
        "protective_state": payload.get("protective_state") or payload.get("status") or "unknown",
        "stop_price": _positive_float(payload.get("stop_price")),
        "target_price": _positive_float(payload.get("target_price")),
        "protected_quantity": int(payload.get("protected_quantity") or row["quantity"] or 0),
        "payload": payload,
    }


def _position_details(
    *,
    open_lots: tuple[dict[str, Any], ...],
    protective_orders: tuple[dict[str, Any], ...],
    open_quantity: int | None,
    position_direction: str | None,
) -> dict[str, Any]:
    if not open_quantity or open_quantity <= 0:
        return {
            "entry_timestamp": None,
            "decision_id": None,
            "configuration_version": None,
            "stop_price": None,
            "target_price": None,
            "unprotected": False,
            "inconsistent": False,
            "reason_codes": (),
        }
    reasons: list[str] = []
    inconsistent = False
    unprotected = False
    if not open_lots:
        reasons.append("wca.runtime_state.open_lots_missing")
        return {
            "entry_timestamp": None,
            "decision_id": None,
            "configuration_version": None,
            "stop_price": None,
            "target_price": None,
            "unprotected": True,
            "inconsistent": True,
            "reason_codes": tuple(reasons),
        }

    lot_quantity = sum(int(lot["quantity"]) for lot in open_lots)
    if lot_quantity != open_quantity:
        inconsistent = True
        reasons.append("wca.runtime_state.open_lot_quantity_mismatch")
    sides = {str(lot["side"]).upper() for lot in open_lots}
    if len(sides) != 1 or (position_direction is not None and position_direction not in sides):
        inconsistent = True
        reasons.append("wca.runtime_state.open_lot_side_inconsistent")
    if any(lot["entry_price"] is None or float(lot["entry_price"]) <= 0 for lot in open_lots):
        inconsistent = True
        reasons.append("wca.runtime_state.open_lot_entry_price_missing")
    if any(not lot.get("decision_id") for lot in open_lots):
        inconsistent = True
        reasons.append("wca.runtime_state.open_lot_decision_id_missing")
    if any(not lot.get("configuration_version") for lot in open_lots):
        inconsistent = True
        reasons.append("wca.runtime_state.open_lot_configuration_version_missing")
    opened_at = _earliest_opened_at(open_lots)
    if opened_at is None:
        inconsistent = True
        reasons.append("wca.runtime_state.open_lot_entry_timestamp_missing")

    protections_by_intent = {
        str(order.get("order_intent_id") or ""): order
        for order in protective_orders
        if order.get("order_intent_id")
    }
    stops: list[float] = []
    targets: list[float] = []
    for lot in open_lots:
        order_intent_id = str(lot.get("order_intent_id") or "")
        protection = protections_by_intent.get(order_intent_id, {})
        stop_price = lot.get("stop_price") or protection.get("stop_price")
        target_price = lot.get("target_price") or protection.get("target_price")
        if stop_price is None or target_price is None:
            unprotected = True
            continue
        stops.append(float(stop_price))
        targets.append(float(target_price))
    if unprotected or len(stops) != len(open_lots) or len(targets) != len(open_lots):
        reasons.append("wca.runtime_state.position_protection_missing")
    side = position_direction or str(open_lots[0]["side"]).upper()
    stop_price = None
    target_price = None
    if stops:
        stop_price = max(stops) if side == WcaSide.BUY.value else min(stops)
    if targets:
        target_price = min(targets) if side == WcaSide.BUY.value else max(targets)
    return {
        "entry_timestamp": opened_at,
        "decision_id": str(open_lots[0].get("decision_id") or "") or None,
        "configuration_version": str(open_lots[0].get("configuration_version") or "") or None,
        "stop_price": stop_price,
        "target_price": target_price,
        "unprotected": unprotected,
        "inconsistent": inconsistent,
        "reason_codes": tuple(reasons),
    }


def _pending_order(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(row["payload_json"] or "{}")
    proposed = payload.get("proposed_order") if isinstance(payload.get("proposed_order"), dict) else payload
    return {
        "status": row["status"],
        "side": str(proposed.get("side") or "HOLD"),
        "quantity": int(proposed.get("quantity") or 0),
        "client_order_id": row["client_order_id"],
        "order_intent_id": row["order_intent_id"],
        "payload": payload,
    }


def _is_entry_order(row: sqlite3.Row, current_direction: str | None) -> bool:
    payload = json.loads(row["payload_json"] or "{}")
    proposed = payload.get("proposed_order") if isinstance(payload.get("proposed_order"), dict) else payload
    side = str(proposed.get("side") or "HOLD").upper()
    return current_direction is None or side == current_direction


def _row_payload(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


def _positive_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _earliest_opened_at(lots: tuple[dict[str, Any], ...]) -> datetime | None:
    timestamps: list[datetime] = []
    for lot in lots:
        try:
            timestamps.append(_parse_dt(str(lot.get("opened_at") or lot.get("timestamp"))))
        except (TypeError, ValueError):
            continue
    return min(timestamps) if timestamps else None


def _wca_only_broker_snapshot(snapshot: BrokerAccountSnapshot, *, local_realized_pnl_today: float) -> BrokerAccountSnapshot:
    positions = [position for position in snapshot.positions if position.algorithmId == WCA_ALGORITHM_ID and (position.positionOwner in (None, WCA_ALGORITHM_ID))]
    pending = [order for order in snapshot.pendingOrders if order.algorithmId == WCA_ALGORITHM_ID and (order.positionOwner in (None, WCA_ALGORITHM_ID))]
    partial = [order for order in snapshot.partiallyFilledOrders if order.algorithmId == WCA_ALGORITHM_ID and (order.positionOwner in (None, WCA_ALGORITHM_ID))]
    return snapshot.model_copy(
        update={
            "positions": positions,
            "pendingOrders": pending,
            "partiallyFilledOrders": partial,
            "realizedPnlToday": local_realized_pnl_today,
        }
    )

def _broker_snapshot_isolation_reason_codes(
    snapshot: BrokerAccountSnapshot,
    *,
    broker_account_id: str,
    symbol: str,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if snapshot.accountId != broker_account_id:
        reasons.append("wca.runtime_state.broker_snapshot_account_mismatch")
    symbol_upper = symbol.upper()
    non_wca_positions = [
        position
        for position in snapshot.positions
        if position.symbol.upper() == symbol_upper and position.algorithmId != WCA_ALGORITHM_ID
    ]
    non_wca_orders = [
        order
        for order in [*snapshot.pendingOrders, *snapshot.partiallyFilledOrders]
        if order.symbol.upper() == symbol_upper and order.algorithmId != WCA_ALGORITHM_ID
    ]
    if non_wca_positions:
        reasons.append("wca.runtime_state.shared_physical_account_position_conflict")
    if non_wca_orders:
        reasons.append("wca.runtime_state.shared_physical_account_order_conflict")
    return tuple(reasons)


def _dynamic_profile_version(row: sqlite3.Row | None) -> str:
    if row is None:
        return "wca.dynamic_profile.none_recorded"
    payload = json.loads(row["payload_json"] or "{}")
    return str(payload.get("profile_version") or row["profile_id"])


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "WCA_AUTHORITATIVE_RUNTIME_STATE_VERSION",
    "WcaAuthoritativeRuntimeState",
    "load_wca_authoritative_runtime_state",
]
