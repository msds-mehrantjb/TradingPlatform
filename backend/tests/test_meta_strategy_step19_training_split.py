from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

from backend.app.algorithms.meta_strategy.training import training_core
from backend.app.algorithms.meta_strategy.training import (
    MetaTrainingConfig,
    assert_no_unowned_training_symbols,
    build_nested_walk_forward_plan,
    train_meta_strategy_baselines,
    training_symbol_ownership,
)
from backend.app.algorithms.meta_strategy.training.training_service import OWNER_MODULES
from backend.tests.test_meta_strategy_champion_challengers import patched_optional_boosters_unavailable
from backend.tests.test_meta_strategy_nested_training import examples, labeled_row, patched_training_io


TRAINING_PACKAGE_PATH = Path("backend/app/algorithms/meta_strategy/training")
TRAINING_PACKAGE = "backend.app.algorithms.meta_strategy.training"


class MetaStrategyStep19TrainingSplitTest(unittest.TestCase):
    def test_every_legacy_training_symbol_has_exactly_one_new_owner(self) -> None:
        ownership = assert_no_unowned_training_symbols()
        core_symbols = {
            node.name
            for node in ast.parse(Path("backend/app/algorithms/meta_strategy/training/training_core.py").read_text(encoding="utf-8")).body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        }

        self.assertEqual(set(ownership), core_symbols)
        self.assertEqual(set(ownership.values()) - set(OWNER_MODULES), set())
        self.assertEqual(ownership["MetaTrainingConfig"], "configuration")
        self.assertEqual(ownership["training_example"], "dataset")
        self.assertEqual(ownership["chronological_purged_folds"], "purging")
        self.assertEqual(ownership["build_nested_walk_forward_plan"], "walk_forward")
        self.assertEqual(ownership["tune_probability_calibration_from_probability_rows"], "calibration_training")
        self.assertEqual(ownership["evaluate_economic_promotion"], "economic_evaluation")
        self.assertEqual(ownership["train_meta_strategy_baselines"], "trainer")
        self.assertEqual(ownership["train_and_validate_meta_model_v2"], "trainer")

    def test_training_modules_import_without_cycles_or_legacy_import_edges(self) -> None:
        modules = sorted(path.stem for path in TRAINING_PACKAGE_PATH.glob("*.py") if path.name != "__init__.py")
        for module in modules:
            importlib.import_module(f"{TRAINING_PACKAGE}.{module}")

        graph = {module: set() for module in modules}
        legacy_edges = []
        for module in modules:
            tree = ast.parse((TRAINING_PACKAGE_PATH / f"{module}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                imported = imported_module_name(node)
                if imported == "backend.app.meta_strategy_training":
                    legacy_edges.append(module)
                if imported and imported.startswith(f"{TRAINING_PACKAGE}."):
                    graph[module].add(imported.rsplit(".", 1)[-1])

        self.assertEqual(legacy_edges, [])
        self.assertFalse(has_cycle(graph), graph)

    def test_new_trainer_produces_deterministic_artifact_and_report_shape(self) -> None:
        kwargs = {
            "decision_snapshot_dir": Path("unused"),
            "symbol": "SPY",
            "minimum_total_candidates": 120,
            "minimum_buy_candidates": 20,
            "minimum_sell_candidates": 20,
            "minimum_positive_outcomes": 40,
            "minimum_negative_outcomes": 20,
            "minimum_candidates_per_outer_fold": 12,
            "minimum_trading_sessions": 4,
            "minimum_regimes_represented": 2,
            "minimum_calibration_rows": 20,
            "minimum_isotonic_rows": 40,
            "outer_folds": 2,
            "inner_folds": 2,
            "maximum_holding_horizon_minutes": 10,
            "embargo_minutes": 10,
        }
        rows = [labeled_row(index) for index in range(180)]

        with patched_training_io(rows), patched_optional_boosters_unavailable():
            first = training_core.train_meta_strategy_baselines(**kwargs)
        with patched_training_io(rows), patched_optional_boosters_unavailable():
            second = train_meta_strategy_baselines(**kwargs)

        self.assertEqual(second["status"], first["status"])
        self.assertEqual(second["validationPolicy"], first["validationPolicy"])
        self.assertEqual(second["featureSchemaHash"], first["featureSchemaHash"])
        self.assertEqual(set(second["models"]), set(first["models"]))
        self.assertEqual(second["metrics"]["outerWalkForward"], first["metrics"]["outerWalkForward"])
        self.assertEqual(second["finalTestRows"], first["finalTestRows"])
        self.assertIn("logistic_regression_champion", second["models"])

    def test_final_holdout_is_never_used_for_tuning_or_walk_forward_validation(self) -> None:
        config = MetaTrainingConfig(
            minimumTotalCandidates=120,
            minimumBuyCandidates=20,
            minimumSellCandidates=20,
            minimumPositiveOutcomes=40,
            minimumNegativeOutcomes=20,
            minimumCandidatesPerOuterFold=12,
            minimumTradingSessions=4,
            minimumRegimesRepresented=2,
            outerFolds=3,
            innerFolds=2,
            maximumHoldingHorizonMinutes=10,
            embargoMinutes=10,
        ).normalized()
        plan = build_nested_walk_forward_plan(examples(), config)
        final_holdout_ids = {row["rowId"] for row in plan["finalTestRows"]}

        self.assertTrue(plan["sufficient"])
        self.assertGreater(len(final_holdout_ids), 0)
        self.assertTrue(final_holdout_ids.isdisjoint({row["rowId"] for row in plan["developmentRows"]}))
        for fold in plan["outerFolds"]:
            tuning_ids = {row["rowId"] for row in fold["trainRows"]}
            validation_ids = {row["rowId"] for row in fold["validationRows"]}
            self.assertTrue(final_holdout_ids.isdisjoint(tuning_ids))
            self.assertTrue(final_holdout_ids.isdisjoint(validation_ids))

    def test_legacy_file_is_deleted_after_parity_cutover(self) -> None:
        self.assertFalse(Path("backend/app/meta_strategy_training.py").exists())
        self.assertGreater(len(training_symbol_ownership()), 20)


def imported_module_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Import):
        return node.names[0].name if node.names else None
    if isinstance(node, ast.ImportFrom):
        return node.module
    return None


def has_cycle(graph: dict[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for child in graph.get(node, set()):
            if child in graph and visit(child):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


if __name__ == "__main__":
    unittest.main()
