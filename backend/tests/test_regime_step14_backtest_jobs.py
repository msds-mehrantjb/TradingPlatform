from __future__ import annotations

import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.algorithms.regime.repository import RegimeRepository
from backend.app.algorithms.regime.runtime import RegimeBackgroundJobManager
from backend.app.algorithms.regime.service import RegimeApplicationService
from backend.app.main import app


class RegimeStep14BacktestJobsTest(unittest.TestCase):
    def test_backtest_api_returns_promptly_while_background_job_runs(self) -> None:
        client = TestClient(app)
        payload = {"symbol": "SPY", "algorithmInstanceId": f"step14-api-{uuid4().hex}", "candles": fixture_candles(35)}

        def slow_backtest(self: RegimeApplicationService, request: dict) -> dict:
            time.sleep(0.35)
            return {
                "algorithmId": "regime",
                "authoritativeEngine": "backend.app.algorithms.regime.backtest.engine",
                "engineVersion": "regime_backtest_v3_backend",
                "runtimeMode": "backtest",
                "symbol": "SPY",
                "metrics": {"netProfit": 0.0, "tradeCount": 0, "decisionCount": len(request.get("candles") or [])},
                "totalPnl": 0.0,
            }

        with patch.object(RegimeApplicationService, "run_backtest", slow_backtest):
            started = time.perf_counter()
            response = client.post("/api/regime/backtests/run", json=payload)
            elapsed = time.perf_counter() - started
            self.assertEqual(response.status_code, 202, response.text)
            self.assertLess(elapsed, 0.5)

            job_id = response.json()["jobId"]
            status = client.get(f"/api/regime/backtests/jobs/{job_id}")
            self.assertEqual(status.status_code, 200, status.text)
            self.assertIn(status.json()["status"], {"queued", "running", "completed"})

            final = wait_for_job(client, job_id)
            self.assertEqual(final["status"], "completed")
            self.assertEqual(final["resultMetadata"]["authoritativeEngine"], "backend.app.algorithms.regime.backtest.engine")

    def test_cancel_queued_backtest_job_safely(self) -> None:
        repository = temp_repository("cancel")
        manager = RegimeBackgroundJobManager(lambda: SlowBacktestService(repository), max_concurrent_backtests=1)
        first = manager.enqueue("backtest", {"symbol": "SPY", "algorithmInstanceId": f"step14-cancel-a-{uuid4().hex}", "candles": fixture_candles(30)})
        second = manager.enqueue("backtest", {"symbol": "SPY", "algorithmInstanceId": f"step14-cancel-b-{uuid4().hex}", "candles": fixture_candles(30)})

        cancelled = manager.cancel(second["jobId"])

        self.assertTrue(cancelled["cancelAccepted"])
        final_first = wait_for_manager(manager, first["jobId"])
        final_second = wait_for_manager(manager, second["jobId"], terminal={"completed", "failed", "cancelled"})
        self.assertEqual(final_first["status"], "completed")
        self.assertEqual(final_second["status"], "cancelled")

    def test_backtest_job_uses_backtest_namespace_and_does_not_mutate_paper_inventory(self) -> None:
        repository = temp_repository("namespace")
        manager = RegimeBackgroundJobManager(lambda: RegimeApplicationService(repository=repository), max_concurrent_backtests=1)
        receipt = manager.enqueue(
            "backtest",
            {
                "symbol": "SPY",
                "algorithmInstanceId": f"step14-namespace-{uuid4().hex}",
                "runtimeMode": "paper",
                "inventorySnapshot": {"openPosition": {"quantity": 999, "runtimeMode": "paper"}},
                "candles": fixture_candles(35),
            },
        )

        final = wait_for_manager(manager, receipt["jobId"])
        durable = repository.read_backtest_job(receipt["jobId"])
        counts = repository.table_counts()

        self.assertEqual(final["status"], "completed")
        self.assertEqual(durable["runtimeMode"], "backtest")
        self.assertEqual(durable["payload"]["runtimeMode"], "backtest")
        self.assertNotIn("inventorySnapshot", durable["payload"])
        self.assertGreaterEqual(counts["regime_backtest_jobs"], 1)
        self.assertEqual(counts["regime_positions"], 0)
        self.assertEqual(counts["regime_trades"], 0)


class SlowBacktestService(RegimeApplicationService):
    def run_backtest(self, payload: dict) -> dict:
        time.sleep(0.25)
        return {
            "algorithmId": "regime",
            "authoritativeEngine": "backend.app.algorithms.regime.backtest.engine",
            "engineVersion": "regime_backtest_v3_backend",
            "runtimeMode": "backtest",
            "symbol": "SPY",
            "metrics": {"netProfit": 0.0, "tradeCount": 0, "decisionCount": len(payload.get("candles") or [])},
            "totalPnl": 0.0,
        }


def wait_for_job(client: TestClient, job_id: str) -> dict:
    for _ in range(80):
        response = client.get(f"/api/regime/backtests/jobs/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"completed", "failed", "cancelled", "quarantined"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Regime backtest job did not finish: {job_id}")


def wait_for_manager(manager: RegimeBackgroundJobManager, job_id: str, terminal: set[str] | None = None) -> dict:
    terminal_statuses = terminal or {"completed", "failed", "cancelled", "quarantined"}
    for _ in range(80):
        payload = manager.get(job_id)
        if payload["status"] in terminal_statuses:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Regime backtest job did not finish: {job_id}")


def fixture_candles(count: int) -> list[dict[str, float | str]]:
    start = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)
    price = 100.0
    rows: list[dict[str, float | str]] = []
    for index in range(count):
        price += 0.05
        rows.append(
            {
                "timestamp": (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
                "open": price - 0.02,
                "high": price + 0.10,
                "low": price - 0.10,
                "close": price,
                "volume": 150_000 + index,
            }
        )
    return rows


def temp_repository(label: str) -> RegimeRepository:
    root = Path(__file__).resolve().parent / "tmp" / "regime_step14"
    root.mkdir(parents=True, exist_ok=True)
    return RegimeRepository(f"sqlite:///{root / f'{label}_{uuid4().hex}.sqlite'}")


if __name__ == "__main__":
    unittest.main()
