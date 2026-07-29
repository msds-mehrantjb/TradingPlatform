from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.app.algorithms.weighted_voting.service import WeightedVotingService
from backend.app.algorithms.weighted_voting.strategy_lifecycle import (
    WEIGHTED_VOTING_LIFECYCLE_AFTER_MARKET_WORKFLOW,
    WEIGHTED_VOTING_PROMOTION_PRIORITY_CANDIDATES,
    WEIGHTED_VOTING_STRATEGY_LIFECYCLE_AUDIT_PREFIX,
    WEIGHTED_VOTING_STRATEGY_LIFECYCLE_LATEST_KEY,
    apply_strategy_lifecycle_decision,
    evaluate_strategy_lifecycle_change,
    load_latest_strategy_lifecycle_snapshot,
    rollback_strategy_lifecycle_version,
    strategy_lifecycle_status,
)


TS = datetime(2026, 1, 5, 22, 30, tzinfo=timezone.utc)


class WeightedVotingStrategyLifecycleTest(unittest.TestCase):
    def test_promotion_cannot_occur_without_evidence(self) -> None:
        decision = evaluate_strategy_lifecycle_change(None)

        self.assertFalse(decision.approved)
        self.assertEqual(decision.action, "reject")
        self.assertIn("weighted_voting.strategy_lifecycle.evidence_required", decision.reason_codes)

    def test_initial_snapshot_contains_only_active_catalog_strategies(self) -> None:
        store = MemoryStore()
        initial = load_latest_strategy_lifecycle_snapshot(store, timestamp=TS)

        self.assertEqual(initial.strategy_states, {"S2": "active", "S5": "active", "S6": "active", "S7": "active"})
        self.assertTrue(all(key.startswith("weighted_voting.") for key in store.snapshots))

    def test_intraday_or_non_admin_workflow_rejects_even_strong_evidence(self) -> None:
        decision = evaluate_strategy_lifecycle_change(
            passing_evidence("S2", workflow="automatic_runtime", after_market_session_complete=False)
        )

        self.assertFalse(decision.approved)
        self.assertEqual(decision.action, "reject")
        self.assertIn("weighted_voting.strategy_lifecycle.after_market_required", decision.reason_codes)

    def test_retired_strategy_ids_are_not_lifecycle_targets(self) -> None:
        for strategy_id in ("S1", "S3", "S4", "S8"):
            with self.subTest(strategy_id=strategy_id):
                with self.assertRaisesRegex(ValueError, "unknown Weighted Voting strategy lifecycle evidence target"):
                    passing_evidence(strategy_id)

    def test_active_strategy_demotes_or_disables_when_demotion_gates_fail(self) -> None:
        demote = evaluate_strategy_lifecycle_change(passing_evidence("S2", recent_net_expectancy_after_costs=-0.03))
        disable = evaluate_strategy_lifecycle_change(passing_evidence("S5", strategy_error_rate=0.10))

        self.assertTrue(demote.approved)
        self.assertEqual(demote.action, "demote")
        self.assertEqual(demote.target_lifecycle, "shadow")
        self.assertIn("weighted_voting.strategy_lifecycle.recent_expectancy_negative", demote.reason_codes)
        self.assertTrue(disable.approved)
        self.assertEqual(disable.action, "disable")
        self.assertEqual(disable.target_lifecycle, "disabled")
        self.assertIn("weighted_voting.strategy_lifecycle.strategy_errors_exceeded", disable.reason_codes)

    def test_rollback_restores_previous_lifecycle_version(self) -> None:
        store = MemoryStore()
        initial = load_latest_strategy_lifecycle_snapshot(store, timestamp=TS)
        decision = evaluate_strategy_lifecycle_change(passing_evidence("S2", recent_net_expectancy_after_costs=-0.03), current_snapshot=initial)
        updated = apply_strategy_lifecycle_decision(store, decision, current_snapshot=initial, approved_by="risk-admin")

        rolled_back = rollback_strategy_lifecycle_version(
            store,
            target_lifecycle_version=initial.lifecycle_version,
            rolled_back_at=TS,
            approved_by="risk-admin",
        )

        self.assertEqual(updated.strategy_states["S2"], "shadow")
        self.assertEqual(rolled_back.lifecycle_version, initial.lifecycle_version)
        self.assertEqual(rolled_back.strategy_states["S2"], "active")
        self.assertEqual(store.snapshots[WEIGHTED_VOTING_STRATEGY_LIFECYCLE_LATEST_KEY]["lifecycle_version"], initial.lifecycle_version)
        rollback_audits = [key for key in store.snapshots if key.startswith(WEIGHTED_VOTING_STRATEGY_LIFECYCLE_AUDIT_PREFIX)]
        self.assertEqual(len(rollback_audits), 2)

    def test_foreign_algorithm_evidence_is_rejected_and_status_exposes_priority_candidates(self) -> None:
        with self.assertRaises(ValueError):
            passing_evidence("S2", algorithm_id="voting_ensemble")

        status = strategy_lifecycle_status()
        service_status = WeightedVotingService(store=MemoryStore()).status()

        self.assertEqual(status["promotionPriorityCandidates"], WEIGHTED_VOTING_PROMOTION_PRIORITY_CANDIDATES)
        self.assertEqual(service_status["strategyLifecycle"]["workflow"], WEIGHTED_VOTING_LIFECYCLE_AFTER_MARKET_WORKFLOW)
        self.assertIn("rule_based_only_no_ml", service_status["strategyLifecycle"]["rules"])


def passing_evidence(strategy_id: str, **overrides):
    values = {
        "algorithm_id": "weighted_voting",
        "strategy_id": strategy_id,
        "evidence_id": f"evidence-{strategy_id}",
        "evaluated_at": TS,
        "workflow": WEIGHTED_VOTING_LIFECYCLE_AFTER_MARKET_WORKFLOW,
        "after_market_session_complete": True,
        "eligible_opportunities": 250,
        "completed_trades": 80,
        "net_expectancy_after_costs": 0.012,
        "conservative_expectancy_lower_bound": 0.004,
        "maximum_drawdown": 0.03,
        "mae_quality": 0.72,
        "mfe_quality": 0.74,
        "walk_forward_stability": 0.78,
        "holdout_stability": 0.76,
        "paper_shadow_stability": 0.80,
        "session_consistency": 0.82,
        "regime_consistency": 0.79,
        "severe_tail_loss_pattern": False,
        "correlation_with_active_strategies": 0.42,
        "incremental_portfolio_value": 0.006,
        "data_quality_stability": 0.99,
        "recent_net_expectancy_after_costs": 0.006,
        "data_readiness_rate": 0.98,
        "execution_cost_edge_ratio": 0.25,
        "paper_backtest_divergence": 0.08,
        "strategy_error_rate": 0.0,
    }
    values.update(overrides)
    from backend.app.algorithms.weighted_voting.strategy_lifecycle import WeightedVotingStrategyLifecycleEvidence

    return WeightedVotingStrategyLifecycleEvidence(**values)


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


if __name__ == "__main__":
    unittest.main()
