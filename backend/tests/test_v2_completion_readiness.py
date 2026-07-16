from __future__ import annotations

import unittest

from backend.app.validation import build_v2_completion_readiness_report
from backend.app.validation.v2_readiness import COMPLETION_CONDITIONS, V2_READINESS_VERSION


class V2CompletionReadinessTest(unittest.TestCase):
    def test_user_definition_of_done_has_executable_evidence_for_every_condition(self) -> None:
        report = build_v2_completion_readiness_report()

        self.assertEqual(report.readinessVersion, V2_READINESS_VERSION)
        self.assertTrue(report.complete)
        self.assertEqual(report.failedCount, 0)
        self.assertEqual(report.passedCount, len(COMPLETION_CONDITIONS))
        self.assertEqual(len(report.conditions), 40)
        self.assertTrue(all(condition.passed for condition in report.conditions))
        self.assertTrue(all(not condition.missingEvidence for condition in report.conditions))

    def test_readiness_gate_covers_core_safety_and_rollout_contracts(self) -> None:
        report = build_v2_completion_readiness_report()
        condition_ids = {condition.conditionId for condition in report.conditions}

        for condition_id in (
            "directional_10",
            "no_direction_proxy",
            "point_in_time_features",
            "v1_v2_not_mixed",
            "hard_risk_no_lookahead_tests",
            "stage_rollback",
            "no_live_trading",
        ):
            with self.subTest(condition_id=condition_id):
                self.assertIn(condition_id, condition_ids)


if __name__ == "__main__":
    unittest.main()
