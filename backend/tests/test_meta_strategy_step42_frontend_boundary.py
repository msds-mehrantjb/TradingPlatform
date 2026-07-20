from __future__ import annotations

import re
import unittest
from pathlib import Path

from backend.app.algorithms.meta_strategy import ALGORITHM_ID, MetaStrategyApplicationService


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = ROOT / "frontend" / "src"
FRONTEND_MAIN = FRONTEND_SRC / "main.ts"

PROHIBITED_META_STRATEGY_FRONTEND_PATTERNS = {
    "local_meta_strategy_calculation": r"\bcalculateMetaStrategy\b",
    "local_meta_strategy_order_builder": r"\bmetaTargetOrderRecommendation\b",
    "local_meta_strategy_order_overrides": r"\bapplyMetaTargetOrderOverrides\b",
    "local_meta_strategy_feature_builder": r"\bmetaStrategyFeature\s*\(",
    "local_meta_strategy_role_weights": r"\bmetaRoleWeight\s*\(",
    "local_meta_strategy_family_aggregation": r"\bmetaFamilyAggregationScores\s*\(",
    "local_meta_strategy_family_display_scoring": r"\bmetaFamilyDisplayScores\s*\(",
    "legacy_frontend_training_post": r"/api/meta-strategy/train-baselines",
    "legacy_frontend_training_status": r"/api/meta-strategy/training-status",
    "dedicated_frontend_training_run": r"/api/meta-strategy/training/run",
    "frontend_train_button": r'data-meta-training-action=["\']train["\']',
    "frontend_train_action_branch": r"metaTrainingAction\s*===\s*[\"']train[\"']",
    "frontend_promotion_decision_request": r"/api/meta-strategy/promotion/evaluate",
    "frontend_paper_stability_pass_request": r"/api/meta-strategy/paper-stability/validate",
}

ALLOWED_META_STRATEGY_PRESENTATION_ROUTES = {
    "/api/meta-strategy/status",
    "/api/meta-strategy/configuration",
    "/api/meta-strategy/evaluate",
    "/api/meta-strategy/prediction/evaluate",
    "/api/meta-strategy/shadow/evaluate",
    "/api/meta-strategy/paper/evaluate",
    "/api/meta-strategy/artifacts/status",
    "/api/meta-strategy/backtests/run",
    "/api/meta-strategy/diagnostics",
    "/api/meta-strategy/final-acceptance",
}


class MetaStrategyStep42FrontendBoundaryTest(unittest.TestCase):
    maxDiff = None

    def test_frontend_source_contains_no_prohibited_meta_strategy_authority(self) -> None:
        violations: list[str] = []
        for path in frontend_source_files():
            text = path.read_text(encoding="utf-8")
            for label, pattern in PROHIBITED_META_STRATEGY_FRONTEND_PATTERNS.items():
                if re.search(pattern, text):
                    violations.append(f"{path.relative_to(ROOT)}: {label}")

        self.assertEqual(violations, [])

    def test_meta_strategy_panel_clears_frontend_order_authority_and_uses_backend_status(self) -> None:
        source = FRONTEND_MAIN.read_text(encoding="utf-8")

        self.assertIn("state.currentMetaTargetOrder = null", source)
        self.assertIn("/api/meta-strategy/status", source)
        self.assertIn("Backend Meta-Strategy service is authoritative", source)

    def test_frontend_meta_strategy_api_usage_is_presentation_only(self) -> None:
        used_routes = set(re.findall(r"/api/meta-strategy/[A-Za-z0-9_./-]+", all_frontend_source()))

        self.assertTrue(used_routes <= ALLOWED_META_STRATEGY_PRESENTATION_ROUTES)

    def test_backend_service_remains_authoritative_without_frontend_runtime(self) -> None:
        response = MetaStrategyApplicationService().status()

        self.assertEqual(response["algorithmId"], ALGORITHM_ID)
        self.assertEqual(response["operation"], "status")
        self.assertEqual(response["status"], "OK")
        self.assertEqual(response["payload"]["packageBoundary"], "dedicated")


def frontend_source_files() -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(FRONTEND_SRC.rglob("*"))
        if path.is_file() and path.suffix in {".ts", ".tsx", ".js", ".jsx"}
    )


def all_frontend_source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in frontend_source_files())


if __name__ == "__main__":
    unittest.main()
