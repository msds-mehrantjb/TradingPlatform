from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.meta_strategy.observability import build_meta_strategy_observability_snapshot
from backend.tests.meta_strategy.paper_e2e_support import (
    PaperE2EEnv,
    blocked_runner,
)

NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)


class MetaStrategyRequiredPaperE2ETest(unittest.TestCase):
    def test_phase13_happy_path_processes_one_automatic_paper_entry_end_to_end(self) -> None:
        env = PaperE2EEnv()
        first = env.enqueue_finalized_bar()
        duplicate = env.enqueue_finalized_bar()

        decision_job = env.decision_worker().run_once(now=NOW)
        event_id = env.jobs.read_payload(first.payload_reference)["payload"]["eventId"]
        decision = env.jobs.decision_for_event(event_id)
        outbox = env.jobs.outbox_for_order_intent("meta_strategy.order_intent.decision-1")
        snapshot_before_submit = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})

        self.assertEqual(duplicate.job_id, first.job_id)
        self.assertTrue(duplicate.duplicate)
        self.assertIsNotNone(decision_job)
        self.assertEqual(env.state_provider.load_count, 1)
        self.assertEqual(env.state_provider.last_context["inventorySnapshot"]["source"], "authoritative_meta_strategy_inventory_repository")
        self.assertEqual(env.state_provider.last_context["accountSnapshot"]["source"], "fake_authoritative_paper_account")
        self.assertEqual(env.jobs.queue_status(queue_name="finalised_bar_decisions", now=NOW)["queues"]["finalised_bar_decisions"]["succeeded"], 1)
        self.assertEqual(outbox["status"], "PENDING")
        self.assertEqual(snapshot_before_submit.reserved_risk_dollars, 10.0)
        self.assertTrue(outbox["payload"]["atomicPersistence"]["decisionRecordPersisted"])
        self.assertTrue(outbox["payload"]["atomicPersistence"]["riskReservationPersisted"])
        self.assertEqual(len(env.inventory.inventory_records("order_intents")), 1)
        self.assertIn("strategyEvidence", decision["payload"]["stages"])
        self.assertTrue(decision["payload"]["stages"]["strategyEvidence"]["eligible"])
        self.assertTrue(decision["payload"]["stages"]["aggregateCandidate"]["eligible"])
        self.assertEqual(outbox["payload"]["quantity"], 10)
        self.assertEqual(outbox["payload"]["accountEquity"], 100_000.0)
        self.assertEqual(outbox["payload"]["buyingPower"], 100_000.0)
        self.assertEqual(outbox["payload"]["remainingAlgorithmRisk"], 1_000.0)

        result = env.submission_worker().run_once(now=NOW + timedelta(seconds=1))
        acknowledged = env.jobs.outbox_for_order_intent("meta_strategy.order_intent.decision-1")

        self.assertEqual(result["status"], "ACKNOWLEDGED")
        self.assertEqual(env.broker.submit_count, 1)
        self.assertEqual(env.market_clock.read_count, 1)
        self.assertEqual(acknowledged["brokerOrderId"], "broker-meta_strategy.order_intent.decision-1")
        self.assertIn("executionGuard", acknowledged["payload"])
        self.assertIn("meta_strategy.execution_guard.new_entry_allowed", acknowledged["payload"]["executionGuard"]["reasonCodes"])

        env.broker.enqueue_fill(order_intent_id="meta_strategy.order_intent.decision-1", quantity=10, price=100.0, side="BUY", event_id="phase13-entry-fill")
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=2))

        open_snapshot = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        sibling_snapshot = env.sibling_inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        self.assertEqual(open_snapshot.open_positions[0].quantity, 10.0)
        self.assertEqual(sibling_snapshot.open_positions, ())
        self.assertEqual(open_snapshot.reserved_risk_dollars, 0.0)

        env.enqueue_end_of_day_position_management(key="phase13-position-management")
        self.assertGreaterEqual(env.jobs.queue_status(queue_name="position_management", now=NOW)["queues"]["position_management"]["pending"], 1)
        position_result = env.position_worker().run_once(now=NOW + timedelta(minutes=1))
        self.assertEqual(position_result["createdExitIntentCount"], 1)

        observability = build_meta_strategy_observability_snapshot(
            job_repository=env.jobs,
            inventory_repository=env.inventory,
            settings_store=env.settings_store,
            now=NOW + timedelta(seconds=3),
        )
        self.assertGreaterEqual(observability["metrics"]["brokerEventCount"], 1)
        self.assertEqual(observability["metrics"]["finalizedCandleOutcomeCounts"]["ORDER_SUBMITTED"], 1)
        self.assertEqual(env.readiness()["status"], "OK")

    def test_phase13_blocked_path_matrix_never_submits_or_leaks_reserved_risk(self) -> None:
        cases = (
            ("paper_toggle_off", "before", lambda env: env.jobs.update_paper_trading_control(new_paper_entries_enabled=False, updated_by="test", reason="phase13.off", now=NOW + timedelta(milliseconds=1)), "meta_strategy.paper_control.new_entry_blocked_before_submission"),
            ("market_closed", "before", lambda env: setattr(env.market_clock, "is_open", False), "meta_strategy.execution_guard.market_closed"),
            ("readiness_false", "before", lambda env: env.set_readiness(status="REJECTED", complete=False, paper_ready=False), "meta_strategy.execution_guard.readiness_status_not_ok"),
            ("runtime_shadow", "before", lambda env: env.set_runtime(mode="SHADOW"), "meta_strategy.execution_guard.runtime_not_paper"),
            ("runtime_disabled", "before", lambda env: env.set_runtime(enabled=False), "meta_strategy.execution_guard.runtime_disabled"),
            ("stale_candle", "before", lambda env: setattr(env.market_data, "stale_candle", True), "meta_strategy.execution_guard.decision_stale"),
            ("stale_quote", "before", lambda env: setattr(env.market_data, "stale_quote", True), "meta_strategy.execution_guard.quote_stale"),
            ("missing_account_equity", "before", lambda env: setattr(env.account_source, "equity", None), "meta_strategy.sizing.account_equity_unavailable"),
            ("zero_account_equity", "before", lambda env: setattr(env.account_source, "equity", 0.0), "meta_strategy.sizing.zero_account_equity"),
            ("zero_buying_power", "before", lambda env: setattr(env.account_source, "buying_power", 0.0), "meta_strategy.sizing.zero_buying_power"),
            ("zero_local_risk", "before", lambda env: setattr(env, "remaining_algorithm_risk", 0.0), "meta_strategy.sizing.zero_algorithm_risk"),
            ("zero_global_risk", "before", lambda env: setattr(env.global_risk, "maximum_risk", 0.0), "meta_strategy.execution_guard.zero_global_risk"),
            ("hard_safety_failure", "before", lambda env: setattr(env, "pipeline_runner", blocked_runner("meta_strategy.safety.hard_gate_blocked")), "meta_strategy.safety.hard_gate_blocked"),
            ("local_gate_failure", "before", lambda env: setattr(env, "pipeline_runner", blocked_runner("meta_strategy.execution_guard.local_gates_not_passed")), "meta_strategy.execution_guard.local_gates_not_passed"),
            ("global_risk_rejection", "before", lambda env: setattr(env.global_risk, "action", "REJECT_NEW_ENTRY"), "meta_strategy.execution.global_risk_rejected"),
            ("settings_version_changed", "after_decision", lambda env: env.activate_replacement_settings(), "meta_strategy.execution_guard.settings_version_changed"),
            ("duplicate_order", "after_decision", lambda env: env.mark_current_order_duplicate(), "meta_strategy.execution_guard.duplicate_client_order_id"),
            ("existing_meta_strategy_position", "after_decision", lambda env: env.seed_meta_strategy_position(), "meta_strategy.execution_guard.existing_position"),
            ("wrong_capital_partition", "after_decision", lambda env: env.patch_current_outbox({"capitalPartitionId": "weighted_voting.paper.default"}), "meta_strategy.execution_guard.wrong_capital_partition"),
            ("wrong_algorithm_id", "after_decision", lambda env: env.patch_current_outbox({"algorithmId": "weighted_voting"}), "meta_strategy.execution.foreign_outbox_rejected"),
        )
        for name, timing, mutate, reason in cases:
            with self.subTest(name=name):
                env = PaperE2EEnv()
                if timing == "before":
                    mutate(env)
                env.enqueue_finalized_bar()
                env.decision_worker().run_once(now=NOW)
                if timing == "after_decision":
                    mutate(env)

                result = env.submission_worker().run_once(now=NOW + timedelta(seconds=1))

                self.assertEqual(env.broker.submit_count, 0)
                self.assertEqual(env.sibling_inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0}).open_positions, ())
                self.assertLessEqual(env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0}).reserved_risk_dollars, 0.0)
                outcome = env.latest_outcome()
                self.assertIsNotNone(outcome)
                if result is None:
                    self.assertEqual(outcome["outcome"], "BLOCKED")
                    self.assertIn(reason, outcome["reasonCodes"])
                else:
                    self.assertEqual(result["status"], "REJECTED")
                    self.assertIn(reason, result["reasonCodes"])
                    self.assertIn(reason, env.jobs.outbox_for_order_intent("meta_strategy.order_intent.decision-1")["payload"]["reasonCodes"])

    def test_phase13_duplicate_finalized_event_does_not_create_duplicate_order(self) -> None:
        env = PaperE2EEnv()
        first = env.enqueue_finalized_bar()
        duplicate = env.enqueue_finalized_bar()

        env.decision_worker().run_once(now=NOW)
        env.submission_worker().run_once(now=NOW + timedelta(seconds=1))
        second_submission = env.submission_worker().run_once(now=NOW + timedelta(seconds=2))

        self.assertEqual(duplicate.job_id, first.job_id)
        self.assertTrue(duplicate.duplicate)
        self.assertIsNone(second_submission)
        self.assertEqual(env.broker.submit_count, 1)

    def test_phase13_restart_recovery_scenarios_do_not_duplicate_orders(self) -> None:
        scenarios = (
            "before_decision_job",
            "after_decision_persistence",
            "unknown_broker_outcome",
            "partial_fill",
            "open_position",
            "paper_toggle_off",
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                env = PaperE2EEnv()
                env.enqueue_finalized_bar()
                if scenario == "before_decision_job":
                    restarted = env.restart()
                    restarted.decision_worker().run_once(now=NOW + timedelta(seconds=1))
                    restarted.submission_worker().run_once(now=NOW + timedelta(seconds=2))
                    self.assertEqual(restarted.broker.submit_count, 1)
                    continue

                env.decision_worker().run_once(now=NOW)
                if scenario == "after_decision_persistence":
                    restarted = env.restart()
                    restarted.submission_worker().run_once(now=NOW + timedelta(seconds=1))
                    restarted.submission_worker().run_once(now=NOW + timedelta(seconds=2))
                    self.assertEqual(restarted.broker.submit_count, 1)
                    continue

                if scenario == "paper_toggle_off":
                    env.jobs.update_paper_trading_control(new_paper_entries_enabled=False, updated_by="test", reason="phase13.restart.off", now=NOW + timedelta(milliseconds=1))
                    restarted = env.restart()
                    result = restarted.submission_worker().run_once(now=NOW + timedelta(seconds=1))
                    self.assertEqual(result["status"], "REJECTED")
                    self.assertEqual(restarted.broker.submit_count, 0)
                    continue

                if scenario == "unknown_broker_outcome":
                    env.broker.timeout_after_submit = True
                    first = env.submission_worker().run_once(now=NOW + timedelta(seconds=1))
                    self.assertEqual(first["status"], "RECONCILIATION_REQUIRED")
                    env.broker.timeout_after_submit = False
                    restarted = env.restart()
                    restarted.reconciliation_worker().run_once(now=NOW + timedelta(seconds=20))
                    restarted.submission_worker().run_once(now=NOW + timedelta(seconds=21))
                    self.assertEqual(restarted.broker.submit_count, 1)
                    self.assertEqual(restarted.jobs.outbox_for_order_intent("meta_strategy.order_intent.decision-1")["status"], "ACKNOWLEDGED")
                    continue

                env.submission_worker().run_once(now=NOW + timedelta(seconds=1))
                if scenario == "partial_fill":
                    env.broker.enqueue_fill(order_intent_id="meta_strategy.order_intent.decision-1", quantity=4, price=100.0, side="BUY", event_id="phase13-partial-fill", status="PARTIALLY_FILLED")
                    restarted = env.restart()
                    restarted.reconciliation_worker().run_once(now=NOW + timedelta(seconds=2))
                    self.assertEqual(restarted.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0}).open_positions[0].quantity, 4.0)
                    self.assertEqual(restarted.broker.submit_count, 1)
                    continue

                env.broker.enqueue_fill(order_intent_id="meta_strategy.order_intent.decision-1", quantity=10, price=100.0, side="BUY", event_id="phase13-open-position-fill")
                env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=2))
                restarted = env.restart()
                restarted.enqueue_end_of_day_position_management(key="phase13-restart-open-position")
                restarted.position_worker().run_once(now=NOW + timedelta(minutes=1))
                restarted.submission_worker().run_once(now=NOW + timedelta(minutes=1, seconds=1))
                self.assertEqual(restarted.broker.submit_count, 2)

    def test_finalized_bar_to_closed_paper_trade_and_pnl_with_integration_safe_broker(self) -> None:
        env = PaperE2EEnv()
        env.enqueue_finalized_bar()

        decision_job = env.decision_worker().run_once(now=NOW)
        outbox = env.jobs.outbox_for_order_intent("meta_strategy.order_intent.decision-1")
        self.assertIsNotNone(decision_job)
        self.assertEqual(env.jobs.queue_status(queue_name="finalised_bar_decisions", now=NOW)["queues"]["finalised_bar_decisions"]["succeeded"], 1)
        self.assertEqual(outbox["status"], "PENDING")

        env.submission_worker().run_once(now=NOW + timedelta(seconds=1))
        acknowledged = env.jobs.outbox_for_order_intent("meta_strategy.order_intent.decision-1")
        self.assertEqual(acknowledged["status"], "ACKNOWLEDGED")

        env.broker.enqueue_fill(order_intent_id="meta_strategy.order_intent.decision-1", quantity=10, price=100.0, side="BUY", event_id="entry-fill")
        env.reconciliation_worker().run_once(now=NOW + timedelta(seconds=2))
        open_snapshot = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 100.0})
        self.assertEqual(open_snapshot.open_positions[0].quantity, 10.0)

        env.enqueue_end_of_day_position_management()
        position_result = env.position_worker().run_once(now=NOW + timedelta(minutes=1))
        exit_intent_id = "meta_strategy.exit.meta_strategy.position.meta_strategy.paper.default.SPY.SESSION_END"
        self.assertEqual(position_result["createdExitIntentCount"], 1)
        self.assertEqual(env.jobs.outbox_for_order_intent(exit_intent_id)["status"], "PENDING")

        env.submission_worker().run_once(now=NOW + timedelta(minutes=1, seconds=1))
        env.broker.enqueue_fill(order_intent_id=exit_intent_id, quantity=10, price=101.0, side="SELL", event_id="exit-fill")
        env.reconciliation_worker().run_once(now=NOW + timedelta(minutes=1, seconds=2))

        closed = env.inventory.current_inventory_snapshot(mark_prices={"SPY": 101.0})
        self.assertEqual(closed.open_positions, ())
        self.assertGreater(closed.realised_pnl, 0.0)
        self.assertGreaterEqual(env.jobs.broker_event_count(), 2)
