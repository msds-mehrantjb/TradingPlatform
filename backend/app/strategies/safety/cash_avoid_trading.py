from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.domain.feature_engine import FeatureQuality, PointInTimeFeatureSnapshot
from backend.app.domain.models import AccountRiskState, GateResult, GateStatus, GlobalGateDecision, StrategyRole, _require_utc
from backend.app.strategies.registry import StrategyCollection, resolve_strategy


SafetyOrderIntent = Literal[
    "new_entry",
    "protective_exit",
    "risk_reducing",
    "end_of_day_liquidation",
    "reconciliation",
]

ENTRY_BLOCKING_INTENTS = frozenset({"new_entry"})
NON_ENTRY_INTENTS = frozenset({"protective_exit", "risk_reducing", "end_of_day_liquidation", "reconciliation"})


class CashAvoidTradingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "cash_avoid_trading_safety_v1"
    manualCashMode: bool = False
    maxSpreadBasisPoints: float = Field(default=12.0, ge=0)
    extremeAtrPercentile: float = Field(default=0.95, ge=0, le=1)
    extremeRealizedVolatilityPercentile: float = Field(default=0.95, ge=0, le=1)
    maxDailyLossPercent: float = Field(default=3.0, ge=0, le=100)
    maxAccountStateAgeSeconds: int = Field(default=120, ge=0)
    maxOperationalStateAgeSeconds: int = Field(default=120, ge=0)
    eventBlackoutImportance: tuple[str, ...] = ("high", "major", "fomc", "cpi", "jobs")

    @property
    def configurationHash(self) -> str:
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


class SafetyOperationalState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    marketOpen: bool | None = None
    eventBlackoutActive: bool | None = None
    haltOrLuld: bool | None = None
    circuitBreaker: bool | None = None
    brokerAccountRestricted: bool | None = None
    manualCashMode: bool | None = None
    restrictionExplanation: str | None = None
    observedAt: datetime

    @field_validator("observedAt")
    @classmethod
    def observed_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


@dataclass(frozen=True)
class SafetyEvaluationContext:
    orderIntent: SafetyOrderIntent
    checkedAt: datetime
    sessionDate: date
    accountRiskState: AccountRiskState | None
    operationalState: SafetyOperationalState | None
    featureSnapshot: PointInTimeFeatureSnapshot | None = None


class CashAvoidTradingSafety:
    registryEntry = resolve_strategy("cash_avoid_trading_filter")

    def __init__(self, config: CashAvoidTradingConfig | None = None) -> None:
        self.config = config or CashAvoidTradingConfig()

    def evaluate(self, context: SafetyEvaluationContext) -> GlobalGateDecision:
        if self.registryEntry.collection != StrategyCollection.SAFETY.value or self.registryEntry.role != StrategyRole.SAFETY.value:
            raise ValueError("Cash / Avoid Trading Filter must be registered as safety")
        checked_at = _require_utc(context.checkedAt)
        reason_codes = self._reason_codes(context, checked_at)
        data_ready = not any(code.startswith(("safety.insufficient_data", "safety.unknown_critical_state", "safety.stale_")) for code in reason_codes)
        blocks_new_entry = bool(context.orderIntent in ENTRY_BLOCKING_INTENTS and self._has_blocking_reason(reason_codes))
        if context.orderIntent in NON_ENTRY_INTENTS:
            reason_codes.append(f"safety.intent_allowed:{context.orderIntent}")
            blocks_new_entry = False
        if not reason_codes:
            reason_codes.append("safety.new_entries_allowed")

        status = GateStatus.FAIL if blocks_new_entry else GateStatus.CAUTION if self._has_blocking_reason(reason_codes) else GateStatus.PASS
        gate = GateResult(
            gateId=self.registryEntry.strategyId,
            gateName=self.registryEntry.strategyName,
            status=status,
            blocksTrading=blocks_new_entry,
            reasonCodes=reason_codes,
            explanation=self._gate_explanation(context.orderIntent, blocks_new_entry, reason_codes),
            checkedAt=checked_at,
            configurationHash=self.config.configurationHash,
        )
        return GlobalGateDecision(
            status=status,
            eligible=not blocks_new_entry,
            dataReady=data_ready,
            gateResults=[gate],
            reasonCodes=reason_codes,
            explanation=self._decision_explanation(context.orderIntent, blocks_new_entry, reason_codes),
            checkedAt=checked_at,
            sessionDate=context.sessionDate,
            configurationHash=self.config.configurationHash,
        )

    def _reason_codes(self, context: SafetyEvaluationContext, checked_at: datetime) -> list[str]:
        reason_codes: list[str] = []
        snapshot = context.featureSnapshot
        if snapshot is None or not snapshot.dataReady:
            reason_codes.append("safety.insufficient_data")
        if snapshot is not None:
            reason_codes.extend(self._feature_reason_codes(snapshot))

        operational_state = context.operationalState
        if operational_state is None:
            reason_codes.extend(
                [
                    "safety.unknown_critical_state:marketOpen",
                    "safety.unknown_critical_state:haltOrLuld",
                    "safety.unknown_critical_state:circuitBreaker",
                    "safety.unknown_critical_state:brokerAccountRestricted",
                ]
            )
        else:
            if (checked_at - operational_state.observedAt).total_seconds() > self.config.maxOperationalStateAgeSeconds:
                reason_codes.append("safety.stale_operational_state")
            reason_codes.extend(self._operational_reason_codes(operational_state))

        account_state = context.accountRiskState
        if account_state is None:
            reason_codes.append("safety.unknown_critical_state:accountRiskState")
        else:
            if (checked_at - account_state.observedAt).total_seconds() > self.config.maxAccountStateAgeSeconds:
                reason_codes.append("safety.stale_account_state")
            if account_state.equity <= 0:
                reason_codes.append("safety.broker_account_restriction:zero_equity")
            daily_loss_limit = account_state.equity * (self.config.maxDailyLossPercent / 100)
            if self.config.maxDailyLossPercent > 0 and account_state.realizedPnlToday <= -daily_loss_limit:
                reason_codes.append("safety.daily_loss_limit")
            if account_state.buyingPower <= 0:
                reason_codes.append("safety.broker_account_restriction:no_buying_power")

        manual_cash_mode = self.config.manualCashMode or bool(operational_state and operational_state.manualCashMode)
        if manual_cash_mode:
            reason_codes.append("safety.manual_cash_mode")
        return _unique(reason_codes)

    def _feature_reason_codes(self, snapshot: PointInTimeFeatureSnapshot) -> list[str]:
        reason_codes: list[str] = []
        spread = _feature_number(snapshot, "spreadBasisPoints")
        if spread is None:
            reason_codes.append("safety.insufficient_data:spread")
        elif spread > self.config.maxSpreadBasisPoints:
            reason_codes.append("safety.extreme_spread")

        realized_volatility = _feature_number(snapshot, "spy1mRealizedVolatilityPercentile")
        if realized_volatility is not None and realized_volatility >= self.config.extremeRealizedVolatilityPercentile:
            reason_codes.append("safety.extreme_volatility:realized")

        atr_percentile = _atr_percentile_from_raw(snapshot)
        if atr_percentile is not None and atr_percentile >= self.config.extremeAtrPercentile:
            reason_codes.append("safety.extreme_volatility:atr")

        event = snapshot.features.get("economicEventState")
        event_state = event.value if event and isinstance(event.value, dict) else snapshot.rawInputs.get("economicEventState")
        if isinstance(event_state, dict):
            importance = str(event_state.get("importance") or event_state.get("category") or "").lower()
            if bool(event_state.get("active") or event_state.get("isActive")) and importance in self.config.eventBlackoutImportance:
                reason_codes.append("safety.event_blackout")
        return reason_codes

    def _operational_reason_codes(self, state: SafetyOperationalState) -> list[str]:
        reason_codes: list[str] = []
        critical = {
            "marketOpen": state.marketOpen,
            "haltOrLuld": state.haltOrLuld,
            "circuitBreaker": state.circuitBreaker,
            "brokerAccountRestricted": state.brokerAccountRestricted,
        }
        for name, value in critical.items():
            if value is None:
                reason_codes.append(f"safety.unknown_critical_state:{name}")
        if state.marketOpen is False:
            reason_codes.append("safety.market_closed")
        if state.eventBlackoutActive is True:
            reason_codes.append("safety.event_blackout")
        if state.haltOrLuld is True:
            reason_codes.append("safety.halt_or_luld")
        if state.circuitBreaker is True:
            reason_codes.append("safety.circuit_breaker")
        if state.brokerAccountRestricted is True:
            reason_codes.append("safety.broker_account_restriction")
        return reason_codes

    def _has_blocking_reason(self, reason_codes: list[str]) -> bool:
        allowed_only = {code for code in reason_codes if code.startswith("safety.intent_allowed") or code == "safety.new_entries_allowed"}
        return bool(set(reason_codes) - allowed_only)

    def _gate_explanation(self, order_intent: SafetyOrderIntent, blocked: bool, reason_codes: list[str]) -> str:
        if blocked:
            return f"Cash / Avoid Trading blocks new entries because: {', '.join(reason_codes)}."
        if order_intent in NON_ENTRY_INTENTS:
            return f"Cash / Avoid Trading does not block {order_intent}; reasons remain visible: {', '.join(reason_codes)}."
        return f"Cash / Avoid Trading allows new entries: {', '.join(reason_codes)}."

    def _decision_explanation(self, order_intent: SafetyOrderIntent, blocked: bool, reason_codes: list[str]) -> str:
        if blocked:
            return f"Hard safety failed closed for automatic new entries: {', '.join(reason_codes)}."
        return f"Hard safety permits {order_intent}: {', '.join(reason_codes)}."


def _feature_number(snapshot: PointInTimeFeatureSnapshot, name: str) -> float | None:
    feature = snapshot.features.get(name)
    if not feature or feature.quality != FeatureQuality.READY.value:
        return None
    return float(feature.value) if isinstance(feature.value, int | float) else None


def _atr_percentile_from_raw(snapshot: PointInTimeFeatureSnapshot) -> float | None:
    candles = snapshot.rawInputs.get("spy1mCandles") or []
    if len(candles) < 20:
        return None
    true_ranges: list[float] = []
    atr_values: list[float] = []
    for index in range(1, len(candles)):
        current = candles[index]
        previous_close = float(candles[index - 1]["close"])
        true_ranges.append(
            max(
                float(current["high"]) - float(current["low"]),
                abs(float(current["high"]) - previous_close),
                abs(float(current["low"]) - previous_close),
            )
        )
        if len(true_ranges) >= 14:
            atr_values.append(sum(true_ranges[-14:]) / 14)
    if len(atr_values) < 5:
        return None
    current = atr_values[-1]
    below = sum(1 for value in atr_values if value < current)
    equal = sum(1 for value in atr_values if value == current)
    return (below + (0.5 * equal)) / len(atr_values)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
