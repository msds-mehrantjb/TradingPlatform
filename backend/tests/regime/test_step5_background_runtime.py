from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.algorithms.regime import api as regime_api
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_events import event_payload_has_forbidden_operational_state
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService


TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_step5_runtime"


class CapturingRegimeService(RegimeApplicationService):
    def __init__(self, repository: RegimeRepository) -> None:
        super().__init__(repository)
        self.last_evaluate_payload: dict | None = None

    def evaluate(self, payload: dict) -> dict:
        self.last_evaluate_payload = copy.deepcopy(payload)
        return super().evaluate(payload)


def test_nested_operational_state_is_rejected_before_runtime_enqueue() -> None:
    assert event_payload_has_forbidden_operational_state({"marketData": {"account": {"buyingPower": 1_000_000}}})
    assert event_payload_has_forbidden_operational_state({"marketData": {"contextFeeds": {"positions": []}}})
    assert not event_payload_has_forbidden_operational_state({"marketData": {"contextFeeds": {"quoteFreshness": {"bid": 100.0, "ask": 100.01}}}})

    async def scenario() -> None:
        supervisor = _supervisor()[0]
        rejected = await supervisor.publish_completed_bar({**_completed_bar_payload(), "marketData": {"account": {"buyingPower": 1_000_000}}})
        assert rejected["accepted"] is False
        assert "regime.runtime.event.operational_state_payload_rejected" in rejected["reasonCodes"]

    asyncio.run(scenario())


def test_worker_loads_shared_account_snapshot_inside_background_processing() -> None:
    calls: list[dict[str, str]] = []

    def account_provider(identity: dict[str, str]) -> dict:
        calls.append(dict(identity))
        return {
            "sourceAuthority": "shared_backend_service",
            "availableBuyingPower": 12_345.0,
            "buyingPower": 12_345.0,
            "globalRiskCapacityQuantity": 7,
            "positionsReconciled": True,
            "openOrdersReconciled": True,
        }

    async def scenario() -> None:
        supervisor, service = _supervisor(account_snapshot_provider=account_provider)
        await supervisor.start()
        try:
            await _wait_for(lambda: supervisor.status()["recovery_succeeded"] is True)
            receipt = await supervisor.publish_completed_bar(_completed_bar_payload())
            assert receipt["accepted"] is True
            await _wait_for(lambda: supervisor.status()["processed_events"] == 1)
            assert calls == [
                {
                    "algorithmId": "regime",
                    "algorithmInstanceId": "regime-default",
                    "accountId": "default",
                    "runtimeMode": "shadow",
                    "symbol": "SPY",
                }
            ]
            assert service.last_evaluate_payload is not None
            assert service.last_evaluate_payload["account"]["availableBuyingPower"] == 12_345.0
            assert service.last_evaluate_payload["account"]["globalRiskCapacityQuantity"] == 7
            assert service.last_evaluate_payload["account"]["runtimeLoadedBy"].startswith("regime_runtime_supervisor")
        finally:
            await supervisor.shutdown()

    asyncio.run(scenario())


def test_processing_lease_is_persisted_with_full_instance_mode_symbol_identity() -> None:
    async def scenario() -> None:
        supervisor, service = _supervisor()
        await supervisor.start()
        try:
            await _wait_for(lambda: supervisor.status()["recovery_succeeded"] is True)
            receipt = await supervisor.publish_completed_bar(_completed_bar_payload())
            assert receipt["accepted"] is True
            await _wait_for(lambda: supervisor.status()["processed_events"] == 1)
        finally:
            await supervisor.shutdown()

        identity = {
            "algorithmId": "regime",
            "algorithmInstanceId": "regime-default",
            "accountId": "default",
            "runtimeMode": "shadow",
            "symbol": "SPY",
        }
        leases = service.repository.read_owned_records("regime_runtime_instances", identity)
        assert any(record.get("workerId") == "regime-processing-lease:shadow:SPY" for record in leases)
        events = service.repository.read_owned_records("regime_runtime_events", identity)
        assert any(event.get("eventType") == "runtime_processing_lease_claimed" for event in events)
        assert any(event.get("eventType") == "runtime_processing_lease_released" for event in events)

    asyncio.run(scenario())


def test_step5_runtime_api_aliases_read_status_or_enqueue_commands(monkeypatch) -> None:
    class FakeSupervisor:
        def status(self):
            return {"algorithmId": "regime", "supervisor_started": True, "queueDepth": 0}

        def health(self):
            return {"algorithmId": "regime", "healthy": True}

        def recovery_status(self):
            return {"algorithmId": "regime", "recoveryStatus": "completed"}

        def latest_decision(self):
            return {"algorithmId": "regime", "decision": None}

        def latest_checkpoint(self):
            return {"algorithmId": "regime", "checkpoint": None}

        def queue_depth(self):
            return {"algorithmId": "regime", "queueDepth": 0, "commandQueueDepth": 0}

        async def submit_command(self, command_type, payload, *, actor="api"):
            return {"algorithmId": "regime", "accepted": True, "commandType": command_type}

    monkeypatch.setattr(regime_api, "get_regime_runtime_supervisor", lambda: FakeSupervisor())

    assert regime_api.regime_runtime_status()["supervisor_started"] is True
    assert regime_api.regime_supervisor_health()["healthy"] is True
    assert regime_api.regime_recovery()["recoveryStatus"] == "completed"
    assert regime_api.regime_queue()["queueDepth"] == 0
    assert regime_api.latest_regime_decision()["decision"] is None
    assert regime_api.latest_regime_checkpoint()["checkpoint"] is None
    assert "jobs" in regime_api.regime_backtest_jobs()
    assert asyncio.run(regime_api.pause_regime_runtime({"reason": "test"}))["commandType"] == "pause"


def _supervisor(*, account_snapshot_provider=None) -> tuple[RegimeRuntimeSupervisor, CapturingRegimeService]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    repository = RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")
    service = CapturingRegimeService(repository)
    supervisor = RegimeRuntimeSupervisor(
        service=service,
        config=RegimeRuntimeSupervisorConfig(
            queue_maxsize=4,
            command_queue_maxsize=4,
            max_processing_lag_seconds=75,
            heartbeat_interval_seconds=60,
            maintenance_interval_seconds=60,
        ),
        account_snapshot_provider=account_snapshot_provider,
    )
    return supervisor, service


def _completed_bar_payload() -> dict:
    candles = []
    price = 100.0
    for index in range(40):
        price += 0.03
        candles.append(
            {
                "timestamp": f"2026-07-23T14:{index:02d}:00Z",
                "open": round(price - 0.02, 4),
                "high": round(price + 0.08, 4),
                "low": round(price - 0.08, 4),
                "close": round(price, 4),
                "volume": 125_000,
            }
        )
    return {
        "algorithmInstanceId": "regime-default",
        "accountId": "default",
        "runtimeMode": "shadow",
        "symbol": "SPY",
        "completedBarTimestamp": candles[-1]["timestamp"],
        "publishedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "marketData": {
            "symbol": "SPY",
            "primaryCandles": candles,
            "oneMinuteCandles": candles,
            "contextFeeds": {
                "quoteFreshness": {"status": "fresh", "ageMs": 1000, "bid": 100.0, "ask": 100.02, "spreadBps": 2.0, "expectedFillQuantity": 100},
                "scheduledEconomicEvent": {"state": "none", "minutesUntilEvent": 999},
            },
        },
    }


async def _wait_for(predicate, *, timeout_seconds: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    pytest.fail("Timed out waiting for runtime condition")
