from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.app.algorithms.voting_ensemble.position_state import VotingEnsemblePositionState
from backend.app.backtesting.event_replay import ReplayTrade


VOTING_ENSEMBLE_TRADE_COUNTER_STATE_VERSION = "voting_ensemble_trade_counter_state_v1"


def trade_counter_state_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_TRADE_COUNTER_STATE_VERSION,
        "voting_ensemble.trade_counter_state.trade_count",
        "voting_ensemble.trade_counter_state.setup_entry_count",
        "voting_ensemble.trade_counter_state.entry_cooldown",
        "voting_ensemble.trade_counter_state.stop_cooldown",
        "voting_ensemble.trade_counter_state.duplicate_order_key",
    )


@dataclass
class VotingEnsembleTradeCounterState(VotingEnsemblePositionState):
    tradeCounterVersion: str = VOTING_ENSEMBLE_TRADE_COUNTER_STATE_VERSION

    def record_trade(self, trade: ReplayTrade, *, setup_key: str, stopped_out: bool) -> None:
        super().record_trade(trade, setup_key=setup_key, stopped_out=stopped_out)

    def total_trades(self) -> int:
        return super().total_trades()

    def entry_cooldown_active(self, timestamp: datetime, cooldown_seconds: int) -> bool:
        return super().entry_cooldown_active(timestamp, cooldown_seconds)

    def stop_cooldown_active(self, timestamp: datetime, cooldown_seconds: int) -> bool:
        return super().stop_cooldown_active(timestamp, cooldown_seconds)

