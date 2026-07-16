from __future__ import annotations

from datetime import datetime

from backend.app.risk.settings import GlobalRiskSettings
from backend.app.risk.types import GateResult, GlobalOrderIntent, PortfolioSnapshot


def evaluate_order_integrity_gates(intent: GlobalOrderIntent, portfolio: PortfolioSnapshot, settings: GlobalRiskSettings, *, evaluated_at: datetime) -> tuple[GateResult, ...]:
    pending_decision_ids = {order.decisionId for order in portfolio.pendingOrders}
    pending_client_ids = {order.clientOrderId for order in portfolio.pendingOrders if order.clientOrderId}
    pending_intent_keys = {order.intentKey for order in portfolio.pendingOrders}
    intent_key = global_intent_key(intent)
    same_symbol_pending = [order for order in portfolio.pendingOrders if order.symbol.upper() == intent.symbol.upper()]

    gates = [
        _gate("duplicate_decision_id", intent.decisionId not in pending_decision_ids, "Duplicate decision ID gate evaluated.", intent, evaluated_at),
        _gate("duplicate_client_order_id", not intent.clientOrderId or intent.clientOrderId not in pending_client_ids, "Duplicate client-order ID gate evaluated.", intent, evaluated_at),
        _gate("existing_pending_order_for_same_intent", intent_key not in pending_intent_keys, "Pending intent duplicate gate evaluated.", intent, evaluated_at),
        _gate("conflicting_simultaneous_orders", not _has_conflict(intent, same_symbol_pending), "Conflicting simultaneous order gate evaluated.", intent, evaluated_at),
        _gate("invalid_quantity", intent.requestedQuantity > 0, "Quantity gate evaluated.", intent, evaluated_at),
        _gate("invalid_price", intent.expectedEntryPrice > 0, "Entry price gate evaluated.", intent, evaluated_at),
        _gate("invalid_stop_relationship", _valid_stop_relationship(intent), "Stop/target relationship gate evaluated.", intent, evaluated_at),
        _gate("expired_intent", evaluated_at <= intent.expiresAt, "Intent expiration gate evaluated.", intent, evaluated_at),
        _gate("intent_based_on_stale_market_data", intent.marketDataTimestamp <= intent.generatedAt <= intent.expiresAt, "Intent timestamp ordering gate evaluated.", intent, evaluated_at),
        _gate("insufficient_shortability", _shortable(intent, settings), "Shortability gate evaluated.", intent, evaluated_at),
        _gate("unsupported_fractional_quantity", intent.fractionalQuantityAllowed or float(intent.requestedQuantity).is_integer(), "Fractional quantity gate evaluated.", intent, evaluated_at),
        _gate("maximum_share_cap", settings.maximumShareCap <= 0 or intent.requestedQuantity <= settings.maximumShareCap, "Maximum share cap evaluated.", intent, evaluated_at),
        _gate("maximum_notional_cap", settings.maximumNotionalCap <= 0 or intent.requested_notional <= settings.maximumNotionalCap, "Maximum notional cap evaluated.", intent, evaluated_at),
    ]
    return tuple(gates)


def global_intent_key(intent: GlobalOrderIntent) -> str:
    return f"{intent.algorithmId}:{intent.symbol.upper()}:{intent.decisionId}:{intent.positionEffect}:{intent.settingsVersion}:{intent.profileVersion}"


def _gate(gate_id: str, passed: bool, reason: str, intent: GlobalOrderIntent, evaluated_at: datetime) -> GateResult:
    return GateResult(
        gateId=gate_id,
        gateName=gate_id.replace("_", " ").title(),
        status="pass" if passed else "fail",
        reason=reason,
        blocksNewEntries=not passed and intent.is_new_entry,
        blocksProtectiveExits=not passed and gate_id in {"invalid_quantity", "invalid_price", "expired_intent"},
        evaluatedAt=evaluated_at,
    )


def _has_conflict(intent: GlobalOrderIntent, pending_orders) -> bool:
    return any(order.side != intent.side for order in pending_orders)


def _valid_stop_relationship(intent: GlobalOrderIntent) -> bool:
    if intent.protectiveStopPrice is None or intent.targetPrice is None:
        return True
    if intent.side == "Buy":
        return intent.protectiveStopPrice < intent.expectedEntryPrice < intent.targetPrice
    return intent.targetPrice < intent.expectedEntryPrice < intent.protectiveStopPrice


def _shortable(intent: GlobalOrderIntent, settings: GlobalRiskSettings) -> bool:
    if intent.positionEffect != "enter_short":
        return True
    if not settings.shortSalesEnabled:
        return False
    return intent.shortable and intent.borrowAvailable is not False


__all__ = ["evaluate_order_integrity_gates", "global_intent_key"]
