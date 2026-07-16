from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.app.domain.feature_engine import MarketCandle
from backend.app.domain.models import DomainModel, OrderPlan, Signal


class ExecutionSimulationConfig(DomainModel):
    configVersion: str = "execution_simulation_v1"
    latencySeconds: int = Field(default=1, ge=0)
    bidAskSpreadDollars: float = Field(default=0.02, ge=0.0)
    slippagePerShare: float = Field(default=0.01, ge=0.0)
    feesPerShare: float = Field(default=0.0, ge=0.0)
    maxVolumeParticipation: float = Field(default=0.10, gt=0.0, le=1.0)
    orderExpirationSeconds: int = Field(default=300, ge=1)
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
        submitted_at = decision_at + timedelta(seconds=self.config.latencySeconds)
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
        expires_at = submitted_at + timedelta(seconds=self.config.orderExpirationSeconds)
        for candle in candles:
            if candle.timestamp > expires_at:
                return self._expired(order_plan, submitted_at, "execution.order_expired")
            touched, entry_price, reason = self._entry_touched(order_plan, candle)
            if not touched or entry_price is None:
                continue
            fill_quantity = min(requested, max(0, int(candle.volume * self.config.maxVolumeParticipation)))
            if fill_quantity <= 0:
                return self._unfilled(order_plan, submitted_at, "execution.no_volume_available")
            status = "FILLED" if fill_quantity == requested else "PARTIAL"
            reason_codes = [reason, side_conservative_reason(order_plan.side, is_entry=True)]
            if status == "PARTIAL":
                reason_codes.append("execution.partial_fill_volume_participation")
            return SimulatedFill(
                status=status,
                filledQuantity=fill_quantity,
                requestedQuantity=requested,
                averagePrice=entry_price,
                filledAt=candle.timestamp,
                submittedAt=submitted_at,
                side=order_plan.side,
                orderType=order_plan.orderType,
                reasonCodes=reason_codes,
                costs=costs(fill_quantity, self.config),
            )
        return self._expired(order_plan, submitted_at, "execution.order_unfilled")

    def _entry_touched(self, order_plan: OrderPlan, candle: MarketCandle) -> tuple[bool, float | None, str]:
        side = Signal(order_plan.side)
        if order_plan.orderType == "MARKET":
            return True, entry_price(side, candle.open, self.config), "execution.market_entry_next_executable"
        if order_plan.orderType == "LIMIT":
            if side == Signal.BUY and candle.low <= (order_plan.limitPrice or order_plan.entryPrice):
                return True, entry_price(side, min(order_plan.limitPrice or order_plan.entryPrice, candle.open), self.config), "execution.limit_entry_touched"
            if side == Signal.SELL and candle.high >= (order_plan.limitPrice or order_plan.entryPrice):
                return True, entry_price(side, max(order_plan.limitPrice or order_plan.entryPrice, candle.open), self.config), "execution.limit_entry_touched"
            return False, None, "execution.limit_not_touched"
        if order_plan.orderType == "STOP_LIMIT":
            stop_triggered = candle.high >= order_plan.entryPrice if side == Signal.BUY else candle.low <= order_plan.entryPrice
            if not stop_triggered:
                return False, None, "execution.stop_not_triggered"
            limit_price = order_plan.limitPrice or order_plan.entryPrice
            limit_fillable = candle.low <= limit_price if side == Signal.BUY else candle.high >= limit_price
            if not limit_fillable:
                return False, None, "execution.stop_limit_triggered_not_filled"
            return True, entry_price(side, limit_price, self.config), "execution.stop_limit_entry_filled"
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
                return self._exit(order_plan, fill, order_plan.stopPrice, candle.timestamp, "protective_stop", reason_codes)
            if stop_hit:
                return self._exit(order_plan, fill, order_plan.stopPrice, candle.timestamp, "protective_stop", ["execution.protective_stop_hit"])
            if target_hit:
                return self._exit(order_plan, fill, order_plan.targetPrice, candle.timestamp, "profit_target", ["execution.profit_target_hit"])
        if self.config.endOfDayExit and order_plan.endOfDayExit and candles:
            last = candles[-1]
            return self._exit(order_plan, fill, last.close, last.timestamp, "end_of_day", ["execution.end_of_day_exit"])
        return SimulatedExit(status="OPEN", exitReason="open", exitPrice=None, exitAt=None, pnl=0.0, costs=costs(fill.filledQuantity, self.config), reasonCodes=["execution.position_open"])

    def _exit(
        self,
        order_plan: OrderPlan,
        fill: SimulatedFill,
        price: float | None,
        timestamp: datetime,
        reason: Literal["protective_stop", "profit_target", "strategy_invalidation", "time_stop", "end_of_day"],
        reason_codes: list[str],
    ) -> SimulatedExit:
        assert price is not None
        side = Signal(order_plan.side)
        exit_px = exit_price(side, price, self.config)
        quantity = fill.filledQuantity
        multiplier = 1 if side == Signal.BUY else -1
        all_costs = costs(quantity, self.config)
        pnl = ((exit_px - float(fill.averagePrice)) * quantity * multiplier) - fill.costs["total"] - all_costs["total"]
        return SimulatedExit(
            status="EXITED",
            exitReason=reason,
            exitPrice=exit_px,
            exitAt=timestamp,
            pnl=round(pnl, 6),
            costs=all_costs,
            reasonCodes=[*reason_codes, side_conservative_reason(side, is_entry=False)],
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


def entry_price(side: Signal, reference_price: float, config: ExecutionSimulationConfig) -> float:
    half_spread = config.bidAskSpreadDollars / 2
    if side == Signal.BUY:
        return round(reference_price + half_spread + config.slippagePerShare, 6)
    return round(reference_price - half_spread - config.slippagePerShare, 6)


def exit_price(side: Signal, reference_price: float, config: ExecutionSimulationConfig) -> float:
    half_spread = config.bidAskSpreadDollars / 2
    if side == Signal.BUY:
        return round(reference_price - half_spread - config.slippagePerShare, 6)
    return round(reference_price + half_spread + config.slippagePerShare, 6)


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
    slippage = quantity * config.slippagePerShare
    fees = quantity * config.feesPerShare
    return {"slippage": round(slippage, 6), "fees": round(fees, 6), "total": round(slippage + fees, 6)}


def side_conservative_reason(side: Signal | str, *, is_entry: bool) -> str:
    normalized = Signal(side)
    if is_entry:
        return "execution.buy_entry_uses_ask" if normalized == Signal.BUY else "execution.sell_entry_uses_bid"
    return "execution.sell_exit_uses_bid" if normalized == Signal.BUY else "execution.buy_to_cover_exit_uses_ask"
