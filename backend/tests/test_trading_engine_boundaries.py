from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend.app.api.trading_engine import V1TradingEngine, V2TradingEngine, trading_engine_for_config
from backend.app.domain import market_features
from backend.app.execution import v1 as execution_v1

from backend.tests.test_v1_ensemble_baseline import fixture_candles


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "v1_ensemble_fixtures.json"


class TradingEngineBoundariesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_default_engine_resolves_to_v1_when_v2_flags_are_disabled(self) -> None:
        engine = trading_engine_for_config({"featureFlags": {"strategyEngineV2Enabled": False, "familyEnsembleV2Enabled": False}})

        self.assertIsInstance(engine, V1TradingEngine)
        self.assertEqual(engine.version, "voting_ensemble_v1")

    def test_v2_flags_route_active_boundary_to_v2(self) -> None:
        engine = trading_engine_for_config(
            {
                "featureFlags": {
                    "strategyEngineV2Enabled": True,
                    "familyEnsembleV2Enabled": True,
                    "globalGateEngineEnabled": True,
                }
            }
        )

        self.assertIsInstance(engine, V2TradingEngine)
        self.assertEqual(engine.version, "voting_ensemble_v2")

    def test_rollback_flag_routes_legacy_boundary_immediately_to_v1(self) -> None:
        engine = trading_engine_for_config(
            {
                "featureFlags": {
                    "strategyEngineV2Enabled": True,
                    "familyEnsembleV2Enabled": True,
                    "globalGateEngineEnabled": True,
                    "deterministicV2RollbackMode": "V1",
                }
            }
        )

        self.assertIsInstance(engine, V1TradingEngine)
        self.assertEqual(engine.version, "voting_ensemble_v1")

    def test_engine_replays_v1_vote_fixtures(self) -> None:
        engine = V1TradingEngine()
        for case in self.fixture["voteCases"]:
            with self.subTest(case=case["name"]):
                candles = fixture_candles(case["pattern"], self.fixture["candleFixture"]["count"])

                self.assertEqual(engine.strategy_votes(candles, case["priorClose"], timeframe="1Min"), case["expected"]["votes"])
                self.assertEqual(engine.vote_summary(candles, case["priorClose"], timeframe="1Min")["signal"], case["expected"]["signal"])
                self.assertEqual(market_features.regime_label(candles), case["expected"]["regime"])

    def test_engine_replays_v1_sizing_fixture(self) -> None:
        engine = V1TradingEngine()
        config = engine.dynamic_risk_config(self.fixture["riskConfigInput"])
        case = self.fixture["positionSizingCases"][0]

        self.assertEqual(
            engine.position_size_for_config(
                config,
                equity=case["equity"],
                entry_price=case["entryPrice"],
                stop_distance=case["stopDistance"],
            ),
            (
                case["expected"]["shares"],
                case["expected"]["plannedRisk"],
                case["expected"]["mode"],
            ),
        )
        self.assertEqual(
            execution_v1.position_size_for_config(
                config,
                equity=case["equity"],
                entry_price=case["entryPrice"],
                stop_distance=case["stopDistance"],
            ),
            (
                case["expected"]["shares"],
                case["expected"]["plannedRisk"],
                case["expected"]["mode"],
            ),
        )

    def test_v2_engine_returns_family_aware_vote_summary(self) -> None:
        engine = V2TradingEngine()
        case = self.fixture["voteCases"][0]
        candles = fixture_candles(case["pattern"], self.fixture["candleFixture"]["count"])

        summary = engine.vote_summary(candles, case["priorClose"], timeframe="1Min")

        self.assertEqual(summary["engineVersion"], "voting_ensemble_v2")
        self.assertEqual(summary["algorithmVersion"], "family_aware_deterministic_ensemble_v1")
        self.assertIn(summary["signal"], {"Buy", "Sell", "Hold"})
        self.assertIn("configurationHash", summary)

    def test_v2_engine_backtest_uses_event_replay_boundary(self) -> None:
        engine = V2TradingEngine()
        case = self.fixture["voteCases"][0]
        candles = fixture_candles(case["pattern"], self.fixture["candleFixture"]["count"])

        result = engine.run_backtest(candles, timeframe="1Min")

        self.assertEqual(result["engineVersion"], "voting_ensemble_v2")
        self.assertEqual(result["algorithmVersion"], "family_aware_deterministic_ensemble_v1")
        self.assertIn("event replay", result["explanation"])
        self.assertIn("decisionCount", result)


if __name__ == "__main__":
    unittest.main()
