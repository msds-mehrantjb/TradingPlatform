from __future__ import annotations

import importlib
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.algorithms.meta_strategy import REGIME_STRATEGIES, MetaStrategyMarketSnapshot


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
FORBIDDEN_EVIDENCE_KEYS = {
    "candidateSide",
    "entry",
    "stop",
    "target",
    "orderIntent",
    "positionSize",
    "buyScore",
    "sellScore",
    "vote",
    "voteWeight",
}


class MetaStrategyStep11RegimeModulesTest(unittest.TestCase):
    maxDiff = None

    def test_regime_modules_describe_environment_without_casting_votes(self) -> None:
        self.assertEqual(len(REGIME_STRATEGIES), 2)
        for entry in REGIME_STRATEGIES:
            strategy = strategy_for(entry.strategy_id)
            with self.subTest(strategy=entry.strategy_id):
                result = strategy.evaluate(snapshot_fixture(**valid_overrides(entry.strategy_id)))
                self.assertEqual(result.signal, "HOLD")
                self.assertTrue(result.eligible)
                self.assertEqual(result.family, entry.family)
                self.assertEqual(result.required_input_status, {name: True for name in entry.required_inputs})
                self.assertFalse(result.evidence["canGenerateTrade"])
                self.assertFalse(result.evidence["castsIndependentVote"])
                self.assertFalse(FORBIDDEN_EVIDENCE_KEYS.intersection(result.evidence))
                self.assertEqual(entry.supported_directions, ("HOLD",))
                self.assertIn("regimeLabel", result.evidence)
                self.assertIn("volatility", result.evidence)
                self.assertIn("strategyFit", result.evidence)

    def test_regime_results_are_persisted_with_evidence(self) -> None:
        for entry in REGIME_STRATEGIES:
            strategy = strategy_for(entry.strategy_id)
            with self.subTest(strategy=entry.strategy_id):
                result = strategy.evaluate(snapshot_fixture(**valid_overrides(entry.strategy_id)))
                persisted = result.evidence["persistedResult"]
                payload = persisted["payload"]
                evaluation = payload["regimeEvaluation"]
                persisted_evidence = payload["evidence"]

                self.assertEqual(persisted["algorithmId"], "meta_strategy")
                self.assertEqual(persisted["resultType"], "regime_evaluation")
                self.assertEqual(payload["strategyId"], entry.strategy_id)
                self.assertEqual(evaluation["regime_id"], entry.strategy_id)
                self.assertEqual(evaluation["features"], persisted_evidence)
                self.assertEqual(persisted_evidence["strategyFit"], result.evidence["strategyFit"])
                self.assertIn("versions", persisted)
                self.assertIn("algorithmVersion", persisted["versions"])

    def test_regime_missing_data_fails_safely(self) -> None:
        for entry in REGIME_STRATEGIES:
            strategy = strategy_for(entry.strategy_id)
            with self.subTest(strategy=entry.strategy_id):
                result = strategy.evaluate(snapshot_fixture(**missing_overrides(entry.strategy_id)))
                self.assertEqual(result.signal, "HOLD")
                self.assertFalse(result.eligible)
                self.assertIn("meta_strategy.regime.missing_required_inputs", result.reason_codes)
                self.assertIn(False, result.required_input_status.values())
                self.assertFalse(result.evidence["canGenerateTrade"])
                self.assertFalse(result.evidence["castsIndependentVote"])
                self.assertTrue(result.evidence["missingDataSafe"])
                self.assertFalse(result.evidence["dataReady"])
                self.assertNotIn("persistedResult", result.evidence)

    def test_regime_outputs_have_expected_environment_labels(self) -> None:
        adx_result = strategy_for("adx_trend_strength_regime").evaluate(snapshot_fixture(adx={"1m": 38.0}, moving_averages={"1m": {"ema20": 102.0, "ema50": 100.0}}))
        atr_result = strategy_for("atr_volatility_regime").evaluate(snapshot_fixture(atr={"1m": 5.0}, relative_volume={"1m": 3.0}, economic_event_state={"state": "blocked", "active": True}))

        self.assertEqual(adx_result.evidence["regimeLabel"], "strong_trend")
        self.assertEqual(adx_result.evidence["direction"], 1)
        self.assertGreater(adx_result.evidence["strategyFit"]["TREND"], adx_result.evidence["strategyFit"]["MEAN_REVERSION"])
        self.assertEqual(atr_result.evidence["volatility"], "EXTREME")
        self.assertLessEqual(atr_result.evidence["strategyFit"]["TREND"], 0.6)

    def test_regime_registry_uses_dedicated_modules(self) -> None:
        expected_modules = {
            "adx_trend_strength_regime": "regime.adx_trend_strength",
            "atr_volatility_regime": "regime.atr_volatility_regime",
        }
        for entry in REGIME_STRATEGIES:
            with self.subTest(strategy=entry.strategy_id):
                self.assertTrue(entry.implementation_module.endswith(expected_modules[entry.strategy_id]))
                self.assertNotEqual(entry.implementation_module, "backend.app.algorithms.meta_strategy.strategies.regime")

    def test_regime_sources_do_not_encode_directional_votes(self) -> None:
        regime_dir = Path(__file__).parents[1] / "app" / "algorithms" / "meta_strategy" / "strategies" / "regime"
        for path in regime_dir.glob("*.py"):
            with self.subTest(file=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn('signal="BUY"', source)
                self.assertNotIn('signal="SELL"', source)
                self.assertNotIn("signal='BUY'", source)
                self.assertNotIn("signal='SELL'", source)
                self.assertNotIn("buyScore", source)
                self.assertNotIn("sellScore", source)
                self.assertNotIn("backend.app.algorithms.regime", source)


def strategy_for(strategy_id: str):
    entry = next(item for item in REGIME_STRATEGIES if item.strategy_id == strategy_id)
    module = importlib.import_module(entry.implementation_module)
    return getattr(module, entry.implementation_class)()


def snapshot_fixture(**overrides: Any) -> MetaStrategyMarketSnapshot:
    price = float(overrides.get("price", 101.0))
    return MetaStrategyMarketSnapshot(
        algorithm_id="meta_strategy",
        algorithm_version="meta_strategy_algorithm_v1",
        configuration_version="meta_strategy_config_v1",
        strategy_catalog_version="meta_strategy_strategy_catalog_v1",
        decision_id="decision-regime-1",
        snapshot_id="snapshot-regime-1",
        timestamp=NOW,
        symbol="SPY",
        last_price=price,
        bid_price=price - 0.01,
        ask_price=price + 0.01,
        spread_bps=5.0,
        volume=100_000,
        source_cutoff_timestamp=NOW,
        point_in_time=overrides.get("point_in_time", True),
        candles={"1m": candles(60, price)},
        vwap=100.0,
        moving_averages=overrides.get("moving_averages", {"1m": {"ema20": 101.0, "ema50": 100.0}}),
        atr=overrides.get("atr", {"1m": 1.0}),
        adx=overrides.get("adx", {"1m": 24.0}),
        rsi={"1m": 50.0},
        macd={"1m": {"macd": 0.1, "signal": 0.05, "histogram": 0.05}},
        bollinger_bands={"1m": {"upper": 102.0, "middle": 100.0, "lower": 98.0}},
        relative_volume=overrides.get("relative_volume", {"1m": 1.2}),
        spread={"basisPoints": 5.0, "dollars": 0.02},
        liquidity={"level": "good", "score": 1.0},
        session_phase="morning",
        gap_state={"state": "flat_open", "gapPercent": 0.0},
        qqq_iwm_context={"spyVsQqq": 1.0, "spyVsIwm": 1.0},
        breadth={"averageReturn": 0.001, "positiveShare": 0.56, "componentCount": 500},
        economic_event_state=overrides.get("economic_event_state", {"state": "none", "active": False}),
        features={},
    )


def candles(count: int, close: float) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "timestamp": NOW.isoformat(),
            "open": close - 0.05,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": 100_000,
        }
        for _ in range(count)
    )


def valid_overrides(strategy_id: str) -> dict[str, Any]:
    return {
        "adx_trend_strength_regime": {"adx": {"1m": 28.0}, "atr": {"1m": 1.2}, "moving_averages": {"1m": {"ema20": 102.0, "ema50": 100.0}}},
        "atr_volatility_regime": {"atr": {"1m": 2.8}, "relative_volume": {"1m": 2.1}, "economic_event_state": {"state": "none", "active": False}},
    }[strategy_id]


def missing_overrides(strategy_id: str) -> dict[str, Any]:
    return {
        "adx_trend_strength_regime": {"adx": {}, "atr": {}, "moving_averages": {}},
        "atr_volatility_regime": {"atr": {}, "relative_volume": {}, "economic_event_state": {}},
    }[strategy_id]


if __name__ == "__main__":
    unittest.main()
