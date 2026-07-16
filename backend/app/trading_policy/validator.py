from __future__ import annotations

from datetime import datetime

from backend.app.domain.models import AccountRiskState, HardRiskLimits, Signal, TradeCandidate


def policy_validation_errors(
    *,
    candidate: TradeCandidate,
    account: AccountRiskState,
    hard_limits: HardRiskLimits,
    now: datetime,
    quantity: int,
    approved_risk_dollars: float,
    maximum_notional: float,
) -> list[str]:
    errors: list[str] = []
    if candidate.signal == Signal.HOLD.value:
        errors.append("policy.candidate_hold")
    if account.tradesToday >= hard_limits.maximumTradesPerDay:
        errors.append("policy.max_trades_per_day")
    if now.time() >= hard_limits.newEntryCutoff:
        errors.append("policy.new_entry_cutoff")
    if approved_risk_dollars <= 0:
        errors.append("policy.no_approved_risk")
    if maximum_notional <= 0:
        errors.append("policy.no_notional_capacity")
    if quantity <= 0:
        errors.append("policy.zero_quantity")
    return errors
