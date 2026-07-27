from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.algorithms.regime import api as regime_api
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService


TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_runtime"


def test_runtime_supervisor_starts_fail_closed_then_recovers() -> None:
    async def scenario() -> None:
        supervisor = _supervisor()
        await supervisor.start()
        try:
            status = supervisor.status()
            assert status["supervisor_started"] is True
            assert status["entry_creation_paused_for_reconciliation"] is True
            await _wait_for(lambda: supervisor.status()["recovery_succeeded"] is True)
            recovered = supervisor.recovery_status()
            assert recovered["recoveryStatus"] == "completed"
            assert supervisor.status()["inventory_reconciled"] is True
            assert supervisor.status()["entry_creation_paused_for_reconciliation"] is False
        finally:
            await supervisor.shutdown()

    asyncio.run(scenario())


def test_completed_bar_event_is_processed_by_background_worker() -> None:
    async def scenario() -> None:
        supervisor = _supervisor()
        await supervisor.start()
        try:
            await _wait_for(lambda: supervisor.status()["recovery_succeeded"] is True)
            receipt = await supervisor.publish_completed_bar(_completed_bar_payload())
            assert receipt["accepted"] is True
            assert receipt["queueDepth"] >= 0
            await _wait_for(lambda: supervisor.status()["processed_events"] == 1)
            status = supervisor.status()
            assert status["last_decision_id"]
            assert status["latest_decision"]["algorithmId"] == "regime"
            assert status["latest_decision"]["settingsSource"].endswith("RegimeRepository")
            assert supervisor.latest_checkpoint()["checkpoint"] is not None
        finally:
            await supervisor.shutdown()

    asyncio.run(scenario())


def test_duplicate_stale_out_of_order_and_payload_state_are_rejected() -> None:
    async def scenario() -> None:
        supervisor = _supervisor(max_processing_lag_seconds=1)
        await supervisor.start()
        try:
            await _wait_for(lambda: supervisor.status()["recovery_succeeded"] is True)
            payload = _completed_bar_payload()
            first = await supervisor.publish_completed_bar(payload)
            assert first["accepted"] is True
            await _wait_for(lambda: supervisor.status()["processed_events"] == 1)

            duplicate = await supervisor.publish_completed_bar({**payload, "eventId": first["eventId"]})
            assert duplicate["accepted"] is True
            await _wait_for(lambda: supervisor.status()["duplicate_events"] == 1)

            operational = await supervisor.publish_completed_bar({**_completed_bar_payload(), "settings": {"unsafe": True}})
            assert operational["accepted"] is False
            assert "regime.runtime.event.operational_state_payload_rejected" in operational["reasonCodes"]

            stale_payload = _completed_bar_payload(minute=33, published_at="2026-07-23T14:33:00Z")
            stale = await supervisor.publish_completed_bar(stale_payload)
            assert stale["accepted"] is True
            await _wait_for(lambda: supervisor.status()["stale_events"] == 1)

            old_payload = _completed_bar_payload(minute=31)
            out_of_order = await supervisor.publish_completed_bar(old_payload)
            assert out_of_order["accepted"] is True
            await _wait_for(lambda: supervisor.status()["out_of_order_events"] == 1)
        finally:
            await supervisor.shutdown()

    asyncio.run(scenario())


def test_pause_resume_and_emergency_flatten_are_commands_not_inline_calculations() -> None:
    async def scenario() -> None:
        supervisor = _supervisor()
        await supervisor.start()
        try:
            await _wait_for(lambda: supervisor.status()["recovery_succeeded"] is True)
            pause = await supervisor.submit_command("pause", {"reason": "test"})
            assert pause["accepted"] is True
            await _wait_for(lambda: supervisor.status()["paused"] is True)
            resume = await supervisor.submit_command("resume", {})
            assert resume["accepted"] is True
            await _wait_for(lambda: supervisor.status()["paused"] is False)
            flatten = await supervisor.submit_command("emergency_flatten", {"reason": "test"})
            assert flatten["accepted"] is True
            await _wait_for(lambda: supervisor.status()["emergency_flatten_requested"] is True)
            assert supervisor.health()["liveTradingEnabled"] is False if "liveTradingEnabled" in supervisor.health() else True
        finally:
            await supervisor.shutdown()

    asyncio.run(scenario())


def test_runtime_api_routes_delegate_to_supervisor_without_inline_decisions(monkeypatch) -> None:
    class FakeSupervisor:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def status(self):
            return {"algorithmId": "regime", "queueDepth": 0}

        def health(self):
            return {"algorithmId": "regime", "healthy": True}

        def latest_checkpoint(self):
            return {"algorithmId": "regime", "checkpoint": None}

        def queue_depth(self):
            return {"algorithmId": "regime", "queueDepth": 0}

        def latest_decision(self):
            return {"algorithmId": "regime", "decision": None}

        def recovery_status(self):
            return {"algorithmId": "regime", "recoveryStatus": "completed"}

        async def submit_command(self, command_type, payload, *, actor="api"):
            self.commands.append(command_type)
            return {"accepted": True, "commandType": command_type}

        async def publish_completed_bar(self, payload):
            return {"accepted": True, "queued": True}

    fake = FakeSupervisor()
    monkeypatch.setattr(regime_api, "get_regime_runtime_supervisor", lambda: fake)

    assert regime_api.regime_supervisor_status()["queueDepth"] == 0
    assert regime_api.regime_supervisor_health()["healthy"] is True
    assert regime_api.latest_regime_checkpoint()["checkpoint"] is None
    assert asyncio.run(regime_api.pause_regime_runtime({"reason": "test"}))["accepted"] is True
    assert asyncio.run(regime_api.resume_regime_runtime({}))["accepted"] is True
    assert asyncio.run(regime_api.emergency_flatten_regime_runtime({}))["accepted"] is True
    assert fake.commands == ["pause", "resume", "emergency_flatten"]


def _supervisor(*, max_processing_lag_seconds: int = 75) -> RegimeRuntimeSupervisor:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    repository = RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")
    service = RegimeApplicationService(repository)
    return RegimeRuntimeSupervisor(
        service=service,
        config=RegimeRuntimeSupervisorConfig(
            queue_maxsize=4,
            command_queue_maxsize=4,
            max_processing_lag_seconds=max_processing_lag_seconds,
            heartbeat_interval_seconds=60,
            maintenance_interval_seconds=60,
        ),
    )


def _completed_bar_payload(*, minute: int = 32, published_at: str | None = None) -> dict:
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
    selected = candles[: minute + 1]
    return {
        "algorithmInstanceId": "regime-default",
        "accountId": "default",
        "runtimeMode": "shadow",
        "symbol": "SPY",
        "completedBarTimestamp": selected[-1]["timestamp"],
        "publishedAt": published_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "marketData": {
            "symbol": "SPY",
            "primaryCandles": selected,
            "oneMinuteCandles": selected,
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
