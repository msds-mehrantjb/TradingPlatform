"""Meta-Strategy-owned local entry gates.

These gates are intentionally local to Meta-Strategy. Global account-risk gates
remain a separate shared layer and are not evaluated here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal


ExecutionMode = Literal["PAPER", "LIVE"]


@dataclass(frozen=True)
class MetaStrategyLocalGateConfig:
    minimum_active_strategies: int = 2
    minimum_independent_families: int = 2
    minimum_deterministic_score: float = 0.50
    minimum_deterministic_edge: float = 0.05
    minimum_calibrated_success_probability: float = 0.52
    maximum_uncertainty: float = 0.45
    maximum_missingness: float = 0.25
    maximum_ood_score: float = 0.70
    minimum_model_health: float = 0.70
    minimum_reward_risk_after_costs: float = 1.00
    maximum_spread_bps: float = 15.0
    minimum_liquidity: float = 50_000.0
    maximum_daily_loss: float = 1_000.0
    maximum_daily_trades: int = 5
    cooldown_seconds: int = 300
    allowed_session_phases: tuple[str, ...] = ("regular", "open", "midday", "power_hour")
    paper_trading_allowed: bool = True
    live_trading_allowed: bool = False
    configuration_hash: str = "meta_strategy_local_gates_v1"

    def __post_init__(self) -> None:
        _reject_non_finite(self)


@dataclass(frozen=True)
class MetaStrategyLocalGateContext:
    timestamp: datetime
    proposed_quantity: int
    active_strategy_count: int
    independent_family_count: int
    deterministic_score: float
    deterministic_edge: float
    calibrated_success_probability: float
    uncertainty: float
    missingness: float
    ood_score: float
    model_health_score: float
    reward_risk_after_costs: float
    spread_bps: float
    liquidity: float
    realized_daily_pnl: float
    daily_trade_count: int
    last_trade_at: datetime | None
    event_blackout: bool
    session_phase: str
    execution_mode: ExecutionMode
    paper_trading_permission: bool = True
    live_trading_permission: bool = False

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("meta_strategy.local_gates.timestamp_must_be_timezone_aware")
        if self.last_trade_at is not None and (self.last_trade_at.tzinfo is None or self.last_trade_at.utcoffset() is None):
            raise ValueError("meta_strategy.local_gates.last_trade_at_must_be_timezone_aware")
        _reject_non_finite(self)


@dataclass(frozen=True)
class MetaStrategyLocalGateResult:
    gate_id: str
    passed: bool
    observed: object
    threshold: object
    reason_code: str


@dataclass(frozen=True)
class MetaStrategyLocalGateEvaluation:
    passed: bool
    proposed_quantity: int
    approved_quantity: int
    gate_results: tuple[MetaStrategyLocalGateResult, ...]
    reason_codes: tuple[str, ...]
    configuration_hash: str
    scope: Literal["LOCAL_META_STRATEGY"] = "LOCAL_META_STRATEGY"
    global_gates_applied: bool = False


def evaluate_meta_strategy_local_gates(
    context: MetaStrategyLocalGateContext,
    *,
    config: MetaStrategyLocalGateConfig | None = None,
) -> MetaStrategyLocalGateEvaluation:
    settings = config or MetaStrategyLocalGateConfig()
    checks = (
        _minimum("minimum_active_strategies", context.active_strategy_count, settings.minimum_active_strategies),
        _minimum("minimum_independent_families", context.independent_family_count, settings.minimum_independent_families),
        _minimum("minimum_deterministic_score", context.deterministic_score, settings.minimum_deterministic_score),
        _minimum("minimum_deterministic_edge", context.deterministic_edge, settings.minimum_deterministic_edge),
        _minimum(
            "minimum_calibrated_success_probability",
            context.calibrated_success_probability,
            settings.minimum_calibrated_success_probability,
        ),
        _maximum("maximum_uncertainty", context.uncertainty, settings.maximum_uncertainty),
        _maximum("maximum_missingness", context.missingness, settings.maximum_missingness),
        _maximum("maximum_ood_score", context.ood_score, settings.maximum_ood_score),
        _minimum("minimum_model_health", context.model_health_score, settings.minimum_model_health),
        _minimum("minimum_reward_risk_after_costs", context.reward_risk_after_costs, settings.minimum_reward_risk_after_costs),
        _maximum("maximum_spread", context.spread_bps, settings.maximum_spread_bps),
        _minimum("minimum_liquidity", context.liquidity, settings.minimum_liquidity),
        _daily_loss_limit(context.realized_daily_pnl, settings.maximum_daily_loss),
        _trade_count_limit(context.daily_trade_count, settings.maximum_daily_trades),
        _cooldown(context.timestamp, context.last_trade_at, settings.cooldown_seconds),
        _boolean_clear("event_blackout", context.event_blackout, reason_suffix="event_blackout_active"),
        _session_restriction(context.session_phase, settings.allowed_session_phases),
        _paper_live_permission(context, settings),
    )
    passed = all(check.passed for check in checks)
    proposed = max(0, int(context.proposed_quantity))
    return MetaStrategyLocalGateEvaluation(
        passed=passed,
        proposed_quantity=proposed,
        approved_quantity=proposed if passed else 0,
        gate_results=checks,
        reason_codes=tuple(check.reason_code for check in checks if not check.passed),
        configuration_hash=settings.configuration_hash,
    )


def _minimum(gate_id: str, observed: float, threshold: float) -> MetaStrategyLocalGateResult:
    passed = float(observed) >= float(threshold)
    return MetaStrategyLocalGateResult(
        gate_id=gate_id,
        passed=passed,
        observed=observed,
        threshold=threshold,
        reason_code=f"meta_strategy.local_gate.{gate_id}_below_minimum",
    )


def _maximum(gate_id: str, observed: float, threshold: float) -> MetaStrategyLocalGateResult:
    passed = float(observed) <= float(threshold)
    return MetaStrategyLocalGateResult(
        gate_id=gate_id,
        passed=passed,
        observed=observed,
        threshold=threshold,
        reason_code=f"meta_strategy.local_gate.{gate_id}_above_maximum",
    )


def _daily_loss_limit(realized_daily_pnl: float, maximum_daily_loss: float) -> MetaStrategyLocalGateResult:
    threshold = -abs(float(maximum_daily_loss))
    passed = float(realized_daily_pnl) > threshold
    return MetaStrategyLocalGateResult(
        gate_id="daily_loss_limit",
        passed=passed,
        observed=realized_daily_pnl,
        threshold=threshold,
        reason_code="meta_strategy.local_gate.daily_loss_limit_reached",
    )


def _trade_count_limit(daily_trade_count: int, maximum_daily_trades: int) -> MetaStrategyLocalGateResult:
    passed = int(daily_trade_count) < int(maximum_daily_trades)
    return MetaStrategyLocalGateResult(
        gate_id="trade_count_limit",
        passed=passed,
        observed=daily_trade_count,
        threshold=maximum_daily_trades,
        reason_code="meta_strategy.local_gate.trade_count_limit_reached",
    )


def _cooldown(timestamp: datetime, last_trade_at: datetime | None, cooldown_seconds: int) -> MetaStrategyLocalGateResult:
    if last_trade_at is None:
        observed: object = None
        passed = True
    else:
        observed = max(0.0, (timestamp.astimezone(UTC) - last_trade_at.astimezone(UTC)).total_seconds())
        passed = float(observed) >= float(cooldown_seconds)
    return MetaStrategyLocalGateResult(
        gate_id="cooldown",
        passed=passed,
        observed=observed,
        threshold=cooldown_seconds,
        reason_code="meta_strategy.local_gate.cooldown_active",
    )


def _boolean_clear(gate_id: str, value: bool, *, reason_suffix: str) -> MetaStrategyLocalGateResult:
    return MetaStrategyLocalGateResult(
        gate_id=gate_id,
        passed=not bool(value),
        observed=bool(value),
        threshold=False,
        reason_code=f"meta_strategy.local_gate.{reason_suffix}",
    )


def _session_restriction(session_phase: str, allowed: tuple[str, ...]) -> MetaStrategyLocalGateResult:
    normalized = str(session_phase).lower()
    allowed_normalized = tuple(str(item).lower() for item in allowed)
    return MetaStrategyLocalGateResult(
        gate_id="session_restriction",
        passed=normalized in allowed_normalized,
        observed=normalized,
        threshold=allowed_normalized,
        reason_code="meta_strategy.local_gate.session_restricted",
    )


def _paper_live_permission(
    context: MetaStrategyLocalGateContext,
    config: MetaStrategyLocalGateConfig,
) -> MetaStrategyLocalGateResult:
    mode = str(context.execution_mode).upper()
    if mode == "PAPER":
        passed = config.paper_trading_allowed and context.paper_trading_permission
        observed = {"mode": mode, "configAllowed": config.paper_trading_allowed, "runtimePermission": context.paper_trading_permission}
    elif mode == "LIVE":
        passed = config.live_trading_allowed and context.live_trading_permission
        observed = {"mode": mode, "configAllowed": config.live_trading_allowed, "runtimePermission": context.live_trading_permission}
    else:
        passed = False
        observed = {"mode": mode, "configAllowed": False, "runtimePermission": False}
    return MetaStrategyLocalGateResult(
        gate_id="paper_live_permission",
        passed=passed,
        observed=observed,
        threshold={"paperAllowed": config.paper_trading_allowed, "liveAllowed": config.live_trading_allowed},
        reason_code="meta_strategy.local_gate.paper_live_permission_denied",
    )


def _reject_non_finite(instance: object) -> None:
    for name, value in vars(instance).items():
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"meta_strategy.local_gates.{name}_must_be_finite")


__all__ = [
    "ExecutionMode",
    "MetaStrategyLocalGateConfig",
    "MetaStrategyLocalGateContext",
    "MetaStrategyLocalGateEvaluation",
    "MetaStrategyLocalGateResult",
    "evaluate_meta_strategy_local_gates",
]
