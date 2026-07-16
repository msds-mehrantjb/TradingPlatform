from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Iterable, Protocol

from pydantic import Field

from backend.app.domain.feature_engine import (
    MarketCandle,
    OpeningRangeLevels,
    PointInTimeFeatureEngine,
    PointInTimeFeatureRequest,
    PointInTimeFeatureSnapshot,
    PremarketLevels,
    PriorDayOHLC,
)
from backend.app.domain.models import (
    AccountRiskState,
    BaselineTradingSettings,
    ContextSignal,
    Direction,
    DomainModel,
    DynamicPolicyBounds,
    EffectiveTradePolicy,
    EnsembleDecision,
    GlobalGateDecision,
    HardRiskLimits,
    OperatingMode,
    OrderPlan,
    RegimeState,
    Signal,
    StrategyFamily,
    TradeCandidate,
)
from backend.app.domain.trading_settings import trading_settings_configuration_hash
from backend.app.ensemble.family_aware import FamilyAwareDeterministicEnsemble
from backend.app.execution.simulation import ExecutionSimulationConfig, RealisticExecutionSimulator
from backend.app.gates import BrokerAccountSnapshot, BrokerPositionState, GlobalGateEngine, GlobalGateInput, aggregate_global_account_risk
from backend.app.ml.features import MLFeatureSet
from backend.app.ml.inference import SafeMLInferenceConfig, SafeMLInferenceResult, apply_safe_ml_inference
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.safety import (
    CashAvoidTradingSafety,
    SafetyEvaluationContext,
    SafetyOperationalState,
)


class ContextModule(Protocol):
    registryEntry: Any

    def evaluate(self, context: StrategyEvaluationContext) -> ContextSignal:
        ...


class RegimeModule(Protocol):
    registryEntry: Any

    def evaluate(self, context: StrategyEvaluationContext) -> RegimeState:
        ...


class DynamicPolicyEngine(Protocol):
    def effective_policy(
        self,
        *,
        candidate: TradeCandidate | None,
        ensembleDecision: EnsembleDecision,
        gateDecision: GlobalGateDecision,
        mlDecision: SafeMLInferenceResult,
        accountRiskState: AccountRiskState,
        decidedAt: datetime,
        sessionDate: date,
    ) -> EffectiveTradePolicy:
        ...


class OrderValidator(Protocol):
    def order_plan(
        self,
        *,
        candidate: TradeCandidate | None,
        policy: EffectiveTradePolicy,
        gateDecision: GlobalGateDecision,
        mlDecision: SafeMLInferenceResult,
        decidedAt: datetime,
        sessionDate: date,
    ) -> OrderPlan | None:
        ...


class CandidateFeatureBuilder(Protocol):
    def __call__(
        self,
        *,
        snapshotId: str,
        symbol: str,
        decisionTimestamp: datetime,
        schemaHash: str,
        featureSnapshot: PointInTimeFeatureSnapshot,
        ensembleDecision: EnsembleDecision,
    ) -> MLFeatureSet:
        ...


class ReplayEngineConfig(DomainModel):
    engineVersion: str = "event_driven_replay_engine_v1"
    decisionIntervalSeconds: int = Field(default=60, ge=1)
    minWarmupCandles: int = Field(default=20, ge=1)
    submitOrderDelaySeconds: int = Field(default=1, ge=1)
    defaultTargetDistance: float = Field(default=1.0, gt=0)
    defaultStopDistance: float = Field(default=0.75, gt=0)
    startingEquity: float = Field(default=100_000.0, gt=0)
    execution: ExecutionSimulationConfig = Field(default_factory=ExecutionSimulationConfig)
    sessionRules: "ReplaySessionRules" = Field(default_factory=lambda: ReplaySessionRules())
    featureSchemaHash: str = Field(default="event_replay_candidate_features_v1", min_length=1)
    configurationHash: str = Field(default="event_replay_config_v1", min_length=1)


class ReplaySessionRules(DomainModel):
    configVersion: str = "replay_session_rules_v1"
    entryStartTimeUtc: time = time(14, 30)
    newEntryCutoffTimeUtc: time = time(20, 45)
    endOfDayLiquidationTimeUtc: time = time(20, 55)
    maxConcurrentPositions: int = Field(default=1, ge=0)
    maxSymbolExposure: int = Field(default=1, ge=0)
    cooldownAfterEntrySeconds: int = Field(default=0, ge=0)
    cooldownAfterStopSeconds: int = Field(default=0, ge=0)
    maxEntriesPerSetup: int = Field(default=1, ge=1)
    maxTradesPerDay: int = Field(default=10, ge=0)
    pyramidingAllowed: bool = False
    duplicateOrderPrevention: bool = True


class ReplayDecisionSnapshot(DomainModel):
    snapshotId: str
    symbol: str
    decisionTimestampUtc: datetime
    sessionDate: date
    maxInputTimestampUtc: datetime | None
    featureSnapshot: dict[str, Any]
    strategyOutputs: list[dict[str, Any]]
    contextOutputs: list[dict[str, Any]]
    regimeState: dict[str, Any] | None
    gateDecision: dict[str, Any]
    deterministicCandidate: dict[str, Any] | None
    ensembleDecision: dict[str, Any]
    mlInference: dict[str, Any]
    effectivePolicy: dict[str, Any]
    orderPlan: dict[str, Any] | None
    fill: dict[str, Any] | None
    exit: dict[str, Any] | None
    reasonCodes: list[str]


class ReplayTrade(DomainModel):
    tradeId: str
    decisionSnapshotId: str
    symbol: str
    side: Signal
    quantity: int
    submittedAt: datetime
    filledAt: datetime | None
    entryPrice: float | None
    exitAt: datetime | None
    exitPrice: float | None
    pnl: float
    costs: dict[str, float]
    fillStatus: str
    exitStatus: str | None
    reasonCodes: list[str]


class ReplayResult(DomainModel):
    engineVersion: str
    symbol: str
    sessionDate: date
    decisionCount: int
    snapshots: list[ReplayDecisionSnapshot]
    trades: list[ReplayTrade]
    explanation: str


@dataclass
class ReplaySessionState:
    trades: list[ReplayTrade] = field(default_factory=list)
    seenOrderKeys: set[str] = field(default_factory=set)
    setupEntryCounts: dict[str, int] = field(default_factory=dict)
    lastEntryAt: datetime | None = None
    lastStopAt: datetime | None = None

    def active_positions(self, timestamp: datetime, symbol: str) -> list[ReplayTrade]:
        return [
            trade
            for trade in self.trades
            if trade.symbol == symbol.upper() and (trade.exitAt is None or trade.exitAt > timestamp)
        ]


@dataclass(frozen=True)
class ReplayComponents:
    featureEngine: PointInTimeFeatureEngine = field(default_factory=PointInTimeFeatureEngine)
    directionalStrategies: tuple[Any, ...] = ()
    contextModules: tuple[ContextModule, ...] = ()
    regimeModule: RegimeModule | None = None
    familyEnsemble: FamilyAwareDeterministicEnsemble = field(default_factory=FamilyAwareDeterministicEnsemble)
    safetyModule: CashAvoidTradingSafety = field(default_factory=CashAvoidTradingSafety)
    globalGateEngine: GlobalGateEngine = field(default_factory=GlobalGateEngine)
    mlConfig: SafeMLInferenceConfig = field(default_factory=SafeMLInferenceConfig)
    mlModelArtifact: dict[str, Any] | None = None
    policyEngine: DynamicPolicyEngine = field(default_factory=lambda: DefaultReplayPolicyEngine())
    orderValidator: OrderValidator = field(default_factory=lambda: DefaultReplayOrderValidator())
    executionSimulator: RealisticExecutionSimulator | None = None
    featureBuilder: CandidateFeatureBuilder | None = None


class EventDrivenReplayEngine:
    def __init__(self, components: ReplayComponents | None = None, config: ReplayEngineConfig | None = None) -> None:
        self.components = components or ReplayComponents()
        self.config = config or ReplayEngineConfig()

    def replay_session(
        self,
        *,
        symbol: str,
        sessionDate: date,
        spy1mCandles: list[MarketCandle],
        spy5mCandles: list[MarketCandle] | None = None,
        spy15mCandles: list[MarketCandle] | None = None,
        qqqCandles: list[MarketCandle] | None = None,
        iwmCandles: list[MarketCandle] | None = None,
        priorDayOHLC: PriorDayOHLC | None = None,
        premarket: PremarketLevels | None = None,
        openingRange: OpeningRangeLevels | None = None,
        breadthComponents: dict[str, list[MarketCandle]] | None = None,
        economicEventState: dict[str, Any] | None = None,
    ) -> ReplayResult:
        ordered = sorted(spy1mCandles, key=lambda candle: candle.timestamp)
        snapshots: list[ReplayDecisionSnapshot] = []
        state = ReplaySessionState()
        execution_simulator = self.components.executionSimulator or RealisticExecutionSimulator(self.config.execution)
        for index, candle in enumerate(ordered):
            if index + 1 < self.config.minWarmupCandles:
                continue
            if not self._is_decision_timestamp(candle.timestamp):
                continue
            decision = self.decide_at(
                symbol=symbol,
                sessionDate=sessionDate,
                evaluationTimestamp=candle.timestamp,
                spy1mCandles=ordered[: index + 1],
                spy5mCandles=prefix(spy5mCandles or [], candle.timestamp),
                spy15mCandles=prefix(spy15mCandles or [], candle.timestamp),
                qqqCandles=prefix(qqqCandles or [], candle.timestamp),
                iwmCandles=prefix(iwmCandles or [], candle.timestamp),
                priorDayOHLC=priorDayOHLC,
                premarket=premarket,
                openingRange=openingRange,
                breadthComponents=prefix_components(breadthComponents or {}, candle.timestamp),
                economicEventState=economicEventState or {},
                brokerAccountSnapshot=replay_broker_snapshot(
                    state=state,
                    symbol=symbol,
                    session_date=sessionDate,
                    observed_at=candle.timestamp,
                    mark_price=candle.close,
                    equity=self.config.startingEquity,
                ),
            )
            order_plan = OrderPlan.model_validate(decision.orderPlan) if decision.orderPlan else None
            rule_reason_codes: list[str] = []
            if order_plan:
                order_plan, rule_reason_codes = apply_session_rules(order_plan, decision, state, self.config.sessionRules)
                decision = decision.model_copy(
                    update={
                        "orderPlan": order_plan.model_dump(mode="json"),
                        "reasonCodes": sorted(set([*decision.reasonCodes, *rule_reason_codes])),
                    }
                )
            execution_candles = candles_through_liquidation(ordered[index + 1 :], sessionDate, self.config.sessionRules)
            execution = execution_simulator.simulate(order_plan, execution_candles, decision.decisionTimestampUtc) if order_plan else None
            fill = execution.fill.model_dump(mode="json") if execution else None
            exit_payload = execution.exit.model_dump(mode="json") if execution and execution.exit else None
            decision = decision.model_copy(update={"fill": fill, "exit": exit_payload})
            snapshots.append(decision)
            if execution and order_plan and execution.fill.filledQuantity > 0:
                trade = ReplayTrade(
                    tradeId=f"trade-{decision.snapshotId}",
                    decisionSnapshotId=decision.snapshotId,
                    symbol=symbol,
                    side=order_plan.side,
                    quantity=execution.fill.filledQuantity,
                    submittedAt=execution.fill.submittedAt,
                    filledAt=execution.fill.filledAt,
                    entryPrice=execution.fill.averagePrice,
                    exitAt=execution.exit.exitAt if execution.exit else None,
                    exitPrice=execution.exit.exitPrice if execution.exit else None,
                    pnl=execution.exit.pnl if execution.exit else 0.0,
                    costs={
                        "entry": execution.fill.costs.get("total", 0.0),
                        "exit": execution.exit.costs.get("total", 0.0) if execution.exit else 0.0,
                        "total": execution.fill.costs.get("total", 0.0) + (execution.exit.costs.get("total", 0.0) if execution.exit else 0.0),
                    },
                    fillStatus=execution.fill.status,
                    exitStatus=execution.exit.status if execution.exit else None,
                    reasonCodes=["trade.linked_to_decision_snapshot", *execution.reasonCodes],
                )
                state.trades.append(trade)
                state.lastEntryAt = trade.filledAt
                if trade.exitStatus == "EXITED" and execution.exit and execution.exit.exitReason == "protective_stop":
                    state.lastStopAt = trade.exitAt
                state.setupEntryCounts[setup_key(decision)] = state.setupEntryCounts.get(setup_key(decision), 0) + 1
        return ReplayResult(
            engineVersion=self.config.engineVersion,
            symbol=symbol.upper(),
            sessionDate=sessionDate,
            decisionCount=len(snapshots),
            snapshots=snapshots,
            trades=state.trades,
            explanation="Event-driven replay used point-in-time data prefixes and live-style V2 decision components.",
        )

    def decide_at(
        self,
        *,
        symbol: str,
        sessionDate: date,
        evaluationTimestamp: datetime,
        spy1mCandles: list[MarketCandle],
        spy5mCandles: list[MarketCandle],
        spy15mCandles: list[MarketCandle],
        qqqCandles: list[MarketCandle],
        iwmCandles: list[MarketCandle],
        priorDayOHLC: PriorDayOHLC | None = None,
        premarket: PremarketLevels | None = None,
        openingRange: OpeningRangeLevels | None = None,
        breadthComponents: dict[str, list[MarketCandle]] | None = None,
        economicEventState: dict[str, Any] | None = None,
        accountRiskState: AccountRiskState | None = None,
        brokerAccountSnapshot: BrokerAccountSnapshot | None = None,
    ) -> ReplayDecisionSnapshot:
        evaluation_at = evaluationTimestamp.astimezone(UTC)
        self._reject_future_inputs(evaluation_at, spy1mCandles, spy5mCandles, spy15mCandles, qqqCandles, iwmCandles, breadthComponents or {})
        feature_snapshot = self.components.featureEngine.compute(
            PointInTimeFeatureRequest(
                evaluationTimestamp=evaluation_at,
                sessionDate=sessionDate,
                spy1mCandles=spy1mCandles,
                spy5mCandles=spy5mCandles,
                spy15mCandles=spy15mCandles,
                qqqAlignedCandles=qqqCandles,
                iwmAlignedCandles=iwmCandles,
                priorDayOHLC=priorDayOHLC,
                premarket=premarket,
                openingRange=openingRange,
                economicEventState=economicEventState or {},
                breadthComponents=breadthComponents or {},
                executionStyle="backtest",
            )
        )
        strategy_signals = self._run_directional(feature_snapshot)
        context_signals = self._run_context(feature_snapshot)
        regime_state = self._run_regime(feature_snapshot)
        preliminary_risk = (
            aggregate_global_account_risk(brokerAccountSnapshot, candidateSymbol=symbol)
            if brokerAccountSnapshot
            else None
        )
        account_state = accountRiskState or (preliminary_risk.accountRiskState if preliminary_risk else self._account_state(sessionDate, evaluation_at))
        safety_decision = self.components.safetyModule.evaluate(
            SafetyEvaluationContext(
                orderIntent="new_entry",
                checkedAt=evaluation_at,
                sessionDate=sessionDate,
                accountRiskState=account_state,
                operationalState=SafetyOperationalState(
                    marketOpen=True,
                    eventBlackoutActive=False,
                    haltOrLuld=False,
                    circuitBreaker=False,
                    brokerAccountRestricted=False,
                    manualCashMode=False,
                    observedAt=evaluation_at,
                ),
                featureSnapshot=feature_snapshot,
            )
        )
        ensemble_decision = self.components.familyEnsemble.aggregate(
            strategySignals=strategy_signals,
            contextSignals=context_signals,
            regimeState=regime_state,
            safetyDecision=safety_decision,
            decidedAt=evaluation_at,
            sessionDate=sessionDate,
        )
        candidate = self._candidate(symbol, ensemble_decision, feature_snapshot, evaluation_at, sessionDate)
        snapshot_id = self._snapshot_id(symbol, evaluation_at, ensemble_decision)
        authoritative_risk = (
            aggregate_global_account_risk(brokerAccountSnapshot, candidateSymbol=symbol, candidateSide=candidate.signal if candidate else None)
            if brokerAccountSnapshot
            else preliminary_risk
        )
        if authoritative_risk:
            account_state = authoritative_risk.accountRiskState
        pre_order_gate = self.components.globalGateEngine.evaluate(
            self._global_gate_input(
                symbol=symbol,
                session_date=sessionDate,
                evaluated_at=evaluation_at,
                feature_snapshot=feature_snapshot,
                account_risk_state=account_state,
                candidate=candidate,
                ensemble_decision=ensemble_decision,
                regime_state=regime_state,
                context_signals=context_signals,
                broker_state=authoritative_risk.brokerState if authoritative_risk else None,
                risk_state=authoritative_risk.riskState if authoritative_risk else None,
            )
        ).to_global_gate_decision()
        feature_set = self._feature_set(
            snapshot_id=snapshot_id,
            symbol=symbol,
            decision_timestamp=evaluation_at,
            feature_snapshot=feature_snapshot,
            ensemble_decision=ensemble_decision,
        )
        ml_decision = apply_safe_ml_inference(
            deterministic_signal=ensemble_decision.signal,
            feature_set=feature_set,
            model_artifact=self.components.mlModelArtifact,
            config=self.components.mlConfig,
            hard_gates_passed=pre_order_gate.eligible,
            candidate_eligible=bool(candidate and ensemble_decision.eligible),
            session_date=sessionDate,
            predicted_at=evaluation_at,
        )
        policy = self.components.policyEngine.effective_policy(
            candidate=candidate,
            ensembleDecision=ensemble_decision,
            gateDecision=pre_order_gate,
            mlDecision=ml_decision,
            accountRiskState=account_state,
            decidedAt=evaluation_at,
            sessionDate=sessionDate,
        )
        order_plan = self.components.orderValidator.order_plan(
            candidate=candidate,
            policy=policy,
            gateDecision=pre_order_gate,
            mlDecision=ml_decision,
            decidedAt=evaluation_at,
            sessionDate=sessionDate,
        )
        final_gate = self.components.globalGateEngine.evaluate(
            self._global_gate_input(
                symbol=symbol,
                session_date=sessionDate,
                evaluated_at=evaluation_at,
                feature_snapshot=feature_snapshot,
                account_risk_state=account_state,
                candidate=candidate,
                ensemble_decision=ensemble_decision,
                regime_state=regime_state,
                context_signals=context_signals,
                broker_state=authoritative_risk.brokerState if authoritative_risk else None,
                risk_state=authoritative_risk.riskState if authoritative_risk else None,
                order_plan=order_plan,
            )
        ).to_global_gate_decision()
        if order_plan and order_plan.eligible and not final_gate.eligible:
            order_plan = blocked_order_plan(order_plan, final_gate.reasonCodes, evaluation_at)
        return ReplayDecisionSnapshot(
            snapshotId=snapshot_id,
            symbol=symbol.upper(),
            decisionTimestampUtc=evaluation_at,
            sessionDate=sessionDate,
            maxInputTimestampUtc=max_input_timestamp(spy1mCandles, spy5mCandles, spy15mCandles, qqqCandles, iwmCandles, breadthComponents or {}),
            featureSnapshot=feature_snapshot.model_dump(mode="json"),
            strategyOutputs=[signal.model_dump(mode="json") for signal in strategy_signals],
            contextOutputs=[signal.model_dump(mode="json") for signal in context_signals],
            regimeState=regime_state.model_dump(mode="json") if regime_state else None,
            gateDecision=final_gate.model_dump(mode="json"),
            deterministicCandidate=candidate.model_dump(mode="json") if candidate else None,
            ensembleDecision=ensemble_decision.model_dump(mode="json"),
            mlInference=ml_decision.model_dump(mode="json"),
            effectivePolicy=policy.model_dump(mode="json"),
            orderPlan=order_plan.model_dump(mode="json") if order_plan else None,
            fill=None,
            exit=None,
            reasonCodes=["replay.point_in_time_prefix", "replay.live_style_components"],
        )

    def _run_directional(self, feature_snapshot: PointInTimeFeatureSnapshot):
        if not self.components.directionalStrategies:
            return []
        return self.components.familyEnsemble.run_directional_strategies(
            StrategyEvaluationContext(
                registryEntry=self.components.familyEnsemble.registryEntry,
                featureSnapshot=feature_snapshot,
                configurationHash=self.config.configurationHash,
            ),
            self.components.directionalStrategies,
        )

    def _run_context(self, feature_snapshot: PointInTimeFeatureSnapshot) -> list[ContextSignal]:
        return [
            module.evaluate(
                StrategyEvaluationContext(
                    registryEntry=module.registryEntry,
                    featureSnapshot=feature_snapshot,
                    configurationHash=self.config.configurationHash,
                )
            )
            for module in self.components.contextModules
        ]

    def _run_regime(self, feature_snapshot: PointInTimeFeatureSnapshot) -> RegimeState | None:
        module = self.components.regimeModule
        if module is None:
            return None
        return module.evaluate(
            StrategyEvaluationContext(
                registryEntry=module.registryEntry,
                featureSnapshot=feature_snapshot,
                configurationHash=self.config.configurationHash,
            )
        )

    def _candidate(
        self,
        symbol: str,
        decision: EnsembleDecision,
        features: PointInTimeFeatureSnapshot,
        decided_at: datetime,
        session_date: date,
    ) -> TradeCandidate | None:
        if decision.signal == Signal.HOLD.value or not decision.eligible:
            return None
        latest_close = feature_number(features, "spy1mClose") or latest_raw_close(features)
        if latest_close is None:
            return None
        if decision.signal == Signal.BUY.value:
            stop = latest_close - self.config.defaultStopDistance
            target = latest_close + self.config.defaultTargetDistance
            direction = Direction.LONG
        else:
            stop = latest_close + self.config.defaultStopDistance
            target = latest_close - self.config.defaultTargetDistance
            direction = Direction.SHORT
        return TradeCandidate(
            candidateId=f"candidate-{decision.decisionId}",
            symbol=symbol.upper(),
            signal=Signal(decision.signal),
            direction=direction,
            entryPrice=latest_close,
            stopPrice=stop,
            targetPrice=target,
            quantity=1,
            confidence=decision.confidence,
            expectedValue=None,
            features=candidate_strategy_features(decision),
            reasonCodes=["replay.deterministic_candidate"],
            explanation="Replay candidate derived from deterministic family-aware ensemble output.",
            generatedAt=decided_at,
            sessionDate=session_date,
            configurationHash=self.config.configurationHash,
        )

    def _feature_set(
        self,
        *,
        snapshot_id: str,
        symbol: str,
        decision_timestamp: datetime,
        feature_snapshot: PointInTimeFeatureSnapshot,
        ensemble_decision: EnsembleDecision,
    ) -> MLFeatureSet:
        if self.components.featureBuilder:
            return self.components.featureBuilder(
                snapshotId=snapshot_id,
                symbol=symbol,
                decisionTimestamp=decision_timestamp,
                schemaHash=self.config.featureSchemaHash,
                featureSnapshot=feature_snapshot,
                ensembleDecision=ensemble_decision,
            )
        missing = {name: value.quality != "READY" for name, value in feature_snapshot.features.items()}
        values = {
            "target_distance": self.config.defaultTargetDistance,
            "stop_distance": self.config.defaultStopDistance,
            "expected_transaction_cost": 0.0,
            "deterministic_score": ensemble_decision.finalScore,
        }
        return MLFeatureSet(
            schemaHash=self.config.featureSchemaHash,
            snapshotId=snapshot_id,
            symbol=symbol.upper(),
            decisionTimestampUtc=decision_timestamp.isoformat(),
            featureValues=values,
            missingIndicators=missing,
            forbiddenFieldsChecked=["finalOutcome", "fills", "brokerSubmissionResult", "metaModelPrediction"],
            explanation="Replay decision-time feature set for safe ML inference.",
        )

    def _global_gate_input(
        self,
        *,
        symbol: str,
        session_date: date,
        evaluated_at: datetime,
        feature_snapshot: PointInTimeFeatureSnapshot,
        account_risk_state: AccountRiskState,
        candidate: TradeCandidate | None,
        ensemble_decision: EnsembleDecision,
        regime_state: RegimeState | None,
        context_signals: list[ContextSignal],
        broker_state: dict[str, Any] | None = None,
        risk_state: dict[str, Any] | None = None,
        order_plan: OrderPlan | None = None,
    ) -> GlobalGateInput:
        spread_bps = feature_number(feature_snapshot, "spreadBasisPoints")
        realized_volatility = feature_number(feature_snapshot, "spy1mRealizedVolatilityPercentile")
        latest_volume = latest_raw_volume(feature_snapshot)
        strategy_family, setup_subtype = candidate_strategy_context(ensemble_decision)
        return GlobalGateInput(
            orderIntent="new_entry",
            evaluatedAt=evaluated_at,
            sessionDate=session_date,
            symbol=symbol.upper(),
            accountRiskState=account_risk_state,
            candidate=candidate,
            candidateStrategyFamily=strategy_family,
            setupSubtype=setup_subtype,
            ensembleDecision=ensemble_decision,
            regimeState=regime_state,
            contextSignals=context_signals,
            orderPlan=order_plan,
            featureSnapshot=feature_snapshot,
            operationalState={
                "tradingEnabled": True,
                "paperTradingMode": True,
                "marketOpen": True,
                "entryWindowOpen": True,
                "validSession": True,
            },
            dataState={
                "freshCandle": feature_snapshot.dataReady,
                "freshQuote": True,
                "validBidAsk": True,
                "monotonicTimestamps": True,
                "requiredTimeframeSynchronized": not any(code.startswith("missing_spy") for code in feature_snapshot.reasonCodes),
                "requiredAuxiliaryDataReady": not any(code.startswith(("qqq_", "iwm_", "breadth_")) for code in feature_snapshot.reasonCodes),
                "featureSchemaValid": bool(feature_snapshot.engineVersion),
            },
            brokerState=broker_state or {
                "brokerConnected": True,
                "paperAccountActive": True,
                "accountNotRestricted": True,
                "symbolTradable": True,
                "buyingPowerCurrent": True,
                "positionsReconciled": True,
                "openOrdersReconciled": True,
            },
            marketState={
                "symbolHalt": False,
                "luldPause": False,
                "marketWideCircuitBreaker": False,
                "lockedOrCrossedQuote": False,
                "spreadBps": spread_bps,
                "realizedVolatilityPercentile": realized_volatility,
            },
            executionState={
                "liquidityShares": latest_volume if latest_volume is not None else 1,
                "spreadBps": spread_bps,
                "expectedSlippageDollars": 0.0,
                "entryDistanceDollars": 0.0,
                "duplicateOrder": False,
                "conflictingOrder": False,
                "cooldownActive": False,
                "riskWithinBudget": True,
                "notionalWithinCap": True,
                "protectiveOrderPossible": True,
                "uniqueClientOrderId": True,
            },
            riskState=risk_state or {
                "drawdownFromIntradayHighPercent": 0.0,
                "totalOpenRiskPercent": 0.0,
                "totalSpyNotionalPercent": 0.0,
                "sameDirectionExposurePercent": 0.0,
                "consecutiveLosses": 0,
                "modelHealthy": True,
            },
        )

    def _account_state(self, session_date: date, observed_at: datetime) -> AccountRiskState:
        return AccountRiskState(
            accountId="paper-replay-account",
            equity=self.config.startingEquity,
            buyingPower=self.config.startingEquity,
            openPositionNotional=0.0,
            realizedPnlToday=0.0,
            tradesToday=0,
            observedAt=observed_at,
            sessionDate=session_date,
        )

    def _reject_future_inputs(self, evaluation_at: datetime, *collections: Any) -> None:
        latest = max_input_timestamp(*collections)
        if latest and latest > evaluation_at:
            raise ValueError("event replay cannot pass future candles into decision code")

    def _is_decision_timestamp(self, timestamp: datetime) -> bool:
        return int(timestamp.timestamp()) % self.config.decisionIntervalSeconds == 0

    def _snapshot_id(self, symbol: str, timestamp: datetime, decision: EnsembleDecision) -> str:
        payload = {
            "symbol": symbol.upper(),
            "timestamp": timestamp.isoformat(),
            "decisionId": decision.decisionId,
            "config": self.config.configurationHash,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        return f"replay-snapshot-{digest}"


class DefaultReplayPolicyEngine:
    def effective_policy(
        self,
        *,
        candidate: TradeCandidate | None,
        ensembleDecision: EnsembleDecision,
        gateDecision: GlobalGateDecision,
        mlDecision: SafeMLInferenceResult,
        accountRiskState: AccountRiskState,
        decidedAt: datetime,
        sessionDate: date,
    ) -> EffectiveTradePolicy:
        risk_cap = mlDecision.recommendedRiskCap if mlDecision.effectiveMode == OperatingMode.ACTIVE.value else 1.0
        baseline_settings = BaselineTradingSettings(
            startingCapital=accountRiskState.equity or 1.0,
            orderAllocationPercent=1.0,
            dailyAllocationPercent=5.0,
            riskBudgetPercentOfOrder=1.0,
            maxTradesPerDay=10,
            stopLossPercent=1.0,
            fixedStopDistanceDollars=0.75,
            takeProfitR=1.5,
            slippagePerShare=0.0,
            positionSizingMode="risk",
            settingsVersion="replay_baseline_settings_v2",
            configurationHash="replay_baseline_settings_v2",
        )
        hard_limits = HardRiskLimits(
            maxDailyLossPercent=3.0,
            maxOrderNotional=accountRiskState.equity,
            maxPositionNotional=accountRiskState.equity,
            maxShareQuantity=1000,
            minStopDistanceDollars=0.01,
            maxSlippagePerShare=1.0,
            configurationHash="replay_hard_limits_v2",
        )
        dynamic_bounds = DynamicPolicyBounds(
            minConfidence=0.0,
            minReliability=0.0,
            minRegimeFit=0.0,
            maxSpreadPercent=100.0,
            maxParticipationPercent=100.0,
            minLiquidityShares=0,
            configurationHash="replay_dynamic_bounds_v2",
        )
        policy_hash = trading_settings_configuration_hash(
            baseline_settings=baseline_settings,
            hard_limits=hard_limits,
            dynamic_bounds=dynamic_bounds,
            strategy_configuration_hash="replay_strategy_config_v2",
            ensemble_configuration_hash=ensembleDecision.configurationHash,
            ml_configuration_hash=mlDecision.configurationHash,
            risk_configuration_hash=hard_limits.configurationHash,
            sizing_configuration_hash=baseline_settings.configurationHash,
            entry_configuration_hash="replay_entry_v2",
            exit_configuration_hash="replay_exit_v2",
            gate_configuration_hash=gateDecision.configurationHash,
            backtest_configuration_hash="event_driven_replay_engine_v1",
        )
        return EffectiveTradePolicy(
            mode=OperatingMode(mlDecision.effectiveMode),
            baselineSettings=baseline_settings,
            hardRiskLimits=hard_limits,
            dynamicBounds=dynamic_bounds,
            accountRiskState=accountRiskState,
            maxQuantity=max(0, int(100 * risk_cap)),
            maxNotional=accountRiskState.equity * 0.01 * risk_cap,
            riskDollars=accountRiskState.equity * 0.001 * risk_cap,
            explanation="Replay effective policy created after gate and ML inference evaluation.",
            effectiveAt=decidedAt,
            sessionDate=sessionDate,
            configurationHash=policy_hash,
        )


class DefaultReplayOrderValidator:
    def order_plan(
        self,
        *,
        candidate: TradeCandidate | None,
        policy: EffectiveTradePolicy,
        gateDecision: GlobalGateDecision,
        mlDecision: SafeMLInferenceResult,
        decidedAt: datetime,
        sessionDate: date,
    ) -> OrderPlan | None:
        if candidate is None:
            return None
        if not gateDecision.eligible or not mlDecision.candidateAccepted or policy.maxQuantity <= 0:
            return OrderPlan(
                orderPlanId=f"no-order-{candidate.candidateId}",
                candidateId=candidate.candidateId,
                symbol=candidate.symbol,
                side=candidate.signal,
                orderType="NO_ORDER",
                quantity=0,
                entryPrice=candidate.entryPrice,
                stopPrice=candidate.stopPrice,
                targetPrice=candidate.targetPrice,
                limitPrice=None,
                timeInForce="DAY",
                eligible=False,
                validationErrors=["order.blocked_by_gate_ml_or_policy"],
                explanation="Replay order validator blocked the candidate.",
                generatedAt=decidedAt,
                sessionDate=sessionDate,
                configurationHash=policy.configurationHash,
            )
        quantity = min(candidate.quantity or 1, policy.maxQuantity)
        return OrderPlan(
            orderPlanId=f"order-{candidate.candidateId}",
            candidateId=candidate.candidateId,
            symbol=candidate.symbol,
            side=candidate.signal,
            orderType="LIMIT",
            quantity=quantity,
            entryPrice=candidate.entryPrice,
            stopPrice=candidate.stopPrice,
            targetPrice=candidate.targetPrice,
            limitPrice=candidate.entryPrice,
            timeInForce="DAY",
            eligible=True,
            validationErrors=[],
            explanation="Replay order validator accepted a bounded paper order plan.",
            generatedAt=decidedAt,
            sessionDate=sessionDate,
            configurationHash=policy.configurationHash,
        )


def apply_session_rules(
    order_plan: OrderPlan,
    decision: ReplayDecisionSnapshot,
    state: ReplaySessionState,
    rules: ReplaySessionRules,
) -> tuple[OrderPlan, list[str]]:
    if order_plan.orderType == "NO_ORDER" or not order_plan.eligible:
        return order_plan, []
    timestamp = decision.decisionTimestampUtc
    reason_codes: list[str] = []
    if timestamp.time() < rules.entryStartTimeUtc:
        reason_codes.append("session.entry_before_start")
    if timestamp.time() >= rules.newEntryCutoffTimeUtc:
        reason_codes.append("session.new_entry_cutoff")
    if timestamp.time() >= rules.endOfDayLiquidationTimeUtc:
        reason_codes.append("session.after_eod_liquidation_time")
    active_positions = state.active_positions(timestamp, order_plan.symbol)
    if active_positions and not rules.pyramidingAllowed:
        reason_codes.append("session.pyramiding_disabled")
    if len(active_positions) >= rules.maxConcurrentPositions:
        reason_codes.append("session.max_concurrent_positions")
    if len(active_positions) >= rules.maxSymbolExposure:
        reason_codes.append("session.global_symbol_exposure_limit")
    if len(state.trades) >= rules.maxTradesPerDay:
        reason_codes.append("session.max_trades_per_day")
    if state.lastEntryAt and (timestamp - state.lastEntryAt).total_seconds() < rules.cooldownAfterEntrySeconds:
        reason_codes.append("session.cooldown_after_entry")
    if state.lastStopAt and (timestamp - state.lastStopAt).total_seconds() < rules.cooldownAfterStopSeconds:
        reason_codes.append("session.cooldown_after_stop")
    key = setup_key(decision)
    if state.setupEntryCounts.get(key, 0) >= rules.maxEntriesPerSetup:
        reason_codes.append("session.max_entries_per_setup")
    order_key = duplicate_order_key(order_plan, timestamp)
    if rules.duplicateOrderPrevention and order_key in state.seenOrderKeys:
        reason_codes.append("session.duplicate_order_prevented")
    if reason_codes:
        return blocked_order_plan(order_plan, reason_codes, timestamp), reason_codes
    state.seenOrderKeys.add(order_key)
    return order_plan, ["session.new_entry_rules_passed"]


def blocked_order_plan(order_plan: OrderPlan, reason_codes: list[str], timestamp: datetime) -> OrderPlan:
    return OrderPlan(
        orderPlanId=f"blocked-{order_plan.orderPlanId}",
        candidateId=order_plan.candidateId,
        symbol=order_plan.symbol,
        side=order_plan.side,
        orderType="NO_ORDER",
        quantity=0,
        entryPrice=order_plan.entryPrice,
        stopPrice=order_plan.stopPrice,
        targetPrice=order_plan.targetPrice,
        limitPrice=None,
        timeInForce=order_plan.timeInForce,
        eligible=False,
        validationErrors=[*order_plan.validationErrors, *reason_codes],
        explanation="Replay session and position rules blocked this new entry; protective exits remain allowed.",
        generatedAt=timestamp,
        sessionDate=order_plan.sessionDate,
        configurationHash=order_plan.configurationHash,
    )


def candles_through_liquidation(candles: list[MarketCandle], session_date: date, rules: ReplaySessionRules) -> list[MarketCandle]:
    liquidation_at = datetime.combine(session_date, rules.endOfDayLiquidationTimeUtc, tzinfo=UTC)
    return [candle for candle in candles if candle.timestamp <= liquidation_at]


def setup_key(decision: ReplayDecisionSnapshot) -> str:
    ensemble = decision.ensembleDecision
    families = ",".join(sorted(str(family) for family in ensemble.get("supportingFamilies", [])))
    return f"{ensemble.get('signal')}|{families}|{round(float(ensemble.get('finalScore') or 0.0), 2)}"


def duplicate_order_key(order_plan: OrderPlan, timestamp: datetime) -> str:
    return f"{order_plan.symbol.upper()}|{order_plan.side}|{timestamp.isoformat()}"


def prefix(candles: list[MarketCandle], timestamp: datetime) -> list[MarketCandle]:
    return [candle for candle in sorted(candles, key=lambda item: item.timestamp) if candle.timestamp <= timestamp]


def prefix_components(components: dict[str, list[MarketCandle]], timestamp: datetime) -> dict[str, list[MarketCandle]]:
    return {name: prefix(candles, timestamp) for name, candles in components.items()}


def max_input_timestamp(*collections: Any) -> datetime | None:
    timestamps: list[datetime] = []
    for collection in collections:
        if isinstance(collection, dict):
            for candles in collection.values():
                timestamps.extend(candle.timestamp for candle in candles)
        elif isinstance(collection, Iterable):
            timestamps.extend(candle.timestamp for candle in collection if isinstance(candle, MarketCandle))
    return max(timestamps) if timestamps else None


def feature_number(snapshot: PointInTimeFeatureSnapshot, name: str) -> float | None:
    feature = snapshot.features.get(name)
    if not feature:
        return None
    try:
        return float(feature.value)
    except (TypeError, ValueError):
        return None


def latest_raw_close(snapshot: PointInTimeFeatureSnapshot) -> float | None:
    candles = ((snapshot.rawInputs.get("spy1mCandles") or []))
    if not candles:
        return None
    latest = candles[-1]
    return float(latest.get("close")) if isinstance(latest, dict) and latest.get("close") else None


def latest_raw_volume(snapshot: PointInTimeFeatureSnapshot) -> float | None:
    candles = snapshot.rawInputs.get("spy1mCandles") or []
    if not candles:
        return None
    latest = candles[-1]
    return float(latest.get("volume")) if isinstance(latest, dict) and latest.get("volume") is not None else None


def candidate_strategy_features(decision: EnsembleDecision) -> dict[str, Any]:
    family, setup_subtype = candidate_strategy_context(decision)
    return {
        "strategyFamily": family.value if family else None,
        "setupSubtype": setup_subtype,
    }


def candidate_strategy_context(decision: EnsembleDecision) -> tuple[StrategyFamily | None, str]:
    candidate_direction = int(decision.direction)
    matching = [
        signal
        for signal in decision.strategySignals
        if signal.eligible
        and signal.dataReady
        and int(signal.direction) == candidate_direction
        and signal.setupDetected
    ]
    if matching:
        strongest = max(matching, key=lambda signal: float(signal.confidence) * float(signal.reliability) * float(signal.regimeFit))
        return StrategyFamily(strongest.family), strongest.strategyId
    if decision.supportingFamilies:
        return StrategyFamily(decision.supportingFamilies[0]), "family_aware_candidate"
    return None, "unspecified"


def replay_broker_snapshot(
    *,
    state: ReplaySessionState,
    symbol: str,
    session_date: date,
    observed_at: datetime,
    mark_price: float,
    equity: float,
) -> BrokerAccountSnapshot:
    active_positions = state.active_positions(observed_at, symbol)
    return BrokerAccountSnapshot(
        accountId="paper-replay-account",
        equity=equity,
        buyingPower=equity,
        realizedPnlToday=sum(trade.pnl for trade in state.trades if trade.exitAt and trade.exitAt.date() == observed_at.date()),
        intradayEquityHigh=equity,
        positions=[
            BrokerPositionState(
                algorithmId="meta_strategy",
                symbol=trade.symbol,
                side=trade.side,
                quantity=trade.quantity,
                averageEntryPrice=trade.entryPrice or mark_price,
                markPrice=mark_price,
                stopPrice=None,
                openedAt=trade.filledAt,
            )
            for trade in active_positions
        ],
        pendingOrders=[],
        partiallyFilledOrders=[],
        observedAt=observed_at,
        sessionDate=session_date,
        sourceAuthority="broker",
        positionsReconciled=True,
        openOrdersReconciled=True,
    )
