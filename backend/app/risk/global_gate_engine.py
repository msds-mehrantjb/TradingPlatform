"""Authoritative neutral global gate engine."""

from __future__ import annotations

from math import floor

from backend.app.risk.gate_contracts import (
    AccountWideLedgerSnapshot,
    GlobalGateDecision,
    GlobalGateInput,
    GlobalGateOrderSide,
    GlobalGateProposedOrder,
    GlobalGateResult,
    build_global_gate_idempotency_key,
)

GLOBAL_GATE_ENGINE_VERSION = "global_gate_engine_v2_step12"


class GlobalGateEngine:
    """Evaluate account-wide gates after an algorithm has produced a complete proposal."""

    def evaluate(self, gate_input: GlobalGateInput) -> GlobalGateResult:
        order = gate_input.proposed_order
        ledger = aggregate_account_ledger(gate_input)
        gate_key = build_global_gate_idempotency_key(order)
        blockers: list[str] = []
        warnings: list[str] = []

        if gate_input.policy.emergency_flatten or gate_input.market_state.market_wide_circuit_breaker:
            blockers.append("global_gate.emergency_flatten")
            return self._result(
                gate_input,
                ledger,
                gate_key,
                decision=GlobalGateDecision.EMERGENCY_LIQUIDATE,
                allowed_quantity=0,
                blockers=tuple(dict.fromkeys(blockers)),
                warnings=(),
                emergency_flatten=True,
                allow_exit=True,
            )

        if order.is_risk_reducing_exit:
            blockers.extend(_exit_blockers(gate_input, gate_key))
            allowed = 0 if blockers else order.quantity
            return self._result(
                gate_input,
                ledger,
                gate_key,
                decision=GlobalGateDecision.EXIT_ONLY if blockers else GlobalGateDecision.ALLOW,
                allowed_quantity=allowed,
                blockers=tuple(dict.fromkeys(blockers)),
                warnings=(),
                allow_exit=True,
            )

        blockers.extend(_entry_blockers(gate_input, ledger, gate_key))
        if blockers:
            return self._result(
                gate_input,
                ledger,
                gate_key,
                decision=GlobalGateDecision.REJECT_NEW_ENTRY,
                allowed_quantity=0,
                blockers=tuple(dict.fromkeys(blockers)),
                warnings=(),
                allow_exit=True,
            )

        allowed_quantity, cap_warnings = _reduced_quantity(gate_input, ledger)
        warnings.extend(cap_warnings)
        if allowed_quantity <= 0:
            blockers.append("global_gate.quantity.no_capacity")
            return self._result(
                gate_input,
                ledger,
                gate_key,
                decision=GlobalGateDecision.REJECT_NEW_ENTRY,
                allowed_quantity=0,
                blockers=tuple(dict.fromkeys(blockers)),
                warnings=tuple(dict.fromkeys(warnings)),
                allow_exit=True,
            )
        decision = GlobalGateDecision.REDUCE_QUANTITY if allowed_quantity < order.quantity else GlobalGateDecision.ALLOW
        return self._result(
            gate_input,
            ledger,
            gate_key,
            decision=decision,
            allowed_quantity=allowed_quantity,
            blockers=(),
            warnings=tuple(dict.fromkeys(warnings)),
            allow_exit=True,
        )

    def _result(
        self,
        gate_input: GlobalGateInput,
        ledger: AccountWideLedgerSnapshot,
        gate_key: str,
        *,
        decision: GlobalGateDecision,
        allowed_quantity: int,
        blockers: tuple[str, ...],
        warnings: tuple[str, ...],
        allow_exit: bool,
        emergency_flatten: bool = False,
    ) -> GlobalGateResult:
        order = gate_input.proposed_order
        reason_codes = tuple(dict.fromkeys((GLOBAL_GATE_ENGINE_VERSION, *blockers, *warnings)))
        return GlobalGateResult(
            decision=decision,
            algorithm_id=order.algorithm_id,
            proposed_quantity=order.quantity,
            allowed_quantity=allowed_quantity,
            max_additional_risk=_remaining_open_risk_capacity(gate_input, ledger),
            reason_codes=reason_codes,
            evaluated_at=gate_input.evaluation_timestamp,
            allow_new_entries=(
                not order.is_risk_reducing_exit
                and decision in (GlobalGateDecision.ALLOW, GlobalGateDecision.REDUCE_QUANTITY)
                and allowed_quantity > 0
            ),
            allow_position_increases=gate_input.policy.allow_position_increases and decision in (GlobalGateDecision.ALLOW, GlobalGateDecision.REDUCE_QUANTITY),
            allow_risk_reducing_exits=allow_exit,
            emergency_flatten=emergency_flatten,
            requested_quantity=order.quantity,
            approved_quantity=allowed_quantity,
            blockers=blockers,
            warnings=warnings,
            account_snapshot_id=gate_input.account_state.account_snapshot_id,
            market_snapshot_id=gate_input.market_state.market_snapshot_id,
            evaluation_timestamp=gate_input.evaluation_timestamp,
            idempotency_key=gate_key,
            account_ledger=ledger,
        )


def allow_all_for_contract_boundary(algorithm_id: str, proposed_quantity: int) -> GlobalGateResult:
    return GlobalGateResult(
        decision=GlobalGateDecision.ALLOW,
        algorithm_id=algorithm_id,
        proposed_quantity=proposed_quantity,
        allowed_quantity=proposed_quantity,
        requested_quantity=proposed_quantity,
        approved_quantity=proposed_quantity,
        reason_codes=("global_gate.contract_boundary",),
    )


def aggregate_account_ledger(gate_input: GlobalGateInput) -> AccountWideLedgerSnapshot:
    positions = gate_input.ledger_state.positions
    pending = gate_input.ledger_state.pending_orders
    symbol_exposure: dict[str, float] = {}
    for position in positions:
        symbol_exposure[position.symbol] = symbol_exposure.get(position.symbol, 0.0) + position.market_value
    return AccountWideLedgerSnapshot(
        realized_pl=gate_input.account_state.realized_pl,
        unrealized_pl=gate_input.account_state.unrealized_pl,
        estimated_exit_costs=gate_input.account_state.estimated_exit_costs,
        gross_exposure=sum(position.market_value for position in positions),
        net_exposure=sum(position.signed_market_value for position in positions),
        symbol_exposure=symbol_exposure,
        open_stop_risk=sum(position.open_stop_risk for position in positions),
        pending_order_risk=sum(order.pending_risk for order in pending),
        reserved_buying_power=sum(order.reserved_buying_power for order in pending),
        open_order_count=len(pending),
    )


def _entry_blockers(gate_input: GlobalGateInput, ledger: AccountWideLedgerSnapshot, gate_key: str) -> tuple[str, ...]:
    order = gate_input.proposed_order
    account = gate_input.account_state
    market = gate_input.market_state
    policy = gate_input.policy
    blockers: list[str] = []
    if order.quantity <= 0:
        blockers.append("global_gate.order.zero_quantity")
    if not policy.master_entry_enabled:
        blockers.append("global_gate.entry.master_switch_off")
    if not account.broker_market_clock_open or not market.authoritative_broker_market_clock_open:
        blockers.append("global_gate.entry.broker_market_clock_closed")
    if account.new_entry_cutoff_reached:
        blockers.append("global_gate.entry.new_entry_cutoff")
    if not account.broker_connected:
        blockers.append("global_gate.broker.connectivity_unavailable")
    if account.status.upper() != "ACTIVE":
        blockers.append("global_gate.account.status_not_active")
    if not market.market_data_fresh:
        blockers.append("global_gate.market_data.stale")
    if not market.market_data_complete:
        blockers.append("global_gate.market_data.incomplete")
    if market.symbol_halted:
        blockers.append("global_gate.market.symbol_halt")
    if market.luld_active:
        blockers.append("global_gate.market.luld_active")
    if not market.broker_position_reconciled:
        blockers.append("global_gate.reconciliation.position_mismatch")
    if not market.broker_open_orders_reconciled:
        blockers.append("global_gate.reconciliation.open_order_mismatch")
    if _daily_loss(account) > account.daily_loss_limit > 0:
        blockers.append("global_gate.account.daily_loss_limit")
    if _drawdown(account) > account.drawdown_limit > 0:
        blockers.append("global_gate.account.drawdown_limit")
    if policy.max_open_orders and ledger.open_order_count >= policy.max_open_orders:
        blockers.append("global_gate.order_flow.maximum_open_orders")
    if _is_duplicate(gate_input, gate_key):
        blockers.append("global_gate.order_flow.duplicate_order")
    if _has_conflicting_order(gate_input):
        blockers.append("global_gate.order_flow.conflicting_order")
    if order.is_position_increase and not policy.allow_position_increases:
        blockers.append("global_gate.entry.position_increase_disabled")
    if policy.absolute_spread_ceiling and market.spread > policy.absolute_spread_ceiling:
        blockers.append("global_gate.market.absolute_spread_ceiling")
    if policy.absolute_liquidity_floor and market.liquidity < policy.absolute_liquidity_floor:
        blockers.append("global_gate.market.absolute_liquidity_floor")
    if policy.slippage_ceiling and market.estimated_slippage > policy.slippage_ceiling:
        blockers.append("global_gate.market.slippage_ceiling")
    if not _valid_order_geometry(order):
        blockers.append("global_gate.order.final_geometry_invalid")
    if policy.high_impact_event_blackout_enabled and market.high_impact_event_blackout:
        blockers.append("global_gate.event.high_impact_blackout")
    return tuple(blockers)


def _exit_blockers(gate_input: GlobalGateInput, gate_key: str) -> tuple[str, ...]:
    blockers: list[str] = []
    if gate_input.proposed_order.quantity <= 0:
        blockers.append("global_gate.order.zero_quantity")
    if _is_duplicate(gate_input, gate_key):
        blockers.append("global_gate.order_flow.duplicate_order")
    if not _valid_order_geometry(gate_input.proposed_order):
        blockers.append("global_gate.order.final_geometry_invalid")
    return tuple(blockers)


def _reduced_quantity(gate_input: GlobalGateInput, ledger: AccountWideLedgerSnapshot) -> tuple[int, tuple[str, ...]]:
    order = gate_input.proposed_order
    caps: list[tuple[str, int]] = [("requested", order.quantity)]
    warnings: list[str] = []
    _cap_by_value(caps, "symbol_exposure", _remaining_symbol_exposure(gate_input, ledger), order.limit_price)
    _cap_by_value(caps, "gross_exposure", _remaining_gross_exposure(gate_input, ledger), order.limit_price)
    _cap_by_value(caps, "net_exposure", _remaining_net_exposure(gate_input, ledger), order.limit_price)
    _cap_by_value(caps, "available_buying_power", _remaining_buying_power(gate_input, ledger), order.limit_price)
    _cap_by_risk(caps, "open_stop_risk", _remaining_open_risk_capacity(gate_input, ledger), order)
    allowed = max(0, min(quantity for _, quantity in caps))
    for cap_id, quantity in caps:
        if quantity < order.quantity:
            warnings.append(f"global_gate.quantity.reduced_by_{cap_id}")
    return allowed, tuple(warnings)


def _cap_by_value(caps: list[tuple[str, int]], cap_id: str, remaining_value: float | None, price: float) -> None:
    if remaining_value is None:
        return
    caps.append((cap_id, floor(max(0.0, remaining_value) / price)))


def _cap_by_risk(caps: list[tuple[str, int]], cap_id: str, remaining_risk: float | None, order: GlobalGateProposedOrder) -> None:
    if remaining_risk is None or order.planned_risk <= 0:
        return
    caps.append((cap_id, floor(order.quantity * max(0.0, remaining_risk) / order.planned_risk)))


def _remaining_symbol_exposure(gate_input: GlobalGateInput, ledger: AccountWideLedgerSnapshot) -> float | None:
    limit = gate_input.policy.max_symbol_exposure
    if not limit:
        return None
    current = ledger.symbol_exposure.get(gate_input.proposed_order.symbol, 0.0)
    return limit - current


def _remaining_gross_exposure(gate_input: GlobalGateInput, ledger: AccountWideLedgerSnapshot) -> float | None:
    limit = gate_input.policy.max_gross_exposure
    if not limit:
        return None
    return limit - ledger.gross_exposure


def _remaining_net_exposure(gate_input: GlobalGateInput, ledger: AccountWideLedgerSnapshot) -> float | None:
    limit = gate_input.policy.max_net_exposure
    if not limit:
        return None
    order = gate_input.proposed_order
    if order.side == GlobalGateOrderSide.BUY.value:
        remaining_signed = limit - ledger.net_exposure
    else:
        remaining_signed = limit + ledger.net_exposure
    return remaining_signed


def _remaining_buying_power(gate_input: GlobalGateInput, ledger: AccountWideLedgerSnapshot) -> float | None:
    reserve = gate_input.policy.buying_power_reserve
    available = gate_input.account_state.available_buying_power - ledger.reserved_buying_power - reserve
    return available


def _remaining_open_risk_capacity(gate_input: GlobalGateInput, ledger: AccountWideLedgerSnapshot) -> float:
    limit = gate_input.policy.max_open_stop_risk
    if not limit:
        return gate_input.proposed_order.planned_risk
    return max(0.0, limit - ledger.open_stop_risk - ledger.pending_order_risk)


def _is_duplicate(gate_input: GlobalGateInput, gate_key: str) -> bool:
    if gate_key in gate_input.ledger_state.completed_idempotency_keys:
        return True
    for pending in gate_input.ledger_state.pending_orders:
        if pending.idempotency_key == gate_key or pending.order_intent_id == gate_input.proposed_order.order_intent_id:
            return True
    return False


def _has_conflicting_order(gate_input: GlobalGateInput) -> bool:
    order = gate_input.proposed_order
    for pending in gate_input.ledger_state.pending_orders:
        if pending.symbol == order.symbol and pending.side != order.side:
            return True
    for position in gate_input.ledger_state.positions:
        if position.symbol == order.symbol and position.side != order.side and not order.is_risk_reducing_exit:
            return True
    return False


def _valid_order_geometry(order: GlobalGateProposedOrder) -> bool:
    if order.limit_price <= 0:
        return False
    if order.stop_price is None or order.target_price is None:
        return True
    if order.side == GlobalGateOrderSide.BUY.value:
        return order.stop_price < order.limit_price < order.target_price
    return order.target_price < order.limit_price < order.stop_price


def _daily_loss(account) -> float:
    return max(0.0, -(account.realized_pl + account.unrealized_pl - account.estimated_exit_costs))


def _drawdown(account) -> float:
    return max(0.0, account.high_water_equity - account.equity)


__all__ = [
    "GLOBAL_GATE_ENGINE_VERSION",
    "GlobalGateEngine",
    "aggregate_account_ledger",
    "allow_all_for_contract_boundary",
]
