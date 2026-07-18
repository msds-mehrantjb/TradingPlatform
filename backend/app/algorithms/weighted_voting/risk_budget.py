"""Risk budget calculations owned by Weighted Voting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal

from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.models import WeightedEffectiveSettings, WeightedSide


WEIGHTED_VOTING_RISK_BUDGET_VERSION = "weighted_voting_risk_budget_v2"
WEIGHTED_VOTING_RISK_BUDGET_NAMESPACE = "weighted_voting.risk_budget"


@dataclass(frozen=True)
class WeightedVotingOpenPositionRisk:
    symbol: str
    quantity: int
    side: str
    entry_price: float
    stop_price: float
    current_price: float
    position_id: str = ""
    algorithm_id: Literal["weighted_voting"] = WEIGHTED_VOTING_ALGORITHM_ID

    @property
    def trade_level_risk(self) -> float:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting risk budget cannot use another algorithm's position risk")
        return max(0.0, abs(self.quantity) * abs(self.entry_price - self.stop_price))

    @property
    def notional(self) -> float:
        return max(0.0, abs(self.quantity) * self.current_price)

    @property
    def unrealized_loss(self) -> float:
        if self.side == WeightedSide.BUY.value:
            pnl = (self.current_price - self.entry_price) * self.quantity
        elif self.side == WeightedSide.SELL.value:
            pnl = (self.entry_price - self.current_price) * abs(self.quantity)
        else:
            pnl = 0.0
        return max(0.0, -pnl)


@dataclass(frozen=True)
class WeightedVotingRiskBudget:
    """Algorithm-local risk budget; global risk can cap it but never raise it."""

    account_equity: float
    risk_percent: float
    data_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    session_date: date | None = None
    algorithm_id: Literal["weighted_voting"] = WEIGHTED_VOTING_ALGORITHM_ID
    budget_version: str = WEIGHTED_VOTING_RISK_BUDGET_VERSION
    configured_daily_risk_allowance: float | None = None
    global_daily_risk_cap: float | None = None
    open_positions: tuple[WeightedVotingOpenPositionRisk, ...] = ()
    realized_pnl: float = 0.0
    pending_trade_risk: float | None = None
    max_simultaneous_positions: int = 1
    capital_partition_percent: float = 100.0
    capital_used_by_open_positions: float | None = None
    shutdown_loss_limit: float | None = None
    shutdown_requested: bool = False
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting risk budget cannot be assigned to another algorithm")
        for position in self.open_positions:
            if position.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
                raise ValueError("Weighted Voting risk budget cannot include another algorithm's position")

    @property
    def local_daily_risk_allowance(self) -> float:
        configured = self.configured_daily_risk_allowance
        if configured is None:
            configured = self.account_equity * (self.risk_percent / 100.0)
        return max(0.0, configured)

    @property
    def daily_risk_allowance(self) -> float:
        if self.global_daily_risk_cap is None:
            return self.local_daily_risk_allowance
        return max(0.0, min(self.local_daily_risk_allowance, self.global_daily_risk_cap))

    @property
    def risk_used_by_open_positions(self) -> float:
        return max(0.0, sum(position.trade_level_risk for position in self.open_positions))

    @property
    def realized_weighted_voting_loss(self) -> float:
        return max(0.0, -self.realized_pnl)

    @property
    def unrealized_weighted_voting_loss(self) -> float:
        return max(0.0, sum(position.unrealized_loss for position in self.open_positions))

    @property
    def remaining_daily_risk(self) -> float:
        used = self.risk_used_by_open_positions + self.realized_weighted_voting_loss + self.unrealized_weighted_voting_loss
        return max(0.0, self.daily_risk_allowance - used)

    @property
    def trade_level_risk(self) -> float:
        if self.pending_trade_risk is not None:
            return max(0.0, self.pending_trade_risk)
        return self.daily_risk_allowance

    @property
    def symbol_level_risk(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for position in self.open_positions:
            totals[position.symbol] = totals.get(position.symbol, 0.0) + position.trade_level_risk
        return totals

    @property
    def capital_partition(self) -> float:
        return max(0.0, self.account_equity * (self.capital_partition_percent / 100.0))

    @property
    def capital_used(self) -> float:
        if self.capital_used_by_open_positions is not None:
            return max(0.0, self.capital_used_by_open_positions)
        return max(0.0, sum(position.notional for position in self.open_positions))

    @property
    def remaining_capital_partition(self) -> float:
        return max(0.0, self.capital_partition - self.capital_used)

    @property
    def daily_shutdown_state(self) -> bool:
        if self.shutdown_requested:
            return True
        loss_limit = self.shutdown_loss_limit if self.shutdown_loss_limit is not None else self.daily_risk_allowance
        total_loss = self.realized_weighted_voting_loss + self.unrealized_weighted_voting_loss
        return total_loss >= max(0.0, loss_limit)

    @property
    def simultaneous_position_slots_remaining(self) -> int:
        return max(0, self.max_simultaneous_positions - len(self.open_positions))

    @property
    def risk_dollars(self) -> float:
        if self.daily_shutdown_state or self.simultaneous_position_slots_remaining <= 0:
            return 0.0
        return max(0.0, min(self.trade_level_risk, self.remaining_daily_risk, self.remaining_capital_partition))

    def with_global_cap(self, global_daily_risk_cap: float | None) -> WeightedVotingRiskBudget:
        return WeightedVotingRiskBudget(
            account_equity=self.account_equity,
            risk_percent=self.risk_percent,
            data_timestamp=self.data_timestamp,
            session_date=self.session_date,
            configured_daily_risk_allowance=self.configured_daily_risk_allowance,
            global_daily_risk_cap=global_daily_risk_cap,
            open_positions=self.open_positions,
            realized_pnl=self.realized_pnl,
            pending_trade_risk=self.pending_trade_risk,
            max_simultaneous_positions=self.max_simultaneous_positions,
            capital_partition_percent=self.capital_partition_percent,
            capital_used_by_open_positions=self.capital_used_by_open_positions,
            shutdown_loss_limit=self.shutdown_loss_limit,
            shutdown_requested=self.shutdown_requested,
            reason_codes=tuple(dict.fromkeys((*self.reason_codes, "weighted_voting.risk_budget.global_cap_applied"))),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "algorithmId": self.algorithm_id,
            "namespace": WEIGHTED_VOTING_RISK_BUDGET_NAMESPACE,
            "budgetVersion": self.budget_version,
            "dataTimestamp": self.data_timestamp.isoformat(),
            "sessionDate": self.session_date.isoformat() if self.session_date else None,
            "accountEquity": self.account_equity,
            "riskPercent": self.risk_percent,
            "dailyRiskAllowance": self.daily_risk_allowance,
            "localDailyRiskAllowance": self.local_daily_risk_allowance,
            "globalDailyRiskCap": self.global_daily_risk_cap,
            "remainingDailyRisk": self.remaining_daily_risk,
            "riskUsedByOpenWeightedVotingPositions": self.risk_used_by_open_positions,
            "realizedWeightedVotingLoss": self.realized_weighted_voting_loss,
            "unrealizedWeightedVotingLoss": self.unrealized_weighted_voting_loss,
            "tradeLevelRisk": self.trade_level_risk,
            "symbolLevelRisk": dict(self.symbol_level_risk),
            "maximumSimultaneousPositions": self.max_simultaneous_positions,
            "simultaneousPositionSlotsRemaining": self.simultaneous_position_slots_remaining,
            "capitalPartition": self.capital_partition,
            "remainingCapitalPartition": self.remaining_capital_partition,
            "dailyShutdownState": self.daily_shutdown_state,
            "availableRiskDollars": self.risk_dollars,
            "reasonCodes": self.reason_codes,
            "explanation": "Weighted Voting owns this local risk budget; global account risk may only reduce the allowance.",
        }


def build_weighted_voting_risk_budget(
    *,
    account_equity: float,
    effective_settings: WeightedEffectiveSettings | None = None,
    config: WeightedVotingConfig | None = None,
    open_positions: tuple[WeightedVotingOpenPositionRisk, ...] = (),
    realized_pnl: float = 0.0,
    global_daily_risk_cap: float | None = None,
    pending_trade_risk: float | None = None,
    max_simultaneous_positions: int | None = None,
    timestamp: datetime | None = None,
) -> WeightedVotingRiskBudget:
    active_config = config or WeightedVotingConfig()
    risk_percent = (
        effective_settings.base_risk_per_trade_percent
        if effective_settings is not None
        else active_config.risk_per_trade_baseline_percent
    )
    daily_risk_percent = (
        effective_settings.maximum_daily_loss_percent
        if effective_settings is not None
        else active_config.daily_risk_baseline_percent
    )
    partition_percent = (
        effective_settings.daily_allocation_percent
        if effective_settings is not None
        else active_config.daily_allocation_percent
    )
    maximum_positions = max_simultaneous_positions if max_simultaneous_positions is not None else max(1, active_config.maximum_weighted_daily_trades)
    return WeightedVotingRiskBudget(
        account_equity=account_equity,
        risk_percent=risk_percent,
        data_timestamp=timestamp or datetime.now(timezone.utc),
        configured_daily_risk_allowance=account_equity * (daily_risk_percent / 100.0),
        global_daily_risk_cap=global_daily_risk_cap,
        open_positions=open_positions,
        realized_pnl=realized_pnl,
        pending_trade_risk=pending_trade_risk,
        max_simultaneous_positions=maximum_positions,
        capital_partition_percent=partition_percent,
        shutdown_loss_limit=account_equity * (daily_risk_percent / 100.0),
        reason_codes=("weighted_voting.risk_budget.built",),
    )
