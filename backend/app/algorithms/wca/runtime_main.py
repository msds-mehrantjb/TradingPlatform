"""Executable entry point for the standalone WCA background runtime process."""

from __future__ import annotations

import argparse
import json
from typing import Sequence

from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.runtime_supervisor import WCA_RUNTIME_SUPERVISOR_VERSION, WcaRuntimeSettings, WcaRuntimeSupervisor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the standalone WCA background runtime process.")
    parser.add_argument("--database-url", default=None, help="SQLite database URL. Defaults to application settings.")
    parser.add_argument("--once", action="store_true", help="Run one supervisor iteration and exit.")
    parser.add_argument("--max-iterations", type=int, default=None, help="Stop after this many iterations.")
    parser.add_argument("--poll-seconds", type=float, default=1.0, help="Sleep interval between runtime iterations.")
    parser.add_argument("--owner-id", default=None, help="Stable runtime owner/lease identifier.")
    parser.add_argument("--max-lag-seconds", type=int, default=120, help="Pause new entries when finalized-bar lag exceeds this threshold.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = WcaSqliteRepository(args.database_url)
    runtime_repository = WcaRuntimeRepository(repository)
    supervisor = WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(poll_seconds=args.poll_seconds, max_lag_seconds=args.max_lag_seconds),
        owner_id=args.owner_id,
    )
    if args.once:
        result = supervisor.run_once()
        print(json.dumps({"runtimeVersion": WCA_RUNTIME_SUPERVISOR_VERSION, "result": result}, sort_keys=True))
        return 0
    supervisor.run_forever(max_iterations=args.max_iterations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
