"""Stable paper-trading validation for WCA rollout."""

from __future__ import annotations

from typing import Protocol

from backend.app.algorithms.wca.contracts import (
    WCA_PAPER_STABILITY_VALIDATION_SCHEMA_VERSION,
    WcaPaperStabilityValidationRequest,
    WcaPaperStabilityValidationResult,
)


WCA_PAPER_STABILITY_VALIDATION_VERSION = WCA_PAPER_STABILITY_VALIDATION_SCHEMA_VERSION


class WcaPaperStabilityRepository(Protocol):
    def write_paper_stability_validation(self, result: WcaPaperStabilityValidationResult) -> None:
        ...


def validate_wca_paper_stability(
    request: WcaPaperStabilityValidationRequest,
    *,
    repository: WcaPaperStabilityRepository | None = None,
) -> WcaPaperStabilityValidationResult:
    validation_days = (request.ended_at - request.started_at).total_seconds() / 86_400
    market_conditions = tuple(sorted({decision.market_condition for decision in request.decisions}))
    rejected_entries = sum(1 for decision in request.decisions if decision.rejected)
    total_pnl = round(sum(exit_record.pnl for exit_record in request.exits), 10)
    max_drawdown_percent = _max_drawdown_percent(tuple(point.equity for point in request.equity_curve))
    average_slippage = (
        round(sum(fill.slippage_per_share for fill in request.fills) / len(request.fills), 10)
        if request.fills
        else 0.0
    )
    reconciliation_discrepancies = sum(len(result.discrepancies) for result in request.reconciliation_results)
    blockers = _blocking_reasons(
        request,
        validation_days=validation_days,
        market_conditions=market_conditions,
        max_drawdown_percent=max_drawdown_percent,
        average_slippage=average_slippage,
        reconciliation_discrepancies=reconciliation_discrepancies,
    )
    stable = not blockers
    result = WcaPaperStabilityValidationResult(
        validation_id=request.validation_id,
        validation_version=WCA_PAPER_STABILITY_VALIDATION_VERSION,
        account_id=request.account_id,
        started_at=request.started_at,
        ended_at=request.ended_at,
        validation_days=round(validation_days, 6),
        market_conditions=market_conditions,
        decisions=len(request.decisions),
        rejected_entries=rejected_entries,
        fills=len(request.fills),
        exits=len(request.exits),
        total_pnl=total_pnl,
        max_drawdown_percent=max_drawdown_percent,
        average_slippage_per_share=average_slippage,
        reconciliation_checks=len(request.reconciliation_results),
        reconciliation_discrepancies=reconciliation_discrepancies,
        duplicate_requests=request.duplicate_requests,
        duplicate_preventions=request.duplicate_preventions,
        rollback_tested=request.rollback.tested,
        rollback_restored_safe_state=request.rollback.restored_safe_state,
        paper_trading_stable=stable,
        rollout_phase_passed=stable,
        blocking_reasons=tuple(blockers),
        reason_codes=(
            "wca.paper_stability.validation_recorded",
            "wca.paper_stability.stable" if stable else "wca.paper_stability.blocked",
            *blockers,
        ),
        explanation="WCA paper trading stability was evaluated across decisions, rejections, fills, exits, slippage, P/L, drawdown, reconciliation, duplicate prevention, and rollback evidence.",
    )
    if repository is not None:
        repository.write_paper_stability_validation(result)
    return result


def _blocking_reasons(
    request: WcaPaperStabilityValidationRequest,
    *,
    validation_days: float,
    market_conditions: tuple[str, ...],
    max_drawdown_percent: float,
    average_slippage: float,
    reconciliation_discrepancies: int,
) -> list[str]:
    blockers: list[str] = []
    if validation_days < request.min_validation_days:
        blockers.append("wca.paper_stability.period_too_short")
    if len(market_conditions) < request.min_market_conditions:
        blockers.append("wca.paper_stability.insufficient_market_conditions")
    if not request.decisions:
        blockers.append("wca.paper_stability.no_decisions_tracked")
    if not any(decision.rejected for decision in request.decisions):
        blockers.append("wca.paper_stability.no_rejected_entries_tracked")
    if not request.fills:
        blockers.append("wca.paper_stability.no_fills_tracked")
    if not request.exits:
        blockers.append("wca.paper_stability.no_exits_tracked")
    if not request.equity_curve:
        blockers.append("wca.paper_stability.no_drawdown_tracked")
    elif max_drawdown_percent > request.max_drawdown_percent:
        blockers.append("wca.paper_stability.drawdown_limit_exceeded")
    if not request.fills:
        blockers.append("wca.paper_stability.no_slippage_tracked")
    elif average_slippage > request.max_average_slippage_per_share:
        blockers.append("wca.paper_stability.slippage_limit_exceeded")
    if not request.reconciliation_results:
        blockers.append("wca.paper_stability.no_reconciliation_results")
    elif reconciliation_discrepancies > 0 or any(result.hard_operational_warning for result in request.reconciliation_results):
        blockers.append("wca.paper_stability.reconciliation_not_clean")
    if request.duplicate_requests <= 0:
        blockers.append("wca.paper_stability.no_duplicate_prevention_exercised")
    elif request.duplicate_preventions < request.duplicate_requests:
        blockers.append("wca.paper_stability.duplicate_prevention_failed")
    if not request.rollback.tested:
        blockers.append("wca.paper_stability.rollback_not_tested")
    if not request.rollback.restored_safe_state:
        blockers.append("wca.paper_stability.rollback_safe_state_not_restored")
    return list(dict.fromkeys(blockers))


def _max_drawdown_percent(equity_values: tuple[float, ...]) -> float:
    peak = 0.0
    max_drawdown = 0.0
    for equity in equity_values:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
    return round(max_drawdown, 10)


__all__ = [
    "WCA_PAPER_STABILITY_VALIDATION_VERSION",
    "WcaPaperStabilityRepository",
    "validate_wca_paper_stability",
]
