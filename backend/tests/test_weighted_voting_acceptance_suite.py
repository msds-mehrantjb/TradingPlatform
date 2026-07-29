import asyncio
import json
import time
import unittest
from pathlib import Path

from backend.app.algorithms.weighted_voting.acceptance_suite import (
    WEIGHTED_VOTING_SYSTEM_ACCEPTANCE_VERSION,
    build_weighted_voting_system_acceptance_report,
    weighted_voting_system_acceptance_is_complete,
)
from backend.app.algorithms.weighted_voting.catalog import WEIGHTED_VOTING_STRATEGY_CATALOG
from backend.app.algorithms.weighted_voting.runtime_supervisor import WeightedVotingEventBus, WeightedVotingRuntimeConfig, WeightedVotingRuntimeSupervisor
from backend.app.algorithms.weighted_voting.service import WeightedVotingService
from backend.app.execution import PaperOrderGateway
from backend.tests.test_weighted_voting_runtime_supervisor import (
    AcceptedExecutionService,
    FakePaperBroker,
    MemoryStore,
    event_from_payload,
    evaluate_payload,
    seeded_inventory,
    validated_rollout_flags,
    validated_rollout_validation,
)


ROOT = Path(__file__).resolve().parents[2]


class WeightedVotingAcceptanceSuiteTest(unittest.TestCase):
    def test_machine_readable_report_covers_every_requested_acceptance_requirement(self) -> None:
        report = build_weighted_voting_system_acceptance_report()

        self.assertEqual(report["algorithmId"], "weighted_voting")
        self.assertEqual(report["version"], WEIGHTED_VOTING_SYSTEM_ACCEPTANCE_VERSION)
        self.assertTrue(report["machineReadable"])
        self.assertTrue(report["complete"])
        self.assertTrue(weighted_voting_system_acceptance_is_complete())
        self.assertEqual(report["counts"], {"pass": 106, "fail": 0})
        self.assertEqual(report["requirementCount"], 106)
        self.assertEqual(report["blockingRequirementIds"], [])
        json.dumps(report, sort_keys=True)

    def test_acceptance_report_has_pass_fail_status_for_every_requirement_and_existing_evidence(self) -> None:
        report = build_weighted_voting_system_acceptance_report()
        requirement_ids = set()
        categories = set()

        for item in report["requirements"]:
            with self.subTest(requirement=item["requirementId"]):
                self.assertNotIn(item["requirementId"], requirement_ids)
                requirement_ids.add(item["requirementId"])
                categories.add(item["category"])
                self.assertIn(item["status"], {"pass", "fail"})
                self.assertTrue(item["evidence"])
                for evidence in item["evidence"]:
                    self.assertTrue((ROOT / evidence).exists(), evidence)

        self.assertEqual(
            categories,
            {
                "Isolation tests",
                "Background-runtime tests",
                "Inventory tests",
                "Settings tests",
                "Strategy tests",
                "Gate and sizing tests",
                "Execution tests",
                "Parity tests",
                "Performance tests",
            },
        )

    def test_strategy_acceptance_matrix_covers_every_active_or_shadow_strategy(self) -> None:
        report = build_weighted_voting_system_acceptance_report()
        requirement_ids = {item["requirementId"] for item in report["requirements"]}
        behaviors = {
            "warm_up",
            "data_readiness",
            "buy",
            "sell",
            "hold",
            "session_boundary",
            "invalidation",
            "stale_data",
            "malformed_data",
            "confidence_bounds",
            "no_future_candle_usage",
        }

        for entry in WEIGHTED_VOTING_STRATEGY_CATALOG:
            if entry.lifecycle not in {"active", "shadow"}:
                continue
            for behavior in behaviors:
                self.assertIn(f"strategy.{entry.strategy_id.lower()}.{behavior}", requirement_ids)

    def test_service_status_exposes_system_acceptance_report(self) -> None:
        status = WeightedVotingService(store=MemoryStore()).status()

        self.assertIn("systemAcceptance", status)
        self.assertEqual(status["systemAcceptance"]["version"], WEIGHTED_VOTING_SYSTEM_ACCEPTANCE_VERSION)
        self.assertTrue(status["systemAcceptance"]["complete"])
        self.assertEqual(status["systemAcceptance"]["counts"]["fail"], 0)

    def test_acceptance_latency_smoke_for_one_minute_workflow(self) -> None:
        store = MemoryStore()
        supervisor = WeightedVotingRuntimeSupervisor(
            service=WeightedVotingService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=64, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=64),
        )
        latencies_ms = []
        for offset in range(12):
            started = time.perf_counter()
            asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=offset))))
            latencies_ms.append((time.perf_counter() - started) * 1000)

        self.assertLess(max(latencies_ms), 60_000.0)
        self.assertLess(sum(latencies_ms) / len(latencies_ms), 5_000.0)
        self.assertFalse(any(key.startswith(("wca.", "meta_strategy.", "voting_ensemble.")) for key in store.snapshots))

    def test_acceptance_latency_smoke_for_decision_to_order_path(self) -> None:
        latencies_ms = []
        broker_submissions = 0
        for offset in range(5):
            store = MemoryStore()
            broker = FakePaperBroker()
            gateway = PaperOrderGateway(broker, store)
            supervisor = WeightedVotingRuntimeSupervisor(
                service=AcceptedExecutionService(store=store),
                store=store,
                config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
                event_bus=WeightedVotingEventBus(maxsize=8),
                paper_gateway=gateway,
                inventory_repository=seeded_inventory(store),
                rollout_flags=validated_rollout_flags(),
                rollout_validation=validated_rollout_validation(),
            )
            asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=offset))))
            item = supervisor.execution_queue.get_nowait()
            started = time.perf_counter()
            supervisor.process_execution_queue_item(item)
            latencies_ms.append((time.perf_counter() - started) * 1000)
            broker_submissions += broker.submit_count

        self.assertEqual(broker_submissions, 5)
        self.assertLess(max(latencies_ms), 60_000.0)
        self.assertLess(sum(latencies_ms) / len(latencies_ms), 5_000.0)


if __name__ == "__main__":
    unittest.main()
