from __future__ import annotations

import re
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app


ROOT = Path(__file__).resolve().parents[2]
REGIME_PACKAGE = ROOT / "backend" / "app" / "algorithms" / "regime"
FRONTEND_SRC = ROOT / "frontend" / "src"


class RegimeStep1AuthorityContractTest(unittest.TestCase):
    def test_api_metadata_never_declares_frontend_authoritative(self) -> None:
        client = TestClient(app)
        for path in ("/api/regime/backtests/status", "/api/regime/backend/inventory", "/api/regime/runtime/status", "/api/regime/backtests/routes"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            for key, value in _flatten(payload):
                text = f"{key}={value}".lower()
                self.assertNotRegex(text, r"authoritative.*(frontend|typescript)")
                self.assertNotRegex(text, r"(frontend|typescript).*authoritative")

        api_source = (REGIME_PACKAGE / "api.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("frontend/src/algorithms/regime", api_source)
        self.assertNotIn("typescript core", api_source)
        self.assertNotIn("return regime_service.evaluate(payload)", api_source)
        self.assertNotIn("run_regime_backtest(payload)", api_source)

    def test_regime_api_enqueues_decision_and_backtest_jobs(self) -> None:
        client = TestClient(app)
        decision_response = client.post(
            "/api/regime/evaluate",
            json={
                "marketData": {"symbol": "SPY", "primaryCandles": _candles(30)},
            },
        )
        self.assertEqual(decision_response.status_code, 202, decision_response.text)
        decision_receipt = decision_response.json()
        self.assertEqual(decision_receipt["algorithmId"], "regime")
        self.assertEqual(decision_receipt["jobKind"], "decision_evaluation")
        self.assertIn(decision_receipt["status"], {"queued", "running", "completed"})
        decision_job = _wait_for_job(client, decision_receipt["jobId"])
        self.assertEqual(decision_job["status"], "completed")
        self.assertEqual(decision_job["result"]["algorithmId"], "regime")
        self.assertEqual(decision_job["result"]["runtime"], "backend.app.algorithms.regime.execution_pipeline")

        backtest_response = client.post("/api/regime/backtests/run", json={"symbol": "SPY", "candles": _candles(30)})
        self.assertEqual(backtest_response.status_code, 202, backtest_response.text)
        backtest_job = _wait_for_job(client, backtest_response.json()["jobId"])
        self.assertEqual(backtest_job["status"], "completed")
        self.assertEqual(backtest_job["result"]["authoritativeEngine"], "backend.app.algorithms.regime.backtest.engine")

    def test_frontend_cannot_submit_authoritative_regime_results_or_orders(self) -> None:
        regime_algorithm_dir = FRONTEND_SRC / "algorithms" / "regime"
        self.assertFalse(regime_algorithm_dir.exists())

        source = "\n".join(path.read_text(encoding="utf-8") for path in FRONTEND_SRC.rglob("*.ts"))
        forbidden_patterns = (
            r"/api/regime/decisions/record",
            r"/api/regime/backtests/record",
            r"function\s+calculateRegimeDecision\s*\(",
            r"function\s+buildRegimeOrderIntent\s*\(",
            r"function\s+runRegimeBacktest\s*\(",
            r"appendTradeHistory\([^)]*\"regime\"",
        )
        for pattern in forbidden_patterns:
            self.assertIsNone(re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL), pattern)

        main_source = (FRONTEND_SRC / "main.ts").read_text(encoding="utf-8")
        self.assertIn("Regime UI is display-only", main_source)
        self.assertIn("function maybeAutoSubmitRegimeTargetOrder() {\n  return;\n}", main_source)

    def test_no_second_production_regime_decision_engine_exists(self) -> None:
        forbidden_definitions = re.compile(r"def\s+(calculate_regime_decision|execute_regime_pipeline|run_regime_backtest)\s*\(")
        for path in (ROOT / "backend" / "app").rglob("*.py"):
            if REGIME_PACKAGE in path.parents:
                continue
            source = path.read_text(encoding="utf-8")
            self.assertIsNone(forbidden_definitions.search(source), str(path))

        frontend_source = "\n".join(path.read_text(encoding="utf-8") for path in FRONTEND_SRC.rglob("*.ts"))
        self.assertNotIn("authoritativeEngine: \"frontend", frontend_source)
        self.assertNotIn("authoritativeRuntime: \"frontend", frontend_source)


def _wait_for_job(client: TestClient, job_id: str) -> dict:
    for _ in range(50):
        response = client.get(f"/api/regime/jobs/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Regime job did not finish: {job_id}")


def _flatten(value: object, prefix: str = "") -> list[tuple[str, object]]:
    if isinstance(value, dict):
        rows: list[tuple[str, object]] = []
        for key, item in value.items():
            rows.extend(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return rows
    if isinstance(value, list):
        rows = []
        for index, item in enumerate(value):
            rows.extend(_flatten(item, f"{prefix}.{index}"))
        return rows
    return [(prefix, value)]


def _candles(count: int) -> list[dict[str, float | str]]:
    start = datetime(2026, 7, 23, 16, 0, tzinfo=UTC)
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
                "volume": 120_000 + index,
            }
        )
    return rows


if __name__ == "__main__":
    unittest.main()
