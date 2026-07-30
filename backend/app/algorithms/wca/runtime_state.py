"""Authoritative WCA runtime-state loader."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import Field

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaContractModel, WcaSide
from backend.app.algorithms.wca.exits import WcaBacktestOpenPosition
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.gates import BrokerAccountSnapshot, aggregate_global_account_risk


WCA_AUTHORITATIVE_RUNTIME_STATE_VERSION = "wca_authoritative_runtime_state_v1"


class WcaAuthoritativeRuntimeState(WcaContractModel):
    algorithm_id: str = WCA_ALGORITHM_ID
    state_version: str
    state_hash: str = ""
    broker_account: dict[str, Any]
    broker_account_id: str
    symbol: str
    wca_inventory: dict[str, Any]
    current_position_direction: str | None
    current_quantity: int | None = Field(default=None, ge=0)
    available_quantity: int | None = Field(default=None, ge=0)
    average_entry_price: float | None = Field(default=None, ge=0)
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
        side = WcaSide.BUY if self.current_position_direction == WcaSide.BUY.value else WcaSide.SELL
        entry = self.average_entry_price or 0.01
        return WcaBacktestOpenPosition(
            trade_id=f"wca-runtime-position-{self.broker_account_id}-{self.symbol}-{self.inventory_state_version[:12]}",
            decision_id=self.global_risk_decision_id,
            symbol=self.symbol,
            side=side,
            quantity=self.current_quantity,
            entry_at=self.broker_observation_timestamp or self.state_timestamp,
            entry_price=entry,
            stop_price=max(0.01, entry * 0.5),
            target_price=max(0.01, entry * 10.0),
        )


def load_wca_authoritative_runtime_state(
    repository: WcaSqliteRepository,
    *,
    broker_account_id: str,
    symbol: str,
    state_timestamp: datetime,
    maximum_permitted_state_age_seconds: int,
) -> WcaAuthoritativeRuntimeState:
    timestamp = state_timestamp.astimezone(timezone.utc)
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
        latest_lot = conn.execute(
            """
            SELECT side, payload_json, timestamp
            FROM wca_owned_lots
            WHERE algorithm_id = ? AND account_id = ? AND symbol = ? AND status = 'open' AND quantity > 0
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (WCA_ALGORITHM_ID, broker_account_id, symbol),
        ).fetchone()
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
            SELECT payload_json
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
            "SELECT configuration_version FROM wca_active_configuration WHERE algorithm_id = ?",
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
    if broker_snapshot is not None and broker_snapshot.sourceAuthority != "broker":
        reason_codes.append("wca.runtime_state.broker_source_not_authoritative")
    if broker_snapshot is not None and (not broker_snapshot.positionsReconciled or not broker_snapshot.openOrdersReconciled):
        reason_codes.append("wca.runtime_state.broker_not_reconciled")

    open_quantity = int(inventory["open_quantity"]) if inventory is not None else None
    position_direction = _position_direction(latest_lot, open_quantity)
    available_quantity = open_quantity
    pending_entry_orders = tuple(_pending_order(row) for row in pending_rows if _is_entry_order(row, position_direction))
    pending_exit_orders = tuple(_pending_order(row) for row in pending_rows if not _is_entry_order(row, position_direction))
    protective_orders = tuple(json.loads(row["payload_json"] or "{}") for row in protective_rows)
    partial_fills = tuple(json.loads(row["payload_json"] or "{}") for row in partial_fill_rows)
    daily_loss = float(daily["daily_loss"]) if daily is not None else None
    broker_positions = tuple(position.model_dump(mode="json") for position in broker_snapshot.positions) if broker_snapshot is not None else ()
    pending_broker_orders = tuple(order.model_dump(mode="json") for order in broker_snapshot.pendingOrders) if broker_snapshot is not None else ()
    broker_partial_orders = tuple(order.model_dump(mode="json") for order in broker_snapshot.partiallyFilledOrders) if broker_snapshot is not None else ()
    global_snapshot = aggregate_global_account_risk(broker_snapshot, candidateSymbol=symbol, candidateSide=position_direction) if broker_snapshot is not None else None
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
        remaining_portfolio_risk = max(0.0, broker_snapshot.equity - float(risk_state.get("totalOpenRiskDollars", 0.0)))
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
    freshness_result = "FAIL" if any(code.startswith("wca.runtime_state.") and code not in {"wca.runtime_state.account_entry_not_permitted"} for code in reason_codes[1:]) else "PASS"
    if freshness_result == "PASS":
        reason_codes.append("wca.runtime_state.fresh")

    state = WcaAuthoritativeRuntimeState(
        state_version=WCA_AUTHORITATIVE_RUNTIME_STATE_VERSION,
        state_hash=state_hash,
        broker_account=broker_payload,
        broker_account_id=broker_account_id,
        symbol=symbol,
        wca_inventory=inventory_payload,
        current_position_direction=position_direction,
        current_quantity=open_quantity,
        available_quantity=available_quantity,
        average_entry_price=float(inventory["average_entry_price"]) if inventory is not None else None,
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
        equity=float(broker_snapshot.equity) if broker_snapshot is not None else None,
        buying_power=float(broker_snapshot.buyingPower) if broker_snapshot is not None else None,
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
        current_aggregate_exposure=float(risk_state.get("positionNotionalDollars", 0.0)) if broker_snapshot is not None else None,
        concentration_restrictions=tuple(code for code in global_snapshot.reasonCodes if "exposure" in code) if global_snapshot is not None else (),
        global_circuit_breaker_status="open" if freshness_result == "PASS" and account_entry_permission else "entries_blocked",
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


def _position_direction(latest_lot: sqlite3.Row | None, quantity: int | None) -> str | None:
    if quantity is None or quantity <= 0:
        return None
    if latest_lot is None:
        return WcaSide.BUY.value
    side = str(latest_lot["side"]).upper()
    return WcaSide.SELL.value if side == WcaSide.SELL.value else WcaSide.BUY.value


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
