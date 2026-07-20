from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_TRIPLE_BARRIER_LABEL_VERSION,
    MetaStrategyDatasetValidationError,
    MetaStrategyLabelCandle,
    MetaStrategyLabelExecutionConfig,
    build_labeled_dataset_row,
    build_meta_strategy_features_from_characterization_fixture,
    create_triple_barrier_label,
    validate_feature_row_for_labeling,
)


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
FIXTURE_PATH = Path("backend/tests/fixtures/meta_strategy_current_behavior.json")


def label_config(**overrides) -> MetaStrategyLabelExecutionConfig:
    values = {
        "maxHoldingPeriodMinutes": 5,
        "spreadDollars": 0.02,
        "slippagePerShare": 0.01,
        "feesPerShare": 0.005,
        "flatFeePerOrder": 0.10,
        "configurationHash": "meta-strategy-characterization-label-v1",
    }
    values.update(overrides)
    return MetaStrategyLabelExecutionConfig(**values)


def candle_at(minute: int, *, open_price: float, high: float, low: float, close: float) -> MetaStrategyLabelCandle:
    return MetaStrategyLabelCandle(
        timestamp=NOW + timedelta(minutes=minute),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=100000,
    )


def future_candles_for_legacy_label(side: str, first_barrier_hit: str) -> tuple[MetaStrategyLabelCandle, ...]:
    if side == "HOLD":
        return (candle_at(1, open_price=100.0, high=100.1, low=99.9, close=100.0),)
    if first_barrier_hit == "TARGET" and side == "BUY":
        return (
            candle_at(1, open_price=100.0, high=100.8, low=99.8, close=100.4),
            candle_at(2, open_price=100.4, high=102.3, low=100.2, close=102.0),
        )
    if first_barrier_hit == "TARGET" and side == "SELL":
        return (
            candle_at(1, open_price=100.0, high=100.2, low=99.6, close=99.8),
            candle_at(2, open_price=99.8, high=99.9, low=97.8, close=98.1),
        )
    if first_barrier_hit == "STOP" and side == "BUY":
        return (candle_at(1, open_price=100.0, high=100.2, low=98.8, close=99.0),)
    return (candle_at(1, open_price=100.0, high=100.1, low=99.9, close=100.0),)


class MetaStrategyStep17LabelingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = json.loads(FIXTURE_PATH.read_text())["fixtures"]

    def test_triple_barrier_labels_match_legacy_characterization_fixtures(self) -> None:
        for fixture in self.fixtures:
            with self.subTest(fixture=fixture["id"]):
                legacy_label = fixture["label"]
                label = create_triple_barrier_label(
                    decision_id=fixture["deterministicCandidate"].get("decisionId", f"{fixture['id']}-decision"),
                    snapshot_id=f"{fixture['id']}-snapshot",
                    symbol="SPY",
                    decision_timestamp_utc=NOW,
                    candidate_side=legacy_label["candidateSide"],
                    geometry=fixture.get("candidateGeometry"),
                    future_candles=future_candles_for_legacy_label(legacy_label["candidateSide"], legacy_label["firstBarrierHit"]),
                    config=label_config(),
                )

                self.assertEqual(label.labelVersion, META_STRATEGY_TRIPLE_BARRIER_LABEL_VERSION)
                self.assertEqual(label.labelEndTimestampUtc, label.metaLabel.timestamp)
                self.assertIsNotNone(label.labelEndTimestampUtc)
                for key, expected_value in legacy_label.items():
                    self.assertEqual(label.legacy_fixture_summary()[key], expected_value, key)

    def test_latency_uses_next_executable_entry_and_not_decision_timestamp_candle(self) -> None:
        label = create_triple_barrier_label(
            decision_id="decision-latency",
            snapshot_id="snapshot-latency",
            symbol="SPY",
            decision_timestamp_utc=NOW,
            candidate_side="BUY",
            geometry={"candidateId": "candidate-buy", "quantity": 10, "stopPrice": 99.0, "targetPrice": 102.0, "eligible": True},
            future_candles=(
                MetaStrategyLabelCandle(timestamp=NOW, open=100.0, high=103.0, low=97.0, close=102.0),
                candle_at(1, open_price=100.0, high=100.2, low=98.8, close=99.0),
            ),
            config=label_config(latencyMilliseconds=0),
        )

        self.assertEqual(label.entryTimestampUtc, NOW + timedelta(minutes=1))
        self.assertEqual(label.firstBarrierHit, "STOP")
        self.assertEqual(label.costAdjustedTrainingLabel, 0)
        self.assertEqual(label.labelEndTimestampUtc, NOW + timedelta(minutes=1))

    def test_same_bar_ambiguity_is_controlled_by_explicit_policy(self) -> None:
        geometry = {"candidateId": "candidate-buy", "quantity": 10, "stopPrice": 99.0, "targetPrice": 102.0, "eligible": True}
        ambiguous_candle = (candle_at(1, open_price=100.0, high=102.5, low=98.5, close=100.0),)

        stop_first = create_triple_barrier_label(
            decision_id="decision-same-bar-stop",
            snapshot_id="snapshot-same-bar-stop",
            symbol="SPY",
            decision_timestamp_utc=NOW,
            candidate_side="BUY",
            geometry=geometry,
            future_candles=ambiguous_candle,
            config=label_config(sameBarAmbiguityPolicy="stop_first"),
        )
        target_first = create_triple_barrier_label(
            decision_id="decision-same-bar-target",
            snapshot_id="snapshot-same-bar-target",
            symbol="SPY",
            decision_timestamp_utc=NOW,
            candidate_side="BUY",
            geometry=geometry,
            future_candles=ambiguous_candle,
            config=label_config(sameBarAmbiguityPolicy="target_first"),
        )
        excluded = create_triple_barrier_label(
            decision_id="decision-same-bar-exclude",
            snapshot_id="snapshot-same-bar-exclude",
            symbol="SPY",
            decision_timestamp_utc=NOW,
            candidate_side="BUY",
            geometry=geometry,
            future_candles=ambiguous_candle,
            config=label_config(sameBarAmbiguityPolicy="exclude_from_training"),
        )

        self.assertEqual(stop_first.firstBarrierHit, "STOP")
        self.assertTrue(stop_first.ambiguous)
        self.assertEqual(target_first.firstBarrierHit, "TARGET")
        self.assertTrue(target_first.ambiguous)
        self.assertEqual(excluded.firstBarrierHit, "AMBIGUOUS")
        self.assertFalse(excluded.eligibleForTraining)
        self.assertIn("same_bar_ambiguity_excluded", excluded.reasonCodes)
        self.assertEqual(excluded.labelEndTimestampUtc, NOW + timedelta(minutes=1))

    def test_gap_through_stop_uses_open_as_exit_reference(self) -> None:
        label = create_triple_barrier_label(
            decision_id="decision-gap-stop",
            snapshot_id="snapshot-gap-stop",
            symbol="SPY",
            decision_timestamp_utc=NOW,
            candidate_side="BUY",
            geometry={"candidateId": "candidate-buy", "quantity": 10, "stopPrice": 99.0, "targetPrice": 102.0, "eligible": True},
            future_candles=(
                candle_at(1, open_price=100.0, high=100.4, low=99.4, close=99.8),
                candle_at(2, open_price=98.5, high=98.8, low=97.9, close=98.2),
            ),
            config=label_config(),
        )

        self.assertEqual(label.firstBarrierHit, "STOP")
        self.assertTrue(label.gapThroughStop)
        self.assertEqual(label.labelEndTimestampUtc, NOW + timedelta(minutes=2))
        self.assertAlmostEqual(label.exitPrice or 0.0, 98.48)
        self.assertLess(label.netPnlAfterCosts or 0.0, 0.0)

    def test_labeled_dataset_keeps_future_outcome_fields_out_of_feature_row(self) -> None:
        fixture = next(item for item in self.fixtures if item["id"] == "trending_market")
        feature_set = build_meta_strategy_features_from_characterization_fixture(fixture)
        label = create_triple_barrier_label(
            decision_id=fixture["deterministicCandidate"]["decisionId"],
            snapshot_id=f"{fixture['id']}-snapshot",
            symbol="SPY",
            decision_timestamp_utc=NOW,
            candidate_side="BUY",
            geometry=fixture["candidateGeometry"],
            future_candles=future_candles_for_legacy_label("BUY", "TARGET"),
            config=label_config(),
        )

        row = build_labeled_dataset_row(feature_set=feature_set, execution_label=label)

        self.assertNotIn("costAdjustedTrainingLabel", row.featureValues)
        self.assertEqual(row.labelValues["costAdjustedTrainingLabel"], 1)
        self.assertEqual(row.lineage.labelEndTimestampUtc, label.labelEndTimestampUtc)
        self.assertIn("meta_strategy.dataset.feature_row_point_in_time", row.validationReasonCodes)

    def test_dataset_validation_rejects_future_fields_and_foreign_algorithm_rows(self) -> None:
        with self.assertRaisesRegex(MetaStrategyDatasetValidationError, "future/label fields"):
            validate_feature_row_for_labeling({"algorithmId": "meta_strategy", "futureHigh": 105.0})

        with self.assertRaisesRegex(MetaStrategyDatasetValidationError, "rejects row from algorithm"):
            validate_feature_row_for_labeling({"algorithmId": "weighted_voting", "feature": 1.0})


if __name__ == "__main__":
    unittest.main()
