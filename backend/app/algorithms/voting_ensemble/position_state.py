from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


VOTING_ENSEMBLE_POSITION_STATE_VERSION = "voting_ensemble_position_state_v1"
VOTING_ENSEMBLE_ALGORITHM_ID = "voting_ensemble"


def position_state_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_POSITION_STATE_VERSION,
        "voting_ensemble.position_state.algorithm_owned",
        "voting_ensemble.position_state.active_position_scope",
        "voting_ensemble.position_state.broker_snapshot_attribution",
    )


@dataclass
class VotingEnsemblePositionState:
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    stateVersion: str = VOTING_ENSEMBLE_POSITION_STATE_VERSION
    trades: list[Any] = field(default_factory=list)
    seenOrderKeys: set[str] = field(default_factory=set)
    setupEntryCounts: dict[str, int] = field(default_factory=dict)
    lastEntryAt: datetime | None = None
    lastStopAt: datetime | None = None

    def active_positions(self, timestamp: datetime, symbol: str) -> list[Any]:
        return [
            trade
            for trade in self.trades
            if trade.symbol == symbol.upper() and (trade.exitAt is None or trade.exitAt > timestamp)
        ]

    def record_trade(self, trade: Any, *, setup_key: str, stopped_out: bool) -> None:
        self.trades.append(trade)
        self.lastEntryAt = trade.filledAt
        if stopped_out:
            self.lastStopAt = trade.exitAt
        self.setupEntryCounts[setup_key] = self.setupEntryCounts.get(setup_key, 0) + 1

    def total_trades(self) -> int:
        return len(self.trades)

    def setup_entry_count(self, setup_key: str) -> int:
        return self.setupEntryCounts.get(setup_key, 0)

    def entry_cooldown_active(self, timestamp: datetime, cooldown_seconds: int) -> bool:
        return bool(self.lastEntryAt and (timestamp - self.lastEntryAt).total_seconds() < cooldown_seconds)

    def stop_cooldown_active(self, timestamp: datetime, cooldown_seconds: int) -> bool:
        return bool(self.lastStopAt and (timestamp - self.lastStopAt).total_seconds() < cooldown_seconds)

    def duplicate_order_seen(self, order_key: str) -> bool:
        return order_key in self.seenOrderKeys

    def remember_order_key(self, order_key: str) -> None:
        self.seenOrderKeys.add(order_key)

    def realized_pnl_today(self, observed_at: datetime) -> float:
        return sum(trade.pnl for trade in self.trades if trade.exitAt and trade.exitAt.date() == observed_at.date())
