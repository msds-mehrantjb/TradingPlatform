from __future__ import annotations

import ast
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.algorithms.meta_strategy import ALGORITHM_ID, MetaStrategyApplicationService
from backend.app.algorithms.meta_strategy.api import router
from backend.tests.test_meta_strategy_step7_market_snapshot import request_with


ROOT = Path(__file__).resolve().parents[2]
META_STRATEGY_DIR = ROOT / "backend" / "app" / "algorithms" / "meta_strategy"


class MetaStrategyStep40ApplicationServiceTest(unittest.TestCase):
    def test_service_and_api_boundary_files_exist(self) -> None:
        self.assertTrue((META_STRATEGY_DIR / "service.py").is_file())
        self.assertTrue((META_STRATEGY_DIR / "api.py").is_file())

    def test_service_orchestrates_authoritative_package_entrypoints(self) -> None:
        tree = ast.parse((META_STRATEGY_DIR / "service.py").read_text(encoding="utf-8"))
        imports = imported_module_names(tree)
        calls = called_names(tree)

        self.assertIn("backend.app.algorithms.meta_strategy.execution_pipeline", imports)
        self.assertIn("backend.app.algorithms.meta_strategy.backtest", imports)
        self.assertIn("backend.app.algorithms.meta_strategy.models", imports)
        self.assertIn("backend.app.algorithms.meta_strategy.promotion", imports)
        self.assertIn("backend.app.algorithms.meta_strategy.training", imports)
        self.assertIn("run_meta_strategy_execution_pipeline", calls)
        self.assertIn("run_meta_strategy_backtest", calls)
        self.assertIn("load_runtime_model_artifact_data", calls)
        self.assertIn("train_and_validate_meta_model_v2", calls)
        self.assertIn("evaluate_meta_strategy_promotion_policy", calls)

    def test_service_does_not_import_formula_or_legacy_api_modules(self) -> None:
        tree = ast.parse((META_STRATEGY_DIR / "service.py").read_text(encoding="utf-8"))
        imports = set(imported_module_names(tree))

        forbidden = {
            "backend.app.algorithms.meta_strategy.candidate_generator",
            "backend.app.algorithms.meta_strategy.family_aggregation",
            "backend.app.algorithms.meta_strategy.candidate_geometry",
            "backend.app.algorithms.meta_strategy.sizing",
            "backend.app.algorithms.meta_strategy.local_gates",
            "backend.app.meta_strategy_training",
            "backend.app.ml.features",
            "backend.app.ml.inference",
            "backend.app.api.v2",
        }
        self.assertFalse(forbidden & imports)

    def test_service_evaluation_calls_pipeline_and_returns_summary(self) -> None:
        service = MetaStrategyApplicationService()

        response = service.evaluate({"snapshotRequest": request_with().model_dump(mode="python")})

        self.assertEqual(response["algorithmId"], ALGORITHM_ID)
        self.assertEqual(response["operation"], "evaluation")
        self.assertEqual(response["status"], "OK")
        self.assertEqual(response["payload"]["mode"], "EVALUATION")
        self.assertEqual(response["payload"]["decisionId"], "decision-1")
        self.assertIn("stageSequence", response["payload"])

    def test_service_requires_inputs_for_non_runnable_operations(self) -> None:
        service = MetaStrategyApplicationService()

        self.assertEqual(service.train({})["status"], "REQUIRES_INPUT")
        self.assertEqual(service.load_artifact({})["status"], "REQUIRES_INPUT")
        self.assertEqual(service.backtest({})["status"], "REQUIRES_INPUT")
        self.assertEqual(service.promote({})["status"], "REQUIRES_INPUT")

    def test_api_routes_delegate_directly_to_service(self) -> None:
        tree = ast.parse((META_STRATEGY_DIR / "api.py").read_text(encoding="utf-8"))
        for node in [item for item in tree.body if isinstance(item, ast.FunctionDef)]:
            with self.subTest(route=node.name):
                self.assertEqual(len(node.body), 1)
                self.assertIsInstance(node.body[0], ast.Return)
                call = node.body[0].value
                self.assertIsInstance(call, ast.Call)
                self.assertIsInstance(call.func, ast.Attribute)
                self.assertIsInstance(call.func.value, ast.Name)
                self.assertEqual(call.func.value.id, "META_STRATEGY_SERVICE")

    def test_router_exposes_step40_service_boundaries(self) -> None:
        paths = {route.path for route in router.routes}

        self.assertIn("/api/meta-strategy/evaluate", paths)
        self.assertIn("/api/meta-strategy/training/run", paths)
        self.assertIn("/api/meta-strategy/artifacts/load", paths)
        self.assertIn("/api/meta-strategy/backtests/run", paths)
        self.assertIn("/api/meta-strategy/shadow/evaluate", paths)
        self.assertIn("/api/meta-strategy/paper/evaluate", paths)
        self.assertIn("/api/meta-strategy/promotion/evaluate", paths)
        self.assertIn("/api/meta-strategy/diagnostics", paths)

    def test_main_app_includes_meta_strategy_service_router(self) -> None:
        from backend.app.main import app

        client = TestClient(app)
        response = client.get("/api/meta-strategy/diagnostics")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["algorithmId"], ALGORITHM_ID)
        self.assertEqual(response.json()["operation"], "diagnostics")


def imported_module_names(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def called_names(tree: ast.AST) -> tuple[str, ...]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.append(node.func.attr)
    return tuple(names)


if __name__ == "__main__":
    unittest.main()
