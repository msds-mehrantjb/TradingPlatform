from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from pydantic import ValidationError

from backend.app.backtesting import (
    ReplayDecisionSnapshot,
    ReplayResult,
    V1ShadowDecision,
    build_historical_shadow_comparison,
    historical_shadow_application_config,
)
from backend.app.domain.models import Signal


START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
SESSION_DATE = date(2026, 1, 5)


class HistoricalShadowComparisonTest(unittest.TestCase):
    def test_shadow_config_enables_v2_strategy_and_family_flags_only(self) -> None:
        config = historical_shadow_application_config().as_dict()
        flags = config["featureFlags"]

        self.assertTrue(flags["strategyEngineV2Enabled"])
        self.assertTrue(flags["familyEnsembleV2Enabled"])
        self.assertFalse(flags["metaModelV2Enabled"])
        self.assertFalse(flags["dynamicTradingPolicyEnabled"])
        self.assertFalse(flags["globalGateEngineEnabled"])
        self.assertIn("configurationHash", config)

    def test_report_records_v2_decisions_without_affecting_paper_orders(self) -> None:
        report = build_historical_shadow_comparison(
            v1Decisions=[
                v1_decision("v1-1", START, Signal.BUY, trade_opened=True, expected_value=0.3, drawdown=0.2),
                v1_decision("v1-2", START + timedelta(minutes=1), Signal.HOLD),
            ],
            v2Replay=replay_result(
                [
                    v2_snapshot("v2-1", START, Signal.HOLD, data_ready=True),
                    v2_snapshot("v2-2", START + timedelta(minutes=1), Signal.SELL, data_ready=False),
                ]
            ),
            generatedAt=START,
            minimumCleanV2SnapshotsForMl=10,
        )

        self.assertEqual(report.featureFlags.orderBehavior, "V1_OR_DISABLED")
        self.assertFalse(report.featureFlags.paperOrderSubmissionEnabled)
        self.assertEqual(report.storage.v1Namespace, "voting_ensemble_v1_reference")
        self.assertEqual(report.storage.v2Namespace, "family_ensemble_v2_shadow")
        self.assertFalse(report.storage.v1TrainingCompatibleWithV2)
        self.assertEqual(report.recordedV2SnapshotIds, ["v2-1", "v2-2"])
        self.assertTrue(all(snapshot["orderBehavior"] == "DISABLED" for snapshot in report.v2ShadowSnapshots))
        self.assertTrue(all(snapshot["fill"] is None and snapshot["exit"] is None for snapshot in report.v2ShadowSnapshots))
        self.assertEqual(report.aggregates.signalDifferenceCount, 2)
        self.assertEqual(report.aggregates.v1TradeCount, 1)
        self.assertEqual(report.aggregates.v2CandidateCount, 1)
        self.assertEqual(report.aggregates.tradeCountDifference, 0)
        self.assertEqual(report.aggregates.dataReadinessFailureCount, 3)
        self.assertIn("TREND", report.familyCoverage)
        self.assertIsNotNone(report.strategyCorrelation)
        self.assertFalse(report.v2MlTrainingAllowed)
        self.assertEqual(report.mlTrainingBlockReason, "v2_ml_training_blocked_until_enough_clean_shadow_snapshots_exist")

    def test_v2_ml_training_unlocks_only_after_clean_snapshot_threshold(self) -> None:
        report = build_historical_shadow_comparison(
            v1Decisions=[
                v1_decision("v1-1", START, Signal.BUY),
                v1_decision("v1-2", START + timedelta(minutes=1), Signal.SELL),
            ],
            v2Replay=replay_result(
                [
                    v2_snapshot("v2-1", START, Signal.BUY, data_ready=True),
                    v2_snapshot("v2-2", START + timedelta(minutes=1), Signal.SELL, data_ready=True),
                ]
            ),
            generatedAt=START,
            minimumCleanV2SnapshotsForMl=2,
        )

        self.assertTrue(report.v2MlTrainingAllowed)
        self.assertEqual(report.cleanV2SnapshotCount, 2)

    def test_old_proxy_strategy_or_self_vote_is_rejected_from_v2_shadow(self) -> None:
        with self.assertRaisesRegex(ValidationError, "old proxy or aggregator"):
            build_historical_shadow_comparison(
                v1Decisions=[v1_decision("v1-1", START, Signal.HOLD)],
                v2Replay=replay_result(
                    [
                        v2_snapshot(
                            "v2-1",
                            START,
                            Signal.BUY,
                            data_ready=True,
                            extra_strategy={
                                "strategyId": "ensemble_strategy_voting",
                                "strategyName": "Ensemble Strategy Voting",
                                "family": "MARKET_CONTEXT",
                                "signal": "BUY",
                                "direction": 1,
                                "eligible": True,
                                "dataReady": True,
                            },
                        )
                    ]
                ),
                generatedAt=START,
            )

    def test_signal_difference_explanation_uses_v2_strategy_evidence(self) -> None:
        report = build_historical_shadow_comparison(
            v1Decisions=[v1_decision("v1-1", START, Signal.BUY)],
            v2Replay=replay_result([v2_snapshot("v2-1", START, Signal.SELL, data_ready=True)]),
            generatedAt=START,
        )

        comparison = report.decisionComparisons[0]
        self.assertTrue(comparison.signalChanged)
        self.assertIn("supporting=REVERSAL", comparison.explanation)
        self.assertIn("score=-0.72", comparison.explanation)


def v1_decision(
    snapshot_id: str,
    timestamp: datetime,
    signal: Signal,
    *,
    trade_opened: bool = False,
    expected_value: float | None = None,
    drawdown: float = 0.0,
) -> V1ShadowDecision:
    return V1ShadowDecision(
        snapshotId=snapshot_id,
        symbol="SPY",
        decisionTimestampUtc=timestamp,
        sessionDate=SESSION_DATE,
        signal=signal,
        tradeOpened=trade_opened,
        expectedValue=expected_value,
        drawdown=drawdown,
        strategyProxyMappings=["v1 documented proxy mappings"],
        explanation="V1 reference decision.",
    )


def replay_result(snapshots: list[ReplayDecisionSnapshot]) -> ReplayResult:
    return ReplayResult(
        engineVersion="event_driven_replay_engine_v1",
        symbol="SPY",
        sessionDate=SESSION_DATE,
        decisionCount=len(snapshots),
        snapshots=snapshots,
        trades=[],
        explanation="Synthetic V2 replay result for historical shadow comparison.",
    )


def v2_snapshot(
    snapshot_id: str,
    timestamp: datetime,
    signal: Signal,
    *,
    data_ready: bool,
    extra_strategy: dict | None = None,
) -> ReplayDecisionSnapshot:
    strategy_outputs = [
        strategy_output("multi_timeframe_trend_alignment", "Multi-Timeframe Trend Alignment", "TREND", signal, data_ready),
        strategy_output("opening_range_breakout", "Opening Range Breakout", "BREAKOUT", Signal.HOLD, data_ready),
    ]
    if extra_strategy:
        strategy_outputs.append(extra_strategy)
    supporting = ["REVERSAL"] if signal == Signal.SELL else (["TREND"] if signal == Signal.BUY else [])
    opposing = ["TREND"] if signal == Signal.SELL else []
    return ReplayDecisionSnapshot(
        snapshotId=snapshot_id,
        symbol="SPY",
        decisionTimestampUtc=timestamp,
        sessionDate=SESSION_DATE,
        maxInputTimestampUtc=timestamp,
        featureSnapshot={
            "dataReady": data_ready,
            "reasonCodes": [] if data_ready else ["qqq_auxiliary_data_stale"],
        },
        strategyOutputs=strategy_outputs,
        contextOutputs=[],
        regimeState=None,
        gateDecision={"eligible": True},
        deterministicCandidate=(
            {
                "candidateId": f"candidate-{snapshot_id}",
                "expectedValue": -0.1 if signal == Signal.SELL else 0.4,
            }
            if signal != Signal.HOLD
            else None
        ),
        ensembleDecision={
            "signal": signal.value,
            "finalScore": -0.72 if signal == Signal.SELL else (0.66 if signal == Signal.BUY else 0.0),
            "supportingFamilies": supporting,
            "opposingFamilies": opposing,
        },
        mlInference={"mode": "OFF"},
        effectivePolicy={},
        orderPlan=None,
        fill={"status": "SHOULD_BE_REMOVED_IN_SHADOW"},
        exit={"status": "SHOULD_BE_REMOVED_IN_SHADOW", "drawdown": 0.1},
        reasonCodes=["synthetic.v2_replay"],
    )


def strategy_output(
    strategy_id: str,
    strategy_name: str,
    family: str,
    signal: Signal,
    data_ready: bool,
) -> dict:
    direction = 1 if signal == Signal.BUY else (-1 if signal == Signal.SELL else 0)
    return {
        "strategyId": strategy_id,
        "strategyName": strategy_name,
        "family": family,
        "signal": signal.value,
        "direction": direction,
        "eligible": data_ready,
        "dataReady": data_ready,
        "features": {"setupId": f"{strategy_id}-{signal.value.lower()}"} if signal != Signal.HOLD else {},
    }


if __name__ == "__main__":
    unittest.main()
