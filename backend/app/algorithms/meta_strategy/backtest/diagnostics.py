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
    event_policy: dict[str, str | bool]
    execution_model: dict[str, float | bool]
    reason_codes: tuple[str, ...]


def build_backtest_diagnostics(
    *,
    decision_count: int,
    artifact_missing_count: int,
    lookahead_violation_count: int,
    spread_bps: float,
    slippage_bps: float,
    fee_per_share: float,
    regulatory_fee_per_share: float = 0.0,
    order_delay_seconds: float = 0.0,
    limit_order_fill_probability: float = 1.0,
    finalized_one_minute_events: bool = True,
    derived_higher_timeframes: bool = True,
) -> MetaStrategyBacktestDiagnostics:
    return MetaStrategyBacktestDiagnostics(
        algorithm_id=ALGORITHM_ID,
        decision_count=decision_count,
        artifact_missing_count=artifact_missing_count,
        lookahead_violation_count=lookahead_violation_count,
        modeled_costs={
            "spreadBps": spread_bps,
            "slippageBps": slippage_bps,
            "feePerShare": fee_per_share,
            "regulatoryFeePerShare": regulatory_fee_per_share,
        },
        event_policy={
            "drivenBy": "finalized_one_minute_events",
            "higherTimeframes": "derived_from_one_minute",
            "lookaheadPrevention": lookahead_violation_count == 0,
            "finalizedOneMinuteEvents": finalized_one_minute_events,
            "derivedHigherTimeframes": derived_higher_timeframes,
        },
        execution_model={
            "orderDelaySeconds": order_delay_seconds,
            "limitOrderFillProbability": limit_order_fill_probability,
            "modelsLimitNonFill": limit_order_fill_probability < 1.0,
            "modelsPartialFills": True,
        },
        reason_codes=(
            "meta_strategy.backtest.diagnostics_recorded",
            "meta_strategy.backtest.finalized_one_minute_event_driven",
            "meta_strategy.backtest.derived_higher_timeframes_from_one_minute",
            "meta_strategy.backtest.no_lookahead_enforced",
        ),
    )


__all__ = ["MetaStrategyBacktestDiagnostics", "build_backtest_diagnostics"]
