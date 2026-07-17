from __future__ import annotations

from pydantic import Field

from backend.app.algorithms.voting_ensemble.exit_policy import VOTING_ENSEMBLE_DEFAULT_MAX_HOLDING_MINUTES, voting_ensemble_execution_config
from backend.app.algorithms.voting_ensemble.profit_target_policy import VOTING_ENSEMBLE_DEFAULT_TARGET_DISTANCE
from backend.app.algorithms.voting_ensemble.stop_loss_policy import VOTING_ENSEMBLE_DEFAULT_STOP_DISTANCE
from backend.app.domain.models import DomainModel
from backend.app.execution.simulation import ExecutionSimulationConfig


VOTING_ENSEMBLE_BACKTEST_CONFIG_VERSION = "voting_ensemble_backtest_config_v1"


def backtest_config_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_BACKTEST_CONFIG_VERSION,
        "voting_ensemble.backtest_config.starting_capital",
        "voting_ensemble.backtest_config.warmup_candles",
        "voting_ensemble.backtest_config.stop_target_defaults",
        "voting_ensemble.backtest_config.execution_defaults",
        "voting_ensemble.backtest_config.decision_record_controls",
    )


class VotingEnsembleBacktestConfig(DomainModel):
    startingCapital: float = Field(default=100_000.0, gt=0)
    warmupCandles: int = Field(default=40, ge=2)
    targetDistance: float = Field(default=VOTING_ENSEMBLE_DEFAULT_TARGET_DISTANCE, gt=0)
    stopDistance: float = Field(default=VOTING_ENSEMBLE_DEFAULT_STOP_DISTANCE, gt=0)
    quantity: int = Field(default=1, ge=1)
    maximumHoldingMinutes: int = Field(default=VOTING_ENSEMBLE_DEFAULT_MAX_HOLDING_MINUTES, ge=1)
    includeDecisionRecords: bool = True
    maximumDecisionRecords: int | None = Field(default=None, ge=0)
    execution: ExecutionSimulationConfig = Field(default_factory=voting_ensemble_execution_config)
    configVersion: str = VOTING_ENSEMBLE_BACKTEST_CONFIG_VERSION

