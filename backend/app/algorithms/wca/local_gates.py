"""WCA-local safety gate engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.app.algorithms.wca.contracts import WcaAggregationResult, WcaEffectiveSettings, WcaGateStatus, WcaLocalGateResult, WcaSide
from backend.app.algorithms.wca.strategies.indicators import eastern_minutes


@dataclass(frozen=True)
class WcaLocalGateContext:
    evaluation_timestamp: datetime
    trades_today: int = 0
    cooldown_until: datetime | None = None
    has_open_wca_position: bool = False
    realized_daily_loss: float = 0
    allocated_daily_loss_budget: float = 0
    planned_risk: float = 0
    remaining_allocated_risk_budget: float = 0
    is_risk_reducing_exit: bool = False


@dataclass(frozen=True)
class WcaLocalGateConfig:
    maximum_family_concentration: float = 0.45
    minimum_winner_edge: float = 0.05
    minimum_expected_value_after_costs: float = 0.0


@dataclass(frozen=True)
class WcaLocalGateDefinition:
    gate_id: str
    name: str
    responsibility: str


WCA_LOCAL_GATE_INVENTORY: tuple[WcaLocalGateDefinition, ...] = (
    WcaLocalGateDefinition("minimum_active_strategies", "Minimum active strategies", "Require enough active WCA primary voters before a new entry."),
    WcaLocalGateDefinition("minimum_directional_agreement", "Minimum directional agreement", "Require enough agreement on the winning WCA side."),
    WcaLocalGateDefinition("minimum_average_calibrated_confidence", "Minimum average calibrated confidence", "Require sufficient calibrated confidence for the winning WCA side."),
    WcaLocalGateDefinition("minimum_aggregate_score", "Minimum aggregate score", "Require a minimum absolute WCA aggregate score."),
    WcaLocalGateDefinition("minimum_winner_edge", "Minimum winner edge", "Require separation between the winning side and the opposing side."),
    WcaLocalGateDefinition("minimum_expectancy_after_costs", "Minimum expectancy after costs", "Require nonnegative or configured WCA expectancy after costs."),
    WcaLocalGateDefinition("maximum_strategy_family_concentration", "Maximum strategy-family concentration", "Limit overconcentration in one WCA strategy family."),
    WcaLocalGateDefinition("strategy_health_eligibility", "Strategy-health eligibility", "Track unhealthy or invalid WCA strategies excluded from aggregation."),
    WcaLocalGateDefinition("wca_trade_count_limit", "WCA trade-count limit", "Enforce the WCA daily trade-count limit."),
    WcaLocalGateDefinition("wca_cooldown", "WCA cooldown", "Block new WCA entries until the WCA cooldown expires."),
    WcaLocalGateDefinition("wca_pyramiding_restrictions", "WCA pyramiding restrictions", "Prevent adding to WCA positions unless WCA pyramiding is enabled."),
    WcaLocalGateDefinition("wca_daily_loss_allocation", "WCA daily-loss allocation", "Enforce the WCA allocated daily-loss budget."),
    WcaLocalGateDefinition("wca_allocated_risk_budget", "WCA allocated-risk budget", "Require planned WCA risk to fit inside remaining allocated risk."),
    WcaLocalGateDefinition("session_entry_restrictions", "Session entry restrictions", "Block WCA entries after the configured session cutoff."),
    WcaLocalGateDefinition("dynamic_profile_restrictions", "Dynamic-profile restrictions", "Honor WCA dynamic-profile entry blocks and zero-risk profiles."),
)

WCA_LOCAL_GATE_IDS = frozenset(gate.gate_id for gate in WCA_LOCAL_GATE_INVENTORY)


def evaluate_wca_local_gates(
    *,
    aggregation: WcaAggregationResult,
    effective_settings: WcaEffectiveSettings,
    context: WcaLocalGateContext,
    config: WcaLocalGateConfig = WcaLocalGateConfig(),
) -> tuple[WcaLocalGateResult, ...]:
    if context.is_risk_reducing_exit:
        return (
            _gate(
                "risk_reducing_exit_protection",
                WcaGateStatus.NOT_APPLICABLE,
                False,
                "wca.local_gate.exit_protected",
                "Risk-reducing WCA exits bypass entry-only local gates.",
                True,
                True,
                "info",
            ),
        )
    proposed = aggregation.pre_gate_decision
    if proposed == WcaSide.HOLD.value:
        return (
            _gate("proposed_entry", WcaGateStatus.NOT_APPLICABLE, False, "wca.local_gate.no_entry", "No directional WCA entry is proposed.", proposed, "BUY_OR_SELL"),
        )
    directional_agreement = aggregation.buy_agreement if proposed == WcaSide.BUY.value else aggregation.sell_agreement
    average_confidence = aggregation.buy_average_confidence if proposed == WcaSide.BUY.value else aggregation.sell_average_confidence
    return (
        _min_gate("minimum_active_strategies", aggregation.active_strategy_count, effective_settings.baseline.minimum_active_strategies, "wca.local_gate.minimum_active_strategies"),
        _min_gate("minimum_directional_agreement", directional_agreement, effective_settings.final_minimum_agreement, "wca.local_gate.minimum_directional_agreement"),
        _min_gate("minimum_average_calibrated_confidence", average_confidence, effective_settings.final_minimum_confidence, "wca.local_gate.minimum_average_calibrated_confidence"),
        _min_gate("minimum_aggregate_score", abs(aggregation.normalized_net_score), effective_settings.final_minimum_score, "wca.local_gate.minimum_aggregate_score"),
        _min_gate("minimum_winner_edge", aggregation.winner_edge, config.minimum_winner_edge, "wca.local_gate.minimum_winner_edge"),
        _min_gate("minimum_expectancy_after_costs", aggregation.estimated_expectancy_after_costs, config.minimum_expected_value_after_costs, "wca.local_gate.minimum_expectancy_after_costs"),
        _max_gate("maximum_strategy_family_concentration", aggregation.family_concentration, config.maximum_family_concentration, "wca.local_gate.maximum_strategy_family_concentration"),
        _strategy_health_gate(aggregation),
        _max_gate("wca_trade_count_limit", context.trades_today, effective_settings.final_max_daily_trades - 1, "wca.local_gate.trade_count_limit"),
        _cooldown_gate(context),
        _pyramiding_gate(context, effective_settings),
        _daily_loss_gate(context),
        _risk_budget_gate(context),
        _session_gate(effective_settings, context),
        _dynamic_profile_gate(effective_settings),
    )


def apply_local_gates_to_decision(
    proposed: WcaSide,
    gates: tuple[WcaLocalGateResult, ...],
    *,
    is_risk_reducing_exit: bool = False,
) -> WcaSide:
    if is_risk_reducing_exit:
        return proposed
    if any(gate.status == WcaGateStatus.FAIL.value and gate.blocks_entry for gate in gates):
        return WcaSide.HOLD
    return proposed


def _min_gate(gate_id: str, actual: float | int, required: float | int, reason_code: str) -> WcaLocalGateResult:
    status = WcaGateStatus.PASS if actual >= required else WcaGateStatus.FAIL
    return _gate(gate_id, status, status == WcaGateStatus.FAIL, reason_code, f"{gate_id} must be at least {required}.", actual, required)


def _max_gate(gate_id: str, actual: float | int, maximum: float | int, reason_code: str) -> WcaLocalGateResult:
    status = WcaGateStatus.PASS if actual <= maximum else WcaGateStatus.FAIL
    return _gate(gate_id, status, status == WcaGateStatus.FAIL, reason_code, f"{gate_id} must be no more than {maximum}.", actual, maximum)


def _strategy_health_gate(aggregation: WcaAggregationResult) -> WcaLocalGateResult:
    unhealthy = tuple(exclusion for exclusion in aggregation.exclusions if "unhealthy" in " ".join(exclusion.reason_codes) or "invalid" in " ".join(exclusion.reason_codes))
    status = WcaGateStatus.PASS if not unhealthy else WcaGateStatus.WARN
    return _gate("strategy_health_eligibility", status, False, "wca.local_gate.strategy_health_eligibility", "Unhealthy strategies are excluded from aggregation.", len(unhealthy), 0, "warn" if unhealthy else "info")


def _cooldown_gate(context: WcaLocalGateContext) -> WcaLocalGateResult:
    blocked = context.cooldown_until is not None and context.evaluation_timestamp < context.cooldown_until
    return _gate("wca_cooldown", WcaGateStatus.FAIL if blocked else WcaGateStatus.PASS, blocked, "wca.local_gate.cooldown", "WCA cooldown must have expired.", context.evaluation_timestamp.isoformat(), context.cooldown_until.isoformat() if context.cooldown_until else "expired")


def _pyramiding_gate(context: WcaLocalGateContext, settings: WcaEffectiveSettings) -> WcaLocalGateResult:
    blocked = context.has_open_wca_position and not settings.final_pyramiding_enabled
    return _gate("wca_pyramiding_restrictions", WcaGateStatus.FAIL if blocked else WcaGateStatus.PASS, blocked, "wca.local_gate.pyramiding_restrictions", "WCA pyramiding must be enabled before adding to a WCA position.", context.has_open_wca_position, settings.final_pyramiding_enabled)


def _daily_loss_gate(context: WcaLocalGateContext) -> WcaLocalGateResult:
    if context.allocated_daily_loss_budget <= 0:
        return _gate("wca_daily_loss_allocation", WcaGateStatus.NOT_APPLICABLE, False, "wca.local_gate.daily_loss_allocation.not_configured", "No WCA allocated daily-loss budget is configured.", context.realized_daily_loss, context.allocated_daily_loss_budget)
    blocked = context.realized_daily_loss >= context.allocated_daily_loss_budget
    return _gate("wca_daily_loss_allocation", WcaGateStatus.FAIL if blocked else WcaGateStatus.PASS, blocked, "wca.local_gate.daily_loss_allocation", "WCA realized daily loss must remain below its allocated budget.", context.realized_daily_loss, context.allocated_daily_loss_budget)


def _risk_budget_gate(context: WcaLocalGateContext) -> WcaLocalGateResult:
    if context.remaining_allocated_risk_budget <= 0:
        return _gate("wca_allocated_risk_budget", WcaGateStatus.NOT_APPLICABLE, False, "wca.local_gate.risk_budget.not_configured", "No WCA allocated risk budget is configured.", context.planned_risk, context.remaining_allocated_risk_budget)
    blocked = context.planned_risk > context.remaining_allocated_risk_budget
    return _gate("wca_allocated_risk_budget", WcaGateStatus.FAIL if blocked else WcaGateStatus.PASS, blocked, "wca.local_gate.risk_budget", "WCA planned risk must fit inside remaining allocated risk budget.", context.planned_risk, context.remaining_allocated_risk_budget)


def _session_gate(settings: WcaEffectiveSettings, context: WcaLocalGateContext) -> WcaLocalGateResult:
    minutes = eastern_minutes(context.evaluation_timestamp)
    blocked = minutes > settings.final_entry_cutoff_minutes
    return _gate("session_entry_restrictions", WcaGateStatus.FAIL if blocked else WcaGateStatus.PASS, blocked, "wca.local_gate.session_entry_restrictions", "WCA entries must occur before the configured entry cutoff.", minutes, settings.final_entry_cutoff_minutes)


def _dynamic_profile_gate(settings: WcaEffectiveSettings) -> WcaLocalGateResult:
    blocked = settings.entries_blocked or settings.final_risk_percent <= 0
    return _gate("dynamic_profile_restrictions", WcaGateStatus.FAIL if blocked else WcaGateStatus.PASS, blocked, "wca.local_gate.dynamic_profile_restrictions", "WCA dynamic profile must allow new entries.", settings.entries_blocked, False)


def _gate(
    gate_id: str,
    status: WcaGateStatus,
    blocks_entry: bool,
    reason_code: str,
    detail: str,
    evaluated_value: float | int | str | bool | None,
    required_value: float | int | str | bool | None,
    severity: str = "error",
) -> WcaLocalGateResult:
    return WcaLocalGateResult(
        gate_id=gate_id,
        status=status,
        blocks_entry=blocks_entry,
        severity=severity if status != WcaGateStatus.PASS else "info",
        reason_code=reason_code,
        detail=detail,
        evaluated_value=evaluated_value,
        required_value=required_value,
        reason_codes=(reason_code,),
        explanation=detail,
    )

__all__ = (
    "WCA_LOCAL_GATE_IDS",
    "WCA_LOCAL_GATE_INVENTORY",
    "WcaLocalGateConfig",
    "WcaLocalGateContext",
    "WcaLocalGateDefinition",
    "WcaLocalGateResult",
    "apply_local_gates_to_decision",
    "evaluate_wca_local_gates",
)
