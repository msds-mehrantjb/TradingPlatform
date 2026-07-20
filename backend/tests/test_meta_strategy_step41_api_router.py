from __future__ import annotations

import ast
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.algorithms.meta_strategy import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.api import router
from backend.app.main import app


ROOT = Path(__file__).resolve().parents[2]
META_STRATEGY_API = ROOT / "backend" / "app" / "algorithms" / "meta_strategy" / "api.py"
API_V2 = ROOT / "backend" / "app" / "api" / "v2.py"

MIGRATED_V2_META_STRATEGY_ROUTES = {
    "/meta-model/predict",
    "/activation/deterministic/evaluate",
    "/ml-filter/rollout/evaluate",
    "/dynamic-policy/shadow/evaluate",
    "/dynamic-policy/activation/evaluate",
    "/ml-risk-modifier/experiment/evaluate",
    "/models/status",
}


class MetaStrategyStep41ApiRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_dedicated_router_exposes_required_phase13_categories(self) -> None:
        paths = {route.path for route in router.routes}

        expected = {
            "/api/meta-strategy/status",
            "/api/meta-strategy/configuration",
            "/api/meta-strategy/evaluate",
            "/api/meta-strategy/prediction/evaluate",
            "/api/meta-strategy/shadow/evaluate",
            "/api/meta-strategy/paper/evaluate",
            "/api/meta-strategy/training/run",
            "/api/meta-strategy/artifacts/load",
            "/api/meta-strategy/artifacts/status",
            "/api/meta-strategy/backtests/run",
            "/api/meta-strategy/promotion/evaluate",
            "/api/meta-strategy/paper-stability/validate",
            "/api/meta-strategy/diagnostics",
            "/api/meta-strategy/final-acceptance",
        }
        self.assertTrue(expected <= paths)

    def test_migrated_legacy_meta_strategy_routes_exist_under_dedicated_router(self) -> None:
        paths = {route.path for route in router.routes}

        for old_path in MIGRATED_V2_META_STRATEGY_ROUTES:
            with self.subTest(route=old_path):
                self.assertIn(f"/api/meta-strategy{old_path}", paths)

    def test_general_v2_router_no_longer_exposes_meta_strategy_specific_routes(self) -> None:
        v2_routes = decorated_route_paths(API_V2)

        self.assertFalse(MIGRATED_V2_META_STRATEGY_ROUTES & v2_routes)

    def test_main_app_routes_migrated_paths_to_meta_strategy_package(self) -> None:
        response = self.client.get("/api/meta-strategy/models/status")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["algorithmId"], ALGORITHM_ID)
        self.assertEqual(body["operation"], "status")

    def test_old_v2_meta_strategy_routes_are_not_active(self) -> None:
        response = self.client.get("/api/v2/models/status")

        self.assertEqual(response.status_code, 404)

    def test_prediction_route_fails_closed_and_does_not_submit_orders(self) -> None:
        response = self.client.post("/api/meta-strategy/prediction/evaluate", json={})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["operation"], "prediction")
        self.assertEqual(body["status"], "REQUIRES_INPUT")
        self.assertFalse(body["payload"]["orderSubmissionAllowed"])
        self.assertTrue(body["payload"]["approvedSubmissionEndpointRequired"])
        self.assertIn("meta_strategy.prediction.no_order_submission", body["reasonCodes"])

    def test_api_route_functions_delegate_to_service_only(self) -> None:
        tree = ast.parse(META_STRATEGY_API.read_text(encoding="utf-8"))
        for node in [item for item in tree.body if isinstance(item, ast.FunctionDef)]:
            with self.subTest(route=node.name):
                self.assertEqual(len(node.body), 1)
                self.assertIsInstance(node.body[0], ast.Return)
                call = node.body[0].value
                self.assertIsInstance(call, ast.Call)
                self.assertIsInstance(call.func, ast.Attribute)
                self.assertIsInstance(call.func.value, ast.Name)
                self.assertEqual(call.func.value.id, "META_STRATEGY_SERVICE")

    def test_final_acceptance_endpoint_reports_boundary_conditions(self) -> None:
        response = self.client.get("/api/meta-strategy/final-acceptance")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["algorithmId"], ALGORITHM_ID)
        self.assertEqual(body["operation"], "final_acceptance")
        self.assertTrue(body["payload"]["complete"])
        self.assertEqual(body["payload"]["counts"]["PASSED"], 24)
        self.assertFalse(body["payload"]["liveExecutionEnabled"])


def decorated_route_paths(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    paths: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            route_path = router_path_from_decorator(decorator)
            if route_path is not None:
                paths.add(route_path)
    return paths


def router_path_from_decorator(decorator: ast.expr) -> str | None:
    if not isinstance(decorator, ast.Call):
        return None
    if not isinstance(decorator.func, ast.Attribute) or decorator.func.attr not in {"get", "post"}:
        return None
    if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "router":
        return None
    if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
        return None
    return str(decorator.args[0].value)


if __name__ == "__main__":
    unittest.main()
