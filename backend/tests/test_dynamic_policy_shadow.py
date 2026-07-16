from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from pydantic import ValidationError

from backend.app.backtesting import ReplayDecisionSnapshot, build_dynamic_policy_shadow_report
from backend.app.domain.models import (
    AccountRiskState,
    BaselineTradingSettings,
    Direction,
    DynamicPolicyBounds,
    HardRiskLimits,
    OperatingMode,
    RegimeState,
    Signal,
    TradeCandidate,
)


NOW = datetime(2026, 1, 5, 16, 0, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)


class DynamicPolicyShadowTest(unittest.TestCase):
    def test_dynamic_policy_shadow_calculates_risk_stop_quantity_entry_target_and_holding_time(self) -> None:
        report = build_dynamic_policy_shadow_report(snapshot=snapshot(), generatedAt=NOW)

        self.assertEqual(report.shadowConfig.dynamicTradingPolicyMode, OperatingMode.SHADOW.value)
        self.assertTrue(report.staticPaperOrderPathUnchanged)
        self.assertFalse(report.dynamicSubmittedPaperOrder)
        self.assertTrue(report.replayableSideBySide)
        self.assertTrue(report.capBreakdownsComplete)
        self.assertTrue(report.hardLimitsRespected)
        self.assertTrue(report.baselineRiskNotExceeded)
        self.assertIsNotNone(report.dynamicPolicy)
        assert report.dynamicPolicy is not None
        self.assertGreaterEqual(report.dynamicPolicy["approvedRiskDollars"], 0)
        self.assertLessEqual(report.dynamicPolicy["approvedRiskDollars"], report.comparison.staticRiskDollars)
        self.assertGreater(report.dynamicPolicy["capBreakdown"]["stopPlan"]["selectedStopDistance"], 0)
        self.assertGreaterEqual(report.dynamicPolicy["quantity"], 0)
        self.assertEqual(report.dynamicPolicy["entryPlan"]["orderType"], "LIMIT")
        self.assertGreater(report.dynamicPolicy["target"], 0)
        self.assertGreater(report.dynamicPolicy["holdingPeriodMinutes"], 0)
        self.assertIn("dynamic_policy.cap_breakdown_complete", report.reasonCodes)
        self.assertIn("dynamic_policy.baseline_risk_not_exceeded", report.reasonCodes)

    def test_dynamic_policy_shadow_uses_identical_candidate_and_keeps_static_order(self) -> None:
        report = build_dynamic_policy_shadow_report(snapshot=snapshot(), generatedAt=NOW)

        self.assertEqual(report.comparison.candidateId, "candidate-dynamic-shadow")
        self.assertTrue(report.comparison.identicalCandidate)
        self.assertEqual(report.staticOrderPlan["orderPlanId"], "static-order-dynamic-shadow")
        self.assertEqual(report.staticOrderPlan["orderType"], "LIMIT")
        self.assertTrue(report.staticOrderPlan["eligible"])
        self.assertFalse(report.dynamicSubmittedPaperOrder)

    def test_report_rejects_dynamic_shadow_that_exceeds_baseline_risk(self) -> None:
        report = build_dynamic_policy_shadow_report(snapshot=snapshot(static_risk_dollars=100.0), generatedAt=NOW)
        payload = report.model_dump(mode="json")
        payload["comparison"]["dynamicApprovedRiskDollars"] = 2.0
        payload["comparison"]["staticRiskDollars"] = 1.0
        payload["baselineRiskNotExceeded"] = False

        with self.assertRaisesRegex(ValidationError, "cannot exceed baseline risk"):
            type(report).model_validate(payload)

    def test_no_candidate_is_replayable_without_dynamic_trade_plan(self) -> None:
        no_trade = snapshot(has_candidate=False, ensemble_signal="HOLD", static_order=False)
        report = build_dynamic_policy_shadow_report(snapshot=no_trade, generatedAt=NOW)

        self.assertIsNone(report.dynamicPolicy)
        self.assertEqual(report.comparison.dynamicQuantity, 0)
        self.assertTrue(report.capBreakdownsComplete)
        self.assertTrue(report.hardLimitsRespected)
        self.assertIn("dynamic_policy.no_deterministic_candidate", report.reasonCodes)


def snapshot(
    *,
    has_candidate: bool = True,
    ensemble_signal: str = "BUY",
    static_order: bool = True,
    static_risk_dollars: float = 100.0,
) -> ReplayDecisionSnapshot:
    candidate = trade_candidate(signal=ensemble_signal).model_dump(mode="json") if has_candidate else None
    return ReplayDecisionSnapshot(
        snapshotId="dynamic-policy-shadow-snapshot",
        symbol="SPY",
        decisionTimestampUtc=NOW,
        sessionDate=SESSION_DATE,
        maxInputTimestampUtc=NOW,
        featureSnapshot={"engineVersion": "point_in_time_feature_engine_v1", "dataReady": True},
        strategyOutputs=[{"strategyId": "multi_timeframe_trend_alignment", "signal": ensemble_signal, "dataReady": True}],
        contextOutputs=[],
        regimeState=regime_state().model_dump(mode="json"),
        gateDecision={"eligible": True, "configurationHash": "gate-hash", "reasonCodes": []},
        deterministicCandidate=candidate,
        ensembleDecision={"signal": ensemble_signal, "configurationHash": "ensemble-hash", "engineVersion": "family_aware_deterministic_ensemble_v1"},
        mlInference={
            "mode": "SHADOW",
            "effectiveMode": "SHADOW",
            "deterministicSignal": ensemble_signal,
            "finalSignal": ensemble_signal,
            "candidateAccepted": has_candidate,
            "mlWouldAcceptCandidate": True,
            "appliedToOrder": False,
            "successProbability": 0.70,
            "calibratedProbability": 0.70,
            "recommendedRiskCap": 1.0,
            "modelHealth": {"status": "OK", "score": 1.0},
            "reasonCodes": ["ml.shadow_record_only"],
            "configurationHash": "safe_ml_shadow",
        },
        effectivePolicy=effective_policy(static_risk_dollars=static_risk_dollars),
        orderPlan=order_plan(eligible=static_order and has_candidate, signal=ensemble_signal) if static_order else None,
        fill=None,
        exit=None,
        reasonCodes=["synthetic.dynamic_policy_shadow"],
    )


def trade_candidate(*, signal: str) -> TradeCandidate:
    side = Signal(signal)
    return TradeCandidate(
        candidateId="candidate-dynamic-shadow",
        symbol="SPY",
        signal=side,
        direction=Direction.LONG if side == Signal.BUY else Direction.SHORT,
        entryPrice=100.0,
        stopPrice=99.0 if side == Signal.BUY else 101.0,
        targetPrice=102.0 if side == Signal.BUY else 98.0,
        quantity=1,
        confidence=0.80,
        expectedValue=0.2,
        features={
            "atr": 0.30,
            "spreadDollars": 0.02,
            "currentVolume": 50_000,
            "expectedVolume": 40_000,
            "globalExposureRemainingNotional": 5_000,
        },
        reasonCodes=["synthetic.candidate"],
        explanation="Synthetic deterministic candidate for dynamic policy shadow.",
        generatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="candidate-dynamic-shadow",
    )


def effective_policy(*, static_risk_dollars: float) -> dict:
    return {
        "mode": "SHADOW",
        "baselineSettings": baseline_settings().model_dump(mode="json"),
        "hardRiskLimits": hard_limits().model_dump(mode="json"),
        "dynamicBounds": dynamic_bounds().model_dump(mode="json"),
        "accountRiskState": account_state().model_dump(mode="json"),
        "maxQuantity": 100,
        "maxNotional": 2000.0,
        "riskDollars": static_risk_dollars,
        "explanation": "Synthetic static baseline effective policy.",
        "effectiveAt": NOW,
        "sessionDate": SESSION_DATE,
        "configurationHash": "static-policy-dynamic-shadow",
    }


def order_plan(*, eligible: bool, signal: str) -> dict:
    return {
        "orderPlanId": "static-order-dynamic-shadow" if eligible else "static-no-order-dynamic-shadow",
        "candidateId": "candidate-dynamic-shadow",
        "symbol": "SPY",
        "side": signal if signal != "HOLD" else "BUY",
        "orderType": "LIMIT" if eligible else "NO_ORDER",
        "quantity": 1 if eligible else 0,
        "entryPrice": 100.0,
        "stopPrice": 99.0,
        "targetPrice": 102.0,
        "limitPrice": 100.0 if eligible else None,
        "maximumHoldingMinutes": 30,
        "timeInForce": "DAY",
        "eligible": eligible,
        "validationErrors": [] if eligible else ["order.no_trade"],
        "explanation": "Synthetic static paper order path.",
        "generatedAt": NOW,
        "sessionDate": SESSION_DATE,
        "configurationHash": "static-policy-dynamic-shadow",
    }


def regime_state() -> RegimeState:
    return RegimeState(
        regimeId="regime-dynamic-shadow",
        label="strong_trend",
        direction=Direction.LONG,
        volatility="NORMAL",
        confidence=0.8,
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash="regime-dynamic-shadow",
    )


def account_state() -> AccountRiskState:
    return AccountRiskState(
        accountId="paper",
        equity=10_000,
        buyingPower=10_000,
        openPositionNotional=0.0,
        realizedPnlToday=0.0,
        tradesToday=0,
        observedAt=NOW,
        sessionDate=SESSION_DATE,
    )


def baseline_settings() -> BaselineTradingSettings:
    return BaselineTradingSettings(
        baseRiskPercent=1.0,
        basePositionPercent=50.0,
        baseOrderAllocationPercent=20.0,
        baseDailyAllocationPercent=50.0,
        baseAtrStopMultiplier=2.0,
        baseMinimumStopPercent=0.05,
        baseTargetR=2.0,
        baseMaximumHoldingMinutes=30,
        baseParticipationPercent=1.0,
        configurationHash="baseline-dynamic-shadow",
    )


def hard_limits() -> HardRiskLimits:
    return HardRiskLimits(
        maximumRiskPerTradePercent=1.0,
        maximumDailyLossPercent=3.0,
        maximumOpenRiskPercent=2.0,
        maximumPositionPercent=50.0,
        maximumOrderNotionalPercent=20.0,
        maximumDailyNotionalPercent=50.0,
        maximumShares=1000,
        maximumVolumeParticipationPercent=1.0,
        maximumTradesPerDay=10,
        maxOrderNotional=2_000,
        maxPositionNotional=5_000,
        maxShareQuantity=1000,
        minStopDistanceDollars=0.01,
        configurationHash="hard-limits-dynamic-shadow",
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
        configurationHash="dynamic-bounds-shadow",
    )


if __name__ == "__main__":
    unittest.main()
