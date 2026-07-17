from __future__ import annotations

from typing import Any

from backend.app.domain.models import StrategyFamily
from backend.app.gates import GlobalGateConfig, GlobalGateEngine, GlobalGateEngineDecision, GlobalGateInput, StrategyConditionalGateConfig, global_gate_configuration_hash


VOTING_ENSEMBLE_LOCAL_GATE_VERSION = "voting_ensemble_local_gates_v1"


def voting_ensemble_local_gate_config() -> GlobalGateConfig:
    conditional = StrategyConditionalGateConfig(
        configVersion="voting_ensemble_strategy_conditional_gates_v1",
        configurationHash="voting_ensemble_strategy_conditional_gates_v1",
        minimumBreadthCoverage=0.65,
        lateSessionMinutesUntilClose=20,
    )
    return GlobalGateConfig(
        gateVersion=VOTING_ENSEMBLE_LOCAL_GATE_VERSION,
        automaticEntriesFailClosed=True,
        requireMlWhenEnabled=False,
        requireModelHealthWhenEnabled=False,
        minimumDeterministicScore=0.20,
        minimumIndependentFamilySupport=2,
        minimumExpectedValueAfterCosts=0.0,
        maximumSpreadBps=25.0,
        maximumExpectedSlippageDollars=0.05,
        maximumEntryDistanceDollars=2.0,
        minimumLiquidityShares=1,
        maximumDailyLossPercent=2.0,
        maximumDrawdownFromIntradayHighPercent=5.0,
        maximumOpenRiskPercent=3.0,
        maximumSpyNotionalPercent=50.0,
        maximumSameDirectionExposurePercent=50.0,
        maximumTradesPerDay=3,
        maximumConsecutiveLosses=3,
        defaultRiskMultiplierCap=1.0,
        defaultMaximumRiskPercent=0.5,
        defaultMaximumNotionalPercent=10.0,
        conditionalGates=conditional,
        configurationHash="voting_ensemble_local_gate_config_v1",
    )


class VotingEnsembleLocalGateEngine(GlobalGateEngine):
    def __init__(self, config: GlobalGateConfig | None = None) -> None:
        super().__init__(config or voting_ensemble_local_gate_config())

    def evaluate(self, inputs: GlobalGateInput | dict[str, Any]) -> GlobalGateEngineDecision:
        decision = super().evaluate(inputs)
        configuration_hash = global_gate_configuration_hash(
            self.config,
            {
                "algorithmId": "voting_ensemble",
                "gateVersion": VOTING_ENSEMBLE_LOCAL_GATE_VERSION,
            },
        )
        reason_codes = list(dict.fromkeys(["voting_ensemble.local_gates.evaluated", *decision.reasonCodes]))
        return decision.model_copy(
            update={
                "gateVersion": VOTING_ENSEMBLE_LOCAL_GATE_VERSION,
                "configurationHash": configuration_hash,
                "reasonCodes": reason_codes,
                "explanation": f"Voting Ensemble local gates evaluated: {decision.explanation}",
            }
        )


def local_gate_family_scope() -> tuple[StrategyFamily, ...]:
    return (
        StrategyFamily.TREND,
        StrategyFamily.BREAKOUT,
        StrategyFamily.REVERSAL,
        StrategyFamily.MEAN_REVERSION,
        StrategyFamily.GAP_SESSION,
    )
