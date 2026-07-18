from __future__ import annotations

import ast
import importlib
import pkgutil
import unittest
from pathlib import Path


PACKAGE_NAME = "backend.app.algorithms.weighted_voting"
PACKAGE_PATH = Path(__file__).parents[1] / "app" / "algorithms" / "weighted_voting"

EXPECTED_FILES = {
    "__init__.py",
    "identity.py",
    "api.py",
    "service.py",
    "models.py",
    "config.py",
    "catalog.py",
    "market_snapshot.py",
    "market_condition.py",
    "signal_engine.py",
    "weight_engine.py",
    "aggregation.py",
    "decision_gates.py",
    "dynamic_settings.py",
    "risk_budget.py",
    "position_sizing.py",
    "position_trade_state.py",
    "entry_policy.py",
    "exit_policy.py",
    "order_proposal.py",
    "performance_tracker.py",
    "persistence.py",
    "scheduler.py",
    "strategies/base.py",
    "strategies/opening_range_breakout.py",
    "strategies/first_pullback_after_open.py",
    "strategies/vwap_trend_continuation.py",
    "strategies/vwap_mean_reversion.py",
    "strategies/failed_breakout_reversal.py",
    "strategies/liquidity_sweep_reversal.py",
    "strategies/bollinger_atr_reversion.py",
    "strategies/volatility_breakout.py",
    "backtest/data_validation.py",
    "backtest/execution_simulator.py",
    "backtest/walk_forward.py",
    "backtest/engine.py",
}

FORBIDDEN_IMPORT_PREFIXES = (
    "backend.app.ensemble",
    "backend.app.ml",
    "backend.app.strategies",
    "backend.app.trading_policy",
    "backend.app.backtesting",
    "backend.app.market_forecast",
    "backend.app.market_forecast_worker",
    "backend.app.meta_strategy_training",
    "backend.app.train_market_forecast",
    "frontend",
)


class WeightedVotingPackageArchitectureTest(unittest.TestCase):
    def test_requested_package_structure_exists(self) -> None:
        missing = sorted(path for path in EXPECTED_FILES if not (PACKAGE_PATH / path).is_file())

        self.assertEqual(missing, [])

    def test_package_and_all_modules_import_without_cycles(self) -> None:
        package = importlib.import_module(PACKAGE_NAME)
        imported = {package.__name__}

        for module in pkgutil.walk_packages(package.__path__, f"{PACKAGE_NAME}."):
            imported.add(importlib.import_module(module.name).__name__)

        self.assertIn(f"{PACKAGE_NAME}.service", imported)
        self.assertIn(f"{PACKAGE_NAME}.strategies.opening_range_breakout", imported)
        self.assertIn(f"{PACKAGE_NAME}.backtest.engine", imported)

    def test_weighted_voting_does_not_import_other_algorithm_packages(self) -> None:
        violations: list[str] = []
        for path in sorted(PACKAGE_PATH.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if _is_forbidden_import(alias.name):
                            violations.append(f"{path.relative_to(PACKAGE_PATH)} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if _is_forbidden_import(node.module):
                        violations.append(f"{path.relative_to(PACKAGE_PATH)} imports from {node.module}")
                    if node.module.startswith("backend.app.algorithms.") and not node.module.startswith(PACKAGE_NAME):
                        violations.append(f"{path.relative_to(PACKAGE_PATH)} imports sibling algorithm {node.module}")

        self.assertEqual(violations, [])


def _is_forbidden_import(module_name: str) -> bool:
    return any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in FORBIDDEN_IMPORT_PREFIXES)


if __name__ == "__main__":
    unittest.main()
