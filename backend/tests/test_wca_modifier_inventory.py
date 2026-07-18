from __future__ import annotations

import ast
import importlib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.algorithms.wca.contracts import WcaCandle, WcaEvaluationStatus, WcaMarketSnapshot, WcaModifierEvaluation, WcaQuote
from backend.app.algorithms.wca.modifiers import WCA_MODIFIERS, evaluate_all_modifiers
from backend.app.algorithms.wca.strategy_registry import WCA_MODIFIER_REGISTRY, WCA_MODIFIER_SLUGS


ROOT = Path(__file__).parents[2]
MODIFIER_PATH = ROOT / "backend" / "app" / "algorithms" / "wca" / "modifiers"
MODIFIER_PACKAGE = "backend.app.algorithms.wca.modifiers"
UTC = timezone.utc

EXPECTED_MODIFIER_FILES = {
    "__init__.py",
    "base.py",
    "vwap_position.py",
    "volume_confirmation.py",
    "macd_momentum.py",
    "market_structure.py",
    "adx_trend_strength.py",
    "atr_volatility_regime.py",
    "multi_timeframe_trend_alignment.py",
    "relative_strength_vs_qqq_iwm.py",
    "market_breadth.py",
    "session_phase.py",
    "spread_liquidity.py",
}

MODIFIER_CLASSES = (
    ("vwap_position", "VwapPositionModifier"),
    ("volume_confirmation", "VolumeConfirmationModifier"),
    ("macd_momentum", "MacdMomentumModifier"),
    ("market_structure", "MarketStructureModifier"),
    ("adx_trend_strength", "AdxTrendStrengthModifier"),
    ("atr_volatility_regime", "AtrVolatilityRegimeModifier"),
    ("multi_timeframe_trend_alignment", "MultiTimeframeTrendAlignmentModifier"),
    ("relative_strength_vs_qqq_iwm", "RelativeStrengthVsQqqIwmModifier"),
    ("market_breadth", "MarketBreadthModifier"),
    ("session_phase", "SessionPhaseModifier"),
    ("spread_liquidity", "SpreadLiquidityModifier"),
)


class WcaModifierInventoryTest(unittest.TestCase):
    def test_modifier_package_contains_completed_executable_inventory(self) -> None:
        self.assertEqual({path.name for path in MODIFIER_PATH.glob("*.py")}, EXPECTED_MODIFIER_FILES)
        self.assertEqual(tuple(slug for slug, _class_name in MODIFIER_CLASSES), tuple(row.slug for row in WCA_MODIFIER_REGISTRY))
        self.assertEqual({modifier.modifier_id for modifier in WCA_MODIFIERS}, WCA_MODIFIER_SLUGS)

    def test_each_registered_modifier_imports_and_evaluates_independently(self) -> None:
        snapshot = market_snapshot()
        for slug, class_name in MODIFIER_CLASSES:
            with self.subTest(modifier=slug):
                module = importlib.import_module(f"{MODIFIER_PACKAGE}.{slug}")
                modifier = getattr(module, class_name)()
                result = modifier.evaluate(snapshot)

                self.assertEqual(modifier.modifier_id, slug)
                self.assertIsInstance(result, WcaModifierEvaluation)
                self.assertEqual(result.modifier_id, slug)
                self.assertGreaterEqual(result.multiplier, 0)
                self.assertNotIn("signal", result.model_dump(mode="json"))

    def test_modifiers_do_not_cast_independent_buy_or_sell_votes(self) -> None:
        for result in evaluate_all_modifiers(market_snapshot()):
            payload = result.model_dump(mode="json")

            self.assertNotIn("side", payload)
            self.assertNotIn("signal", payload)
            self.assertNotIn("direction", payload)
            self.assertIn(result.status, {WcaEvaluationStatus.ACTIVE.value, WcaEvaluationStatus.NOT_APPLICABLE.value, WcaEvaluationStatus.INVALID.value})

    def test_modifier_files_have_no_foreign_algorithm_imports_or_mutable_state(self) -> None:
        violations: list[str] = []
        for path in sorted(MODIFIER_PATH.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for module_name in _imported_modules(tree):
                if module_name.startswith("backend.app.algorithms.") and not module_name.startswith("backend.app.algorithms.wca"):
                    violations.append(f"{path.name} imports {module_name}")
                if module_name.startswith(("backend.app.ml", "backend.app.market_forecast", "backend.app.database")):
                    violations.append(f"{path.name} imports {module_name}")
            for node in tree.body:
                value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
                if isinstance(value, (ast.Dict, ast.List, ast.Set)):
                    violations.append(f"{path.name}:{node.lineno} has mutable module-level state")

        self.assertEqual(violations, [])


def market_snapshot() -> WcaMarketSnapshot:
    timestamp = datetime(2026, 1, 6, 17, 0, tzinfo=UTC)
    candles = tuple(
        WcaCandle(
            timestamp=timestamp - timedelta(minutes=59 - index),
            open=100 + index * 0.03,
            high=100.15 + index * 0.03,
            low=99.90 + index * 0.03,
            close=100.05 + index * 0.03,
            volume=100000 + index * 5000,
        )
        for index in range(60)
    )
    latest = candles[-1]
    return WcaMarketSnapshot(
        symbol="SPY",
        data_timestamp=latest.timestamp,
        decision_timestamp=latest.timestamp,
        candles=candles,
        quote=WcaQuote(timestamp=latest.timestamp, bid=latest.close - 0.01, ask=latest.close + 0.01),
    )


def _imported_modules(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


if __name__ == "__main__":
    unittest.main()
