"""Final WCA order validation after sizing and paper adjustments."""

from __future__ import annotations

from math import isfinite

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, ProposedOrder, WcaDecision, WcaOrderStatus, WcaOrderValidationContext, WcaOrderValidationResult, WcaRuntimeMode, WcaSide
from backend.app.algorithms.wca.strategies.indicators import eastern_minutes


WCA_ORDER_VALIDATION_VERSION = "wca_order_validation_v1"
WCA_ORDER_VALIDATION_PASSED = "wca.order_validation.passed"
WCA_ORDER_VALIDATION_FAILED = "wca.order_validation.failed"
WCA_ORDER_VALIDATION_EXIT_CRITICAL_ALERT = "wca.order_validation.critical_exit_alert"
WCA_FINAL_PRE_OUTBOX_VALIDATION_PASSED = "wca.order_validation.final_pre_outbox.passed"
WCA_FINAL_PRE_OUTBOX_VALIDATION_FAILED = "wca.order_validation.final_pre_outbox.failed"
WCA_AUTOMATIC_PAPER_ORDER_STAGES = {"LIMITED_AUTOMATIC_PAPER", "AUTOMATIC_PAPER"}


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
    entry_order = not context.is_risk_reducing_exit
    runtime_mode = context.runtime_mode

    if order.algorithm_id != WCA_ALGORITHM_ID or decision.algorithm_id != WCA_ALGORITHM_ID or snapshot.algorithm_id != WCA_ALGORITHM_ID:
        reasons.append("wca.order_validation.ownership_algorithm_mismatch")
    if order.account_id != context.account_id:
        reasons.append("wca.order_validation.account_mismatch")
    if context.account_id in {"", "default", "shared", "live"}:
        reasons.append("wca.order_validation.dedicated_wca_broker_account_required")
    if order.decision_id != decision.decision_id:
        reasons.append("wca.order_validation.ownership_decision_mismatch")
    if order.symbol != snapshot.symbol:
        reasons.append("wca.order_validation.ownership_symbol_mismatch")
    if not context.position_owned_by_wca and context.current_position_quantity > 0:
        reasons.append("wca.order_validation.ownership_position_mismatch")
    if context.cross_algorithm_position_mutation:
        reasons.append("wca.order_validation.cross_algorithm_position_mutation")

    if side not in (WcaSide.BUY.value, WcaSide.SELL.value):
        reasons.append("wca.order_validation.invalid_side")
    if sizing_side != side:
        reasons.append("wca.order_validation.side_sizing_mismatch")

    if order.quantity <= 0 or sizing.final_quantity <= 0:
        reasons.append("wca.order_validation.zero_quantity")
    if order.quantity != sizing.final_quantity:
        reasons.append("wca.order_validation.quantity_sizing_mismatch")
    if context.idempotency_required and not order.idempotency_key:
        reasons.append("wca.order_validation.missing_idempotency_key")
    if order.idempotency_key and context.idempotency_key_seen:
        reasons.append("wca.order_validation.duplicate_idempotency_key")

    if not all(_positive_number(price) for price in prices):
        reasons.append("wca.order_validation.invalid_prices")
    elif not _valid_price_geometry(order):
        reasons.append("wca.order_validation.invalid_price_geometry")
    elif order.limit_price is not None and snapshot.candles:
        reference = snapshot.candles[-1].close
        if _positive_number(reference) and abs(float(order.limit_price) - reference) / reference > 0.10:
            reasons.append("wca.order_validation.unreasonable_price")

    if not context.paper_only_mode:
        reasons.append("wca.order_validation.paper_only_required")
    if context.requires_executable_paper_stage:
        if context.broker_endpoint != "paper":
            reasons.append("wca.order_validation.paper_endpoint_required")
        if runtime_mode not in {WcaRuntimeMode.MANUAL_PAPER, WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER, WcaRuntimeMode.AUTOMATIC_PAPER}:
            reasons.append("wca.order_validation.runtime_stage_not_executable_paper")
        if runtime_mode in {WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER, WcaRuntimeMode.AUTOMATIC_PAPER} and not context.automatic_paper_enabled:
            reasons.append("wca.order_validation.automatic_paper_feature_flag_disabled")
        rollout_policy_required = bool(
            context.rollout_policy_required
            or context.rollout_stage
            or decision.rollout_stage
            or (order.rollout_stage if order is not None else "")
        )
        if entry_order and rollout_policy_required and runtime_mode in {WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER, WcaRuntimeMode.AUTOMATIC_PAPER}:
            rollout_stage = str(context.rollout_stage or "").upper()
            if rollout_stage not in WCA_AUTOMATIC_PAPER_ORDER_STAGES:
                reasons.append("wca.order_validation.rollout_stage_not_automatic_paper")
            if not context.rollout_evidence_revision or not context.rollout_evidence_hash:
                reasons.append("wca.order_validation.rollout_evidence_missing")
    if str(context.order_type).upper() not in {"LIMIT", "STOP_LIMIT"}:
        reasons.append("wca.order_validation.invalid_order_type")
    if str(context.time_in_force).upper() != "DAY":
        reasons.append("wca.order_validation.invalid_time_in_force")
    if _status_value(order.status) not in (
        WcaOrderStatus.PROPOSED.value,
        WcaOrderStatus.RISK_APPROVED.value,
        WcaOrderStatus.VALIDATED.value,
        WcaOrderStatus.OUTBOX_RESERVED.value,
        WcaOrderStatus.ACCEPTED_FOR_PAPER.value,
    ):
        reasons.append("wca.order_validation.invalid_paper_status")

    if settings is None:
        reasons.append("wca.order_validation.missing_effective_settings")
    else:
        if settings.entries_blocked and entry_order:
            reasons.append("wca.order_validation.risk_entries_blocked")
        if settings.final_risk_percent <= 0 and entry_order:
            reasons.append("wca.order_validation.risk_entries_blocked")
        if eastern_minutes(context.evaluation_timestamp) > settings.final_entry_cutoff_minutes and entry_order:
            reasons.append("wca.order_validation.session_closed")
        if settings.final_permitted_order_types and str(context.order_type).upper() not in {order_type.upper() for order_type in settings.final_permitted_order_types}:
            reasons.append("wca.order_validation.order_type_not_permitted_by_wca_settings")
        if settings.final_max_allowed_shares and order.quantity > settings.final_max_allowed_shares:
            reasons.append("wca.order_validation.quantity_exceeds_max_allowed")
        if context.current_position_quantity > 0:
            current_side = _side_value(context.current_position_side)
            if current_side == side and not (context.allow_position_increase and settings.final_pyramiding_enabled):
                reasons.append("wca.order_validation.ownership_position_increase_blocked")
            if current_side and current_side != side and entry_order:
                reasons.append("wca.order_validation.ownership_opposite_position")
    if entry_order:
        reasons.extend(context.market_session_reason_codes)
        if not context.market_is_open:
            reasons.append("wca.order_validation.market_closed")
        if not context.allowed_session_window:
            reasons.append("wca.order_validation.entry_session_window_closed")
        if context.candle_freshness_seconds is not None:
            candle_age = abs((context.evaluation_timestamp - snapshot.data_timestamp).total_seconds())
            if candle_age > context.candle_freshness_seconds:
                reasons.append("wca.order_validation.stale_finalized_candle")
        if context.decision_expiration_seconds is not None:
            decision_age = max(0.0, (context.evaluation_timestamp - decision.decision_timestamp).total_seconds())
            if decision_age > context.decision_expiration_seconds:
                reasons.append("wca.order_validation.decision_expired")
        if context.command_deadline_at is not None and context.evaluation_timestamp > context.command_deadline_at:
            reasons.append("wca.order_validation.runtime_command_deadline_expired")
        if not snapshot.data_ready or not context.data_ready:
            reasons.append("wca.order_validation.data_not_ready")
        if not context.inventory_consistent:
            reasons.append("wca.order_validation.inventory_inconsistent")
        if context.conflicting_wca_position:
            reasons.append("wca.order_validation.conflicting_wca_position")
        if context.pending_wca_entry:
            reasons.append("wca.order_validation.pending_wca_entry")
        if context.cooldown_active:
            reasons.append("wca.order_validation.cooldown_active")
        if context.circuit_breaker_open:
            reasons.append("wca.order_validation.circuit_breaker_open")
        if context.max_approved_quantity is not None and order.quantity > context.max_approved_quantity:
            reasons.append("wca.order_validation.maximum_approved_quantity_exceeded")
        if not context.protective_exit_plan_present:
            reasons.append("wca.order_validation.missing_protective_exit_plan")
        if str(context.rollout_stage or "").upper() == "LIMITED_AUTOMATIC_PAPER":
            reasons.extend(_limited_automatic_paper_cap_reasons(decision, context))

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

    if not context.new_entry_permitted and entry_order:
        reasons.append("wca.order_validation.new_entry_not_permitted")
    if context.is_risk_reducing_exit and not context.risk_reducing_exit_permitted:
        reasons.append("wca.order_validation.risk_reducing_exit_not_permitted")
    if context.quote_freshness_seconds is not None and snapshot.quote is None:
        reasons.append("wca.order_validation.missing_fresh_quote")
    elif context.quote_freshness_seconds is not None:
        age = abs((context.evaluation_timestamp - snapshot.quote.timestamp).total_seconds())
        if age > context.quote_freshness_seconds:
            reasons.append("wca.order_validation.stale_quote")
    if entry_order and context.available_buying_power is not None and order.limit_price is not None:
        if order.quantity * float(order.limit_price) > context.available_buying_power + 1e-6:
            reasons.append("wca.order_validation.buying_power_exceeded")
    if entry_order and context.max_position_value is not None and order.limit_price is not None:
        existing_value = max(0, context.current_position_quantity) * float(order.limit_price)
        proposed_value = order.quantity * float(order.limit_price)
        if existing_value + proposed_value > context.max_position_value + 1e-6:
            reasons.append("wca.order_validation.max_position_exceeded")
    if context.realized_daily_loss is not None and context.max_daily_loss is not None:
        if context.realized_daily_loss >= context.max_daily_loss - 1e-9 and entry_order:
            reasons.append("wca.order_validation.max_daily_loss_exceeded")
    if context.trades_today is not None and context.max_daily_trades is not None:
        if context.trades_today >= context.max_daily_trades and entry_order:
            reasons.append("wca.order_validation.max_daily_trades_exceeded")
    if context.aggregate_global_risk_used is not None and context.aggregate_global_risk_limit is not None:
        if context.aggregate_global_risk_used > context.aggregate_global_risk_limit + 1e-6:
            reasons.append("wca.order_validation.aggregate_global_risk_exceeded")
    if entry_order and context.max_spread_percent is not None and _positive_number(sizing.entry_price):
        spread_percent = (sizing.spread / sizing.entry_price) * 100.0
        if spread_percent > context.max_spread_percent + 1e-9:
            reasons.append("wca.order_validation.spread_limit_exceeded")
    if entry_order and context.average_one_minute_volume is not None and context.max_participation_percent is not None:
        max_quantity = context.average_one_minute_volume * (context.max_participation_percent / 100.0)
        if order.quantity > max_quantity + 1e-9:
            reasons.append("wca.order_validation.participation_limit_exceeded")
    if context.expected_net_edge is not None and context.expected_net_edge <= context.minimum_net_edge and entry_order:
        reasons.append("wca.order_validation.expected_net_edge_not_met")

    if not _blocking_validation_reasons(reasons):
        reasons.append(WCA_ORDER_VALIDATION_PASSED)
        return WcaOrderValidationResult(valid=True, reason_codes=tuple(reasons))
    if context.is_risk_reducing_exit:
        reasons.append(WCA_ORDER_VALIDATION_EXIT_CRITICAL_ALERT)
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


def _blocking_validation_reasons(reasons: list[str]) -> tuple[str, ...]:
    non_blocking = {
        WCA_ORDER_VALIDATION_VERSION,
        "wca_session_validation_v1",
        "wca.session.entry_window_open",
        WCA_ORDER_VALIDATION_PASSED,
    }
    return tuple(reason for reason in reasons if reason not in non_blocking)


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


def _limited_automatic_paper_cap_reasons(decision: WcaDecision, context: WcaOrderValidationContext) -> tuple[str, ...]:
    order = decision.proposed_order
    if order is None:
        return ()
    reasons: list[str] = []
    allowed_symbols = {symbol.upper() for symbol in context.rollout_allowed_symbols if symbol}
    if allowed_symbols and order.symbol.upper() not in allowed_symbols:
        reasons.append("wca.order_validation.rollout_limited_symbol_not_allowed")
    if context.rollout_max_quantity is not None and order.quantity > context.rollout_max_quantity:
        reasons.append("wca.order_validation.rollout_limited_quantity_exceeded")
    if context.rollout_max_daily_trades is not None and context.trades_today is not None:
        if context.trades_today >= context.rollout_max_daily_trades:
            reasons.append("wca.order_validation.rollout_limited_daily_trade_cap_exceeded")
    if context.rollout_max_daily_loss is not None and context.realized_daily_loss is not None:
        if context.realized_daily_loss >= context.rollout_max_daily_loss - 1e-9:
            reasons.append("wca.order_validation.rollout_limited_daily_loss_cap_exceeded")
    if context.rollout_allowed_entry_windows and not _timestamp_in_windows(context.evaluation_timestamp, context.rollout_allowed_entry_windows):
        reasons.append("wca.order_validation.rollout_limited_entry_window_closed")
    allowed_strategy_ids = {strategy_id.upper() for strategy_id in context.rollout_allowed_strategy_ids if strategy_id}
    active_strategy_ids = _directional_strategy_ids(decision)
    if allowed_strategy_ids and not active_strategy_ids.issubset(allowed_strategy_ids):
        reasons.append("wca.order_validation.rollout_limited_strategy_set_not_allowed")
    return tuple(reasons)


def _directional_strategy_ids(decision: WcaDecision) -> set[str]:
    ids: set[str] = set()
    for evaluation in decision.aggregation.strategy_evaluations:
        if _side_value(evaluation.signal) != WcaSide.HOLD.value:
            ids.add(evaluation.strategy_id.upper())
    return ids


def _timestamp_in_windows(timestamp, windows: tuple[str, ...]) -> bool:
    minute = eastern_minutes(timestamp)
    for raw_window in windows:
        window = str(raw_window).strip()
        if not window:
            continue
        time_range = window.split()[0]
        if "-" not in time_range:
            continue
        start, end = time_range.split("-", 1)
        start_minutes = _window_minutes(start)
        end_minutes = _window_minutes(end)
        if start_minutes is not None and end_minutes is not None and start_minutes <= minute <= end_minutes:
            return True
    return False


def _window_minutes(value: str) -> int | None:
    try:
        hour, minute = value.split(":", 1)
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def assert_wca_final_pre_outbox_validation(decision: WcaDecision, context: WcaOrderValidationContext) -> WcaDecision:
    validation = validate_wca_final_order(decision, context)
    if not validation.valid:
        raise ValueError(";".join((*validation.reason_codes, WCA_FINAL_PRE_OUTBOX_VALIDATION_FAILED)))
    if decision.proposed_order is None:
        raise ValueError("cannot reserve WCA outbox without a proposed order")
    proposed = decision.proposed_order.model_copy(
        update={"reason_codes": _append_reasons(decision.proposed_order.reason_codes, (*validation.reason_codes, WCA_FINAL_PRE_OUTBOX_VALIDATION_PASSED))}
    )
    return decision.model_copy(
        update={
            "proposed_order": proposed,
            "reason_codes": _append_reasons(decision.reason_codes, (*validation.reason_codes, WCA_FINAL_PRE_OUTBOX_VALIDATION_PASSED)),
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
    "WCA_FINAL_PRE_OUTBOX_VALIDATION_FAILED",
    "WCA_FINAL_PRE_OUTBOX_VALIDATION_PASSED",
    "WCA_ORDER_VALIDATION_EXIT_CRITICAL_ALERT",
    "WCA_ORDER_VALIDATION_PASSED",
    "WCA_ORDER_VALIDATION_VERSION",
    "WcaOrderValidationContext",
    "WcaOrderValidationResult",
    "apply_wca_final_order_validation",
    "assert_wca_final_pre_outbox_validation",
    "drop_wca_order",
    "validate_wca_final_order",
]
