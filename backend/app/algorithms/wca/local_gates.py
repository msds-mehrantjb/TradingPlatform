"""WCA-local safety gate engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from backend.app.algorithms.wca.configuration import (
    CashAvoidTradingSettings,
    EconomicEventRiskSettings,
    ExtremeVolatilitySettings,
    InvalidOrStaleDataSettings,
    SessionEntryBlockSettings,
    UnsafeLiquiditySettings,
    UnsafeSpreadSettings,
    WcaHardFilterSettings,
)
from backend.app.algorithms.wca.contracts import WcaAggregationResult, WcaEffectiveSettings, WcaGateStatus, WcaLocalGateResult, WcaMarketSnapshot, WcaSide
from backend.app.algorithms.wca.strategies.indicators import atr, average_volume, completed_candles, eastern_minutes


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

WCA_HARD_FILTER_IDS = frozenset(
    {
        "cash_avoid_trading",
        "economic_event_risk",
        "invalid_or_stale_data",
        "unsafe_spread",
        "unsafe_liquidity",
        "extreme_volatility",
        "session_entry_block",
    }
)


def evaluate_wca_hard_filters(
    *,
    snapshot: WcaMarketSnapshot,
    context: WcaLocalGateContext,
    settings: WcaHardFilterSettings | None = None,
) -> tuple[WcaLocalGateResult, ...]:
    settings = settings or WcaHardFilterSettings()
    return (
        _cash_avoid_filter(context, settings.cash_avoid_trading),
        _economic_event_filter(snapshot, settings.economic_event_risk),
        _invalid_or_stale_data_filter(snapshot, settings.invalid_or_stale_data),
        _unsafe_spread_filter(snapshot, settings.unsafe_spread),
        _unsafe_liquidity_filter(snapshot, settings.unsafe_liquidity),
        _extreme_volatility_filter(snapshot, settings.extreme_volatility),
        _session_entry_block_filter(snapshot, settings.session_entry_block),
    )


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


def _cash_avoid_filter(context: WcaLocalGateContext, settings: CashAvoidTradingSettings) -> WcaLocalGateResult:
    if not settings.enabled:
        return _gate("cash_avoid_trading", WcaGateStatus.NOT_APPLICABLE, False, "wca.hard_filter.cash_avoid_trading.disabled", "Cash/avoid-trading filter is disabled in the active WCA configuration.", context.remaining_allocated_risk_budget, settings.minimum_remaining_risk_budget, "info")
    blocked = context.remaining_allocated_risk_budget <= settings.minimum_remaining_risk_budget
    return _gate("cash_avoid_trading", WcaGateStatus.FAIL if blocked else WcaGateStatus.PASS, blocked, "wca.hard_filter.cash_avoid_trading", "Remaining WCA risk budget must permit a new entry.", context.remaining_allocated_risk_budget, settings.minimum_remaining_risk_budget)


def _economic_event_filter(snapshot: WcaMarketSnapshot, settings: EconomicEventRiskSettings) -> WcaLocalGateResult:
    if not settings.enabled:
        return _gate("economic_event_risk", WcaGateStatus.NOT_APPLICABLE, False, "wca.hard_filter.economic_event_risk.disabled", "Economic-event filter is disabled in the active WCA configuration.", False, True, "info")
    matching = tuple(code for code in snapshot.reason_codes if code in settings.blocking_reason_codes)
    blocked = bool(matching)
    return _gate("economic_event_risk", WcaGateStatus.FAIL if blocked else WcaGateStatus.PASS, blocked, "wca.hard_filter.economic_event_risk", "Configured economic-event risk reason codes block new WCA entries.", ",".join(matching) if matching else "none", "none")


def _invalid_or_stale_data_filter(snapshot: WcaMarketSnapshot, settings: InvalidOrStaleDataSettings) -> WcaLocalGateResult:
    if not settings.enabled:
        return _gate("invalid_or_stale_data", WcaGateStatus.NOT_APPLICABLE, False, "wca.hard_filter.invalid_or_stale_data.disabled", "Invalid/stale-data filter is disabled in the active WCA configuration.", snapshot.data_ready, True, "info")
    age = (snapshot.decision_timestamp.astimezone(timezone.utc) - snapshot.data_timestamp.astimezone(timezone.utc)).total_seconds()
    candles = completed_candles(snapshot)
    blocked = not snapshot.data_ready or not candles or age > settings.stale_after_seconds
    reason = "wca.hard_filter.invalid_or_stale_data.stale" if age > settings.stale_after_seconds else "wca.hard_filter.invalid_or_stale_data"
    return _gate("invalid_or_stale_data", WcaGateStatus.FAIL if blocked else WcaGateStatus.PASS, blocked, reason, "WCA entries require data-ready, non-stale completed one-minute bars.", round(age, 4), settings.stale_after_seconds)


def _unsafe_spread_filter(snapshot: WcaMarketSnapshot, settings: UnsafeSpreadSettings) -> WcaLocalGateResult:
    if not settings.enabled:
        return _gate("unsafe_spread", WcaGateStatus.NOT_APPLICABLE, False, "wca.hard_filter.unsafe_spread.disabled", "Unsafe-spread filter is disabled in the active WCA configuration.", 0, settings.maximum_spread_percent, "info")
    if snapshot.quote is None:
        return _gate("unsafe_spread", WcaGateStatus.FAIL, True, "wca.hard_filter.unsafe_spread.missing_quote", "Missing quote blocks new WCA entries while preserving exits.", None, settings.maximum_spread_percent)
    midpoint = max((snapshot.quote.bid + snapshot.quote.ask) / 2.0, 0.01)
    spread_percent = (snapshot.quote.ask - snapshot.quote.bid) / midpoint
    if spread_percent >= settings.maximum_spread_percent:
        return _gate("unsafe_spread", WcaGateStatus.FAIL, True, "wca.hard_filter.unsafe_spread", "Spread exceeds the configured WCA hard ceiling.", round(spread_percent, 6), settings.maximum_spread_percent)
    if spread_percent >= settings.reduction_spread_percent:
        return _gate("unsafe_spread", WcaGateStatus.WARN, False, "wca.hard_filter.unsafe_spread.reduced", "Spread is elevated; WCA risk and quantity are reduced.", round(spread_percent, 6), settings.reduction_spread_percent, "warn", quantity_multiplier=settings.reduction_multiplier, risk_multiplier=settings.reduction_multiplier)
    return _gate("unsafe_spread", WcaGateStatus.PASS, False, "wca.hard_filter.unsafe_spread.pass", "Spread is within WCA limits.", round(spread_percent, 6), settings.maximum_spread_percent)


def _unsafe_liquidity_filter(snapshot: WcaMarketSnapshot, settings: UnsafeLiquiditySettings) -> WcaLocalGateResult:
    if not settings.enabled:
        return _gate("unsafe_liquidity", WcaGateStatus.NOT_APPLICABLE, False, "wca.hard_filter.unsafe_liquidity.disabled", "Unsafe-liquidity filter is disabled in the active WCA configuration.", 0, settings.minimum_average_volume, "info")
    candles = completed_candles(snapshot)
    avg_volume = average_volume(candles, min(20, len(candles))) if candles else 0
    if avg_volume < settings.minimum_average_volume:
        return _gate("unsafe_liquidity", WcaGateStatus.FAIL, True, "wca.hard_filter.unsafe_liquidity", "Average one-minute volume is below the WCA hard floor.", round(avg_volume, 4), settings.minimum_average_volume)
    if avg_volume < settings.reduction_average_volume:
        return _gate("unsafe_liquidity", WcaGateStatus.WARN, False, "wca.hard_filter.unsafe_liquidity.reduced", "Average one-minute volume is thin; WCA risk and quantity are reduced.", round(avg_volume, 4), settings.reduction_average_volume, "warn", quantity_multiplier=settings.reduction_multiplier, risk_multiplier=settings.reduction_multiplier)
    return _gate("unsafe_liquidity", WcaGateStatus.PASS, False, "wca.hard_filter.unsafe_liquidity.pass", "Average one-minute volume is within WCA limits.", round(avg_volume, 4), settings.minimum_average_volume)


def _extreme_volatility_filter(snapshot: WcaMarketSnapshot, settings: ExtremeVolatilitySettings) -> WcaLocalGateResult:
    if not settings.enabled:
        return _gate("extreme_volatility", WcaGateStatus.NOT_APPLICABLE, False, "wca.hard_filter.extreme_volatility.disabled", "Extreme-volatility filter is disabled in the active WCA configuration.", 0, settings.maximum_atr_percent, "info")
    candles = completed_candles(snapshot)
    if len(candles) < settings.atr_period + 1:
        return _gate("extreme_volatility", WcaGateStatus.FAIL, True, "wca.hard_filter.extreme_volatility.insufficient_history", "ATR volatility gate requires completed ATR history.", len(candles), settings.atr_period + 1)
    atr_percent = atr(candles, settings.atr_period) / max(candles[-1].close, 0.01)
    if atr_percent >= settings.maximum_atr_percent:
        return _gate("extreme_volatility", WcaGateStatus.FAIL, True, "wca.hard_filter.extreme_volatility", "ATR volatility exceeds the configured WCA hard ceiling.", round(atr_percent, 6), settings.maximum_atr_percent)
    if atr_percent >= settings.reduction_atr_percent:
        return _gate("extreme_volatility", WcaGateStatus.WARN, False, "wca.hard_filter.extreme_volatility.reduced", "ATR volatility is elevated; WCA risk and quantity are reduced.", round(atr_percent, 6), settings.reduction_atr_percent, "warn", quantity_multiplier=settings.reduction_multiplier, risk_multiplier=settings.reduction_multiplier)
    return _gate("extreme_volatility", WcaGateStatus.PASS, False, "wca.hard_filter.extreme_volatility.pass", "ATR volatility is within WCA limits.", round(atr_percent, 6), settings.maximum_atr_percent)


def _session_entry_block_filter(snapshot: WcaMarketSnapshot, settings: SessionEntryBlockSettings) -> WcaLocalGateResult:
    if not settings.enabled:
        return _gate("session_entry_block", WcaGateStatus.NOT_APPLICABLE, False, "wca.hard_filter.session_entry_block.disabled", "Session-entry filter is disabled in the active WCA configuration.", 0, settings.entry_cutoff_minutes, "info")
    minutes = eastern_minutes(snapshot.data_timestamp)
    blocked = minutes < settings.entry_start_minutes or minutes > settings.entry_cutoff_minutes
    return _gate("session_entry_block", WcaGateStatus.FAIL if blocked else WcaGateStatus.PASS, blocked, "wca.hard_filter.session_entry_block", "WCA entries must be inside the configured entry session.", minutes, settings.entry_cutoff_minutes)


def _gate(
    gate_id: str,
    status: WcaGateStatus,
    blocks_entry: bool,
    reason_code: str,
    detail: str,
    evaluated_value: float | int | str | bool | None,
    required_value: float | int | str | bool | None,
    severity: str = "error",
    *,
    quantity_multiplier: float = 1.0,
    risk_multiplier: float = 1.0,
    exit_allowed: bool = True,
) -> WcaLocalGateResult:
    return WcaLocalGateResult(
        gate_id=gate_id,
        status=status,
        blocks_entry=blocks_entry,
        entry_blocked=blocks_entry,
        quantity_multiplier=quantity_multiplier,
        risk_multiplier=risk_multiplier,
        warning=status == WcaGateStatus.WARN,
        exit_allowed=exit_allowed,
        severity=severity if status != WcaGateStatus.PASS else "info",
        reason_code=reason_code,
        detail=detail,
        evaluated_value=evaluated_value,
        required_value=required_value,
        reason_codes=(reason_code,),
        explanation=detail,
    )

__all__ = (
    "WCA_HARD_FILTER_IDS",
    "WCA_LOCAL_GATE_IDS",
    "WCA_LOCAL_GATE_INVENTORY",
    "WcaLocalGateConfig",
    "WcaLocalGateContext",
    "WcaLocalGateDefinition",
    "WcaLocalGateResult",
    "apply_local_gates_to_decision",
    "evaluate_wca_hard_filters",
    "evaluate_wca_local_gates",
)
