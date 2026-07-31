from __future__ import annotations

import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.algorithms.regime.contracts import (
    REGIME_ALLOWED_RUNTIME_MODE_VALUES,
    RegimeRuntimeMode,
    normalize_regime_runtime_mode,
)
from backend.app.algorithms.regime.runtime import RegimeBackgroundJobManager
from backend.app.algorithms.regime.service import regime_backend_inventory
from backend.app.main import app


ROOT = Path(__file__).resolve().parents[3]


class RegimePaperOnlyAuthorityBoundaryTest(unittest.TestCase):
    def test_runtime_mode_contract_rejects_live_and_unknown_modes(self) -> None:
        self.assertEqual(
            REGIME_ALLOWED_RUNTIME_MODE_VALUES,
            ("shadow", "paper", "backtest", "replay"),
        )
        self.assertEqual(normalize_regime_runtime_mode("shadow"), RegimeRuntimeMode.SHADOW)
        self.assertEqual(normalize_regime_runtime_mode("paper"), RegimeRuntimeMode.PAPER)
        self.assertEqual(normalize_regime_runtime_mode("backtest"), RegimeRuntimeMode.BACKTEST)
        self.assertEqual(normalize_regime_runtime_mode("replay"), RegimeRuntimeMode.REPLAY)

        with self.assertRaisesRegex(ValueError, "live runtime mode is disabled"):
            normalize_regime_runtime_mode("live")
        with self.assertRaisesRegex(ValueError, "Unsupported Regime runtime mode"):
            normalize_regime_runtime_mode("simulation")

    def test_http_boundary_rejects_live_unknown_and_authoritative_payloads(self) -> None:
        client = TestClient(app)

        live = client.post("/api/regime/evaluate", json={"runtimeMode": "live", "marketData": _market_data()})
        self.assertEqual(live.status_code, 400, live.text)
        self.assertIn("regime.api.runtime_mode_rejected", live.json()["detail"]["reasonCodes"])

        unknown = client.post("/api/regime/backtests/run", json={"runtimeMode": "simulation", "candles": _candles()})
        self.assertEqual(unknown.status_code, 400, unknown.text)
        self.assertIn("regime.api.runtime_mode_rejected", unknown.json()["detail"]["reasonCodes"])

        frontend_decision = client.post("/api/regime/evaluate", json={"marketData": _market_data(), "orderIntent": {"side": "Buy"}})
        self.assertEqual(frontend_decision.status_code, 400, frontend_decision.text)
        self.assertIn("regime.api.frontend_authoritative_payload_rejected", frontend_decision.json()["detail"]["reasonCodes"])

        caller_state = client.post("/api/regime/evaluate", json={"marketData": _market_data(), "account": {"availableBuyingPower": 25_000}})
        self.assertEqual(caller_state.status_code, 400, caller_state.text)
        self.assertIn("account", caller_state.json()["detail"]["forbiddenKeys"])

    def test_direct_recording_endpoints_are_not_authoritative_submission_paths(self) -> None:
        client = TestClient(app)

        decision = client.post("/api/regime/decisions/record", json={"algorithmId": "regime", "decision": {"signal": "Buy"}})
        self.assertEqual(decision.status_code, 410, decision.text)
        self.assertIn("regime.api.direct_decision_recording_disabled", decision.json()["detail"]["reasonCodes"])

        backtest = client.post("/api/regime/backtests/record", json={"algorithmId": "regime", "result": {"netProfit": 1.0}})
        self.assertEqual(backtest.status_code, 410, backtest.text)
        self.assertIn("regime.api.direct_backtest_recording_disabled", backtest.json()["detail"]["reasonCodes"])

    def test_background_job_manager_rejects_live_before_starting_work(self) -> None:
        manager = RegimeBackgroundJobManager()

        with self.assertRaisesRegex(ValueError, "live runtime mode is disabled"):
            manager.enqueue("decision_evaluation", {"runtimeMode": "live", "marketData": _market_data()})

    def test_backend_package_declares_only_authoritative_runtime(self) -> None:
        inventory = regime_backend_inventory()

        self.assertEqual(inventory["algorithmId"], "regime")
        self.assertEqual(inventory["productionDecisionCore"], "backend.app.algorithms.regime.execution_pipeline.execute_regime_pipeline")
        self.assertEqual(inventory["productionBacktestCore"], "backend.app.algorithms.regime.backtest.engine.run_regime_backtest")
        self.assertEqual(inventory["frontendDecisionSubmissionAllowed"], False)
        self.assertEqual(tuple(inventory["allowedRuntimeModes"]), ("shadow", "paper", "backtest", "replay"))

    def test_frontend_regime_transport_does_not_send_settings_account_or_authoritative_results(self) -> None:
        main_source = (ROOT / "frontend" / "src" / "main.ts").read_text(encoding="utf-8")
        api_source = (ROOT / "frontend" / "src" / "features" / "regime" / "api.ts").read_text(encoding="utf-8")
        payload_fn = re.search(
            r"function backendRegimeEvaluationPayload\(market: RegimeFrontendMarketContext\)(.*?)\n}\n\nfunction backendRegimeEvaluationKey",
            main_source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(payload_fn)
        payload_body = payload_fn.group(1)

        self.assertIn('requestType: "diagnostic_shadow"', payload_body)
        self.assertNotIn("marketData", payload_body)
        self.assertNotIn("settings:", payload_body)
        self.assertNotIn("account:", payload_body)
        self.assertIn("AUTHORITATIVE_REGIME_PAYLOAD_KEYS", api_source)
        self.assertIn("DIRECT_EVALUATION_DATA_KEYS", api_source)
        self.assertIn("backend workers own decisions", api_source)


def _market_data() -> dict[str, object]:
    return {"symbol": "SPY", "primaryCandles": _candles()}


def _candles() -> list[dict[str, float | str]]:
    return [
        {"timestamp": "2026-07-23T16:00:00Z", "open": 100.0, "high": 100.2, "low": 99.9, "close": 100.1, "volume": 125000},
        {"timestamp": "2026-07-23T16:01:00Z", "open": 100.1, "high": 100.3, "low": 100.0, "close": 100.2, "volume": 125500},
    ]


if __name__ == "__main__":
    unittest.main()
