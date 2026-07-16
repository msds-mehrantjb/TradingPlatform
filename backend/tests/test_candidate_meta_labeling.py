from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.domain.feature_engine import MarketCandle
from backend.app.domain.models import OrderPlan, Signal
from backend.app.ml.meta_labeling import META_LABEL_VERSION, MetaLabelExecutionConfig, create_candidate_meta_label
from backend.tests.test_decision_snapshot_v2_archive import CONFIG_HASH, NOW, ensemble, snapshot


def candle_at(minute: int, *, open_price: float, high: float, low: float, close: float) -> MarketCandle:
    return MarketCandle(
        timestamp=NOW + timedelta(minutes=minute),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=100000,
        tradeCount=1000,
        symbol="SPY",
        timeframe="1Min",
    )


def order_plan(side: Signal, *, order_id: str = "order-1") -> OrderPlan:
    return OrderPlan(
        orderPlanId=order_id,
        candidateId=f"candidate-{side.value.lower()}",
        symbol="SPY",
        side=side,
        orderType="STOP_LIMIT",
        quantity=10,
        entryPrice=100,
        stopPrice=99 if side == Signal.BUY else 101,
        targetPrice=102 if side == Signal.BUY else 98,
        limitPrice=100.02,
        timeInForce="DAY",
        eligible=True,
        explanation="Synthetic order plan for candidate meta-label tests.",
        generatedAt=NOW,
        sessionDate=NOW.date(),
        configurationHash=CONFIG_HASH,
    )


def config() -> MetaLabelExecutionConfig:
    return MetaLabelExecutionConfig(
        maxHoldingPeriodMinutes=5,
        spreadDollars=0.02,
        slippagePerShare=0.01,
        feesPerShare=0.005,
        flatFeePerOrder=0.10,
        configurationHash="meta-label-config-a",
    )


class CandidateMetaLabelingTest(unittest.TestCase):
    def test_buy_candidate_target_before_stop_is_successful_after_costs(self) -> None:
        label = create_candidate_meta_label(
            snapshot(ensembleDecision=ensemble(Signal.BUY), orderPlan=order_plan(Signal.BUY)),
            [
                candle_at(1, open_price=100, high=100.5, low=99.8, close=100.2),
                candle_at(2, open_price=100.2, high=102.2, low=100.1, close=102.0),
            ],
            config(),
        )

        self.assertEqual(label.labelVersion, META_LABEL_VERSION)
        self.assertEqual(label.candidateSide, Signal.BUY.value)
        self.assertEqual(label.firstBarrierHit, "TARGET")
        self.assertEqual(label.strictOutcomeLabel, 1)
        self.assertEqual(label.costAdjustedTrainingLabel, 1)
        self.assertGreater(label.entryTimestampUtc, NOW)
        self.assertAlmostEqual(label.entryPrice, 100.02)
        self.assertIn("profit target", label.barrierExplanation)

    def test_sell_candidate_uses_side_correct_profit_and_stop_barriers(self) -> None:
        label = create_candidate_meta_label(
            snapshot(ensembleDecision=ensemble(Signal.SELL), orderPlan=order_plan(Signal.SELL)),
            [
                candle_at(1, open_price=100, high=100.2, low=99.6, close=99.8),
                candle_at(2, open_price=99.8, high=99.9, low=97.8, close=98.1),
            ],
            config(),
        )

        self.assertEqual(label.candidateSide, Signal.SELL.value)
        self.assertEqual(label.upperBarrierPrice, 98)
        self.assertEqual(label.lowerBarrierPrice, 101)
        self.assertEqual(label.firstBarrierHit, "TARGET")
        self.assertEqual(label.strictOutcomeLabel, 1)
        self.assertEqual(label.costAdjustedTrainingLabel, 1)
        self.assertGreater(label.netPnlAfterCosts or 0, 0)

    def test_hold_snapshot_is_diagnostic_not_failed_training_example(self) -> None:
        label = create_candidate_meta_label(snapshot(ensembleDecision=ensemble(Signal.HOLD)), [], config())

        self.assertFalse(label.eligibleForTraining)
        self.assertEqual(label.firstBarrierHit, "NO_CANDIDATE")
        self.assertIsNone(label.strictOutcomeLabel)
        self.assertIsNone(label.costAdjustedTrainingLabel)
        self.assertIn("hold_snapshot_diagnostic_only", label.reasonCodes)

    def test_decision_timestamp_candle_cannot_be_used_as_entry_or_barrier(self) -> None:
        decision_candle = MarketCandle(
            timestamp=NOW,
            open=100,
            high=103,
            low=97,
            close=102,
            volume=100000,
            tradeCount=1000,
            symbol="SPY",
            timeframe="1Min",
        )
        label = create_candidate_meta_label(
            snapshot(ensembleDecision=ensemble(Signal.BUY), orderPlan=order_plan(Signal.BUY)),
            [
                decision_candle,
                candle_at(1, open_price=100, high=100.2, low=98.8, close=99.0),
            ],
            config(),
        )

        self.assertEqual(label.entryTimestampUtc, NOW + timedelta(minutes=1))
        self.assertEqual(label.firstBarrierHit, "STOP")
        self.assertEqual(label.strictOutcomeLabel, 0)
        self.assertEqual(label.costAdjustedTrainingLabel, 0)

    def test_candidate_meta_label_rejects_entry_at_or_before_decision(self) -> None:
        from pydantic import ValidationError
        from backend.app.domain.models import CandidateMetaLabel

        with self.assertRaisesRegex(ValidationError, "after the decision timestamp"):
            CandidateMetaLabel(
                labelId="invalid-label",
                snapshotId="snapshot-1",
                symbol="SPY",
                candidateSide=Signal.BUY,
                decisionTimestampUtc=NOW,
                sessionDateNewYork=NOW.date(),
                entryTimestampUtc=datetime(2026, 1, 5, 15, 45, tzinfo=UTC),
                entryPrice=100,
                profitTargetPrice=102,
                protectiveStopPrice=99,
                upperBarrierPrice=102,
                lowerBarrierPrice=99,
                verticalBarrierTimestampUtc=NOW + timedelta(minutes=5),
                firstBarrierHit="TARGET",
                firstBarrierTimestampUtc=NOW + timedelta(minutes=1),
                exitPrice=102,
                strictOutcomeLabel=1,
                costAdjustedTrainingLabel=1,
                quantity=10,
                orderFillBehavior="next_open_after_latency",
                barrierExplanation="Invalid timestamp fixture.",
                eligibleForTraining=True,
                createdAt=NOW,
                configurationHash=CONFIG_HASH,
            )


if __name__ == "__main__":
    unittest.main()
