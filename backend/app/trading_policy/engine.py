from __future__ import annotations

from backend.app.domain.models import OperatingMode
from backend.app.trading_policy.baseline import baseline_holding_minutes, baseline_risk_dollars, baseline_target_r
from backend.app.trading_policy.entry_policy import build_entry_plan
from backend.app.trading_policy.exit_policy import build_exit_plan
from backend.app.trading_policy.models import (
    DynamicPolicyInputs,
    DynamicTradingPolicyConfig,
    DynamicTradingPolicyDecision,
    POLICY_ENGINE_VERSION,
    RiskCapBreakdown,
    policy_configuration_hash,
)
from backend.app.trading_policy.position_sizing import size_position
from backend.app.trading_policy.risk_caps import (
    daily_loss_remaining_dollars,
    daily_notional_cap_dollars,
    dynamic_risk_caps,
    effective_risk_multiplier_from_caps,
    hard_risk_cap_dollars,
    open_risk_cap_dollars,
    order_notional_cap_dollars,
    position_notional_cap_dollars,
    share_cap,
)
from backend.app.trading_policy.validator import policy_validation_errors


class DynamicTradingPolicyEngine:
    def __init__(self, config: DynamicTradingPolicyConfig | None = None) -> None:
        self.config = config or DynamicTradingPolicyConfig()

    def evaluate(self, inputs: DynamicPolicyInputs) -> DynamicTradingPolicyDecision:
        baseline_risk = baseline_risk_dollars(
            account_equity=inputs.accountRiskState.equity,
            settings=inputs.baselineSettings,
            candidate=inputs.candidate,
        )
        caps = dynamic_risk_caps(
            candidate=inputs.candidate,
            regime=inputs.regimeState,
            context_signals=inputs.contextSignals,
            prediction=inputs.metaModelPrediction,
            account=inputs.accountRiskState,
            baseline=inputs.baselineSettings,
            hard_limits=inputs.hardRiskLimits,
            bounds=inputs.dynamicBounds,
            config=self.config,
            evaluated_at=inputs.evaluatedAt,
        )
        dynamic_multiplier, limiting_cap = effective_risk_multiplier_from_caps(caps)
        bounded_multiplier = min(
            1.0,
            inputs.dynamicBounds.maximumRiskMultiplier,
            max(inputs.dynamicBounds.minimumRiskMultiplier, dynamic_multiplier),
        )
        mode_value = str(self.config.mode)
        applied_multiplier = 1.0 if mode_value in {OperatingMode.OFF.value, OperatingMode.SHADOW.value, OperatingMode.FALLBACK.value} else bounded_multiplier
        signal_risk = baseline_risk
        dynamic_risk = signal_risk * applied_multiplier
        hard_cap = hard_risk_cap_dollars(inputs.accountRiskState, inputs.hardRiskLimits)
        daily_remaining = daily_loss_remaining_dollars(inputs.accountRiskState, inputs.hardRiskLimits)
        open_cap = open_risk_cap_dollars(inputs.accountRiskState, inputs.hardRiskLimits)
        approved_risk = max(0.0, min(dynamic_risk, hard_cap, daily_remaining, open_cap))
        order_cap = order_notional_cap_dollars(inputs.accountRiskState, inputs.baselineSettings, inputs.hardRiskLimits)
        position_cap = position_notional_cap_dollars(inputs.accountRiskState, inputs.baselineSettings, inputs.hardRiskLimits)
        daily_cap = daily_notional_cap_dollars(inputs.accountRiskState, inputs.baselineSettings, inputs.hardRiskLimits)
        buying_power_cap = inputs.accountRiskState.buyingPower
        maximum_notional = max(0.0, min(order_cap, position_cap, daily_cap, buying_power_cap))
        max_shares = share_cap(inputs.hardRiskLimits)
        sizing = size_position(
            candidate=inputs.candidate,
            account=inputs.accountRiskState,
            baseline_settings=inputs.baselineSettings,
            hard_limits=inputs.hardRiskLimits,
            approved_risk_dollars=approved_risk,
            maximum_notional=maximum_notional,
        )
        quantity = sizing.quantity
        entry_plan = build_entry_plan(inputs.candidate, entry_offset_bps=inputs.baselineSettings.baseEntryOffsetBps, config=self.config)
        target_price = _target_price(inputs.candidate, sizing.stopPlan.selectedStopDistance, baseline_target_r(inputs.baselineSettings))
        exit_plan = build_exit_plan(
            inputs.candidate,
            target_r=baseline_target_r(inputs.baselineSettings),
            holding_minutes=baseline_holding_minutes(inputs.baselineSettings),
            bounds=inputs.dynamicBounds,
            stop_price=sizing.stopPlan.selectedStopPrice,
            target_price=target_price,
            protective_quantity=quantity,
            bracket_oco_supported="BRACKET_OCO" in {str(item).upper() for item in self.config.supportedOrderTypes},
        )
        validation_errors = policy_validation_errors(
            candidate=inputs.candidate,
            account=inputs.accountRiskState,
            hard_limits=inputs.hardRiskLimits,
            now=inputs.evaluatedAt,
            quantity=quantity,
            approved_risk_dollars=approved_risk,
            maximum_notional=maximum_notional,
        )
        if entry_plan is None:
            validation_errors.append("policy.unsupported_or_unconfirmed_entry_plan")
        cap_breakdown = RiskCapBreakdown(
            baselineRiskDollars=round(baseline_risk, 6),
            signalRiskDollars=round(signal_risk, 6),
            dynamicRiskDollars=round(dynamic_risk, 6),
            hardRiskCapDollars=round(hard_cap, 6),
            dailyLossRemainingDollars=round(daily_remaining, 6),
            openRiskCapDollars=round(open_cap, 6),
            orderNotionalCapDollars=round(order_cap, 6),
            positionNotionalCapDollars=round(position_cap, 6),
            dailyNotionalCapDollars=round(daily_cap, 6),
            buyingPowerCapDollars=round(buying_power_cap, 6),
            shareCap=max_shares,
            volumeParticipationCapShares=_share_cap_value(sizing.shareCaps, "liquidityParticipationShares"),
            dynamicRiskCaps=caps,
            limitingRiskCap=limiting_cap.capName,
            stopPlan=sizing.stopPlan,
            shareCaps=sizing.shareCaps,
            limitingShareCap=sizing.limitingShareCap,
            plannedRiskDollars=sizing.plannedRiskDollars,
            appliedCaps=_applied_caps(
                approved_risk=approved_risk,
                dynamic_risk=dynamic_risk,
                hard_cap=hard_cap,
                daily_remaining=daily_remaining,
                open_cap=open_cap,
                maximum_notional=maximum_notional,
                order_cap=order_cap,
                position_cap=position_cap,
                daily_cap=daily_cap,
                buying_power_cap=buying_power_cap,
            ),
            explanation="Approved risk and notional capacity are the minimum of baseline/dynamic policy and hard caps.",
        )
        policy_hash = policy_configuration_hash(
            {
                "policyVersion": POLICY_ENGINE_VERSION,
                "config": self.config,
                "baselineSettingsHash": inputs.baselineSettings.configurationHash,
                "hardLimitsHash": inputs.hardRiskLimits.configurationHash,
                "dynamicBoundsHash": inputs.dynamicBounds.configurationHash,
                "candidateHash": inputs.candidate.configurationHash,
                "regimeHash": inputs.regimeState.configurationHash,
                "metaModelHash": inputs.metaModelPrediction.configurationHash,
                "contextHashes": [context.configurationHash for context in inputs.contextSignals],
            }
        )
        cap_reason_codes = [reason for cap in caps for reason in cap.reasonCodes]
        reason_codes = cap_reason_codes + validation_errors
        if mode_value == OperatingMode.SHADOW.value:
            reason_codes.append("policy.shadow_dynamic_adjustments_not_applied")
        trade_allowed = not validation_errors
        explanation = " ".join(
            [
                f"Policy mode {mode_value} evaluated deterministic baseline and dynamic adjustments.",
                "Independent risk caps were evaluated and the most restrictive cap was applied.",
                f"{limiting_cap.capName} limited effective risk multiplier to {limiting_cap.multiplier:.4f}.",
                *[cap.explanation for cap in caps],
                "Hard limits cap risk and notional capacity and cannot be overridden.",
            ]
        )
        return DynamicTradingPolicyDecision(
            tradeAllowed=trade_allowed,
            approvedRiskDollars=round(approved_risk, 6) if trade_allowed else 0.0,
            effectiveRiskMultiplier=round(applied_multiplier, 6),
            maximumNotional=round(maximum_notional, 6),
            quantity=quantity if trade_allowed else 0,
            entryPlan=entry_plan if trade_allowed else None,
            stop=exit_plan.stopPrice if trade_allowed and exit_plan else None,
            target=exit_plan.targetPrice if trade_allowed and exit_plan else None,
            holdingPeriodMinutes=exit_plan.holdingPeriodMinutes if trade_allowed and exit_plan else 0,
            exitPlan=exit_plan if trade_allowed else None,
            capBreakdown=cap_breakdown,
            reasonCodes=reason_codes,
            explanation=explanation,
            policyVersion=POLICY_ENGINE_VERSION,
            mode=self.config.mode,
            decidedAt=inputs.evaluatedAt,
            sessionDate=inputs.candidate.sessionDate,
            configurationHash=policy_hash,
        )


def _applied_caps(**values: float) -> list[str]:
    applied: list[str] = []
    if values["approved_risk"] == values["hard_cap"]:
        applied.append("hard_risk_cap")
    if values["approved_risk"] == values["daily_remaining"]:
        applied.append("daily_loss_remaining")
    if values["approved_risk"] == values["open_cap"]:
        applied.append("open_risk_cap")
    if values["maximum_notional"] == values["order_cap"]:
        applied.append("order_notional_cap")
    if values["maximum_notional"] == values["position_cap"]:
        applied.append("position_notional_cap")
    if values["maximum_notional"] == values["daily_cap"]:
        applied.append("daily_notional_cap")
    if values["maximum_notional"] == values["buying_power_cap"]:
        applied.append("buying_power_cap")
    return applied


def _target_price(candidate, stop_distance: float, target_r: float) -> float | None:
    if candidate.targetPrice is not None:
        return candidate.targetPrice
    if stop_distance <= 0:
        return None
    signal = getattr(candidate.signal, "value", candidate.signal)
    if signal == "SELL":
        return max(0.01, candidate.entryPrice - (stop_distance * target_r))
    return candidate.entryPrice + (stop_distance * target_r)


def _share_cap_value(caps, cap_name: str) -> int | None:
    for cap in caps:
        if cap.capName == cap_name:
            return cap.shares
    return None
