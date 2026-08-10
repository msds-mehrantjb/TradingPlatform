"""Production-parity backtest engine for Weighted Voting."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from math import sqrt
from typing import Any

from backend.app.algorithms.weighted_voting.backtest.data_validation import WeightedBacktestDataManifest, validate_historical_data
from backend.app.algorithms.weighted_voting.backtest.execution_simulator import (
    WEIGHTED_VOTING_EXECUTION_SIMULATOR_VERSION,
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
    WeightedVotingGatePipelineResult,
)
from backend.app.algorithms.weighted_voting.decision_kernel import WEIGHTED_VOTING_DECISION_KERNEL_VERSION, WeightedVotingDecisionKernel
from backend.app.algorithms.weighted_voting.dynamic_settings import (
    WEIGHTED_VOTING_DYNAMIC_SETTINGS_VERSION,
    DynamicSettingsResolver,
    default_dynamic_envelope,
    default_hard_limits,
    default_weighted_settings,
    resolve_effective_settings,
)
from backend.app.algorithms.weighted_voting.entry_policy import WeightedEntryPolicyResult, evaluate_entry_policy
from backend.app.algorithms.weighted_voting.exit_policy import (
    WeightedExitAction,
    WeightedVotingExitDecision,
    WeightedVotingExitInputs,
    WeightedVotingExitLifecycleState,
    evaluate_exit_lifecycle,
    open_exit_lifecycle,
)
from backend.app.domain.exchange_calendar import ExchangeCalendarService
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.inventory import (
    WEIGHTED_VOTING_INVENTORY_NAMESPACE,
    WEIGHTED_VOTING_INVENTORY_VERSION,
    WeightedVotingInventoryEventType,
    WeightedVotingInventoryRepository,
)
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
    WeightedSessionPhase,
    WeightedSide,
    WeightedExitReason,
    WeightedStrategyOutcome,
    WeightedWeightState,
    WeightedVotingSignal,
)
from backend.app.algorithms.weighted_voting.position_sizing import WeightedVotingSizingResult
from backend.app.algorithms.weighted_voting.runtime_context import (
    WEIGHTED_VOTING_RUNTIME_CONTEXT_VERSION,
    WeightedVotingExecutionCostEstimate,
    WeightedVotingRuntimeContextBuilder,
    WeightedVotingStaticAccountPort,
    WeightedVotingStaticGlobalRiskPort,
    WeightedVotingStaticMarketDataPort,
)
from backend.app.algorithms.weighted_voting.signal_engine import evaluate_signals
from backend.app.algorithms.weighted_voting.strategies.common import eastern_minutes
from backend.app.algorithms.weighted_voting.weight_engine import WEIGHTED_VOTING_WEIGHT_ENGINE_VERSION, create_unseeded_equal_weight_state, update_performance_weight_state


WEIGHTED_VOTING_BACKTEST_ENGINE_VERSION = "weighted_voting_backtest_engine_v2"
WEIGHTED_VOTING_BACKTEST_REQUIRED_STRUCTURE = (
    "weighted_voting/backtest/__init__.py",
    "weighted_voting/backtest/data_validation.py",
    "weighted_voting/backtest/engine.py",
    "weighted_voting/backtest/execution_simulator.py",
    "weighted_voting/backtest/walk_forward.py",
)
WEIGHTED_VOTING_BACKTEST_OWNED_CAPABILITIES = (
    "weighted_voting_data_validation",
    "warm_up_handling",
    "real_strategy_invocation",
    "historical_weight_state_loading",
    "point_in_time_market_snapshot",
    "decision_replay",
    "local_gate_replay",
    "position_sizing",
    "entry_simulation",
    "stop_target_simulation",
    "slippage",
    "spread",
    "fees_and_regulatory_costs",
    "partial_fills",
    "session_close",
    "position_ownership",
    "trade_recording",
    "strategy_attribution",
    "algorithm_metrics",
    "strategy_metrics",
    "equity_curve",
    "drawdown_curve",
    "walk_forward_folds",
    "configuration_manifest",
    "data_manifest",
    "reproducibility_hashes",
)
WEIGHTED_VOTING_BACKTEST_PRODUCTION_CALLS = (
    "create_unseeded_equal_weight_state",
    "update_performance_weight_state",
    "WeightedVotingDecisionKernel.evaluate",
    "classify_market_condition",
    "DynamicSettingsResolver.resolve",
    "evaluate_signals",
    "aggregate_weighted_signals",
    "evaluate_local_decision_gates",
    "calculate_weighted_voting_position_size",
    "evaluate_entry_policy",
    "simulate_entry_fill",
    "open_exit_lifecycle",
    "evaluate_exit_lifecycle",
)


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
class WeightedBacktestConfigurationManifest:
    run_id: str
    symbol: str
    engine_version: str
    config_version: str
    strategy_catalog_version: str
    account_equity: float
    starting_cash: float
    allow_short: bool
    session_cutoff_eastern_minutes: int
    force_close_eastern_minutes: int
    decision_start_index: int
    use_performance_weights: bool
    use_dynamic_settings: bool
    cost_model: dict[str, float]
    initial_weight_version: str | None
    active_weight_version: str
    active_weight_hash: str | None
    settings_version: str
    settings_hash: str
    inventory_version: str
    inventory_namespace: str
    code_versions: dict[str, str]
    calibration_outcome_count: int
    source: str

    def deterministic_json(self) -> str:
        return json.dumps(
            {
                "runId": self.run_id,
                "symbol": self.symbol,
                "engineVersion": self.engine_version,
                "configVersion": self.config_version,
                "strategyCatalogVersion": self.strategy_catalog_version,
                "accountEquity": self.account_equity,
                "startingCash": self.starting_cash,
                "allowShort": self.allow_short,
                "sessionCutoffEasternMinutes": self.session_cutoff_eastern_minutes,
                "forceCloseEasternMinutes": self.force_close_eastern_minutes,
                "decisionStartIndex": self.decision_start_index,
                "usePerformanceWeights": self.use_performance_weights,
                "useDynamicSettings": self.use_dynamic_settings,
                "costModel": self.cost_model,
                "initialWeightVersion": self.initial_weight_version,
                "activeWeightVersion": self.active_weight_version,
                "activeWeightHash": self.active_weight_hash,
                "settingsVersion": self.settings_version,
                "settingsHash": self.settings_hash,
                "inventoryVersion": self.inventory_version,
                "inventoryNamespace": self.inventory_namespace,
                "codeVersions": self.code_versions,
                "calibrationOutcomeCount": self.calibration_outcome_count,
                "source": self.source,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def manifest_hash(self) -> str:
        return hashlib.sha256(self.deterministic_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WeightedBacktestDecisionTrace:
    candle_index: int
    data_timestamp: datetime
    decision: WeightedDecision
    gate_result: WeightedVotingGatePipelineResult
    sizing_result: WeightedVotingSizingResult
    entry_policy: WeightedEntryPolicyResult | None
    market_condition: WeightedMarketCondition
    inventory_snapshot_version: int
    runtime_context_manifest_hash: str
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
    settings_version: str
    configuration_hash: str
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
    drawdown_curve: tuple[tuple[datetime, float], ...]
    position_size_distribution: dict[str, float]


@dataclass(frozen=True)
class WeightedBacktestResult:
    run: WeightedBacktestRun
    manifest: WeightedBacktestDataManifest
    configuration_manifest: WeightedBacktestConfigurationManifest
    decisions: tuple[WeightedBacktestDecisionTrace, ...]
    trades: tuple[WeightedBacktestTrade, ...]
    strategy_results: dict[str, WeightedBacktestStrategyResult]
    algorithm_results: WeightedBacktestAlgorithmResult
    historical_outcomes: tuple[WeightedStrategyOutcome, ...]
    production_function_calls: tuple[str, ...]
    reproducibility_hash: str
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
    settings_version: str
    configuration_hash: str
    favorable_excursion: float = 0.0
    adverse_excursion: float = 0.0


def backtest_engine_status() -> dict[str, object]:
    return {
        "version": WEIGHTED_VOTING_BACKTEST_ENGINE_VERSION,
        "status": "implemented",
        "requiredStructure": WEIGHTED_VOTING_BACKTEST_REQUIRED_STRUCTURE,
        "ownedCapabilities": WEIGHTED_VOTING_BACKTEST_OWNED_CAPABILITIES,
        "criticalRule": "call_same_weighted_voting_logic_used_in_paper_trading",
        "productionCalls": WEIGHTED_VOTING_BACKTEST_PRODUCTION_CALLS,
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
    allocated_capital = config.account_equity * config.weighted_config.daily_allocation_percent / 100.0
    inventory_store = _RunScopedInventoryStore()
    inventory_repository = WeightedVotingInventoryRepository(inventory_store, symbol=config.symbol, allocated_capital=allocated_capital, allow_shorting=config.allow_short)
    if ordered_candles:
        inventory_repository.initialize_session(
            session_date=ordered_candles[0].timestamp.date(),
            allocated_capital=allocated_capital,
            cash_available=allocated_capital,
            occurred_at=ordered_candles[0].timestamp,
            expected_snapshot_version=inventory_repository.current_snapshot(now=ordered_candles[0].timestamp).snapshot_version,
            event_id=f"{config.run_id}.session.{ordered_candles[0].timestamp.date().isoformat()}",
        )
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
    configuration_manifest = _configuration_manifest(
        config,
        created_at,
        weight_state=weight_state,
        settings_version=settings_resolver.default_settings.settings_version,
        settings_hash=settings_resolver.default_settings.deterministic_hash(),
    )

    for index, candle in enumerate(ordered_candles):
        if pending_order is not None and open_position is None:
            fill = simulate_entry_fill(order=pending_order, candle_index=index, candle=candle, cost_model=config.cost_model)
            production_calls.append("simulate_entry_fill")
            if fill.filled and pending_context is not None and fill.fill_price is not None:
                decision, signals, sizing, effective_settings, condition = pending_context
                stop = fill.fill_price - sizing.stop_distance if decision.proposed_side == WeightedSide.BUY.value else fill.fill_price + sizing.stop_distance
                supporting = tuple(signal.strategy_id for signal in signals if signal.signal == decision.proposed_side and signal.eligible and signal.data_ready)
                trade_id = f"{config.run_id}-trade-{len(trades) + 1}"
                lifecycle = open_exit_lifecycle(
                    trade_id=trade_id,
                    symbol=config.symbol,
                    side=decision.proposed_side,
                    quantity=fill.quantity,
                    entry_price=fill.fill_price,
                    entry_timestamp=candle.timestamp,
                    stop_price=stop,
                    effective_settings=effective_settings,
                    supporting_strategy_ids=supporting,
                )
                production_calls.append("open_exit_lifecycle")
                _record_backtest_fill(
                    inventory_repository=inventory_repository,
                    run_id=config.run_id,
                    trade_id=trade_id,
                    order=pending_order,
                    decision=decision,
                    fill_quantity=fill.quantity,
                    fill_price=fill.fill_price,
                    supporting_strategy_ids=supporting,
                    occurred_at=candle.timestamp,
                )
                if fill.partial:
                    _release_backtest_order(
                        inventory_repository=inventory_repository,
                        run_id=config.run_id,
                        order=pending_order,
                        occurred_at=candle.timestamp,
                        suffix="partial-fill-remainder",
                    )
                open_position = _OpenBacktestPosition(
                    lifecycle=lifecycle,
                    supporting_strategy_ids=supporting,
                    regime_label=str(condition.market_quality),
                    session_label=str(condition.session_phase),
                    entry_fee=entry_fee(fill.quantity, config.cost_model),
                    entry_spread_cost=fill.quantity * (pending_order.spread / 2.0),
                    entry_slippage_cost=fill.quantity * config.cost_model.entry_slippage_per_share,
                    partial_fill=fill.partial,
                    settings_version=effective_settings.settings_version,
                    configuration_hash=effective_settings.configuration_hash,
                )
                daily_trade_count += 1
                position_sizes.append(fill.quantity)
            expired = candle.timestamp >= ordered_candles[min(len(ordered_candles) - 1, pending_order.earliest_entry_index)].timestamp
            if not fill.filled and expired:
                _release_backtest_order(
                    inventory_repository=inventory_repository,
                    run_id=config.run_id,
                    order=pending_order,
                    occurred_at=candle.timestamp,
                    suffix="expired",
                )
            if fill.filled or expired:
                pending_order = None
                pending_context = None

        snapshot = _snapshot(config.symbol, ordered_candles[: index + 1], manifest.manifest_hash)
        if open_position is not None:
            _mark_backtest_position(inventory_repository=inventory_repository, position=open_position, mark_price=candle.close, occurred_at=candle.timestamp)
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
                _close_backtest_inventory_position(
                    inventory_repository=inventory_repository,
                    position=open_position,
                    exit_price=trade.exit_price,
                    exit_reason=trade.exit_reason,
                    occurred_at=trade.exit_timestamp,
                )
                daily_trade_count = inventory_repository.current_snapshot(now=trade.exit_timestamp).daily_trade_count
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

        context = _runtime_context(
            snapshot=snapshot,
            config=config,
            weight_state=weight_state,
            inventory_repository=inventory_repository,
            previous_condition=previous_condition,
        )
        kernel_result = WeightedVotingDecisionKernel.evaluate(
            context,
            config=config.weighted_config,
            settings_resolver=settings_resolver,
            historical_outcomes=tuple(outcomes),
            signal_evaluator=evaluate_signals,
        )
        production_calls.append("WeightedVotingDecisionKernel.evaluate")
        production_calls.append("classify_market_condition")
        production_calls.append("DynamicSettingsResolver.resolve")
        production_calls.append("evaluate_signals")
        production_calls.append("aggregate_weighted_signals")
        production_calls.append("evaluate_local_decision_gates")
        production_calls.append("calculate_weighted_voting_position_size")
        condition = kernel_result.market_condition
        previous_condition = condition
        effective_settings = kernel_result.effective_settings
        signals = kernel_result.signals
        for signal in signals:
            if signal.signal in (WeightedSide.BUY.value, WeightedSide.SELL.value):
                opportunity_counts[signal.strategy_id] += 1
        decision = kernel_result.decision
        gate_result = kernel_result.gate_result
        for reason_code in gate_result.reason_codes:
            gate_rejections[reason_code] += 1
        sizing = kernel_result.sizing_result
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
                _reserve_backtest_order(
                    inventory_repository=inventory_repository,
                    run_id=config.run_id,
                    order=pending_order,
                    decision=decision,
                    sizing=sizing,
                    occurred_at=candle.timestamp,
                )
        decisions.append(
            WeightedBacktestDecisionTrace(
                candle_index=index,
                data_timestamp=candle.timestamp,
                decision=decision,
                gate_result=gate_result,
                sizing_result=sizing,
                entry_policy=entry_policy,
                market_condition=condition,
                inventory_snapshot_version=context.inventory_snapshot.snapshot_version,
                runtime_context_manifest_hash=context.manifest_hash,
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
        _close_backtest_inventory_position(
            inventory_repository=inventory_repository,
            position=open_position,
            exit_price=trade.exit_price,
            exit_reason=trade.exit_reason,
            occurred_at=trade.exit_timestamp,
        )
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
        configuration_manifest=configuration_manifest,
        decisions=tuple(decisions),
        trades=tuple(trades),
        strategy_results=strategy_results,
        algorithm_results=algorithm_results,
        historical_outcomes=tuple(outcomes),
        production_function_calls=tuple(production_calls),
        reproducibility_hash=_reproducibility_hash(configuration_manifest, manifest, tuple(production_calls)),
        reason_codes=(
            "weighted_voting.backtest.production_parity",
            "weighted_voting.backtest.no_lookahead_next_candle_entry",
            "weighted_voting.backtest.configuration_manifest",
            "weighted_voting.backtest.reproducibility_hash",
        ),
        explanation="Backtest decisions use completed candles only and route through production Weighted Voting functions before simulating historical fills and exits.",
    )


def _configuration_manifest(
    config: WeightedBacktestEngineConfig,
    created_at: datetime,
    *,
    weight_state: WeightedWeightState,
    settings_version: str,
    settings_hash: str,
) -> WeightedBacktestConfigurationManifest:
    return WeightedBacktestConfigurationManifest(
        run_id=config.run_id,
        symbol=config.symbol,
        engine_version=WEIGHTED_VOTING_BACKTEST_ENGINE_VERSION,
        config_version=config.weighted_config.config_version,
        strategy_catalog_version=WEIGHTED_VOTING_CATALOG_VERSION,
        account_equity=config.account_equity,
        starting_cash=config.starting_cash,
        allow_short=config.allow_short,
        session_cutoff_eastern_minutes=config.session_cutoff_eastern_minutes,
        force_close_eastern_minutes=config.force_close_eastern_minutes,
        decision_start_index=config.decision_start_index,
        use_performance_weights=config.use_performance_weights,
        use_dynamic_settings=config.use_dynamic_settings,
        cost_model={
            "entrySlippagePerShare": config.cost_model.entry_slippage_per_share,
            "exitSlippagePerShare": config.cost_model.exit_slippage_per_share,
            "feePerShare": config.cost_model.fee_per_share,
            "regulatoryFeePerShare": config.cost_model.regulatory_fee_per_share,
            "minimumFee": config.cost_model.minimum_fee,
        },
        initial_weight_version=config.initial_weight_state.weight_version if config.initial_weight_state is not None else None,
        active_weight_version=weight_state.weight_version,
        active_weight_hash=weight_state.output_hash or weight_state.deterministic_hash(),
        settings_version=settings_version,
        settings_hash=settings_hash,
        inventory_version=WEIGHTED_VOTING_INVENTORY_VERSION,
        inventory_namespace=WEIGHTED_VOTING_INVENTORY_NAMESPACE,
        code_versions={
            "backtest_engine": WEIGHTED_VOTING_BACKTEST_ENGINE_VERSION,
            "decision_kernel": WEIGHTED_VOTING_DECISION_KERNEL_VERSION,
            "runtime_context": WEIGHTED_VOTING_RUNTIME_CONTEXT_VERSION,
            "dynamic_settings": WEIGHTED_VOTING_DYNAMIC_SETTINGS_VERSION,
            "weight_engine": WEIGHTED_VOTING_WEIGHT_ENGINE_VERSION,
            "execution_simulator": WEIGHTED_VOTING_EXECUTION_SIMULATOR_VERSION,
        },
        calibration_outcome_count=len(config.calibration_outcomes),
        source=config.source,
    )


def _snapshot(symbol: str, candles: tuple[WeightedVotingCandle, ...], manifest_hash: str) -> WeightedVotingMarketSnapshot:
    latest = candles[-1]
    spread = max(0.02, latest.close * 0.0002)
    exchange_session = ExchangeCalendarService().session_for_date(latest.timestamp.date())
    return WeightedVotingMarketSnapshot(
        symbol=symbol,
        data_timestamp=latest.timestamp,
        one_minute_candles=candles,
        bid=round(latest.close - spread / 2.0, 10),
        ask=round(latest.close + spread / 2.0, 10),
        spread=round(spread, 10),
        session_date=exchange_session.sessionDate.isoformat(),
        session_phase=_weighted_session_phase(latest.timestamp, exchange_session),
        data_freshness_seconds=0.0,
        data_manifest_hash=manifest_hash,
        explanation="Weighted Voting backtest snapshot built only from completed historical candles and the shared exchange session calendar.",
    )


def _runtime_context(
    *,
    snapshot: WeightedVotingMarketSnapshot,
    config: WeightedBacktestEngineConfig,
    weight_state: WeightedWeightState,
    inventory_repository: WeightedVotingInventoryRepository,
    previous_condition: WeightedMarketCondition | None,
):
    inventory = inventory_repository.current_snapshot(now=snapshot.data_timestamp, session_date=snapshot.data_timestamp.date())
    return WeightedVotingRuntimeContextBuilder(
        market_data_port=WeightedVotingStaticMarketDataPort(snapshot),
        inventory_repository=inventory_repository,
        account_port=WeightedVotingStaticAccountPort(
            account_equity=config.account_equity,
            broker_buying_power=inventory.cash_available,
            source_id="weighted_voting.backtest.historical_account_observation",
        ),
        global_risk_port=WeightedVotingStaticGlobalRiskPort(
            global_available_risk=max(0.0, config.account_equity * config.weighted_config.risk_per_trade_baseline_percent / 100.0),
            global_max_shares=config.weighted_config.maximum_shares or 2_147_483_647,
            gate_response=None,
            source_id="weighted_voting.backtest.simulated_global_risk_service",
        ),
        effective_settings=resolve_effective_settings(timestamp=snapshot.data_timestamp),
        active_weight_state=weight_state,
        observed_at=snapshot.data_timestamp,
        mode="replay_fixture",
        cost_estimate=WeightedVotingExecutionCostEstimate(
            slippage_per_share=config.cost_model.entry_slippage_per_share,
            fee_per_share=config.cost_model.fee_per_share * 2 + config.cost_model.regulatory_fee_per_share,
            observed_at=snapshot.data_timestamp,
            source_id="weighted_voting.backtest.simulated_cost_model",
            reason_codes=("weighted_voting.backtest.cost_model",),
        ),
        previous_market_condition=previous_condition,
    ).build()


class _RunScopedInventoryStore:
    """In-memory store scoped to a single backtest/replay run."""

    def __init__(self) -> None:
        self.snapshots: dict[str, dict[str, Any]] = {}

    def read_snapshot(self, key: str) -> dict[str, Any]:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict[str, Any]) -> None:
        if not key.startswith("weighted_voting.inventory."):
            raise ValueError("Backtest inventory adapter may only write run-scoped weighted_voting.inventory records")
        self.snapshots[key] = snapshot


def _reserve_backtest_order(
    *,
    inventory_repository: WeightedVotingInventoryRepository,
    run_id: str,
    order: WeightedBacktestPendingOrder,
    decision: WeightedDecision,
    sizing: WeightedVotingSizingResult,
    occurred_at: datetime,
) -> None:
    snapshot = inventory_repository.current_snapshot(now=occurred_at)
    notional = abs(order.requested_quantity * float(order.entry_policy.limit_price or order.entry_policy.trigger_price or 0.0))
    inventory_repository.append_event(
        event_id=f"{run_id}.reserve.{order.order_id}",
        event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
        payload={
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "order_id": order.order_id,
            "symbol": inventory_repository.symbol,
            "side": str(order.side),
            "quantity": order.requested_quantity,
            "filled_quantity": 0,
            "remaining_quantity": order.requested_quantity,
            "order_type": str(order.entry_policy.entry_type),
            "limit_price": order.entry_policy.limit_price,
            "stop_price": order.entry_policy.trigger_price,
            "reserved_buying_power": round(notional, 10),
            "reserved_cash": round(notional, 10),
            "planned_risk_dollars": round(float(sizing.effective_risk_dollars or sizing.risk_dollars or 0.0), 10),
            "decision_id": decision.decision_id,
            "order_intent_id": order.order_id,
            "client_order_id": order.order_id,
            "status": "WORKING",
            "created_at": occurred_at.isoformat(),
            "updated_at": occurred_at.isoformat(),
            "expiration": order.entry_policy.entry_expiration.isoformat() if order.entry_policy.entry_expiration else None,
        },
        occurred_at=occurred_at,
        expected_snapshot_version=snapshot.snapshot_version,
    )


def _record_backtest_fill(
    *,
    inventory_repository: WeightedVotingInventoryRepository,
    run_id: str,
    trade_id: str,
    order: WeightedBacktestPendingOrder,
    decision: WeightedDecision,
    fill_quantity: int,
    fill_price: float,
    supporting_strategy_ids: tuple[str, ...],
    occurred_at: datetime,
) -> None:
    snapshot = inventory_repository.current_snapshot(now=occurred_at)
    signed_quantity = fill_quantity if order.side == WeightedSide.BUY.value else -fill_quantity
    inventory_repository.append_event(
        event_id=f"{run_id}.fill.{order.order_id}.{fill_quantity}.{occurred_at.isoformat()}",
        event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
        payload={
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "fill_id": f"{run_id}.fill.{order.order_id}.{occurred_at.isoformat()}",
            "position_id": trade_id,
            "symbol": inventory_repository.symbol,
            "side": str(order.side),
            "quantity": signed_quantity,
            "allow_open_short": bool(order.side == WeightedSide.SELL.value),
            "average_entry_price": fill_price,
            "opened_at": occurred_at.isoformat(),
            "decision_id": decision.decision_id,
            "order_intent_id": order.order_id,
            "client_order_id": order.order_id,
            "owning_strategy_ids": supporting_strategy_ids,
            "source": "weighted_voting.backtest.simulated_broker",
        },
        occurred_at=occurred_at,
        expected_snapshot_version=snapshot.snapshot_version,
    )


def _release_backtest_order(
    *,
    inventory_repository: WeightedVotingInventoryRepository,
    run_id: str,
    order: WeightedBacktestPendingOrder,
    occurred_at: datetime,
    suffix: str,
) -> None:
    snapshot = inventory_repository.current_snapshot(now=occurred_at)
    inventory_repository.append_event(
        event_id=f"{run_id}.release.{order.order_id}.{suffix}",
        event_type=WeightedVotingInventoryEventType.ORDER_RELEASED,
        payload={
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "order_id": order.order_id,
            "client_order_id": order.order_id,
        },
        occurred_at=occurred_at,
        expected_snapshot_version=snapshot.snapshot_version,
    )


def _mark_backtest_position(
    *,
    inventory_repository: WeightedVotingInventoryRepository,
    position: _OpenBacktestPosition,
    mark_price: float,
    occurred_at: datetime,
) -> None:
    snapshot = inventory_repository.current_snapshot(now=occurred_at)
    if not any(item.position_id == position.lifecycle.trade_id for item in snapshot.open_positions):
        return
    inventory_repository.append_event(
        event_id=f"{position.lifecycle.trade_id}.mark.{occurred_at.isoformat()}",
        event_type=WeightedVotingInventoryEventType.POSITION_MARKED,
        payload={
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "position_id": position.lifecycle.trade_id,
            "mark_price": mark_price,
        },
        occurred_at=occurred_at,
        expected_snapshot_version=snapshot.snapshot_version,
    )


def _close_backtest_inventory_position(
    *,
    inventory_repository: WeightedVotingInventoryRepository,
    position: _OpenBacktestPosition,
    exit_price: float,
    exit_reason: str,
    occurred_at: datetime,
) -> None:
    snapshot = inventory_repository.current_snapshot(now=occurred_at)
    if not any(item.position_id == position.lifecycle.trade_id for item in snapshot.open_positions):
        return
    inventory_repository.append_event(
        event_id=f"{position.lifecycle.trade_id}.close.{occurred_at.isoformat()}",
        event_type=WeightedVotingInventoryEventType.POSITION_CLOSED,
        payload={
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "position_id": position.lifecycle.trade_id,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
        },
        occurred_at=occurred_at,
        expected_snapshot_version=snapshot.snapshot_version,
    )


def _weighted_session_phase(timestamp: datetime, exchange_session) -> WeightedSessionPhase:
    if not exchange_session.contains_timestamp(timestamp):
        return WeightedSessionPhase.OUTSIDE_SESSION
    minute = exchange_session.minutes_after_open(timestamp)
    minutes_until_close = (exchange_session.closeTimestamp - timestamp).total_seconds() / 60.0 if exchange_session.closeTimestamp else 0.0
    if minute < 30:
        return WeightedSessionPhase.OPENING
    if minutes_until_close <= 15:
        return WeightedSessionPhase.CLOSING
    if minute < 150:
        return WeightedSessionPhase.MORNING
    if minute < 300:
        return WeightedSessionPhase.MIDDAY
    return WeightedSessionPhase.AFTERNOON


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
        settings_version=position.settings_version,
        configuration_hash=position.configuration_hash,
        reason_codes=exit_decision.reason_codes,
    )


def _strategy_outcome(strategy_id: str, trade: WeightedBacktestTrade, manifest_hash: str) -> WeightedStrategyOutcome:
    notional = max(1.0, abs(trade.entry_price * trade.quantity))
    return WeightedStrategyOutcome(
        outcome_id=f"{trade.trade_id}.{strategy_id}.outcome",
        trade_id=trade.trade_id,
        strategy_id=strategy_id,
        side=WeightedSide(trade.side),
        entry_timestamp=trade.entry_timestamp,
        exit_timestamp=trade.exit_timestamp,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        is_closed=True,
        fully_reconciled=True,
        gross_return=trade.gross_pnl / notional,
        outcome_return=trade.net_pnl / notional,
        slippage_cost_return=max(0.0, trade.total_costs - trade.entry_fee - trade.exit_fee) / notional,
        fee_cost_return=(trade.entry_fee + trade.exit_fee) / notional,
        total_cost_return=trade.total_costs / notional,
        maximum_favorable_excursion_return=trade.favorable_excursion / notional,
        maximum_adverse_excursion_return=trade.adverse_excursion / notional,
        opportunity_count=1,
        execution_quality=max(0.0, min(1.0, 1.0 - (trade.total_costs / notional) / 0.01)),
        regime_label=trade.regime_label,
        session_label=trade.session_label,
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
    drawdown_curve = _drawdown_curve(equity_curve, config.starting_cash)
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
        drawdown_curve=drawdown_curve,
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


def _spread_from_snapshot(snapshot: WeightedVotingMarketSnapshot) -> float:
    if snapshot.bid is None or snapshot.ask is None:
        return 0.0
    return max(0.0, snapshot.ask - snapshot.bid)


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


def _drawdown_curve(equity_curve: list[tuple[datetime, float]], starting_cash: float) -> tuple[tuple[datetime, float], ...]:
    peak = starting_cash
    curve: list[tuple[datetime, float]] = []
    for timestamp, value in equity_curve:
        peak = max(peak, value)
        curve.append((timestamp, round(max(0.0, peak - value), 10)))
    return tuple(curve)


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


def _reproducibility_hash(
    configuration_manifest: WeightedBacktestConfigurationManifest,
    data_manifest: WeightedBacktestDataManifest,
    production_calls: tuple[str, ...],
) -> str:
    payload = {
        "engineVersion": WEIGHTED_VOTING_BACKTEST_ENGINE_VERSION,
        "configurationManifestHash": configuration_manifest.manifest_hash,
        "dataManifestHash": data_manifest.manifest_hash,
        "productionCalls": production_calls,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
