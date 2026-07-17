from __future__ import annotations

from backend.app.domain.models import OrderPlan
from backend.app.domain.feature_engine import MarketCandle
from backend.app.execution.simulation import ExecutionSimulationConfig, RealisticExecutionSimulator, SimulatedExecution


VOTING_ENSEMBLE_EXIT_POLICY_VERSION = "voting_ensemble_exit_policy_v1"
VOTING_ENSEMBLE_DEFAULT_MAX_HOLDING_MINUTES = 30


def voting_ensemble_execution_config() -> ExecutionSimulationConfig:
    return ExecutionSimulationConfig(
        configVersion=VOTING_ENSEMBLE_EXIT_POLICY_VERSION,
        latencySeconds=1,
        bidAskSpreadDollars=0.02,
        slippagePerShare=0.01,
        feesPerShare=0.0,
        maxVolumeParticipation=0.10,
        orderExpirationSeconds=300,
        conservativeSameBarRule="STOP_FIRST",
        endOfDayExit=True,
    )


def exit_policy_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_EXIT_POLICY_VERSION,
        "voting_ensemble.exit_policy.bracket_stop_target",
        "voting_ensemble.exit_policy.time_stop",
        "voting_ensemble.exit_policy.end_of_day_exit",
        "voting_ensemble.exit_policy.conservative_stop_first",
    )


class VotingEnsembleExecutionSimulator(RealisticExecutionSimulator):
    def __init__(self, config: ExecutionSimulationConfig | None = None) -> None:
        super().__init__(config or voting_ensemble_execution_config())

    def simulate(self, order_plan: OrderPlan, future_candles: list[MarketCandle], decision_at) -> SimulatedExecution:
        execution = super().simulate(order_plan, future_candles, decision_at)
        codes = list(dict.fromkeys([*exit_policy_reason_codes(), *execution.reasonCodes]))
        exit_result = execution.exit
        if exit_result is not None:
            exit_result = exit_result.model_copy(
                update={"reasonCodes": list(dict.fromkeys([*exit_policy_reason_codes(), *exit_result.reasonCodes]))}
            )
        return execution.model_copy(update={"exit": exit_result, "reasonCodes": codes})
