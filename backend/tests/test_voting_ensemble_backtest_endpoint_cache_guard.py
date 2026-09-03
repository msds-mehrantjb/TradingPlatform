from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.app import main


class VotingEnsembleBacktestEndpointCacheGuardTest(unittest.TestCase):
    """The full-range endpoint reports a missing dedicated replay; it never computes one.

    The dedicated replay takes about 85 minutes on real data. When the endpoint computed
    it inside a request thread, every page load against a fresh dataset started another
    one: the frontend gave up after 20 seconds, the thread kept going, and a handful of
    reloads pinned the backend for hours. A miss is a 409 that names the file and the job
    that produces it; a hit is served from the cache untouched.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.one_minute = root / "continuous_1m.jsonl"
        self.one_minute.write_text('{"timestamp": "2026-09-01T13:30:00Z", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}\n', encoding="utf-8")
        self.manifest = {
            "symbol": "SPY",
            "manifest": str(root / "manifest.json"),
            "files": {"continuous1mJsonl": str(self.one_minute)},
        }
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _get(self):
        with mock.patch.object(main, "backtest_data_manifest_for_range", return_value=self.manifest), mock.patch.object(
            main, "latest_ml_artifact_job_status", return_value={"status": "queued", "jobId": "job-1"}
        ):
            return self.client.get(
                "/api/voting-ensemble/backtest",
                params={"symbol": "SPY", "timeframe": "1Min", "start_date": "2020-07-28", "end_date": "2026-09-01", "max_trades": 20},
            )

    def test_a_missing_dedicated_cache_is_reported_not_computed(self) -> None:
        with mock.patch.object(main, "cached_voting_ensemble_backtest", side_effect=AssertionError("must not compute in a request")):
            response = self._get()

        self.assertEqual(response.status_code, 409)
        detail = response.json()["detail"]
        self.assertIn("has not been produced", detail["message"])
        self.assertTrue(detail["expectedPath"].endswith("voting_ensemble_dedicated_v2_1Min_2020-07-28_2026-09-01.json"))
        self.assertEqual(detail["latestJob"]["status"], "queued")

    def test_an_existing_dedicated_cache_is_served(self) -> None:
        cache_path = main.dedicated_voting_ensemble_cache_path(
            data_path=self.one_minute, timeframe="1Min", start_date="2020-07-28", end_date="2026-09-01"
        )
        cache_path.write_text(
            json.dumps(
                {
                    "trades": [{"side": "Long", "netPnl": 1.0}],
                    "totalTrades": 1,
                    "engine": "voting_ensemble_pipeline",
                    "matchesLiveAlgorithm": True,
                    "mlReplaySnapshots": {"rowCount": 1},
                    "stageResultsJsonl": str(self.one_minute),
                }
            ),
            encoding="utf-8",
        )

        response = self._get()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["engine"], "voting_ensemble_pipeline")
        self.assertEqual(body["totalTrades"], 1)
        self.assertEqual(body["timeframe"], "1Min")


if __name__ == "__main__":
    unittest.main()
