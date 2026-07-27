from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.algorithms.meta_strategy import (
    ALGORITHM_ID,
    META_STRATEGY_FINAL_DOD_IDS,
    META_STRATEGY_FINAL_ACCEPTANCE_VERSION,
    META_STRATEGY_RECOVERY_TEST_IDS,
    build_meta_strategy_final_acceptance_report,
    meta_strategy_acceptance_is_complete,
)
from backend.app.algorithms.meta_strategy.service import MetaStrategyApplicationService


ROOT = Path(__file__).resolve().parents[2]

class MetaStrategyStep47FinalAcceptanceTest(unittest.TestCase):
    maxDiff = None

    def test_final_acceptance_module_exists_and_declares_evidence_requirements(self) -> None:
        self.assertTrue((ROOT / "backend/app/algorithms/meta_strategy/final_acceptance.py").is_file())
        self.assertEqual(len(META_STRATEGY_RECOVERY_TEST_IDS), 13)
        self.assertEqual(len(META_STRATEGY_FINAL_DOD_IDS), 18)
        self.assertIn("live_execution_disabled", META_STRATEGY_FINAL_DOD_IDS)

    def test_acceptance_is_blocked_without_required_evidence(self) -> None:
        report = build_meta_strategy_final_acceptance_report()

        self.assertEqual(report["algorithmId"], ALGORITHM_ID)
        self.assertEqual(report["version"], META_STRATEGY_FINAL_ACCEPTANCE_VERSION)
        self.assertFalse(report["complete"])
        self.assertGreater(report["counts"]["FAILED"], 0)
        self.assertTrue(report["blockingStatements"])
        self.assertIn("failedControls", report)
        for item in report["items"]:
            self.assertTrue(item["requiredForCompletion"])

        self.assertFalse(meta_strategy_acceptance_is_complete())

    def test_acceptance_reports_evidence_categories(self) -> None:
        report = build_meta_strategy_final_acceptance_report()
        categories = {item["category"] for item in report["items"]}
        self.assertIn("Recovery", categories)
        self.assertIn("Definition of done", categories)
        self.assertIn("Operational evidence", categories)

    def test_live_execution_remains_disabled_until_separately_approved(self) -> None:
        report = build_meta_strategy_final_acceptance_report()
        live_item = next(item for item in report["items"] if item["itemId"] == "live_disabled")

        self.assertFalse(report["liveExecutionEnabled"])
        self.assertTrue(report["liveExecutionApprovalRequired"])
        self.assertEqual(live_item["status"], "PASSED")

    def test_service_returns_final_acceptance_ledger(self) -> None:
        response = MetaStrategyApplicationService().final_acceptance()

        self.assertEqual(response["algorithmId"], ALGORITHM_ID)
        self.assertEqual(response["operation"], "final_acceptance")
        self.assertEqual(response["status"], "REJECTED")
        self.assertFalse(response["payload"]["complete"])
        self.assertIn("failedControls", response["payload"])


if __name__ == "__main__":
    unittest.main()
