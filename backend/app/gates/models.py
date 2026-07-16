from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import Field, field_validator

from backend.app.domain.models import (
    AccountRiskState,
    ContextSignal,
    DomainModel,
    EnsembleDecision,
    GateResult,
    GateStatus,
    GlobalGateDecision,
    MetaModelPrediction,
    OrderPlan,
    RegimeState,
    StrategyFamily,
    TradeCandidate,
    _require_utc,
)


GLOBAL_GATE_ENGINE_VERSION = "global_gate_engine_v1"

GateOrderIntent = Literal["new_entry", "protective_exit", "risk_reducing", "end_of_day_liquidation", "reconciliation"]
GateSeverity = Literal["hard", "caution", "info"]
ConditionalGateAction = Literal["hard_block", "caution", "info"]


def _default_higher_timeframe_actions() -> dict[StrategyFamily, ConditionalGateAction]:
    return {
        StrategyFamily.TREND: "hard_block",
        StrategyFamily.BREAKOUT: "hard_block",
        StrategyFamily.REVERSAL: "caution",
        StrategyFamily.MEAN_REVERSION: "caution",
        StrategyFamily.GAP_SESSION: "caution",
    }


def _default_context_conflict_actions() -> dict[StrategyFamily, ConditionalGateAction]:
    return {
        StrategyFamily.TREND: "caution",
        StrategyFamily.BREAKOUT: "caution",
        StrategyFamily.REVERSAL: "caution",
        StrategyFamily.MEAN_REVERSION: "caution",
        StrategyFamily.GAP_SESSION: "caution",
    }


def _default_execution_trigger_actions() -> dict[StrategyFamily, ConditionalGateAction]:
    return {
        StrategyFamily.TREND: "hard_block",
        StrategyFamily.BREAKOUT: "hard_block",
        StrategyFamily.REVERSAL: "hard_block",
        StrategyFamily.MEAN_REVERSION: "hard_block",
        StrategyFamily.GAP_SESSION: "hard_block",
    }


def _default_confirmation_actions() -> dict[StrategyFamily, ConditionalGateAction]:
    return {
        StrategyFamily.TREND: "hard_block",
        StrategyFamily.BREAKOUT: "hard_block",
        StrategyFamily.REVERSAL: "caution",
        StrategyFamily.MEAN_REVERSION: "caution",
        StrategyFamily.GAP_SESSION: "caution",
    }


def _default_late_session_actions() -> dict[StrategyFamily, ConditionalGateAction]:
    return {
        StrategyFamily.TREND: "caution",
        StrategyFamily.BREAKOUT: "hard_block",
        StrategyFamily.REVERSAL: "caution",
        StrategyFamily.MEAN_REVERSION: "caution",
        StrategyFamily.GAP_SESSION: "hard_block",
    }


class StrategyConditionalGateConfig(DomainModel):
    configVersion: str = "strategy_conditional_gates_v1"
    weeklyDailyConflictActionByFamily: dict[StrategyFamily, ConditionalGateAction] = Field(default_factory=_default_higher_timeframe_actions)
    oneHourConflictActionByFamily: dict[StrategyFamily, ConditionalGateAction] = Field(default_factory=_default_higher_timeframe_actions)
    contextConflictActionByFamily: dict[StrategyFamily, ConditionalGateAction] = Field(default_factory=_default_context_conflict_actions)
    executionTriggerActionByFamily: dict[StrategyFamily, ConditionalGateAction] = Field(default_factory=_default_execution_trigger_actions)
    fiveMinuteConfirmationActionByFamily: dict[StrategyFamily, ConditionalGateAction] = Field(default_factory=_default_confirmation_actions)
    lateSessionActionByFamily: dict[StrategyFamily, ConditionalGateAction] = Field(default_factory=_default_late_session_actions)
    highAdxThreshold: float = Field(default=30.0, ge=0.0)
    lowAdxThreshold: float = Field(default=16.0, ge=0.0)
    relativeStrengthConflictThreshold: float = Field(default=0.15, ge=0.0)
    breadthConflictThreshold: float = Field(default=0.45, ge=0.0, le=1.0)
    minimumBreadthCoverage: float = Field(default=0.65, ge=0.0, le=1.0)
    lateSessionMinutesUntilClose: int = Field(default=20, ge=0)
    configurationHash: str = Field(default="strategy_conditional_gates_v1", min_length=1)


class GlobalGateConfig(DomainModel):
    gateVersion: str = GLOBAL_GATE_ENGINE_VERSION
    automaticEntriesFailClosed: bool = True
    requireMlWhenEnabled: bool = False
    requireModelHealthWhenEnabled: bool = False
    minimumDeterministicScore: float = Field(default=0.2, ge=0.0, le=1.0)
    minimumIndependentFamilySupport: int = Field(default=2, ge=0)
    minimumExpectedValueAfterCosts: float = 0.0
    minimumMlProbability: float = Field(default=0.55, ge=0.0, le=1.0)
    maximumSpreadBps: float = Field(default=25.0, ge=0.0)
    maximumExpectedSlippageDollars: float = Field(default=0.05, ge=0.0)
    maximumEntryDistanceDollars: float = Field(default=2.0, ge=0.0)
    minimumLiquidityShares: int = Field(default=1, ge=0)
    maximumDailyLossPercent: float = Field(default=3.0, ge=0.0, le=100.0)
    maximumDrawdownFromIntradayHighPercent: float = Field(default=5.0, ge=0.0, le=100.0)
    maximumOpenRiskPercent: float = Field(default=3.0, ge=0.0, le=100.0)
    maximumSpyNotionalPercent: float = Field(default=50.0, ge=0.0, le=100.0)
    maximumSameDirectionExposurePercent: float = Field(default=50.0, ge=0.0, le=100.0)
    maximumTradesPerDay: int = Field(default=10, ge=0)
    maximumConsecutiveLosses: int = Field(default=3, ge=0)
    defaultRiskMultiplierCap: float = Field(default=1.0, ge=0.0, le=1.0)
    defaultMaximumRiskPercent: float = Field(default=1.0, ge=0.0, le=100.0)
    defaultMaximumNotionalPercent: float = Field(default=10.0, ge=0.0, le=100.0)
    conditionalGates: StrategyConditionalGateConfig = Field(default_factory=StrategyConditionalGateConfig)
    configurationHash: str = Field(default="global_gate_config_v1", min_length=1)


class GateCheckResult(DomainModel):
    gateId: str = Field(min_length=1)
    group: str = Field(min_length=1)
    status: GateStatus
    severity: GateSeverity
    blocksNewEntry: bool
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class GlobalGateInput(DomainModel):
    orderIntent: GateOrderIntent
    evaluatedAt: datetime
    sessionDate: date
    symbol: str = Field(default="SPY", min_length=1)
    accountRiskState: AccountRiskState | None = None
    candidate: TradeCandidate | None = None
    candidateStrategyFamily: StrategyFamily | None = None
    setupSubtype: str | None = None
    ensembleDecision: EnsembleDecision | None = None
    metaModelPrediction: MetaModelPrediction | None = None
    regimeState: RegimeState | None = None
    contextSignals: list[ContextSignal] = Field(default_factory=list)
    orderPlan: OrderPlan | None = None
    featureSnapshot: Any | None = None
    dataState: dict[str, Any] = Field(default_factory=dict)
    operationalState: dict[str, Any] = Field(default_factory=dict)
    brokerState: dict[str, Any] = Field(default_factory=dict)
    marketState: dict[str, Any] = Field(default_factory=dict)
    executionState: dict[str, Any] = Field(default_factory=dict)
    riskState: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class GlobalGateEngineDecision(DomainModel):
    allowed: bool
    hardBlockers: list[GateCheckResult] = Field(default_factory=list)
    cautions: list[GateCheckResult] = Field(default_factory=list)
    informationalResults: list[GateCheckResult] = Field(default_factory=list)
    riskMultiplierCap: float = Field(ge=0.0, le=1.0)
    maximumRiskDollars: float = Field(ge=0.0)
    maximumNotionalDollars: float = Field(ge=0.0)
    evaluatedAt: datetime
    sessionDate: date
    gateVersion: str = Field(min_length=1)
    configurationHash: str = Field(min_length=1)
    reasonCodes: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    def to_global_gate_decision(self) -> GlobalGateDecision:
        gate_results = [
            GateResult(
                gateId=result.gateId,
                gateName=result.group,
                status=result.status,
                blocksTrading=result.blocksNewEntry,
                reasonCodes=result.reasonCodes,
                explanation=result.explanation,
                checkedAt=self.evaluatedAt,
                configurationHash=self.configurationHash,
            )
            for result in [*self.hardBlockers, *self.cautions, *self.informationalResults]
        ]
        status = GateStatus.FAIL if self.hardBlockers else GateStatus.CAUTION if self.cautions else GateStatus.PASS
        failure_codes = [
            code
            for result in [*self.hardBlockers, *self.cautions]
            for code in result.reasonCodes
        ]
        return GlobalGateDecision(
            status=status,
            eligible=self.allowed,
            dataReady=not any("critical_feed_unavailable" in code or "data_health" in code for code in failure_codes),
            gateResults=gate_results,
            reasonCodes=self.reasonCodes,
            explanation=self.explanation,
            checkedAt=self.evaluatedAt,
            sessionDate=self.sessionDate,
            configurationHash=self.configurationHash,
        )


def global_gate_configuration_hash(config: GlobalGateConfig, extra: dict[str, Any] | None = None) -> str:
    payload = {"config": config.model_dump(mode="json"), "extra": extra or {}}
    serialized = json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value
