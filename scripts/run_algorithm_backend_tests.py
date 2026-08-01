from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_TESTS = REPO_ROOT / "backend" / "tests"


ALGORITHM_TEST_PATTERNS: dict[str, tuple[str, ...]] = {
    "voting_ensemble": ("test_voting_ensemble*.py",),
    "weighted_voting": ("test_weighted_voting*.py",),
    "wca": ("test_wca*.py",),
    "regime": (
        "test_regime*.py",
        "test_adx_atr_regime.py",
    ),
    "meta_strategy": (
        "test_meta_strategy*.py",
        "test_meta_model*.py",
        "test_meta_probability*.py",
    ),
}


def algorithm_test_files(algorithm: str) -> list[Path]:
    patterns = ALGORITHM_TEST_PATTERNS[algorithm]
    files: dict[Path, None] = {}
    for pattern in patterns:
        for path in BACKEND_TESTS.glob(pattern):
            files[path] = None
    return sorted(files)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run backend tests for one algorithm without using repo-wide readiness gates.",
    )
    parser.add_argument(
        "algorithm",
        choices=sorted(ALGORITHM_TEST_PATTERNS),
        help="Algorithm backend suite to run.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the resolved test files and exit.",
    )
    args, pytest_args = parser.parse_known_args()
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    args.pytest_args = pytest_args
    return args


def main() -> int:
    args = parse_args()
    files = algorithm_test_files(args.algorithm)
    if not files:
        print(f"No backend tests matched algorithm {args.algorithm!r}.", file=sys.stderr)
        return 2

    if args.list:
        for path in files:
            print(path.relative_to(REPO_ROOT).as_posix())
        return 0

    pytest_args = list(args.pytest_args)
    if not pytest_args:
        pytest_args = ["-q", "-ra"]

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "not alpaca_paper_smoke",
        *[str(path) for path in files],
        *pytest_args,
    ]
    print(f"Running {args.algorithm} backend suite ({len(files)} files)")
    print(" ".join(command))
    return subprocess.call(command, cwd=REPO_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
