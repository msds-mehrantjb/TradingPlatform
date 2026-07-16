from __future__ import annotations

import json
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import ValidationError

from backend.app.domain.models import (
    AccountRiskState,
    BaselineTradingSettings,
    ContextSignal,
    DecisionSnapshotV2,
    Direction,
    DynamicPolicyBounds,
    EffectiveTradePolicy,
    EnsembleDecision,
    FamilyScore,
    GateResult,
    GateStatus,
    GlobalGateDecision,
    HardRiskLimits,
    MetaModelPrediction,
    OperatingMode,
    OrderPlan,
    RegimeState,
    Signal,
    StrategyFamily,
    StrategyRole,
    StrategySignal,
    TradeCandidate,
)


NOW = datetime(2026, 1, 5, 15, 29, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)
CONFIG_HASH = "test-config"


def strategy_signal() -> StrategySignal:
    return StrategySignal(
        strategyId="trend_alignment",
        strategyName="Trend Alignment",
        strategyVersion="v2.0.0",
        family=StrategyFamily.TREND,
        role=StrategyRole.DIRECTIONAL,
        signal=Signal.BUY,
        direction=Direction.LONG,
        confidence=0.72,
        active=True,
        eligible=True,
        dataReady=True,
        setupDetected=True,
        regimeFit=0.68,
        reliability=0.61,
        structuralInvalidationPrice=99.5,
        reasonCodes=["trend.confirmed"],
        explanation="Trend setup is confirmed.",
        features={"sma20": 101.0},
        requiredInputs=["candles.1m"],
        inputTimestamps={"candles.1m": NOW},
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def baseline_settings() -> BaselineTradingSettings:
    return BaselineTradingSettings(
        startingCapital=25000,
        orderAllocationPercent=10,
        dailyAllocationPercent=30,
        riskBudgetPercentOfOrder=50,
        maxTradesPerDay=3,
        stopLossPercent=0.35,
        fixedStopDistanceDollars=1,
        takeProfitR=1.5,
        slippagePerShare=0.02,
        positionSizingMode="allocation",
        settingsVersion="baseline-v1",
        configurationHash=CONFIG_HASH,
    )


def hard_limits() -> HardRiskLimits:
    return HardRiskLimits(
        maxDailyLossPercent=2,
        maxOrderNotional=2500,
        maxPositionNotional=12500,
        maxShareQuantity=100,
        minStopDistanceDollars=0.05,
        maxSlippagePerShare=0.05,
        configurationHash=CONFIG_HASH,
    )


def dynamic_bounds() -> DynamicPolicyBounds:
    return DynamicPolicyBounds(
        minConfidence=0.6,
        minReliability=0.55,
        minRegimeFit=0.5,
        maxSpreadPercent=0.03,
        maxParticipationPercent=0.3,
        minLiquidityShares=10000,
        configurationHash=CONFIG_HASH,
    )


def account_state() -> AccountRiskState:
    return AccountRiskState(
        accountId="paper-account",
        equity=25000,
        buyingPower=7500,
        openPositionNotional=0,
        realizedPnlToday=0,
        tradesToday=0,
        observedAt=NOW,
        sessionDate=SESSION_DATE,
    )


class DomainModelsV2Test(unittest.TestCase):
    def test_strategy_signal_rejects_invalid_confidence_and_direction_proxy(self) -> None:
        with self.assertRaises(ValidationError):
            StrategySignal(
                **{
                    **strategy_signal().model_dump(),
                    "confidence": 1.01,
                }
            )

        with self.assertRaisesRegex(ValidationError, "direction must be derived from signal"):
            StrategySignal(
                **{
                    **strategy_signal().model_dump(),
                    "direction": Direction.SHORT,
                }
            )

    def test_strategy_signal_rejects_non_utc_timestamps_and_extra_fields(self) -> None:
        with self.assertRaisesRegex(ValidationError, "timezone-aware UTC"):
            StrategySignal(
                **{
                    **strategy_signal().model_dump(),
                    "evaluatedAt": datetime(2026, 1, 5, 15, 29),
                }
            )

        with self.assertRaises(ValidationError):
            StrategySignal(
                **{
                    **strategy_signal().model_dump(),
                    "unexpectedOneOffOutput": True,
                }
            )

    def test_trade_candidate_and_order_plan_reject_invalid_geometry(self) -> None:
        with self.assertRaisesRegex(ValidationError, "BUY geometry"):
            TradeCandidate(
                candidateId="bad-buy",
                symbol="SPY",
                signal=Signal.BUY,
                direction=Direction.LONG,
                entryPrice=100,
                stopPrice=101,
                targetPrice=102,
                quantity=1,
                confidence=0.7,
                expectedValue=0.1,
                explanation="Bad buy geometry.",
                generatedAt=NOW,
                sessionDate=SESSION_DATE,
                configurationHash=CONFIG_HASH,
            )

        with self.assertRaisesRegex(ValidationError, "SELL geometry"):
            OrderPlan(
                orderPlanId="bad-sell",
                candidateId="candidate-1",
                symbol="SPY",
                side=Signal.SELL,
                orderType="STOP_LIMIT",
                quantity=1,
                entryPrice=100,
                stopPrice=99,
                targetPrice=98,
                limitPrice=99.95,
                timeInForce="DAY",
                eligible=False,
                explanation="Bad sell geometry.",
                generatedAt=NOW,
                sessionDate=SESSION_DATE,
                configurationHash=CONFIG_HASH,
            )

    def test_decision_snapshot_v2_serialization_round_trip(self) -> None:
        signal = strategy_signal()
        family_score = FamilyScore(
            family=StrategyFamily.TREND,
            buyScore=0.7,
            sellScore=0.1,
            holdScore=0.2,
            confidence=0.72,
            reliability=0.61,
            explanation="Trend family favors buy.",
        )
        regime = RegimeState(
            regimeId="trend-up",
            label="Trend Up",
            direction=Direction.LONG,
            volatility="NORMAL",
            confidence=0.67,
            evaluatedAt=NOW,
            sessionDate=SESSION_DATE,
            configurationHash=CONFIG_HASH,
        )
        ensemble = EnsembleDecision(
            decisionId="decision-1",
            signal=Signal.BUY,
            direction=Direction.LONG,
            confidence=0.7,
            familyScores=[family_score],
            strategySignals=[signal],
            reasonCodes=["family.trend.buy"],
            explanation="Canonical ensemble decision.",
            dataReady=True,
            eligible=True,
            decidedAt=NOW,
            sessionDate=SESSION_DATE,
            configurationHash=CONFIG_HASH,
            engineVersion="ensemble-v2",
        )
        gate = GateResult(
            gateId="global-risk",
            gateName="Global Risk",
            status=GateStatus.PASS,
            blocksTrading=False,
            explanation="Risk gates pass.",
            checkedAt=NOW,
            configurationHash=CONFIG_HASH,
        )
        policy = EffectiveTradePolicy(
            mode=OperatingMode.SHADOW,
            baselineSettings=baseline_settings(),
            hardRiskLimits=hard_limits(),
            dynamicBounds=dynamic_bounds(),
            accountRiskState=account_state(),
            maxQuantity=23,
            maxNotional=2500,
            riskDollars=25,
            explanation="Policy is effective in shadow mode.",
            effectiveAt=NOW,
            sessionDate=SESSION_DATE,
            configurationHash=CONFIG_HASH,
        )
        candidate = TradeCandidate(
            candidateId="candidate-1",
            symbol="SPY",
            signal=Signal.BUY,
            direction=Direction.LONG,
            entryPrice=100,
            stopPrice=99,
            targetPrice=101.5,
            quantity=23,
            confidence=0.7,
            expectedValue=0.12,
            explanation="Candidate geometry is valid.",
            generatedAt=NOW,
            sessionDate=SESSION_DATE,
            configurationHash=CONFIG_HASH,
        )
        snapshot = DecisionSnapshotV2(
            snapshotVersion="decision_snapshot_v2",
            snapshotId="snapshot-1",
            symbol="SPY",
            sessionDate=SESSION_DATE,
            decisionTimestamp=NOW,
            operatingMode=OperatingMode.SHADOW,
            strategySignals=[signal],
            contextSignals=[
                ContextSignal(
                    contextId="market-context",
                    signal=Signal.BUY,
                    direction=Direction.LONG,
                    confidence=0.65,
                    dataReady=True,
                    explanation="Context confirms buy.",
                    evaluatedAt=NOW,
                    sessionDate=SESSION_DATE,
                    configurationHash=CONFIG_HASH,
                )
            ],
            regimeState=regime,
            ensembleDecision=ensemble,
            metaModelPrediction=MetaModelPrediction(
                modelId="meta-v2",
                modelVersion="0.0.1",
                signal=Signal.BUY,
                probabilityBuy=0.64,
                probabilitySell=0.16,
                probabilityHold=0.2,
                confidence=0.64,
                reliability=0.58,
                predictedAt=NOW,
                sessionDate=SESSION_DATE,
                configurationHash=CONFIG_HASH,
            ),
            globalGateDecision=GlobalGateDecision(
                status=GateStatus.PASS,
                eligible=True,
                dataReady=True,
                gateResults=[gate],
                explanation="Global gates pass.",
                checkedAt=NOW,
                sessionDate=SESSION_DATE,
                configurationHash=CONFIG_HASH,
            ),
            effectiveTradePolicy=policy,
            tradeCandidate=candidate,
            orderPlan=OrderPlan(
                orderPlanId="order-plan-1",
                candidateId="candidate-1",
                symbol="SPY",
                side=Signal.BUY,
                orderType="STOP_LIMIT",
                quantity=23,
                entryPrice=100,
                stopPrice=99,
                targetPrice=101.5,
                limitPrice=100.03,
                timeInForce="DAY",
                eligible=True,
                explanation="Order geometry is valid.",
                generatedAt=NOW,
                sessionDate=SESSION_DATE,
                configurationHash=CONFIG_HASH,
            ),
            explanation="Full V2 decision snapshot.",
            engineVersion="ensemble-v2",
            configurationHash=CONFIG_HASH,
        )

        encoded = snapshot.model_dump_json()
        decoded = DecisionSnapshotV2.model_validate_json(encoded)

        self.assertEqual(decoded.model_dump(mode="json"), snapshot.model_dump(mode="json"))
        payload = json.loads(encoded)
        self.assertEqual(payload["strategySignals"][0]["confidence"], 0.72)
        self.assertEqual(payload["strategySignals"][0]["regimeFit"], 0.68)
        self.assertEqual(payload["strategySignals"][0]["reliability"], 0.61)
        self.assertEqual(payload["decisionTimestamp"], "2026-01-05T15:29:00Z")
        self.assertEqual(payload["sessionDate"], "2026-01-05")

    def test_frontend_model_file_uses_same_enum_values_and_field_names(self) -> None:
        frontend_models = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "domain" / "models.ts").read_text(encoding="utf-8")
        for value in ["BUY", "SELL", "HOLD", "DIRECTIONAL", "AGGREGATOR", "MEAN_REVERSION", "PASS", "CAUTION", "FAIL", "INFO", "SHADOW", "ACTIVE"]:
            self.assertIn(value, frontend_models)
        for field_name in [
            "strategyId",
            "strategyName",
            "regimeFit",
            "reliability",
            "configurationHash",
            "decisionTimestamp",
            "sessionDate",
            "CandidateMetaLabel",
            "strictOutcomeLabel",
            "costAdjustedTrainingLabel",
            "probabilityCandidateSuccess",
            "MLFeatureSet",
            "candidate_meta_feature_schema_v1",
            "missingIndicators",
            "OutOfSampleForecastFeature",
            "market_forecast_oos_feature_v1",
            "trainingWindowEndUtc",
        ]:
            self.assertIn(field_name, frontend_models)


if __name__ == "__main__":
    unittest.main()
