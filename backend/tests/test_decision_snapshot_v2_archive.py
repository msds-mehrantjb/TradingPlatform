from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from pydantic import ValidationError

from backend.app.domain.models import (
    AccountRiskState,
    BaselineTradingSettings,
    DecisionSnapshotV2,
    Direction,
    DynamicPolicyBounds,
    EffectiveTradePolicy,
    EnsembleDecision,
    GateResult,
    GateStatus,
    GlobalGateDecision,
    HardRiskLimits,
    OperatingMode,
    RegimeState,
    Signal,
    StrategyFamily,
    StrategyRole,
    StrategySignal,
    V1SnapshotArchiveRecord,
    decision_snapshot_configuration_hash,
)


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)
CONFIG_HASH = "snapshot-test"


def signal(strategy_id: str = "trend_alignment", role: StrategyRole = StrategyRole.DIRECTIONAL) -> StrategySignal:
    return StrategySignal(
        strategyId=strategy_id,
        strategyName=strategy_id.replace("_", " ").title(),
        strategyVersion="2.0.0",
        family=StrategyFamily.TREND if role == StrategyRole.DIRECTIONAL else StrategyFamily.MARKET_CONTEXT,
        role=role,
        signal=Signal.BUY,
        direction=Direction.LONG,
        confidence=0.7,
        active=True,
        eligible=True,
        dataReady=True,
        setupDetected=True,
        regimeFit=0.6,
        reliability=0.5,
        structuralInvalidationPrice=99.0,
        reasonCodes=["test.signal"],
        explanation="Synthetic signal.",
        features={"feature": 1},
        requiredInputs=["spy1mCandles"],
        inputTimestamps={"spy1mCandles": NOW},
        evaluatedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def gate_decision() -> GlobalGateDecision:
    gate = GateResult(
        gateId="cash_avoid_trading_filter",
        gateName="Cash / Avoid Trading Filter",
        status=GateStatus.PASS,
        blocksTrading=False,
        reasonCodes=["safety.new_entries_allowed"],
        explanation="Safety passes.",
        checkedAt=NOW,
        configurationHash=CONFIG_HASH,
    )
    return GlobalGateDecision(
        status=GateStatus.PASS,
        eligible=True,
        dataReady=True,
        gateResults=[gate],
        reasonCodes=["safety.new_entries_allowed"],
        explanation="Global gates pass.",
        checkedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def policy() -> EffectiveTradePolicy:
    return EffectiveTradePolicy(
        mode=OperatingMode.SHADOW,
        baselineSettings=BaselineTradingSettings(
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
            configurationHash="settings-a",
        ),
        hardRiskLimits=HardRiskLimits(
            maxDailyLossPercent=2,
            maxOrderNotional=2500,
            maxPositionNotional=12500,
            maxShareQuantity=100,
            minStopDistanceDollars=0.05,
            maxSlippagePerShare=0.05,
            configurationHash=CONFIG_HASH,
        ),
        dynamicBounds=DynamicPolicyBounds(
            minConfidence=0.6,
            minReliability=0.5,
            minRegimeFit=0.5,
            maxSpreadPercent=0.03,
            maxParticipationPercent=0.3,
            minLiquidityShares=10000,
            configurationHash=CONFIG_HASH,
        ),
        accountRiskState=AccountRiskState(
            accountId="paper-account",
            equity=25000,
            buyingPower=10000,
            openPositionNotional=0,
            realizedPnlToday=0,
            tradesToday=0,
            observedAt=NOW,
            sessionDate=SESSION_DATE,
        ),
        maxQuantity=25,
        maxNotional=2500,
        riskDollars=50,
        explanation="Policy snapshot.",
        effectiveAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
    )


def ensemble(candidate: Signal = Signal.HOLD) -> EnsembleDecision:
    direction = {Signal.BUY: Direction.LONG, Signal.SELL: Direction.SHORT, Signal.HOLD: Direction.FLAT}[candidate]
    return EnsembleDecision(
        decisionId="ensemble-1",
        signal=candidate,
        direction=direction,
        confidence=0.8 if candidate != Signal.HOLD else 0.2,
        rawScore=0.5 if candidate == Signal.BUY else 0.0,
        finalScore=0.5 if candidate == Signal.BUY else 0.0,
        buyConfidence=0.5 if candidate == Signal.BUY else 0.0,
        sellConfidence=0.0,
        holdConfidence=1.0 if candidate == Signal.HOLD else 0.5,
        eligibleStrategyCount=1,
        strategySignals=[signal()],
        reasonCodes=["test.ensemble"],
        explanation="Ensemble decision.",
        dataReady=True,
        eligible=candidate != Signal.HOLD,
        decidedAt=NOW,
        sessionDate=SESSION_DATE,
        configurationHash=CONFIG_HASH,
        engineVersion="family_aware_deterministic_ensemble_v1",
    )


def snapshot(**updates) -> DecisionSnapshotV2:
    payload = {
        "snapshotId": "snapshot-1",
        "codeVersion": "abc123",
        "symbol": "SPY",
        "marketDataFeed": "fixture",
        "sessionDate": SESSION_DATE,
        "sessionDateNewYork": SESSION_DATE,
        "decisionTimestamp": NOW,
        "decisionTimestampUtc": NOW,
        "operatingMode": OperatingMode.SHADOW,
        "dataQuality": {"dataReady": True, "eligibleForTraining": True},
        "rawMarketReferences": {"spy1mCandles": [{"provider": "fixture", "timestamp": NOW.isoformat()}]},
        "featureSnapshot": {"engineVersion": "point_in_time_feature_engine_v1", "eligibleForTraining": True, "rawInputs": {"provider": "fixture"}},
        "strategySignals": [signal()],
        "contextSignals": [],
        "regimeState": RegimeState(
            regimeId="adx_atr_regime",
            label="range",
            direction=Direction.FLAT,
            volatility="NORMAL",
            confidence=0.5,
            evaluatedAt=NOW,
            sessionDate=SESSION_DATE,
            configurationHash=CONFIG_HASH,
        ),
        "ensembleDecision": ensemble(),
        "globalGateDecision": gate_decision(),
        "effectiveTradePolicy": policy(),
        "tradeCandidate": None,
        "orderPlan": None,
        "fillResult": None,
        "positionState": {"quantity": 0},
        "finalOutcome": None,
        "eligibleForTraining": True,
        "explanation": "Full V2 decision snapshot.",
        "engineVersion": "ensemble-v2",
        "strategyConfigurationHash": "strategy-config-a",
        "tradingSettingsHash": "settings-a",
        "configurationHash": CONFIG_HASH,
    }
    payload.update(updates)
    return DecisionSnapshotV2(**payload)


class DecisionSnapshotV2ArchiveTest(unittest.TestCase):
    def test_v1_archive_is_separate_and_not_training_compatible(self) -> None:
        archive = V1SnapshotArchiveRecord(
            archiveId="archive-1",
            sourceSnapshotId="v1-row-1",
            sourceSchemaVersion="voting_ensemble_v1",
            archivedAt=NOW,
            explanation="Preserved only for historical comparison.",
        )

        self.assertFalse(archive.trainingCompatibleWithV2)
        self.assertEqual(archive.preservedFor, "historical_comparison")
        with self.assertRaisesRegex(ValidationError, "incompatible with V2 training"):
            V1SnapshotArchiveRecord(
                archiveId="archive-2",
                sourceSnapshotId="v1-row-2",
                sourceSchemaVersion="voting_ensemble_v1",
                archivedAt=NOW,
                trainingCompatibleWithV2=True,
                explanation="Invalid V1 training archive.",
            )

    def test_v1_schema_payload_cannot_validate_as_v2_snapshot(self) -> None:
        with self.assertRaises(ValidationError):
            snapshot(snapshotSchemaVersion="voting_ensemble_v1")

    def test_demo_or_fallback_market_data_is_excluded_from_training(self) -> None:
        with self.assertRaisesRegex(ValidationError, "incompatible data"):
            snapshot(rawMarketReferences={"spy1mCandles": [{"provider": "demo"}]}, eligibleForTraining=True)

        stored = snapshot(rawMarketReferences={"spy1mCandles": [{"provider": "fallback"}]}, eligibleForTraining=False)
        self.assertIn("demo_or_fallback_market_data", stored.trainingIncompatibilityReasons)

    def test_old_duplicated_aggregator_signal_is_not_training_compatible(self) -> None:
        with self.assertRaisesRegex(ValidationError, "incompatible data"):
            snapshot(strategySignals=[signal("ensemble_strategy_voting", StrategyRole.AGGREGATOR)], eligibleForTraining=True)

    def test_hash_changes_when_effective_versions_or_settings_change(self) -> None:
        base_parts = {
            "strategyConfigurationHash": "strategy-a",
            "gateVersion": "gate-a",
            "labelVersion": "label-a",
            "tradingSettingsHash": "settings-a",
            "policyVersion": "policy-a",
        }
        base_hash = decision_snapshot_configuration_hash(base_parts)

        for key in base_parts:
            changed = {**base_parts, key: f"{base_parts[key]}-changed"}
            self.assertNotEqual(base_hash, decision_snapshot_configuration_hash(changed), key)

    def test_hold_decision_snapshot_records_eligible_timestamp(self) -> None:
        stored = snapshot(ensembleDecision=ensemble(Signal.HOLD), tradeCandidate=None, orderPlan=None)

        self.assertEqual(stored.ensembleDecision.signal, Signal.HOLD.value)
        self.assertEqual(stored.samplingProbability, 1.0)
        self.assertEqual(stored.sampleWeight, 1.0)
        self.assertEqual(stored.samplingReason, "record_all_eligible_decision_timestamps")

    def test_v2_snapshot_contains_reproducibility_payloads(self) -> None:
        stored = snapshot()

        self.assertEqual(stored.snapshotSchemaVersion, "decision_snapshot_v2")
        self.assertEqual(stored.decisionTimestampUtc, stored.decisionTimestamp)
        self.assertEqual(stored.sessionDateNewYork, stored.sessionDate)
        self.assertTrue(stored.rawMarketReferences)
        self.assertTrue(stored.featureSnapshot)
        self.assertEqual(stored.directionalStrategyOutputs[0].strategyId, stored.strategySignals[0].strategyId)
        self.assertEqual(stored.safetyOutput.status, stored.globalGateDecision.status)
        self.assertEqual(stored.globalGateResults[0].gateId, "cash_avoid_trading_filter")


if __name__ == "__main__":
    unittest.main()
