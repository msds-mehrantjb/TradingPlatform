from __future__ import annotations

from typing import Any

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


def voting_ensemble_execution_stress_scenarios() -> tuple[ExecutionSimulationConfig, ...]:
    baseline = voting_ensemble_execution_config()
    return (
        baseline.model_copy(update={"scenarioName": "baseline_costs"}),
        baseline.model_copy(update={"scenarioName": "two_times_costs", "costMultiplier": 2.0}),
        baseline.model_copy(update={"scenarioName": "three_times_costs", "costMultiplier": 3.0}),
        baseline.model_copy(update={"scenarioName": "elevated_latency", "queueLatencySeconds": 3, "routingLatencySeconds": 2}),
        baseline.model_copy(update={"scenarioName": "stale_quotes", "quoteAgeSeconds": 10.0, "maxQuoteAgeSeconds": 5.0}),
        baseline.model_copy(update={"scenarioName": "thin_liquidity", "liquidityHaircut": 0.10, "partialFillRatio": 0.25}),
        baseline.model_copy(update={"scenarioName": "high_volatility_event_period", "eventShock": True, "volatilitySlippageMultiplier": 2.0, "spreadWideningMultiplier": 2.0}),
        baseline.model_copy(update={"scenarioName": "opening_session_spread_expansion", "openingSessionSpreadMultiplier": 3.0, "spreadWideningMultiplier": 1.5}),
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
    # The gate configurations a replay ran under. A baseline recorded without these
    # cannot be reproduced: the calendar and the segment map are what determine which
    # bars were vetoed, so they belong in the run's configuration, not around it.
    # The operational posture the replay is simulating. Replay has no operations to
    # measure, so this is an assumption and is written down as one: the default says
    # trading was enabled and in paper mode. Market-open, entry-window and session
    # validity are deliberately left out, so they keep deriving from the bars being
    # replayed and those gates still bind.
    # Exchange-local segment boundaries, so a replay can reproduce a live run that
    # used non-default ones. Shared with the live producer, not a second copy.
    sessionSegments: dict[str, Any] | None = None
    operationalHealth: dict[str, Any] | None = None
    sessionPolicy: dict[str, Any] | None = None
    eventCalendar: dict[str, Any] | None = None
    execution: ExecutionSimulationConfig = Field(default_factory=voting_ensemble_execution_config)
    executionStressScenarios: tuple[ExecutionSimulationConfig, ...] = Field(default_factory=voting_ensemble_execution_stress_scenarios)
    configVersion: str = VOTING_ENSEMBLE_BACKTEST_CONFIG_VERSION

