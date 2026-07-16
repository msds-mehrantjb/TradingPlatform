"""Production-parity backtest engine for Weighted Voting."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from math import sqrt

from backend.app.algorithms.weighted_voting.aggregation import aggregate_weighted_signals
from backend.app.algorithms.weighted_voting.backtest.data_validation import WeightedBacktestDataManifest, validate_historical_data
from backend.app.algorithms.weighted_voting.backtest.execution_simulator import (
    WeightedBacktestExecutionCostModel,
    WeightedBacktestPendingOrder,
    conservative_exit_price,
    entry_fee,
    exit_fee,
    simulate_entry_fill,
)
from backend.app.algorithms.weighted_voting.catalog import WEIGHTED_VOTING_CATALOG_VERSION
from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.decision_gates import (
    WeightedFiveMinuteAlignment,
    WeightedVotingGatePipelineResult,
    WeightedVotingLocalGateInputs,
    evaluate_local_decision_gates,
)
from backend.app.algorithms.weighted_voting.dynamic_settings import DynamicSettingsResolver, default_dynamic_envelope, default_hard_limits, default_weighted_settings
from backend.app.algorithms.weighted_voting.entry_policy import WeightedEntryPolicyResult, evaluate_entry_policy
from backend.app.algorithms.weighted_voting.exit_policy import (
    WeightedExitAction,
    WeightedVotingExitDecision,
    WeightedVotingExitInputs,
    WeightedVotingExitLifecycleState,
    evaluate_exit_lifecycle,
    open_exit_lifecycle,
)
from backend.app.algorithms.weighted_voting.market_condition import classify_market_condition
from backend.app.algorithms.weighted_voting.market_snapshot import WeightedVotingCandle, WeightedVotingMarketSnapshot
from backend.app.algorithms.weighted_voting.models import (
    WeightedBacktestRun,
    WeightedBacktestStatus,
    WeightedDecision,
    WeightedDefaultSettings,
    WeightedDynamicEnvelope,
    WeightedEffectiveSettings,
    WeightedHardLimits,
    WeightedMarketCondition,
    WeightedMarketQuality,
    WeightedPositionState,
    WeightedSide,
    WeightedExitReason,
    WeightedStrategyOutcome,
    WeightedWeightState,
    WeightedVotingSignal,
)
from backend.app.algorithms.weighted_voting.position_sizing import WeightedVotingSizingContext, WeightedVotingSizingResult, calculate_weighted_voting_position_size
from backend.app.algorithms.weighted_voting.signal_engine import evaluate_signals
from backend.app.algorithms.weighted_voting.strategies.common import average_true_range, average_volume, eastern_minutes
from backend.app.algorithms.weighted_voting.weight_engine import create_unseeded_equal_weight_state, update_performance_weight_state


WEIGHTED_VOTING_BACKTEST_ENGINE_VERSION = "weighted_voting_backtest_engine_v2"


@dataclass(frozen=True)
class WeightedBacktestEngineConfig:
    symbol: str
    account_equity: float = 100_000.0
    starting_cash: float = 100_000.0
    source: str = "weighted_voting_backtest"
    run_id: str = "weighted-voting-backtest"
    allow_short: bool = True
    session_cutoff_eastern_minutes: int = 945
    force_close_eastern_minutes: int = 959
    decision_start_index: int = 1
    cost_model: WeightedBacktestExecutionCostModel = WeightedBacktestExecutionCostModel()
    weighted_config: WeightedVotingConfig = WeightedVotingConfig()
    calibration_outcomes: tuple[WeightedStrategyOutcome, ...] = ()
    use_performance_weights: bool = False
    use_dynamic_settings: bool = True
    default_settings: WeightedDefaultSettings | None = None
    dynamic_envelope: WeightedDynamicEnvelope | None = None
    hard_limits: WeightedHardLimits | None = None
    initial_weight_state: WeightedWeightState | None = None


@dataclass(frozen=True)
class WeightedBacktestDecisionTrace:
    candle_index: int
    data_timestamp: datetime
    decision: WeightedDecision
    gate_result: WeightedVotingGatePipelineResult
    sizing_result: WeightedVotingSizingResult
    entry_policy: WeightedEntryPolicyResult | None
    market_condition: WeightedMarketCondition
    completed_candle_count: int
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class WeightedBacktestTrade:
    trade_id: str
    side: WeightedSide | str
    quantity: int
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_price: float
    exit_price: float
    gross_pnl: float
    net_pnl: float
    total_costs: float
    entry_fee: float
    exit_fee: float
    favorable_excursion: float
    adverse_excursion: float
    holding_minutes: float
    exit_reason: str
    supporting_strategy_ids: tuple[str, ...]
    regime_label: str
    session_label: str
    partial_fill: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class WeightedBacktestStrategyResult:
    strategy_id: str
    opportunity_count: int
    trade_count: int
    expectancy: float
    profit_factor: float
    maximum_drawdown: float
    favorable_excursion: float
    adverse_excursion: float
    regime_performance: dict[str, float]
    session_performance: dict[str, float]
    correlation: dict[str, float]


@dataclass(frozen=True)
class WeightedBacktestAlgorithmResult:
    net_pnl: float
    return_percent: float
    expectancy: float
    profit_factor: float
    maximum_drawdown: float
    sharpe: float
    sortino: float
    calmar: float
    turnover: float
    average_holding_minutes: float
    long_results: dict[str, float]
    short_results: dict[str, float]
    cost_ratio: float
    gate_rejection_counts: dict[str, int]
    equity_curve: tuple[tuple[datetime, float], ...]
    position_size_distribution: dict[str, float]


@dataclass(frozen=True)
class WeightedBacktestResult:
    run: WeightedBacktestRun
    manifest: WeightedBacktestDataManifest
    decisions: tuple[WeightedBacktestDecisionTrace, ...]
    trades: tuple[WeightedBacktestTrade, ...]
    strategy_results: dict[str, WeightedBacktestStrategyResult]
    algorithm_results: WeightedBacktestAlgorithmResult
    historical_outcomes: tuple[WeightedStrategyOutcome, ...]
    production_function_calls: tuple[str, ...]
    reason_codes: tuple[str, ...]
    explanation: str


@dataclass
class _OpenBacktestPosition:
    lifecycle: WeightedVotingExitLifecycleState
    supporting_strategy_ids: tuple[str, ...]
    regime_label: str
    session_label: str
    entry_fee: float
    entry_spread_cost: float
    entry_slippage_cost: float
    partial_fill: bool
    favorable_excursion: float = 0.0
    adverse_excursion: float = 0.0


def backtest_engine_status() -> dict[str, str]:
    return {
        "version": WEIGHTED_VOTING_BACKTEST_ENGINE_VERSION,
        "status": "implemented",
        "explanation": "Weighted Voting backtests call the same production strategy, condition, weighting, aggregation, gate, settings, sizing, entry, and exit functions used by paper trading.",
    }


def run_weighted_voting_backtest(
    *,
    candles: tuple[WeightedVotingCandle, ...],
    config: WeightedBacktestEngineConfig,
    created_at: datetime,
    data_manifest_hash: str | None = None,
) -> WeightedBacktestResult:
    ordered_candles = tuple(sorted(candles, key=lambda candle: candle.timestamp))
    validation = validate_historical_data(
        symbol=config.symbol,
        candles_by_timeframe={"1m": ordered_candles},
        source=config.source,
        created_at=created_at,
        fill_policy="none",
    )
    manifest = validation.manifest
    if data_manifest_hash is not None and data_manifest_hash != manifest.manifest_hash:
        raise ValueError("supplied data manifest hash does not match immutable Weighted Voting manifest")
    if validation.blocks_run:
        raise ValueError("Weighted Voting backtest data validation blocked the run: " + ",".join(validation.errors))

    production_calls: list[str] = []
    decisions: list[WeightedBacktestDecisionTrace] = []
    trades: list[WeightedBacktestTrade] = []
    outcomes: list[WeightedStrategyOutcome] = []
    gate_rejections: dict[str, int] = defaultdict(int)
    opportunity_counts: dict[str, int] = defaultdict(int)
    strategy_returns: dict[str, list[float]] = defaultdict(list)
    strategy_regime_returns: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    strategy_session_returns: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    strategy_favorable: dict[str, list[float]] = defaultdict(list)
    strategy_adverse: dict[str, list[float]] = defaultdict(list)
    equity_curve: list[tuple[datetime, float]] = []
    position_sizes: list[int] = []
    pending_order: WeightedBacktestPendingOrder | None = None
    pending_context: tuple[WeightedDecision, tuple[WeightedVotingSignal, ...], WeightedVotingSizingResult, WeightedEffectiveSettings, WeightedMarketCondition] | None = None
    open_position: _OpenBacktestPosition | None = None
    previous_condition: WeightedMarketCondition | None = None
    daily_trade_count = 0
    realized_pnl = 0.0
    weight_state = config.initial_weight_state or create_unseeded_equal_weight_state(timestamp=created_at, data_timestamp=ordered_candles[0].timestamp if ordered_candles else created_at)
    production_calls.append("initial_weight_state" if config.initial_weight_state is not None else "create_unseeded_equal_weight_state")
    if config.use_performance_weights:
        weight_state = update_performance_weight_state(
            weight_state,
            config.calibration_outcomes,
            update_timestamp=created_at,
            data_timestamp=ordered_candles[0].timestamp if ordered_candles else created_at,
            session_date=ordered_candles[0].timestamp.date().isoformat() if ordered_candles else None,
            config=config.weighted_config,
        )
        production_calls.append("update_performance_weight_state")
    settings_resolver = DynamicSettingsResolver(
        default_settings=config.default_settings or default_weighted_settings(timestamp=created_at),
        dynamic_envelope=config.dynamic_envelope or default_dynamic_envelope(timestamp=created_at).model_copy(update={"enabled": bool(config.use_dynamic_settings)}),
        hard_limits=config.hard_limits or default_hard_limits(timestamp=created_at),
    )

    for index, candle in enumerate(ordered_candles):
        if pending_order is not None and open_position is None:
            fill = simulate_entry_fill(order=pending_order, candle_index=index, candle=candle, cost_model=config.cost_model)
            production_calls.append("simulate_entry_fill")
            if fill.filled and pending_context is not None and fill.fill_price is not None:
                decision, signals, sizing, effective_settings, condition = pending_context
                stop = fill.fill_price - sizing.stop_distance if decision.proposed_side == WeightedSide.BUY.value else fill.fill_price + sizing.stop_distance
                lifecycle = open_exit_lifecycle(
                    trade_id=f"{config.run_id}-trade-{len(trades) + 1}",
                    symbol=config.symbol,
                    side=decision.proposed_side,
                    quantity=fill.quantity,
                    entry_price=fill.fill_price,
                    entry_timestamp=candle.timestamp,
                    stop_price=stop,
                    effective_settings=effective_settings,
                )
                production_calls.append("open_exit_lifecycle")
                supporting = tuple(signal.strategy_id for signal in signals if signal.signal == decision.proposed_side and signal.eligible and signal.data_ready)
                open_position = _OpenBacktestPosition(
                    lifecycle=lifecycle,
                    supporting_strategy_ids=supporting,
                    regime_label=str(condition.market_quality),
                    session_label=str(condition.session_phase),
                    entry_fee=entry_fee(fill.quantity, config.cost_model),
                    entry_spread_cost=fill.quantity * (pending_order.spread / 2.0),
                    entry_slippage_cost=fill.quantity * config.cost_model.entry_slippage_per_share,
                    partial_fill=fill.partial,
                )
                daily_trade_count += 1
                position_sizes.append(fill.quantity)
            if fill.filled or candle.timestamp >= ordered_candles[min(len(ordered_candles) - 1, pending_order.earliest_entry_index)].timestamp:
                pending_order = None
                pending_context = None

        snapshot = _snapshot(config.symbol, ordered_candles[: index + 1], manifest.manifest_hash)
        if open_position is not None:
            exit_decision, open_position = _evaluate_open_position(
                open_position=open_position,
                candle=candle,
                snapshot=snapshot,
                current_decision=decisions[-1].decision if decisions else None,
                end_of_session=_is_force_close(candle, config),
            )
            production_calls.append("evaluate_exit_lifecycle")
            if exit_decision.action == WeightedExitAction.EXIT.value:
                trade = _close_trade(
                    position=open_position,
                    exit_decision=exit_decision,
                    candle=candle,
                    cost_model=config.cost_model,
                    spread=_spread_from_snapshot(snapshot),
                )
                trades.append(trade)
                realized_pnl += trade.net_pnl
                equity_curve.append((trade.exit_timestamp, config.starting_cash + realized_pnl))
                for strategy_id in trade.supporting_strategy_ids:
                    strategy_returns[strategy_id].append(trade.net_pnl / config.account_equity)
                    strategy_regime_returns[strategy_id][trade.regime_label].append(trade.net_pnl / config.account_equity)
                    strategy_session_returns[strategy_id][trade.session_label].append(trade.net_pnl / config.account_equity)
                    strategy_favorable[strategy_id].append(trade.favorable_excursion)
                    strategy_adverse[strategy_id].append(trade.adverse_excursion)
                    outcomes.append(_strategy_outcome(strategy_id, trade, manifest.manifest_hash))
                open_position = None

        if index < max(config.decision_start_index, 1) or index >= len(ordered_candles) - 1 or open_position is not None or pending_order is not None:
            continue
        if _is_after_session_cutoff(candle, config):
            continue

        condition = classify_market_condition(snapshot, config=config.weighted_config, previous_condition=previous_condition)
        production_calls.append("classify_market_condition")
        previous_condition = condition
        effective_settings = settings_resolver.resolve(condition, timestamp=candle.timestamp)
        production_calls.append("DynamicSettingsResolver.resolve")
        signals = tuple(evaluate_signals(snapshot, config.weighted_config))
        production_calls.append("evaluate_signals")
        signals = _apply_weight_state(signals, weight_state)
        for signal in signals:
            if signal.signal in (WeightedSide.BUY.value, WeightedSide.SELL.value):
                opportunity_counts[signal.strategy_id] += 1
        decision = aggregate_weighted_signals(list(signals), config=config.weighted_config, decision_timestamp=candle.timestamp, historical_outcomes=tuple(outcomes))
        production_calls.append("aggregate_weighted_signals")
        gate_result = evaluate_local_decision_gates(
            WeightedVotingLocalGateInputs(
                decision=decision,
                signals=signals,
                market_snapshot=snapshot,
                five_minute_alignment=_five_minute_alignment(snapshot, decision.proposed_side),
                expected_value_after_costs=_expected_value_after_costs(signals, decision, snapshot, config),
                spread_cost=_spread_from_snapshot(snapshot),
                slippage_cost=config.cost_model.entry_slippage_per_share + config.cost_model.exit_slippage_per_share,
                fee_cost=config.cost_model.fee_per_share * 2,
                atr_percent=_atr_percent(snapshot),
                entry_quality=decision.vote_scores.winner_score,
                session_allowed=_session_allowed(candle, config),
                weighted_daily_loss_percent=_daily_loss_percent(realized_pnl, config),
                weighted_daily_trade_count=daily_trade_count,
                capital_available=max(0.0, config.starting_cash + realized_pnl),
                current_position=_position_state(config.symbol, open_position, candle.timestamp),
                data_timestamp=candle.timestamp,
            ),
            config=config.weighted_config,
        )
        production_calls.append("evaluate_local_decision_gates")
        for reason_code in gate_result.reason_codes:
            gate_rejections[reason_code] += 1
        sizing = calculate_weighted_voting_position_size(
            WeightedVotingSizingContext(
                decision=decision,
                effective_settings=effective_settings,
                market_snapshot=snapshot,
                account_equity=config.account_equity,
                available_buying_power=max(0.0, config.starting_cash + realized_pnl),
                remaining_weighted_daily_risk=max(0.0, config.account_equity * effective_settings.maximum_daily_loss_percent / 100.0 + realized_pnl),
                remaining_weighted_capital_partition=max(0.0, config.account_equity * effective_settings.daily_allocation_percent / 100.0),
                global_available_risk=max(0.0, config.account_equity * effective_settings.base_risk_per_trade_percent / 100.0),
                global_max_shares=effective_settings.maximum_shares or 2_147_483_647,
                structural_invalidation_price=_structural_invalidation(signals, decision.proposed_side),
                atr=average_true_range(snapshot.one_minute_candles, 14),
                slippage_per_share=config.cost_model.entry_slippage_per_share,
                current_one_minute_volume=snapshot.one_minute_candles[-1].volume,
                average_one_minute_volume=average_volume(snapshot.one_minute_candles, 20),
                local_gate_result=gate_result,
            )
        )
        production_calls.append("calculate_weighted_voting_position_size")
        entry_policy = None
        if sizing.quantity > 0 and (config.allow_short or decision.proposed_side != WeightedSide.SELL.value):
            entry_policy = evaluate_entry_policy(
                decision=decision,
                signals=signals,
                snapshot=snapshot,
                effective_settings=effective_settings,
                current_time=candle.timestamp,
            )
            production_calls.append("evaluate_entry_policy")
            if entry_policy.accepted:
                pending_order = WeightedBacktestPendingOrder(
                    order_id=f"{config.run_id}-order-{index}",
                    side=decision.proposed_side,
                    requested_quantity=sizing.quantity,
                    decision_candle_index=index,
                    earliest_entry_index=index + 1,
                    entry_policy=entry_policy,
                    participation_rate=effective_settings.maximum_participation_rate,
                    spread=_spread_from_snapshot(snapshot),
                    reason_codes=("weighted_voting.backtest.next_candle_entry_enforced",),
                )
                pending_context = (decision, signals, sizing, effective_settings, condition)
        decisions.append(
            WeightedBacktestDecisionTrace(
                candle_index=index,
                data_timestamp=candle.timestamp,
                decision=decision,
                gate_result=gate_result,
                sizing_result=sizing,
                entry_policy=entry_policy,
                market_condition=condition,
                completed_candle_count=len(snapshot.one_minute_candles),
                reason_codes=tuple(dict.fromkeys(decision.reason_codes + gate_result.reason_codes + sizing.reason_codes)),
            )
        )

    if open_position is not None and ordered_candles:
        final_candle = ordered_candles[-1]
        exit_decision, open_position = _evaluate_open_position(
            open_position=open_position,
            candle=final_candle,
            snapshot=_snapshot(config.symbol, ordered_candles, manifest.manifest_hash),
            current_decision=decisions[-1].decision if decisions else None,
            end_of_session=True,
        )
        production_calls.append("evaluate_exit_lifecycle")
        trade = _close_trade(open_position, exit_decision, final_candle, config.cost_model, _spread_from_snapshot(_snapshot(config.symbol, ordered_candles, manifest.manifest_hash)))
        trades.append(trade)
        realized_pnl += trade.net_pnl
        equity_curve.append((trade.exit_timestamp, config.starting_cash + realized_pnl))

    algorithm_results = _algorithm_results(trades, decisions, equity_curve, gate_rejections, position_sizes, config)
    strategy_results = _strategy_results(opportunity_counts, strategy_returns, strategy_regime_returns, strategy_session_returns, strategy_favorable, strategy_adverse)
    run = WeightedBacktestRun(
        run_id=config.run_id,
        status=WeightedBacktestStatus.COMPLETED,
        configuration_version=config.weighted_config.config_version,
        strategy_catalog_version=WEIGHTED_VOTING_CATALOG_VERSION,
        weight_version=weight_state.weight_version,
        settings_version="weighted_backtest_dynamic_settings",
        data_manifest_hash=manifest.manifest_hash,
        folds=(),
        started_at=created_at,
        completed_at=created_at,
        reason_codes=tuple(validation.warnings),
        explanation="Complete Weighted Voting production-parity backtest run referencing the immutable historical-data manifest.",
    )
    return WeightedBacktestResult(
        run=run,
        manifest=manifest,
        decisions=tuple(decisions),
        trades=tuple(trades),
        strategy_results=strategy_results,
        algorithm_results=algorithm_results,
        historical_outcomes=tuple(outcomes),
        production_function_calls=tuple(production_calls),
        reason_codes=("weighted_voting.backtest.production_parity", "weighted_voting.backtest.no_lookahead_next_candle_entry"),
        explanation="Backtest decisions use completed candles only and route through production Weighted Voting functions before simulating historical fills and exits.",
    )


def _snapshot(symbol: str, candles: tuple[WeightedVotingCandle, ...], manifest_hash: str) -> WeightedVotingMarketSnapshot:
    latest = candles[-1]
    spread = max(0.02, latest.close * 0.0002)
    return WeightedVotingMarketSnapshot(
        symbol=symbol,
        data_timestamp=latest.timestamp,
        one_minute_candles=candles,
        bid=round(latest.close - spread / 2.0, 10),
        ask=round(latest.close + spread / 2.0, 10),
        data_manifest_hash=manifest_hash,
        explanation="Weighted Voting backtest snapshot built only from completed historical candles.",
    )


def _apply_weight_state(signals: tuple[WeightedVotingSignal, ...], weight_state) -> tuple[WeightedVotingSignal, ...]:
    return tuple(signal.model_copy(update={"final_weight": weight_state.strategy_weights.get(signal.strategy_id, signal.final_weight)}) for signal in signals)


def _evaluate_open_position(
    *,
    open_position: _OpenBacktestPosition,
    candle: WeightedVotingCandle,
    snapshot: WeightedVotingMarketSnapshot,
    current_decision: WeightedDecision | None,
    end_of_session: bool,
) -> tuple[WeightedVotingExitDecision, _OpenBacktestPosition]:
    lifecycle = open_position.lifecycle
    stop_touched, target_touched, current_price = _intrabar_exit_price(lifecycle, candle)
    exit_decision = evaluate_exit_lifecycle(
        WeightedVotingExitInputs(
            lifecycle=lifecycle,
            current_price=current_price,
            current_timestamp=candle.timestamp,
            current_condition_quality=WeightedMarketQuality.CLEAN,
            current_weighted_decision=current_decision,
            end_of_session=end_of_session,
        )
    )
    if stop_touched and target_touched and exit_decision.action == WeightedExitAction.EXIT.value:
        exit_decision = evaluate_exit_lifecycle(
            WeightedVotingExitInputs(
                lifecycle=lifecycle,
                current_price=lifecycle.protective_stop,
                current_timestamp=candle.timestamp,
                current_condition_quality=WeightedMarketQuality.CLEAN,
                current_weighted_decision=current_decision,
                end_of_session=end_of_session,
            )
        )
    updated_position = _update_excursions(open_position, candle)
    return exit_decision, _replace_lifecycle(updated_position, exit_decision.updated_lifecycle)


def _intrabar_exit_price(lifecycle: WeightedVotingExitLifecycleState, candle: WeightedVotingCandle) -> tuple[bool, bool, float]:
    if lifecycle.side == WeightedSide.BUY.value:
        stop_touched = candle.low <= lifecycle.protective_stop
        target_touched = candle.high >= lifecycle.profit_target
    else:
        stop_touched = candle.high >= lifecycle.protective_stop
        target_touched = candle.low <= lifecycle.profit_target
    if stop_touched:
        return stop_touched, target_touched, lifecycle.protective_stop
    if target_touched:
        return stop_touched, target_touched, lifecycle.profit_target
    return stop_touched, target_touched, candle.close


def _close_trade(
    position: _OpenBacktestPosition,
    exit_decision: WeightedVotingExitDecision,
    candle: WeightedVotingCandle,
    cost_model: WeightedBacktestExecutionCostModel,
    spread: float,
) -> WeightedBacktestTrade:
    lifecycle = position.lifecycle
    raw_exit = exit_decision.stop_price if exit_decision.exit_reason == "stop_hit" else exit_decision.target_price if exit_decision.exit_reason == "target_hit" else candle.close
    exit_price = conservative_exit_price(side=lifecycle.side, raw_exit_price=raw_exit, cost_model=cost_model, spread=spread)
    gross = (exit_price - lifecycle.entry_price) * lifecycle.remaining_quantity if lifecycle.side == WeightedSide.BUY.value else (lifecycle.entry_price - exit_price) * lifecycle.remaining_quantity
    fee = exit_fee(lifecycle.remaining_quantity, cost_model)
    exit_slippage_cost = lifecycle.remaining_quantity * cost_model.exit_slippage_per_share
    exit_spread_cost = lifecycle.remaining_quantity * (spread / 2.0)
    costs = position.entry_fee + fee + position.entry_slippage_cost + exit_slippage_cost + position.entry_spread_cost + exit_spread_cost
    net = gross - position.entry_fee - fee
    return WeightedBacktestTrade(
        trade_id=lifecycle.trade_id,
        side=lifecycle.side,
        quantity=lifecycle.remaining_quantity,
        entry_timestamp=lifecycle.entry_timestamp,
        exit_timestamp=candle.timestamp,
        entry_price=lifecycle.entry_price,
        exit_price=exit_price,
        gross_pnl=round(gross, 10),
        net_pnl=round(net, 10),
        total_costs=round(costs, 10),
        entry_fee=round(position.entry_fee, 10),
        exit_fee=round(fee, 10),
        favorable_excursion=round(position.favorable_excursion, 10),
        adverse_excursion=round(position.adverse_excursion, 10),
        holding_minutes=max(0.0, (candle.timestamp - lifecycle.entry_timestamp).total_seconds() / 60.0),
        exit_reason=str(exit_decision.exit_reason),
        supporting_strategy_ids=position.supporting_strategy_ids,
        regime_label=position.regime_label,
        session_label=position.session_label,
        partial_fill=position.partial_fill,
        reason_codes=exit_decision.reason_codes,
    )


def _strategy_outcome(strategy_id: str, trade: WeightedBacktestTrade, manifest_hash: str) -> WeightedStrategyOutcome:
    return WeightedStrategyOutcome(
        strategy_id=strategy_id,
        side=WeightedSide(trade.side),
        entry_timestamp=trade.entry_timestamp,
        exit_timestamp=trade.exit_timestamp,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        outcome_return=trade.net_pnl / max(1.0, abs(trade.entry_price * trade.quantity)),
        exit_reason=WeightedExitReason(trade.exit_reason) if trade.exit_reason in {reason.value for reason in WeightedExitReason} else WeightedExitReason.RISK_GATE,
        reason_codes=(f"weighted_voting.regime.{trade.regime_label}", f"weighted_voting.session.{trade.session_label}"),
        explanation=f"Backtest trade outcome attributed to a supporting Weighted Voting strategy using manifest {manifest_hash}.",
    )


def _algorithm_results(
    trades: list[WeightedBacktestTrade],
    decisions: list[WeightedBacktestDecisionTrace],
    equity_curve: list[tuple[datetime, float]],
    gate_rejections: dict[str, int],
    position_sizes: list[int],
    config: WeightedBacktestEngineConfig,
) -> WeightedBacktestAlgorithmResult:
    returns = [trade.net_pnl / config.account_equity for trade in trades]
    net_pnl = sum(trade.net_pnl for trade in trades)
    gross_positive = sum(trade.net_pnl for trade in trades if trade.net_pnl > 0)
    gross_negative = abs(sum(trade.net_pnl for trade in trades if trade.net_pnl < 0))
    turnover = sum(abs(trade.entry_price * trade.quantity) + abs(trade.exit_price * trade.quantity) for trade in trades) / config.account_equity
    total_costs = sum(trade.total_costs for trade in trades)
    gross_abs = sum(abs(trade.gross_pnl) for trade in trades)
    return WeightedBacktestAlgorithmResult(
        net_pnl=round(net_pnl, 10),
        return_percent=round(net_pnl / config.account_equity * 100.0, 10),
        expectancy=round(_mean([trade.net_pnl for trade in trades]), 10),
        profit_factor=round(gross_positive / gross_negative if gross_negative > 0 else (4.0 if gross_positive > 0 else 0.0), 10),
        maximum_drawdown=round(_drawdown([value for _, value in equity_curve], config.starting_cash), 10),
        sharpe=round(_sharpe(returns), 10),
        sortino=round(_sortino(returns), 10),
        calmar=round((net_pnl / config.account_equity) / max(0.000001, _drawdown([value for _, value in equity_curve], config.starting_cash) / config.account_equity), 10),
        turnover=round(turnover, 10),
        average_holding_minutes=round(_mean([trade.holding_minutes for trade in trades]), 10),
        long_results=_side_results(trades, WeightedSide.BUY.value),
        short_results=_side_results(trades, WeightedSide.SELL.value),
        cost_ratio=round(total_costs / gross_abs if gross_abs > 0 else 0.0, 10),
        gate_rejection_counts=dict(sorted(gate_rejections.items())),
        equity_curve=tuple(equity_curve),
        position_size_distribution=_position_size_distribution(position_sizes),
    )


def _strategy_results(
    opportunities: dict[str, int],
    returns: dict[str, list[float]],
    regime_returns: dict[str, dict[str, list[float]]],
    session_returns: dict[str, dict[str, list[float]]],
    favorable: dict[str, list[float]],
    adverse: dict[str, list[float]],
) -> dict[str, WeightedBacktestStrategyResult]:
    strategy_ids = sorted(set(opportunities) | set(returns))
    correlations = {strategy_id: _correlations(strategy_id, returns) for strategy_id in strategy_ids}
    return {
        strategy_id: WeightedBacktestStrategyResult(
            strategy_id=strategy_id,
            opportunity_count=opportunities.get(strategy_id, 0),
            trade_count=len(returns.get(strategy_id, [])),
            expectancy=round(_mean(returns.get(strategy_id, [])), 10),
            profit_factor=round(_profit_factor(returns.get(strategy_id, [])), 10),
            maximum_drawdown=round(_return_drawdown(returns.get(strategy_id, [])), 10),
            favorable_excursion=round(_mean(favorable.get(strategy_id, [])), 10),
            adverse_excursion=round(_mean(adverse.get(strategy_id, [])), 10),
            regime_performance={key: round(_mean(values), 10) for key, values in sorted(regime_returns.get(strategy_id, {}).items())},
            session_performance={key: round(_mean(values), 10) for key, values in sorted(session_returns.get(strategy_id, {}).items())},
            correlation=correlations.get(strategy_id, {}),
        )
        for strategy_id in strategy_ids
    }


def _five_minute_alignment(snapshot: WeightedVotingMarketSnapshot, side: WeightedSide | str) -> WeightedFiveMinuteAlignment:
    candles = snapshot.one_minute_candles[-5:]
    if side not in (WeightedSide.BUY.value, WeightedSide.SELL.value) or len(candles) < 5:
        return WeightedFiveMinuteAlignment.UNAVAILABLE
    move = candles[-1].close - candles[0].open
    if abs(move) < candles[-1].close * 0.0002:
        return WeightedFiveMinuteAlignment.NEUTRAL
    if side == WeightedSide.BUY.value and move > 0:
        return WeightedFiveMinuteAlignment.POSITIVE
    if side == WeightedSide.SELL.value and move < 0:
        return WeightedFiveMinuteAlignment.POSITIVE
    return WeightedFiveMinuteAlignment.NEGATIVE


def _expected_value_after_costs(signals: tuple[WeightedVotingSignal, ...], decision: WeightedDecision, snapshot: WeightedVotingMarketSnapshot, config: WeightedBacktestEngineConfig) -> float:
    directional = [signal.expected_return_after_costs for signal in signals if signal.signal == decision.proposed_side]
    gross = max(directional) if directional else 0.0
    latest = snapshot.one_minute_candles[-1]
    cost = (_spread_from_snapshot(snapshot) + config.cost_model.entry_slippage_per_share + config.cost_model.exit_slippage_per_share + config.cost_model.fee_per_share * 2) / latest.close
    return gross - cost


def _atr_percent(snapshot: WeightedVotingMarketSnapshot) -> float | None:
    atr = average_true_range(snapshot.one_minute_candles, 14)
    latest = snapshot.one_minute_candles[-1]
    return atr / latest.close if atr is not None and latest.close > 0 else None


def _spread_from_snapshot(snapshot: WeightedVotingMarketSnapshot) -> float:
    if snapshot.bid is None or snapshot.ask is None:
        return 0.0
    return max(0.0, snapshot.ask - snapshot.bid)


def _structural_invalidation(signals: tuple[WeightedVotingSignal, ...], side: WeightedSide | str) -> float | None:
    levels = [signal.invalidation_level for signal in signals if signal.signal == side and signal.invalidation_level is not None]
    if not levels:
        return None
    return max(levels) if side == WeightedSide.BUY.value else min(levels)


def _position_state(symbol: str, open_position: _OpenBacktestPosition | None, timestamp: datetime) -> WeightedPositionState | None:
    if open_position is None:
        return None
    lifecycle = open_position.lifecycle
    quantity = lifecycle.remaining_quantity if lifecycle.side == WeightedSide.BUY.value else -lifecycle.remaining_quantity
    return WeightedPositionState(symbol=symbol, quantity=quantity, average_entry_price=lifecycle.entry_price, data_timestamp=timestamp, explanation="Weighted Voting backtest open position.")


def _session_allowed(candle: WeightedVotingCandle, config: WeightedBacktestEngineConfig) -> bool:
    minutes = eastern_minutes(candle.timestamp)
    return 570 <= minutes < config.session_cutoff_eastern_minutes


def _is_after_session_cutoff(candle: WeightedVotingCandle, config: WeightedBacktestEngineConfig) -> bool:
    return eastern_minutes(candle.timestamp) >= config.session_cutoff_eastern_minutes


def _is_force_close(candle: WeightedVotingCandle, config: WeightedBacktestEngineConfig) -> bool:
    return eastern_minutes(candle.timestamp) >= config.force_close_eastern_minutes


def _daily_loss_percent(realized_pnl: float, config: WeightedBacktestEngineConfig) -> float:
    return max(0.0, -realized_pnl / config.account_equity * 100.0)


def _update_excursions(position: _OpenBacktestPosition, candle: WeightedVotingCandle) -> _OpenBacktestPosition:
    lifecycle = position.lifecycle
    if lifecycle.side == WeightedSide.BUY.value:
        favorable = max(position.favorable_excursion, candle.high - lifecycle.entry_price)
        adverse = min(position.adverse_excursion, candle.low - lifecycle.entry_price)
    else:
        favorable = max(position.favorable_excursion, lifecycle.entry_price - candle.low)
        adverse = min(position.adverse_excursion, lifecycle.entry_price - candle.high)
    position.favorable_excursion = favorable
    position.adverse_excursion = adverse
    return position


def _replace_lifecycle(position: _OpenBacktestPosition, lifecycle: WeightedVotingExitLifecycleState) -> _OpenBacktestPosition:
    position.lifecycle = lifecycle
    return position


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _profit_factor(values: list[float]) -> float:
    wins = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    return wins / losses if losses > 0 else (4.0 if wins > 0 else 0.0)


def _return_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _drawdown(equity_values: list[float], starting_cash: float) -> float:
    peak = starting_cash
    drawdown = 0.0
    for value in equity_values:
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
    return drawdown


def _sharpe(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    deviation = sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))
    return mean / deviation * sqrt(len(values)) if deviation > 0 else 0.0


def _sortino(values: list[float]) -> float:
    negatives = [value for value in values if value < 0]
    if not values or not negatives:
        return 0.0
    downside = sqrt(sum(value * value for value in negatives) / len(negatives))
    return _mean(values) / downside * sqrt(len(values)) if downside > 0 else 0.0


def _side_results(trades: list[WeightedBacktestTrade], side: str) -> dict[str, float]:
    side_trades = [trade for trade in trades if trade.side == side]
    return {
        "trade_count": float(len(side_trades)),
        "net_pnl": round(sum(trade.net_pnl for trade in side_trades), 10),
        "expectancy": round(_mean([trade.net_pnl for trade in side_trades]), 10),
    }


def _position_size_distribution(position_sizes: list[int]) -> dict[str, float]:
    if not position_sizes:
        return {"count": 0.0, "minimum": 0.0, "maximum": 0.0, "average": 0.0}
    return {
        "count": float(len(position_sizes)),
        "minimum": float(min(position_sizes)),
        "maximum": float(max(position_sizes)),
        "average": round(_mean([float(size) for size in position_sizes]), 10),
    }


def _correlations(strategy_id: str, returns: dict[str, list[float]]) -> dict[str, float]:
    own = returns.get(strategy_id, [])
    values: dict[str, float] = {}
    for other_id, other in returns.items():
        if other_id == strategy_id:
            continue
        corr = _pearson(own, other)
        if corr is not None:
            values[other_id] = round(corr, 10)
    return dict(sorted(values.items()))


def _pearson(left: list[float], right: list[float]) -> float | None:
    size = min(len(left), len(right))
    if size < 2:
        return None
    left_values = left[-size:]
    right_values = right[-size:]
    left_mean = _mean(left_values)
    right_mean = _mean(right_values)
    numerator = sum((left_values[index] - left_mean) * (right_values[index] - right_mean) for index in range(size))
    left_denominator = sqrt(sum((value - left_mean) ** 2 for value in left_values))
    right_denominator = sqrt(sum((value - right_mean) ** 2 for value in right_values))
    denominator = left_denominator * right_denominator
    return numerator / denominator if denominator > 0 else None
