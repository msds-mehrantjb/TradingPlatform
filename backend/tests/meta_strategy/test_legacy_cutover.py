from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_ROOT = ROOT / "backend" / "app"

DELETED_LEGACY_MODULES = (
    "backend.app.meta_strategy_training",
    "backend.app.ml.features",
    "backend.app.ml.meta_labeling",
    "backend.app.ml.forecast_oos",
    "backend.app.ml.inference",
)

DELETED_LEGACY_PATHS = (
    "backend/app/meta_strategy_training.py",
    "backend/app/ml/features.py",
    "backend/app/ml/meta_labeling.py",
    "backend/app/ml/forecast_oos.py",
    "backend/app/ml/inference.py",
)

DEDICATED_TRAINING_CORE = "backend.app.algorithms.meta_strategy.training.training_core"


class MetaStrategyStep46LegacyDeletionTest(unittest.TestCase):
    maxDiff = None

    def test_deleted_legacy_authority_paths_are_absent(self) -> None:
        present = [path for path in DELETED_LEGACY_PATHS if (ROOT / path).exists()]

        self.assertEqual(present, [])

    def test_production_imports_do_not_depend_on_deleted_legacy_modules(self) -> None:
        violations = []
        for path in production_python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                for imported in imported_module_names(node):
                    if imported in DELETED_LEGACY_MODULES:
                        violations.append(f"{path.relative_to(ROOT)} imports {imported}")

        self.assertEqual(violations, [])

    def test_production_strings_do_not_point_to_deleted_legacy_paths(self) -> None:
        violations = []
        for path in production_python_files():
            source = path.read_text(encoding="utf-8")
            for legacy_path in DELETED_LEGACY_PATHS:
                if legacy_path in source:
                    violations.append(f"{path.relative_to(ROOT)} references {legacy_path}")

        self.assertEqual(violations, [])

    def test_training_package_uses_training_core_without_compatibility_fallbacks(self) -> None:
        training_path = PRODUCTION_ROOT / "algorithms" / "meta_strategy" / "training"
        training_core_references = []
        compatibility_references = []
        for path in sorted(training_path.glob("*.py")):
            if path.name == "training_core.py":
                continue
            source = path.read_text(encoding="utf-8")
            if "training_core" in source:
                training_core_references.append(path.name)
            if "compatibility_core" in source:
                compatibility_references.append(path.name)

        self.assertGreater(len(training_core_references), 0)
        self.assertEqual(compatibility_references, [])

    def test_package_owned_inference_uses_package_training_core(self) -> None:
        inference_path = PRODUCTION_ROOT / "algorithms" / "meta_strategy" / "inference" / "safe_inference.py"
        tree = ast.parse(inference_path.read_text(encoding="utf-8"), filename=str(inference_path))
        imports = {imported for node in ast.walk(tree) for imported in imported_module_names(node)}

        self.assertIn(DEDICATED_TRAINING_CORE, imports)
        self.assertTrue(set(DELETED_LEGACY_MODULES).isdisjoint(imports))


def production_python_files() -> tuple[Path, ...]:
    files = []
    for path in PRODUCTION_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        files.append(path)
    return tuple(sorted(files))


def imported_module_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        names = [node.module] if node.module else []
        if node.module:
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        return tuple(name for name in names if name)
    return ()


if __name__ == "__main__":
    unittest.main()
