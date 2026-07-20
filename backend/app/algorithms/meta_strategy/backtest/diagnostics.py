"""Backtest diagnostics for Meta-Strategy simulations."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID


@dataclass(frozen=True)
class MetaStrategyBacktestDiagnostics:
    algorithm_id: str
    decision_count: int
    artifact_missing_count: int
    lookahead_violation_count: int
    modeled_costs: dict[str, float]
    reason_codes: tuple[str, ...]


def build_backtest_diagnostics(
    *,
    decision_count: int,
    artifact_missing_count: int,
    lookahead_violation_count: int,
    spread_bps: float,
    slippage_bps: float,
    fee_per_share: float,
) -> MetaStrategyBacktestDiagnostics:
    return MetaStrategyBacktestDiagnostics(
        algorithm_id=ALGORITHM_ID,
        decision_count=decision_count,
        artifact_missing_count=artifact_missing_count,
        lookahead_violation_count=lookahead_violation_count,
        modeled_costs={"spreadBps": spread_bps, "slippageBps": slippage_bps, "feePerShare": fee_per_share},
        reason_codes=("meta_strategy.backtest.diagnostics_recorded",),
    )


__all__ = ["MetaStrategyBacktestDiagnostics", "build_backtest_diagnostics"]
