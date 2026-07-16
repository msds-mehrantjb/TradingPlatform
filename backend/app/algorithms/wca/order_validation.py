"""Final WCA order validation after sizing and paper adjustments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, ProposedOrder, WcaDecision, WcaOrderStatus, WcaSide
from backend.app.algorithms.wca.strategies.indicators import eastern_minutes


WCA_ORDER_VALIDATION_VERSION = "wca_order_validation_v1"
WCA_ORDER_VALIDATION_PASSED = "wca.order_validation.passed"
WCA_ORDER_VALIDATION_FAILED = "wca.order_validation.failed"


@dataclass(frozen=True)
class WcaOrderValidationContext:
    evaluation_timestamp: datetime
    paper_only_mode: bool = True
    current_position_quantity: int = 0
    current_position_side: WcaSide | str | None = None
    allow_position_increase: bool = False
    position_owned_by_wca: bool = True


@dataclass(frozen=True)
class WcaOrderValidationResult:
    valid: bool
    reason_codes: tuple[str, ...]


def validate_wca_final_order(decision: WcaDecision, context: WcaOrderValidationContext) -> WcaOrderValidationResult:
    """Validate the final proposed order after every override or backend adjustment."""

    reasons: list[str] = [WCA_ORDER_VALIDATION_VERSION]
    order = decision.proposed_order
    sizing = decision.sizing
    settings = decision.effective_settings
    snapshot = decision.market_snapshot

    if order is None:
        reasons.append("wca.order_validation.no_order")
        if sizing.final_quantity > 0:
            reasons.append("wca.order_validation.quantity_without_order")
        return WcaOrderValidationResult(valid=False, reason_codes=tuple(reasons))

    side = _side_value(order.side)
    sizing_side = _side_value(sizing.side)
    prices = (order.trigger_price, order.limit_price, order.stop_price, order.target_price)

    if order.algorithm_id != WCA_ALGORITHM_ID or decision.algorithm_id != WCA_ALGORITHM_ID or snapshot.algorithm_id != WCA_ALGORITHM_ID:
        reasons.append("wca.order_validation.ownership_algorithm_mismatch")
    if order.decision_id != decision.decision_id:
        reasons.append("wca.order_validation.ownership_decision_mismatch")
    if order.symbol != snapshot.symbol:
        reasons.append("wca.order_validation.ownership_symbol_mismatch")
    if not context.position_owned_by_wca and context.current_position_quantity > 0:
        reasons.append("wca.order_validation.ownership_position_mismatch")

    if side not in (WcaSide.BUY.value, WcaSide.SELL.value):
        reasons.append("wca.order_validation.invalid_side")
    if sizing_side != side:
        reasons.append("wca.order_validation.side_sizing_mismatch")

    if order.quantity <= 0 or sizing.final_quantity <= 0:
        reasons.append("wca.order_validation.zero_quantity")
    if order.quantity != sizing.final_quantity:
        reasons.append("wca.order_validation.quantity_sizing_mismatch")

    if not all(_positive_number(price) for price in prices):
        reasons.append("wca.order_validation.invalid_prices")
    elif not _valid_price_geometry(order):
        reasons.append("wca.order_validation.invalid_price_geometry")

    if not context.paper_only_mode:
        reasons.append("wca.order_validation.paper_only_required")
    if _status_value(order.status) not in (WcaOrderStatus.PROPOSED.value, WcaOrderStatus.ACCEPTED_FOR_PAPER.value):
        reasons.append("wca.order_validation.invalid_paper_status")

    if settings is None:
        reasons.append("wca.order_validation.missing_effective_settings")
    else:
        if settings.entries_blocked or settings.final_risk_percent <= 0:
            reasons.append("wca.order_validation.risk_entries_blocked")
        if eastern_minutes(context.evaluation_timestamp) > settings.final_entry_cutoff_minutes:
            reasons.append("wca.order_validation.session_closed")
        if settings.final_max_allowed_shares and order.quantity > settings.final_max_allowed_shares:
            reasons.append("wca.order_validation.quantity_exceeds_max_allowed")
        if context.current_position_quantity > 0:
            current_side = _side_value(context.current_position_side)
            if current_side == side and not (context.allow_position_increase and settings.final_pyramiding_enabled):
                reasons.append("wca.order_validation.ownership_position_increase_blocked")
            if current_side and current_side != side:
                reasons.append("wca.order_validation.ownership_opposite_position")

    if sizing.stop_distance <= 0 or sizing.stop_risk_dollars <= 0:
        reasons.append("wca.order_validation.invalid_risk")
    if sizing.reward_risk_ratio < sizing.minimum_reward_risk:
        reasons.append("wca.order_validation.reward_risk_not_met")
    if sizing.approved_risk_budget is not None and sizing.stop_risk_dollars > sizing.approved_risk_budget + 1e-6:
        reasons.append("wca.order_validation.risk_budget_exceeded")
    if all(_positive_number(price) for price in (order.trigger_price, order.stop_price)):
        order_risk = abs(float(order.trigger_price) - float(order.stop_price)) * order.quantity
        if sizing.approved_risk_budget is not None and order_risk > sizing.approved_risk_budget + 1e-6:
            reasons.append("wca.order_validation.order_risk_budget_exceeded")

    if len(reasons) == 1:
        reasons.append(WCA_ORDER_VALIDATION_PASSED)
        return WcaOrderValidationResult(valid=True, reason_codes=tuple(reasons))
    reasons.append(WCA_ORDER_VALIDATION_FAILED)
    return WcaOrderValidationResult(valid=False, reason_codes=tuple(_dedupe(reasons)))


def apply_wca_final_order_validation(decision: WcaDecision, context: WcaOrderValidationContext) -> WcaDecision:
    if decision.proposed_order is None and decision.sizing.final_quantity <= 0:
        return decision

    validation = validate_wca_final_order(decision, context)
    if validation.valid and decision.proposed_order is not None:
        proposed = decision.proposed_order.model_copy(
            update={"reason_codes": _append_reasons(decision.proposed_order.reason_codes, validation.reason_codes)}
        )
        return decision.model_copy(
            update={
                "proposed_order": proposed,
                "reason_codes": _append_reasons(decision.reason_codes, validation.reason_codes),
            }
        )
    return drop_wca_order(decision, validation.reason_codes)


def drop_wca_order(decision: WcaDecision, reason_codes: tuple[str, ...]) -> WcaDecision:
    reasons = _append_reasons(reason_codes, (WCA_ORDER_VALIDATION_FAILED,))
    sizing = decision.sizing.model_copy(
        update={
            "final_quantity": 0,
            "blocked_reason": _first_failure(reasons),
            "reason_codes": _append_reasons(decision.sizing.reason_codes, reasons),
        }
    )
    return decision.model_copy(
        update={
            "sizing": sizing,
            "proposed_order": None,
            "reason_codes": _append_reasons(decision.reason_codes, reasons),
        }
    )


def _valid_price_geometry(order: ProposedOrder) -> bool:
    trigger = float(order.trigger_price)
    limit = float(order.limit_price)
    stop = float(order.stop_price)
    target = float(order.target_price)
    if abs(trigger - limit) > 1e-9:
        return False
    if _side_value(order.side) == WcaSide.BUY.value:
        return stop < trigger < target
    if _side_value(order.side) == WcaSide.SELL.value:
        return target < trigger < stop
    return False


def _status_value(status: WcaOrderStatus | str) -> str:
    return status.value if isinstance(status, WcaOrderStatus) else str(status)


def _side_value(side: WcaSide | str | None) -> str:
    return side.value if isinstance(side, WcaSide) else str(side or "")


def _positive_number(value: float | None) -> bool:
    return value is not None and isfinite(value) and value > 0


def _append_reasons(existing: tuple[str, ...], additions: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_dedupe((*existing, *additions)))


def _dedupe(reasons: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(reasons))


def _first_failure(reasons: tuple[str, ...]) -> str:
    for reason in reasons:
        if reason not in {WCA_ORDER_VALIDATION_VERSION, WCA_ORDER_VALIDATION_PASSED, WCA_ORDER_VALIDATION_FAILED}:
            return reason
    return WCA_ORDER_VALIDATION_FAILED


__all__ = [
    "WCA_ORDER_VALIDATION_FAILED",
    "WCA_ORDER_VALIDATION_PASSED",
    "WCA_ORDER_VALIDATION_VERSION",
    "WcaOrderValidationContext",
    "WcaOrderValidationResult",
    "apply_wca_final_order_validation",
    "drop_wca_order",
    "validate_wca_final_order",
]
