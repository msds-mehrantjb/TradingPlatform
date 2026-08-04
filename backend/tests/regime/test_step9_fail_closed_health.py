from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.algorithms.regime.position_manager import RegimePositionManager
from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime import RegimeBackgroundJobManager
from backend.app.algorithms.regime.runtime_events import RegimeFinalisedBarEvent
from backend.app.algorithms.regime.runtime_health import REGIME_HEALTH_COMPONENTS
from backend.app.algorithms.regime.runtime_supervisor import RegimeRuntimeSupervisor, RegimeRuntimeSupervisorConfig
from backend.app.algorithms.regime.service import RegimeApplicationService


TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "tmp" / "regime_step9"


def test_step9_health_api_exposes_all_required_components() -> None:
    supervisor = _supervisor(_repository("components"))

    health = supervisor.health()

    assert set(REGIME_HEALTH_COMPONENTS).issubset(set(health["componentHealth"]))
    assert health["componentHealth"]["database"]["status"] == "unknown"
    assert health["newEntriesBlocked"] is True


def test_step9_missing_paper_gateway_marks_broker_unhealthy_and_persists_failure() -> None:
    repository = _repository("paper-gateway-missing")
    identity = _paper_identity()
    supervisor = RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository),
        config=RegimeRuntimeSupervisorConfig(
            default_algorithm_instance_id=identity["algorithmInstanceId"],
            default_account_id=identity["accountId"],
            default_runtime_mode="paper",
            maintenance_interval_seconds=60,
            heartbeat_interval_seconds=60,
        ),
    )

    result = supervisor.process_execution_outbox_once()
    health = supervisor.health()
    events = repository.read_owned_records("regime_runtime_events", identity)

    assert result["processed"] is False
    assert "regime.execution.paper_gateway_unavailable" in result["reasonCodes"]
    assert health["componentHealth"]["paper_broker"]["status"] == "unhealthy"
    assert "regime.execution.paper_gateway_unavailable" in health["entryBlockReasonCodes"]
    assert any(event.get("eventType") == "runtime_component_failure" and event.get("payload", {}).get("component") == "paper_broker" for event in events)


def test_step9_settings_failure_blocks_entries_and_is_visible_in_health() -> None:
    async def scenario() -> None:
        repository = BrokenSettingsRepository(f"sqlite:///{_db_path('settings-failure')}")
        supervisor = _supervisor(repository)
        event = RegimeFinalisedBarEvent.from_payload(_completed_bar_payload())

        result = await supervisor.process_finalised_bar_event(event)
        health = supervisor.health()
        events = repository.read_owned_records("regime_runtime_events", _identity())

        assert result["processed"] is False
        assert "regime.runtime.settings_unavailable" in result["reasonCodes"]
        assert health["componentHealth"]["settings_repository"]["status"] == "unhealthy"
        assert "regime.runtime.settings_unavailable" in health["entryBlockReasonCodes"]
        assert any(event.get("eventType") == "runtime_component_failure" and event.get("payload", {}).get("component") == "settings_repository" for event in events)

    asyncio.run(scenario())


def test_step9_backtest_recovery_failure_blocks_new_backtest_jobs_only() -> None:
    repository = BrokenBacktestRecoveryRepository(f"sqlite:///{_db_path('backtest-recovery')}")
    manager = RegimeBackgroundJobManager(lambda: RegimeApplicationService(repository), max_concurrent_backtests=1)

    manager.start()
    _wait_for(lambda: manager.status()["startupRecoveryFailure"] is not None)
    receipt = manager.enqueue("backtest", {"symbol": "SPY", "candles": _candles(5)})
    status = manager.status()

    assert receipt["accepted"] is False
    assert receipt["status"] == "failed"
    assert "regime.backtest.new_jobs_blocked_recovery_unhealthy" in receipt["reasonCodes"]
    assert status["componentHealth"]["backtest_worker"]["status"] == "unhealthy"
    assert status["newBacktestJobsBlocked"] is True


def test_step9_backtest_status_read_failure_is_not_silent_success() -> None:
    repository = BrokenBacktestReadRepository(f"sqlite:///{_db_path('backtest-read')}")
    manager = RegimeBackgroundJobManager(lambda: RegimeApplicationService(repository), max_concurrent_backtests=1)

    result = manager.get("regime-backtest-read-failure")

    assert result["status"] == "failed"
    assert "regime.backtest.job_status_read_failed" in result["reasonCodes"]
    assert result["componentHealth"]["database"]["status"] == "unhealthy"


def test_step9_unresolved_position_discrepancy_blocks_entries_but_keeps_protective_work_possible() -> None:
    repository = _repository("position-discrepancy")
    manager = RegimePositionManager(repository)
    position = manager.apply_fill_observation(_identity(), _fill())["position"]

    reconciliation = manager.reconcile_broker_observations(_identity(), [{"algorithmId": "regime", "positionId": position["positionId"], "quantity": 1}])
    protected = manager.evaluate_position(
        _identity(),
        repository.latest_open_regime_positions(_identity())[0],
        candle={"timestamp": "2026-07-23T15:31:00Z", "open": 100.0, "high": 100.1, "low": 99.0, "close": 99.2, "volume": 100_000},
        settings_snapshot={"settingsVersion": "settings-v1", "flattenTimeEt": "15:55"},
        confirmed_regime="strong_uptrend",
        entry_paused=True,
    )

    assert reconciliation["reconciled"] is False
    assert reconciliation["blockNewEntries"] is True
    assert "regime.position.reconciliation_discrepancy" in reconciliation["reasonCodes"]
    assert protected["action"] == "exit"
    assert protected["exitAction"]["reason"] == "broker_reconciliation_discrepancy"


def _repository(label: str) -> RegimeRepository:
    return RegimeRepository(f"sqlite:///{_db_path(label)}")


def _db_path(label: str) -> Path:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return TEST_TMP_ROOT / f"{label}_{uuid4().hex}.sqlite3"


def _supervisor(repository: RegimeRepository) -> RegimeRuntimeSupervisor:
    return RegimeRuntimeSupervisor(
        service=RegimeApplicationService(repository),
        config=RegimeRuntimeSupervisorConfig(default_runtime_mode="shadow", maintenance_interval_seconds=60, heartbeat_interval_seconds=60),
    )


def _identity() -> dict[str, str]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-default",
        "accountId": "default",
        "runtimeMode": "shadow",
        "symbol": "SPY",
    }


def _paper_identity() -> dict[str, str]:
    return {
        "algorithmId": "regime",
        "algorithmInstanceId": "regime-paper-default",
        "accountId": "paper-account-123",
        "runtimeMode": "paper",
        "symbol": "SPY",
    }


def _completed_bar_payload() -> dict:
    candles = _candles(40)
    return {
        "algorithmInstanceId": "regime-default",
        "accountId": "default",
        "runtimeMode": "shadow",
        "symbol": "SPY",
        "completedBarTimestamp": candles[-1]["timestamp"],
        "publishedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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


def _candles(count: int) -> list[dict[str, float | str]]:
    price = 100.0
    start = datetime(2026, 7, 23, 14, 0, tzinfo=UTC)
    rows: list[dict[str, float | str]] = []
    for index in range(count):
        price += 0.03
        rows.append(
            {
                "timestamp": (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
                "open": round(price - 0.02, 4),
                "high": round(price + 0.08, 4),
                "low": round(price - 0.08, 4),
                "close": round(price, 4),
                "volume": 125_000,
            }
        )
    return rows


def _fill() -> dict:
    return {
        **_identity(),
        "algorithmId": "regime",
        "decisionId": "decision-step9",
        "orderIntentId": "intent-step9",
        "fillId": "fill-step9",
        "brokerOrderId": "broker-step9",
        "symbol": "SPY",
        "side": "Buy",
        "filledQuantity": 5,
        "submittedQuantity": 5,
        "averageFillPrice": 100.0,
        "stopPrice": 99.5,
        "targetPrice": 101.0,
        "filledAt": "2026-07-23T15:30:00Z",
        "settingsVersion": "settings-v1",
    }


def _wait_for(predicate, *, timeout_seconds: float = 5.0) -> None:
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition was not met")


class BrokenSettingsRepository(RegimeRepository):
    def ensure_active_settings_snapshot(self, identity):  # type: ignore[no-untyped-def]
        raise RuntimeError("settings store offline")


class BrokenBacktestRecoveryRepository(RegimeRepository):
    def recover_abandoned_backtest_jobs(self, *, owner_id: str, stale_after_seconds: int = 120):  # type: ignore[no-untyped-def]
        raise RuntimeError("backtest recovery database offline")


class BrokenBacktestReadRepository(RegimeRepository):
    def read_backtest_job(self, job_id: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("backtest job database offline")
