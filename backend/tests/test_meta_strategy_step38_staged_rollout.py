from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_LIVE_ROLLOUT_STAGES,
    META_STRATEGY_ROLLOUT_STAGES,
    MetaStrategyManualApproval,
    advance_meta_strategy_rollout,
    artifact_hash,
    initial_meta_strategy_rollout_state,
    rollback_meta_strategy_rollout,
)
from backend.tests.test_meta_strategy_step37_promotion_evidence import candidate_artifact, valid_evidence


ROOT = Path(__file__).resolve().parents[2]
ROLLOUT_PATH = ROOT / "backend" / "app" / "algorithms" / "meta_strategy" / "promotion" / "rollout.py"
NOW = datetime(2026, 1, 13, 15, 30, tzinfo=UTC)


class MetaStrategyStep38StagedRolloutTest(unittest.TestCase):
    def test_rollout_file_exists_and_declares_only_limited_live_stage(self) -> None:
        self.assertTrue(ROLLOUT_PATH.is_file())
        self.assertEqual(
            META_STRATEGY_ROLLOUT_STAGES,
            ("OFF", "RESEARCH", "SHADOW", "PAPER_FILTER", "PAPER_RISK_REDUCTION", "LIMITED_LIVE_FILTER"),
        )
        self.assertEqual(META_STRATEGY_LIVE_ROLLOUT_STAGES, ("LIMITED_LIVE_FILTER",))
        self.assertNotIn("LIVE", META_STRATEGY_ROLLOUT_STAGES)
        self.assertNotIn("UNRESTRICTED_LIVE", META_STRATEGY_ROLLOUT_STAGES)

    def test_rollout_advances_sequentially_through_research_and_shadow_without_live_control(self) -> None:
        artifact = candidate_artifact()
        state = initial_meta_strategy_rollout_state(checked_at=NOW)

        research = advance_meta_strategy_rollout(state, target_stage="RESEARCH", candidate_artifact=artifact, checked_at=NOW)
        shadow = advance_meta_strategy_rollout(research.state, target_stage="SHADOW", candidate_artifact=artifact, checked_at=NOW)

        self.assertTrue(research.approved)
        self.assertEqual(research.state.stage, "RESEARCH")
        self.assertTrue(shadow.approved)
        self.assertEqual(shadow.state.stage, "SHADOW")
        self.assertEqual(shadow.state.artifact_id, artifact["artifactId"])

    def test_promotion_beyond_shadow_requires_walk_forward_holdout_and_paper_stability(self) -> None:
        artifact = candidate_artifact()
        shadow_state = state_at_shadow(artifact)

        no_evidence = advance_meta_strategy_rollout(shadow_state, target_stage="PAPER_FILTER", candidate_artifact=artifact, checked_at=NOW)
        bad_evidence = valid_evidence(artifact, paper_stability={"stable": False, "paperSessions": 0})
        bad_paper = advance_meta_strategy_rollout(
            shadow_state,
            target_stage="PAPER_FILTER",
            candidate_artifact=artifact,
            evidence=bad_evidence,
            checked_at=NOW,
        )

        self.assertFalse(no_evidence.approved)
        self.assertIn("meta_strategy.rollout.promotion_evidence_required_beyond_shadow", no_evidence.reason_codes)
        self.assertFalse(bad_paper.approved)
        self.assertIn("meta_strategy.rollout.paper_stability_required_beyond_shadow", bad_paper.reason_codes)

        good = advance_meta_strategy_rollout(
            shadow_state,
            target_stage="PAPER_FILTER",
            candidate_artifact=artifact,
            evidence=valid_evidence(artifact),
            checked_at=NOW,
        )
        self.assertTrue(good.approved)
        self.assertEqual(good.state.stage, "PAPER_FILTER")

    def test_live_stage_requires_manual_approval_and_never_enables_unrestricted_live(self) -> None:
        artifact = candidate_artifact()
        paper_risk_state = state_at_paper_risk_reduction(artifact)

        rejected = advance_meta_strategy_rollout(
            paper_risk_state,
            target_stage="LIMITED_LIVE_FILTER",
            candidate_artifact=artifact,
            evidence=valid_evidence(artifact),
            checked_at=NOW,
        )
        unrestricted = advance_meta_strategy_rollout(
            paper_risk_state,
            target_stage="LIVE",
            candidate_artifact=artifact,
            evidence=valid_evidence(artifact),
            manual_approval=manual_approval(),
            checked_at=NOW,
        )
        approved = advance_meta_strategy_rollout(
            paper_risk_state,
            target_stage="LIMITED_LIVE_FILTER",
            candidate_artifact=artifact,
            evidence=valid_evidence(artifact),
            manual_approval=manual_approval(),
            checked_at=NOW,
        )

        self.assertFalse(rejected.approved)
        self.assertIn("meta_strategy.rollout.manual_approval_required_for_live", rejected.reason_codes)
        self.assertFalse(unrestricted.approved)
        self.assertIn("meta_strategy.rollout.unrestricted_live_not_supported", unrestricted.reason_codes)
        self.assertTrue(approved.approved)
        self.assertEqual(approved.state.stage, "LIMITED_LIVE_FILTER")
        self.assertEqual(approved.state.manual_approval_id, "approval-step38")

    def test_rollback_restores_previous_artifact_and_mode(self) -> None:
        first = candidate_artifact()
        second_base = {**candidate_artifact(), "artifactId": "meta-strategy-candidate-artifact-38b"}
        second = {**second_base, "artifactHash": artifact_hash(second_base)}
        paper_filter = state_at_paper_filter(first)
        advanced = advance_meta_strategy_rollout(
            paper_filter,
            target_stage="PAPER_RISK_REDUCTION",
            candidate_artifact=second,
            evidence=valid_evidence(second),
            checked_at=NOW,
        )

        rollback = rollback_meta_strategy_rollout(advanced.state, checked_at=NOW)

        self.assertTrue(advanced.approved)
        self.assertEqual(advanced.state.stage, "PAPER_RISK_REDUCTION")
        self.assertEqual(advanced.state.artifact_id, second["artifactId"])
        self.assertTrue(rollback.approved)
        self.assertEqual(rollback.action, "ROLLBACK")
        self.assertEqual(rollback.state.stage, "PAPER_FILTER")
        self.assertEqual(rollback.state.artifact_id, first["artifactId"])
        self.assertEqual(rollback.state.artifact_hash, first["artifactHash"])


def state_at_shadow(artifact: dict):
    state = initial_meta_strategy_rollout_state(checked_at=NOW)
    state = advance_meta_strategy_rollout(state, target_stage="RESEARCH", candidate_artifact=artifact, checked_at=NOW).state
    return advance_meta_strategy_rollout(state, target_stage="SHADOW", candidate_artifact=artifact, checked_at=NOW).state


def state_at_paper_filter(artifact: dict):
    return advance_meta_strategy_rollout(
        state_at_shadow(artifact),
        target_stage="PAPER_FILTER",
        candidate_artifact=artifact,
        evidence=valid_evidence(artifact),
        checked_at=NOW,
    ).state


def state_at_paper_risk_reduction(artifact: dict):
    return advance_meta_strategy_rollout(
        state_at_paper_filter(artifact),
        target_stage="PAPER_RISK_REDUCTION",
        candidate_artifact=artifact,
        evidence=valid_evidence(artifact),
        checked_at=NOW,
    ).state


def manual_approval() -> MetaStrategyManualApproval:
    return MetaStrategyManualApproval(
        approval_id="approval-step38",
        approved_by="risk-manager",
        approved_at=NOW,
    )


if __name__ == "__main__":
    unittest.main()
