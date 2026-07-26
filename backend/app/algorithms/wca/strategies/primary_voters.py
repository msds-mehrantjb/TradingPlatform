"""Primary WCA voter construction from the authoritative catalog."""

from __future__ import annotations

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaStrategyEvaluation
from backend.app.algorithms.wca.strategy_registry import StrategyConfig, WcaStrategy, build_wca_primary_voters


WCA_PRIMARY_VOTERS: tuple[WcaStrategy, ...] = build_wca_primary_voters()


def evaluate_all_primary_voters(
    snapshot: WcaMarketSnapshot,
    config: StrategyConfig = StrategyConfig(),
) -> tuple[WcaStrategyEvaluation, ...]:
    return tuple(voter.evaluate(snapshot, config) for voter in WCA_PRIMARY_VOTERS)


__all__ = ("WCA_PRIMARY_VOTERS", "evaluate_all_primary_voters")
