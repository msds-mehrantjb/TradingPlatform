"""Continuous WCA-owned paper position management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from pydantic import Field

from backend.app.algorithms.wca.contracts import ProposedOrder, WCA_ALGORITHM_ID, WcaContractModel, WcaOrderStatus, WcaSide
from backend.app.algorithms.wca.market_calendar import WcaMarketCalendar


WCA_POSITION_MANAGER_VERSION = "wca_position_manager_v1"


@dataclass(frozen=True)
class WcaPositionManagementSettings:
    default_stop_distance: float = 1.0
    default_target_distance: float = 2.0
    trailing_enabled: bool = False
    trailing_distance: float = 1.0
    time_exit_minutes: int | None = None
    end_of_day_flatten_buffer_minutes: int = 5


class WcaManagedPosition(WcaContractModel):
    account_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: WcaSide | str
    open_quantity: int = Field(ge=0)
    average_entry_price: float = Field(ge=0)
    mark_price: float = Field(gt=0)
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    stop_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    trailing_enabled: bool = False
    trailing_stop_price: float | None = Field(default=None, gt=0)
    opened_at: datetime | None = None
    time_exit_due: bool = False
    signal_exit_due: bool = False
    end_of_day_exit_due: bool = False
    emergency_exit_due: bool = False
    circuit_breaker_open: bool = False
    pending_exit_orders: tuple[ProposedOrder, ...] = ()
    reason_codes: tuple[str, ...] = ()


class WcaPositionManagementRepository(Protocol):
    def list_open_wca_lots(self, *, account_id: str, symbol: str) -> tuple[dict[str, Any], ...]:
        ...

    def write_position_management_snapshot(self, position: WcaManagedPosition, *, evaluated_at: datetime) -> None:
        ...

    def close_wca_attributed_position_quantity(self, *, account_id: str, symbol: str, quantity: int, exit_price: float, exit_reason: str, evaluated_at: datetime) -> bool:
        ...

    def realized_pnl_for_wca_position(self, *, account_id: str, symbol: str) -> float:
        ...


def manage_wca_position(
    *,
    repository: WcaPositionManagementRepository,
    account_id: str,
    symbol: str,
    mark_price: float,
    evaluated_at: datetime | None = None,
    opposite_signal: WcaSide | str = WcaSide.HOLD,
    emergency_exit: bool = False,
    global_emergency_risk_reduction: bool = False,
    settings: WcaPositionManagementSettings | None = None,
    calendar: WcaMarketCalendar | None = None,
) -> WcaManagedPosition:
    evaluated = (evaluated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    config = settings or WcaPositionManagementSettings()
    lots = repository.list_open_wca_lots(account_id=account_id, symbol=symbol)
    realized_pnl = repository.realized_pnl_for_wca_position(account_id=account_id, symbol=symbol) if hasattr(repository, "realized_pnl_for_wca_position") else 0.0
    position = build_managed_position(
        lots=lots,
        account_id=account_id,
        symbol=symbol,
        mark_price=mark_price,
        realized_pnl=realized_pnl,
        evaluated_at=evaluated,
        opposite_signal=opposite_signal,
        emergency_exit=emergency_exit,
        global_emergency_risk_reduction=global_emergency_risk_reduction,
        settings=config,
        calendar=calendar or WcaMarketCalendar(),
    )
    repository.write_position_management_snapshot(position, evaluated_at=evaluated)
    return position


def build_managed_position(
    *,
    lots: tuple[dict[str, Any], ...],
    account_id: str,
    symbol: str,
    mark_price: float,
    realized_pnl: float = 0.0,
    evaluated_at: datetime,
    opposite_signal: WcaSide | str = WcaSide.HOLD,
    emergency_exit: bool = False,
    global_emergency_risk_reduction: bool = False,
    settings: WcaPositionManagementSettings | None = None,
    calendar: WcaMarketCalendar | None = None,
) -> WcaManagedPosition:
    config = settings or WcaPositionManagementSettings()
    if not lots:
        return WcaManagedPosition(account_id=account_id, symbol=symbol, side=WcaSide.HOLD, open_quantity=0, average_entry_price=0, mark_price=mark_price, realized_pnl=round(realized_pnl, 10), reason_codes=(WCA_POSITION_MANAGER_VERSION, "wca.position.flat"))
    side = _side_value(lots[0]["side"])
    quantity = sum(int(lot["quantity"]) for lot in lots)
    average_entry = _weighted_average(lots)
    opened_at = min((_dt(lot.get("opened_at") or lot.get("timestamp")) for lot in lots), default=evaluated_at)
    stop_price = _protective_stop(lots)
    target_price = _target_price(lots)
    if stop_price is None:
        stop_price = average_entry - config.default_stop_distance if side == WcaSide.BUY.value else average_entry + config.default_stop_distance
    if target_price is None:
        target_price = average_entry + config.default_target_distance if side == WcaSide.BUY.value else average_entry - config.default_target_distance
    unrealized = _pnl(side, average_entry, mark_price, quantity)
    reason_codes = [WCA_POSITION_MANAGER_VERSION, "wca.position.open"]
    trailing_stop = None
    if config.trailing_enabled:
        trailing_stop = mark_price - config.trailing_distance if side == WcaSide.BUY.value else mark_price + config.trailing_distance
        stop_price = max(stop_price, trailing_stop) if side == WcaSide.BUY.value else min(stop_price, trailing_stop)
        reason_codes.append("wca.position.trailing_stop_active")
    signal_exit_due = _side_value(opposite_signal) in {WcaSide.BUY.value, WcaSide.SELL.value} and _side_value(opposite_signal) != side
    time_exit_due = config.time_exit_minutes is not None and opened_at + timedelta(minutes=config.time_exit_minutes) <= evaluated_at
    eod_due = (calendar or WcaMarketCalendar()).should_flatten(evaluated_at, buffer_minutes=config.end_of_day_flatten_buffer_minutes)
    emergency_due = emergency_exit or global_emergency_risk_reduction
    protective_triggered = (side == WcaSide.BUY.value and mark_price <= stop_price) or (side == WcaSide.SELL.value and mark_price >= stop_price)
    target_triggered = (side == WcaSide.BUY.value and mark_price >= target_price) or (side == WcaSide.SELL.value and mark_price <= target_price)
    unprotected = not any(lot.get("stop_price") for lot in lots)
    exit_due = protective_triggered or target_triggered or signal_exit_due or time_exit_due or eod_due or emergency_due or unprotected
    if unprotected:
        reason_codes.append("wca.position.circuit_breaker.unprotected_position")
    if emergency_due:
        reason_codes.append("wca.position.global_emergency_risk_reduction")
    if eod_due:
        reason_codes.append("wca.position.end_of_day_flatten")
    pending = (_exit_order(account_id, symbol, side, quantity, mark_price, stop_price, target_price, evaluated_at, reason_codes),) if exit_due and quantity > 0 else ()
    return WcaManagedPosition(
        account_id=account_id,
        symbol=symbol,
        side=side,
        open_quantity=quantity,
        average_entry_price=round(average_entry, 10),
        mark_price=mark_price,
        realized_pnl=round(realized_pnl, 10),
        unrealized_pnl=round(unrealized, 10),
        stop_price=round(stop_price, 10),
        target_price=round(target_price, 10),
        trailing_enabled=config.trailing_enabled,
        trailing_stop_price=round(trailing_stop, 10) if trailing_stop else None,
        opened_at=opened_at,
        time_exit_due=time_exit_due,
        signal_exit_due=signal_exit_due,
        end_of_day_exit_due=eod_due,
        emergency_exit_due=emergency_due,
        circuit_breaker_open=unprotected,
        pending_exit_orders=pending,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
    )


def _exit_order(account_id: str, symbol: str, side: str, quantity: int, mark_price: float, stop_price: float, target_price: float, evaluated_at: datetime, reason_codes: list[str]) -> ProposedOrder:
    exit_side = WcaSide.SELL if side == WcaSide.BUY.value else WcaSide.BUY
    stamp = evaluated_at.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
    return ProposedOrder(
        decision_id=f"wca-position-exit-{symbol}-{stamp}",
        order_intent_id=f"wca-exit-{account_id}-{symbol}-{stamp}",
        idempotency_key=f"wca-exit:{account_id}:{symbol}:{stamp}",
        account_id=account_id,
        symbol=symbol,
        side=exit_side,
        quantity=quantity,
        trigger_price=max(0.01, mark_price),
        limit_price=max(0.01, mark_price),
        stop_price=max(0.01, stop_price),
        target_price=max(0.01, target_price),
        status=WcaOrderStatus.PROPOSED,
        reason_codes=tuple((*reason_codes, "wca.position.pending_risk_reducing_exit")),
    )


def _weighted_average(lots: tuple[dict[str, Any], ...]) -> float:
    total_qty = sum(int(lot["quantity"]) for lot in lots)
    return sum(float(lot["entry_price"]) * int(lot["quantity"]) for lot in lots) / total_qty if total_qty else 0.0


def _protective_stop(lots: tuple[dict[str, Any], ...]) -> float | None:
    stops = [float(lot["stop_price"]) for lot in lots if lot.get("stop_price")]
    if not stops:
        return None
    side = _side_value(lots[0]["side"])
    return max(stops) if side == WcaSide.BUY.value else min(stops)


def _target_price(lots: tuple[dict[str, Any], ...]) -> float | None:
    targets = [float(lot["target_price"]) for lot in lots if lot.get("target_price")]
    if not targets:
        return None
    side = _side_value(lots[0]["side"])
    return min(targets) if side == WcaSide.BUY.value else max(targets)


def _pnl(side: str, entry: float, mark: float, quantity: int) -> float:
    return (mark - entry) * quantity if side == WcaSide.BUY.value else (entry - mark) * quantity


def _side_value(side: WcaSide | str) -> str:
    return side.value if isinstance(side, WcaSide) else str(side)


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


__all__ = ["WCA_POSITION_MANAGER_VERSION", "WcaManagedPosition", "WcaPositionManagementSettings", "build_managed_position", "manage_wca_position"]
