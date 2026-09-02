from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.position_manager import RegimePositionManager
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime_events import RegimeFinalisedBarEvent
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.execution import PaperOrderGateway


TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "tmp" / "regime_step11"


def test_step11_completed_bar_worker_enqueues_idempotent_paper_exit_intent() -> None:
    async def scenario() -> None:
        repository = _repository()
        identity = _identity()
        manager = RegimePositionManager(repository)
        now = datetime(2026, 7, 23, 14, 39, tzinfo=UTC)
        opened = manager.apply_fill_observation(identity, _fill_observation(now))["position"]
        supervisor = RegimeRuntimeSupervisor(
            service=RegimeApplicationService(repository),
            config=RegimeRuntimeSupervisorConfig(default_runtime_mode="paper", max_processing_lag_seconds=99_999_999, maintenance_interval_seconds=60, heartbeat_interval_seconds=60),
            paper_gateway=PaperOrderGateway(_BrokerWithoutPositions(), _SnapshotStore()),
        )
        event = RegimeFinalisedBarEvent.from_payload(_completed_bar_payload(now, low=99.4, close=99.45))

        first = await supervisor.process_finalised_bar_event(event)
        second = await supervisor.process_finalised_bar_event(event)
        outbox = repository.read_owned_records("regime_execution_outbox", identity)
        exit_outbox = [
            record
            for record in outbox
            if str(record.get("orderIntent", {}).get("positionEffect") or record.get("positionEffect") or "").startswith("exit")
        ]
        positions = repository.latest_regime_positions(identity)

        assert first["processed"] is True
        assert second["processed"] is False
        assert "regime.runtime.event.duplicate_durable_completed" in second["reasonCodes"]
        assert len(exit_outbox) == 1
        assert exit_outbox[0]["orderIntent"]["paperOnly"] is True
        assert exit_outbox[0]["orderIntent"]["liveTradingEnabled"] is False
        assert exit_outbox[0]["orderIntent"]["positionId"] == opened["positionId"]
        assert exit_outbox[0]["processingStatus"] == "created"
        assert positions[-1]["positionStatus"] == "closed"
        assert positions[-1]["exitReason"] == "initial_stop"

    asyncio.run(scenario())


def _repository() -> RegimeRepository:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return RegimeRepository(f"sqlite:///{TEST_TMP_ROOT / f'{uuid4().hex}.sqlite3'}")


def _identity() -> dict[str, str]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-default",
        "accountId": "default",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }


def _fill_observation(now: datetime) -> dict:
    return {
        **_identity(),
        "algorithmId": "regime",
        "decisionId": "regime-entry-decision-step11",
        "orderIntentId": "regime-entry-intent-step11",
        "fillId": "regime-fill-step11",
        "brokerOrderId": "broker-regime-step11",
        "symbol": "SPY",
        "side": "Buy",
        "filledQuantity": 5,
        "submittedQuantity": 5,
        "averageFillPrice": 100.0,
        "stopPrice": 99.5,
        "targetPrice": 101.0,
        "filledAt": (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
        "settingsVersion": "regime-settings-v1",
    }


def _completed_bar_payload(now: datetime, *, low: float, close: float) -> dict:
    candles = _candles(40, now=now, low=low, close=close)
    return {
        **_identity(),
        "completedBarTimestamp": candles[-1]["timestamp"],
        "publishedAt": candles[-1]["timestamp"],
        "marketData": {
            "symbol": "SPY",
            "primaryCandles": candles,
            "oneMinuteCandles": candles,
            "contextFeeds": {
                "quoteFreshness": {"status": "fresh", "ageMs": 250, "bid": close - 0.01, "ask": close + 0.01, "spreadBps": 2.0, "expectedFillQuantity": 100},
                "scheduledEconomicEvent": {"state": "none", "minutesUntilEvent": 999},
            },
        },
    }


def _candles(count: int, *, now: datetime, low: float, close: float) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    price = 100.0
    start = now - timedelta(minutes=count - 1)
    for index in range(count):
        timestamp = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        if index == count - 1:
            rows.append({"timestamp": timestamp, "open": 100.0, "high": 100.1, "low": low, "close": close, "volume": 150_000})
        else:
            price += 0.01
            rows.append({"timestamp": timestamp, "open": price - 0.02, "high": price + 0.08, "low": price - 0.08, "close": price, "volume": 150_000 + index})
    return rows


class _BrokerWithoutPositions:
    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent):  # type: ignore[no-untyped-def]
        raise AssertionError("completed-bar worker must not submit broker orders inline")

    def refresh_order(self, client_order_id: str):  # type: ignore[no-untyped-def]
        return None

    def cancel_order(self, client_order_id: str) -> bool:
        return True

    def refresh_positions(self) -> list[dict]:
        return []


class _SnapshotStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = dict(snapshot)
