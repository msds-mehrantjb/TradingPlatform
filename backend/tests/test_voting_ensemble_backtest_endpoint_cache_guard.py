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


class DedicatedReplayIsOneMinuteOnlyTest(unittest.TestCase):
    """A five-minute request must not be served the one-minute run under another name.

    The runner evaluates the one-minute tape and derives the five- and fifteen-minute bars
    itself, so asking it for "5Min" ran the same computation and stamped a different label.
    The two cached files were byte-identical apart from that label, and the panel showed
    the one-minute run on its five-minute tab.
    """

    def test_only_one_minute_names_a_dedicated_artifact(self) -> None:
        data_path = Path("continuous_1m.jsonl")
        one_minute = main.dedicated_voting_ensemble_cache_path(
            data_path=data_path, timeframe="1Min", start_date="2020-07-28", end_date="2026-09-01"
        )
        self.assertTrue(one_minute.name.startswith("voting_ensemble_dedicated_v2_1Min_"))

        for timeframe in ("5Min", "15Min", "1Hour"):
            with self.subTest(timeframe=timeframe), self.assertRaises(ValueError):
                main.dedicated_voting_ensemble_cache_path(
                    data_path=data_path, timeframe=timeframe, start_date="2020-07-28", end_date="2026-09-01"
                )

    def test_five_minutes_falls_through_to_the_legacy_engine_and_says_so(self) -> None:
        # The legacy engine reads the bars it is named for, so a five-minute result is a
        # real five-minute backtest, tagged so the panel's badge cannot claim it is live.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        five_minute = root / "continuous_5m.jsonl"
        five_minute.write_text('{"timestamp": "2026-09-01T13:30:00Z", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}\n', encoding="utf-8")
        manifest = {"symbol": "SPY", "manifest": str(root / "manifest.json"), "files": {"continuous5mJsonl": str(five_minute)}}

        with mock.patch.object(main, "run_voting_ensemble_backtest", return_value={"trades": []}) as legacy:
            result = main.cached_voting_ensemble_backtest(
                data_path=five_minute, manifest=manifest, timeframe="5Min", start_date="2020-07-28", end_date="2026-09-01"
            )

        legacy.assert_called_once()
        self.assertEqual(result["engine"], "legacy_main_py")
        self.assertFalse(result["matchesLiveAlgorithm"])
        self.assertEqual(result["timeframe"], "5Min")
        # And it is not written where the served dedicated artifact lives.
        self.assertFalse((root / "voting_ensemble_dedicated_v2_5Min_2020-07-28_2026-09-01.json").exists())


if __name__ == "__main__":
    unittest.main()
