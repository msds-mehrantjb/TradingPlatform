from __future__ import annotations

import ast
import unittest
from pathlib import Path

from backend.app.algorithms.meta_strategy import (
    META_STRATEGY_ALGORITHM_ID,
    meta_strategy_ownership_boundary,
    meta_strategy_service_boundary,
)


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_PATH = ROOT / "backend" / "app" / "algorithms" / "meta_strategy"
PACKAGE_NAME = "backend.app.algorithms.meta_strategy"

FORBIDDEN_ALGORITHM_IMPORT_PREFIXES = (
    "backend.app.algorithms.wca",
    "backend.app.algorithms.regime",
    "backend.app.algorithms.weighted_voting",
    "backend.app.algorithms.voting_ensemble",
)
ALLOWED_SHARED_INFRASTRUCTURE_IMPORT_PREFIXES = (
    "backend.app.domain",
    "backend.app.gates",
    "backend.app.global_risk",
    "backend.app.risk",
    "backend.app.broker",
    "backend.app.brokers",
    "backend.app.logging",
    "backend.app.observability",
    "backend.app.database",
    "backend.app.db",
    "backend.app.persistence",
)
LEGACY_CHARACTERIZATION_IMPORT_PREFIXES = (
    "backend.app.ensemble",
    "backend.app.ml",
    "backend.app.strategies",
)
PRIVATE_STATE_MARKERS = (
    "wca_private_state",
    "regime_private_state",
    "weighted_voting_private_state",
    "voting_ensemble_private_state",
)


class MetaStrategyStep6ArchitectureIsolationTest(unittest.TestCase):
    maxDiff = None

    def test_meta_strategy_package_does_not_import_sibling_algorithm_modules(self) -> None:
        violations = []
        for path in meta_strategy_source_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for imported_module in imported_module_names(tree):
                if is_forbidden_algorithm_import(imported_module):
                    violations.append(f"{path.relative_to(PACKAGE_PATH)} imports {imported_module}")

        self.assertEqual(violations, [])

    def test_dynamic_imports_cannot_target_sibling_algorithm_modules(self) -> None:
        violations = []
        for path in meta_strategy_source_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for module_name in dynamic_import_targets(tree):
                if is_forbidden_algorithm_import(module_name):
                    violations.append(f"{path.relative_to(PACKAGE_PATH)} dynamically imports {module_name}")

        self.assertEqual(violations, [])

    def test_backend_algorithm_imports_are_limited_to_meta_strategy_itself(self) -> None:
        violations = []
        for path in meta_strategy_source_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for imported_module in imported_module_names(tree):
                if imported_module.startswith("backend.app.algorithms.") and not imported_module.startswith(PACKAGE_NAME):
                    violations.append(f"{path.relative_to(PACKAGE_PATH)} imports sibling algorithm {imported_module}")

        self.assertEqual(violations, [])

    def test_non_algorithm_backend_imports_are_shared_or_legacy_characterization_only(self) -> None:
        violations = []
        for path in meta_strategy_source_files():
            relative = path.relative_to(PACKAGE_PATH).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for imported_module in imported_module_names(tree):
                if not imported_module.startswith("backend.app.") or imported_module.startswith(PACKAGE_NAME):
                    continue
                if imported_module.startswith("backend.app.algorithms."):
                    continue
                if starts_with_any(imported_module, ALLOWED_SHARED_INFRASTRUCTURE_IMPORT_PREFIXES):
                    continue
                if relative == "characterization.py" and starts_with_any(imported_module, LEGACY_CHARACTERIZATION_IMPORT_PREFIXES):
                    continue
                violations.append(f"{relative} imports non-shared backend module {imported_module}")

        self.assertEqual(violations, [])

    def test_private_state_markers_are_declarative_only(self) -> None:
        allowed_files = {"identity.py", "contracts.py", "__init__.py"}
        violations = []
        for path in meta_strategy_source_files():
            relative = path.relative_to(PACKAGE_PATH).as_posix()
            if relative in allowed_files:
                continue
            source = path.read_text(encoding="utf-8")
            for marker in PRIVATE_STATE_MARKERS:
                if marker in source:
                    violations.append(f"{relative} references {marker}")

        self.assertEqual(violations, [])

    def test_ownership_boundary_declares_sibling_private_state_as_forbidden(self) -> None:
        service_boundary = meta_strategy_service_boundary()
        ownership = meta_strategy_ownership_boundary()

        self.assertEqual(service_boundary.algorithm_id, META_STRATEGY_ALGORITHM_ID)
        self.assertEqual(set(service_boundary.forbidden_private_state), set(PRIVATE_STATE_MARKERS))
        self.assertFalse(ownership["mayReadSiblingPrivateState"])
        self.assertFalse(ownership["mayMutateForeignAlgorithmState"])


def meta_strategy_source_files() -> tuple[Path, ...]:
    return tuple(path for path in sorted(PACKAGE_PATH.rglob("*.py")) if "__pycache__" not in path.parts)


def imported_module_names(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def dynamic_import_targets(tree: ast.AST) -> tuple[str, ...]:
    targets: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if is_importlib_import_module_call(node) or is_dunder_import_call(node):
            for arg in node.args[:1]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    targets.append(arg.value)
    return tuple(targets)


def is_importlib_import_module_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "import_module"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "importlib"
    )


def is_dunder_import_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Name) and node.func.id == "__import__"


def is_forbidden_algorithm_import(module_name: str) -> bool:
    return starts_with_any(module_name, FORBIDDEN_ALGORITHM_IMPORT_PREFIXES)


def starts_with_any(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(module_name == prefix or module_name.startswith(f"{prefix}.") for prefix in prefixes)


if __name__ == "__main__":
    unittest.main()
