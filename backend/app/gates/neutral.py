from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator

from backend.app.domain.models import DomainModel, GateStatus, _require_utc


NEUTRAL_GLOBAL_GATE_SERVICE_VERSION = "neutral_global_gate_service_v1"

NeutralGlobalGateIntent = Literal["new_entry", "protective_exit", "risk_reducing", "end_of_day_liquidation", "reconciliation"]
NeutralGateSeverity = Literal["hard", "caution", "info"]
NeutralGateAction = Literal["allow", "reduce_quantity", "reject_new_entry", "exits_only", "emergency_liquidation"]
NON_ENTRY_INTENTS = {"protective_exit", "risk_reducing", "end_of_day_liquidation", "reconciliation"}


class NeutralGlobalGateConfig(DomainModel):
    serviceVersion: str = NEUTRAL_GLOBAL_GATE_SERVICE_VERSION
    configurationVersion: str = "neutral_global_gate_config_v1"
    catastrophicDailyLossPercent: float = Field(default=5.0, ge=0.0, le=100.0)
    maximumAccountDrawdownPercent: float = Field(default=8.0, ge=0.0, le=100.0)
    maximumTotalOpenRiskPercent: float = Field(default=5.0, ge=0.0, le=100.0)
    maximumGrossExposurePercent: float = Field(default=150.0, ge=0.0)
    maximumNetExposurePercent: float = Field(default=100.0, ge=0.0)
    maximumPerSymbolExposurePercent: float = Field(default=50.0, ge=0.0)
    minimumBuyingPowerReservePercent: float = Field(default=10.0, ge=0.0, le=100.0)
    buyingPowerReserveCautionPercent: float = Field(default=15.0, ge=0.0, le=100.0)
    maximumPendingOrderRiskPercent: float = Field(default=2.0, ge=0.0, le=100.0)
    maximumOrderRatePerMinute: int = Field(default=30, ge=0)
    maximumAbsoluteSpreadBps: float = Field(default=75.0, ge=0.0)
    candleFreshnessSeconds: int = Field(default=120, ge=0)
    quoteFreshnessSeconds: int = Field(default=15, ge=0)
    maximumClockDriftSeconds: int = Field(default=2, ge=0)
    configurationHash: str = Field(default="neutral_global_gate_config_v1", min_length=1)


class NeutralOperationalState(DomainModel):
    masterTradingEnabled: bool
    paperTradingMode: bool
    liveTradingRequested: bool = False
    allowedSession: bool
    marketCalendarOpen: bool
    entryWindowOpen: bool
    orderApiHealthy: bool
    brokerConnected: bool
    accountNotRestricted: bool
    systemClockHealthy: bool
    systemClockDriftSeconds: float | None = Field(default=None, ge=0.0)
    emergencyKillSwitch: bool = False


class NeutralDataState(DomainModel):
    freshCandle: bool
    freshQuote: bool
    candleAgeSeconds: float | None = Field(default=None, ge=0.0)
    quoteAgeSeconds: float | None = Field(default=None, ge=0.0)
    validMarketData: bool
    corruptedMarketData: bool = False


class NeutralMarketState(DomainModel):
    tradingHalt: bool = False
    luldActive: bool = False
    marketWideCircuitBreaker: bool = False
    absoluteSpreadBps: float | None = Field(default=None, ge=0.0)
    globalEventBlackout: bool = False


class NeutralAccountRiskState(DomainModel):
    equity: float = Field(gt=0.0)
    dailyPnl: float = 0.0
    accountDrawdownPercent: float = Field(default=0.0, ge=0.0)
    totalOpenRiskPercent: float = Field(default=0.0, ge=0.0)
    grossExposurePercent: float = Field(default=0.0, ge=0.0)
    netExposurePercent: float = Field(default=0.0, ge=0.0)
    perSymbolExposurePercent: float = Field(default=0.0, ge=0.0)
    buyingPowerReservePercent: float = Field(default=100.0, ge=0.0, le=100.0)
    pendingOrderRiskPercent: float = Field(default=0.0, ge=0.0)


class NeutralOrderFlowState(DomainModel):
    orderRateLastMinute: int = Field(default=0, ge=0)
    duplicateOrder: bool = False
    idempotencyKeySeen: bool = False
    idempotencyKeyValid: bool = True


class NeutralGlobalGateInput(DomainModel):
    intent: NeutralGlobalGateIntent
    evaluatedAt: datetime
    sessionDate: date
    symbol: str = Field(default="SPY", min_length=1)
    operational: NeutralOperationalState
    data: NeutralDataState
    market: NeutralMarketState
    accountRisk: NeutralAccountRiskState
    orderFlow: NeutralOrderFlowState

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class NeutralGlobalGateResult(DomainModel):
    gateId: str = Field(min_length=1)
    group: str = Field(min_length=1)
    status: GateStatus
    severity: NeutralGateSeverity
    blocksNewEntry: bool
    reasonCodes: tuple[str, ...] = Field(default_factory=tuple)
    explanation: str = Field(min_length=1)


class NeutralGlobalGateDecision(DomainModel):
    serviceVersion: str = NEUTRAL_GLOBAL_GATE_SERVICE_VERSION
    allowed: bool
    action: NeutralGateAction
    riskReducingExitAllowed: bool
    emergencyLiquidationRequired: bool
    quantityMultiplierCap: float = Field(ge=0.0, le=1.0)
    gateResults: tuple[NeutralGlobalGateResult, ...]
    hardBlockers: tuple[NeutralGlobalGateResult, ...]
    cautions: tuple[NeutralGlobalGateResult, ...]
    informationalResults: tuple[NeutralGlobalGateResult, ...]
    reasonCodes: tuple[str, ...]
    evaluatedAt: datetime
    sessionDate: date
    configurationHash: str = Field(min_length=1)
    explanation: str = Field(min_length=1)

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class NeutralGlobalGateService:
    """Direction-neutral account, broker, infrastructure, and emergency gates."""

    def __init__(self, config: NeutralGlobalGateConfig | None = None) -> None:
        self.config = config or NeutralGlobalGateConfig()

    def evaluate(self, inputs: NeutralGlobalGateInput | dict[str, Any]) -> NeutralGlobalGateDecision:
        context = inputs if isinstance(inputs, NeutralGlobalGateInput) else NeutralGlobalGateInput(**inputs)
        results = (
            *self._operational(context),
            *self._data_health(context),
            *self._market_safety(context),
            *self._account_risk(context),
            *self._order_flow(context),
        )
        emergency = self._emergency_liquidation_required(context)
        hard = tuple(result for result in results if result.blocksNewEntry and context.intent == "new_entry")
        cautions = tuple(
            result
            for result in results
            if result.severity == "caution"
            or (context.intent in NON_ENTRY_INTENTS and result.blocksNewEntry)
        )
        infos = tuple(result for result in results if result.severity == "info" and result not in hard)
        allowed = context.intent != "new_entry" or not hard
        quantity_cap = 0.0 if hard else 0.5 if cautions and context.intent == "new_entry" else 1.0
        action = self._action(context, hard, cautions, emergency)
        reason_codes = tuple(code for result in (*hard, *cautions, *infos) for code in result.reasonCodes)
        config_hash = neutral_global_gate_configuration_hash(self.config, {"intent": context.intent, "symbol": context.symbol})
        return NeutralGlobalGateDecision(
            allowed=allowed,
            action=action,
            riskReducingExitAllowed=True,
            emergencyLiquidationRequired=emergency,
            quantityMultiplierCap=quantity_cap,
            gateResults=results,
            hardBlockers=hard,
            cautions=cautions,
            informationalResults=infos,
            reasonCodes=reason_codes or ("neutral_global_gate.all_passed",),
            evaluatedAt=context.evaluatedAt,
            sessionDate=context.sessionDate,
            configurationHash=config_hash,
            explanation="Neutral global gates permit this intent." if allowed else "Neutral global gates reject new entry without altering algorithm state.",
        )

    def _operational(self, context: NeutralGlobalGateInput) -> tuple[NeutralGlobalGateResult, ...]:
        state = context.operational
        results = [
            _bool_gate("global.operational.master_switch", "Operational", state.masterTradingEnabled, "neutral_global_gate.operational.master_switch_off", "Master trading switch is enabled."),
            _bool_gate("global.operational.paper_environment", "Operational", state.paperTradingMode and not state.liveTradingRequested, "neutral_global_gate.operational.live_environment_blocked", "Paper-trading environment guard passed."),
            _bool_gate("global.operational.market_calendar", "Operational", state.marketCalendarOpen, "neutral_global_gate.operational.market_calendar_closed", "Market calendar is open."),
            _bool_gate("global.operational.allowed_session", "Operational", state.allowedSession, "neutral_global_gate.operational.session_not_allowed", "Session is allowed."),
            _bool_gate("global.operational.entry_cutoff", "Operational", state.entryWindowOpen, "neutral_global_gate.operational.entry_cutoff", "New-entry cutoff has not been reached."),
            _bool_gate("global.operational.broker_connection", "Operational", state.brokerConnected, "neutral_global_gate.operational.broker_disconnected", "Broker connection is healthy."),
            _bool_gate("global.operational.account_status", "Operational", state.accountNotRestricted, "neutral_global_gate.operational.account_restricted", "Account is unrestricted."),
            _bool_gate("global.operational.order_api", "Operational", state.orderApiHealthy, "neutral_global_gate.operational.order_api_unhealthy", "Order API is healthy."),
            _bool_gate("global.operational.system_clock", "Operational", state.systemClockHealthy, "neutral_global_gate.operational.clock_unhealthy", "System clock is healthy."),
            _false_gate("global.operational.kill_switch", "Operational", state.emergencyKillSwitch, "neutral_global_gate.operational.kill_switch", "Emergency kill switch is inactive."),
        ]
        if state.systemClockDriftSeconds is not None and state.systemClockDriftSeconds > self.config.maximumClockDriftSeconds:
            results.append(_fail("global.operational.clock_drift", "Operational", ("neutral_global_gate.operational.clock_drift",), "System clock drift exceeds the configured global limit."))
        return tuple(results)

    def _data_health(self, context: NeutralGlobalGateInput) -> tuple[NeutralGlobalGateResult, ...]:
        state = context.data
        results = [
            _bool_gate("global.data.fresh_candle", "Data health", state.freshCandle, "neutral_global_gate.data.stale_candle", "Candle data is fresh."),
            _bool_gate("global.data.fresh_quote", "Data health", state.freshQuote, "neutral_global_gate.data.stale_quote", "Quote data is fresh."),
            _bool_gate("global.data.valid_market_data", "Data health", state.validMarketData, "neutral_global_gate.data.invalid_market_data", "Market data is valid."),
            _false_gate("global.data.corrupted_market_data", "Data health", state.corruptedMarketData, "neutral_global_gate.data.corrupted_market_data", "Market data is not corrupted."),
        ]
        if state.candleAgeSeconds is not None and state.candleAgeSeconds > self.config.candleFreshnessSeconds:
            results.append(_fail("global.data.candle_age", "Data health", ("neutral_global_gate.data.candle_age_exceeded",), "Candle age exceeds the configured global freshness limit."))
        if state.quoteAgeSeconds is not None and state.quoteAgeSeconds > self.config.quoteFreshnessSeconds:
            results.append(_fail("global.data.quote_age", "Data health", ("neutral_global_gate.data.quote_age_exceeded",), "Quote age exceeds the configured global freshness limit."))
        return tuple(results)

    def _market_safety(self, context: NeutralGlobalGateInput) -> tuple[NeutralGlobalGateResult, ...]:
        state = context.market
        results = [
            _false_gate("global.market.trading_halt", "Market safety", state.tradingHalt, "neutral_global_gate.market.trading_halt", "No trading halt is active."),
            _false_gate("global.market.luld", "Market safety", state.luldActive, "neutral_global_gate.market.luld_active", "No LULD state is active."),
            _false_gate("global.market.circuit_breaker", "Market safety", state.marketWideCircuitBreaker, "neutral_global_gate.market.circuit_breaker", "No market-wide circuit breaker is active."),
            _false_gate("global.market.event_blackout", "Market safety", state.globalEventBlackout, "neutral_global_gate.market.event_blackout", "No global event blackout is active."),
        ]
        if state.absoluteSpreadBps is None:
            results.append(_info("global.market.spread_unavailable", "Market safety", ("neutral_global_gate.market.spread_unavailable",), "Absolute spread was not supplied to the global gate service."))
        elif state.absoluteSpreadBps > self.config.maximumAbsoluteSpreadBps:
            results.append(_fail("global.market.absolute_spread", "Market safety", ("neutral_global_gate.market.absolute_spread_exceeded",), "Absolute emergency spread ceiling is exceeded."))
        else:
            results.append(_pass("global.market.absolute_spread", "Market safety", ("neutral_global_gate.market.absolute_spread_passed",), "Absolute emergency spread ceiling passed."))
        return tuple(results)

    def _account_risk(self, context: NeutralGlobalGateInput) -> tuple[NeutralGlobalGateResult, ...]:
        state = context.accountRisk
        daily_loss_percent = max(0.0, (-state.dailyPnl / state.equity) * 100.0)
        results: list[NeutralGlobalGateResult] = []
        comparisons = [
            ("global.risk.daily_loss", daily_loss_percent, self.config.catastrophicDailyLossPercent, "neutral_global_gate.risk.catastrophic_daily_loss", "Catastrophic account daily-loss limit."),
            ("global.risk.drawdown", state.accountDrawdownPercent, self.config.maximumAccountDrawdownPercent, "neutral_global_gate.risk.account_drawdown", "Account drawdown stop."),
            ("global.risk.total_open_risk", state.totalOpenRiskPercent, self.config.maximumTotalOpenRiskPercent, "neutral_global_gate.risk.total_open_risk", "Maximum total open risk."),
            ("global.risk.gross_exposure", state.grossExposurePercent, self.config.maximumGrossExposurePercent, "neutral_global_gate.risk.gross_exposure", "Maximum gross exposure."),
            ("global.risk.net_exposure", abs(state.netExposurePercent), self.config.maximumNetExposurePercent, "neutral_global_gate.risk.net_exposure", "Maximum net exposure."),
            ("global.risk.symbol_exposure", state.perSymbolExposurePercent, self.config.maximumPerSymbolExposurePercent, "neutral_global_gate.risk.symbol_exposure", "Per-symbol emergency exposure."),
            ("global.risk.pending_order_risk", state.pendingOrderRiskPercent, self.config.maximumPendingOrderRiskPercent, "neutral_global_gate.risk.pending_order_risk", "Pending-order risk."),
        ]
        for gate_id, value, limit, code, label in comparisons:
            if value > limit:
                results.append(_fail(gate_id, "Global account risk", (code,), f"{label} exceeds the configured global limit."))
            else:
                results.append(_pass(gate_id, "Global account risk", (f"{code}:passed",), f"{label} is inside the configured global limit."))
        if state.buyingPowerReservePercent < self.config.minimumBuyingPowerReservePercent:
            results.append(_fail("global.risk.buying_power_reserve", "Global account risk", ("neutral_global_gate.risk.buying_power_reserve",), "Buying-power reserve is below the hard global floor."))
        elif state.buyingPowerReservePercent < self.config.buyingPowerReserveCautionPercent:
            results.append(_caution("global.risk.buying_power_reserve", "Global account risk", ("neutral_global_gate.risk.buying_power_reserve_caution",), "Buying-power reserve is near the hard global floor."))
        else:
            results.append(_pass("global.risk.buying_power_reserve", "Global account risk", ("neutral_global_gate.risk.buying_power_reserve:passed",), "Buying-power reserve passed."))
        return tuple(results)

    def _order_flow(self, context: NeutralGlobalGateInput) -> tuple[NeutralGlobalGateResult, ...]:
        state = context.orderFlow
        results = [
            _bool_gate("global.order_flow.idempotency_key", "Order flow", state.idempotencyKeyValid, "neutral_global_gate.order_flow.invalid_idempotency_key", "Idempotency key is valid."),
            _false_gate("global.order_flow.duplicate_order", "Order flow", state.duplicateOrder, "neutral_global_gate.order_flow.duplicate_order", "No duplicate order is detected."),
            _false_gate("global.order_flow.idempotency_duplicate", "Order flow", state.idempotencyKeySeen, "neutral_global_gate.order_flow.idempotency_duplicate", "Idempotency key has not already been used."),
        ]
        if state.orderRateLastMinute > self.config.maximumOrderRatePerMinute:
            results.append(_fail("global.order_flow.order_rate", "Order flow", ("neutral_global_gate.order_flow.rate_limit",), "Order-rate limit is exceeded."))
        else:
            results.append(_pass("global.order_flow.order_rate", "Order flow", ("neutral_global_gate.order_flow.rate_limit:passed",), "Order-rate limit passed."))
        results.append(_info("global.order_flow.exit_protection", "Order flow", ("neutral_global_gate.order_flow.risk_reducing_exit_protected",), "Risk-reducing exits remain available when new entries are blocked."))
        return tuple(results)

    def _emergency_liquidation_required(self, context: NeutralGlobalGateInput) -> bool:
        return bool(
            context.operational.emergencyKillSwitch
            or context.market.marketWideCircuitBreaker
            or context.accountRisk.accountDrawdownPercent > self.config.maximumAccountDrawdownPercent
            or max(0.0, (-context.accountRisk.dailyPnl / context.accountRisk.equity) * 100.0) > self.config.catastrophicDailyLossPercent
        )

    def _action(
        self,
        context: NeutralGlobalGateInput,
        hard: tuple[NeutralGlobalGateResult, ...],
        cautions: tuple[NeutralGlobalGateResult, ...],
        emergency: bool,
    ) -> NeutralGateAction:
        if emergency:
            return "emergency_liquidation"
        if context.intent in NON_ENTRY_INTENTS:
            return "allow"
        if hard:
            if any(not result.gateId.startswith("global.order_flow.") for result in hard):
                return "exits_only"
            return "reject_new_entry"
        if cautions:
            return "reduce_quantity"
        return "allow"


def neutral_global_gate_configuration_hash(config: NeutralGlobalGateConfig, extra: dict[str, Any] | None = None) -> str:
    payload = {"config": config.model_dump(mode="json"), "extra": extra or {}}
    serialized = json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _bool_gate(gate_id: str, group: str, value: bool, reason_code: str, passed: str) -> NeutralGlobalGateResult:
    if not value:
        return _fail(gate_id, group, (reason_code,), passed.replace(" passed", " failed").replace(" is enabled", " is disabled"))
    return _pass(gate_id, group, (f"{reason_code}:passed",), passed)


def _false_gate(gate_id: str, group: str, value: bool, reason_code: str, passed: str) -> NeutralGlobalGateResult:
    if value:
        return _fail(gate_id, group, (reason_code,), passed.replace("No ", "").replace(" is inactive", " is active"))
    return _pass(gate_id, group, (f"{reason_code}:passed",), passed)


def _fail(gate_id: str, group: str, reason_codes: tuple[str, ...], explanation: str) -> NeutralGlobalGateResult:
    return _gate(gate_id, group, GateStatus.FAIL, "hard", True, reason_codes, explanation)


def _caution(gate_id: str, group: str, reason_codes: tuple[str, ...], explanation: str) -> NeutralGlobalGateResult:
    return _gate(gate_id, group, GateStatus.CAUTION, "caution", False, reason_codes, explanation)


def _pass(gate_id: str, group: str, reason_codes: tuple[str, ...], explanation: str) -> NeutralGlobalGateResult:
    return _gate(gate_id, group, GateStatus.PASS, "info", False, reason_codes, explanation)


def _info(gate_id: str, group: str, reason_codes: tuple[str, ...], explanation: str) -> NeutralGlobalGateResult:
    return _gate(gate_id, group, GateStatus.INFO, "info", False, reason_codes, explanation)


def _gate(
    gate_id: str,
    group: str,
    status: GateStatus,
    severity: NeutralGateSeverity,
    blocks: bool,
    reason_codes: tuple[str, ...],
    explanation: str,
) -> NeutralGlobalGateResult:
    return NeutralGlobalGateResult(gateId=gate_id, group=group, status=status, severity=severity, blocksNewEntry=blocks, reasonCodes=reason_codes, explanation=explanation)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
