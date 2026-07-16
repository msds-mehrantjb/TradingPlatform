from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from pydantic import ValidationError

from backend.app.domain.models import (
    AccountRiskState,
    BaselineTradingSettings,
    ContextSignal,
    Direction,
    DynamicPolicyBounds,
    HardRiskLimits,
    MetaModelPrediction,
    OperatingMode,
    RegimeState,
    Signal,
    TradeCandidate,
)
from backend.app.trading_policy import DynamicPolicyInputs, DynamicTradingPolicyConfig, DynamicTradingPolicyEngine
from backend.app.trading_policy.exit_policy import protective_quantity_for_fill, protective_stop_update


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)


class DynamicTradingPolicyEngineTest(unittest.TestCase):
    def test_policy_engine_is_deterministic_for_identical_inputs(self) -> None:
        engine = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE))
        inputs = policy_inputs()

        first = engine.evaluate(inputs)
        second = engine.evaluate(inputs)

        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))
        self.assertTrue(first.tradeAllowed)
        self.assertEqual(first.policyVersion, "dynamic_trading_policy_engine_v1")

    def test_dynamic_modes_off_shadow_active_and_fallback(self) -> None:
        off = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.OFF)).evaluate(policy_inputs())
        shadow = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.SHADOW)).evaluate(policy_inputs())
        active = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(policy_inputs())
        fallback = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.FALLBACK)).evaluate(policy_inputs())

        self.assertEqual(off.effectiveRiskMultiplier, 1.0)
        self.assertEqual(shadow.effectiveRiskMultiplier, 1.0)
        self.assertLess(active.effectiveRiskMultiplier, 1.0)
        self.assertEqual(fallback.effectiveRiskMultiplier, 1.0)
        self.assertIn("policy.shadow_dynamic_adjustments_not_applied", shadow.reasonCodes)

    def test_hard_limits_cannot_be_overridden_by_favorable_signals_or_ml(self) -> None:
        inputs = policy_inputs(
            candidate_confidence=1.0,
            meta_probability=0.99,
            hard_limits=hard_limits(maximum_risk_per_trade_percent=0.1, maximum_shares=5000),
        )
        decision = DynamicTradingPolicyEngine(
            DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE, maximumMetaRiskMultiplier=2.0)
        ).evaluate(inputs)

        self.assertTrue(decision.tradeAllowed)
        self.assertEqual(decision.approvedRiskDollars, 10.0)
        self.assertEqual(decision.quantity, 10)
        self.assertIn("hard_risk_cap", decision.capBreakdown.appliedCaps)
        self.assertIn("Hard limits cap risk", decision.explanation)

    def test_every_adjustment_is_explained_and_visible_in_cap_breakdown(self) -> None:
        inputs = policy_inputs(
            context_signals=[
                context_signal("economic-event", Signal.SELL, Direction.SHORT, "Event context conflicts with long candidate.", features={"recommendedRiskCap": 0.35}),
            ],
            regime=regime_state(label="unknown", volatility="HIGH", confidence=0.4),
        )
        decision = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(inputs)

        self.assertTrue(decision.tradeAllowed)
        self.assertIn("policy.cap.volatility_high", decision.reasonCodes)
        self.assertIn("policy.cap.event_risk", decision.reasonCodes)
        self.assertIn("High volatility limits risk", decision.explanation)
        self.assertIn("Economic-event context limits risk", decision.explanation)
        self.assertEqual(decision.capBreakdown.limitingRiskCap, "eventCap")
        self.assertGreaterEqual(len(decision.capBreakdown.appliedCaps), 1)

    def test_one_severe_adverse_cap_reduces_complete_trade_risk(self) -> None:
        inputs = policy_inputs(
            candidate_confidence=1.0,
            meta_probability=0.99,
            regime=regime_state(label="strong_trend", volatility="EXTREME", confidence=0.95),
        )

        decision = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(inputs)

        self.assertFalse(decision.tradeAllowed)
        self.assertEqual(decision.effectiveRiskMultiplier, 0.0)
        self.assertEqual(decision.capBreakdown.limitingRiskCap, "volatilityCap")
        self.assertIn("policy.cap.volatility_extreme", decision.reasonCodes)

    def test_missing_ml_uses_configured_fallback_cap_in_optional_mode(self) -> None:
        inputs = policy_inputs(candidate_confidence=1.0, meta_probability=None)

        decision = DynamicTradingPolicyEngine(
            DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE, missingMlFallbackCap=0.25)
        ).evaluate(inputs)

        self.assertTrue(decision.tradeAllowed)
        self.assertEqual(decision.effectiveRiskMultiplier, 0.25)
        self.assertEqual(decision.capBreakdown.limitingRiskCap, "MLCap")
        self.assertEqual(decision.approvedRiskDollars, 25.0)
        self.assertIn("policy.cap.ml_missing_fallback", decision.reasonCodes)

    def test_daily_drawdown_progressively_reduces_risk_before_hard_stop(self) -> None:
        inputs = policy_inputs(
            candidate_confidence=1.0,
            meta_probability=0.99,
            account=account_state(realized_pnl_today=-150),
            hard_limits=hard_limits(maximum_daily_loss_percent=3.0),
        )

        decision = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(inputs)

        self.assertTrue(decision.tradeAllowed)
        self.assertEqual(decision.effectiveRiskMultiplier, 0.5)
        self.assertEqual(decision.approvedRiskDollars, 50.0)
        self.assertEqual(decision.capBreakdown.limitingRiskCap, "drawdownCap")
        self.assertIn("policy.cap.daily_drawdown", decision.reasonCodes)

    def test_zero_hard_capacity_blocks_trade_even_when_candidate_is_favorable(self) -> None:
        inputs = policy_inputs(
            account=account_state(realized_pnl_today=-300),
            hard_limits=hard_limits(maximum_daily_loss_percent=3.0),
        )

        decision = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(inputs)

        self.assertFalse(decision.tradeAllowed)
        self.assertEqual(decision.approvedRiskDollars, 0.0)
        self.assertEqual(decision.quantity, 0)
        self.assertIn("policy.no_approved_risk", decision.reasonCodes)

    def test_filter_mode_is_rejected_for_dynamic_policy(self) -> None:
        with self.assertRaisesRegex(ValidationError, "OFF, SHADOW, ACTIVE, or FALLBACK"):
            DynamicTradingPolicyConfig(mode=OperatingMode.FILTER)

    def test_planned_stop_risk_never_exceeds_approved_risk(self) -> None:
        decision = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(
            policy_inputs(candidate_confidence=1.0, meta_probability=0.99)
        )

        self.assertLessEqual(decision.capBreakdown.plannedRiskDollars, decision.approvedRiskDollars)
        self.assertEqual(decision.capBreakdown.stopPlan.limitingComponent, "strategyStructuralInvalidationStop")
        self.assertIn("riskBasedShares", [cap.capName for cap in decision.capBreakdown.shareCaps])

    def test_quantity_zero_when_any_hard_share_cap_permits_less_than_one_share(self) -> None:
        decision = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(
            policy_inputs(
                candidate_confidence=1.0,
                meta_probability=0.99,
                hard_limits=hard_limits(maximum_shares=0),
            )
        )

        self.assertFalse(decision.tradeAllowed)
        self.assertEqual(decision.quantity, 0)
        self.assertEqual(decision.capBreakdown.limitingShareCap, "absoluteMaximumShares")
        self.assertIn("policy.zero_quantity", decision.reasonCodes)

    def test_wider_stops_reduce_quantity(self) -> None:
        narrow = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(
            policy_inputs(candidate_confidence=1.0, meta_probability=0.99)
        )
        wide = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(
            policy_inputs(
                candidate=trade_candidate(
                    confidence=1.0,
                    features={
                        "atr": 20.0,
                        "spreadDollars": 0.02,
                        "currentVolume": 50_000,
                        "expectedVolume": 40_000,
                        "globalExposureRemainingNotional": 5_000,
                    },
                ),
                meta_probability=0.99,
            )
        )

        self.assertGreater(wide.capBreakdown.stopPlan.selectedStopDistance, narrow.capBreakdown.stopPlan.selectedStopDistance)
        self.assertLess(wide.quantity, narrow.quantity)
        self.assertEqual(wide.capBreakdown.stopPlan.limitingComponent, "atrVolatilityStop")

    def test_volume_participation_uses_conservative_current_or_expected_volume(self) -> None:
        decision = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(
            policy_inputs(
                candidate=trade_candidate(
                    confidence=1.0,
                    features={
                        "currentVolume": 900,
                        "expectedVolume": 10_000,
                        "averageVolume": 100_000,
                        "globalExposureRemainingNotional": 5_000,
                    },
                ),
                meta_probability=0.99,
            )
        )

        self.assertEqual(decision.capBreakdown.volumeParticipationCapShares, 9)
        self.assertEqual(decision.capBreakdown.limitingShareCap, "liquidityParticipationShares")
        self.assertEqual(decision.quantity, 9)

    def test_cross_algorithm_exposure_cap_is_included(self) -> None:
        decision = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(
            policy_inputs(
                candidate=trade_candidate(
                    confidence=1.0,
                    features={
                        "currentVolume": 50_000,
                        "expectedVolume": 40_000,
                        "globalExposureRemainingNotional": 450,
                    },
                ),
                meta_probability=0.99,
            )
        )

        self.assertEqual(decision.capBreakdown.limitingShareCap, "globalExposureShares")
        self.assertEqual(decision.quantity, 4)

    def test_trend_and_mean_reversion_use_limit_entries_with_cancel_conditions(self) -> None:
        trend = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(
            policy_inputs(candidate=trade_candidate(confidence=1.0, features=policy_features(strategyFamily="TREND")), meta_probability=0.99)
        )
        mean_reversion = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(
            policy_inputs(candidate=trade_candidate(confidence=1.0, features=policy_features(strategyFamily="MEAN_REVERSION")), meta_probability=0.99)
        )

        for decision in (trend, mean_reversion):
            self.assertTrue(decision.tradeAllowed)
            self.assertEqual(decision.entryPlan.orderType, "LIMIT")
            self.assertIn("structure_invalidates", decision.entryPlan.cancelConditions)
            self.assertIn("maximum_chase_distance_exceeded", decision.entryPlan.cancelConditions)
            self.assertIn("entry_order_expired", decision.entryPlan.cancelConditions)

    def test_breakout_uses_stop_limit_when_supported_and_limit_retest_fallback_when_not(self) -> None:
        candidate = trade_candidate(confidence=1.0, features=policy_features(strategyFamily="BREAKOUT", setupSubtype="volatility_breakout"))
        stop_limit = DynamicTradingPolicyEngine(
            DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE, supportedOrderTypes=["LIMIT", "STOP_LIMIT"])
        ).evaluate(policy_inputs(candidate=candidate, meta_probability=0.99))
        fallback = DynamicTradingPolicyEngine(
            DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE, supportedOrderTypes=["LIMIT"])
        ).evaluate(policy_inputs(candidate=candidate, meta_probability=0.99))

        self.assertEqual(stop_limit.entryPlan.orderType, "STOP_LIMIT")
        self.assertEqual(stop_limit.entryPlan.intent, "breakout_stop_limit_with_spread_aware_buffer")
        self.assertGreater(stop_limit.entryPlan.triggerPrice, candidate.entryPrice)
        self.assertIn("maximum_chase_distance_exceeded", stop_limit.entryPlan.cancelConditions)
        self.assertEqual(fallback.entryPlan.orderType, "LIMIT")
        self.assertEqual(fallback.entryPlan.intent, "breakout_confirmed_limit_retest_entry")
        self.assertIn("stop_limit_unavailable_confirmed_retest_fallback", fallback.entryPlan.brokerCapabilityAssumptions)

    def test_reversal_requires_confirmation_and_uses_reclaimed_level(self) -> None:
        unconfirmed = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(
            policy_inputs(candidate=trade_candidate(confidence=1.0, features=policy_features(strategyFamily="REVERSAL")), meta_probability=0.99)
        )
        confirmed = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(
            policy_inputs(
                candidate=trade_candidate(
                    confidence=1.0,
                    features=policy_features(strategyFamily="REVERSAL", reclaimConfirmed=True, reclaimedLevel=100.25),
                ),
                meta_probability=0.99,
            )
        )

        self.assertFalse(unconfirmed.tradeAllowed)
        self.assertIn("policy.unsupported_or_unconfirmed_entry_plan", unconfirmed.reasonCodes)
        self.assertTrue(confirmed.tradeAllowed)
        self.assertEqual(confirmed.entryPlan.intent, "reversal_reclaim_rejection_limit_entry")
        self.assertEqual(confirmed.entryPlan.entryPrice, 100.25)
        self.assertIn("sweep_or_failed_breakout_extreme_breached", confirmed.entryPlan.cancelConditions)

    def test_gap_session_policy_uses_subtype(self) -> None:
        continuation = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(
            policy_inputs(
                candidate=trade_candidate(confidence=1.0, features=policy_features(strategyFamily="GAP_SESSION", setupSubtype="gap_continuation")),
                meta_probability=0.99,
            )
        )
        fade = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(
            policy_inputs(
                candidate=trade_candidate(confidence=1.0, features=policy_features(strategyFamily="GAP_SESSION", setupSubtype="gap_fade")),
                meta_probability=0.99,
            )
        )

        self.assertEqual(continuation.entryPlan.orderType, "STOP_LIMIT")
        self.assertEqual(fade.entryPlan.orderType, "LIMIT")
        self.assertEqual(fade.entryPlan.intent, "gap_session_continuation_or_fade_policy")

    def test_unsupported_broker_order_capability_blocks_submission(self) -> None:
        decision = DynamicTradingPolicyEngine(
            DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE, supportedOrderTypes=["MARKET"])
        ).evaluate(
            policy_inputs(candidate=trade_candidate(confidence=1.0, features=policy_features(strategyFamily="TREND")), meta_probability=0.99)
        )

        self.assertFalse(decision.tradeAllowed)
        self.assertIsNone(decision.entryPlan)
        self.assertIn("policy.unsupported_or_unconfirmed_entry_plan", decision.reasonCodes)

    def test_accepted_trade_has_complete_exit_plan_with_optional_features_disabled(self) -> None:
        decision = DynamicTradingPolicyEngine(DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE)).evaluate(
            policy_inputs(candidate=trade_candidate(confidence=1.0, features=policy_features(structuralInvalidationPrice=99.4)), meta_probability=0.99)
        )

        self.assertTrue(decision.tradeAllowed)
        self.assertIsNotNone(decision.exitPlan)
        self.assertEqual(decision.exitPlan.initialProtectiveStop, decision.stop)
        self.assertEqual(decision.exitPlan.profitTarget, decision.target)
        self.assertEqual(decision.exitPlan.maximumHoldingMinutes, decision.holdingPeriodMinutes)
        self.assertEqual(decision.exitPlan.strategyInvalidationPrice, 99.4)
        self.assertTrue(decision.exitPlan.endOfDayExit)
        self.assertFalse(decision.exitPlan.breakEvenStopEnabled)
        self.assertFalse(decision.exitPlan.trailingStopEnabled)
        self.assertFalse(decision.exitPlan.partialExitEnabled)
        self.assertFalse(decision.exitPlan.pyramidingEnabled)
        self.assertEqual(decision.exitPlan.protectiveOrderQuantity, decision.quantity)
        self.assertTrue(decision.exitPlan.bracketOcoPlan)

    def test_stop_widening_is_impossible_and_trailing_can_only_reduce_risk(self) -> None:
        widened, widen_reasons = protective_stop_update(
            side=Signal.BUY,
            entry_price=100.0,
            current_stop=99.0,
            proposed_stop=98.5,
        )
        tightened, tighten_reasons = protective_stop_update(
            side=Signal.BUY,
            entry_price=100.0,
            current_stop=99.0,
            proposed_stop=99.5,
        )

        self.assertEqual(widened, 99.0)
        self.assertIn("exit.stop_widening_rejected", widen_reasons)
        self.assertEqual(tightened, 99.5)
        self.assertIn("exit.stop_maintains_or_reduces_risk", tighten_reasons)

    def test_protective_order_quantity_follows_actual_filled_quantity(self) -> None:
        self.assertEqual(protective_quantity_for_fill(planned_quantity=100, filled_quantity=25), 25)
        self.assertEqual(protective_quantity_for_fill(planned_quantity=100, filled_quantity=125), 100)

    def test_bracket_oco_plan_only_when_supported(self) -> None:
        decision = DynamicTradingPolicyEngine(
            DynamicTradingPolicyConfig(mode=OperatingMode.ACTIVE, supportedOrderTypes=["LIMIT", "STOP_LIMIT"])
        ).evaluate(policy_inputs(candidate=trade_candidate(confidence=1.0), meta_probability=0.99))

        self.assertTrue(decision.tradeAllowed)
        self.assertFalse(decision.exitPlan.bracketOcoSupported)
        self.assertFalse(decision.exitPlan.bracketOcoPlan)


def policy_inputs(
    *,
    candidate: TradeCandidate | None = None,
    candidate_confidence: float = 0.8,
    meta_probability: float | None = 0.7,
    context_signals: list[ContextSignal] | None = None,
    regime: RegimeState | None = None,
    account: AccountRiskState | None = None,
    hard_limits: HardRiskLimits | None = None,
) -> DynamicPolicyInputs:
    return DynamicPolicyInputs(
        candidate=candidate or trade_candidate(confidence=candidate_confidence),
        regimeState=regime or regime_state(),
        contextSignals=context_signals or [],
        metaModelPrediction=meta_prediction(probability=meta_probability),
        accountRiskState=account or account_state(),
        baselineSettings=baseline_settings(),
        hardRiskLimits=hard_limits or hard_limits_default(),
        dynamicBounds=dynamic_bounds(),
        evaluatedAt=NOW,
    )


def trade_candidate(*, confidence: float, features: dict | None = None) -> TradeCandidate:
    return TradeCandidate(
        candidateId="candidate-1",
        symbol="SPY",
        signal=Signal.BUY,
        direction=Direction.LONG,
        entryPrice=100.0,
        stopPrice=99.0,
        targetPrice=102.0,
        quantity=0,
        confidence=confidence,
        expectedValue=0.2,
        features=features or policy_features(),
        explanation="Favorable candidate.",
        generatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="candidate-config",
    )


def policy_features(**overrides) -> dict:
    return {
        "atr": 0.2,
        "spreadDollars": 0.02,
        "currentVolume": 50_000,
        "expectedVolume": 40_000,
        "globalExposureRemainingNotional": 5_000,
        **overrides,
    }


def regime_state(label: str = "strong_trend", volatility: str = "NORMAL", confidence: float = 0.8) -> RegimeState:
    return RegimeState(
        regimeId="regime-1",
        label=label,
        direction=Direction.LONG,
        volatility=volatility,
        confidence=confidence,
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=f"regime-{label}-{volatility}-{confidence}",
    )


def context_signal(context_id: str, signal: Signal, direction: Direction, explanation: str, features: dict | None = None) -> ContextSignal:
    return ContextSignal(
        contextId=context_id,
        signal=signal,
        direction=direction,
        confidence=0.8,
        dataReady=True,
        explanation=explanation,
        features=features or {},
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=f"context-{context_id}",
    )


def meta_prediction(*, probability: float | None) -> MetaModelPrediction:
    return MetaModelPrediction(
        modelId="meta",
        modelVersion="v1",
        candidateSide=Signal.BUY,
        probabilityCandidateSuccess=probability,
        probabilityTargetBeforeStop=probability,
        probabilityProfitableAfterCosts=probability,
        signal=Signal.BUY,
        probabilityBuy=probability or 0.0,
        probabilitySell=0.1,
        probabilityHold=0.2,
        confidence=0.8,
        reliability=0.7,
        predictedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=f"meta-{probability}",
    )


def account_state(*, realized_pnl_today: float = 0.0) -> AccountRiskState:
    return AccountRiskState(
        accountId="paper",
        equity=10_000,
        buyingPower=10_000,
        openPositionNotional=0,
        realizedPnlToday=realized_pnl_today,
        tradesToday=0,
        observedAt=NOW,
        sessionDate=SESSION_DATE,
    )


def baseline_settings() -> BaselineTradingSettings:
    return BaselineTradingSettings(
        baseRiskPercent=1.0,
        basePositionPercent=50,
        baseOrderAllocationPercent=20,
        baseDailyAllocationPercent=50,
        baseTargetR=2.0,
        baseMaximumHoldingMinutes=30,
        configurationHash="baseline",
    )


def hard_limits_default() -> HardRiskLimits:
    return hard_limits()


def hard_limits(
    *,
    maximum_risk_per_trade_percent: float = 1.0,
    maximum_daily_loss_percent: float = 3.0,
    maximum_shares: int = 1000,
) -> HardRiskLimits:
    return HardRiskLimits(
        maximumRiskPerTradePercent=maximum_risk_per_trade_percent,
        maximumDailyLossPercent=maximum_daily_loss_percent,
        maximumOpenRiskPercent=2.0,
        maximumPositionPercent=50.0,
        maximumOrderNotionalPercent=20.0,
        maximumDailyNotionalPercent=50.0,
        maximumShares=maximum_shares,
        maximumTradesPerDay=10,
        maxOrderNotional=2_000,
        maxPositionNotional=5_000,
        maxShareQuantity=maximum_shares,
        configurationHash=f"limits-{maximum_risk_per_trade_percent}-{maximum_daily_loss_percent}-{maximum_shares}",
    )


def dynamic_bounds() -> DynamicPolicyBounds:
    return DynamicPolicyBounds(
        minimumRiskMultiplier=0.0,
        maximumRiskMultiplier=1.0,
        minimumTargetR=1.0,
        maximumTargetR=3.0,
        minimumHoldingMinutes=1,
        maximumHoldingMinutes=120,
        minimumAtrStopMultiplier=0.5,
        maximumAtrStopMultiplier=4.0,
        minConfidence=0.0,
        minReliability=0.0,
        minRegimeFit=0.0,
        maxSpreadPercent=100.0,
        maxParticipationPercent=100.0,
        minLiquidityShares=0,
        configurationHash="bounds",
    )


if __name__ == "__main__":
    unittest.main()
