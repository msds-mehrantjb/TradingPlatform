from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.app.domain.feature_engine import MarketCandle
from backend.app.domain.models import DomainModel, OrderPlan, Signal


class ExecutionSimulationConfig(DomainModel):
    configVersion: str = "execution_simulation_v1"
    scenarioName: str = "baseline_costs"
    costMultiplier: float = Field(default=1.0, ge=0.0)
    latencySeconds: int = Field(default=1, ge=0)
    queueLatencySeconds: int = Field(default=0, ge=0)
    routingLatencySeconds: int = Field(default=0, ge=0)
    quoteAgeSeconds: float = Field(default=0.0, ge=0.0)
    maxQuoteAgeSeconds: float = Field(default=5.0, ge=0.0)
    bidAskSpreadDollars: float = Field(default=0.02, ge=0.0)
    spreadWideningMultiplier: float = Field(default=1.0, ge=0.0)
    openingSessionSpreadMultiplier: float = Field(default=1.0, ge=0.0)
    slippagePerShare: float = Field(default=0.01, ge=0.0)
    volatilitySlippageMultiplier: float = Field(default=1.0, ge=0.0)
    participationSlippageMultiplier: float = Field(default=1.0, ge=0.0)
    adverseSelectionBps: float = Field(default=0.0, ge=0.0)
    feesPerShare: float = Field(default=0.0, ge=0.0)
    maxVolumeParticipation: float = Field(default=0.10, gt=0.0, le=1.0)
    liquidityHaircut: float = Field(default=1.0, ge=0.0, le=1.0)
    partialFillRatio: float | None = Field(default=None, ge=0.0, le=1.0)
    forceNoFill: bool = False
    brokerReject: bool = False
    exchangeHalt: bool = False
    eventShock: bool = False
    orderExpirationSeconds: int = Field(default=300, ge=1)
    cancelReplaceEnabled: bool = False
    cancelReplaceAfterSeconds: int = Field(default=60, ge=1)
    maxCancelReplaceAttempts: int = Field(default=0, ge=0)
    replacementPriceOffsetBps: float = Field(default=0.0, ge=0.0)
    stopGapSlippageMultiplier: float = Field(default=1.0, ge=0.0)
    targetGapSlippageMultiplier: float = Field(default=1.0, ge=0.0)
    conservativeSameBarRule: Literal["STOP_FIRST"] = "STOP_FIRST"
    endOfDayExit: bool = True


class SimulatedFill(DomainModel):
    status: Literal["FILLED", "PARTIAL", "UNFILLED", "EXPIRED"]
    filledQuantity: int = Field(ge=0)
    requestedQuantity: int = Field(ge=0)
    averagePrice: float | None = Field(default=None, gt=0)
    filledAt: datetime | None = None
    submittedAt: datetime
    side: Signal
    orderType: str
    reasonCodes: list[str] = Field(default_factory=list)
    costs: dict[str, float]


class SimulatedExit(DomainModel):
    status: Literal["EXITED", "OPEN"]
    exitReason: Literal["protective_stop", "profit_target", "strategy_invalidation", "time_stop", "end_of_day", "open"]
    exitPrice: float | None = Field(default=None, gt=0)
    exitAt: datetime | None = None
    grossPnl: float = 0.0
    pnl: float
    costs: dict[str, float]
    reasonCodes: list[str] = Field(default_factory=list)


class SimulatedExecution(DomainModel):
    fill: SimulatedFill
    exit: SimulatedExit | None = None
    reasonCodes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unfilled_orders_have_no_exit(self) -> "SimulatedExecution":
        if self.fill.filledQuantity == 0 and self.exit is not None:
            raise ValueError("unfilled orders cannot have exits")
        return self


class RealisticExecutionSimulator:
    def __init__(self, config: ExecutionSimulationConfig | None = None) -> None:
        self.config = config or ExecutionSimulationConfig()

    def simulate(self, order_plan: OrderPlan, future_candles: list[MarketCandle], decision_at: datetime) -> SimulatedExecution:
        submitted_at = decision_at + timedelta(seconds=self.config.latencySeconds + self.config.queueLatencySeconds + self.config.routingLatencySeconds)
        eligible_candles = [candle for candle in future_candles if candle.timestamp > submitted_at]
        fill = self._simulate_entry(order_plan, eligible_candles, submitted_at)
        if fill.filledQuantity <= 0:
            return SimulatedExecution(fill=fill, reasonCodes=fill.reasonCodes)
        exit_result = self._simulate_bracket_exit(order_plan, fill, eligible_candles)
        return SimulatedExecution(fill=fill, exit=exit_result, reasonCodes=[*fill.reasonCodes, *(exit_result.reasonCodes if exit_result else [])])

    def _simulate_entry(self, order_plan: OrderPlan, candles: list[MarketCandle], submitted_at: datetime) -> SimulatedFill:
        requested = int(order_plan.quantity)
        if not order_plan.eligible or order_plan.orderType == "NO_ORDER" or requested <= 0:
            return self._unfilled(order_plan, submitted_at, "execution.order_not_eligible")
        if self.config.brokerReject:
            return self._unfilled(order_plan, submitted_at, "execution.broker_rejection")
        if self.config.exchangeHalt:
            return self._unfilled(order_plan, submitted_at, "execution.exchange_halt")
        if self.config.quoteAgeSeconds > self.config.maxQuoteAgeSeconds:
            return self._unfilled(order_plan, submitted_at, "execution.stale_quote")
        if self.config.forceNoFill:
            return self._unfilled(order_plan, submitted_at, "execution.scenario_no_fill")
        expires_at = submitted_at + timedelta(seconds=self.config.orderExpirationSeconds)
        replacement_attempts = 0
        working_order = order_plan
        for candle in candles:
            if candle.timestamp > expires_at:
                return self._expired(working_order, submitted_at, "execution.order_expired")
            working_order, replacement_attempts, replacement_reason = self._maybe_replace_order(working_order, candle, submitted_at, replacement_attempts)
            touched, entry_price, reason = self._entry_touched(working_order, candle)
            if not touched or entry_price is None:
                continue
            volume_capacity = max(0, int(candle.volume * self.config.maxVolumeParticipation * self.config.liquidityHaircut))
            if self.config.partialFillRatio is not None:
                volume_capacity = min(volume_capacity, max(0, int(requested * self.config.partialFillRatio)))
            fill_quantity = min(requested, volume_capacity)
            if fill_quantity <= 0:
                return self._unfilled(working_order, submitted_at, "execution.no_volume_available")
            status = "FILLED" if fill_quantity == requested else "PARTIAL"
            reason_codes = [reason, replacement_reason, side_conservative_reason(working_order.side, is_entry=True), scenario_reason(self.config)]
            if status == "PARTIAL":
                reason_codes.append("execution.partial_fill_volume_participation")
            if self.config.adverseSelectionBps > 0:
                reason_codes.append("execution.adverse_selection_priced")
            if self.config.spreadWideningMultiplier > 1 or self.config.openingSessionSpreadMultiplier > 1:
                reason_codes.append("execution.spread_widening_scenario")
            return SimulatedFill(
                status=status,
                filledQuantity=fill_quantity,
                requestedQuantity=requested,
                averagePrice=entry_price,
                filledAt=candle.timestamp,
                submittedAt=submitted_at,
                side=working_order.side,
                orderType=working_order.orderType,
                reasonCodes=[code for code in reason_codes if code],
                costs=costs(fill_quantity, self.config),
            )
        return self._expired(working_order, submitted_at, "execution.order_unfilled")

    def _maybe_replace_order(self, order_plan: OrderPlan, candle: MarketCandle, submitted_at: datetime, attempts: int) -> tuple[OrderPlan, int, str]:
        if not self.config.cancelReplaceEnabled or attempts >= self.config.maxCancelReplaceAttempts:
            return order_plan, attempts, ""
        if candle.timestamp < submitted_at + timedelta(seconds=self.config.cancelReplaceAfterSeconds):
            return order_plan, attempts, ""
        if order_plan.limitPrice is None or self.config.replacementPriceOffsetBps <= 0:
            return order_plan, attempts, ""
        side = Signal(order_plan.side)
        offset = order_plan.limitPrice * (self.config.replacementPriceOffsetBps / 10000.0)
        replacement = order_plan.limitPrice + offset if side == Signal.BUY else max(0.01, order_plan.limitPrice - offset)
        return order_plan.model_copy(update={"limitPrice": round(replacement, 6)}), attempts + 1, "execution.cancel_replace_adjusted_limit"

    def _entry_touched(self, order_plan: OrderPlan, candle: MarketCandle) -> tuple[bool, float | None, str]:
        side = Signal(order_plan.side)
        if order_plan.orderType == "MARKET":
            return True, entry_price(side, candle.open, self.config), "execution.market_entry_next_executable"
        if order_plan.orderType == "LIMIT":
            if side == Signal.BUY and candle.low <= (order_plan.limitPrice or order_plan.entryPrice):
                return True, entry_price(side, min(order_plan.limitPrice or order_plan.entryPrice, candle.open), self.config, candle=candle), "execution.limit_entry_touched"
            if side == Signal.SELL and candle.high >= (order_plan.limitPrice or order_plan.entryPrice):
                return True, entry_price(side, max(order_plan.limitPrice or order_plan.entryPrice, candle.open), self.config, candle=candle), "execution.limit_entry_touched"
            return False, None, "execution.limit_not_touched"
        if order_plan.orderType == "STOP_LIMIT":
            stop_triggered = candle.high >= order_plan.entryPrice if side == Signal.BUY else candle.low <= order_plan.entryPrice
            if not stop_triggered:
                return False, None, "execution.stop_not_triggered"
            limit_price = order_plan.limitPrice or order_plan.entryPrice
            limit_fillable = candle.low <= limit_price if side == Signal.BUY else candle.high >= limit_price
            if not limit_fillable:
                return False, None, "execution.stop_limit_triggered_not_filled"
            return True, entry_price(side, limit_price, self.config, candle=candle), "execution.stop_limit_entry_filled"
        return False, None, "execution.unsupported_order_type"

    def _simulate_bracket_exit(self, order_plan: OrderPlan, fill: SimulatedFill, candles: list[MarketCandle]) -> SimulatedExit:
        side = Signal(order_plan.side)
        for candle in candles:
            if fill.filledAt and candle.timestamp < fill.filledAt:
                continue
            if strategy_invalidation_touched(side, order_plan, candle):
                return self._exit(order_plan, fill, candle.close, candle.timestamp, "strategy_invalidation", ["execution.strategy_invalidation_exit"])
            if time_stop_touched(order_plan, fill, candle):
                return self._exit(order_plan, fill, candle.close, candle.timestamp, "time_stop", ["execution.time_stop_exit"])
            stop_hit = stop_touched(side, order_plan, candle)
            target_hit = target_touched(side, order_plan, candle)
            ambiguous = stop_hit and target_hit
            if ambiguous:
                reason_codes = ["execution.same_bar_target_stop_ambiguous", "execution.conservative_stop_first"]
                return self._exit(order_plan, fill, order_plan.stopPrice, candle.timestamp, "protective_stop", reason_codes, candle=candle)
            if stop_hit:
                return self._exit(order_plan, fill, stop_gap_price(side, order_plan, candle, self.config), candle.timestamp, "protective_stop", ["execution.protective_stop_hit", gap_reason(side, order_plan, candle, is_stop=True)], candle=candle)
            if target_hit:
                return self._exit(order_plan, fill, target_gap_price(side, order_plan, candle, self.config), candle.timestamp, "profit_target", ["execution.profit_target_hit", gap_reason(side, order_plan, candle, is_stop=False)], candle=candle)
        if self.config.endOfDayExit and order_plan.endOfDayExit and candles:
            last = candles[-1]
            return self._exit(order_plan, fill, last.close, last.timestamp, "end_of_day", ["execution.end_of_day_exit"], candle=last)
        return SimulatedExit(status="OPEN", exitReason="open", exitPrice=None, exitAt=None, pnl=0.0, costs=costs(fill.filledQuantity, self.config), reasonCodes=["execution.position_open"])

    def _exit(
        self,
        order_plan: OrderPlan,
        fill: SimulatedFill,
        price: float | None,
        timestamp: datetime,
        reason: Literal["protective_stop", "profit_target", "strategy_invalidation", "time_stop", "end_of_day"],
        reason_codes: list[str],
        candle: MarketCandle | None = None,
    ) -> SimulatedExit:
        assert price is not None
        side = Signal(order_plan.side)
        exit_px = exit_price(side, price, self.config, candle=candle)
        quantity = fill.filledQuantity
        multiplier = 1 if side == Signal.BUY else -1
        all_costs = costs(quantity, self.config)
        gross_pnl = (exit_px - float(fill.averagePrice)) * quantity * multiplier
        pnl = gross_pnl - fill.costs["total"] - all_costs["total"]
        return SimulatedExit(
            status="EXITED",
            exitReason=reason,
            exitPrice=exit_px,
            exitAt=timestamp,
            grossPnl=round(gross_pnl, 6),
            pnl=round(pnl, 6),
            costs=all_costs,
            reasonCodes=[code for code in [*reason_codes, side_conservative_reason(side, is_entry=False), scenario_reason(self.config)] if code],
        )

    def _unfilled(self, order_plan: OrderPlan, submitted_at: datetime, reason: str) -> SimulatedFill:
        return SimulatedFill(
            status="UNFILLED",
            filledQuantity=0,
            requestedQuantity=order_plan.quantity,
            averagePrice=None,
            filledAt=None,
            submittedAt=submitted_at,
            side=order_plan.side,
            orderType=order_plan.orderType,
            reasonCodes=[reason],
            costs=costs(0, self.config),
        )

    def _expired(self, order_plan: OrderPlan, submitted_at: datetime, reason: str) -> SimulatedFill:
        fill = self._unfilled(order_plan, submitted_at, reason)
        return fill.model_copy(update={"status": "EXPIRED"})


def entry_price(side: Signal, reference_price: float, config: ExecutionSimulationConfig, *, candle: MarketCandle | None = None) -> float:
    half_spread = effective_spread(config, candle) / 2
    slippage = effective_slippage(config, candle)
    adverse = reference_price * (config.adverseSelectionBps / 10000.0)
    if side == Signal.BUY:
        return round(reference_price + half_spread + slippage + adverse, 6)
    return round(reference_price - half_spread - slippage - adverse, 6)


def exit_price(side: Signal, reference_price: float, config: ExecutionSimulationConfig, *, candle: MarketCandle | None = None) -> float:
    half_spread = effective_spread(config, candle) / 2
    slippage = effective_slippage(config, candle)
    adverse = reference_price * (config.adverseSelectionBps / 10000.0)
    if side == Signal.BUY:
        return round(reference_price - half_spread - slippage - adverse, 6)
    return round(reference_price + half_spread + slippage + adverse, 6)


def effective_spread(config: ExecutionSimulationConfig, candle: MarketCandle | None = None) -> float:
    opening_multiplier = config.openingSessionSpreadMultiplier if candle and candle.timestamp.hour == 14 and candle.timestamp.minute < 45 else 1.0
    return config.bidAskSpreadDollars * config.spreadWideningMultiplier * opening_multiplier * config.costMultiplier


def effective_slippage(config: ExecutionSimulationConfig, candle: MarketCandle | None = None) -> float:
    volatility_multiplier = config.volatilitySlippageMultiplier
    if candle and candle.open > 0:
        range_pct = (candle.high - candle.low) / candle.open
        volatility_multiplier *= 1.0 + min(3.0, range_pct * 100.0)
    shock_multiplier = 2.0 if config.eventShock else 1.0
    return config.slippagePerShare * config.costMultiplier * volatility_multiplier * config.participationSlippageMultiplier * shock_multiplier


def stop_gap_price(side: Signal, order_plan: OrderPlan, candle: MarketCandle, config: ExecutionSimulationConfig) -> float | None:
    if order_plan.stopPrice is None:
        return None
    if side == Signal.BUY and candle.open < order_plan.stopPrice:
        return candle.open - ((order_plan.stopPrice - candle.open) * max(0.0, config.stopGapSlippageMultiplier - 1.0))
    if side == Signal.SELL and candle.open > order_plan.stopPrice:
        return candle.open + ((candle.open - order_plan.stopPrice) * max(0.0, config.stopGapSlippageMultiplier - 1.0))
    return order_plan.stopPrice


def target_gap_price(side: Signal, order_plan: OrderPlan, candle: MarketCandle, config: ExecutionSimulationConfig) -> float | None:
    if order_plan.targetPrice is None:
        return None
    if side == Signal.BUY and candle.open > order_plan.targetPrice:
        return order_plan.targetPrice + ((candle.open - order_plan.targetPrice) / max(1.0, config.targetGapSlippageMultiplier))
    if side == Signal.SELL and candle.open < order_plan.targetPrice:
        return order_plan.targetPrice - ((order_plan.targetPrice - candle.open) / max(1.0, config.targetGapSlippageMultiplier))
    return order_plan.targetPrice


def gap_reason(side: Signal, order_plan: OrderPlan, candle: MarketCandle, *, is_stop: bool) -> str:
    price = order_plan.stopPrice if is_stop else order_plan.targetPrice
    if price is None:
        return ""
    if is_stop and ((side == Signal.BUY and candle.open < price) or (side == Signal.SELL and candle.open > price)):
        return "execution.stop_gap"
    if not is_stop and ((side == Signal.BUY and candle.open > price) or (side == Signal.SELL and candle.open < price)):
        return "execution.target_gap"
    return ""


def stop_touched(side: Signal, order_plan: OrderPlan, candle: MarketCandle) -> bool:
    if order_plan.stopPrice is None:
        return False
    return candle.low <= order_plan.stopPrice if side == Signal.BUY else candle.high >= order_plan.stopPrice


def target_touched(side: Signal, order_plan: OrderPlan, candle: MarketCandle) -> bool:
    if order_plan.targetPrice is None:
        return False
    return candle.high >= order_plan.targetPrice if side == Signal.BUY else candle.low <= order_plan.targetPrice


def strategy_invalidation_touched(side: Signal, order_plan: OrderPlan, candle: MarketCandle) -> bool:
    if order_plan.strategyInvalidationPrice is None:
        return False
    return candle.close <= order_plan.strategyInvalidationPrice if side == Signal.BUY else candle.close >= order_plan.strategyInvalidationPrice


def time_stop_touched(order_plan: OrderPlan, fill: SimulatedFill, candle: MarketCandle) -> bool:
    if order_plan.maximumHoldingMinutes is None or fill.filledAt is None:
        return False
    return candle.timestamp >= fill.filledAt + timedelta(minutes=order_plan.maximumHoldingMinutes)


def costs(quantity: int, config: ExecutionSimulationConfig) -> dict[str, float]:
    slippage = quantity * effective_slippage(config)
    spread = quantity * effective_spread(config)
    adverse = quantity * config.adverseSelectionBps / 10000.0
    fees = quantity * config.feesPerShare * config.costMultiplier
    total = slippage + spread + adverse + fees
    return {"slippage": round(slippage, 6), "spread": round(spread, 6), "adverseSelection": round(adverse, 6), "fees": round(fees, 6), "total": round(total, 6)}


def scenario_reason(config: ExecutionSimulationConfig) -> str:
    return f"execution.scenario:{config.scenarioName}" if config.scenarioName else ""


def side_conservative_reason(side: Signal | str, *, is_entry: bool) -> str:
    normalized = Signal(side)
    if is_entry:
        return "execution.buy_entry_uses_ask" if normalized == Signal.BUY else "execution.sell_entry_uses_bid"
    return "execution.sell_exit_uses_bid" if normalized == Signal.BUY else "execution.buy_to_cover_exit_uses_ask"
