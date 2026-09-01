from __future__ import annotations

from collections import Counter
from enum import Enum
from importlib.util import find_spec
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.algorithms.voting_ensemble.lifecycle import (
    PROMOTION_APPROVAL_MARKER,
    PROMOTION_CANDIDATE_EVIDENCE_MARKER,
    VOTING_ENSEMBLE_PROTECTED_SHADOW_MODULE_IDS,
    VotingEnsembleLifecycleState,
    promotion_policy_status,
)
from backend.app.domain.models import StrategyFamily, StrategyRole


ModuleLifecycleStatus = VotingEnsembleLifecycleState
ModuleRole = Literal[
    "DIRECTIONAL",
    "CONTEXT",
    "REGIME",
    "SAFETY",
    "AGGREGATOR",
    "TRADING_SETTINGS",
    "RISK_BUDGET",
    "ORDER_PLANNER",
    "EXECUTION_ADAPTER",
    "BACKTEST_REPLAY_ADAPTER",
    "BACKGROUND_WORKER",
]
FORBIDDEN_MUTABLE_ALGORITHM_PREFIXES = (
    "backend.app.algorithms.wca",
    "backend.app.algorithms.weighted_voting",
    "backend.app.algorithms.regime",
    "backend.app.algorithms.session",
    "backend.app.algorithms.meta_strategy",
)


class StrategyCollection(str, Enum):
    DIRECTIONAL = "DIRECTIONAL"
    CONTEXT = "CONTEXT"
    REGIME = "REGIME"
    SAFETY = "SAFETY"
    AGGREGATOR = "AGGREGATOR"
    TRADING_SETTINGS = "TRADING_SETTINGS"
    RISK_BUDGET = "RISK_BUDGET"
    ORDER_PLANNER = "ORDER_PLANNER"
    EXECUTION_ADAPTER = "EXECUTION_ADAPTER"
    BACKTEST_REPLAY_ADAPTER = "BACKTEST_REPLAY_ADAPTER"
    BACKGROUND_WORKER = "BACKGROUND_WORKER"


class StrategyRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    strategyId: str = Field(min_length=1)
    strategyName: str = Field(min_length=1)
    strategyVersion: str = Field(min_length=1)
    family: str = Field(min_length=1)
    role: ModuleRole
    collection: StrategyCollection
    lifecycleStatus: ModuleLifecycleStatus
    requiredInputs: tuple[str, ...]
    implementationPath: str = Field(min_length=1)
    runtimeBinding: str = Field(min_length=1)
    backtestBinding: str = Field(min_length=1)
    settingsNamespace: str = Field(min_length=1)
    stateNamespace: str = Field(min_length=1)
    persistenceNamespace: str = Field(min_length=1)
    testPath: str = Field(min_length=1)
    promotionEvidence: tuple[str, ...] = ()
    enabled: bool

    @property
    def id(self) -> str:
        return self.strategyId

    @property
    def status(self) -> ModuleLifecycleStatus:
        return self.lifecycleStatus

    @property
    def evidence(self) -> tuple[str, ...]:
        return self.promotionEvidence

    @model_validator(mode="after")
    def enabled_must_match_lifecycle(self) -> "StrategyRegistryEntry":
        if self.enabled != (self.lifecycleStatus == "active"):
            raise ValueError("registry enabled flag must match authoritative lifecycleStatus")
        if self.lifecycleStatus == "active" and self.runtimeBinding == "not_wired":
            raise ValueError(f"active Voting Ensemble module {self.strategyId} has no runtime binding")
        if any(self.implementationPath.startswith(prefix) for prefix in FORBIDDEN_MUTABLE_ALGORITHM_PREFIXES):
            raise ValueError(f"{self.strategyId} points to another algorithm's mutable implementation")
        if any(self.runtimeBinding.startswith(prefix) for prefix in FORBIDDEN_MUTABLE_ALGORITHM_PREFIXES):
            raise ValueError(f"{self.strategyId} runtime binding points to another algorithm's mutable implementation")
        return self


ModuleStatus = StrategyRegistryEntry


class VotingEnsembleInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm_id: Literal["voting_ensemble"] = "voting_ensemble"
    inventoryVersion: Literal["voting_ensemble_authoritative_inventory_v1"] = "voting_ensemble_authoritative_inventory_v1"
    directional: tuple[StrategyRegistryEntry, ...]
    context: tuple[StrategyRegistryEntry, ...]
    regime: tuple[StrategyRegistryEntry, ...]
    safety: tuple[StrategyRegistryEntry, ...]
    aggregator: tuple[StrategyRegistryEntry, ...]
    tradingSettingsResolver: tuple[StrategyRegistryEntry, ...]
    riskBudget: tuple[StrategyRegistryEntry, ...]
    orderPlanner: tuple[StrategyRegistryEntry, ...]
    executionAdapter: tuple[StrategyRegistryEntry, ...]
    backtestReplayAdapter: tuple[StrategyRegistryEntry, ...]
    backgroundWorker: tuple[StrategyRegistryEntry, ...]

    @model_validator(mode="after")
    def module_ids_are_unique(self) -> "VotingEnsembleInventory":
        ids = [module.strategyId for module in self.modules]
        duplicates = sorted(module_id for module_id in set(ids) if ids.count(module_id) > 1)
        if duplicates:
            raise ValueError(f"duplicate Voting Ensemble authoritative module ids: {', '.join(duplicates)}")
        return self

    @property
    def modules(self) -> tuple[StrategyRegistryEntry, ...]:
        return (
            *self.directional,
            *self.context,
            *self.regime,
            *self.safety,
            *self.aggregator,
            *self.tradingSettingsResolver,
            *self.riskBudget,
            *self.orderPlanner,
            *self.executionAdapter,
            *self.backtestReplayAdapter,
            *self.backgroundWorker,
        )


def _entry(
    strategy_id: str,
    name: str,
    version: str,
    family: str | StrategyFamily,
    role: ModuleRole | StrategyRole,
    collection: StrategyCollection,
    required_inputs: tuple[str, ...],
    implementation_path: str,
    runtime_binding: str,
    backtest_binding: str,
    *,
    lifecycle_status: ModuleLifecycleStatus = "active",
    settings_namespace: str = "voting_ensemble.settings",
    state_namespace: str = "voting_ensemble.state",
    persistence_namespace: str = "voting_ensemble.persistence",
    test_path: str = "backend/tests/test_voting_ensemble_module_inventory.py",
    promotion_evidence: tuple[str, ...] = (),
) -> StrategyRegistryEntry:
    family_value = family.value if isinstance(family, StrategyFamily) else str(family)
    role_value = role.value if isinstance(role, StrategyRole) else str(role)
    return StrategyRegistryEntry(
        strategyId=strategy_id,
        strategyName=name,
        strategyVersion=version,
        family=family_value,
        role=role_value,  # type: ignore[arg-type]
        collection=collection,
        lifecycleStatus=lifecycle_status,
        requiredInputs=required_inputs,
        implementationPath=implementation_path,
        runtimeBinding=runtime_binding,
        backtestBinding=backtest_binding,
        settingsNamespace=settings_namespace,
        stateNamespace=state_namespace,
        persistenceNamespace=persistence_namespace,
        testPath=test_path,
        promotionEvidence=promotion_evidence,
        enabled=lifecycle_status == "active",
    )


VOTING_ENSEMBLE_MODULE_INVENTORY = VotingEnsembleInventory(
    directional=(
        _entry(
            "multi_timeframe_trend_alignment",
            "Multi-Timeframe Trend Alignment",
            "2.0.0",
            StrategyFamily.TREND,
            StrategyRole.DIRECTIONAL,
            StrategyCollection.DIRECTIONAL,
            ("spy_1m_features", "spy_5m_features", "spy_15m_features", "session_vwap"),
            "backend.app.algorithms.voting_ensemble.strategies.directional.multi_timeframe_trend_alignment",
            "backend.app.algorithms.voting_ensemble.service:evaluate_multi_timeframe_trend",
            "backend.app.algorithms.voting_ensemble.strategies.directional.multi_timeframe_trend_alignment:SnapshotMultiTimeframeTrendAlignmentStrategy",
            test_path="backend/tests/test_voting_ensemble_directional_strategies.py",
        ),
        _entry(
            "first_pullback_after_open",
            "First Pullback After Open",
            "2.0.0",
            StrategyFamily.TREND,
            StrategyRole.DIRECTIONAL,
            StrategyCollection.DIRECTIONAL,
            ("spy_1m_candles", "spy_1m_features", "session_vwap"),
            "backend.app.algorithms.voting_ensemble.strategies.directional.first_pullback_after_open",
            "backend.app.algorithms.voting_ensemble.service:evaluate_first_pullback_after_open",
            "backend.app.algorithms.voting_ensemble.strategies.directional.first_pullback_after_open:SnapshotFirstPullbackAfterOpenStrategy",
            test_path="backend/tests/test_voting_ensemble_directional_strategies.py",
        ),
        _entry(
            "failed_breakout_reversal",
            "Failed Breakout Reversal",
            "2.0.0",
            StrategyFamily.REVERSAL,
            StrategyRole.DIRECTIONAL,
            StrategyCollection.DIRECTIONAL,
            ("spy_1m_candles", "reference_levels", "atr", "spread"),
            "backend.app.algorithms.voting_ensemble.strategies.directional.failed_breakout_reversal",
            "backend.app.algorithms.voting_ensemble.service:evaluate_failed_breakout_strategy",
            "backend.app.algorithms.voting_ensemble.strategies.directional.failed_breakout_reversal:SnapshotFailedBreakoutReversalStrategy",
            test_path="backend/tests/test_voting_ensemble_directional_strategies.py",
        ),
        _entry(
            "liquidity_sweep_reversal",
            "Liquidity Sweep Reversal",
            "2.0.0",
            StrategyFamily.REVERSAL,
            StrategyRole.DIRECTIONAL,
            StrategyCollection.DIRECTIONAL,
            ("spy_1m_candles", "liquidity_levels", "atr", "spread", "activity"),
            "backend.app.algorithms.voting_ensemble.strategies.directional.liquidity_sweep_reversal",
            "backend.app.algorithms.voting_ensemble.service:evaluate_liquidity_sweep_reversal",
            "backend.app.algorithms.voting_ensemble.strategies.directional.liquidity_sweep_reversal:SnapshotLiquiditySweepReversalStrategy",
            test_path="backend/tests/test_voting_ensemble_directional_strategies.py",
        ),
        _entry(
            "bollinger_band_reversion",
            "Bollinger Band Reversion",
            "2.0.0",
            StrategyFamily.MEAN_REVERSION,
            StrategyRole.DIRECTIONAL,
            StrategyCollection.DIRECTIONAL,
            ("spy_1m_candles", "bollinger_bands", "spread", "quote_freshness"),
            "backend.app.algorithms.voting_ensemble.strategies.directional.bollinger_band_reversion",
            "backend.app.algorithms.voting_ensemble.service:evaluate_bollinger_band_reversion",
            "backend.app.algorithms.voting_ensemble.strategies.directional.bollinger_band_reversion:BollingerBandReversionStrategy",
            test_path="backend/tests/test_voting_ensemble_directional_strategies.py",
        ),
        _entry(
            "atr_overextension_reversion",
            "ATR Overextension Reversion",
            "2.0.0",
            StrategyFamily.MEAN_REVERSION,
            StrategyRole.DIRECTIONAL,
            StrategyCollection.DIRECTIONAL,
            ("spy_1m_candles", "atr", "session_vwap", "spread", "quote_freshness"),
            "backend.app.algorithms.voting_ensemble.strategies.directional.atr_overextension_reversion",
            "backend.app.algorithms.voting_ensemble.service:evaluate_atr_overextension_reversion",
            "backend.app.algorithms.voting_ensemble.strategies.directional.atr_overextension_reversion:AtrOverextensionReversionStrategy",
            test_path="backend/tests/test_voting_ensemble_directional_strategies.py",
        ),
        _entry(
            "opening_range_breakout",
            "Opening Range Breakout",
            "1.0.0",
            StrategyFamily.BREAKOUT,
            StrategyRole.DIRECTIONAL,
            StrategyCollection.DIRECTIONAL,
            (
                "spy_1m_candles",
                "opening_range",
                "atr",
                "relative_volume",
                "spread",
                "displayed_liquidity",
                "session_state",
                "economic_event_state",
                "regime_state",
            ),
            "backend.app.algorithms.voting_ensemble.strategies.directional.opening_range_breakout",
            "backend.app.algorithms.voting_ensemble.strategies.directional.opening_range_breakout:OpeningRangeBreakoutStrategy",
            "backend.app.algorithms.voting_ensemble.strategies.directional.opening_range_breakout:OpeningRangeBreakoutStrategy",
            lifecycle_status="shadow",
            test_path="backend/tests/test_voting_ensemble_opening_range_breakout.py",
            promotion_evidence=("shadow-only until opening-range breakout evidence is validated after costs",),
        ),
        _entry(
            "vwap_trend_continuation",
            "VWAP Trend Continuation",
            "1.0.0",
            StrategyFamily.TREND,
            StrategyRole.DIRECTIONAL,
            StrategyCollection.DIRECTIONAL,
            (
                "spy_1m_candles",
                "session_vwap",
                "vwap_slope",
                "trend_structure",
                "pullback_window",
                "confirmation_candle",
                "relative_volume",
                "spread",
                "displayed_liquidity",
                "higher_timeframe_evidence",
                "regime_state",
            ),
            "backend.app.algorithms.voting_ensemble.strategies.directional.vwap_trend_continuation",
            "backend.app.algorithms.voting_ensemble.strategies.directional.vwap_trend_continuation:VwapTrendContinuationStrategy",
            "backend.app.algorithms.voting_ensemble.strategies.directional.vwap_trend_continuation:VwapTrendContinuationStrategy",
            lifecycle_status="shadow",
            test_path="backend/tests/test_voting_ensemble_vwap_trend_continuation.py",
            promotion_evidence=("shadow-only until VWAP-continuation trend evidence is validated and overlap-controlled",),
        ),
        _entry(
            "gap_continuation_fade",
            "Gap Continuation / Fade",
            "1.0.0",
            StrategyFamily.GAP_SESSION,
            StrategyRole.DIRECTIONAL,
            StrategyCollection.DIRECTIONAL,
            (
                "prior_close",
                "session_open",
                "premarket_high_low",
                "gap_percent",
                "atr_normalized_gap",
                "premarket_direction",
                "opening_volume",
                "session_vwap",
                "opening_range",
                "regime_state",
                "economic_event_state",
            ),
            "backend.app.algorithms.voting_ensemble.strategies.directional.gap_continuation_fade",
            "backend.app.algorithms.voting_ensemble.strategies.directional.gap_continuation_fade:GapContinuationFadeStrategy",
            "backend.app.algorithms.voting_ensemble.strategies.directional.gap_continuation_fade:GapContinuationFadeStrategy",
            lifecycle_status="shadow",
            test_path="backend/tests/test_voting_ensemble_gap_continuation_fade.py",
            promotion_evidence=("shadow-only until gap/session continuation and fade outcomes are validated by session-window replay",),
        ),
    ),
    context=(
        _entry(
            "relative_strength_qqq_iwm",
            "Relative Strength vs QQQ/IWM",
            "2.0.0",
            StrategyFamily.MARKET_CONTEXT,
            StrategyRole.CONTEXT,
            StrategyCollection.CONTEXT,
            ("spy_candles", "qqq_candles", "iwm_candles"),
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline",
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline:RelativeStrengthQqqIwmSnapshotContext",
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline:RelativeStrengthQqqIwmSnapshotContext",
            test_path="backend/tests/test_voting_ensemble_context_pipeline.py",
        ),
        _entry(
            "market_breadth_momentum",
            "Market Breadth Momentum",
            "2.0.0",
            StrategyFamily.MARKET_CONTEXT,
            StrategyRole.CONTEXT,
            StrategyCollection.CONTEXT,
            ("external_breadth_feed_or_proxy_basket", "component_candles", "component_volume", "component_vwap", "component_ema20"),
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline",
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline:MarketBreadthMomentumSnapshotContext",
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline:MarketBreadthMomentumSnapshotContext",
            test_path="backend/tests/test_voting_ensemble_context_pipeline.py",
        ),
        _entry(
            "economic_event_context",
            "Economic Event Context",
            "2.0.0",
            StrategyFamily.MARKET_CONTEXT,
            StrategyRole.CONTEXT,
            StrategyCollection.CONTEXT,
            ("economic_event_state", "scheduled_events", "market_clock", "spread"),
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline",
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline:EconomicEventSnapshotContext",
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline:EconomicEventSnapshotContext",
            lifecycle_status="shadow",
            test_path="backend/tests/test_voting_ensemble_context_pipeline.py",
            promotion_evidence=("shadow-only until replay evidence proves no active-decision degradation",),
        ),
        _entry(
            "market_structure_context",
            "Market Structure Context",
            "2.0.0",
            StrategyFamily.MARKET_CONTEXT,
            StrategyRole.CONTEXT,
            StrategyCollection.CONTEXT,
            ("spy_1m_candles", "prior_day_ohlc", "premarket_levels", "opening_range", "session_vwap"),
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline",
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline:MarketStructureSnapshotContext",
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline:MarketStructureSnapshotContext",
            lifecycle_status="shadow",
            test_path="backend/tests/test_voting_ensemble_context_pipeline.py",
            promotion_evidence=("shadow-only until structure features pass point-in-time validation",),
        ),
        _entry(
            "volume_confirmation_context",
            "Volume Confirmation",
            "2.0.0",
            StrategyFamily.MARKET_CONTEXT,
            StrategyRole.CONTEXT,
            StrategyCollection.CONTEXT,
            ("spy_1m_relative_volume", "spy_1m_candles", "volume_baseline"),
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline",
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline:VolumeConfirmationSnapshotContext",
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline:VolumeConfirmationSnapshotContext",
            lifecycle_status="shadow",
            test_path="backend/tests/test_voting_ensemble_context_pipeline.py",
            promotion_evidence=("shadow-only until volume confirmation improves net expectancy after costs",),
        ),
        _entry(
            "vwap_position_context",
            "VWAP Position Context",
            "2.0.0",
            StrategyFamily.MARKET_CONTEXT,
            StrategyRole.CONTEXT,
            StrategyCollection.CONTEXT,
            ("session_vwap", "session_vwap_slope", "spy_1m_candles"),
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline",
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline:VwapPositionSnapshotContext",
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline:VwapPositionSnapshotContext",
            lifecycle_status="shadow",
            test_path="backend/tests/test_voting_ensemble_context_pipeline.py",
            promotion_evidence=("shadow-only until VWAP position evidence is validated out of sample",),
        ),
        _entry(
            "market_forecast_context",
            "Market Forecast Multi-Horizon Context",
            "1.0.0",
            StrategyFamily.MARKET_CONTEXT,
            StrategyRole.CONTEXT,
            StrategyCollection.CONTEXT,
            ("spy_1m_candles", "market_forecast_multi_horizon"),
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline",
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline:MarketForecastSnapshotContext",
            "backend.app.algorithms.voting_ensemble.strategies.context.pipeline:MarketForecastSnapshotContext",
            lifecycle_status="shadow",
            test_path="backend/tests/test_voting_ensemble_market_forecast_context.py",
            promotion_evidence=(
                "shadow-only: the forecast's own activation policy is advisory_only_until_live_paper_validation",
            ),
        ),
    ),
    regime=(
        _entry(
            "adx_atr_regime_classifier",
            "ADX/ATR Regime Classifier",
            "2.0.0",
            StrategyFamily.MARKET_CONTEXT,
            StrategyRole.REGIME,
            StrategyCollection.REGIME,
            ("spy_1m_candles", "adx", "atr", "market_structure", "liquidity_state", "session_state", "economic_event_state"),
            "backend.app.algorithms.voting_ensemble.strategies.regime.adx_atr_regime_classifier",
            "backend.app.algorithms.voting_ensemble.strategies.regime.adx_atr_regime_classifier:AdxAtrRegimeClassifier",
            "backend.app.algorithms.voting_ensemble.strategies.regime.adx_atr_regime_classifier:AdxAtrRegimeClassifier",
            test_path="backend/tests/test_voting_ensemble_regime_classifier.py",
            promotion_evidence=("Trend strength", "Volatility level", "Structure", "Liquidity", "Session", "Event risk"),
        ),
    ),
    safety=(
        _entry(
            "cash_avoid_trading_filter",
            "Cash / Avoid Trading Filter",
            "2.0.0",
            StrategyFamily.SAFETY,
            StrategyRole.SAFETY,
            StrategyCollection.SAFETY,
            ("feature_snapshot", "operational_state", "account_risk_state", "order_intent"),
            "backend.app.algorithms.voting_ensemble.gates",
            "backend.app.algorithms.voting_ensemble.gates:VotingEnsembleLocalGateEngine",
            "backend.app.algorithms.voting_ensemble.gates:VotingEnsembleLocalGateEngine",
            test_path="backend/tests/test_voting_ensemble_local_gates.py",
        ),
    ),
    aggregator=(
        _entry(
            "ensemble_strategy_voting",
            "Ensemble Strategy Voting",
            "2.0.0",
            StrategyFamily.MARKET_CONTEXT,
            StrategyRole.AGGREGATOR,
            StrategyCollection.AGGREGATOR,
            ("strategy_signals", "family_scores"),
            "backend.app.algorithms.voting_ensemble.ensemble.family_aware",
            "backend.app.algorithms.voting_ensemble.ensemble.family_aware:FamilyAwareDeterministicEnsemble.aggregate",
            "backend.app.algorithms.voting_ensemble.ensemble.family_aware:FamilyAwareDeterministicEnsemble",
            test_path="backend/tests/test_voting_ensemble_family_aware_authoritative.py",
        ),
    ),
    tradingSettingsResolver=(
        _entry(
            "trading_settings_resolver",
            "Trading Settings Resolver",
            "1.0.0",
            StrategyFamily.SAFETY,
            "TRADING_SETTINGS",
            StrategyCollection.TRADING_SETTINGS,
            ("settings_payload", "market_context"),
            "backend.app.algorithms.voting_ensemble.trading_settings.resolver",
            "backend.app.algorithms.voting_ensemble.trading_settings.resolver:resolve_one_minute_trading_settings",
            "backend.app.algorithms.voting_ensemble.trading_settings.resolver:dynamic_risk_config",
            test_path="backend/tests/test_voting_ensemble_trading_settings.py",
            settings_namespace="voting_ensemble.one_minute_settings",
            state_namespace="voting_ensemble.settings_state",
        ),
    ),
    riskBudget=(
        _entry(
            "risk_budget",
            "Risk Budget",
            "1.0.0",
            StrategyFamily.SAFETY,
            "RISK_BUDGET",
            StrategyCollection.RISK_BUDGET,
            ("account_equity", "available_buying_power", "candidate_edge", "stop_distance"),
            "backend.app.algorithms.voting_ensemble.risk_budget",
            "backend.app.algorithms.voting_ensemble.risk_budget:resolve_voting_ensemble_risk_budget",
            "backend.app.algorithms.voting_ensemble.risk_budget:position_size_for_config",
            test_path="backend/tests/test_voting_ensemble_risk_budget.py",
            state_namespace="voting_ensemble.risk_state",
        ),
    ),
    orderPlanner=(
        _entry(
            "order_planner",
            "Order Planner",
            "1.0.0",
            StrategyFamily.SAFETY,
            "ORDER_PLANNER",
            StrategyCollection.ORDER_PLANNER,
            ("trade_candidate", "effective_policy", "local_gate_decision", "ml_shadow_result"),
            "backend.app.algorithms.voting_ensemble.order_planner",
            "backend.app.algorithms.voting_ensemble.order_planner:VotingEnsembleOrderPlanner",
            "backend.app.algorithms.voting_ensemble.order_planner:VotingEnsembleOrderPlanner",
            test_path="backend/tests/test_voting_ensemble_risk_budget.py",
            state_namespace="voting_ensemble.order_state",
        ),
    ),
    executionAdapter=(
        _entry(
            "execution_adapter",
            "Execution Adapter",
            "1.0.0",
            StrategyFamily.SAFETY,
            "EXECUTION_ADAPTER",
            StrategyCollection.EXECUTION_ADAPTER,
            ("order_plan", "paper_broker_gateway", "global_risk_decision"),
            "backend.app.algorithms.voting_ensemble.execution_adapter",
            "backend.app.algorithms.voting_ensemble.execution_adapter:VotingEnsembleExecutionAdapter",
            "backend.app.algorithms.voting_ensemble.exit_policy:VotingEnsembleExecutionSimulator",
            test_path="backend/tests/test_voting_ensemble_execution_adapter.py",
            state_namespace="voting_ensemble.execution_state",
        ),
    ),
    backtestReplayAdapter=(
        _entry(
            "backtest_replay_adapter",
            "Backtest / Replay Adapter",
            "1.0.0",
            StrategyFamily.MARKET_CONTEXT,
            "BACKTEST_REPLAY_ADAPTER",
            StrategyCollection.BACKTEST_REPLAY_ADAPTER,
            ("point_in_time_candles", "warmup_policy", "execution_simulation_config"),
            "backend.app.algorithms.voting_ensemble.backtesting_adapter",
            "backend.app.algorithms.voting_ensemble.backtesting_adapter:run_voting_ensemble_backtest",
            "backend.app.algorithms.voting_ensemble.backtesting_adapter:VotingEnsembleBacktestingAdapter",
            test_path="backend/tests/test_voting_ensemble_backtest_runner.py",
            state_namespace="voting_ensemble.backtest_state",
            persistence_namespace="voting_ensemble.backtest_runs",
        ),
    ),
    backgroundWorker=(
        _entry(
            "background_worker",
            "Background Worker",
            "1.0.0",
            StrategyFamily.SAFETY,
            "BACKGROUND_WORKER",
            StrategyCollection.BACKGROUND_WORKER,
            ("runtime_command", "validated_payload", "job_id", "idempotency_key", "correlation_id"),
            "backend.app.algorithms.voting_ensemble.runtime.worker",
            "backend.app.algorithms.voting_ensemble.runtime.orchestrator:VotingEnsembleRuntimeOrchestrator",
            "backend.app.algorithms.voting_ensemble.runtime.worker:InProcessVotingEnsembleWorkerAdapter",
            test_path="backend/tests/test_voting_ensemble_evaluation_jobs.py",
            state_namespace="voting_ensemble.runtime.status",
            persistence_namespace="voting_ensemble.runtime.queue",
        ),
    ),
)


VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES = VOTING_ENSEMBLE_MODULE_INVENTORY.directional
VOTING_ENSEMBLE_CONTEXT_STRATEGIES = VOTING_ENSEMBLE_MODULE_INVENTORY.context
VOTING_ENSEMBLE_REGIME_STRATEGIES = VOTING_ENSEMBLE_MODULE_INVENTORY.regime
VOTING_ENSEMBLE_SAFETY_STRATEGIES = VOTING_ENSEMBLE_MODULE_INVENTORY.safety
VOTING_ENSEMBLE_AGGREGATOR_STRATEGIES = VOTING_ENSEMBLE_MODULE_INVENTORY.aggregator
VOTING_ENSEMBLE_TRADING_SETTINGS_RESOLVERS = VOTING_ENSEMBLE_MODULE_INVENTORY.tradingSettingsResolver
VOTING_ENSEMBLE_RISK_BUDGET_MODULES = VOTING_ENSEMBLE_MODULE_INVENTORY.riskBudget
VOTING_ENSEMBLE_ORDER_PLANNER_MODULES = VOTING_ENSEMBLE_MODULE_INVENTORY.orderPlanner
VOTING_ENSEMBLE_EXECUTION_ADAPTERS = VOTING_ENSEMBLE_MODULE_INVENTORY.executionAdapter
VOTING_ENSEMBLE_BACKTEST_REPLAY_ADAPTERS = VOTING_ENSEMBLE_MODULE_INVENTORY.backtestReplayAdapter
VOTING_ENSEMBLE_BACKGROUND_WORKERS = VOTING_ENSEMBLE_MODULE_INVENTORY.backgroundWorker
VOTING_ENSEMBLE_STRATEGIES = VOTING_ENSEMBLE_MODULE_INVENTORY.modules
VOTING_ENSEMBLE_ACTIVE_DIRECTIONAL_STRATEGIES = tuple(entry for entry in VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES if entry.enabled)
VOTING_ENSEMBLE_SHADOW_DIRECTIONAL_STRATEGIES = tuple(entry for entry in VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES if entry.lifecycleStatus == "shadow")
VOTING_ENSEMBLE_ACTIVE_CONTEXT_STRATEGIES = tuple(entry for entry in VOTING_ENSEMBLE_CONTEXT_STRATEGIES if entry.enabled)
VOTING_ENSEMBLE_SHADOW_CONTEXT_STRATEGIES = tuple(entry for entry in VOTING_ENSEMBLE_CONTEXT_STRATEGIES if entry.lifecycleStatus == "shadow")


STRATEGY_ALIAS_MAP: dict[str, str] = {
    "Failed Breakout Strategy": "failed_breakout_reversal",
    "Failed Breakout Reversal": "failed_breakout_reversal",
    "Bollinger Band Reversion": "bollinger_band_reversion",
    "ATR Overextension Reversion": "atr_overextension_reversion",
    "Opening Range Breakout": "opening_range_breakout",
    "VWAP Trend Continuation": "vwap_trend_continuation",
    "Gap Continuation / Fade": "gap_continuation_fade",
    "Bollinger/ATR Reversion": "bollinger_band_reversion",
    "bollinger_atr_reversion": "bollinger_band_reversion",
    "adx_trend_strength_regime": "adx_atr_regime_classifier",
    "atr_volatility_regime": "adx_atr_regime_classifier",
    "ADX Trend Strength Filter": "adx_atr_regime_classifier",
    "ADX Trend Strength Regime": "adx_atr_regime_classifier",
    "ATR Volatility Regime": "adx_atr_regime_classifier",
    "ADX/ATR Regime Classifier": "adx_atr_regime_classifier",
    "Ensemble Strategy Voting": "ensemble_strategy_voting",
    "Economic Event Context": "economic_event_context",
    "Market Structure Context": "market_structure_context",
    "Volume Confirmation": "volume_confirmation_context",
    "volume_confirmation": "volume_confirmation_context",
    "VWAP Position Context": "vwap_position_context",
    "VWAP Position Strategy": "vwap_position_context",
}

_STRATEGIES_BY_ID = {entry.strategyId: entry for entry in VOTING_ENSEMBLE_STRATEGIES}
_STRATEGIES_BY_NAME = {entry.strategyName: entry for entry in VOTING_ENSEMBLE_STRATEGIES}


def inventory_status(strategy_id: str) -> ModuleLifecycleStatus:
    return _STRATEGIES_BY_ID[strategy_id].lifecycleStatus


def active_module_ids(collection: StrategyCollection) -> tuple[str, ...]:
    return tuple(entry.strategyId for entry in VOTING_ENSEMBLE_STRATEGIES if entry.collection == collection.value and entry.enabled)


def shadow_module_ids(collection: StrategyCollection) -> tuple[str, ...]:
    return tuple(entry.strategyId for entry in VOTING_ENSEMBLE_STRATEGIES if entry.collection == collection.value and entry.lifecycleStatus == "shadow")


def directional_strategy_input_ids() -> tuple[str, ...]:
    return active_module_ids(StrategyCollection.DIRECTIONAL)


def canonical_strategy_id(name_or_id: str) -> str:
    if name_or_id in _STRATEGIES_BY_ID:
        return name_or_id
    if name_or_id in STRATEGY_ALIAS_MAP:
        return STRATEGY_ALIAS_MAP[name_or_id]
    if name_or_id in _STRATEGIES_BY_NAME:
        return _STRATEGIES_BY_NAME[name_or_id].strategyId
    raise KeyError(f"Unknown Voting Ensemble strategy module: {name_or_id}")


def resolve_strategy(name_or_id: str) -> StrategyRegistryEntry:
    return _STRATEGIES_BY_ID[canonical_strategy_id(name_or_id)]


def validate_voting_ensemble_inventory_startup(actual_active_ids_by_collection: dict[str, tuple[str, ...]] | None = None) -> dict[str, Any]:
    errors: list[str] = []
    seen = Counter(entry.strategyId for entry in VOTING_ENSEMBLE_STRATEGIES)
    errors.extend(f"duplicate_authoritative_id:{module_id}" for module_id, count in seen.items() if count > 1)
    for entry in VOTING_ENSEMBLE_STRATEGIES:
        if _points_to_forbidden_algorithm(entry.implementationPath) or _points_to_forbidden_algorithm(entry.runtimeBinding):
            errors.append(f"forbidden_mutable_algorithm_binding:{entry.strategyId}")
        if entry.enabled and not _module_path_importable(entry.implementationPath):
            errors.append(f"active_missing_implementation:{entry.strategyId}:{entry.implementationPath}")
        if entry.enabled and entry.runtimeBinding == "not_wired":
            errors.append(f"active_not_wired:{entry.strategyId}")
        if entry.strategyId in VOTING_ENSEMBLE_PROTECTED_SHADOW_MODULE_IDS:
            if entry.lifecycleStatus == "candidate" and not _has_promotion_marker(entry, PROMOTION_CANDIDATE_EVIDENCE_MARKER):
                errors.append(f"promotion_policy.candidate_without_evidence:{entry.strategyId}")
            if entry.lifecycleStatus == "active" and not _has_promotion_marker(entry, PROMOTION_APPROVAL_MARKER):
                errors.append(f"promotion_policy.active_without_approval:{entry.strategyId}")
    if actual_active_ids_by_collection:
        for collection, actual_ids in actual_active_ids_by_collection.items():
            try:
                collection_enum = StrategyCollection(collection)
            except ValueError:
                errors.append(f"unknown_runtime_collection:{collection}")
                continue
            expected_ids = active_module_ids(collection_enum)
            actual_counts = Counter(actual_ids)
            if collection_enum == StrategyCollection.DIRECTIONAL:
                errors.extend(f"active_directional_executed_zero_times:{module_id}" for module_id in expected_ids if actual_counts[module_id] == 0)
                errors.extend(f"active_directional_executed_more_than_once:{module_id}" for module_id, count in actual_counts.items() if count > 1)
            shadow_ids = set(shadow_module_ids(collection_enum))
            errors.extend(f"shadow_module_affects_active_decision:{module_id}" for module_id in actual_ids if module_id in shadow_ids)
            if tuple(actual_ids) != expected_ids:
                errors.append(f"inventory_runtime_disagree:{collection}:{','.join(expected_ids)}:{','.join(actual_ids)}")
    return {
        "algorithmId": "voting_ensemble",
        "inventoryVersion": VOTING_ENSEMBLE_MODULE_INVENTORY.inventoryVersion,
        "valid": not errors,
        "errors": errors,
        "reasonCodes": ["voting_ensemble.inventory.validation.passed" if not errors else "voting_ensemble.inventory.validation.failed"],
    }


def voting_ensemble_inventory_status(actual_active_ids_by_collection: dict[str, tuple[str, ...]] | None = None) -> dict[str, Any]:
    validation = validate_voting_ensemble_inventory_startup(actual_active_ids_by_collection)
    actual = actual_active_ids_by_collection or {}
    modules = []
    for entry in VOTING_ENSEMBLE_STRATEGIES:
        actual_ids = actual.get(entry.collection, ())
        modules.append(
            {
                **entry.model_dump(mode="json"),
                "id": entry.strategyId,
                "status": entry.lifecycleStatus,
                "actualRuntimeCount": actual_ids.count(entry.strategyId),
                "implementationImportable": _module_path_importable(entry.implementationPath),
                "forbiddenMutableAlgorithmBinding": _points_to_forbidden_algorithm(entry.implementationPath)
                or _points_to_forbidden_algorithm(entry.runtimeBinding),
            }
        )
    return {
        **validation,
        "status": "ready" if validation["valid"] else "fail_closed",
        "modules": modules,
        "actualRuntimeBindings": {key: list(value) for key, value in actual.items()},
        "promotionPolicy": promotion_policy_status(VOTING_ENSEMBLE_MODULE_INVENTORY),
    }


def _module_path_importable(path: str) -> bool:
    if path == "not_wired":
        return False
    module_path = path.split(":", 1)[0]
    return find_spec(module_path) is not None


def _points_to_forbidden_algorithm(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in FORBIDDEN_MUTABLE_ALGORITHM_PREFIXES)


def _has_promotion_marker(entry: StrategyRegistryEntry, marker: str) -> bool:
    return any(value.startswith(marker) for value in entry.promotionEvidence)


__all__ = [
    "ModuleLifecycleStatus",
    "ModuleRole",
    "ModuleStatus",
    "STRATEGY_ALIAS_MAP",
    "StrategyCollection",
    "StrategyRegistryEntry",
    "VOTING_ENSEMBLE_ACTIVE_CONTEXT_STRATEGIES",
    "VOTING_ENSEMBLE_ACTIVE_DIRECTIONAL_STRATEGIES",
    "VOTING_ENSEMBLE_AGGREGATOR_STRATEGIES",
    "VOTING_ENSEMBLE_BACKGROUND_WORKERS",
    "VOTING_ENSEMBLE_BACKTEST_REPLAY_ADAPTERS",
    "VOTING_ENSEMBLE_CONTEXT_STRATEGIES",
    "VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES",
    "VOTING_ENSEMBLE_EXECUTION_ADAPTERS",
    "VOTING_ENSEMBLE_MODULE_INVENTORY",
    "VOTING_ENSEMBLE_ORDER_PLANNER_MODULES",
    "VOTING_ENSEMBLE_REGIME_STRATEGIES",
    "VOTING_ENSEMBLE_RISK_BUDGET_MODULES",
    "VOTING_ENSEMBLE_SAFETY_STRATEGIES",
    "VOTING_ENSEMBLE_SHADOW_CONTEXT_STRATEGIES",
    "VOTING_ENSEMBLE_SHADOW_DIRECTIONAL_STRATEGIES",
    "VOTING_ENSEMBLE_STRATEGIES",
    "VOTING_ENSEMBLE_TRADING_SETTINGS_RESOLVERS",
    "VotingEnsembleInventory",
    "active_module_ids",
    "canonical_strategy_id",
    "directional_strategy_input_ids",
    "inventory_status",
    "resolve_strategy",
    "shadow_module_ids",
    "validate_voting_ensemble_inventory_startup",
    "voting_ensemble_inventory_status",
]
