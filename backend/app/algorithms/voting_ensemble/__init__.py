"""Backend-authoritative Voting Ensemble algorithm."""

from .backtest import (
    VOTING_ENSEMBLE_BACKTEST_VERSION,
    VOTING_ENSEMBLE_CONTEXT_CATALOG,
    VOTING_ENSEMBLE_DIRECTIONAL_CATALOG,
    VotingEnsembleBacktestConfig,
    VotingEnsembleBacktestRunner,
)
from .ml_snapshots import (
    VOTING_ENSEMBLE_REPLAY_SNAPSHOT_BRIDGE_VERSION,
    merge_voting_ensemble_replay_snapshot_labels,
    write_voting_ensemble_replay_snapshot_labels,
)

__all__ = [
    "VOTING_ENSEMBLE_BACKTEST_VERSION",
    "VOTING_ENSEMBLE_CONTEXT_CATALOG",
    "VOTING_ENSEMBLE_DIRECTIONAL_CATALOG",
    "VotingEnsembleBacktestConfig",
    "VotingEnsembleBacktestRunner",
    "VOTING_ENSEMBLE_REPLAY_SNAPSHOT_BRIDGE_VERSION",
    "merge_voting_ensemble_replay_snapshot_labels",
    "write_voting_ensemble_replay_snapshot_labels",
]
