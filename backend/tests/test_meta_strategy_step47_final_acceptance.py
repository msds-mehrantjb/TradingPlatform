from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.algorithms.meta_strategy import (
    ALGORITHM_ID,
    META_STRATEGY_FINAL_ACCEPTANCE_ITEMS,
    META_STRATEGY_FINAL_ACCEPTANCE_VERSION,
    build_meta_strategy_final_acceptance_report,
    meta_strategy_acceptance_is_complete,
)
from backend.app.algorithms.meta_strategy.service import MetaStrategyApplicationService


ROOT = Path(__file__).resolve().parents[2]

MANDATORY_ACCEPTANCE_STATEMENTS = (
    "Dedicated package exists.",
    "Backend is authoritative.",
    "Strategies are dedicated.",
    "Candidate generation is dedicated.",
    "Features and labels are dedicated.",
    "Training is dedicated.",
    "Artifacts are dedicated.",
    "Inference is candidate-conditional.",
    "ML cannot create or reverse trades.",
    "ML cannot increase risk.",
    "Dynamic settings are dedicated.",
    "Position sizing is dedicated.",
    "Trade management is dedicated.",
    "Persistence is dedicated.",
    "Backtesting is dedicated.",
    "Runtime and backtest use one pipeline.",
    "Promotion requires walk-forward, holdout and paper stability.",
    "Shared services preserve algorithm attribution.",
    "Cross-algorithm state access is prohibited.",
    "Legacy authority has been deleted.",
    "Dedicated tests pass.",
    "Runtime-parity tests pass.",
    "No mandatory tests are skipped.",
    "Live execution remains disabled until separately approved.",
)


class MetaStrategyStep47FinalAcceptanceTest(unittest.TestCase):
    maxDiff = None

    def test_final_acceptance_module_exists_and_covers_every_mandatory_item(self) -> None:
        self.assertTrue((ROOT / "backend/app/algorithms/meta_strategy/final_acceptance.py").is_file())
        self.assertEqual(len(META_STRATEGY_FINAL_ACCEPTANCE_ITEMS), 24)
        self.assertEqual(
            tuple(item.statement for item in META_STRATEGY_FINAL_ACCEPTANCE_ITEMS),
            MANDATORY_ACCEPTANCE_STATEMENTS,
        )

    def test_every_mandatory_acceptance_item_reports_passed(self) -> None:
        report = build_meta_strategy_final_acceptance_report()

        self.assertEqual(report["algorithmId"], ALGORITHM_ID)
        self.assertEqual(report["version"], META_STRATEGY_FINAL_ACCEPTANCE_VERSION)
        self.assertTrue(report["complete"])
        self.assertEqual(report["counts"], {"PASSED": 24, "FAILED": 0})
        self.assertEqual(report["blockingStatements"], [])
        for item in report["items"]:
            with self.subTest(item=item["itemId"]):
                self.assertTrue(item["requiredForCompletion"])
                self.assertEqual(item["status"], "PASSED")

        self.assertTrue(meta_strategy_acceptance_is_complete())

    def test_every_acceptance_evidence_path_exists(self) -> None:
        missing: list[str] = []
        for item in META_STRATEGY_FINAL_ACCEPTANCE_ITEMS:
            for evidence in item.evidence:
                if not (ROOT / evidence).exists():
                    missing.append(f"{item.item_id}: {evidence}")

        self.assertEqual(missing, [])

    def test_live_execution_remains_disabled_until_separately_approved(self) -> None:
        report = build_meta_strategy_final_acceptance_report()
        live_item = next(item for item in report["items"] if item["itemId"] == "live_execution_disabled_until_separately_approved")

        self.assertFalse(report["liveExecutionEnabled"])
        self.assertTrue(report["liveExecutionApprovalRequired"])
        self.assertEqual(live_item["status"], "PASSED")

    def test_service_returns_final_acceptance_ledger(self) -> None:
        response = MetaStrategyApplicationService().final_acceptance()

        self.assertEqual(response["algorithmId"], ALGORITHM_ID)
        self.assertEqual(response["operation"], "final_acceptance")
        self.assertEqual(response["status"], "OK")
        self.assertTrue(response["payload"]["complete"])
        self.assertEqual(response["payload"]["counts"]["PASSED"], 24)


if __name__ == "__main__":
    unittest.main()
