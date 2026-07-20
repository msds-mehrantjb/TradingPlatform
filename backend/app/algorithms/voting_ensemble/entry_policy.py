from __future__ import annotations

from datetime import date, datetime
from math import floor

from backend.app.domain.models import (
    AccountRiskState,
    BaselineTradingSettings,
    DynamicPolicyBounds,
    EffectiveTradePolicy,
    EnsembleDecision,
    GlobalGateDecision,
    HardRiskLimits,
    OperatingMode,
    OrderPlan,
    TradeCandidate,
)
from backend.app.domain.trading_settings import trading_settings_configuration_hash
from backend.app.algorithms.meta_strategy.inference.safe_inference import SafeMLInferenceResult
from backend.app.algorithms.voting_ensemble.exit_policy import VOTING_ENSEMBLE_EXIT_POLICY_VERSION
from backend.app.algorithms.voting_ensemble.order_planner import (
    VOTING_ENSEMBLE_ORDER_PLANNER_VERSION,
    VotingEnsembleOrderPlanner,
)
from backend.app.algorithms.voting_ensemble.profit_target_policy import VOTING_ENSEMBLE_DEFAULT_TARGET_R, VOTING_ENSEMBLE_PROFIT_TARGET_POLICY_VERSION
from backend.app.algorithms.voting_ensemble.stop_loss_policy import VOTING_ENSEMBLE_DEFAULT_STOP_DISTANCE, VOTING_ENSEMBLE_MINIMUM_STOP_DISTANCE, VOTING_ENSEMBLE_STOP_LOSS_POLICY_VERSION


VOTING_ENSEMBLE_ENTRY_POLICY_VERSION = "voting_ensemble_entry_policy_v1"
VOTING_ENSEMBLE_ORDER_VALIDATOR_VERSION = "voting_ensemble_order_validator_v1"


class VotingEnsembleReplayPolicyEngine:
    def effective_policy(
        self,
        *,
        candidate: TradeCandidate | None,
        ensembleDecision: EnsembleDecision,
        gateDecision: GlobalGateDecision,
        mlDecision: SafeMLInferenceResult,
        accountRiskState: AccountRiskState,
        decidedAt: datetime,
        sessionDate: date,
    ) -> EffectiveTradePolicy:
        risk_cap = mlDecision.recommendedRiskCap if mlDecision.effectiveMode == OperatingMode.ACTIVE.value else 1.0
        edge_cap = _edge_cap(ensembleDecision)
        effective_cap = max(0.0, min(1.0, risk_cap, edge_cap))
        baseline_settings = BaselineTradingSettings(
            startingCapital=accountRiskState.equity or 1.0,
            orderAllocationPercent=10.0,
            dailyAllocationPercent=30.0,
            riskBudgetPercentOfOrder=50.0,
            maxTradesPerDay=3,
            stopLossPercent=0.35,
            fixedStopDistanceDollars=VOTING_ENSEMBLE_DEFAULT_STOP_DISTANCE,
            takeProfitR=VOTING_ENSEMBLE_DEFAULT_TARGET_R,
            slippagePerShare=0.02,
            positionSizingMode="allocation",
            settingsVersion="voting_ensemble_entry_baseline_v1",
            configurationHash="voting_ensemble_entry_baseline_v1",
        )
        hard_limits = HardRiskLimits(
            maximumRiskPerTradePercent=0.5,
            maximumDailyLossPercent=2.0,
            maximumOpenRiskPercent=3.0,
            maximumPositionPercent=50.0,
            maximumOrderNotionalPercent=10.0,
            maximumDailyNotionalPercent=30.0,
            maximumShares=1000,
            maximumVolumeParticipationPercent=1.0,
            maximumTradesPerDay=3,
            maximumConsecutiveLosses=3,
            maximumSpreadBps=25.0,
            allowPyramiding=False,
            maxDailyLossPercent=2.0,
            maxOrderNotional=max(accountRiskState.equity * 0.10, 0.01),
            maxPositionNotional=max(accountRiskState.equity * 0.50, 0.01),
            maxShareQuantity=1000,
            minStopDistanceDollars=VOTING_ENSEMBLE_MINIMUM_STOP_DISTANCE,
            maxSlippagePerShare=1.0,
            configurationHash="voting_ensemble_entry_hard_limits_v1",
        )
        dynamic_bounds = DynamicPolicyBounds(
            minConfidence=0.0,
            minReliability=0.0,
            minRegimeFit=0.0,
            maxSpreadPercent=100.0,
            maxParticipationPercent=100.0,
            minLiquidityShares=0,
            configurationHash="voting_ensemble_entry_dynamic_bounds_v1",
        )
        policy_hash = trading_settings_configuration_hash(
            baseline_settings=baseline_settings,
            hard_limits=hard_limits,
            dynamic_bounds=dynamic_bounds,
            strategy_configuration_hash="voting_ensemble_strategy_inputs_v1",
            ensemble_configuration_hash=ensembleDecision.configurationHash,
            ml_configuration_hash=mlDecision.configurationHash,
            risk_configuration_hash=hard_limits.configurationHash,
            sizing_configuration_hash=baseline_settings.configurationHash,
            entry_configuration_hash=f"{VOTING_ENSEMBLE_ENTRY_POLICY_VERSION}:{VOTING_ENSEMBLE_ORDER_PLANNER_VERSION}",
            exit_configuration_hash=f"{VOTING_ENSEMBLE_STOP_LOSS_POLICY_VERSION}:{VOTING_ENSEMBLE_PROFIT_TARGET_POLICY_VERSION}:{VOTING_ENSEMBLE_EXIT_POLICY_VERSION}",
            gate_configuration_hash=gateDecision.configurationHash,
            backtest_configuration_hash="voting_ensemble_event_replay_v1",
        )
        max_quantity = floor((accountRiskState.equity * 0.10 * effective_cap) / max(candidate.entryPrice, 0.01)) if candidate else 0
        return EffectiveTradePolicy(
            mode=OperatingMode(mlDecision.effectiveMode),
            baselineSettings=baseline_settings,
            hardRiskLimits=hard_limits,
            dynamicBounds=dynamic_bounds,
            accountRiskState=accountRiskState,
            maxQuantity=max(0, min(max_quantity, hard_limits.maximumShares)),
            maxNotional=accountRiskState.equity * 0.10 * effective_cap,
            riskDollars=accountRiskState.equity * 0.005 * effective_cap,
            explanation="Voting Ensemble entry policy created after local gates and ML filter evaluation.",
            effectiveAt=decidedAt,
            sessionDate=sessionDate,
            configurationHash=policy_hash,
        )


class VotingEnsembleOrderValidator:
    def __init__(self, order_planner: VotingEnsembleOrderPlanner | None = None) -> None:
        self.order_planner = order_planner or VotingEnsembleOrderPlanner()

    def order_plan(
        self,
        *,
        candidate: TradeCandidate | None,
        policy: EffectiveTradePolicy,
        gateDecision: GlobalGateDecision,
        mlDecision: SafeMLInferenceResult,
        decidedAt: datetime,
        sessionDate: date,
    ) -> OrderPlan | None:
        return self.order_planner.order_plan(
            candidate=candidate,
            policy=policy,
            gateDecision=gateDecision,
            mlDecision=mlDecision,
            decidedAt=decidedAt,
            sessionDate=sessionDate,
        )


def _edge_cap(decision: EnsembleDecision) -> float:
    edge = abs(float(decision.finalScore))
    if edge >= 0.60:
        return 1.0
    if edge >= 0.45:
        return 0.75
    if edge >= 0.30:
        return 0.50
    if edge >= 0.20:
        return 0.25
    return 0.0
