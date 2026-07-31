from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.algorithms.regime import api as regime_api
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_events import RegimeFinalisedBarEvent
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService


TEST_TMP_ROOT = Path(__file__).resolve().parents[2] / ".pytest_regime_phase3_runtime"


class CapturingRegimeService(RegimeApplicationService):
    def __init__(self, repository: RegimeRepository) -> None:
        super().__init__(repository)
        self.evaluate_calls = 0

    def evaluate(self, payload: dict) -> dict:
        self.evaluate_calls += 1
        return super().evaluate(payload)


def test_phase3_same_finalized_bar_is_processed_once_even_when_manifest_changes() -> None:
    async def scenario() -> None:
        supervisor, service = _supervisor()
        await supervisor.start()
        try:
            await _wait_for(lambda: supervisor.status()["recovery_succeeded"] is True)
            first = await supervisor.publish_completed_bar(_completed_bar_payload(volume=125_000))
            second = await supervisor.publish_completed_bar(_completed_bar_payload(volume=125_500))
            assert first["accepted"] is True
            assert second["accepted"] is True
            await _wait_for(lambda: supervisor.status()["processed_events"] == 1)
            await asyncio.sleep(0.05)
            assert supervisor.status()["duplicate_events"] >= 1
            assert service.evaluate_calls == 1
        finally:
            await supervisor.shutdown()

        decisions = service.repository.read_owned_records("regime_decisions", _identity())
        assert len(decisions) == 1
        decision = decisions[0].get("decision") if isinstance(decisions[0].get("decision"), dict) else {}
        assert str(decision.get("decision_id") or decisions[0].get("decisionId") or "").startswith("regime-decision-")

    asyncio.run(scenario())


def test_phase3_rejects_partial_or_still_forming_finalized_bar_events() -> None:
    async def scenario() -> None:
        supervisor, _ = _supervisor()
        partial = await supervisor.publish_completed_bar({**_completed_bar_payload(), "isFinalized": False})
        wrong_timeframe = await supervisor.publish_completed_bar({**_completed_bar_payload(), "marketData": {**_completed_bar_payload()["marketData"], "timeframe": "5Min"}})
        assert partial["accepted"] is False
        assert wrong_timeframe["accepted"] is False
        assert "regime.runtime.event.invalid" in partial["reasonCodes"]
        assert "regime.runtime.event.invalid" in wrong_timeframe["reasonCodes"]

    asyncio.run(scenario())


def test_phase3_restart_recovery_enqueues_unprocessed_durable_finalized_bar() -> None:
    async def scenario() -> None:
        supervisor, service = _supervisor()
        event = RegimeFinalisedBarEvent.from_payload(_completed_bar_payload())
        service.repository.record_runtime_event(
            {
                **event.identity,
                "eventId": event.event_id,
                "decisionId": event.event_id,
                "timestamp": event.completed_bar_timestamp.isoformat().replace("+00:00", "Z"),
                "eventType": "finalised_bar",
                "processingStatus": "queued",
                "payload": event.as_dict(),
            }
        )
        await supervisor.start()
        try:
            await _wait_for(lambda: supervisor.status()["processed_events"] == 1)
            assert supervisor.recovery_status()["missedFinalizedBarEventsRecovered"]["recoveredCount"] == 1
        finally:
            await supervisor.shutdown()

        decisions = service.repository.read_owned_records("regime_decisions", _identity())
        assert len(decisions) == 1

    asyncio.run(scenario())


def test_phase3_completed_bar_api_only_publishes_durable_event(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeSupervisor:
        async def publish_completed_bar(self, payload: dict) -> dict:
            calls.append(copy.deepcopy(payload))
            return {"algorithmId": "regime", "accepted": True, "reasonCodes": ["regime.runtime.event.enqueued"]}

    monkeypatch.setattr(regime_api, "get_regime_runtime_supervisor", lambda: FakeSupervisor())

    result = asyncio.run(regime_api.enqueue_regime_completed_bar(_completed_bar_payload()))

    assert result["accepted"] is True
    assert len(calls) == 1
    assert calls[0]["symbol"] == "SPY"


def _supervisor() -> tuple[RegimeRuntimeSupervisor, CapturingRegimeService]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    repository = RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")
    service = CapturingRegimeService(repository)
    supervisor = RegimeRuntimeSupervisor(
        service=service,
        config=RegimeRuntimeSupervisorConfig(
            queue_maxsize=8,
            command_queue_maxsize=4,
            max_processing_lag_seconds=75,
            heartbeat_interval_seconds=60,
            maintenance_interval_seconds=60,
        ),
    )
    return supervisor, service


def _identity() -> dict[str, str]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-default",
        "accountId": "default",
        "runtimeMode": "shadow",
        "symbol": "SPY",
    }


def _completed_bar_payload(*, volume: int = 125_000) -> dict:
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
                "volume": volume,
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
            "timeframe": "1Min",
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
