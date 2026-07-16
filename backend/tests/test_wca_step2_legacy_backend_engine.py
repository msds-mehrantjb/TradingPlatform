from __future__ import annotations

import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.algorithms.wca.contracts import WcaEvaluateRequest
from backend.app.algorithms.wca.engine import evaluate_wca_legacy
from backend.app.main import app


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "wca" / "golden_snapshots.json"


class WcaStep2LegacyBackendEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshots = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["snapshots"]

    def test_backend_matches_legacy_frontend_golden_fixtures(self) -> None:
        for snapshot in self.snapshots:
            with self.subTest(snapshot=snapshot["id"]):
                response = evaluate_wca_legacy(WcaEvaluateRequest.model_validate(snapshot))
                expected = snapshot["expected"]
                payload = response.model_dump(mode="json", by_alias=True, exclude_none=True)

                self.assertEqual(payload["rawDecision"], expected["rawDecisionLabel"])
                self.assertEqual(payload["rawSignal"], expected["rawSignal"])
                self.assertEqual(payload["effectiveDecision"], expected["decisionLabel"])
                self.assertEqual(payload["signal"], expected["signal"])
                self.assertEqual(payload["activeStrategyCount"], expected["activeStrategyCount"])
                self.assertEqual(payload["sizingResult"]["finalQuantity"], expected["positionSize"])
                self.assertEqual([row["label"] for row in payload["localGateResult"] if row["status"] == "fail"], expected["failedFilters"])
                self.assertEqual(payload.get("proposedOrder") is not None, expected["signal"] != "Hold" and expected["positionSize"] > 0)

                for key in (
                    "buyScore",
                    "sellScore",
                    "netScore",
                    "activeWeight",
                    "normalizedNetScore",
                    "buyWeight",
                    "sellWeight",
                    "buyAgreement",
                    "sellAgreement",
                    "buyAverageConfidence",
                    "sellAverageConfidence",
                ):
                    self.assertAlmostEqual(payload[key], expected[key], places=4, msg=key)
                for key in (
                    "signalStrength",
                    "sizeMultiplier",
                    "riskDollars",
                    "stopDistance",
                    "sharesByRisk",
                    "sharesByOrder",
                    "sharesByCapital",
                    "sharesByBuyingPower",
                    "sharesByLiquidity",
                    "finalQuantity",
                    "availableBuyingPower",
                    "maxPositionDollars",
                    "currentPositionValue",
                ):
                    self.assertAlmostEqual(payload["sizingResult"][key], expected["sizing"][key], places=4, msg=key)

    def test_api_evaluate_and_configuration_routes(self) -> None:
        client = TestClient(app)
        configuration = client.get("/api/wca/configuration")
        self.assertEqual(configuration.status_code, 200)
        self.assertEqual(configuration.json()["engineVersion"], "wca_legacy_compatible_v1")
        updated = client.put(
            "/api/wca/configuration",
            json={"decisionSettings": {**configuration.json()["decisionSettings"], "buyThreshold": 0.36}},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["decisionSettings"]["buyThreshold"], 0.36)

        response = client.post("/api/wca/evaluate", json=self.snapshots[0])
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["algorithmId"], "wca")
        self.assertEqual(body["engineVersion"], "wca_legacy_compatible_v1")
        self.assertIn("strategyEvaluations", body)
        self.assertIn("localGateResult", body)
        self.assertIn("sizingResult", body)

    def test_api_rejects_missing_strategy_snapshot(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/api/wca/evaluate",
            json={"symbol": "SPY", "timestamp": "2026-01-05T14:35:00+00:00", "marketSnapshot": {"close": 500}},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("strategySignals", response.text)

    def test_openapi_includes_wca_step2_routes(self) -> None:
        client = TestClient(app)
        response = client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]
        self.assertIn("/api/wca/evaluate", paths)
        self.assertIn("/api/wca/configuration", paths)


if __name__ == "__main__":
    unittest.main()
