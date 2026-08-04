from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_idempotency import REGIME_RUNTIME_STAGES
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService


TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_runtime"


def test_duplicate_delivery_after_restart_does_not_create_second_economic_record() -> None:
    async def scenario() -> None:
        path = _db_path()
        first = _supervisor(path=path)
        await first.start()
        try:
            await _wait_for(lambda: first.status()["recovery_succeeded"] is True)
            payload = _completed_bar_payload()
            receipt = await first.publish_completed_bar(payload)
            assert receipt["accepted"] is True
            await _wait_for(lambda: first.status()["processed_events"] == 1)
        finally:
            await first.shutdown()

        counts_after_first = RegimeRepository(f"sqlite:///{path}").table_counts()

        restarted = _supervisor(path=path)
        await restarted.start()
        try:
            await _wait_for(lambda: restarted.status()["recovery_succeeded"] is True)
            repeated = await restarted.publish_completed_bar(payload)
            assert repeated["accepted"] is True
            await _wait_for(lambda: restarted.status()["duplicate_events"] >= 1 or restarted.status()["out_of_order_events"] >= 1)
        finally:
            await restarted.shutdown()

        counts_after_repeat = RegimeRepository(f"sqlite:///{path}").table_counts()
        assert counts_after_repeat["regime_decisions"] == counts_after_first["regime_decisions"]
        assert counts_after_repeat["regime_order_intents"] == counts_after_first["regime_order_intents"]
        assert counts_after_repeat["regime_execution_outbox"] == counts_after_first["regime_execution_outbox"]
        assert counts_after_repeat["regime_trades"] == counts_after_first["regime_trades"]

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", tuple(stage for stage in REGIME_RUNTIME_STAGES if stage not in {"event_received", "snapshot_validated"}))
def test_crash_at_checkpoint_boundary_recovers_without_duplicate_paper_order(stage: str) -> None:
    async def scenario() -> None:
        path = _db_path()
        crashing = _supervisor(path=path, crash_after_stage=stage)
        await crashing.start()
        try:
            await _wait_for(lambda: crashing.status()["recovery_succeeded"] is True)
            await crashing.publish_completed_bar(_completed_bar_payload())
            await _wait_for(lambda: crashing.status()["last_error"] is not None)
        finally:
            await crashing.shutdown()

        recovered = _supervisor(path=path)
        await recovered.start()
        try:
            await _wait_for(lambda: recovered.status()["recovery_succeeded"] is True)
            await recovered.publish_completed_bar(_completed_bar_payload())
            await _wait_for(lambda: recovered.status()["processed_events"] == 1 or recovered.status()["duplicate_events"] >= 1)
        finally:
            await recovered.shutdown()

        counts = RegimeRepository(f"sqlite:///{path}").table_counts()
        assert counts["regime_decisions"] <= 1
        assert counts["regime_order_intents"] <= 1
        assert counts["regime_execution_outbox"] <= 1
        assert counts["regime_trades"] == 0

    asyncio.run(scenario())


def test_corrupt_checkpoint_state_is_quarantined_and_blocks_entries() -> None:
    async def scenario() -> None:
        path = _db_path()
        repository = RegimeRepository(f"sqlite:///{path}")
        repository.write_runtime_checkpoint({**_identity(), "decisionId": "bad-checkpoint", "payload": {"last": 1}})
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE regime_runtime_checkpoints SET payload_json = '{corrupt-json' WHERE decision_id = 'bad-checkpoint'")

        supervisor = _supervisor(path=path)
        await supervisor.start()
        try:
            await _wait_for(lambda: supervisor.status()["quarantined"] is True)
            status = supervisor.status()
            assert status["entry_creation_paused_for_reconciliation"] is True
            assert "regime.runtime.checkpoint_inconsistent" in status["entry_block_reason_codes"]
            assert status["risk_reducing_exits_allowed"] is True
        finally:
            await supervisor.shutdown()

        events = repository.read_owned_records("regime_runtime_events", _identity())
        assert any(event.get("eventType") == "runtime_state_quarantine" for event in events)

    asyncio.run(scenario())


def test_worker_heartbeat_leases_admin_audit_and_outbox_recovery_are_durable() -> None:
    async def scenario() -> None:
        path = _db_path()
        repository = RegimeRepository(f"sqlite:///{path}")
        repository.insert_execution_outbox_record(_identity(), {**_identity(), "decisionId": "decision-outbox", "orderIntentId": "intent-outbox", "side": "Buy", "quantity": 1})
        stale_expiry = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
        repository.record_worker_heartbeat(_identity(), worker_id="regime_decision_worker", owner_id="old-owner", lease_expires_at=stale_expiry)

        supervisor = _supervisor(path=path, heartbeat_interval_seconds=0.05)
        await supervisor.start()
        try:
            await _wait_for(lambda: supervisor.status()["recovery_succeeded"] is True)
            await _wait_for(lambda: supervisor.status()["abandoned_leases_detected"] >= 1)
            command = await supervisor.submit_command("pause", {"reason": "audit-test"}, actor="test")
            assert command["accepted"] is True
            await _wait_for(lambda: supervisor.status()["paused"] is True)
        finally:
            await supervisor.shutdown()

        events = repository.read_owned_records("regime_runtime_events", _identity())
        event_types = {event.get("eventType") for event in events}
        assert "runtime_admin_command_audit" in event_types
        assert "worker_heartbeat" in event_types
        assert "abandoned_lease_detection" in event_types

    asyncio.run(scenario())


def test_out_of_order_bar_rejected_unless_replay_recovery_is_explicit() -> None:
    async def scenario() -> None:
        supervisor = _supervisor(path=_db_path())
        await supervisor.start()
        try:
            await _wait_for(lambda: supervisor.status()["recovery_succeeded"] is True)
            await supervisor.publish_completed_bar(_completed_bar_payload(minute=33))
            await _wait_for(lambda: supervisor.status()["processed_events"] == 1)
            await supervisor.publish_completed_bar(_completed_bar_payload(minute=32))
            await _wait_for(lambda: supervisor.status()["out_of_order_events"] == 1)
            await supervisor.publish_completed_bar({**_completed_bar_payload(minute=32), "replayRecovery": True})
            await _wait_for(lambda: supervisor.status()["processed_events"] == 2 or supervisor.status()["duplicate_events"] >= 1)
        finally:
            await supervisor.shutdown()

    asyncio.run(scenario())


def _supervisor(
    *,
    path: Path,
    crash_after_stage: str | None = None,
    heartbeat_interval_seconds: float = 60.0,
) -> RegimeRuntimeSupervisor:
    repository = RegimeRepository(f"sqlite:///{path}")
    service = RegimeApplicationService(repository)
    return RegimeRuntimeSupervisor(
        service=service,
        config=RegimeRuntimeSupervisorConfig(
            queue_maxsize=4,
            command_queue_maxsize=4,
            max_processing_lag_seconds=99_999_999,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            maintenance_interval_seconds=60,
            crash_after_stage=crash_after_stage,
        ),
    )


def _db_path() -> Path:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return TEST_TMP_ROOT / f"{uuid4().hex}.sqlite3"


def _identity() -> dict[str, str]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-default",
        "accountId": "default",
        "runtimeMode": "shadow",
        "symbol": "SPY",
    }


def _completed_bar_payload(*, minute: int = 32) -> dict:
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
        "publishedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
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
