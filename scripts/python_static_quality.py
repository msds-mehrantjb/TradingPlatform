from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECK_ROOTS = (
    ROOT / "backend" / "app",
    ROOT / "backend" / "tests",
    ROOT / "scripts",
)
EXCLUDED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
}


def iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in CHECK_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(part in EXCLUDED_DIR_NAMES or part.startswith("tmp") for part in path.parts):
                continue
            files.append(path)
    return sorted(files)


def lint_file(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")

    try:
        ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        errors.append(f"{path}:{exc.lineno}:{exc.offset}: syntax error: {exc.msg}")

    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.rstrip(" \t") != line:
            errors.append(f"{path}:{line_number}: trailing whitespace")
        if "\t" in line[: len(line) - len(line.lstrip())]:
            errors.append(f"{path}:{line_number}: tab indentation")

    if text and not text.endswith("\n"):
        errors.append(f"{path}: missing final newline")

    return errors


def main() -> int:
    errors: list[str] = []
    for path in iter_python_files():
        errors.extend(lint_file(path))

    if errors:
        print("Python formatting/linting gate failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"Python formatting/linting gate passed for {len(iter_python_files())} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
