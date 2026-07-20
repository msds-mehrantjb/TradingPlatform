from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from backend.app.algorithms.meta_strategy.training.training_core import (
    MetaTrainingConfig,
    build_nested_walk_forward_plan,
    train_meta_strategy_baselines,
    training_example,
)


START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def labeled_row(index: int) -> dict:
    timestamp = START + timedelta(minutes=index * 5)
    label = ["BUY", "SELL", "HOLD", "BUY", "SELL"][index % 5]
    session_index = index // 20
    regime = ["range", "weak_trend", "strong_trend"][session_index % 3]
    buy_score = 0.7 if label == "BUY" else 0.1
    sell_score = 0.7 if label == "SELL" else 0.1
    hold_score = 0.8 if label == "HOLD" else 0.2
    return {
        "snapshotId": f"snapshot-{index}",
        "capturedAt": timestamp.isoformat(),
        "decisionTimestampUtc": timestamp.isoformat(),
        "labelStart": timestamp.isoformat(),
        "labelEnd": (timestamp + timedelta(minutes=8)).isoformat(),
        "sessionDate": (START.date() + timedelta(days=session_index)).isoformat(),
        "trainingLabel": label,
        "validationLabel": label,
        "costAdjustedTrainingLabel": 0 if label == "HOLD" else 1,
        "regimeState": {"label": regime},
        "familyScores": {
            "meta": {
                "trend_buy_score": buy_score,
                "trend_sell_score": sell_score,
                "trend_hold_score": hold_score,
                "breakout_buy_score": buy_score * 0.8,
                "breakout_sell_score": sell_score * 0.8,
            }
        },
        "metaModelFeatures": {
            "familyAggregation": {
                "trend_buy_score": buy_score,
                "trend_sell_score": sell_score,
                "breakout_buy_score": buy_score * 0.8,
                "breakout_sell_score": sell_score * 0.8,
                "regime_score": 0.4 if regime != "range" else 0.0,
            },
            "marketRegime": {"trend": regime},
            "relativeVolume": 1.0 + (index % 7) * 0.1,
        },
        "finalDecision": {
            "voting": {"signal": label},
            "weighted": {"signal": label},
            "confidence": {"signal": label},
            "regime": {"signal": label},
            "meta": {"signal": label},
        },
        "barriers": {"targetDistance": 1.0, "stopDistance": 0.7},
        "entry": {"spread": 0.02, "slippage": 0.01},
    }


def examples(count: int = 180) -> list[dict]:
    return [training_example(labeled_row(index), maximum_holding_horizon_minutes=10) for index in range(count)]


class NestedMetaStrategyTrainingTest(unittest.TestCase):
    def test_outer_folds_are_chronological_purged_and_embargoed(self) -> None:
        config = MetaTrainingConfig(
            minimumTotalCandidates=120,
            minimumBuyCandidates=20,
            minimumSellCandidates=20,
            minimumPositiveOutcomes=40,
            minimumNegativeOutcomes=20,
            minimumCandidatesPerOuterFold=12,
            minimumTradingSessions=4,
            minimumRegimesRepresented=2,
            outerFolds=3,
            innerFolds=2,
            maximumHoldingHorizonMinutes=10,
            embargoMinutes=10,
        ).normalized()
        plan = build_nested_walk_forward_plan(examples(), config)

        self.assertTrue(plan["sufficient"])
        self.assertGreater(len(plan["outerFolds"]), 0)
        self.assertGreater(plan["report"]["finalTestRows"], 0)
        for fold in plan["outerFolds"]:
            validation_start = fold["validationRows"][0]["timestamp"]
            cutoff = fold["labelWindowCutoff"]
            self.assertGreaterEqual(fold["embargoMinutes"], config.maximumHoldingHorizonMinutes)
            self.assertGreater(fold["purgedRows"], 0)
            self.assertTrue(all(row["timestamp"] < validation_start for row in fold["trainRows"]))
            self.assertTrue(all(row["labelEnd"] < cutoff for row in fold["trainRows"]))
        self.assertLess(plan["developmentRows"][-1]["timestamp"], plan["finalTestRows"][0]["timestamp"])

    def test_insufficient_data_returns_untrusted_without_tiny_holdout_trust(self) -> None:
        with patched_training_io([labeled_row(index) for index in range(12)]):
            result = train_meta_strategy_baselines(
                decision_snapshot_dir=Path("unused"),
                symbol="SPY",
                minimum_total_candidates=60,
                minimum_buy_candidates=10,
                minimum_sell_candidates=10,
                minimum_positive_outcomes=20,
                minimum_negative_outcomes=10,
                minimum_candidates_per_outer_fold=8,
                minimum_trading_sessions=3,
                minimum_regimes_represented=2,
                outer_folds=2,
                inner_folds=2,
                maximum_holding_horizon_minutes=10,
                embargo_minutes=10,
            )

            self.assertEqual(result["status"], "insufficient_data")
            self.assertFalse(result["trusted"])
            self.assertIn("minimumRequirements", result)

    def test_training_report_uses_nested_walk_forward_and_untouched_final_holdout(self) -> None:
        with patched_training_io([labeled_row(index) for index in range(180)]):
            result = train_meta_strategy_baselines(
                decision_snapshot_dir=Path("unused"),
                symbol="SPY",
                minimum_total_candidates=120,
                minimum_buy_candidates=20,
                minimum_sell_candidates=20,
                minimum_positive_outcomes=40,
                minimum_negative_outcomes=20,
                minimum_candidates_per_outer_fold=12,
                minimum_trading_sessions=4,
                minimum_regimes_represented=2,
                outer_folds=2,
                inner_folds=2,
                maximum_holding_horizon_minutes=10,
                embargo_minutes=10,
            )

            self.assertEqual(result["status"], "ready")
            self.assertIn("artifactPath", result)
            self.assertEqual(result["validationPolicy"]["method"], "nested_chronological_purged_walk_forward")
            self.assertGreaterEqual(result["validationPolicy"]["embargoMinutes"], 10)
            self.assertIn("untouched", result["validationPolicy"]["finalHoldoutPolicy"])
            self.assertGreaterEqual(result["metrics"]["outerWalkForward"]["validatedFolds"], 1)
            model = result["models"]["logistic_regression_nested_calibrated"]
            self.assertEqual(model["calibration"]["source"], "inner_out_of_fold")
            self.assertIn("reliabilityCurve", model["calibration"]["metrics"])
            self.assertIn("probabilitySizingApproved", result["metrics"])
            self.assertFalse(result["trusted"] and result["finalTestRows"] < 12)


class FakeArtifactPath:
    def __init__(self) -> None:
        self.parent = self
        self.payload = ""

    def mkdir(self, parents: bool = False, exist_ok: bool = False) -> None:
        return None

    def write_text(self, payload: str, encoding: str = "utf-8") -> int:
        self.payload = payload
        return len(payload)

    def __str__(self) -> str:
        return "fake_meta_strategy_artifact.json"


class patched_training_io:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.artifact_path = FakeArtifactPath()
        self.patches = [
            patch("backend.app.algorithms.meta_strategy.training.training_core.load_labeled_rows", return_value=rows),
            patch("backend.app.algorithms.meta_strategy.training.training_core.save_latest_training_status", side_effect=lambda _root, result: result),
            patch("backend.app.algorithms.meta_strategy.training.training_core.meta_strategy_artifact_path", return_value=self.artifact_path),
        ]

    def __enter__(self):
        for item in self.patches:
            item.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        for item in reversed(self.patches):
            item.__exit__(exc_type, exc, tb)
        return False


if __name__ == "__main__":
    unittest.main()
