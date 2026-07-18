"""Shared WCA decision and paper-execution pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.algorithms.wca.aggregation import aggregate_wca
from backend.app.algorithms.wca.confidence import ConfidenceCalibrationConfig, calibrate_evaluations
from backend.app.algorithms.wca.configuration import default_baseline_settings
from backend.app.algorithms.wca.contracts import (
    WcaBaselineSettings,
    WcaDecision,
    WcaDynamicProfile,
    WcaMarketSnapshot,
    WcaMarketStatus,
    WcaSide,
    WcaStrategyEvaluation,
    WcaWeightSnapshot,
)
from backend.app.algorithms.wca.dynamic_profile import resolve_dynamic_profile
from backend.app.algorithms.wca.exits import WcaBacktestOpenPosition, WcaExitEvaluation, evaluate_wca_exit
from backend.app.algorithms.wca.local_gates import WcaLocalGateContext, apply_local_gates_to_decision, evaluate_wca_local_gates
from backend.app.algorithms.wca.market_status import resolve_market_status
from backend.app.algorithms.wca.order_validation import WcaOrderValidationContext, apply_wca_final_order_validation
from backend.app.algorithms.wca.sizing import WcaManualSizingOverride, WcaSizingContext, size_wca_order
from backend.app.algorithms.wca.strategies.indicators import atr
from backend.app.algorithms.wca.strategies.primary_voters import WCA_PRIMARY_VOTERS
from backend.app.algorithms.wca.strategy_registry import WcaStrategy
from backend.app.algorithms.wca.weights import baseline_weight_snapshot


WCA_EXECUTION_PIPELINE_VERSION = "wca_execution_pipeline_v1"
WCA_EXECUTION_PIPELINE_MODULES = (
    "strategy_registry",
    "confidence_calibration",
    "weight_engine",
    "market_status",
    "dynamic_profile",
    "aggregation",
    "local_gates",
    "sizing",
    "order_proposal",
    "order_validation",
    "exits",
)


@dataclass(frozen=True)
class WcaExecutionPipelineInput:
    run_id: str
    decision_id: str
    order_intent_id: str
    snapshot: WcaMarketSnapshot
    configuration_version: str
    baseline: WcaBaselineSettings | None = None
    weight_snapshot: WcaWeightSnapshot | None = None
    previous_market_status: WcaMarketStatus | None = None
    previous_dynamic_profile: WcaDynamicProfile | None = None
    risk_improvement_confirmations: int = 0
    trades_today: int = 0
    open_position: WcaBacktestOpenPosition | None = None
    realized_daily_loss: float = 0.0
    account_equity: float = 100_000.0
    available_buying_power: float = 100_000.0
    allocated_daily_loss_budget: float | None = None
    remaining_allocated_risk_budget: float | None = None
    global_gate_quantity_cap: int | None = 2_147_483_647
    approved_risk_budget: float | None = None
    allow_position_increase: bool = False
    estimated_cost_per_share: float = 0.01
    estimated_expectancy_after_costs: float = 0.01
    manual_sizing_override: WcaManualSizingOverride | None = None
    emergency_exit: bool = False
    session_exit_minutes: int = 15 * 60 + 59


@dataclass(frozen=True)
class WcaExecutionPipelineResult:
    decision: WcaDecision
    market_status: WcaMarketStatus
    dynamic_profile: WcaDynamicProfile
    exit_evaluation: WcaExitEvaluation | None
    risk_improvement_confirmations: int
    called_production_modules: tuple[str, ...] = WCA_EXECUTION_PIPELINE_MODULES


def run_wca_execution_pipeline(
    pipeline_input: WcaExecutionPipelineInput,
    *,
    voters: tuple[WcaStrategy, ...] = WCA_PRIMARY_VOTERS,
    calibration_config: ConfidenceCalibrationConfig = ConfidenceCalibrationConfig(enabled=False),
) -> WcaExecutionPipelineResult:
    """Build one WCA decision through the same pieces used by backtesting."""

    snapshot = pipeline_input.snapshot
    baseline = pipeline_input.baseline or default_baseline_settings()
    weight_snapshot = pipeline_input.weight_snapshot or baseline_weight_snapshot(cutoff=snapshot.decision_timestamp)
    market_status = resolve_market_status(
        snapshot,
        previous_status=pipeline_input.previous_market_status,
        confirmation_count=pipeline_input.risk_improvement_confirmations,
    )
    next_confirmations = (
        pipeline_input.risk_improvement_confirmations + 1
        if "wca.market.hysteresis.improvement_held" in market_status.reason_codes
        else 0
    )
    dynamic_profile = resolve_dynamic_profile(
        baseline=baseline,
        market_status=market_status,
        calculation_timestamp=snapshot.decision_timestamp,
        previous_profile=pipeline_input.previous_dynamic_profile,
        current_drawdown_percent=_drawdown_percent(pipeline_input.realized_daily_loss, pipeline_input.account_equity),
    )
    effective_settings = dynamic_profile.effective_settings
    evaluations = tuple(voter.evaluate(snapshot) for voter in voters)
    evaluations = _apply_weights(
        calibrate_evaluations(evaluations, tables=(), config=calibration_config),
        weight_snapshot,
    )
    provisional = aggregate_wca(evaluations, effective_settings=effective_settings)
    local_gates = evaluate_wca_local_gates(
        aggregation=provisional,
        effective_settings=effective_settings,
        context=WcaLocalGateContext(
            evaluation_timestamp=snapshot.decision_timestamp,
            trades_today=pipeline_input.trades_today,
            has_open_wca_position=pipeline_input.open_position is not None,
            realized_daily_loss=pipeline_input.realized_daily_loss,
            allocated_daily_loss_budget=_budget(
                pipeline_input.allocated_daily_loss_budget,
                pipeline_input.account_equity,
                baseline.max_daily_loss_percent,
            ),
            remaining_allocated_risk_budget=_budget(
                pipeline_input.remaining_allocated_risk_budget,
                pipeline_input.account_equity,
                baseline.base_risk_percent,
            ),
        ),
    )
    post_gate = apply_local_gates_to_decision(provisional.pre_gate_decision, local_gates)
    aggregation = aggregate_wca(
        evaluations,
        effective_settings=effective_settings,
        local_gates=local_gates,
        estimated_expectancy_after_costs=pipeline_input.estimated_expectancy_after_costs,
    ).model_copy(
        update={
            "post_local_gate_decision": post_gate,
            "signal": post_gate,
            "decision_label": _decision_label(post_gate),
        }
    )
    exit_evaluation = (
        evaluate_wca_exit(
            position=pipeline_input.open_position,
            candle=snapshot.candles[-1],
            opposite_signal=aggregation.signal,
            emergency_exit=pipeline_input.emergency_exit,
            session_exit_minutes=pipeline_input.session_exit_minutes,
        )
        if pipeline_input.open_position is not None
        else None
    )
    side = aggregation.post_local_gate_decision
    quote = snapshot.quote
    bid = quote.bid if quote is not None else max(0.01, snapshot.candles[-1].close - 0.01)
    ask = quote.ask if quote is not None else snapshot.candles[-1].close + 0.01
    sized = size_wca_order(
        WcaSizingContext(
            decision_id=pipeline_input.decision_id,
            order_intent_id=pipeline_input.order_intent_id,
            symbol=snapshot.symbol,
            side=side,
            price=snapshot.candles[-1].close,
            atr=max(_atr(snapshot.candles), 0.01),
            bid=bid,
            ask=ask,
            account_equity=max(1.0, pipeline_input.account_equity),
            available_buying_power=max(0.0, pipeline_input.available_buying_power),
            average_one_minute_volume=max(1.0, _average_volume(snapshot.candles)),
            confidence_size_multiplier=max(abs(aggregation.normalized_net_score), 0.01),
            edge_size_multiplier=max(aggregation.winner_edge, 0.01),
            global_gate_quantity_cap=pipeline_input.global_gate_quantity_cap,
            approved_risk_budget=pipeline_input.approved_risk_budget
            if pipeline_input.approved_risk_budget is not None
            else _budget(None, pipeline_input.account_equity, baseline.base_risk_percent),
            current_position_quantity=pipeline_input.open_position.quantity if pipeline_input.open_position else 0,
            current_position_side=pipeline_input.open_position.side if pipeline_input.open_position else None,
            allow_position_increase=pipeline_input.allow_position_increase,
            estimated_cost_per_share=pipeline_input.estimated_cost_per_share,
        ),
        effective_settings,
        manual_override=pipeline_input.manual_sizing_override,
    )
    decision = WcaDecision(
        decision_id=pipeline_input.decision_id,
        configuration_version=pipeline_input.configuration_version,
        weight_version=weight_snapshot.weight_version,
        data_timestamp=snapshot.data_timestamp,
        decision_timestamp=snapshot.decision_timestamp,
        market_snapshot=snapshot,
        market_status=market_status,
        effective_settings=effective_settings,
        aggregation=aggregation,
        local_gates=local_gates,
        sizing=sized.sizing,
        proposed_order=sized.proposed_order,
        reason_codes=(WCA_EXECUTION_PIPELINE_VERSION,),
    )
    decision = apply_wca_final_order_validation(
        decision,
        WcaOrderValidationContext(
            evaluation_timestamp=snapshot.decision_timestamp,
            paper_only_mode=True,
            current_position_quantity=pipeline_input.open_position.quantity if pipeline_input.open_position else 0,
            current_position_side=pipeline_input.open_position.side if pipeline_input.open_position else None,
            allow_position_increase=pipeline_input.allow_position_increase,
            position_owned_by_wca=True,
        ),
    )
    return WcaExecutionPipelineResult(
        decision=decision,
        market_status=market_status,
        dynamic_profile=dynamic_profile,
        exit_evaluation=exit_evaluation,
        risk_improvement_confirmations=next_confirmations,
    )


def _apply_weights(evaluations: tuple[WcaStrategyEvaluation, ...], weight_snapshot: WcaWeightSnapshot) -> tuple[WcaStrategyEvaluation, ...]:
    weighted = []
    for evaluation in evaluations:
        weight = weight_snapshot.weights.get(evaluation.strategy_id, evaluation.effective_weight)
        direction = 1 if evaluation.signal == WcaSide.BUY.value else -1 if evaluation.signal == WcaSide.SELL.value else 0
        weighted.append(
            evaluation.model_copy(
                update={
                    "effective_weight": weight,
                    "contribution": round(direction * weight * evaluation.calibrated_confidence, 10),
                }
            )
        )
    return tuple(weighted)


def _atr(candles: tuple) -> float:
    return atr(candles, min(14, max(1, len(candles) - 1))) or max((candles[-1].high - candles[-1].low), 0.01)


def _average_volume(candles: tuple) -> float:
    selected = candles[-20:]
    return sum(candle.volume for candle in selected) / len(selected) if selected else 1.0


def _budget(value: float | None, account_equity: float, percent: float) -> float:
    return value if value is not None else max(0.0, account_equity * (percent / 100.0))


def _drawdown_percent(realized_daily_loss: float, account_equity: float) -> float:
    return max(0.0, realized_daily_loss / max(1.0, account_equity) * 100.0)


def _decision_label(side: WcaSide | str) -> str:
    value = side.value if isinstance(side, WcaSide) else str(side)
    return "Buy" if value == WcaSide.BUY.value else "Sell" if value == WcaSide.SELL.value else "Hold"


__all__ = [
    "WCA_EXECUTION_PIPELINE_MODULES",
    "WCA_EXECUTION_PIPELINE_VERSION",
    "WcaExecutionPipelineInput",
    "WcaExecutionPipelineResult",
    "run_wca_execution_pipeline",
]
