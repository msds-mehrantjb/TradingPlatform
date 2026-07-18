"""Authoritative WCA stop, target, and order-proposal sizing."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor, isfinite

from backend.app.algorithms.wca.contracts import ProposedOrder, WcaEffectiveSettings, WcaOrderStatus, WcaSide, WcaSizingResult

WCA_SIZING_VERSION = "wca_sizing_v2_step11"
_UNCAPPED_QUANTITY = 2_147_483_647


@dataclass(frozen=True)
class WcaSizingContext:
    decision_id: str
    order_intent_id: str
    symbol: str
    side: WcaSide | str
    price: float
    atr: float
    bid: float
    ask: float
    account_equity: float
    available_buying_power: float
    average_one_minute_volume: float
    confidence_size_multiplier: float = 1.0
    edge_size_multiplier: float = 1.0
    dynamic_profile_multiplier: float | None = None
    global_gate_quantity_cap: int | None = None
    approved_risk_budget: float | None = None
    current_position_quantity: int = 0
    current_position_side: WcaSide | str | None = None
    allow_position_increase: bool = False
    fixed_stop_fallback: float = 0.05
    minimum_spread_multiple: float = 2.0
    minimum_reward_risk: float | None = None
    estimated_cost_per_share: float | None = None


@dataclass(frozen=True)
class WcaSizingInputDefinition:
    input_id: str
    source: str
    responsibility: str


WCA_SIZING_INPUT_INVENTORY: tuple[WcaSizingInputDefinition, ...] = (
    WcaSizingInputDefinition("wca_signal_strength", "aggregation.normalized_net_score", "Scale WCA quantity by the final WCA signal strength."),
    WcaSizingInputDefinition("wca_confidence_and_edge", "context.confidence_size_multiplier and context.edge_size_multiplier", "Represent WCA conviction before position sizing."),
    WcaSizingInputDefinition("wca_risk_allocation", "effective_settings.final_risk_percent", "Limit risk to the WCA effective risk allocation."),
    WcaSizingInputDefinition("stop_distance", "ATR, minimum stop distance, spread, and fallback", "Calculate per-share WCA stop risk."),
    WcaSizingInputDefinition("available_buying_power", "pipeline_input.available_buying_power", "Cap proposal size by available buying power."),
    WcaSizingInputDefinition("position_cap_limit", "effective_settings.final_max_position_percent", "Cap WCA position value."),
    WcaSizingInputDefinition("liquidity_participation", "effective_settings.final_max_participation_percent", "Cap quantity by WCA liquidity participation."),
    WcaSizingInputDefinition("maximum_shares", "effective_settings.final_max_allowed_shares", "Apply the WCA maximum-share limit."),
    WcaSizingInputDefinition("remaining_wca_risk_budget", "context.approved_risk_budget", "Cap stop risk by remaining approved WCA risk budget."),
    WcaSizingInputDefinition("global_gate_quantity_cap", "context.global_gate_quantity_cap", "Respect the shared global-gate quantity cap without submitting orders."),
)

WCA_SIZING_INPUT_IDS = frozenset(row.input_id for row in WCA_SIZING_INPUT_INVENTORY)


@dataclass(frozen=True)
class WcaManualSizingOverride:
    quantity: int | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None


@dataclass(frozen=True)
class WcaSizedOrder:
    sizing: WcaSizingResult
    proposed_order: ProposedOrder | None


def size_wca_order(
    context: WcaSizingContext,
    effective_settings: WcaEffectiveSettings,
    manual_override: WcaManualSizingOverride | None = None,
) -> WcaSizedOrder:
    """Calculate WCA quantity and a paper/backtest order proposal without submitting it."""

    side = _side_value(context.side)
    spread = context.ask - context.bid
    invalid_reason = _invalid_input_reason(context, spread)
    if invalid_reason:
        return _zero_sized_order(context, effective_settings, side, spread=max(0.0, spread), limiting_factor=invalid_reason, reason_codes=(f"wca.sizing.{invalid_reason}",))
    if side not in (WcaSide.BUY.value, WcaSide.SELL.value):
        return _zero_sized_order(context, effective_settings, side, spread=spread, limiting_factor="non_directional", reason_codes=("wca.sizing.non_directional",))
    if _same_side_position_exists(context, side) and not context.allow_position_increase:
        return _zero_sized_order(
            context,
            effective_settings,
            side,
            spread=spread,
            limiting_factor="position_increase_blocked",
            reason_codes=("wca.sizing.position_increase_blocked",),
        )

    entry_price = _entry_price(context, side)
    stop_distance = _stop_distance(context, effective_settings, entry_price, spread)
    cost_per_share = _cost_per_share(context, effective_settings, spread)
    per_share_risk = stop_distance + cost_per_share
    risk_dollars = _risk_dollars(context, effective_settings)
    caps = _quantity_caps(context, effective_settings, entry_price, per_share_risk, risk_dollars)
    limiting_factor, limiting_quantity = min(caps.items(), key=lambda item: item[1])
    final_quantity = max(0, floor(limiting_quantity))

    minimum_reward_risk = _minimum_reward_risk(context, effective_settings)
    stop_price, target_price = _stop_and_target(entry_price, stop_distance, cost_per_share, minimum_reward_risk, side)
    reward_risk_ratio = _reward_risk_ratio(entry_price, target_price, stop_price, cost_per_share)
    reason_codes = ["wca.sizing.calculated", f"wca.sizing.cap.{limiting_factor}"]

    if reward_risk_ratio < minimum_reward_risk - 1e-9:
        final_quantity = 0
        limiting_factor = "minimum_reward_risk"
        reason_codes.append("wca.sizing.minimum_reward_risk_not_met")

    if manual_override is not None:
        override_result = _apply_manual_override(
            quantity=final_quantity,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            side=side,
            caps=caps,
            minimum_reward_risk=minimum_reward_risk,
            cost_per_share=cost_per_share,
            manual_override=manual_override,
        )
        final_quantity, stop_price, target_price, manual_reason = override_result
        reason_codes.append(manual_reason)
        if final_quantity == 0:
            limiting_factor = "manual_override"

    stop_risk_dollars = final_quantity * per_share_risk
    if context.approved_risk_budget is not None and stop_risk_dollars > context.approved_risk_budget + 1e-9:
        final_quantity = 0
        stop_risk_dollars = 0.0
        limiting_factor = "approved_risk_budget"
        reason_codes.append("wca.sizing.approved_risk_budget_exceeded")

    sizing = _sizing_result(
        context=context,
        side=side,
        entry_price=entry_price,
        stop_distance=stop_distance,
        stop_price=stop_price,
        target_price=target_price,
        spread=spread,
        estimated_costs=cost_per_share,
        minimum_reward_risk=minimum_reward_risk,
        reward_risk_ratio=reward_risk_ratio,
        risk_dollars=risk_dollars,
        stop_risk_dollars=stop_risk_dollars,
        caps=caps,
        final_quantity=final_quantity,
        limiting_factor=limiting_factor,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
    )
    return WcaSizedOrder(sizing=sizing, proposed_order=_proposed_order(context, side, sizing))


def tighten_protective_stop(current_stop_price: float, proposed_stop_price: float, side: WcaSide | str) -> float:
    """Return the risk-reducing stop only; WCA never widens a protective stop automatically."""

    side_value = _side_value(side)
    if side_value == WcaSide.BUY.value:
        return max(current_stop_price, proposed_stop_price)
    if side_value == WcaSide.SELL.value:
        return min(current_stop_price, proposed_stop_price)
    return current_stop_price


def _invalid_input_reason(context: WcaSizingContext, spread: float) -> str:
    checks = (
        ("invalid_price", context.price),
        ("invalid_atr", context.atr),
        ("invalid_bid", context.bid),
        ("invalid_ask", context.ask),
        ("invalid_account_equity", context.account_equity),
        ("invalid_buying_power", context.available_buying_power),
        ("invalid_average_volume", context.average_one_minute_volume),
    )
    for reason, value in checks:
        if not _positive_number(value):
            return reason
    if context.ask <= context.bid or not _positive_number(spread):
        return "invalid_spread"
    if context.confidence_size_multiplier < 0:
        return "invalid_confidence_multiplier"
    if context.edge_size_multiplier < 0:
        return "invalid_edge_multiplier"
    if context.dynamic_profile_multiplier is not None and context.dynamic_profile_multiplier < 0:
        return "invalid_dynamic_multiplier"
    return ""


def _quantity_caps(
    context: WcaSizingContext,
    effective_settings: WcaEffectiveSettings,
    entry_price: float,
    per_share_risk: float,
    risk_dollars: float,
) -> dict[str, float]:
    maximum_share_cap = effective_settings.final_max_allowed_shares or _UNCAPPED_QUANTITY
    global_cap = context.global_gate_quantity_cap if context.global_gate_quantity_cap is not None else _UNCAPPED_QUANTITY
    maximum_position_value = context.account_equity * (effective_settings.final_max_position_percent / 100.0)
    current_position_value = max(0, context.current_position_quantity) * entry_price
    remaining_position_value = max(0.0, maximum_position_value - current_position_value)
    return {
        "risk_based": risk_dollars / per_share_risk if per_share_risk > 0 else 0,
        "order_allocation": (context.account_equity * (effective_settings.final_order_allocation_percent / 100.0)) / entry_price,
        "maximum_position": remaining_position_value / entry_price,
        "buying_power": context.available_buying_power / entry_price,
        "liquidity_participation": context.average_one_minute_volume * (effective_settings.final_max_participation_percent / 100.0),
        "maximum_shares": float(maximum_share_cap),
        "global_gate": float(max(0, global_cap)),
    }


def _risk_dollars(context: WcaSizingContext, effective_settings: WcaEffectiveSettings) -> float:
    dynamic_multiplier = effective_settings.risk_multiplier if context.dynamic_profile_multiplier is None else context.dynamic_profile_multiplier
    raw_risk = (
        context.account_equity
        * (effective_settings.baseline.base_risk_percent / 100.0)
        * min(context.confidence_size_multiplier, context.edge_size_multiplier)
        * dynamic_multiplier
    )
    effective_limit = context.account_equity * (effective_settings.final_risk_percent / 100.0)
    approved_limit = context.approved_risk_budget if context.approved_risk_budget is not None else effective_limit
    return max(0.0, min(raw_risk, effective_limit, approved_limit))


def _stop_distance(context: WcaSizingContext, effective_settings: WcaEffectiveSettings, entry_price: float, spread: float) -> float:
    components = (
        context.atr * effective_settings.final_atr_stop_multiplier,
        entry_price * (effective_settings.final_minimum_stop_distance_percent / 100.0),
        spread * context.minimum_spread_multiple,
        context.fixed_stop_fallback,
    )
    return max(components)


def _cost_per_share(context: WcaSizingContext, effective_settings: WcaEffectiveSettings, spread: float) -> float:
    estimate = effective_settings.final_assumed_slippage_per_share if context.estimated_cost_per_share is None else context.estimated_cost_per_share
    return max(0.0, spread + estimate)


def _entry_price(context: WcaSizingContext, side: str) -> float:
    if side == WcaSide.BUY.value:
        return context.ask
    return context.bid


def _stop_and_target(entry_price: float, stop_distance: float, cost_per_share: float, minimum_reward_risk: float, side: str) -> tuple[float, float]:
    target_distance = stop_distance * minimum_reward_risk + cost_per_share
    if side == WcaSide.BUY.value:
        return round(entry_price - stop_distance, 10), round(entry_price + target_distance, 10)
    return round(entry_price + stop_distance, 10), round(entry_price - target_distance, 10)


def _reward_risk_ratio(entry_price: float, target_price: float, stop_price: float, cost_per_share: float) -> float:
    reward = max(0.0, abs(target_price - entry_price) - cost_per_share)
    risk = abs(entry_price - stop_price)
    return reward / risk if risk > 0 else 0.0


def _apply_manual_override(
    *,
    quantity: int,
    entry_price: float,
    stop_price: float,
    target_price: float,
    side: str,
    caps: dict[str, float],
    minimum_reward_risk: float,
    cost_per_share: float,
    manual_override: WcaManualSizingOverride,
) -> tuple[int, float, float, str]:
    if manual_override.limit_price is not None and manual_override.limit_price != entry_price:
        return 0, stop_price, target_price, "wca.sizing.manual_override_invalid_limit"
    overridden_quantity = quantity if manual_override.quantity is None else manual_override.quantity
    overridden_stop = stop_price if manual_override.stop_price is None else manual_override.stop_price
    overridden_target = target_price if manual_override.target_price is None else manual_override.target_price
    max_cap = floor(min(caps.values()))
    if overridden_quantity < 0 or overridden_quantity > max_cap:
        return 0, overridden_stop, overridden_target, "wca.sizing.manual_override_invalid_quantity"
    if not _stop_and_target_are_valid(entry_price, overridden_stop, overridden_target, side):
        return 0, overridden_stop, overridden_target, "wca.sizing.manual_override_invalid_prices"
    if _reward_risk_ratio(entry_price, overridden_target, overridden_stop, cost_per_share) < minimum_reward_risk:
        return 0, overridden_stop, overridden_target, "wca.sizing.manual_override_reward_risk_not_met"
    return overridden_quantity, overridden_stop, overridden_target, "wca.sizing.manual_override_revalidated"


def _stop_and_target_are_valid(entry_price: float, stop_price: float, target_price: float, side: str) -> bool:
    if side == WcaSide.BUY.value:
        return stop_price < entry_price < target_price
    if side == WcaSide.SELL.value:
        return target_price < entry_price < stop_price
    return False


def _minimum_reward_risk(context: WcaSizingContext, effective_settings: WcaEffectiveSettings) -> float:
    if context.minimum_reward_risk is not None:
        return context.minimum_reward_risk
    return effective_settings.final_take_profit_r


def _same_side_position_exists(context: WcaSizingContext, side: str) -> bool:
    return context.current_position_quantity > 0 and _side_value(context.current_position_side) == side


def _sizing_result(
    *,
    context: WcaSizingContext,
    side: str,
    entry_price: float,
    stop_distance: float,
    stop_price: float,
    target_price: float,
    spread: float,
    estimated_costs: float,
    minimum_reward_risk: float,
    reward_risk_ratio: float,
    risk_dollars: float,
    stop_risk_dollars: float,
    caps: dict[str, float],
    final_quantity: int,
    limiting_factor: str,
    reason_codes: tuple[str, ...],
) -> WcaSizingResult:
    return WcaSizingResult(
        final_quantity=final_quantity,
        risk_dollars=round(risk_dollars, 10),
        stop_distance=round(stop_distance, 10),
        shares_by_risk=round(caps["risk_based"], 10),
        shares_by_order=round(caps["order_allocation"], 10),
        shares_by_capital=round(caps["maximum_position"], 10),
        shares_by_buying_power=round(caps["buying_power"], 10),
        shares_by_liquidity=round(caps["liquidity_participation"], 10),
        limiting_factor=limiting_factor,
        blocked_reason="" if final_quantity > 0 else limiting_factor,
        side=side,
        entry_price=round(entry_price, 10),
        stop_price=stop_price,
        target_price=target_price,
        spread=round(spread, 10),
        estimated_costs=round(estimated_costs, 10),
        minimum_reward_risk=round(minimum_reward_risk, 10),
        reward_risk_ratio=round(reward_risk_ratio, 10),
        approved_risk_budget=context.approved_risk_budget,
        stop_risk_dollars=round(stop_risk_dollars, 10),
        shares_by_maximum_shares=round(caps["maximum_shares"], 10),
        shares_by_global_gate=round(caps["global_gate"], 10),
        reason_codes=reason_codes,
    )


def _zero_sized_order(
    context: WcaSizingContext,
    effective_settings: WcaEffectiveSettings,
    side: str,
    spread: float,
    limiting_factor: str,
    reason_codes: tuple[str, ...],
) -> WcaSizedOrder:
    sizing = WcaSizingResult(
        final_quantity=0,
        risk_dollars=0,
        stop_distance=0,
        shares_by_risk=0,
        shares_by_order=0,
        shares_by_capital=0,
        shares_by_buying_power=0,
        shares_by_liquidity=0,
        limiting_factor=limiting_factor,
        blocked_reason=limiting_factor,
        side=side,
        entry_price=max(0, context.price),
        spread=max(0, spread),
        estimated_costs=max(0, effective_settings.final_assumed_slippage_per_share),
        minimum_reward_risk=_minimum_reward_risk(context, effective_settings),
        reason_codes=reason_codes,
    )
    return WcaSizedOrder(sizing=sizing, proposed_order=None)


def _proposed_order(context: WcaSizingContext, side: str, sizing: WcaSizingResult) -> ProposedOrder | None:
    if sizing.final_quantity <= 0:
        return None
    return ProposedOrder(
        decision_id=context.decision_id,
        order_intent_id=context.order_intent_id,
        symbol=context.symbol,
        side=side,
        quantity=sizing.final_quantity,
        trigger_price=sizing.entry_price,
        limit_price=sizing.entry_price,
        stop_price=sizing.stop_price,
        target_price=sizing.target_price,
        status=WcaOrderStatus.PROPOSED,
        reason_codes=(WCA_SIZING_VERSION, *sizing.reason_codes),
    )


def _side_value(side: WcaSide | str | None) -> str:
    if isinstance(side, WcaSide):
        return side.value
    return str(side or "")


def _positive_number(value: float) -> bool:
    return isfinite(value) and value > 0


__all__ = [
    "WCA_SIZING_INPUT_IDS",
    "WCA_SIZING_INPUT_INVENTORY",
    "WCA_SIZING_VERSION",
    "WcaManualSizingOverride",
    "WcaSizingInputDefinition",
    "WcaSizedOrder",
    "WcaSizingContext",
    "WcaSizingResult",
    "size_wca_order",
    "tighten_protective_stop",
]
