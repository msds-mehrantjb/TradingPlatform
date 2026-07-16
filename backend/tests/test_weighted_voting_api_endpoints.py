from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import backend.app.algorithms.weighted_voting.api as weighted_voting_api
from backend.app.algorithms.weighted_voting.service import WeightedVotingService
from backend.app.main import app


SESSION_OPEN = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)


class WeightedVotingApiEndpointsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore()
        self.original_service = weighted_voting_api.WEIGHTED_VOTING_API_SERVICE
        weighted_voting_api.WEIGHTED_VOTING_API_SERVICE = WeightedVotingService(store=self.store)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        weighted_voting_api.WEIGHTED_VOTING_API_SERVICE = self.original_service

    def test_requested_routes_are_algorithm_specific_and_registered(self) -> None:
        openapi = self.client.get("/openapi.json").json()
        paths = set(openapi["paths"])
        expected = {
            "/api/weighted-voting/evaluate",
            "/api/weighted-voting/status",
            "/api/weighted-voting/config",
            "/api/weighted-voting/weights/active",
            "/api/weighted-voting/weights/history",
            "/api/weighted-voting/backtests",
            "/api/weighted-voting/backtests/{run_id}",
            "/api/weighted-voting/backtests/{run_id}/trades",
            "/api/weighted-voting/backtests/{run_id}/decisions",
            "/api/weighted-voting/backtests/{run_id}/strategy-performance",
            "/api/weighted-voting/daily-update/status",
            "/api/weighted-voting/daily-update/run",
        }

        self.assertTrue(expected.issubset(paths))
        self.assertTrue(all(path.startswith("/api/weighted-voting") for path in expected))
        self.assertIn("WeightedVotingErrorResponse", openapi["components"]["schemas"])

    def test_status_config_and_weights_endpoints_use_backend_store(self) -> None:
        status = self.client.get("/api/weighted-voting/status")
        config = self.client.get("/api/weighted-voting/config")
        weights = self.client.get("/api/weighted-voting/weights/active")
        history = self.client.get("/api/weighted-voting/weights/history")

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["algorithmId"], "weighted_voting")
        self.assertTrue(status.json()["finalAcceptance"]["complete"])
        self.assertEqual(status.json()["finalAcceptance"]["counts"]["pass"], 14)
        self.assertFalse(status.json()["rollout"]["automatic_submission_allowed"])
        self.assertFalse(status.json()["rollout"]["live_trading_allowed"])
        self.assertEqual(config.status_code, 200)
        self.assertEqual(config.json()["algorithmId"], "weighted_voting")
        self.assertEqual(weights.status_code, 200)
        self.assertEqual(weights.json()["weightState"]["algorithm_id"], "weighted_voting")
        self.assertEqual(history.status_code, 200)

    def test_invalid_configurations_are_rejected(self) -> None:
        response = self.client.put("/api/weighted-voting/config", json={"minimum_score": 1.5})

        self.assertEqual(response.status_code, 422)
        self.assertNotIn("voting_ensemble", self.store.snapshots)

    def test_put_config_and_evaluate_do_not_modify_other_algorithms(self) -> None:
        update = self.client.put("/api/weighted-voting/config", json={"minimum_score": 0.6, "minimum_edge": 0.13})
        evaluation = self.client.post("/api/weighted-voting/evaluate", json=evaluate_payload())

        self.assertEqual(update.status_code, 200, update.text)
        self.assertEqual(evaluation.status_code, 200, evaluation.text)
        self.assertEqual(evaluation.json()["algorithmId"], "weighted_voting")
        self.assertIn("decision", evaluation.json())
        self.assertIn("globalOrderProposal", evaluation.json())
        self.assertIn("globalGateApplication", evaluation.json())
        self.assertEqual(evaluation.json()["globalGateApplication"]["proposedQuantity"], evaluation.json()["globalOrderProposal"]["quantity"])
        self.assertLessEqual(evaluation.json()["globalGateApplication"]["globallyAllowedQuantity"], evaluation.json()["globalGateApplication"]["proposedQuantity"])
        self.assertTrue(all(key.startswith("weighted_voting.") for key in self.store.snapshots))

    def test_backtest_endpoints_store_and_return_run_collections(self) -> None:
        response = self.client.post("/api/weighted-voting/backtests", json=backtest_payload("api-run-1"))
        self.assertEqual(response.status_code, 200, response.text)

        run = self.client.get("/api/weighted-voting/backtests/api-run-1")
        trades = self.client.get("/api/weighted-voting/backtests/api-run-1/trades")
        decisions = self.client.get("/api/weighted-voting/backtests/api-run-1/decisions")
        performance = self.client.get("/api/weighted-voting/backtests/api-run-1/strategy-performance")
        missing = self.client.get("/api/weighted-voting/backtests/missing-run")

        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["run"]["run_id"], "api-run-1")
        self.assertEqual(trades.status_code, 200)
        self.assertIn("trades", trades.json())
        self.assertEqual(decisions.status_code, 200)
        self.assertIn("decisions", decisions.json())
        self.assertEqual(performance.status_code, 200)
        self.assertIn("strategyPerformance", performance.json())
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["detail"]["algorithm_id"], "weighted_voting")

    def test_daily_update_endpoints_are_weighted_voting_specific(self) -> None:
        initial = self.client.get("/api/weighted-voting/daily-update/status")
        run = self.client.post(
            "/api/weighted-voting/daily-update/run",
            json={
                "session_date": "2026-07-14",
                "symbol": "SPY",
                "completed_at": "2026-07-14T21:10:00+00:00",
                "candles": candle_rows(),
            },
        )
        after = self.client.get("/api/weighted-voting/daily-update/status")

        self.assertEqual(initial.status_code, 200)
        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["algorithmId"], "weighted_voting")
        self.assertEqual(after.status_code, 200)
        self.assertNotEqual(after.json()["dailyUpdate"]["status"], "never_run")
        self.assertTrue(all(key.startswith("weighted_voting.") for key in self.store.snapshots))


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


def evaluate_payload() -> dict:
    rows = candle_rows(count=95)
    return {
        "symbol": "SPY",
        "data_timestamp": rows[-1]["timestamp"],
        "candles": rows,
        "bid": rows[-1]["close"] - 0.01,
        "ask": rows[-1]["close"] + 0.01,
        "account_equity": 100000,
        "available_buying_power": 100000,
        "capital_available": 100000,
    }


def backtest_payload(run_id: str) -> dict:
    return {
        "run_id": run_id,
        "symbol": "SPY",
        "candles": candle_rows(),
    }


def candle_rows(count: int = 390) -> list[dict]:
    rows = []
    for index in range(count):
        base = 100.0 + index * 0.03
        rows.append(
            {
                "timestamp": (SESSION_OPEN + timedelta(minutes=index)).isoformat(),
                "open": base,
                "high": base + 0.45,
                "low": base - 0.18,
                "close": base + 0.08,
                "volume": 200000 if index != 5 else 5000,
            }
        )
    return rows


if __name__ == "__main__":
    unittest.main()
