from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_health import RegimeRuntimeMetrics, alert_conditions_from_metrics, observe_decision_result, operational_snapshot_from_metrics
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService


class RegimeStep15ObservabilityControlsTest(unittest.TestCase):
    def test_observability_explains_no_trade_and_alerts_from_runtime_status(self) -> None:
        metrics = RegimeRuntimeMetrics(supervisor_started=True, recovery_succeeded=True, inventory_reconciled=True)
        decision = {
            "decision": {
                "signal": "Hold",
                "trade_blockers": ("regime.local_gate.daily_loss_limit", "regime.safety.circuit_breaker"),
                "confirmed_state": {"confirmed_regime": "liquidity_stress"},
                "strategy_outputs": [
                    {"strategy_id": "moving_average_trend", "eligible": True, "signal": "Hold"},
                    {"strategy_id": "opening_range_breakout", "eligible": False, "signal": "Hold"},
                ],
                "family_scores": {"trend": 0.2},
            },
            "familyAggregation": {"familyScores": {"trend": 0.2}},
            "orderProposal": {"quantity": 100},
            "globalRiskApproval": {"approvedQuantity": 40},
            "runtimeTiming": {"classifierLatencyMs": 3.0, "strategyLatencyMs": 5.0, "riskServiceLatencyMs": 1.0},
        }

        observe_decision_result(metrics, decision, decision_latency_ms=12.0, event_age_seconds=8.0)
        snapshot = operational_snapshot_from_metrics(metrics)
        alerts = alert_conditions_from_metrics(metrics)

        self.assertEqual(snapshot["algorithmId"], "regime")
        self.assertEqual(snapshot["signalCounts"]["Hold"], 1)
        self.assertEqual(snapshot["blockersByReason"]["regime.local_gate.daily_loss_limit"], 1)
        self.assertEqual(snapshot["regimeOccupancy"]["liquidity_stress"], 1)
        self.assertEqual(snapshot["strategyOpportunities"]["moving_average_trend"], 1)
        self.assertEqual(snapshot["proposedVsApprovedQuantity"]["reduced"], 60)
        self.assertTrue(snapshot["operatorsCanExplainNoTrade"])
        self.assertIn("regime.alert.daily_loss_limit", {alert["code"] for alert in alerts})
        self.assertIn("regime.alert.circuit_breaker_activation", {alert["code"] for alert in alerts})

    def test_admin_actions_are_durable_and_paused_runtime_keeps_recovery_heartbeat_and_protection(self) -> None:
        async def scenario() -> None:
            repository = temp_repository()
            supervisor = RegimeRuntimeSupervisor(
                service=RegimeApplicationService(repository),
                config=RegimeRuntimeSupervisorConfig(
                    queue_maxsize=4,
                    command_queue_maxsize=8,
                    heartbeat_interval_seconds=0.05,
                    maintenance_interval_seconds=0.05,
                    worker_lease_seconds=1,
                ),
            )
            await supervisor.start()
            try:
                await wait_for(lambda: supervisor.status()["recovery_succeeded"] is True)
                assert (await supervisor.submit_command("pause", {"reason": "operator_test"}, actor="tester"))["accepted"] is True
                await wait_for(lambda: supervisor.status()["paused"] is True)
                assert supervisor.status()["risk_reducing_exits_allowed"] is True
                await wait_for(lambda: any(row.get("eventType") == "worker_heartbeat" for row in repository.read_owned_records("regime_runtime_events", identity())))
                assert (await supervisor.submit_command("disable_strategy", {"strategyId": "moving_average_trend"}, actor="tester"))["accepted"] is True
                await wait_for(lambda: "moving_average_trend" in supervisor.status()["disabled_strategy_ids"])
                assert (await supervisor.submit_command("enable_strategy", {"strategyId": "moving_average_trend"}, actor="tester"))["accepted"] is True
                await wait_for(lambda: "moving_average_trend" not in supervisor.status()["disabled_strategy_ids"])
                assert (await supervisor.submit_command("rotate_settings_version", {"identity": identity()}, actor="tester"))["accepted"] is True
                await wait_for(lambda: "rotate_settings_version" in {row["commandType"] for row in supervisor.admin_audit()["audit"]})
            finally:
                await supervisor.shutdown()

            audit = repository.read_runtime_snapshot(identity(), "admin_audit")
            assert audit is not None
            command_types = {row["commandType"] for row in audit["commands"]}
            assert {"pause", "disable_strategy", "enable_strategy", "rotate_settings_version"}.issubset(command_types)
            runtime_events = repository.read_owned_records("regime_runtime_events", identity())
            assert all(event.get("algorithmId") == "regime" for event in runtime_events)

        asyncio.run(scenario())


def identity() -> dict[str, str]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-default",
        "accountId": "default",
        "runtimeMode": "shadow",
        "symbol": "SPY",
    }


def temp_repository() -> RegimeRepository:
    root = Path(__file__).resolve().parent / "tmp" / "regime_step15"
    root.mkdir(parents=True, exist_ok=True)
    return RegimeRepository(f"sqlite:///{root / f'{uuid4().hex}.sqlite'}")


async def wait_for(predicate, *, timeout: float = 5.0) -> None:
    deadline = datetime.now(UTC) + timedelta(seconds=timeout)
    while datetime.now(UTC) < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("Timed out waiting for Regime runtime condition")


if __name__ == "__main__":
    unittest.main()
