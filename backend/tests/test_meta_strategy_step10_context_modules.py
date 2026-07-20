from __future__ import annotations

import importlib
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.algorithms.meta_strategy import CONTEXT_STRATEGIES, MetaStrategyMarketSnapshot
from backend.app.algorithms.meta_strategy.strategies.context.common import ADJUSTMENT_LIMITS, neutral_adjustments


NOW = datetime(2026, 1, 5, 15, 45, tzinfo=UTC)
ADJUSTMENT_KEYS = tuple(ADJUSTMENT_LIMITS)
FORBIDDEN_EVIDENCE_KEYS = {
    "candidateSide",
    "entry",
    "stop",
    "target",
    "orderIntent",
    "positionSize",
    "buyScore",
    "sellScore",
}


class MetaStrategyStep10ContextModulesTest(unittest.TestCase):
    maxDiff = None

    def test_context_modules_adjust_only_and_never_generate_trades(self) -> None:
        self.assertEqual(len(CONTEXT_STRATEGIES), 6)
        for entry in CONTEXT_STRATEGIES:
            strategy = strategy_for(entry.strategy_id)
            with self.subTest(strategy=entry.strategy_id):
                result = strategy.evaluate(snapshot_fixture())
                self.assertEqual(result.signal, "HOLD")
                self.assertTrue(result.eligible)
                self.assertEqual(result.family, entry.family)
                self.assertEqual(result.required_input_status, {name: True for name in entry.required_inputs})
                self.assertTrue(result.evidence)
                self.assertFalse(result.evidence["canGenerateTrade"])
                self.assertFalse(FORBIDDEN_EVIDENCE_KEYS.intersection(result.evidence))
                self.assertEqual(entry.supported_directions, ("HOLD",))
                for key in ADJUSTMENT_KEYS:
                    self.assertIn(key, result.evidence)

    def test_context_adjustment_limits_are_enforced(self) -> None:
        for entry in CONTEXT_STRATEGIES:
            strategy = strategy_for(entry.strategy_id)
            with self.subTest(strategy=entry.strategy_id):
                result = strategy.evaluate(snapshot_fixture(**extreme_overrides(entry.strategy_id)))
                self.assertEqual(result.signal, "HOLD")
                self.assertFalse(result.evidence["canGenerateTrade"])
                for key, (minimum, maximum) in ADJUSTMENT_LIMITS.items():
                    value = result.evidence[key]
                    self.assertGreaterEqual(value, minimum)
                    self.assertLessEqual(value, maximum)

    def test_missing_context_fails_safely(self) -> None:
        neutral = neutral_adjustments()
        for entry in CONTEXT_STRATEGIES:
            strategy = strategy_for(entry.strategy_id)
            with self.subTest(strategy=entry.strategy_id):
                result = strategy.evaluate(snapshot_fixture(**missing_overrides(entry.strategy_id)))
                self.assertEqual(result.signal, "HOLD")
                self.assertFalse(result.eligible)
                self.assertIn("meta_strategy.context.missing_required_inputs", result.reason_codes)
                self.assertIn(False, result.required_input_status.values())
                self.assertFalse(result.evidence["canGenerateTrade"])
                self.assertTrue(result.evidence["missingContextSafe"])
                for key, value in neutral.items():
                    self.assertEqual(result.evidence[key], value)

    def test_context_registry_uses_dedicated_modules(self) -> None:
        expected_modules = {
            "relative_strength_qqq_iwm": "context.relative_strength_qqq_iwm",
            "market_breadth_momentum": "context.market_breadth_momentum",
            "economic_event_context": "context.economic_event_context",
            "market_structure_context": "context.market_structure_context",
            "volume_confirmation": "context.volume_confirmation",
            "vwap_position_context": "context.vwap_position_context",
        }
        for entry in CONTEXT_STRATEGIES:
            with self.subTest(strategy=entry.strategy_id):
                self.assertTrue(entry.implementation_module.endswith(expected_modules[entry.strategy_id]))
                self.assertNotEqual(entry.implementation_module, "backend.app.algorithms.meta_strategy.strategies.context")

    def test_context_sources_do_not_encode_directional_signals(self) -> None:
        context_dir = Path(__file__).parents[1] / "app" / "algorithms" / "meta_strategy" / "strategies" / "context"
        for path in context_dir.glob("*.py"):
            with self.subTest(file=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn('signal="BUY"', source)
                self.assertNotIn('signal="SELL"', source)
                self.assertNotIn("signal='BUY'", source)
                self.assertNotIn("signal='SELL'", source)


def strategy_for(strategy_id: str):
    entry = next(item for item in CONTEXT_STRATEGIES if item.strategy_id == strategy_id)
    module = importlib.import_module(entry.implementation_module)
    return getattr(module, entry.implementation_class)()


def snapshot_fixture(**overrides: Any) -> MetaStrategyMarketSnapshot:
    price = float(overrides.get("price", 101.0))
    vwap = overrides.get("vwap", 100.0)
    moving_averages = overrides.get("moving_averages", {"1m": {"ema20": 100.5, "ema50": 100.0}})
    relative_volume_override = overrides.get("relative_volume", 1.25)
    relative_volume_map = relative_volume_override if isinstance(relative_volume_override, dict) else {"1m": relative_volume_override}
    return MetaStrategyMarketSnapshot(
        algorithm_id="meta_strategy",
        algorithm_version="meta_strategy_algorithm_v1",
        configuration_version="meta_strategy_config_v1",
        strategy_catalog_version="meta_strategy_strategy_catalog_v1",
        decision_id="decision-context-1",
        snapshot_id="snapshot-context-1",
        timestamp=NOW,
        symbol="SPY",
        last_price=price,
        bid_price=price - 0.01,
        ask_price=price + 0.01,
        spread_bps=overrides.get("spread_bps", 5.0),
        volume=overrides.get("volume", 100_000),
        source_cutoff_timestamp=NOW,
        point_in_time=overrides.get("point_in_time", True),
        candles=overrides.get("candles", {"1m": candles(60, price)}),
        vwap=vwap,
        moving_averages=moving_averages,
        atr=overrides.get("atr", {"1m": 1.0}),
        adx={"1m": 20.0},
        rsi={"1m": 50.0},
        macd={"1m": {"macd": 0.1, "signal": 0.05, "histogram": 0.05}},
        bollinger_bands={"1m": {"upper": 102.0, "middle": 100.0, "lower": 98.0}},
        relative_volume=relative_volume_map,
        spread=overrides.get("spread", {"basisPoints": 5.0, "dollars": 0.02}),
        liquidity={"level": "good", "score": 1.0},
        session_phase=overrides.get("session_phase", "morning"),
        gap_state={"state": "gap_up", "gapPercent": 0.5},
        qqq_iwm_context=overrides.get("qqq_iwm_context", {"spyVsQqq": 1.01, "spyVsIwm": 1.005}),
        breadth=overrides.get("breadth", {"averageReturn": 0.001, "positiveShare": 0.56, "componentCount": 500}),
        economic_event_state=overrides.get("economic_event_state", {"state": "none", "severity": "none", "active": False}),
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


def extreme_overrides(strategy_id: str) -> dict[str, Any]:
    return {
        "relative_strength_qqq_iwm": {"qqq_iwm_context": {"spyVsQqq": 10.0, "spyVsIwm": 0.01}},
        "market_breadth_momentum": {"breadth": {"averageReturn": 0.5, "positiveShare": 2.0, "componentCount": 5}},
        "economic_event_context": {
            "economic_event_state": {"state": "blocked", "severity": "critical", "minutesToEvent": 1, "active": True},
            "spread": {"basisPoints": 100.0, "dollars": 1.0},
        },
        "market_structure_context": {
            "moving_averages": {"1m": {"ema20": 200.0, "ema50": 1.0}},
            "atr": {"1m": 0.01},
            "candles": {"1m": candles(60, 101.0)},
        },
        "volume_confirmation": {"volume": 1, "relative_volume": {"1m": 50.0}},
        "vwap_position_context": {"price": 200.0, "vwap": 1.0, "moving_averages": {"1m": {"ema20": 1.0}}},
    }[strategy_id]


def missing_overrides(strategy_id: str) -> dict[str, Any]:
    return {
        "relative_strength_qqq_iwm": {"qqq_iwm_context": {}},
        "market_breadth_momentum": {"breadth": {}},
        "economic_event_context": {"economic_event_state": {}, "session_phase": "", "spread": {}},
        "market_structure_context": {"candles": {}, "moving_averages": {}, "atr": {}},
        "volume_confirmation": {"volume": 0, "relative_volume": {}},
        "vwap_position_context": {"vwap": None, "moving_averages": {}},
    }[strategy_id]


if __name__ == "__main__":
    unittest.main()
