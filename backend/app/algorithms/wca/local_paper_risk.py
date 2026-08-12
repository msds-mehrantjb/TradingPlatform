"""WCA-local paper risk checks for the isolated paper account."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID, WcaDecision, WcaSide
from backend.app.algorithms.wca.local_paper_account import WcaLocalPaperAccountSnapshot
from backend.app.algorithms.wca.paper_broker import WcaPaperBrokerOrderRequest
from backend.app.algorithms.wca.strategies.indicators import eastern_minutes


WCA_LOCAL_PAPER_RISK_VERSION = "wca_local_paper_risk_v1"
_TERMINAL_LOCAL_ORDER_STATUSES = {"FILLED", "REJECTED", "CANCELLED", "CANCELED", "RECONCILED"}


@dataclass(frozen=True)
class WcaLocalPaperRiskPolicy:
    max_daily_loss: float | None = None
    max_daily_loss_percent: float | None = None
    max_position_percent: float | None = None
    base_risk_percent: float = 1.0
    maximum_shares: int | None = None
    maximum_open_risk: float | None = None
    maximum_open_risk_percent: float | None = None
    max_daily_trades: int | None = None
    allow_position_increase: bool = False
    max_spread_percent: float | None = None
    average_one_minute_volume: float | None = None
    max_participation_percent: float | None = None
    expected_net_edge: float | None = None
    minimum_expected_edge: float | None = None
    confidence_size_multiplier: float = 1.0
    edge_size_multiplier: float = 1.0
    dynamic_profile_multiplier: float = 1.0
    protective_target_required: bool = True
    end_of_session_entry_cutoff_minutes: int | None = None

    @classmethod
    def from_decision(cls, decision: WcaDecision | None) -> "WcaLocalPaperRiskPolicy":
        if decision is None or decision.effective_settings is None:
            return cls()
        settings = decision.effective_settings
        baseline = settings.baseline
        max_daily_loss = settings.final_max_daily_loss_dollars
        if max_daily_loss is None:
            max_daily_loss = None
        return cls(
            max_daily_loss=max_daily_loss,
            max_daily_loss_percent=settings.final_max_daily_loss_percent,
            max_position_percent=settings.final_max_position_percent,
            base_risk_percent=baseline.base_risk_percent,
            maximum_shares=settings.final_max_allowed_shares or None,
            maximum_open_risk=None,
            maximum_open_risk_percent=settings.final_risk_percent,
            max_daily_trades=settings.final_max_daily_trades,
            allow_position_increase=bool(getattr(settings, "final_pyramiding_enabled", False)),
            max_spread_percent=settings.final_max_spread_percent,
            average_one_minute_volume=_average_one_minute_volume(decision),
            max_participation_percent=settings.final_max_participation_percent,
            expected_net_edge=(decision.cost_estimate.conservative_net_edge_per_share if decision.cost_estimate is not None else None),
            minimum_expected_edge=settings.final_minimum_net_edge_per_share,
            confidence_size_multiplier=max(abs(decision.aggregation.normalized_net_score), 0.01),
            edge_size_multiplier=max(decision.aggregation.winner_edge, 0.01),
            dynamic_profile_multiplier=settings.risk_multiplier,
            protective_target_required=True,
            end_of_session_entry_cutoff_minutes=settings.final_entry_cutoff_minutes,
        )


@dataclass(frozen=True)
class WcaLocalPaperRiskContext:
    account_snapshot: WcaLocalPaperAccountSnapshot
    request: WcaPaperBrokerOrderRequest
    decision: WcaDecision | None = None
    policy: WcaLocalPaperRiskPolicy | None = None
    evaluated_at: datetime | None = None
    average_one_minute_volume: float | None = None
    expected_net_edge: float | None = None
    minimum_expected_edge: float | None = None
    allow_position_increase: bool | None = None


@dataclass(frozen=True)
class WcaLocalPaperRiskDecision:
    permitted: bool
    reason_codes: tuple[str, ...]
    risk_budget_dollars: float
    order_risk_dollars: float
    max_position_value: float | None
    maximum_participation_quantity: float | None
    local_equity: float
    local_buying_power: float
    local_cash: float


class WcaLocalPaperRiskManager:
    """Fail-closed checks that use only WCA-owned local paper account state."""

    def evaluate_order(self, context: WcaLocalPaperRiskContext) -> WcaLocalPaperRiskDecision:
        account = context.account_snapshot
        request = context.request
        decision = context.decision
        policy = _merge_policy(context.policy or WcaLocalPaperRiskPolicy.from_decision(decision), context)
        evaluated_at = _evaluation_timestamp(context)
        reasons: list[str] = [WCA_LOCAL_PAPER_RISK_VERSION]

        if account.algorithm_id != WCA_ALGORITHM_ID or request.algorithm_id != WCA_ALGORITHM_ID:
            reasons.append("wca.local_risk.algorithm_mismatch")
        if account.account_id != request.account_id:
            reasons.append("wca.local_risk.account_mismatch")

        local_equity = max(0.0, float(account.equity))
        local_buying_power = max(0.0, float(account.buying_power))
        local_cash = max(0.0, float(account.cash))
        quantity = int(request.quantity)
        entry_price = _entry_price(request)
        notional = quantity * entry_price
        is_exit = _is_risk_reducing_exit(account, request)
        position = _position_for_symbol(account, request.symbol)
        open_order = _matching_open_order(account, request)
        entry_stop = _entry_stop_price(request, decision)
        entry_target = _entry_target_price(request, decision)
        order_risk = quantity * abs(entry_price - entry_stop) if entry_stop is not None else 0.0
        risk_budget = self.risk_budget_dollars(local_equity=local_equity, policy=policy)
        max_daily_loss = policy.max_daily_loss if policy.max_daily_loss is not None else (local_equity * (policy.max_daily_loss_percent / 100.0) if policy.max_daily_loss_percent is not None else None)
        max_open_risk = policy.maximum_open_risk if policy.maximum_open_risk is not None else (local_equity * (policy.maximum_open_risk_percent / 100.0) if policy.maximum_open_risk_percent is not None else None)
        max_position_value = local_equity * (policy.max_position_percent / 100.0) if policy.max_position_percent is not None else None
        max_participation_quantity = (
            policy.average_one_minute_volume * (policy.max_participation_percent / 100.0)
            if policy.average_one_minute_volume is not None and policy.max_participation_percent is not None
            else None
        )

        if not _positive(local_equity):
            reasons.append("wca.local_risk.local_equity_unavailable")
        if quantity <= 0 or not _positive(entry_price):
            reasons.append("wca.local_risk.invalid_order")

        if open_order is not None:
            reasons.append("wca.local_risk.duplicate_order")

        if not is_exit:
            if max_daily_loss is not None and account.daily_loss >= max_daily_loss - 1e-9:
                reasons.append("wca.local_risk.max_daily_loss_exceeded")
            if policy.max_daily_trades is not None and account.trades_today >= policy.max_daily_trades:
                reasons.append("wca.local_risk.max_daily_trades_exceeded")
            if str(account.circuit_breaker_state or "").lower() not in {"", "closed"}:
                reasons.append("wca.local_risk.circuit_breaker_open")
            if account.cooldown_until is not None and evaluated_at < account.cooldown_until.astimezone(timezone.utc):
                reasons.append("wca.local_risk.cooldown_active")
            if policy.end_of_session_entry_cutoff_minutes is not None and eastern_minutes(evaluated_at) > policy.end_of_session_entry_cutoff_minutes:
                reasons.append("wca.local_risk.end_of_session_entry_blocked")
            if position is not None and position.quantity > 0:
                position_side = str(position.side).upper()
                request_side = _side_value(request.side)
                if position_side == request_side and not policy.allow_position_increase:
                    reasons.append("wca.local_risk.position_increase_blocked")
                else:
                    reasons.append("wca.local_risk.duplicate_position")
            if not policy.allow_position_increase and _same_side_position(account, request):
                if "wca.local_risk.position_increase_blocked" not in reasons:
                    reasons.append("wca.local_risk.position_increase_blocked")
            if policy.maximum_shares is not None and quantity > policy.maximum_shares:
                reasons.append("wca.local_risk.maximum_shares_exceeded")
            if notional > local_buying_power + 1e-6:
                reasons.append("wca.local_risk.buying_power_exceeded")
            if notional > local_cash + 1e-6:
                reasons.append("wca.local_risk.available_cash_exceeded")
            if max_position_value is not None:
                existing = max(0, position.quantity if position is not None else 0) * entry_price
                if existing + notional > max_position_value + 1e-6:
                    reasons.append("wca.local_risk.max_position_percent_exceeded")
            if entry_stop is None:
                reasons.append("wca.local_risk.protective_stop_required")
            elif order_risk > risk_budget + 1e-6:
                reasons.append("wca.local_risk.base_risk_percent_exceeded")
            if max_open_risk is not None and account.reserved_risk + order_risk > max_open_risk + 1e-6:
                reasons.append("wca.local_risk.maximum_open_risk_exceeded")
            if policy.protective_target_required and entry_target is None:
                reasons.append("wca.local_risk.protective_target_required")
            spread_percent = _spread_percent(request, decision)
            if policy.max_spread_percent is not None and spread_percent is not None and spread_percent > policy.max_spread_percent + 1e-9:
                reasons.append("wca.local_risk.maximum_spread_exceeded")
            if max_participation_quantity is not None and quantity > max_participation_quantity + 1e-9:
                reasons.append("wca.local_risk.participation_limit_exceeded")
            if policy.expected_net_edge is not None and policy.minimum_expected_edge is not None:
                if policy.expected_net_edge <= policy.minimum_expected_edge:
                    reasons.append("wca.local_risk.minimum_expected_edge_not_met")

        blocking = tuple(code for code in reasons if code != WCA_LOCAL_PAPER_RISK_VERSION)
        if not blocking:
            reasons.append("wca.local_risk.passed")
        return WcaLocalPaperRiskDecision(
            permitted=not blocking,
            reason_codes=tuple(dict.fromkeys(reasons)),
            risk_budget_dollars=round(risk_budget, 10),
            order_risk_dollars=round(order_risk, 10),
            max_position_value=round(max_position_value, 10) if max_position_value is not None else None,
            maximum_participation_quantity=round(max_participation_quantity, 10) if max_participation_quantity is not None else None,
            local_equity=local_equity,
            local_buying_power=local_buying_power,
            local_cash=local_cash,
        )

    def risk_budget_dollars(self, *, local_equity: float, policy: WcaLocalPaperRiskPolicy) -> float:
        multiplier = min(policy.confidence_size_multiplier, policy.edge_size_multiplier) * policy.dynamic_profile_multiplier
        return max(0.0, float(local_equity) * (policy.base_risk_percent / 100.0) * max(0.0, multiplier))


def _evaluation_timestamp(context: WcaLocalPaperRiskContext) -> datetime:
    decision = context.decision
    timestamp = context.evaluated_at
    if timestamp is None and decision is not None:
        timestamp = getattr(decision, "data_timestamp", None) or getattr(decision, "decision_timestamp", None)
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _merge_policy(policy: WcaLocalPaperRiskPolicy, context: WcaLocalPaperRiskContext) -> WcaLocalPaperRiskPolicy:
    return WcaLocalPaperRiskPolicy(
        max_daily_loss=policy.max_daily_loss,
        max_daily_loss_percent=policy.max_daily_loss_percent,
        max_position_percent=policy.max_position_percent,
        base_risk_percent=policy.base_risk_percent,
        maximum_shares=policy.maximum_shares,
        maximum_open_risk=policy.maximum_open_risk,
        maximum_open_risk_percent=policy.maximum_open_risk_percent,
        max_daily_trades=policy.max_daily_trades,
        allow_position_increase=policy.allow_position_increase if context.allow_position_increase is None else bool(context.allow_position_increase),
        max_spread_percent=policy.max_spread_percent,
        average_one_minute_volume=policy.average_one_minute_volume if context.average_one_minute_volume is None else context.average_one_minute_volume,
        max_participation_percent=policy.max_participation_percent,
        expected_net_edge=policy.expected_net_edge if context.expected_net_edge is None else context.expected_net_edge,
        minimum_expected_edge=policy.minimum_expected_edge if context.minimum_expected_edge is None else context.minimum_expected_edge,
        confidence_size_multiplier=policy.confidence_size_multiplier,
        edge_size_multiplier=policy.edge_size_multiplier,
        dynamic_profile_multiplier=policy.dynamic_profile_multiplier,
        protective_target_required=policy.protective_target_required,
        end_of_session_entry_cutoff_minutes=policy.end_of_session_entry_cutoff_minutes,
    )


def _position_for_symbol(account: WcaLocalPaperAccountSnapshot, symbol: str):
    selected = str(symbol or "").upper()
    for position in account.positions:
        if position.symbol.upper() == selected and position.quantity > 0:
            return position
    return None


def _same_side_position(account: WcaLocalPaperAccountSnapshot, request: WcaPaperBrokerOrderRequest) -> bool:
    position = _position_for_symbol(account, request.symbol)
    return bool(position is not None and str(position.side).upper() == _side_value(request.side))


def _matching_open_order(account: WcaLocalPaperAccountSnapshot, request: WcaPaperBrokerOrderRequest):
    selected_symbol = str(request.symbol or "").upper()
    for order in account.open_orders:
        status = str(order.status or "").upper()
        if status in _TERMINAL_LOCAL_ORDER_STATUSES:
            continue
        if order.symbol.upper() != selected_symbol:
            continue
        if order.client_order_id == request.client_order_id or order.idempotency_key == request.idempotency_key or order.order_intent_id == request.order_intent_id:
            continue
        if not _is_protective_client_order_id(order.client_order_id) and not _is_protective_client_order_id(request.client_order_id):
            return order
    return None


def _is_risk_reducing_exit(account: WcaLocalPaperAccountSnapshot, request: WcaPaperBrokerOrderRequest) -> bool:
    if _is_protective_client_order_id(request.client_order_id):
        return True
    position = _position_for_symbol(account, request.symbol)
    if position is None:
        return False
    return str(position.side).upper() != _side_value(request.side)


def _is_protective_client_order_id(client_order_id: str) -> bool:
    return str(client_order_id or "").startswith("wca-protection-")


def _entry_price(request: WcaPaperBrokerOrderRequest) -> float:
    return max(0.0, float(request.limit_price or 0.0))


def _entry_stop_price(request: WcaPaperBrokerOrderRequest, decision: WcaDecision | None) -> float | None:
    if decision is not None and decision.proposed_order is not None and decision.proposed_order.stop_price is not None:
        return float(decision.proposed_order.stop_price)
    if request.stop_price is not None:
        return float(request.stop_price)
    return None


def _entry_target_price(request: WcaPaperBrokerOrderRequest, decision: WcaDecision | None) -> float | None:
    if decision is not None and decision.proposed_order is not None and decision.proposed_order.target_price is not None:
        return float(decision.proposed_order.target_price)
    if request.target_price is not None:
        return float(request.target_price)
    return None


def _spread_percent(request: WcaPaperBrokerOrderRequest, decision: WcaDecision | None) -> float | None:
    if decision is not None and decision.sizing.entry_price > 0 and decision.sizing.spread >= 0:
        return decision.sizing.spread / decision.sizing.entry_price * 100.0
    return None


def _average_one_minute_volume(decision: WcaDecision) -> float | None:
    candles = tuple(decision.market_snapshot.candles or ())
    if not candles:
        return None
    selected = candles[-20:]
    return sum(float(candle.volume) for candle in selected) / len(selected)


def _side_value(side: WcaSide | str) -> str:
    return side.value if hasattr(side, "value") else str(side).upper()


def _positive(value: Any) -> bool:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return isfinite(parsed) and parsed > 0


__all__ = [
    "WCA_LOCAL_PAPER_RISK_VERSION",
    "WcaLocalPaperRiskContext",
    "WcaLocalPaperRiskDecision",
    "WcaLocalPaperRiskManager",
    "WcaLocalPaperRiskPolicy",
]
