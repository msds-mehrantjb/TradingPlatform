from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.app.backtesting.event_replay import ReplaySessionState, ReplayTrade


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
class VotingEnsemblePositionState(ReplaySessionState):
    algorithmId: str = VOTING_ENSEMBLE_ALGORITHM_ID
    stateVersion: str = VOTING_ENSEMBLE_POSITION_STATE_VERSION

    def active_positions(self, timestamp: datetime, symbol: str) -> list[ReplayTrade]:
        return super().active_positions(timestamp, symbol)

