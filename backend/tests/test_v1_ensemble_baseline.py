from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import mock_open, patch

from backend.app import main
from backend.app.config import ApplicationConfig


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "v1_ensemble_fixtures.json"


def fixture_candles(pattern: str, count: int = 60) -> list[dict]:
    rows = []
    start = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    for index in range(count):
        if pattern == "buy":
            close = 100 + index * 0.08
            open_price = close - 0.04
            high = close + 0.08
            low = open_price - 0.04
            volume = 100000 + index * 2000
        elif pattern == "sell":
            close = 100 - index * 0.08
            open_price = close + 0.04
            high = open_price + 0.04
            low = close - 0.08
            volume = 100000 + index * 2000
        elif pattern == "hold":
            close = 100 + ((index % 2) - 0.5) * 0.02
            open_price = 100 - ((index % 2) - 0.5) * 0.01
            high = max(open_price, close) + 0.03
            low = min(open_price, close) - 0.03
            volume = 100000
        else:
            raise ValueError(f"Unknown fixture pattern: {pattern}")

        rows.append(
            {
                "provider": "fixture",
                "feed": "iex",
                "symbol": "SPY",
                "timeframe": "1Min",
                "timestamp": (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
                "open": round(open_price, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close, 4),
                "volume": volume,
                "trade_count": None,
                "vwap": round((high + low + close) / 3, 4),
            }
        )
    return rows


class V1EnsembleBaselineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_application_config_defaults_activate_voting_ensemble_v2_and_keep_paper_flags_safe(self) -> None:
        config = ApplicationConfig().as_dict()
        endpoint_config = main.application_config()

        self.assertEqual(config["version"], "application-config-v1")
        self.assertEqual(endpoint_config, config)
        self.assertEqual(len(config["configurationHash"]), 12)
        self.assertEqual(
            config["featureFlags"],
            {
                "strategyEngineV2Enabled": True,
                "familyEnsembleV2Enabled": True,
                "metaModelV2Enabled": False,
                "dynamicTradingPolicyEnabled": False,
                "globalGateEngineEnabled": True,
                "mlFamilyWeightingEnabled": False,
                "weightedVotingV2Enabled": True,
                "weightedVotingAutoSubmitEnabled": False,
                "wcaBackendEngineEnabled": True,
                "wcaCorrectedStrategyCatalogEnabled": True,
                "wcaDynamicWeightsEnabled": True,
                "wcaDynamicProfileEnabled": True,
                "wcaBackendBacktestEnabled": True,
                "wcaPaperExecutionEnabled": False,
                "regimeV2Enabled": True,
                "regimeDynamicProfileEnabled": True,
                "regimeMlMode": "shadow",
                "regimeGlobalRiskManagerEnabled": True,
                "regimeShortEntriesEnabled": False,
            },
        )

    def test_v1_vote_fixtures_replay(self) -> None:
        for case in self.fixture["voteCases"]:
            with self.subTest(case=case["name"]):
                candles = fixture_candles(case["pattern"], self.fixture["candleFixture"]["count"])
                actual_votes = main.historical_strategy_votes(candles, case["priorClose"], timeframe="1Min")
                actual_summary = main.historical_vote_summary(candles, case["priorClose"], timeframe="1Min")

                expected = case["expected"]
                self.assertEqual(actual_votes, expected["votes"])
                self.assertEqual(
                    actual_summary,
                    {
                        "signal": expected["signal"],
                        "buyVotes": expected["buyVotes"],
                        "sellVotes": expected["sellVotes"],
                        "holdVotes": expected["holdVotes"],
                        "voteStrength": expected["voteStrength"],
                        "regime": expected["regime"],
                    },
                )

    def test_v1_position_sizing_fixtures_replay(self) -> None:
        config = main.dynamic_risk_config(self.fixture["riskConfigInput"])
        self.assertEqual(main.risk_config_hash(config), self.fixture["expectedRiskConfigHash"])

        for case in self.fixture["positionSizingCases"]:
            with self.subTest(case=case["name"]):
                shares, planned_risk, mode = main.position_size_for_config(
                    config,
                    equity=case["equity"],
                    entry_price=case["entryPrice"],
                    stop_distance=case["stopDistance"],
                )
                self.assertEqual(shares, case["expected"]["shares"])
                self.assertEqual(planned_risk, case["expected"]["plannedRisk"])
                self.assertEqual(mode, case["expected"]["mode"])

    def test_v1_order_fixtures_replay(self) -> None:
        config = main.dynamic_risk_config(self.fixture["riskConfigInput"])
        for case_name in ("sizedOrderCase", "blockedOrderCase"):
            case = self.fixture[case_name]
            with self.subTest(case=case_name):
                candles = fixture_candles(case["pattern"], self.fixture["candleFixture"]["count"])
                vote_summary = main.historical_vote_summary(candles, case["priorClose"], timeframe="1Min")
                order = main.open_risk_managed_trade(
                    side=case["side"],
                    candle=candles[-1],
                    opening_range=main.opening_range_values(candles, 15),
                    equity=case["equity"],
                    session_date=case["sessionDate"],
                    vote_summary=vote_summary,
                    risk_config=config,
                )

                if case["expected"] is None:
                    self.assertIsNone(order)
                    continue

                self.assertIsNotNone(order)
                for key, value in case["expected"].items():
                    self.assertEqual(order[key], value)

    def test_v1_decision_snapshot_fixture_replay(self) -> None:
        case = self.fixture["decisionSnapshotCase"]
        original_dir = main.DECISION_SNAPSHOT_DIR
        main.DECISION_SNAPSHOT_DIR = Path("C:/fixture/decision_snapshots")
        with (
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.open", mock_open()),
            patch.object(main, "write_json", side_effect=lambda path, data: str(path)),
        ):
            try:
                result = main.save_decision_snapshot({"snapshot": case["payload"]})
            finally:
                main.DECISION_SNAPSHOT_DIR = original_dir

        self.assertTrue(result["ok"])
        self.assertEqual(result["sessionDate"], case["expected"]["sessionDate"])
        self.assertEqual(result["symbol"], case["expected"]["symbol"])
        self.assertEqual(Path(result["latestPath"]).name, case["expected"]["latestFilename"])
        self.assertEqual(Path(result["path"]).name, case["expected"]["jsonlFilename"])


if __name__ == "__main__":
    unittest.main()
